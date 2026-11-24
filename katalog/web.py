"""Webbserver: en enda process som serverar katalogen, inloggning, konto, admin och API.

Statiska filer serveras från minnet (förkomprimerade, med ETag). Varje förfrågan loggas.
"""
import datetime
import http.cookies
import ipaddress
import os
import json
import re
import socket
import threading
import time
import urllib.parse
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import alerts, backup, bambu, cart, config, costcalc, db, energy, history, homeassistant, newcolors, pages, printers, printlog, scheduler, security
from .static import store
from .webcommon import *  # noqa: F401,F403
from .routes.account import AccountRoutes
from .routes.admin import AdminRoutes
from .routes.api import ApiRoutes
from .routes.cost import CostRoutes
from .routes.printers import PrintersRoutes
from .routes.prints import PrintsRoutes
from .routes.shop import ShopRoutes
from .routes.spools import SpoolsRoutes

STARTUP_ERROR = None   # sätts om datamappen inte går att använda; visas då på alla sidor


class _TooLarge(Exception):
    pass


class _ConnReader:
    """Läser från anslutningen med egen buffert. Varje socket-läsning begränsas av den tid som återstår
    av förfrågans tidsgräns, så en klient som skickar en byte i taget kan inte hålla anslutningen."""

    def __init__(self, raw, sock):
        self._raw, self._sock, self._buf, self._eof = raw, sock, bytearray(), False
        self.reset(time.monotonic() + REQUEST_DEADLINE, HEADER_LIMIT)

    def reset(self, deadline, header_limit):
        self.deadline, self.header_left = deadline, header_limit

    def _fill(self):
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError('förfrågan tog för lång tid')
        self._sock.settimeout(max(0.05, left))
        try:
            chunk = self._raw.read1(65536)
        except (socket.timeout, TimeoutError):
            raise TimeoutError('förfrågan tog för lång tid') from None
        if not chunk:
            self._eof = True
        self._buf += chunk

    def readline(self, size=-1):
        while True:
            i = self._buf.find(b'\n')
            if i >= 0 or self._eof:
                n = i + 1 if i >= 0 else len(self._buf)
                if size is not None and size >= 0:
                    n = min(n, size)
                line = bytes(self._buf[:n])
                del self._buf[:n]
                self.header_left -= len(line)
                if self.header_left < 0:
                    raise _TooLarge()
                return line
            if len(self._buf) > self.header_left or (size is not None and 0 <= size <= len(self._buf)):
                raise _TooLarge()
            self._fill()

    def read(self, n=-1):
        if n is None or n < 0:
            raise ValueError('obegränsad läsning stöds inte')
        while len(self._buf) < n and not self._eof:
            self._fill()
        data = bytes(self._buf[:n])
        del self._buf[:n]
        return data

    def __getattr__(self, name):
        return getattr(self._raw, name)


class Handler(AccountRoutes, AdminRoutes, ApiRoutes, CostRoutes, PrintersRoutes, PrintsRoutes, ShopRoutes, SpoolsRoutes,
              BaseHTTPRequestHandler):
    server_version = 'katalog'
    sys_version = ''
    # Neutrala felsidor (inte http.servers standardtext som avslöjar programvaran).
    error_message_format = '<!doctype html><title>%(code)d</title><p>%(code)d %(message)s</p>'
    error_content_type = 'text/html; charset=utf-8'
    protocol_version = 'HTTP/1.1'     # keep-alive för snabb sidladdning
    timeout = 10                      # sekunder per läsning; stänger även vilande keep-alive
    disable_nagle_algorithm = True    # annars ~40 ms extra per svar (Nagle + fördröjd ACK)

    def setup(self):
        super().setup()
        self._t0 = time.monotonic()
        self.rfile = _ConnReader(self.rfile, self.connection)

    def finish(self):
        try:
            super().finish()
        finally:
            db.close_thread_conn()

    # ---------- loggning ----------
    def log_message(self, fmt, *args):
        msg = fmt % args
        if 'timed out' in msg:
            return  # tysta anslutningar som stängs efter timeout är normalt
        print(f'[http] {self.client_address[0]} {msg}', flush=True)

    def log_request(self, code='-', size='-'):
        pass  # vi loggar själva i send()/static, med tid och utan frågesträng

    def _log(self, code, size):
        path = ''.join(c if c.isprintable() and c != '"' else '?' for c in urllib.parse.urlsplit(self.path).path[:120])
        path = re.sub(r'^/delad/[^/]+', '/delad/***', path)   # delningslänkens token hamnar inte i loggen
        ms = int((time.monotonic() - getattr(self, '_t0', time.monotonic())) * 1000)
        if path != '/healthz':
            print(f'{self.client_ip} "{self.command} {path}" {code} {size} {ms}ms', flush=True)

    def handle_one_request(self):
        self._t0 = time.monotonic()
        self._deadline = self._t0 + REQUEST_DEADLINE
        for attr in ('_sess', '_body_read', '_cl'):
            self.__dict__.pop(attr, None)
        self.rfile.reset(self._deadline, HEADER_LIMIT)
        try:
            super().handle_one_request()
        except _TooLarge:
            self.close_connection = True
            try:
                self.connection.settimeout(self.timeout)
                self.send_error(431, 'Request Header Fields Too Large')
            except OSError:
                pass
        except (TimeoutError, socket.timeout):
            self.close_connection = True

    def parse_request(self):
        if not super().parse_request():
            return False
        self.connection.settimeout(self.timeout)   # skrivning av svaret får vanlig timeout
        values = self.headers.get_all('Content-Length') or []
        bad_name = any(not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", k) for k in self.headers.keys())
        if bad_name or self.headers.defects or self.headers.get_payload() or self.headers.get('Transfer-Encoding') or len(values) > 1 or \
                (values and not _CL.fullmatch(values[0].strip())):
            self.close_connection = True
            self.send_error(400, 'Bad Request framing')
            return False
        self._cl = int(values[0]) if values else 0
        if not _is_trusted(self._peer()) and (self.headers.get('X-Forwarded-For') or self.headers.get('X-Forwarded-Proto')) \
                and self._peer() not in _proxy_like:
            if _is_private(self._peer()):
                _proxy_like.add(self._peer())   # trolig proxy i lokalt nät: undantas från gränsen per IP så att alla inte delar 16 platser
            print(f'[varning] Förfrågan från {self._peer()} har X-Forwarded-*-rubriker men adressen är inte betrodd. '
                  f'Står katalogen bakom en omvänd proxy? Ange då TRUSTED_PROXIES={self._peer()}/32 så att riktiga '
                  f'klientadresser och https känns igen.', flush=True)
        if config.ALLOWED_HOSTS and not self.path.startswith('/healthz') and not self._peer().startswith('127.'):
            try:
                host = (urllib.parse.urlsplit('//' + (self.headers.get('Host') or '')).hostname or '').lower()
            except ValueError:
                host = ''
            if host not in config.ALLOWED_HOSTS:
                self.close_connection = True
                self.send_error(421, 'Misdirected Request')
                return False
        return True

    def read_body(self, limit):
        """Läser exakt Content-Length byte (max limit) inom förfrågans tidsgräns."""
        n = getattr(self, '_cl', 0)
        if n > limit:
            raise ValueError('för stor')
        data = self.rfile.read(n)          # _ConnReader: varje läsning begränsas av tidsgränsen
        self.connection.settimeout(self.timeout)
        if len(data) < n:
            raise ConnectionResetError('anslutningen stängdes')
        self._body_read = True
        return data

    def _peer(self):
        peer = self.client_address[0]
        return peer[7:] if peer.startswith('::ffff:') else peer

    @property
    def client_ip(self):
        peer = self._peer()
        if _is_trusted(peer):
            # Gå baklänges genom X-Forwarded-For och ta första adress som inte är en betrodd proxy.
            for ip in reversed([x.strip() for x in (self.headers.get('X-Forwarded-For') or '').split(',') if x.strip()]):
                try:
                    ipaddress.ip_address(ip)
                except ValueError:
                    break
                if not _is_trusted(ip):
                    return ip
        return peer

    @property
    def is_https(self):
        """https enligt en betrodd proxy (styr HSTS)."""
        return _is_trusted(self._peer()) and (self.headers.get('X-Forwarded-Proto') or '').lower() == 'https'

    @property
    def browser_https(self):
        """Webbläsaren använder https (betrodd proxy, eller Origin/Referer med https) – styr Secure-cookie."""
        if self.is_https:
            return True
        ref = self.headers.get('Origin') or self.headers.get('Referer') or ''
        return ref.lower().startswith('https://')

    def cookies(self):
        # Egen tolkning: SimpleCookie ger upp helt om någon annan app på domänen satt en cookie med t.ex. JSON.
        out = {}
        for part in (self.headers.get('Cookie') or '').split(';'):
            k, sep, v = part.strip().partition('=')
            if sep and k and k not in out:
                v = v.strip()
                out[k] = v[1:-1] if len(v) >= 2 and v[0] == v[-1] == '"' else v
        return out

    def cookie_header(self, name, value, max_age=None, http_only=True):
        secure = config.COOKIE_SECURE == 'true' or (config.COOKIE_SECURE == 'auto' and self.browser_https)
        parts = [f'{name}={value}', 'Path=/', 'SameSite=Lax']
        if http_only:
            parts.append('HttpOnly')
        if secure:
            parts.append('Secure')
        if max_age is not None:
            parts.append(f'Max-Age={int(max_age)}')
        return '; '.join(parts)

    def _common_headers(self, cache):
        self.send_header('Content-Security-Policy', CSP)
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'same-origin')
        self.send_header('Permissions-Policy', 'camera=(self), microphone=(), geolocation=()')   # kameran: skanna etiketter
        self.send_header('Cross-Origin-Opener-Policy', 'same-origin')
        self.send_header('Cross-Origin-Resource-Policy', 'same-origin')
        self.send_header('X-Robots-Tag', 'noindex, nofollow')
        self.send_header('Cache-Control', cache)
        if self.is_https:
            self.send_header('Strict-Transport-Security', 'max-age=31536000')
        # Oläst meddelandekropp: stäng anslutningen så resterna aldrig tolkas som en ny förfrågan.
        if getattr(self, '_cl', 0) > 0 and not getattr(self, '_body_read', False):
            self.close_connection = True
        if self.close_connection:
            self.send_header('Connection', 'close')

    def send(self, code, body=b'', ctype='text/html; charset=utf-8', headers=None, cache='no-store'):
        if isinstance(body, str):
            body = body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        for k, v in (headers or []):
            self.send_header(k, v)
        self._common_headers(cache)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)
        self._log(code, len(body))

    def redirect(self, location, headers=None):
        self.send(303, b'', headers=[('Location', location)] + (headers or []))

    def json(self, code, obj):
        self.send(code, json.dumps(obj, ensure_ascii=False), 'application/json; charset=utf-8')

    def form(self):
        raw = self.read_body(MAX_BODY).decode('utf-8', errors='replace')
        self._last_form_raw = raw
        return {k: v[0] for k, v in urllib.parse.parse_qs(raw, keep_blank_values=True, max_num_fields=50).items()}

    def same_origin(self):
        origin = self.headers.get('Origin')
        if not origin or origin == 'null':
            ref = self.headers.get('Referer')
            if not ref:
                return True  # gamla klienter; CSRF-token krävs ändå
            origin = ref
        host = self.headers.get('Host', '')
        return urllib.parse.urlsplit(origin).netloc.lower() == host.strip().lower()

    # ---------- sessioner ----------
    def session(self):
        if hasattr(self, '_sess'):
            return self._sess
        self._sess = None
        token = self.cookies().get(COOKIE)
        if token and len(token) < 100:
            sid = security.token_hash(token)
            s = db.get_session(sid)
            now = time.time()
            if s and s['last_seen'] > now - config.SESSION_IDLE_DAYS * 86400 \
                    and s['created'] > now - config.SESSION_MAX_DAYS * 86400:
                if now - s['last_seen'] > TOUCH_EVERY:
                    db.touch_session(sid, now)
                self._sess = s
            elif s:
                db.delete_session(sid)
        return self._sess

    def user(self):
        s = self.session()
        return {'id': s['user_id'], 'username': s['username'], 'is_admin': bool(s['is_admin']),
                'must_change_pw': bool(s['must_change_pw'])} if s else None

    def api_user(self):
        """Inloggad via session, eller via personlig API-nyckel (Authorization: Bearer) – bara för läsande API:er."""
        auth = self.headers.get('Authorization') or ''
        if auth.startswith('Bearer '):
            tok = auth[7:].strip()
            if not re.fullmatch(r'kat_[A-Za-z0-9_-]{20,80}', tok) or api_token_attempts.blocked(self.client_ip):
                api_token_attempts.hit(self.client_ip)
                return None
            row = db.api_token_user(security.token_hash(tok))
            if not row:
                api_token_attempts.hit(self.client_ip)
                return None
            return {'id': row['id'], 'username': row['username'], 'is_admin': bool(row['is_admin']), 'must_change_pw': False}
        u = self.user()
        return u if u and not u['must_change_pw'] else None

    def start_session(self, user_row):
        token = security.new_token()
        db.create_session(security.token_hash(token), user_row['id'], security.new_token(),
                          self.client_ip, self.headers.get('User-Agent'))
        return self.cookie_header(COOKIE, token, max_age=config.SESSION_MAX_DAYS * 86400)

    def check_csrf(self, form=None):
        s = self.session()
        sent = (form or {}).get('csrf') or self.headers.get('X-CSRF-Token', '')
        return bool(s) and self.same_origin() and security.safe_equal(sent, s['csrf'])

    def pre_csrf(self):
        """Token före inloggning (dubbelinskickad cookie) för /login och /setup."""
        tok = self.cookies().get(PRE_COOKIE)
        if not tok or not re.fullmatch(r'[A-Za-z0-9_-]{20,80}', tok):
            tok = security.new_token()
        return tok, self.cookie_header(PRE_COOKIE, tok, max_age=3600)

    def check_pre_csrf(self, form):
        tok = form.get('csrf') or ''
        return bool(tok) and self.same_origin() and security.safe_equal(tok, self.cookies().get(PRE_COOKIE))

    # ---------- routing ----------
    def do_HEAD(self):
        self.do_GET()

    def _reject_bad_framing(self):
        """Chunkad överföring av förfrågningskroppar stöds inte; neka hellre än att tolka fel."""
        if self.headers.get('Transfer-Encoding'):
            self.close_connection = True
            self.send(411, 'Content-Length krävs', 'text/plain; charset=utf-8')
            return True
        return False

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        try:
            if self._reject_bad_framing():
                return
            if path == '/healthz':
                return self.send(503 if STARTUP_ERROR else 200, 'fel\n' if STARTUP_ERROR else 'ok\n',
                                 'text/plain; charset=utf-8')
            if path == '/robots.txt':
                return self.send(200, 'User-agent: *\nDisallow: /\n', 'text/plain; charset=utf-8', cache='public, max-age=86400')
            if path.startswith('/assets/') or path == '/favicon.ico':
                return self.static(path, cache='public, max-age=86400')
            if STARTUP_ERROR:
                return self.send(503, pages.error_page(503, 'Katalogen kan inte starta', 'Datamappen kan inte användas. Containerns logg beskriver felet och hur det åtgärdas.'))
            if path == '/login':
                return self.get_login()
            if path == '/setup':
                return self.get_setup()
            if path == '/api/me':
                return self.api_me()
            if path == '/api/owned':
                return self.api_owned_get()
            if path == '/api/cart':
                return self.api_cart_get()
            if path == '/api/wish':
                return self.api_wish_get()
            if path == '/api/history':
                return self.api_history()
            if path == '/api/skus':
                return self.api_skus()
            if path == '/api/new':
                return self.api_new()
            if path.startswith('/api/v1/'):
                return self.api_v1(path)
            if path == '/api/printers':
                return self.api_printers()
            if path == '/api/materials':
                return self.api_materials()
            if path == '/api/tibber/live':
                return self.api_tibber_live()
            m = re.fullmatch(r'/delad/([A-Za-z0-9_-]{20,80})', path)
            if m:
                return self.get_shared(m.group(1))
            u = self.user()
            if not u:
                if APP_PATH.match(path) or store.get(path):
                    return self.redirect('/login?next=' + urllib.parse.quote(self.path, safe='/'))
                return self.send(404, pages.error_page(404, 'Sidan finns inte', 'Kontrollera adressen.'))
            if u['must_change_pw']:
                if path != '/konto':
                    return self.redirect('/konto')
            if path == '/konto':
                return self.get_account()
            if path in ('/konto/export.json', '/konto/export.zip'):
                return self.account_export(path.endswith('.zip'))
            if path == '/inkopslista':
                return self.get_cart()
            if path == '/onskelista':
                return self.get_wish()
            if path == '/kostnad':
                return self.get_cost()
            if path == '/utskrifter':
                return self.get_prints()
            if path == '/skrivare':
                return self.get_printers()
            if path == '/material':
                return self.get_materials()
            if path == '/spolar':
                return self.get_spools()
            if path == '/projekt':
                return self.get_projects()
            if path == '/samkop':
                return self.get_groupbuy()
            m = re.fullmatch(r'/projekt/(\d+)(\.csv)?', path)
            if m:
                return self.get_project(int(m.group(1)), csv_export=bool(m.group(2)))
            if path == '/skrivare/kort':
                return self.get_printer_cards()
            m = re.fullmatch(r'/skrivare/(\d+)/bild\.jpg', path)
            if m:
                return self.get_printer_image(int(m.group(1)))
            m = re.fullmatch(r'/kvitto/(\d+)', path)
            if m:
                return self.get_receipt(int(m.group(1)))
            m = re.fullmatch(r'/admin/sakerhetskopior/([\w.-]+)', path)
            if m:
                return self.get_backup_file(m.group(1))
            if path == '/admin':
                return self.get_admin()
            m = re.fullmatch(r'/admin/anvandare/(\d+)/ta-bort', path)
            if m:
                return self.get_confirm_delete(int(m.group(1)))
            if not APP_PATH.match(path):
                query = urllib.parse.urlsplit(self.path).query
                if path in ('/', '/index.html'):
                    cache = 'no-store'                                   # sidan hämtas alltid färsk
                elif query.startswith('v='):
                    cache = 'private, max-age=31536000, immutable'       # versionsmärkt skript
                else:
                    cache = 'no-cache'
                return self.static(path, cache=cache)
            self.not_found()
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            self.close_connection = True
        except Exception as ex:  # noqa: BLE001
            self.server_error(ex)

    def static(self, path, cache):
        f = store.get(path)
        if not f:
            return self.not_found()
        if self.headers.get('If-None-Match') == f.etag:
            self.send_response(304)
            self.send_header('ETag', f.etag)
            self._common_headers(cache)
            self.end_headers()
            return self._log(304, 0)
        use_gz = f.gz is not None and 'gzip' in (self.headers.get('Accept-Encoding') or '')
        body = f.gz if use_gz else f.body
        self.send_response(200)
        self.send_header('Content-Type', f.ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('ETag', f.etag)
        if f.gz is not None:
            self.send_header('Vary', 'Accept-Encoding')
        if use_gz:
            self.send_header('Content-Encoding', 'gzip')
        self._common_headers(cache)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)
        self._log(200, len(body))

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        try:
            if self._reject_bad_framing():
                return
            if STARTUP_ERROR:
                return self.send(503, pages.error_page(503, 'Katalogen kan inte starta', 'Datamappen kan inte användas. Containerns logg beskriver felet och hur det åtgärdas.'))
            if path in ('/login', '/setup'):
                if login_posts.blocked(self.client_ip):
                    self.close_connection = True
                    return self.send(429, pages.error_page(429, 'För många försök',
                                                           'Vänta en minut och försök igen.'))
                login_posts.hit(self.client_ip)
            if path == '/login':
                return self.post_login()
            if path == '/setup':
                return self.post_setup()
            if path == '/logout':
                return self.post_logout()
            u = self.user()
            if not u:
                return self.send(401, pages.error_page(401, 'Inte inloggad', 'Logga in och försök igen.'))
            if path == '/konto/losenord':
                return self.post_password()
            if path == '/konto/integrationer':
                return self.integrations_consent()
            if path == '/konto/api-nyckel' or path.startswith('/konto/api-nyckel/'):
                return self.api_token_post(path)
            if path == '/konto/logga-ut-andra':
                return self.post_logout_others()
            if u['must_change_pw']:
                return self.redirect('/konto')
            if path.startswith('/konto/bambu/'):
                return self.bambu_post(path)
            if path.startswith('/inkopslista/'):
                return self.cart_post(path)
            if path.startswith('/onskelista/'):
                return self.wish_post(path)
            if path.startswith('/kostnad/'):
                return self.cost_post(path)
            if path.startswith('/utskrifter/'):
                return self.prints_post(path)
            if path.startswith('/skrivare/'):
                return self.printers_post(path)
            if path.startswith('/spolar/'):
                return self.spools_post(path)
            if path.startswith('/projekt/'):
                return self.projects_post(path)
            if path.startswith('/samkop/'):
                return self.groupbuy_post(path)
            if path.startswith('/kvitto/'):
                return self.receipt_post(path)
            if path.startswith('/admin'):
                return self.admin_post(path)
            self.not_found()
        except ValueError:
            self.close_connection = True
            self.send(413, 'För stor förfrågan', 'text/plain; charset=utf-8')
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            self.close_connection = True
        except Exception as ex:  # noqa: BLE001
            self.server_error(ex)

    def do_PUT(self):
        try:
            if self._reject_bad_framing():
                return
            if urllib.parse.urlsplit(self.path).path == '/api/owned':
                return self.api_owned_put()
            p = urllib.parse.urlsplit(self.path).path
            if p == '/api/cart':
                return self.api_cart_put()
            if p == '/api/cart/add':
                return self.api_cart_add()
            if p == '/api/wish':
                return self.api_wish_put()
            if p == '/api/prefs':
                return self.api_prefs_put()
            if p == '/api/match':
                return self.api_match()
            if p == '/api/import':
                return self.api_import()
            m = re.fullmatch(r'/api/receipt/(\d+)', p)
            if m:
                return self.api_receipt_put(int(m.group(1)))
            self.not_found()
        except _TooLarge:
            self.close_connection = True
            self.send(413, 'För stor förfrågan', 'text/plain; charset=utf-8')
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            self.close_connection = True
        except Exception as ex:  # noqa: BLE001
            self.server_error(ex)

    def _not_allowed(self):
        self.close_connection = True
        self.send(405, 'Metoden stöds inte', 'text/plain; charset=utf-8', headers=[('Allow', 'GET, HEAD, POST, PUT')])

    do_DELETE = do_PATCH = do_OPTIONS = do_TRACE = do_CONNECT = _not_allowed

    def not_found(self):
        u = self.user()
        self.send(404, pages.error_page(404, 'Sidan finns inte', 'Kontrollera adressen.', u,
                                        self.session()['csrf'] if u else ''))

    def server_error(self, ex):
        import traceback
        print(f'[fel] {self.command} {urllib.parse.urlsplit(self.path).path}: {ex!r}', flush=True)
        traceback.print_exc()
        self.close_connection = True
        self._headers_buffer = []   # kasta en halvskriven svarsrubrik
        try:
            self.send(500, pages.error_page(500, 'Något gick fel', 'Ett oväntat fel uppstod. Försök igen.'))
        except Exception:  # noqa: BLE001
            pass

    def forbidden(self):
        u = self.user()
        self.send(403, pages.error_page(403, 'Åtkomst nekad',
                                        'Sidan kunde inte verifieras eller du saknar behörighet. Ladda om sidan och försök igen.',
                                        u, self.session()['csrf'] if u else ''))


    # ---------- kvitton ----------
    RECEIPT_TYPES = {'image/jpeg': 'jpg', 'image/png': 'png', 'image/webp': 'webp', 'application/pdf': 'pdf'}
    RECEIPT_MAX = 3 * 1024 * 1024


    # ---------- kostnadsräknare ----------
    COST_PREFS = ('cost_watts', 'cost_source', 'cost_area', 'cost_extra', 'cost_fixed', 'cost_vat', 'cost_mode',
                  'cost_kg', 'cost_wear')


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 128
    allow_reuse_address = True

    def __init__(self, addr, handler, family):
        self.address_family = family
        self._slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
        self._count_lock = threading.Lock()
        self._per_ip = {}
        super().__init__(addr, handler, bind_and_activate=False)
        if family == socket.AF_INET6:
            try:
                self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            except OSError:
                pass
        try:
            self.server_bind()
            self.server_activate()
        except BaseException:
            self.server_close()
            raise

    def process_request(self, request, client_address):
        ip = client_address[0]
        ip = ip[7:] if ip.startswith('::ffff:') else ip
        limited = not _is_trusted(ip) and ip not in _proxy_like
        with self._count_lock:
            if limited and self._per_ip.get(ip, 0) >= MAX_PER_IP:
                return self._refuse(request)
            if not self._slots.acquire(blocking=False):
                return self._refuse(request)
            if limited:
                self._per_ip[ip] = self._per_ip.get(ip, 0) + 1
        try:
            t = threading.Thread(target=self.process_request_thread, args=(request, client_address, ip, limited))
            t.daemon = True
            t.start()
        except BaseException:
            self._release(ip, limited)
            self.shutdown_request(request)

    def _refuse(self, request):
        try:
            request.sendall(b'HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\nRetry-After: 5\r\n\r\n')
        except OSError:
            pass
        self.shutdown_request(request)

    def _release(self, ip, limited):
        with self._count_lock:
            self._slots.release()
            if limited:
                n = self._per_ip.get(ip, 1) - 1
                if n <= 0:
                    self._per_ip.pop(ip, None)
                else:
                    self._per_ip[ip] = n

    def process_request_thread(self, request, client_address, ip=None, limited=False):
        try:
            self.finish_request(request, client_address)
        except Exception:  # noqa: BLE001
            self.handle_error(request, client_address)
        finally:
            self.shutdown_request(request)
            self._release(ip, limited)

    def handle_error(self, request, client_address):
        import sys
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, BrokenPipeError, TimeoutError, socket.timeout)):
            return
        print(f'[http] fel från {client_address[0]}: {exc!r}', flush=True)


def make_server():
    """Lyssnar på alla adresser (IPv4 och, om det finns, IPv6)."""
    try:
        return Server(('::', config.LISTEN_PORT), Handler, socket.AF_INET6)
    except OSError:
        return Server(('0.0.0.0', config.LISTEN_PORT), Handler, socket.AF_INET)


def local_addresses():
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('192.0.2.1', 9))   # skickar inget; ger bara utgående adress
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    return sorted(ip for ip in ips if not ip.startswith('127.'))

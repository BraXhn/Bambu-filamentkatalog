"""Webbserver: en enda process som serverar katalogen, inloggning, konto, admin och API.

Statiska filer serveras från minnet (förkomprimerade, med ETag). Varje förfrågan loggas.
"""
import http.cookies
import ipaddress
import json
import re
import socket
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import bambu, config, db, pages, scheduler, security
from .static import store

COOKIE = 'katalog_sid'
PRE_COOKIE = 'katalog_pre'
MAX_BODY = 64 * 1024
TOUCH_EVERY = 300
# scrypt kräver ~32 MB per beräkning; begränsa samtidiga beräkningar.
_hash_slots = threading.BoundedSemaphore(2)


def _hash(pw):
    with _hash_slots:
        return security.hash_password(pw)


def _verify(pw, stored):
    with _hash_slots:
        return security.verify_password(pw, stored)


def _dummy(pw):
    with _hash_slots:
        security.dummy_verify(pw)


bambu_attempts = security.RateLimiter(6, 15 * 60)
login_posts = security.RateLimiter(10, 60)          # högst 10 inloggningsförsök per minut och IP
MAX_CONNECTIONS = 128                                # samtidiga anslutningar totalt (en tråd var)
MAX_PER_IP = 16                                      # samtidiga anslutningar per klient (ej betrodd proxy)
HEADER_LIMIT = 16 * 1024                             # max storlek på förfrågningsrad + rubriker
REQUEST_DEADLINE = 10.0                              # sekunder för att skicka rubriker och kropp
_CL = re.compile(r'[0-9]{1,6}')
_proxy_like = set()   # adresser som skickar X-Forwarded-* utan att vara betrodda
_trusted = [ipaddress.ip_network(n) for n in config.TRUSTED_PROXIES]
CSP = ("default-src 'self'; img-src 'self' https://cdn.shopify.com data:; style-src 'self' 'unsafe-inline'; "
       "script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; "
       "frame-ancestors 'none'")
PUBLIC = {'/healthz', '/robots.txt', '/favicon.ico'}
APP_PATH = re.compile(r'^/(login|setup|logout|konto|admin|api)(/|$)')
STARTUP_ERROR = None   # sätts om datamappen inte går att använda; visas då på alla sidor


def _is_trusted(ip):
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(a in n for n in _trusted)
_names_cache = {}


def _catalog_names():
    """{katalognyckel: färgnamn} för kontosidan (cachas per filversion)."""
    try:
        mtime = config.CATALOG_PATH.stat().st_mtime
        if _names_cache.get('mtime') != mtime:
            data = json.loads(config.CATALOG_PATH.read_text(encoding='utf-8'))
            _names_cache.update(mtime=mtime, names={d['type'] + '|' + (d['code'] or d['name']): d['name'] for d in data})
        return _names_cache['names']
    except (OSError, ValueError):
        return {}


def _bambu_summary(user_id):
    link = db.get_bambu(user_id)
    if not link:
        return None
    try:
        matched = json.loads(link['matched'] or '{}')
    except ValueError:
        matched = {}
    return {'status': link['status'], 'lastSync': link['last_sync'], 'items': matched}


def safe_next(url):
    """Bara relativa sökvägar inom sajten (skydd mot öppen omdirigering)."""
    if not url or not url.startswith('/') or url.startswith('//') or '\\' in url or any(ord(c) < 32 for c in url):
        return '/'
    if re.match(r'^/(login|logout|setup|auth)(\b|/|\?|$)', url):
        return '/'
    return url[:500]


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


class Handler(BaseHTTPRequestHandler):
    server_version = 'katalog'
    sys_version = ''
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
            _proxy_like.add(self._peer())   # trolig proxy: undantas från gränsen per IP så att alla inte delar 16 platser
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
        c = http.cookies.SimpleCookie()
        try:
            c.load(self.headers.get('Cookie', ''))
        except http.cookies.CookieError:
            return {}
        return {k: v.value for k, v in c.items()}

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
        self.send_header('Permissions-Policy', 'camera=(), microphone=(), geolocation=()')
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
            if path == '/admin':
                return self.get_admin()
            m = re.fullmatch(r'/admin/anvandare/(\d+)/ta-bort', path)
            if m:
                return self.get_confirm_delete(int(m.group(1)))
            if not APP_PATH.match(path):
                return self.static(path, cache='no-cache')
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
            if path == '/konto/logga-ut-andra':
                return self.post_logout_others()
            if u['must_change_pw']:
                return self.redirect('/konto')
            if path.startswith('/konto/bambu/'):
                return self.bambu_post(path)
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
            self.not_found()
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
        try:
            self.send(500, pages.error_page(500, 'Något gick fel', 'Ett oväntat fel uppstod. Försök igen.'))
        except Exception:  # noqa: BLE001
            pass

    def forbidden(self):
        u = self.user()
        self.send(403, pages.error_page(403, 'Åtkomst nekad',
                                        'Sidan kunde inte verifieras eller du saknar behörighet. Ladda om sidan och försök igen.',
                                        u, self.session()['csrf'] if u else ''))

    # ---------- inloggning ----------
    def get_login(self):
        if db.user_count() == 0:
            return self.redirect('/setup')
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        nxt = safe_next(q.get('next', ['/'])[0])
        if self.user():
            return self.redirect(nxt)
        tok, ck = self.pre_csrf()
        info = 'Du är utloggad.' if q.get('ut') else ''
        self.send(200, pages.login(tok, nxt, info=info), headers=[('Set-Cookie', ck)])

    def post_login(self):
        f = self.form()
        nxt = safe_next(f.get('next', '/'))
        username = (f.get('username') or '').strip()[:40]
        password = f.get('password') or ''
        tok, ck = self.pre_csrf()

        def fail(msg, code=400):
            self.send(code, pages.login(tok, nxt, error=msg, username=username), headers=[('Set-Cookie', ck)])

        if not self.check_pre_csrf(f):
            return fail('Sidan hade gått ut. Försök igen.', 403)
        ip = self.client_ip
        generic = ('Fel användarnamn eller lösenord. Efter flera misslyckade försök spärras kontot '
                   'i 15 minuter; en administratör kan låsa upp det.')
        if security.ip_failures.blocked(ip):
            return fail('För många misslyckade försök från din adress. Vänta en stund och försök igen.', 429)
        # Räkna försöket innan den långsamma kontrollen, så samtidiga försök inte slinker förbi.
        security.ip_failures.hit(ip)
        user = db.get_user_by_name(username) if username else None
        now = time.time()
        pair = f'{username.lower()}|{ip}'
        # Spärr per konto+adress (8 fel/kvart) – en angripare kan inte låsa ute ägaren från andra adresser.
        # Global kontospärr bara vid storskalig gissning från många adresser (ACCOUNT_MAX_FAILS).
        # Många misslyckanden mot kontot från olika adresser: sakta ned i stället för att låsa ute ägaren.
        recent = security.account_failures.count(username.lower())
        if recent >= 20:
            time.sleep(min(5.0, recent / 20))
        if security.pair_failures.blocked(pair) or (user and user['locked_until'] > now):
            _dummy(password)
            print(f'[inloggning] spärrat försök mot "{username}" från {ip}', flush=True)
            return fail(generic, 401)
        ok = _verify(password, user['pw_hash']) if user else (_dummy(password) or False)
        if not ok:
            security.pair_failures.hit(pair)
            security.account_failures.hit(username.lower())
            if user:
                db.register_failure(user['id'], security.ACCOUNT_MAX_FAILS, security.ACCOUNT_LOCK_SECONDS, now)
            print(f'[inloggning] misslyckad för "{username}" från {ip}', flush=True)
            return fail(generic, 401)
        if not db.complete_login(user['id'], time.time()):
            return fail(generic, 401)  # kontot spärrades medan lösenordet kontrollerades
        security.pair_failures.reset(pair)
        security.ip_failures.forgive(ip)
        print(f'[inloggning] {user["username"]} loggade in från {ip}', flush=True)
        cookie = self.start_session(user)
        self.redirect('/konto' if user['must_change_pw'] else nxt,
                      headers=[('Set-Cookie', cookie), ('Set-Cookie', self.cookie_header(PRE_COOKIE, '', 0))])

    def post_logout(self):
        f = self.form()
        s = self.session()
        if s and not self.check_csrf(f):
            return self.redirect('/')  # utloggning från annan sajt ignoreras
        if s:
            db.delete_session(s['id'])
        self.redirect('/login?ut=1', headers=[('Set-Cookie', self.cookie_header(COOKIE, '', 0))])

    # ---------- första start ----------
    def get_setup(self):
        if db.user_count() > 0:
            return self.redirect('/login')
        tok, ck = self.pre_csrf()
        self.send(200, pages.setup(tok, need_code=not config.ALLOW_PUBLIC_SETUP), headers=[('Set-Cookie', ck)])

    def post_setup(self):
        f = self.form()
        if db.user_count() > 0:
            return self.redirect('/login')
        tok, ck = self.pre_csrf()

        def fail(msg, code=400):
            self.send(code, pages.setup(tok, msg, {'username': f.get('username', ''), 'code': f.get('code', '')},
                                        need_code=not config.ALLOW_PUBLIC_SETUP), headers=[('Set-Cookie', ck)])

        if not self.check_pre_csrf(f):
            return fail('Sidan hade gått ut. Försök igen.', 403)
        if security.ip_failures.blocked(self.client_ip):
            return fail('För många försök. Vänta en stund.', 429)
        if not config.ALLOW_PUBLIC_SETUP:
            code = db.get_setting('setup_code')
            if not code or not security.safe_equal(f.get('code', '').strip().upper(), code):
                security.ip_failures.hit(self.client_ip)
                return fail('Fel installationskod. Koden står i containerns logg.', 403)
        username = (f.get('username') or '').strip()
        err = security.validate_username(username) or security.validate_password(f.get('password', ''), username)
        if not err and f.get('password') != f.get('password2'):
            err = 'Lösenorden matchar inte.'
        if err:
            return fail(err)
        with db._lock:
            if db.user_count() > 0:
                return self.redirect('/login')
            uid = db.create_user(username, _hash(f['password']), is_admin=True)
        db.delete_setting('setup_code')
        print(f'[konto] första administratören "{username}" skapades', flush=True)
        cookie = self.start_session(db.get_user(uid))
        self.redirect('/admin', headers=[('Set-Cookie', cookie)])

    # ---------- konto ----------
    def _sessions_count(self, uid):
        return db.conn().execute('SELECT COUNT(*) FROM sessions WHERE user_id=?', (uid,)).fetchone()[0]

    def get_account(self, kind='', msg='', code=200, bkind='', bmsg=''):
        u = self.user()
        csrf = self.session()['csrf']
        section = ''
        if not u['must_change_pw']:
            section = pages.bambu_section(csrf, db.get_bambu(u['id']), db.get_bambu_pending(u['id']),
                                          _catalog_names(), bkind, bmsg)
        self.send(code, pages.account(u, csrf, self._sessions_count(u['id']), kind, msg,
                                      must_change=u['must_change_pw'], bambu=section))

    def post_password(self):
        f = self.form()
        if not self.check_csrf(f):
            return self.forbidden()
        u = self.user()
        row = db.get_user(u['id'])
        if security.ip_failures.blocked(self.client_ip):
            return self.get_account('err', 'För många försök. Vänta en stund.', 429)
        security.ip_failures.hit(self.client_ip)
        if not _verify(f.get('current', ''), row['pw_hash']):
            return self.get_account('err', 'Nuvarande lösenord stämmer inte.', 400)
        new = f.get('new', '')
        err = security.validate_password(new, u['username'])
        if not err and new != f.get('new2'):
            err = 'De nya lösenorden matchar inte.'
        if not err and _verify(new, row['pw_hash']):
            err = 'Välj ett annat lösenord än det nuvarande.'
        if err:
            return self.get_account('err', err, 400)
        db.update_user(u['id'], pw_hash=_hash(new), must_change_pw=0)
        db.delete_user_sessions(u['id'], except_id=self.session()['id'])
        self._sess = db.get_session(self.session()['id'])
        self.get_account('ok', 'Lösenordet är bytt. Andra enheter har loggats ut.')

    def post_logout_others(self):
        f = self.form()
        if not self.check_csrf(f):
            return self.forbidden()
        db.delete_user_sessions(self.user()['id'], except_id=self.session()['id'])
        self.get_account('ok', 'Övriga enheter är utloggade.')

    # ---------- admin ----------
    def _admin_page(self, kind='', msg='', secret=None, code=200):
        u = self.user()
        runs = db.recent_runs(10)
        try:
            off = json.loads((config.WWW_DIR / 'offers.json').read_text(encoding='utf-8'))
            o = off.get('offers', [])
            offers_info = f"{len(o)} för {len({x['key'] for x in o})} färger"
        except (OSError, ValueError):
            offers_info = '—'
        self.send(code, pages.admin(u, self.session()['csrf'], db.list_users(), runs, scheduler.next_run_text(),
                                    offers_info, kind, msg, secret))

    def get_admin(self):
        if not self.user()['is_admin']:
            return self.forbidden()
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        msgs = {'start': ('ok', 'Prisuppdateringen har startat.'), 'busy': ('warn', 'En uppdatering pågår redan.')}
        kind, msg = msgs.get(q.get('m', [''])[0], ('', ''))
        self._admin_page(kind, msg)

    def get_confirm_delete(self, uid):
        u = self.user()
        if not u['is_admin']:
            return self.forbidden()
        target = db.get_user(uid)
        if not target or target['id'] == u['id']:
            return self.redirect('/admin')
        self.send(200, pages.confirm_delete(u, self.session()['csrf'], target))

    def admin_post(self, path):
        u = self.user()
        f = self.form()
        if not u['is_admin'] or not self.check_csrf(f):
            return self.forbidden()
        if path == '/admin/priser/uppdatera':
            started = scheduler.trigger('manual')
            return self.redirect('/admin?m=' + ('start' if started else 'busy') + '#priser')
        if path == '/admin/anvandare':
            return self.admin_create(f)
        m = re.fullmatch(r'/admin/anvandare/(\d+)/(losenord|roll|las-upp|logga-ut|ta-bort)', path)
        if not m:
            return self.not_found()
        target = db.get_user(int(m.group(1)))
        action = m.group(2)
        if not target:
            return self._admin_page('err', 'Användaren finns inte.', code=404)
        if target['id'] == u['id']:
            return self._admin_page('err', 'Ändra ditt eget konto under Mitt konto.', code=400)
        name = target['username']
        if action == 'losenord':
            pw = security.generate_password()
            db.update_user(target['id'], pw_hash=_hash(pw), must_change_pw=1, failed_logins=0, locked_until=0)
            db.delete_user_sessions(target['id'])
            print(f'[admin] {u["username"]} återställde lösenordet för "{name}"', flush=True)
            return self._admin_page(secret=(f'Nytt lösenord för {name}', pw))
        if action == 'roll':
            if not db.toggle_admin_keep_one(target['id']):
                return self._admin_page('err', 'Det måste finnas minst en administratör.', code=400)
            print(f'[admin] {u["username"]} ändrade roll för "{name}"', flush=True)
            return self._admin_page('ok', f'{name} är nu {"vanlig användare" if target["is_admin"] else "administratör"}.')
        if action == 'las-upp':
            db.update_user(target['id'], failed_logins=0, locked_until=0)
            return self._admin_page('ok', f'{name} är upplåst.')
        if action == 'logga-ut':
            db.delete_user_sessions(target['id'])
            return self._admin_page('ok', f'{name} är utloggad från alla enheter.')
        if action == 'ta-bort':
            if not db.delete_user_keep_admin(target['id']):
                return self._admin_page('err', 'Det måste finnas minst en administratör.', code=400)
            print(f'[admin] {u["username"]} tog bort "{name}"', flush=True)
            return self._admin_page('ok', f'{name} är borttagen.')

    def admin_create(self, f):
        username = (f.get('username') or '').strip()
        err = security.validate_username(username)
        if not err and db.get_user_by_name(username):
            err = f'Användarnamnet {username} finns redan.'
        pw = f.get('password') or ''
        generated = not pw
        if generated:
            pw = security.generate_password()
        elif not err:
            err = security.validate_password(pw, username)
        if err:
            return self._admin_page('err', err, code=400)
        db.create_user(username, _hash(pw), is_admin=f.get('role') == 'admin', must_change_pw=True)
        print(f'[admin] {self.user()["username"]} skapade "{username}"', flush=True)
        if generated:
            return self._admin_page(secret=(f'{username} är tillagd. Lösenord:', pw))
        return self._admin_page('ok', f'{username} är tillagd. Användaren byter lösenord vid första inloggningen.')

    # ---------- Bambu Lab-koppling ----------
    def bambu_post(self, path):
        f = self.form()
        if not self.check_csrf(f):
            return self.forbidden()
        uid = self.user()['id']
        action = path.rsplit('/', 1)[-1]
        if action not in ('koppla', 'kod', 'ny-kod', 'synka', 'koppla-fran', 'avbryt'):
            return self.not_found()
        if action == 'koppla-fran':
            db.delete_bambu(uid)
            print(f'[bambu] användare {uid} kopplade från sitt Bambu-konto', flush=True)
            return self.get_account(bkind='ok', bmsg='Bambu Lab-kontot är frånkopplat. Synkade markeringar är borttagna.')
        if action == 'avbryt':
            db.clear_bambu_pending(uid)
            return self.redirect('/konto#bambu')
        # Skydda både Bambu-kontot och servern: högst 6 inloggnings-/synkförsök per kvart och användare.
        if bambu_attempts.blocked(uid):
            return self.get_account(bkind='err', bmsg='För många försök. Vänta en kvart och försök igen.', code=429)
        bambu_attempts.hit(uid)
        try:
            if action == 'synka':
                ok, msg = bambu.sync_user(uid)
                return self.get_account(bkind='ok' if ok else 'err', bmsg=msg)
            if action == 'koppla':
                account = (f.get('account') or '').strip()[:120]
                region = f.get('region') if f.get('region') in bambu.API else 'global'
                if not account or not f.get('password'):
                    return self.get_account(bkind='err', bmsg='Fyll i e-post och lösenord.', code=400)
                try:
                    token = bambu.login(account, f['password'], region)
                except bambu.NeedCode:
                    db.set_bambu_pending(uid, account, region, 'code')
                    return self.get_account()
                except bambu.NeedTfa as t:
                    db.set_bambu_pending(uid, account, region, 'tfa', t.tfa_key)
                    return self.get_account()
                return self._bambu_linked(uid, account, region, token)
            pending = db.get_bambu_pending(uid)
            if not pending:
                return self.get_account(bkind='err', bmsg='Inloggningen hos Bambu Lab hann gå ut. Börja om.', code=400)
            if action == 'ny-kod':
                bambu.request_code(pending['account'], pending['region'])
                db.set_bambu_pending(uid, pending['account'], pending['region'], 'code')
                return self.get_account(bkind='ok', bmsg='En ny kod har skickats.')
            code = re.sub(r'\s', '', f.get('code') or '')[:12]
            if pending['step'] == 'tfa':
                token = bambu.login_with_tfa(pending['tfa_key'], code, pending['region'])
            else:
                token = bambu.login_with_code(pending['account'], code, pending['region'])
            return self._bambu_linked(uid, pending['account'], pending['region'], token)
        except bambu.BambuError as ex:
            return self.get_account(bkind='err', bmsg=str(ex), code=400)

    def _bambu_linked(self, uid, account, region, token):
        db.save_bambu_token(uid, account, region, token, bambu.token_expiry(token))
        print(f'[bambu] användare {uid} kopplade Bambu-kontot', flush=True)
        ok, msg = bambu.sync_user(uid)
        return self.get_account(bkind='ok' if ok else 'err', bmsg=('Kontot är kopplat. ' if ok else 'Kontot är kopplat, men ') + msg)

    # ---------- API ----------
    def api_me(self):
        u = self.user()
        if not u:
            return self.json(401, {'error': 'inte inloggad'})
        self.json(200, {'username': u['username'], 'isAdmin': u['is_admin'], 'csrf': self.session()['csrf']})

    def api_owned_get(self):
        u = self.user()
        if not u or u['must_change_pw']:
            return self.json(401, {'error': 'inte inloggad'})
        items = db.get_owned(u['id'])
        self.json(200, {'items': items or [], 'saved': items is not None, 'bambu': _bambu_summary(u['id'])})

    def api_owned_put(self):
        u = self.user()
        if not u or u['must_change_pw']:
            return self.json(401, {'error': 'inte inloggad'})
        if not self.check_csrf():
            return self.json(403, {'error': 'ogiltig token'})
        if not (self.headers.get('Content-Type') or '').startswith('application/json'):
            return self.json(415, {'error': 'JSON krävs'})
        if getattr(self, '_cl', 0) > MAX_BODY:
            return self.json(413, {'error': 'för stor'})
        try:
            data = json.loads(self.read_body(MAX_BODY))
            items = data['items']
            if not isinstance(items, list) or len(items) > 5000 or \
                    not all(isinstance(x, str) and 0 < len(x) <= 120 for x in items):
                raise ValueError
        except (ValueError, KeyError, TypeError):
            return self.json(400, {'error': 'ogiltig data'})
        items = sorted(set(items))
        db.set_owned(u['id'], items)
        self.json(200, {'items': items, 'saved': True})


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

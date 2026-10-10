"""HTTP-server bakom nginx (lyssnar bara på 127.0.0.1).

nginx frågar /auth/check (auth_request) inför varje statisk fil. Allt annat
(inloggning, konto, admin, API) går hit via proxy.
"""
import http.cookies
import ipaddress
import json
import re
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config, db, pages, scheduler, security

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


def safe_next(url):
    """Bara relativa sökvägar inom sajten (skydd mot öppen omdirigering)."""
    if not url or not url.startswith('/') or url.startswith('//') or '\\' in url or any(ord(c) < 32 for c in url):
        return '/'
    if re.match(r'^/(login|logout|setup|auth)(\b|/|\?|$)', url):
        return '/'
    return url[:500]


class Handler(BaseHTTPRequestHandler):
    server_version = 'katalog'
    sys_version = ''
    # HTTP/1.0 + Connection: close: varje anslutning bär exakt en förfrågan, så ett oläst
    # meddelandekropp kan aldrig tolkas som en ny (smugglad) förfrågan.
    protocol_version = 'HTTP/1.0'

    # ---------- hjälpare ----------
    def log_message(self, fmt, *args):  # nginx loggar redan förfrågningarna
        pass

    @property
    def client_ip(self):
        ip = self.headers.get('X-Real-IP', '') or self.client_address[0]
        try:
            return str(ipaddress.ip_address(ip.strip()))
        except ValueError:
            return self.client_address[0]

    @property
    def is_https(self):
        return self.headers.get('X-Forwarded-Proto', '').lower() == 'https'

    def cookies(self):
        c = http.cookies.SimpleCookie()
        try:
            c.load(self.headers.get('Cookie', ''))
        except http.cookies.CookieError:
            return {}
        return {k: v.value for k, v in c.items()}

    def cookie_header(self, name, value, max_age=None, http_only=True):
        secure = config.COOKIE_SECURE == 'true' or (config.COOKIE_SECURE == 'auto' and self.is_https)
        parts = [f'{name}={value}', 'Path=/', 'SameSite=Lax']
        if http_only:
            parts.append('HttpOnly')
        if secure:
            parts.append('Secure')
        if max_age is not None:
            parts.append(f'Max-Age={int(max_age)}')
        return '; '.join(parts)

    def send(self, code, body=b'', ctype='text/html; charset=utf-8', headers=None):
        if isinstance(body, str):
            body = body.encode('utf-8')
        self.close_connection = True
        self.send_response(code)
        self.send_header('Connection', 'close')
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        for k, v in (headers or []):
            self.send_header(k, v)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def redirect(self, location, headers=None):
        self.send(303, b'', headers=[('Location', location)] + (headers or []))

    def json(self, code, obj):
        self.send(code, json.dumps(obj, ensure_ascii=False), 'application/json; charset=utf-8')

    def form(self):
        length = int(self.headers.get('Content-Length') or 0)
        if length > MAX_BODY:
            raise ValueError('för stor')
        raw = self.rfile.read(length).decode('utf-8', errors='replace')
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
        return self.same_origin() and security.safe_equal(form.get('csrf'), self.cookies().get(PRE_COOKIE))

    # ---------- routing ----------
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        try:
            if path == '/auth/check':
                return self.auth_check()
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
                return self.redirect('/login?next=' + urllib.parse.quote(self.path, safe=''))
            if path == '/konto':
                return self.get_account()
            if u['must_change_pw']:
                return self.redirect('/konto')
            if path == '/admin':
                return self.get_admin()
            m = re.fullmatch(r'/admin/anvandare/(\d+)/ta-bort', path)
            if m:
                return self.get_confirm_delete(int(m.group(1)))
            self.not_found()
        except Exception as ex:  # noqa: BLE001
            self.server_error(ex)

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        try:
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
            if path.startswith('/admin'):
                return self.admin_post(path)
            self.not_found()
        except ValueError:
            self.send(413, 'För stor förfrågan', 'text/plain; charset=utf-8')
        except Exception as ex:  # noqa: BLE001
            self.server_error(ex)

    def do_PUT(self):
        try:
            if urllib.parse.urlsplit(self.path).path == '/api/owned':
                return self.api_owned_put()
            self.not_found()
        except Exception as ex:  # noqa: BLE001
            self.server_error(ex)

    def not_found(self):
        u = self.user()
        self.send(404, pages.error_page(404, 'Sidan finns inte', 'Kontrollera adressen.', u,
                                        self.session()['csrf'] if u else ''))

    def server_error(self, ex):
        import traceback
        traceback.print_exc()
        try:
            self.send(500, pages.error_page(500, 'Något gick fel', 'Ett oväntat fel uppstod. Försök igen.'))
        except Exception:  # noqa: BLE001
            pass

    def forbidden(self):
        u = self.user()
        self.send(403, pages.error_page(403, 'Åtkomst nekad',
                                        'Sidan kunde inte verifieras eller du saknar behörighet. Ladda om sidan och försök igen.',
                                        u, self.session()['csrf'] if u else ''))

    # ---------- auth för nginx ----------
    def auth_check(self):
        u = self.user()
        if u and not u['must_change_pw']:
            return self.send(204, headers=[('X-Auth-User', u['username'])])
        self.send(401)

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
        if user and user['locked_until'] > now:
            _dummy(password)
            print(f'[inloggning] försök mot spärrat konto "{username}" från {ip}', flush=True)
            return fail(generic, 401)
        ok = _verify(password, user['pw_hash']) if user else (_dummy(password) or False)
        if not ok:
            if user:
                db.register_failure(user['id'], security.ACCOUNT_MAX_FAILS, security.ACCOUNT_LOCK_SECONDS, now)
            print(f'[inloggning] misslyckad för "{username}" från {ip}', flush=True)
            return fail(generic, 401)
        if not db.complete_login(user['id'], time.time()):
            return fail(generic, 401)  # kontot spärrades medan lösenordet kontrollerades
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

    def get_account(self, kind='', msg='', code=200):
        u = self.user()
        self.send(code, pages.account(u, self.session()['csrf'], self._sessions_count(u['id']), kind, msg,
                                      must_change=u['must_change_pw']))

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

    # ---------- API ----------
    def api_me(self):
        u = self.user()
        if not u:
            return self.json(401, {'error': 'inte inloggad'})
        self.json(200, {'username': u['username'], 'isAdmin': u['is_admin'], 'csrf': self.session()['csrf']})

    def api_owned_get(self):
        u = self.user()
        if not u:
            return self.json(401, {'error': 'inte inloggad'})
        items = db.get_owned(u['id'])
        self.json(200, {'items': items or [], 'saved': items is not None})

    def api_owned_put(self):
        u = self.user()
        if not u:
            return self.json(401, {'error': 'inte inloggad'})
        if not self.check_csrf():
            return self.json(403, {'error': 'ogiltig token'})
        if not (self.headers.get('Content-Type') or '').startswith('application/json'):
            return self.json(415, {'error': 'JSON krävs'})
        length = int(self.headers.get('Content-Length') or 0)
        if length > MAX_BODY:
            return self.json(413, {'error': 'för stor'})
        try:
            data = json.loads(self.rfile.read(length))
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
    request_queue_size = 64


def serve():
    srv = Server(('127.0.0.1', config.APP_PORT), Handler)
    print(f'[app] lyssnar på 127.0.0.1:{config.APP_PORT}', flush=True)
    srv.serve_forever()

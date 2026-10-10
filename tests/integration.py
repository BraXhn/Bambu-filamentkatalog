"""Integrationstest mot en körande container: python3 tests/integration.py http://127.0.0.1:8000

Kräver att containern startats med ADMIN_USERNAME=ci-admin och ADMIN_PASSWORD=$CI_ADMIN_PASSWORD.
Skriver GitHub-annoteringar (::error / ::notice) så resultatet syns i Actions.
"""
import gzip
import http.cookiejar
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = sys.argv[1].rstrip('/') if len(sys.argv) > 1 else 'http://127.0.0.1:8000'
HOST = urllib.parse.urlsplit(BASE).netloc
PW = os.environ.get('CI_ADMIN_PASSWORD', 'Ci-test-fras-som-ar-lang-42')
results = []


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def client():
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar), NoRedirect()), jar


def req(op, path, data=None, method=None, headers=None):
    h = {'Origin': BASE}
    h.update(headers or {})
    body = urllib.parse.urlencode(data).encode() if isinstance(data, dict) else data
    r = urllib.request.Request(BASE + path, data=body, method=method, headers=h)
    try:
        with op.open(r, timeout=20) as x:
            return x.status, x.headers, x.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


def check(cond, name, detail=''):
    results.append((bool(cond), name))
    print(('PASS ' if cond else 'FAIL ') + name + (f' – {detail}' if detail and not cond else ''), flush=True)
    if not cond:
        print(f'::error title=Integrationstest::{name} {detail}'.replace('\n', ' '))


def csrf(html):
    m = re.search(r'name="csrf" value="([^"]+)"', html)
    return m.group(1) if m else ''


anon, _ = client()
s, h, b = req(anon, '/healthz')
check(s == 200, 'healthz svarar 200')
for path in ('/', '/index.html', '/app.js', '/data.json', '/offers.json'):
    s, h, b = req(anon, path)
    check(s == 303 and h.get('Location', '').startswith('/login?next='), f'{path} kräver inloggning', f'{s} {h.get("Location")}')
s, h, b = req(anon, '/assets/ui.css')
check(s == 200 and b'--accent' in b, 'stilmall för inloggningssidan är publik')
s, h, b = req(anon, '/login')
check(s == 200 and b'Logga in' in b, 'inloggningssidan visas')
for hdr in ('Content-Security-Policy', 'X-Content-Type-Options', 'X-Frame-Options', 'Referrer-Policy'):
    check(h.get(hdr), f'säkerhetsrubrik {hdr}')
check(h.get('Cache-Control') == 'no-store', 'inloggningssidan cachas inte', h.get('Cache-Control'))
check('nginx/' not in (h.get('Server') or ''), 'nginx-version döljs', h.get('Server'))
s, h, b = req(anon, '/admin')
check(s == 303 and '/login' in h.get('Location', ''), '/admin kräver inloggning')
s, h, b = req(anon, '/api/owned')
check(s == 401, '/api/owned kräver inloggning')
s, h, b = req(anon, '/setup')
check(s == 303, '/setup stängd när admin finns')

# Inloggning
op, jar = client()
s, h, b = req(op, '/login?next=/')
token = csrf(b.decode())
s, h, b = req(op, '/login', {'csrf': token, 'username': 'ci-admin', 'password': 'fel-losenord', 'next': '/'})
check(s == 401, 'fel lösenord nekas')
s, h, b = req(op, '/login', {'csrf': token, 'username': 'ci-admin', 'password': PW, 'next': '/'})
check(s == 303 and h.get('Location') == '/', 'inloggning lyckas', f'{s} {h.get("Location")}')
cookie = h.get('Set-Cookie', '')
check('HttpOnly' in cookie and 'SameSite=Lax' in cookie, 'sessionscookie är HttpOnly + SameSite', cookie)
s, h, b = req(op, '/', headers={'Accept-Encoding': 'gzip'})
check(s == 200 and h.get('Content-Encoding') == 'gzip', 'katalogen serveras förkomprimerad efter inloggning', f'{s} {h.get("Content-Encoding")}')
check(b'filamentkatalog' in gzip.decompress(b) if h.get('Content-Encoding') == 'gzip' else False, 'index.html innehåll')
s, h, b = req(op, '/offers.json', headers={'Accept-Encoding': 'gzip'})
ok = s == 200
try:
    offers = json.loads(gzip.decompress(b) if h.get('Content-Encoding') == 'gzip' else b)
except Exception:  # noqa: BLE001
    offers, ok = {}, False
check(ok and len(offers.get('offers', [])) > 100, 'offers.json från datavolymen', f'{s}')
s, h, b = req(op, '/data.json')
check(s == 200 and len(json.loads(b)) > 200, 'data.json')
s, h, b = req(op, '/index.html.gz')
check(s == 404, '.gz-filer kan inte hämtas direkt')
s, h, b = req(op, '/api/me')
me = json.loads(b) if s == 200 else {}
check(me.get('username') == 'ci-admin' and me.get('isAdmin'), 'api/me')
s, h, b = req(op, '/api/owned', json.dumps({'items': ['PLA Basic|10101']}).encode(), 'PUT',
              {'Content-Type': 'application/json', 'X-CSRF-Token': me.get('csrf', '')})
check(s == 200, 'spara "Jag har" på kontot')
s, h, b = req(op, '/api/owned', json.dumps({'items': []}).encode(), 'PUT',
              {'Content-Type': 'application/json', 'X-CSRF-Token': me.get('csrf', ''), 'Origin': 'https://evil.example'})
check(s == 403, 'API nekar annan origin')
s, h, b = req(op, '/admin')
check(s == 200 and b'Prisuppdatering' in b and b'ci-admin' in b, 'adminsidan visas')
t = csrf(b.decode())
s, h, b = req(op, '/admin/anvandare', {'csrf': t, 'username': 'ci-user', 'password': '', 'role': 'user'})
check(s == 200 and b'class="secret"' in b, 'admin skapar användare via webben')
s, h, b = req(op, '/konto')
check(s == 200 and b'Byt l' in b, 'kontosidan visas')
check(b'Koppla Bambu Lab-konto' in b, 'Bambu Lab-koppling finns på kontosidan')

# HTTPS bakom omvänd proxy
p2, _ = client()
s, h, b = req(p2, '/login', headers={'X-Forwarded-Proto': 'https'})
check('max-age' in (h.get('Strict-Transport-Security') or ''), 'HSTS när proxyn anger https')
check('Secure' in (h.get('Set-Cookie') or ''), 'Secure-cookie när proxyn anger https')
s, h, b = req(p2, '/login')
check(not h.get('Strict-Transport-Security'), 'ingen HSTS över vanlig http')

# Utloggning
s, h, b = req(op, '/logout', {'csrf': t})
check(s == 303, 'utloggning')
s, h, b = req(op, '/')
check(s == 303, 'katalogen spärras efter utloggning')

# Begränsning av inloggningsförsök i nginx (10/min, burst 5)
rl, _ = client()
s, h, b = req(rl, '/login')
tok = csrf(b.decode())
codes = [req(rl, '/login', {'csrf': tok, 'username': 'x', 'password': 'y'})[0] for _ in range(14)]
check(429 in codes, 'nginx begränsar inloggningsförsök', str(codes))

passed = sum(1 for r in results if r[0])
print(f'::notice title=Integrationstest::{passed}/{len(results)} tester lyckades')
sys.exit(0 if passed == len(results) else 1)

"""Belastningstest: python3 tests/soak.py http://HOST:PORT ANVÄNDARE LÖSENORD [sekunder]

Flera samtidiga klienter med keep-alive bläddrar mellan sidor som en riktig användare, medan
ett antal anslutningar hålls öppna utan att skicka något (långsamma/tysta klienter).
Skriver GitHub-annoteringar och avslutar med fel om något anrop misslyckas.
"""
import http.client
import re
import socket
import statistics
import sys
import threading
import time
import urllib.parse

BASE, USER, PW = sys.argv[1].rstrip('/'), sys.argv[2], sys.argv[3]
SECONDS = float(sys.argv[4]) if len(sys.argv) > 4 else 60
u = urllib.parse.urlsplit(BASE)
HOST, PORT = u.hostname, u.port or 80
PAGES = ['/', '/app.js', '/prices-ui.js', '/data.json', '/offers.json', '/api/me', '/api/owned',
         '/konto', '/admin', '/assets/ui.css', '/healthz']


def conn():
    return http.client.HTTPConnection(HOST, PORT, timeout=15)


def login():
    c = conn()
    c.request('GET', '/login')
    r = c.getresponse(); body = r.read().decode()
    pre = re.search(r'katalog_pre=([^;]+)', r.getheader('Set-Cookie') or '').group(1)
    tok = re.search(r'name="csrf" value="([^"]+)"', body).group(1)
    data = urllib.parse.urlencode({'csrf': tok, 'username': USER, 'password': PW, 'next': '/'})
    c.request('POST', '/login', data, {'Content-Type': 'application/x-www-form-urlencoded',
                                        'Cookie': f'katalog_pre={pre}', 'Origin': BASE})
    r = c.getresponse(); r.read()
    sid = re.search(r'katalog_sid=([^;]+)', r.getheader('Set-Cookie') or '')
    assert r.status == 303 and sid, f'inloggning misslyckades: {r.status}'
    return 'katalog_sid=' + sid.group(1)


for attempt in range(10):   # integrationstestet kan just ha förbrukat minutens inloggningsförsök
    try:
        cookie = login()
        break
    except AssertionError:
        time.sleep(10)
else:
    sys.exit('kunde inte logga in')
lat, errors, count = [], [], [0]
lock = threading.Lock()
stop = time.time() + SECONDS


def browser(n):
    c = conn()
    i = n
    while time.time() < stop:
        path = PAGES[i % len(PAGES)]
        i += 1
        t0 = time.perf_counter()
        try:
            c.request('GET', path, headers={'Cookie': cookie, 'Accept-Encoding': 'gzip'})
            r = c.getresponse(); r.read()
            ok = r.status == 200
            if r.getheader('Connection', '').lower() == 'close':
                c.close(); c = conn()
        except Exception as e:  # noqa: BLE001
            ok, r = False, None
            with lock:
                errors.append(f'{path}: {e!r}')
            c.close(); c = conn()
        with lock:
            count[0] += 1
            lat.append((time.perf_counter() - t0) * 1000)
            if not ok and r is not None:
                errors.append(f'{path}: HTTP {r.status}')


# Tysta klienter: öppnar anslutningar och skickar ingenting.
idle = []
for _ in range(20):
    s = socket.create_connection((HOST, PORT), timeout=5)
    idle.append(s)

threads = [threading.Thread(target=browser, args=(k,)) for k in range(12)]
for t in threads:
    t.start()
for t in threads:
    t.join()
for s in idle:
    s.close()

lat.sort()
p50 = statistics.median(lat) if lat else 0
p95 = lat[int(len(lat) * 0.95) - 1] if lat else 0
rps = count[0] / SECONDS
summary = f'{count[0]} förfrågningar på {SECONDS:.0f} s ({rps:.0f}/s), median {p50:.1f} ms, p95 {p95:.1f} ms, fel {len(errors)}'
print(summary)
print(f'::notice title=Belastningstest::{summary}')
for e in errors[:10]:
    print(f'::error title=Belastningstest::{e}')
sys.exit(1 if errors else 0)

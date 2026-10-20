"""Bambu Lab-koppling mot en låtsasserver: inloggning med e-postkod och 2FA, synk, matchning och frånkoppling."""
import http.cookiejar
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, R)


def free_port():
    s = socket.socket(); s.bind(('127.0.0.1', 0)); p = s.getsockname()[1]; s.close(); return p


def wait(port):
    for _ in range(60):
        try:
            socket.create_connection(('127.0.0.1', port), 0.2).close(); return
        except OSError:
            time.sleep(0.1)


class NoRedir(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


class BambuFlow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.fake_port, self.port = free_port(), free_port()
        self.fake = subprocess.Popen([sys.executable, os.path.join(R, 'tests', 'fake_bambu.py'), str(self.fake_port)])
        env = dict(os.environ, KATALOG_DATA_DIR=self.tmp + '/data', 
                   KATALOG_SEED_DIR=R + '/dist', PORT=str(self.port), PRICE_UPDATE_TIME='',
                   BAMBU_SYNC_HOURS='0', ADMIN_USERNAME='anna', ADMIN_PASSWORD='Bla-bil-kor-fort-9',
                   KATALOG_BAMBU_TEST_BASE=f'http://127.0.0.1:{self.fake_port}',
                   KATALOG_ELPRIS_TEST_BASE='http://127.0.0.1:9')   # inget elpris: fast pris används, inga nätanrop
        self.app = subprocess.Popen([sys.executable, '-m', 'katalog', 'serve'], cwd=R, env=env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        wait(self.fake_port); wait(self.port)
        self.base = f'http://127.0.0.1:{self.port}'
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()), NoRedir())

    def tearDown(self):
        for p in (self.app, self.fake):
            p.terminate(); p.wait(5)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def req(self, path, data=None):
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        r = urllib.request.Request(self.base + path, data=body, headers={'Origin': self.base})
        try:
            with self.op.open(r) as x:
                return x.status, x.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def csrf(self, html):
        return re.search(r'name="csrf" value="([^"]+)"', html).group(1)

    def login(self):
        _, b = self.req('/login')
        s, _ = self.req('/login', {'csrf': self.csrf(b), 'username': 'anna', 'password': 'Bla-bil-kor-fort-9', 'next': '/'})
        self.assertEqual(s, 303)

    def owned(self):
        _, b = self.req('/api/owned')
        return json.loads(b)

    def test_code_login_sync_and_disconnect(self):
        self.login()
        s, b = self.req('/konto')
        self.assertIn('Koppla Bambu Lab-konto', b)
        t = self.csrf(b)
        s, b = self.req('/konto/bambu/koppla', {'csrf': t, 'account': 'a@example.com', 'password': 'fel', 'region': 'global'})
        self.assertEqual(s, 400); self.assertIn('godkände inte', b)
        s, b = self.req('/konto/bambu/koppla', {'csrf': t, 'account': 'a@example.com', 'password': 'kod-kravs', 'region': 'global'})
        self.assertIn('Kod från e-post', b)
        s, b = self.req('/konto/bambu/kod', {'csrf': t, 'code': '000000'})
        self.assertEqual(s, 400); self.assertIn('godkändes inte', b)
        s, b = self.req('/konto/bambu/kod', {'csrf': t, 'code': '123 456'})
        self.assertIn('Kontot är kopplat', b)
        self.assertIn('4 spolar i biblioteket: 3 kopplade till 2 färger', b)  # tom/arkiverad räknas inte
        self.assertIn('Polymaker PolyTerra PLA', b)     # omatchad spole listas
        self.assertNotIn('<script>', b)                 # ogiltig färg från Bambu escapas/filtreras
        self.assertIn('dagar kvar', b)
        self.assertIn('2 nya utskrifter hämtade', b)    # utskriftshistoriken hämtas vid synken
        s, p = self.req('/utskrifter')
        self.assertIn('Benchy', p); self.assertIn('Misslyckad', p); self.assertIn('från Bambu Lab', p)
        self.assertIn('PLA Basic · Black 12', p)        # AMS-facket kopplat till katalogfärgen
        self.assertIn('PLA Basic|10101', self.owned()['forecast'])
        s, p = self.req('/utskrifter/hamta', {'csrf': t})
        self.assertIn('Inga nya avslutade utskrifter', p)   # inga dubbletter
        o = self.owned()['bambu']
        self.assertEqual(o['items']['PLA Basic|10101'], {'grams': 1640, 'total': 2000, 'spools': 2})
        self.assertEqual(o['items']['PETG HF|33102']['grams'], 210)
        self.assertNotIn('PLA Matte|11100', o['items'])
        token = sqlite3.connect(self.tmp + '/data/katalog.db').execute('select token from bambu_links').fetchone()[0]
        self.assertNotIn(token, b)                      # token visas aldrig
        s, b = self.req('/konto/bambu/synka', {'csrf': t})
        self.assertIn('3 kopplade', b)
        s, b = self.req('/konto/bambu/synka', {'csrf': 'fel'})
        self.assertEqual(s, 403)
        # Utgången token: senast kända innehav behålls och användaren uppmanas logga in igen
        db = sqlite3.connect(self.tmp + '/data/katalog.db'); db.execute("update bambu_links set token='gammal'"); db.commit()
        s, b = self.req('/konto/bambu/synka', {'csrf': t})
        self.assertIn('gått ut', b); self.assertIn('Logga in igen', b)
        self.assertEqual(self.owned()['bambu']['status'], 'expired')
        self.assertIn('PLA Basic|10101', self.owned()['bambu']['items'])
        s, b = self.req('/konto/bambu/koppla-fran', {'csrf': t})
        self.assertIn('frånkopplat', b)
        self.assertIsNone(self.owned()['bambu'])

    def test_tfa_and_rate_limit(self):
        self.login()
        _, b = self.req('/konto'); t = self.csrf(b)
        s, b = self.req('/konto/bambu/koppla', {'csrf': t, 'account': 'a@example.com', 'password': 'tfa-kravs', 'region': 'global'})
        self.assertIn('autentiseringsapp', b)
        s, b = self.req('/konto/bambu/kod', {'csrf': t, 'code': '654321'})
        self.assertIn('Kontot är kopplat', b)
        codes = [self.req('/konto/bambu/synka', {'csrf': t})[0] for _ in range(6)]
        self.assertEqual(codes[-1], 429)


class Matching(unittest.TestCase):
    def test_exact_code_beats_hex_guess(self):
        from katalog.bambu import SpoolMatcher, summarize
        cat = json.load(open(os.path.join(R, 'dist', 'data.json'), encoding='utf-8'))
        colors = [{'fila_color_code': '99999', 'fila_id': 'GFA00', 'fila_type': 'PLA Basic', 'fila_color': ['#000000FF']}]
        m = SpoolMatcher(cat, colors)
        # Känd kod som saknas i katalogen ska inte gissas till en annan svart PLA Basic
        self.assertIsNone(m.match({'filamentName': 'PLA Basic', 'filamentId': 'GFA00', 'colors': ['#000000FF']}))
        # Utan tabell: exakt hex inom samma typ
        m2 = SpoolMatcher(cat, [])
        d = m2.match({'filamentName': 'PLA Basic', 'colors': ['#000000FF']})
        self.assertEqual(d and d['code'], '10101')
        matched, unmatched = summarize([{'filamentName': 'Okänd', 'colors': ['#123456'], 'netWeight': 5, 'status': 0}], m2)
        self.assertEqual((matched, unmatched[0]['color']), ({}, '#123456'))


if __name__ == '__main__':
    unittest.main()

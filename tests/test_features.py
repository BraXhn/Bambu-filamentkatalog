"""Skrivare via MQTT, Home Assistant, säsongsråd, samköp, närmaste färg, export/import och Spoolman."""
import datetime
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, R)
sys.path.insert(0, os.path.join(R, 'tests'))
from fake_mqtt import Broker  # noqa: E402
from katalog import bambu, colors, config, dataio, db, groupbuy, homeassistant, mqtt, printers, season, spoolman  # noqa: E402

CATALOG = json.loads(Path(R, 'dist', 'data.json').read_text(encoding='utf-8'))
NAMES = {d['type'] + '|' + (d['code'] or d['name']): d['name'] for d in CATALOG}


class Base(unittest.TestCase):
    def setUp(self):
        self.saved = {k: getattr(config, k) for k in ('DATA_DIR', 'DB_PATH', 'STATE_DIR', 'CATALOG_PATH')}
        self.tmp = Path(tempfile.mkdtemp())
        config.DATA_DIR, config.DB_PATH, config.STATE_DIR = self.tmp, self.tmp / 'katalog.db', self.tmp / 'state'
        config.CATALOG_PATH = Path(R, 'dist', 'data.json')
        config.STATE_DIR.mkdir()
        (config.STATE_DIR / 'bambu_color_codes.json').write_text('[]')   # ingen hämtning från nätet
        db._local.conn = None
        db.init()
        self.uid = db.create_user('anna', 'x')

    def tearDown(self):
        db.conn().close()
        db._local.conn = None
        for k, v in self.saved.items():
            setattr(config, k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)


REPORT = {'print': {'gcode_state': 'RUNNING', 'mc_percent': 40, 'mc_remaining_time': 30, 'subtask_name': 'Benchy',
                    'layer_num': 50, 'total_layer_num': 120, 'nozzle_temper': 220.0, 'bed_temper': 55.0,
                    'ams': {'tray_now': '0', 'ams': [{'id': '0', 'tray': [
                        {'id': '0', 'tray_type': 'PLA', 'tray_sub_brands': 'PLA Basic', 'tray_color': '000000FF', 'tray_info_idx': 'GFA00', 'remain': 63},
                        {'id': '1', 'tray_type': 'PLA', 'tray_sub_brands': 'PLA Basic', 'tray_color': 'FFFFFFFF', 'tray_info_idx': 'GFA00', 'remain': 10}]}]}}}


class Printers(Base):
    def test_lan_status_and_ams(self):
        broker = Broker(password='12345678', reports={'device/SERIAL1/report': [json.dumps(REPORT),
                                                                               json.dumps({'print': {'mc_percent': 45}})]})
        orig_plain = printers._TEST_PLAIN
        os.environ['KATALOG_MQTT_TEST_PORT'] = str(broker.port)
        printers._TEST_PLAIN = True
        try:
            db.save_printer(self.uid, 'SERIAL1', 'Verkstan', 'P1S', 'lan', '127.0.0.1', '12345678')
            pid = db.list_printers(self.uid)[0]['id']
            printers.start(pid)
            for _ in range(50):
                st = printers.status(self.uid, bambu.SpoolMatcher(CATALOG, []))
                if st[0]['percent'] == 45:
                    break
                time.sleep(0.1)
            p = st[0]
            self.assertTrue(p['connected'])
            self.assertEqual((p['state'], p['percent'], p['left'], p['job']), ('RUNNING', 45.0, 30.0, 'Benchy'))   # ändring sammanslagen
            self.assertEqual([t['key'] for t in p['trays']], ['PLA Basic|10101', 'PLA Basic|10100'])
            self.assertTrue(p['trays'][0]['active'])
            self.assertTrue(any(t.endswith('/request') and 'pushall' in m for t, m, _ in broker.published))
            printers.stop_one(pid)
        finally:
            printers._TEST_PLAIN = orig_plain
            broker.close()

    def test_wrong_access_code(self):
        broker = Broker(password='rätt')
        try:
            with self.assertRaises(mqtt.MqttError):
                mqtt.Client('127.0.0.1', broker.port, 'bblp', 'fel', tls=False).connect()
        finally:
            broker.close()


class HomeAssistant(Base):
    def test_publish_discovery(self):
        broker = Broker()
        try:
            for k, v in (('host', '127.0.0.1'), ('port', str(broker.port)), ('user_id', str(self.uid))):
                db.set_setting('ha_' + k, v)
            db.set_pref(self.uid, 'cost_source', 'fast')
            db.save_bambu_token(self.uid, 'a@x', 'global', 'tok', 0)
            db.save_bambu_sync(self.uid, 'ok', matched={'PLA Basic|10101': {'grams': 120, 'total': 1000, 'spools': 1}}, unmatched=[], token='tok')
            n = homeassistant.publish_all(force=True)
            time.sleep(0.3)
            topics = {t: (m, r) for t, m, r in broker.published}
            self.assertGreaterEqual(n, 2)
            cfg = json.loads(topics['homeassistant/sensor/bambu_katalog_filament_pla_basic_10101/config'][0])
            self.assertEqual(cfg['unit_of_measurement'], 'g')
            self.assertTrue(topics['homeassistant/sensor/bambu_katalog_filament_pla_basic_10101/config'][1])   # retained
            self.assertEqual(topics['katalog/filament_pla_basic_10101/state'][0], '120')
            self.assertEqual(topics['katalog/filament_snart_slut/state'][0], '1')
            homeassistant.event('malpris', {'farger': ['PLA Basic Black']})
            time.sleep(0.3)
            self.assertTrue(any(t == 'katalog/event' and 'malpris' in m for t, m, _ in broker.published))
        finally:
            broker.close()


class Season(Base):
    def test_calendar(self):
        self.assertEqual(season.black_friday(2026), datetime.date(2026, 11, 27))
        self.assertIn('Black Friday', season.calendar_hint(datetime.date(2026, 11, 12)))
        self.assertIn('Mellandagsrean', season.calendar_hint(datetime.date(2026, 12, 10)))
        self.assertIsNone(season.calendar_hint(datetime.date(2026, 6, 1)))

    def test_seasonal_advice(self):
        d0 = datetime.date(2025, 6, 1)
        for i in range(400):
            day = d0 + datetime.timedelta(days=i)
            db.save_daily_prices(day.isoformat(), {'PLA Basic|10101': 200 if day.month == 12 else 260})
        tip = season.advice('PLA Basic|10101', datetime.date(2026, 11, 12))
        self.assertIn('december', tip)
        self.assertIsNone(season.advice('PLA Basic|10100'))


class GroupBuy(Base):
    def test_split(self):
        b = db.create_user('bo', 'x')
        db.set_pref(b, 'samkop_ok', '1')
        offers = [dict(key=k, shop='3DJake Sverige', price=250, shipping=None, fee=0, weight=1000, pack='Spole', kind='fixed',
                       condition='Ny', endAt=None, url=f'https://www.3djake.se/{k}') for k in ('PLA Basic|10101', 'PLA Basic|10100')]
        for uid, k in ((self.uid, 'PLA Basic|10101'), (b, 'PLA Basic|10100')):
            db.set_cart_item(uid, k, 2, 'any')
        self.assertEqual({u['id'] for u in groupbuy.participants(self.uid)}, {self.uid, b})
        together, people = groupbuy.plan([self.uid, b], offers, CATALOG)
        self.assertEqual(people[self.uid]['goods'], 500)
        self.assertAlmostEqual(people[self.uid]['total'] + people[b]['total'], together['totals']['total'])
        self.assertLess(together['totals']['total'], people[self.uid]['alone'] + people[b]['alone'])   # fri frakt tillsammans


class ColorsAndData(Base):
    def test_nearest(self):
        hit = colors.nearest('#010101', CATALOG, 1)[0][1]
        self.assertEqual(hit['hex'][0].upper()[:7], '#000000')
        self.assertLess(colors.distance('#ff0000', '#fe0000'), 1)

    def test_export_import_roundtrip(self):
        db.set_owned(self.uid, ['PLA Basic|10101'])
        db.set_wish(self.uid, ['PLA Basic|10100'])
        db.set_alert(self.uid, 'PLA Basic|10100', 250, None)
        db.set_cart_item(self.uid, 'PLA Basic|10101', 3, 'Refill')
        db.add_purchases(self.uid, [('3DJake Sverige', 'PLA Basic|10101', 1, 249, 'Spole', 'https://www.3djake.se/x', 1000)])
        pid = db.create_project(self.uid, 'Hylla', 'Kalle')
        db.update_project(self.uid, pid, colors=['PLA Basic|10101'])
        new = db.add_print(self.uid, 1_700_000_000, 'Benchy', 'A1', 1.5, {'PLA Basic|10101': 14}, 3.5, 0.3, 0.15, 0, 3.8)
        db.update_print(self.uid, new, status='failed', reason='Lossnade från plattan', project_id=pid)
        db.set_pref(self.uid, 'tibber_token', 'hemligt')
        data = dataio.export(db.get_user(self.uid))
        self.assertNotIn('hemligt', json.dumps(data))
        other = db.create_user('ny', 'x')
        n = dataio.import_data(other, data, NAMES)
        self.assertEqual((n['markeringar'], n['önskelista'], n['köp'], n['projekt'], n['utskrifter']), (1, 1, 1, 1, 1))
        p = db.get_prints(other)[0]
        self.assertEqual((p['status'], p['reason']), ('failed', 'Lossnade från plattan'))
        self.assertEqual(db.get_project(other, p['project_id'])['name'], 'Hylla')
        self.assertEqual(dataio.import_data(other, data, NAMES)['utskrifter'], 0)          # ingen dubblering
        with self.assertRaises(ValueError):
            dataio.import_data(other, {'format': 'annat'}, NAMES)
        zipped = dataio.export_csv_zip(db.get_user(self.uid), NAMES)
        self.assertEqual(zipped[:2], b'PK')


class FakeSpoolman(BaseHTTPRequestHandler):
    spools = [{'id': 1, 'remaining_weight': 640, 'archived': False, 'comment': '',
               'filament': {'id': 5, 'name': 'PLA Basic', 'material': 'PLA', 'color_hex': '000000', 'vendor': {'id': 1, 'name': 'Bambu Lab'}}},
              {'id': 2, 'remaining_weight': 300, 'archived': False, 'comment': '',
               'filament': {'id': 6, 'name': 'PolyTerra', 'material': 'PLA', 'color_hex': '123456', 'vendor': {'id': 2, 'name': 'Polymaker'}}}]
    created = []

    def log_message(self, *a):
        pass

    def _out(self, obj):
        b = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header('Content-Length', str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path.startswith('/api/v1/spool'):
            return self._out(self.spools)
        if self.path == '/api/v1/vendor':
            return self._out([{'id': 1, 'name': 'Bambu Lab'}])
        if self.path == '/api/v1/filament':
            return self._out([])
        self._out([])

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        FakeSpoolman.created.append((self.path, body))
        self._out(dict(body, id=99))


class Spoolman(Base):
    def test_pull_and_push(self):
        srv = ThreadingHTTPServer(('127.0.0.1', 0), FakeSpoolman)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f'http://127.0.0.1:{srv.server_port}'
        orig = bambu.get_spools
        try:
            self.assertIsNone(spoolman.check_url(base))
            self.assertIsNotNone(spoolman.check_url('http://8.8.8.8:7912'))
            total, matched, unmatched = spoolman.pull(base, self.uid)
            self.assertEqual((total, matched, unmatched), (2, 1, 1))
            self.assertEqual(json.loads(db.get_pref(self.uid, 'spoolman_matched'))['PLA Basic|10101']['grams'], 640)
            db.save_bambu_token(self.uid, 'a@x', 'global', 'tok', 0)
            bambu.get_spools = lambda token, region='global': [{'id': 77, 'filamentName': 'PLA Basic', 'filamentId': 'GFA00',
                                                               'colors': ['#FFFFFFFF'], 'netWeight': 900, 'totalNetWeight': 1000, 'status': 0}]
            created, updated = spoolman.push(base, self.uid)
            self.assertEqual((created, updated), (1, 0))
            spool = [b for p, b in FakeSpoolman.created if p == '/api/v1/spool'][-1]
            self.assertEqual(spool['remaining_weight'], 900)
            self.assertIn('bambu:77', spool['comment'])
        finally:
            bambu.get_spools = orig
            srv.shutdown()


if __name__ == '__main__':
    unittest.main()

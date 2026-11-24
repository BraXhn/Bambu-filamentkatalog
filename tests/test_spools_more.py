"""Spolar, materialguide, underhåll, kamerabild, låst butik, rabattkoder och veckosammanfattning."""
import datetime
import json
import os
import shutil
import socket
import struct
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, R)
from katalog import alerts, cart, config, db, materials, printers, spools, weekly  # noqa: E402

CATALOG = json.loads(Path(R, 'dist', 'data.json').read_text(encoding='utf-8'))
NOW = datetime.datetime(2026, 11, 24, tzinfo=datetime.timezone.utc)


class Base(unittest.TestCase):
    def setUp(self):
        self.saved = {k: getattr(config, k) for k in ('DATA_DIR', 'DB_PATH', 'STATE_DIR', 'CATALOG_PATH')}
        self.tmp = Path(tempfile.mkdtemp())
        config.DATA_DIR, config.DB_PATH, config.STATE_DIR = self.tmp, self.tmp / 'katalog.db', self.tmp / 'state'
        config.CATALOG_PATH = Path(R, 'dist', 'data.json')
        db._local.conn = None
        db.init()
        self.uid = db.create_user('bea', 'x')

    def tearDown(self):
        db.conn().close()
        db._local.conn = None
        for k, v in self.saved.items():
            setattr(config, k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)


class Spools(Base):
    def test_deduct_weigh_and_dry(self):
        a = db.add_spool(self.uid, key='PLA Basic|10101', name='Svart A', material='PLA Basic', remaining_g=100, opened='2026-11-01')
        b = db.add_spool(self.uid, key='PLA Basic|10101', name='Svart B', material='PLA Basic', remaining_g=1000)
        spools.deduct(self.uid, {'PLA Basic|10101': 150, '': 20})
        self.assertEqual(db.get_spool(self.uid, a)['remaining_g'], 0)          # öppnade spolen först
        sb = db.get_spool(self.uid, b)
        self.assertEqual(sb['remaining_g'], 950)
        self.assertIsNotNone(sb['opened'])                                      # nästa spole öppnas
        self.assertEqual(spools.summary(self.uid), {'PLA Basic|10101': {'grams': 950, 'spools': 1}})
        self.assertEqual(spools.from_weight(1100, 250), 850)
        old = db.add_spool(self.uid, key=None, name='PETG', material='PETG', remaining_g=500, opened='2026-09-01')
        self.assertTrue(spools.dry_status(db.get_spool(self.uid, old), datetime.date(2026, 11, 24))[0])
        self.assertFalse(spools.dry_status(db.get_spool(self.uid, a), datetime.date(2026, 11, 24))[0])
        before = time.time() - 3600
        spools.deduct(self.uid, {'PLA Basic|10101': 10}, ts=before)          # gammal utskrift: spolen fanns inte
        self.assertEqual(db.get_spool(self.uid, b)['remaining_g'], 950)

    def test_available_one_rule_and_bambu_import(self):
        db.save_bambu_token(self.uid, 'a@x', 'global', 'tok', 0)
        db.save_bambu_sync(self.uid, 'ok', matched={'PLA Basic|10101': {'grams': 500, 'total': 1000, 'spools': 1}}, unmatched=[], token='tok')
        db.set_pref(self.uid, 'spoolman_matched', json.dumps({'PLA Basic|10101': {'grams': 600, 'spools': 1}}))
        reserve = db.add_spool(self.uid, key='PLA Basic|10101', name='Reserv', material='PLA Basic', remaining_g=300)
        self.assertEqual(spools.available(self.uid)['PLA Basic|10101'], 600)   # största värdet, inte summan
        spools.deduct(self.uid, {'PLA Basic|10101': 50}, ts=time.time() + 10, only_opened=True)
        self.assertEqual(db.get_spool(self.uid, reserve)['remaining_g'], 300)  # oöppnad reserv orörd vid Bambu-import

    def test_materials(self):
        self.assertTrue(materials.for_type('PETG-CF')['hardened'])
        self.assertEqual(materials.for_type('PA6-CF')['open_days'], 3)
        self.assertEqual(materials.for_type('TPU for AMS')['ams'], 'Ja (gjort för AMS)')
        self.assertIsNone(materials.for_type('Okänt'))


class Maintenance(Base):
    def test_due_after_hours(self):
        db.save_printer(self.uid, 'SER1', 'Verkstan', 'P1S', 'lan', '192.168.1.9', '12345678')
        row = db.list_printers(self.uid)[0]
        db.add_print(self.uid, time.time(), 'Stor', 'Verkstan · P1S', 60, {}, 0, 0, 0, 0, 0)
        items, hours = printers.maintenance(self.uid, row)
        self.assertEqual(hours, 60)
        plate = next(i for i in items if i['task'] == 'platta')
        self.assertTrue(plate['due'])
        db.set_maintenance_done(row['id'], 'platta', 60)
        items, _ = printers.maintenance(self.uid, row)
        self.assertFalse(next(i for i in items if i['task'] == 'platta')['due'])


class Camera(Base):
    def test_snapshot(self):
        jpeg = b'\xff\xd8' + b'x' * 100 + b'\xff\xd9'
        srv = socket.socket()
        srv.bind(('127.0.0.1', 0))
        srv.listen(1)
        got = {}

        def serve():
            c, _ = srv.accept()
            got['auth'] = c.recv(80)
            c.sendall(struct.pack('<IIII', len(jpeg), 0, 1, 0) + jpeg)
            c.close()
        threading.Thread(target=serve, daemon=True).start()
        os.environ['KATALOG_CAMERA_TEST_PORT'] = str(srv.getsockname()[1])
        orig = printers._TEST_PLAIN
        printers._TEST_PLAIN = True
        try:
            db.save_printer(self.uid, 'SER2', 'A1', 'A1', 'lan', '127.0.0.1', 'abc12345')
            row = db.list_printers(self.uid)[0]
            self.assertEqual(printers.snapshot(row), jpeg)
            self.assertIn(b'abc12345', got['auth'])
            self.assertEqual(struct.unpack('<I', got['auth'][:4])[0], 0x40)
            db.update_printer(self.uid, row['id'], mode='cloud')
            self.assertIsNone(printers.snapshot(db.get_printer(self.uid, row['id'])))   # bara lokalt
        finally:
            printers._TEST_PLAIN = orig
            srv.close()


class Planner(Base):
    OFFERS = [dict(key='PLA Basic|10101', shop=s, price=p, shipping=sh, fee=0, weight=1000, pack='Spole', kind='fixed', condition='Ny',
                   endAt=None, url=f'https://www.3djake.se/{s}') for s, p, sh in
              (('3DJake Sverige', 200, 115), ('3DExperten', 250, 69))]

    def test_locked_shop_and_discount(self):
        p = cart.plan([('PLA Basic|10101', 1, 'any', '3DExperten')], self.OFFERS, CATALOG, NOW)
        self.assertEqual(p['shops'][0]['shop'], '3DExperten')
        p = cart.plan([('PLA Basic|10101', 1, 'any', 'Bambu Lab EU')], self.OFFERS, CATALOG, NOW)
        self.assertEqual(p['missing'][0]['shop'], 'Bambu Lab EU')
        disc = cart.apply_discounts(self.OFFERS, {'3DExperten': 40})
        self.assertEqual(disc[1]['price'], 150)
        self.assertEqual(self.OFFERS[1]['price'], 250)                     # originalet orört
        p = cart.plan([('PLA Basic|10101', 1, 'any')], disc, CATALOG, NOW)
        self.assertEqual(p['shops'][0]['shop'], '3DExperten')               # 150 + 69 < 200 + 115


class Weekly(Base):
    def test_due_and_build(self):
        self.assertTrue(weekly.due(datetime.datetime(2026, 11, 29, 18, 5), '2026-47'))
        self.assertFalse(weekly.due(datetime.datetime(2026, 11, 29, 18, 5), '2026-48'))
        self.assertFalse(weekly.due(datetime.datetime(2026, 11, 28, 18, 5), None))
        db.add_spool(self.uid, key=None, name='Gammal PETG', material='PETG', remaining_g=500, opened='2026-01-01')
        db.add_print(self.uid, time.time() - 3600, 'Test', None, 1, {'PLA Basic|10101': 30}, 6, 1, 0.1, 0, 7)
        orig = cart.load_offers
        cart.load_offers = lambda: ([], None)
        try:
            lines = weekly.build(self.uid, {'PLA Basic|10101': 'Black'})
        finally:
            cart.load_offers = orig
        self.assertTrue(any('Gammal PETG' in x for x in lines))
        self.assertTrue(any('1 utskrifter i veckan, 30 g' in x for x in lines))

    def test_short_public_topic(self):
        self.assertTrue(alerts.topic_too_short('https://ntfy.sh/kort'))
        self.assertFalse(alerts.topic_too_short(alerts.parse_target(alerts.random_topic())))



class Security(Base):
    def test_lan_ip(self):
        orig = printers._TEST_PLAIN
        printers._TEST_PLAIN = False
        try:
            for bad in ('0.0.0.0', '0.0.0.1', '127.0.0.1', '169.254.169.254', '::', 'fe80::1', '8.8.8.8', '224.0.0.1', 'evil.example'):
                self.assertFalse(printers.lan_ip_ok(bad), bad)
            for good in ('192.168.1.50', '10.0.0.7', '172.16.5.4'):
                self.assertTrue(printers.lan_ip_ok(good), good)
        finally:
            printers._TEST_PLAIN = orig

    def test_ha_event_needs_selected_user_and_consent(self):
        from katalog import homeassistant
        sent = []
        orig_client, orig_check = homeassistant._client, homeassistant.check_host
        homeassistant.check_host = lambda h, p: None

        class C:
            def publish(self, t, m, retain=False):
                sent.append(t)

            def close(self):
                pass
        homeassistant._client = lambda s: C()
        try:
            other = db.create_user('annan', 'x')
            db.set_setting('ha_host', '192.168.1.2')
            db.set_setting('ha_user_id', str(self.uid))
            homeassistant.event('utskrift', {}, uid=other)
            homeassistant.event('utskrift', {}, uid=self.uid)            # vald men utan samtycke
            self.assertEqual(sent, [])
            db.set_pref(self.uid, 'integrations_ok', '1')
            homeassistant.event('utskrift', {}, uid=self.uid)
            self.assertEqual(sent, ['katalog/event'])
        finally:
            homeassistant._client, homeassistant.check_host = orig_client, orig_check


if __name__ == '__main__':
    unittest.main()

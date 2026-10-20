"""Utskriftslogg och prognos, offert, elprisläge, säkerhetskopior och Tibbers WebSocket-ramar."""
import datetime
import json
import os
import shutil
import socket
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from katalog import backup, config, costcalc, db, energy, printlog  # noqa: E402


class WithDb(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.saved = {k: getattr(config, k) for k in ('DATA_DIR', 'DB_PATH', 'STATE_DIR', 'WWW_DIR', 'BACKUP_DIR', 'RECEIPT_DIR')}
        cls.tmp = Path(tempfile.mkdtemp())
        config.DATA_DIR, config.DB_PATH = cls.tmp, cls.tmp / 'katalog.db'
        config.STATE_DIR, config.WWW_DIR = cls.tmp / 'state', cls.tmp / 'www'
        config.BACKUP_DIR, config.RECEIPT_DIR = cls.tmp / 'backups', cls.tmp / 'receipts'
        db._local.conn = None
        db.init()
        cls.uid = db.create_user('skrivare', 'x')

    @classmethod
    def tearDownClass(cls):
        db.conn().close()
        db._local.conn = None
        for k, v in cls.saved.items():
            setattr(config, k, v)
        shutil.rmtree(cls.tmp, ignore_errors=True)


class PrintLog(WithDb):
    def test_stats_and_forecast(self):
        now = time.time()
        db.add_print(self.uid, now - 10 * 86400, 'A', 'A1', 2, {'PLA Basic|10101': 70, '': 5}, 15, 1, 0.2, 0, 16)
        db.add_print(self.uid, now - 2 * 86400, 'B', 'A1', 1, {'PLA Basic|10101': 70}, 14, 1, 0.1, 0, 15, status='failed')
        db.add_print(self.uid, now - 200 * 86400, 'Gammal', 'P1S', 1, {'PLA Basic|10101': 1000}, 200, 1, 0.1, 0, 201)
        f = printlog.forecast(self.uid, {'PLA Basic|10101': 300}, now)
        self.assertEqual(f['PLA Basic|10101'], {'perDay': 10.0, 'days': 30})    # 140 g på 14 dagar, gamla räknas inte
        self.assertNotIn('', f)
        st = printlog.stats(self.uid, datetime.datetime.fromtimestamp(now, energy.TZ))
        self.assertGreaterEqual(st['printers']['A1']['n'], 2)
        self.assertIn('PLA Basic|10101', st['colors'])
        self.assertFalse(db.add_print(self.uid, now, 'X', None, 1, {}, 0, 0, 0, 0, 0, source='bambu', ext_id='1') and
                         db.add_print(self.uid, now, 'X', None, 1, {}, 0, 0, 0, 0, 0, source='bambu', ext_id='1'))  # inga dubbletter

    def test_printer_watts(self):
        self.assertEqual(printlog._printer_watts('A1 mini', 1), 80)
        self.assertEqual(printlog._printer_watts('X1C', 1), 130)
        self.assertEqual(printlog._printer_watts('okänd', 77), 77)


class Quote(unittest.TestCase):
    def test_quote(self):
        q = costcalc.quote({'total': 100.0}, 50, 10, True, 10)
        self.assertAlmostEqual(q['risk'], 10)
        self.assertAlmostEqual(q['markup'], 55)
        self.assertAlmostEqual(q['ex_vat'], 165)
        self.assertAlmostEqual(q['total'], 206.25)
        self.assertEqual(q['rounded'], 210)
        with self.assertRaises(ValueError):
            costcalc.quote({'total': 1}, 10, 10, False, 3)

    def test_outlook(self):
        t0 = datetime.datetime(2026, 10, 20, 12, tzinfo=energy.TZ)
        iv = [(t0 + datetime.timedelta(hours=h), t0 + datetime.timedelta(hours=h + 1), p) for h, p in enumerate([2, 2, 1.5, 3, 1.0])]
        o = energy.price_outlook(iv, t0 + datetime.timedelta(minutes=10))
        self.assertEqual(o['now'], 2)
        self.assertEqual(o['drop'], (t0 + datetime.timedelta(hours=2), 1.5))
        self.assertEqual(o['low'], (t0 + datetime.timedelta(hours=4), 1.0))
        self.assertIsNone(energy.price_outlook([], t0))


class Backups(WithDb):
    def test_create_restore_and_prune(self):
        config.STATE_DIR.mkdir(parents=True, exist_ok=True)
        (config.STATE_DIR / 'price_history.json').write_text('{"a": 1}')
        (config.RECEIPT_DIR / str(self.uid)).mkdir(parents=True, exist_ok=True)
        (config.RECEIPT_DIR / str(self.uid) / '1.pdf').write_bytes(b'%PDF-1.4')
        db.set_pref(self.uid, 'budget', '500')
        name = backup.create(datetime.datetime(2026, 10, 20, 3, 30))
        db.set_pref(self.uid, 'budget', '999')
        (config.STATE_DIR / 'price_history.json').write_text('{}')
        backup.restore(name)
        self.assertEqual(db.get_pref(self.uid, 'budget'), '500')
        self.assertEqual(json.loads((config.STATE_DIR / 'price_history.json').read_text()), {'a': 1})
        self.assertTrue((config.RECEIPT_DIR / str(self.uid) / '1.pdf').is_file())
        for d in range(1, 12):
            backup.create(datetime.datetime(2026, 9, d, 3, 30))
        self.assertEqual(len(backup.list_backups()), config.BACKUP_KEEP)
        self.assertIsNone(backup.path_of('../katalog.db'))
        with self.assertRaises(ValueError):
            backup.restore('katalog-19990101-0000.zip')

    def test_due(self):
        saved = config.BACKUP_TIME
        try:
            config.BACKUP_TIME = '03:30'
            n = datetime.datetime(2026, 10, 20, 4, 0)
            self.assertTrue(backup.due(n, 'katalog-20261019-0330.zip'))
            self.assertFalse(backup.due(n, 'katalog-20261020-0330.zip'))
            self.assertFalse(backup.due(n.replace(hour=2), None))
            config.BACKUP_TIME = ''
            self.assertFalse(backup.due(n, None))
        finally:
            config.BACKUP_TIME = saved


class WebSocketFrames(unittest.TestCase):
    def test_roundtrip_and_ping(self):
        a, b = socket.socketpair()
        ws = object.__new__(energy._WS)
        ws.s, ws.buf = a, b''
        ws.send({'type': 'connection_init'})
        raw = b.recv(4096)
        self.assertEqual(raw[0], 0x81)                           # FIN + text
        n, mask = raw[1] & 0x7F, raw[2:6]
        self.assertTrue(raw[1] & 0x80)                           # klientramar maskeras
        self.assertEqual(json.loads(bytes(x ^ mask[i % 4] for i, x in enumerate(raw[6:6 + n]))), {'type': 'connection_init'})
        payload = json.dumps({'type': 'next', 'payload': {'x': 'å' * 200}}).encode()
        b.sendall(bytes([0x89, 2]) + b'hi' + bytes([0x81, 126]) + len(payload).to_bytes(2, 'big') + payload)  # ping, sedan text
        self.assertEqual(ws.recv()['type'], 'next')
        pong = b.recv(4096)
        self.assertEqual(pong[0], 0x8A)                          # svarade med pong
        a.close(); b.close()

    def test_rejects_other_hosts(self):
        with self.assertRaises(energy.EnergyError):
            energy._WS('wss://evil.example/graphql')


if __name__ == '__main__':
    unittest.main()

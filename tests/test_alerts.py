"""Prisbevakning, ntfy-adresser och "bästa tid att köpa" från prishistoriken."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from katalog import alerts, config, db  # noqa: E402
from katalog.prices import update  # noqa: E402


def offer(key, shop, price, shipping=50, discount=None, **kw):
    o = dict(key=key, shop=shop, title=key, url=f'https://www.3djake.se/{shop}/{price}', price=price, shipping=shipping,
             fee=0, weight=1000, pack='Spole', kind='fixed', condition='Ny', endAt=None)
    if discount:
        o['discount'] = discount
    o.update(kw)
    return o


class Alerts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.saved = config.DATA_DIR, config.DB_PATH
        cls.tmp = Path(tempfile.mkdtemp())
        config.DATA_DIR, config.DB_PATH = cls.tmp, cls.tmp / 'katalog.db'
        db._local.conn = None
        db.init()
        cls.uid = db.create_user('bevakare', 'x')

    @classmethod
    def tearDownClass(cls):
        db.conn().close()
        db._local.conn = None
        config.DATA_DIR, config.DB_PATH = cls.saved
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_best_and_hit(self):
        offers = [offer('K|1', 'A', 250), offer('K|1', 'B', 230, shipping=None), offer('K|1', 'C', 300, discount=25)]
        info = alerts.best('K|1', offers)
        self.assertEqual(info['best'][1], 300)            # 250 + 50; B saknar frakt och räknas inte
        self.assertEqual(info['deal']['shop'], 'C')
        self.assertTrue(alerts.is_hit({'target': 300, 'min_discount': None}, info))
        self.assertFalse(alerts.is_hit({'target': 299, 'min_discount': None}, info))
        self.assertTrue(alerts.is_hit({'target': None, 'min_discount': 20}, info))
        self.assertFalse(alerts.is_hit({'target': None, 'min_discount': 30}, info))

    def test_parse_target(self):
        self.assertEqual(alerts.parse_target('mitt-amne_1'), 'https://ntfy.sh/mitt-amne_1')
        self.assertEqual(alerts.parse_target('https://ntfy.sh/abc'), 'https://ntfy.sh/abc')
        for bad in ('https://evil.example/abc', 'http://ntfy.sh/abc', 'https://ntfy.sh/abc?x=1', 'https://u@ntfy.sh/abc',
                    'https://ntfy.sh:8443/abc', 'ämne med mellanslag', ''):
            self.assertIsNone(alerts.parse_target(bad), bad)

    def test_notify_once_and_again_when_better(self):
        sent = []
        orig = alerts.send
        alerts.send = lambda target, title, msg, click=None: sent.append((target, title, msg)) and None
        try:
            db.set_pref(self.uid, 'notify', 'https://ntfy.sh/test')
            db.set_alert(self.uid, 'PLA Basic|10101', 300, None)
            names = {'PLA Basic|10101': 'Black'}
            alerts.notify_all([offer('PLA Basic|10101', '3DJake', 240)], names, log=lambda m: None)
            alerts.notify_all([offer('PLA Basic|10101', '3DJake', 240)], names, log=lambda m: None)
            self.assertEqual(len(sent), 1)                      # samma träff aviseras bara en gång
            self.assertIn('PLA Basic Black: 290 kr', sent[0][2])
            alerts.notify_all([offer('PLA Basic|10101', '3DJake', 220)], names, log=lambda m: None)
            self.assertEqual(len(sent), 2)                      # billigare igen: ny avisering
            alerts.notify_all([offer('PLA Basic|10101', '3DJake', 400)], names, log=lambda m: None)
            alerts.notify_all([offer('PLA Basic|10101', '3DJake', 240)], names, log=lambda m: None)
            self.assertEqual(len(sent), 3)                      # träffen försvann och kom tillbaka
        finally:
            alerts.send = orig


class Timing(unittest.TestCase):
    def test_low90_and_usual(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            path = tmp / 'h.json'
            low, high = offer('K|1', 'A', 200), offer('K|1', 'B', 300)
            hist = {update._offer_id(low): {f'2026-09-{d:02d}': 220 for d in range(1, 21)},
                    update._offer_id(high): {f'2026-09-{d:02d}': 250 for d in range(1, 21)}}
            path.write_text(json.dumps(hist))
            update.apply_history([low, high], '2026-09-25T01:00:00+00:00', path)
            self.assertTrue(low.get('low90'))
            self.assertNotIn('usual', low)
            self.assertEqual(high.get('usual'), 250)
            self.assertNotIn('low90', high)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()

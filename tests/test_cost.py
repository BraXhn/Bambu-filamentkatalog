"""Kostnadsräknaren: elpriser (elprisetjustnu.se, Tibber), kostnad över tid, billigaste starttid och filamentpris."""
import datetime
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from katalog import costcalc, energy  # noqa: E402

TZ = energy.TZ
D0 = datetime.datetime(2026, 10, 18, tzinfo=TZ)


def quarter_prices(day, fn):
    """Syntetiskt svar i elprisetjustnu.se:s format (96 kvartar per dygn)."""
    out = []
    for i in range(96):
        s = day + datetime.timedelta(minutes=15 * i)
        out.append({'SEK_per_kWh': fn(s), 'EUR_per_kWh': 0, 'EXR': 11.1,
                    'time_start': s.isoformat(), 'time_end': (s + datetime.timedelta(minutes=15)).isoformat()})
    return out


class Energy(unittest.TestCase):
    def setUp(self):
        energy._cache.clear()
        self.orig = energy._request

    def tearDown(self):
        energy._request = self.orig
        energy._cache.clear()

    def test_spot_prices_vat_extra_and_missing_tomorrow(self):
        calls = []

        def fake(url, data=None, headers=None, timeout=10):
            calls.append(url)
            if '10-18_SE3' in url:
                return 200, json.dumps(quarter_prices(D0, lambda s: 1.0 if s.hour < 12 else 2.0)).encode()
            return 404, b''
        energy._request = fake
        pts = energy.spot_prices('SE3', D0 + datetime.timedelta(hours=8), vat=True, extra=0.5)
        self.assertEqual(len(pts), 96)
        self.assertEqual(pts[0][2], 1.75)                       # 1,00 × 1,25 + 0,50
        self.assertEqual(pts[-1][2], 3.0)
        self.assertTrue(any('/api/v1/prices/2026/10-19_SE3.json' in u for u in calls))
        with self.assertRaises(energy.EnergyError):
            energy.spot_day('SE9', D0.date())

    def test_tibber_parse_and_errors(self):
        today = [{'total': 1.5 + h / 100, 'startsAt': (D0 + datetime.timedelta(hours=h)).isoformat()} for h in range(24)]
        body = {'data': {'viewer': {'homes': [{'currentSubscription': None},
                                              {'currentSubscription': {'priceInfo': {'today': today, 'tomorrow': []}}}]}}}
        seen = {}

        def fake(url, data=None, headers=None, timeout=10):
            seen['auth'] = headers.get('Authorization')
            return 200, json.dumps(body).encode()
        energy._request = fake
        pts = energy.tibber_prices('tok-123', extra=0.25)
        self.assertEqual(seen['auth'], 'Bearer tok-123')
        self.assertEqual(len(pts), 24)
        self.assertEqual(pts[0][1] - pts[0][0], datetime.timedelta(hours=1))
        self.assertAlmostEqual(pts[0][2], 1.75)
        energy._request = lambda *a, **k: (401, b'')
        with self.assertRaises(energy.EnergyError):
            energy.check_tibber('fel-token')

    def test_test_base_only_on_loopback(self):
        self.assertEqual(energy._base('https://evil.example', 'https://www.elprisetjustnu.se'), 'https://www.elprisetjustnu.se')
        self.assertEqual(energy._base('http://127.0.0.1:9999', 'https://x'), 'http://127.0.0.1:9999')

    def test_window_cost_and_cheapest_start(self):
        # 2 kr/kWh dagtid, 0,50 kr/kWh 00–06.
        iv = [(D0 + datetime.timedelta(hours=h), D0 + datetime.timedelta(hours=h + 1), 0.5 if h % 24 < 6 else 2.0) for h in range(48)]
        c = energy.window_cost(iv, D0 + datetime.timedelta(hours=10), 2, 100)   # 0,2 kWh à 2 kr
        self.assertAlmostEqual(c['kwh'], 0.2)
        self.assertAlmostEqual(c['cost'], 0.4)
        self.assertEqual(c['covered'], 1.0)
        best = energy.cheapest_start(iv, 2, 100, D0 + datetime.timedelta(hours=10))
        self.assertEqual(best[0], D0 + datetime.timedelta(hours=24))           # nästa natt
        self.assertAlmostEqual(best[1]['cost'], 0.1)
        part = energy.window_cost(iv, D0 + datetime.timedelta(hours=47), 2, 100)  # sista timmen saknar pris
        self.assertAlmostEqual(part['covered'], 0.5)
        self.assertAlmostEqual(part['cost'], 0.4)                                # uppskattad med medelpriset


class Filament(unittest.TestCase):
    OFFERS = [dict(key='PLA Basic|10101', shop='3DJake Sverige', price=250, shipping=115, fee=0, weight=1000, pack='Spole',
                   kind='fixed', condition='Ny', endAt=None),
              dict(key='PLA Basic|10101', shop='Tradera · X', price=200, shipping=59, fee=10, weight=1000, pack='Refill',
                   kind='fixed', condition='Ny', endAt=None)]

    def test_modes(self):
        p, _ = costcalc.price_per_kg('PLA Basic|10101', 'nu', self.OFFERS, {}, None)
        self.assertEqual(p, 200)
        p, note = costcalc.price_per_kg('PLA Basic|10101', 'frakt', self.OFFERS, {}, None)
        self.assertEqual(p, 269)
        p, note = costcalc.price_per_kg('PLA Basic|10101', 'betalt', self.OFFERS, {'PLA Basic|10101': (180, 1000)}, None)
        self.assertEqual((p, note), (180, 'senaste köpet'))
        p, note = costcalc.price_per_kg('PLA Basic|10101', 'betalt', self.OFFERS, {}, None)
        self.assertEqual(p, 200)
        self.assertIn('inget eget köp', note)
        self.assertEqual(costcalc.price_per_kg('', 'nu', self.OFFERS, {}, 300)[0], 300)
        self.assertIsNone(costcalc.price_per_kg('PLA Basic|99999', 'nu', self.OFFERS, {}, None)[0])

    def test_calculate(self):
        iv = [(D0, D0 + datetime.timedelta(days=2), 2.0)]
        r = costcalc.calculate([('PLA Basic|10101', 50), ('', 10)], 'nu', 300, 1.5, 120, iv, D0, 2, self.OFFERS, {}, D0)
        self.assertAlmostEqual(r['filament'], 50 / 1000 * 200 + 10 / 1000 * 300)
        self.assertAlmostEqual(r['el']['cost'], 0.36)
        self.assertAlmostEqual(r['wear'], 3)
        self.assertAlmostEqual(r['total'], 13 + 0.36 + 3)
        self.assertFalse(r['missing'])


if __name__ == '__main__':
    unittest.main()

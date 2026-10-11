"""Inköpslistans planerare: fri frakt, samfrakt per säljare och att en annons bara räcker till en vara."""
import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from katalog.cart import plan  # noqa: E402

NOW = datetime.datetime(2026, 10, 11, tzinfo=datetime.timezone.utc)
CAT = [{'type': 'PLA Basic', 'code': c, 'name': n} for c, n in (('1', 'A'), ('2', 'B'), ('3', 'C'), ('4', 'D'), ('5', 'E'))]


def offer(key, shop, price, shipping, url, fee=0, pack='Spole'):
    return dict(key=key, shop=shop, price=price, shipping=shipping, fee=fee, url=url, pack=pack, weight=1000,
                kind='fixed', condition='Ny', endAt=None)


class Planner(unittest.TestCase):
    def test_consolidates_for_free_shipping(self):
        keys = [f'PLA Basic|{i}' for i in '12345']
        offers = []
        for i, k in enumerate(keys):
            offers.append(offer(k, '3DJake Sverige', 230, 115, f'https://www.3djake.se/{i}'))
            # Något billigare hos en annan butik, men med egen frakt per köp.
            offers.append(offer(k, f'Tradera · säljare{i}', 215, 79, f'https://www.tradera.com/item/1/{i}/x'))
        r = plan([(k, 1, 'any') for k in keys], offers, CAT, NOW)
        self.assertEqual([s['shop'] for s in r['shops']], ['3DJake Sverige'])   # 5 × 230 = 1150 ≥ 1099 → fri frakt
        self.assertEqual(r['totals']['total'], 1150)

    def test_seller_combined_shipping_and_capacity(self):
        offers = [offer('PLA Basic|1', 'Tradera · X', 150, 60, 'https://www.tradera.com/item/1/1/a', fee=10),
                  offer('PLA Basic|2', 'Tradera · X', 160, 70, 'https://www.tradera.com/item/1/2/b', fee=10),
                  offer('PLA Basic|1', '3DJake Sverige', 242, 115, 'https://www.3djake.se/a')]
        r = plan([('PLA Basic|1', 2, 'any'), ('PLA Basic|2', 1, 'any')], offers, CAT, NOW)
        x = next(s for s in r['shops'] if s['shop'] == 'Tradera · X')
        self.assertEqual((x['goods'], x['shipping'], x['fee']), (310, 70, 20))   # samfrakt: högsta frakten en gång
        jake = next(s for s in r['shops'] if s['shop'] == '3DJake Sverige')
        self.assertEqual(sum(l['qty'] for l in jake['lines']), 1)               # annonsen räcker bara till en

    def test_pack_preference_and_missing(self):
        offers = [offer('PLA Basic|1', '3DJake Sverige', 204, 115, 'https://www.3djake.se/r', pack='Refill')]
        r = plan([('PLA Basic|1', 1, 'Spole'), ('PLA Basic|3', 2, 'any')], offers, CAT, NOW)
        self.assertEqual(r['shops'], [])
        self.assertEqual(sorted((m['key'], m['qty']) for m in r['missing']), [('PLA Basic|1', 1), ('PLA Basic|3', 2)])


if __name__ == '__main__':
    unittest.main()

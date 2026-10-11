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

    def test_bambu_bulk_discount(self):
        keys = [f'PLA Basic|{i}' for i in '1234']
        offers = []
        for i, k in enumerate(keys):
            offers.append(offer(k, '3DJake Sverige', 190, 115, f'https://www.3djake.se/{i}', pack='Refill'))
            offers.append(dict(offer(k, 'Bambu Lab EU', 253, None, f'https://eu.store.bambulab.com/products/p?id={i}',
                                     pack='Refill'), fx=11.0))
        # 4 refills: 4+-nivån ger 15,99 € × 11 = 176 kr/st → 704 + 99 frakt < 3DJake 760 + 115.
        r = plan([(k, 1, 'Refill') for k in keys], offers, CAT, NOW)
        self.assertEqual([s['shop'] for s in r['shops']], ['Bambu Lab EU'])
        b = r['shops'][0]
        self.assertEqual((b['goods'], b['discount'], b['total']), (1012, 308, 1012 - 308 + 99))
        self.assertTrue(b['bulk'][0]['reached'])
        self.assertIn('nivå 4+', b['bulk'][0]['desc'])
        self.assertIn('Lägg till 2 rullar PLA Basic', b['bulk'][0]['next'])
        self.assertTrue(all(l['bulkPrice'] == 176 for l in b['lines']))
        self.assertEqual(r['totals']['discount'], 308)
        # 3 refills når ingen nivå → 3DJake blir billigast.
        r = plan([(k, 1, 'Refill') for k in keys[:3]], offers, CAT, NOW)
        self.assertEqual([s['shop'] for s in r['shops']], ['3DJake Sverige'])

    def test_percent_volume_discount(self):
        keys = [f'PLA Basic|{i}' for i in '12345']
        group = 'https://3dverkstan.se/produkt/pla/'
        offers = []
        for i, k in enumerate(keys):
            offers.append(offer(k, '3DJake Sverige', 230, 115, f'https://www.3djake.se/{i}'))
            offers.append(dict(offer(k, '3DVerkstan', 250, None, f'{group}?attribute_pa_farg={i}'),
                               bulk=[[5, 10], [10, 15]], bulkGroup=group, title='PLA Basic with Spool · X'))
        # 5 st: 5 × 225 = 1125 + 79 frakt = 1204 > 3DJake 5 × 230 = 1150 (fri frakt) → 3DJake.
        r = plan([(k, 1, 'any') for k in keys], offers, CAT, NOW)
        self.assertEqual([s['shop'] for s in r['shops']], ['3DJake Sverige'])
        # Utan 3DJake: volymrabatten 10 % räknas in.
        r = plan([(k, 1, 'any') for k in keys], [o for o in offers if o['shop'] == '3DVerkstan'], CAT, NOW)
        v = r['shops'][0]
        self.assertEqual((v['goods'], v['discount'], v['shipping']), (1250, 125, 79))
        self.assertIn('−10 %', v['bulk'][0]['desc'])
        self.assertIn('Lägg till 5 st', v['bulk'][0]['next'])


if __name__ == '__main__':
    unittest.main()

"""Prishämtarna mot syntetiska sidor med samma struktur som butikerna, samt sammanslagningsreglerna."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

R = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(R))

from katalog import config  # noqa: E402
from katalog.prices import jake, prima, tradera, update  # noqa: E402
from katalog.prices.common import FetchError, Matcher  # noqa: E402

CATALOG = json.loads((R / 'dist' / 'data.json').read_text(encoding='utf-8'))


def ld(obj):
    return f'<script type="application/ld+json">{json.dumps(obj)}</script>'


def jake_page(name, sai, price, stock=True, other=(), links=()):
    variants = ''.join(
        f'<li><input type="radio" {"checked " if s == sai else ""}id="p-variants__button{s}" name="ShopArticleItem_ID" value="{s}">'
        f'<label for="p-variants__button{s}" data-name="{n}"><p>{n}</p></label></li>' for s, n in [(sai, 'Spole'), *other])
    product = {'@context': 'https://schema.org', '@type': 'Product', 'name': f'Bambu Lab {name}, Spole',
               'offers': {'@type': 'Offer', 'price': str(price), 'priceCurrency': 'SEK',
                          'url': f'https://www.3djake.se/bambu-lab/x?sai={sai}',
                          'availability': 'https://schema.org/' + ('InStock' if stock else 'OutOfStock'),
                          'shippingDetails': {'shippingRate': {'value': 115}}}}
    a = ''.join(f'<a href="{h}">x</a>' for h in links)
    return (f'<html>{ld(product)}<h1 class="p-heading"><a href="/bambu-lab">Bambu Lab</a> {name}, Spole (1.000 g)</h1>'
            f'<ul>{variants}</ul>{a}</html>')


class FakeHttp:
    def __init__(self, pages):
        self.pages, self.requests, self.calls = pages, 0, []

    def get(self, url, **_):
        self.requests += 1
        self.calls.append(url)
        if url not in self.pages:
            raise FetchError('HTTP 404')
        v = self.pages[url]
        if isinstance(v, Exception):
            raise v
        return url, v


class Parsers(unittest.TestCase):
    def setUp(self):
        self.m = Matcher(CATALOG)
        self.log = []

    def test_matcher(self):
        self.assertEqual(self.m.key(self.m.match('Bambu Lab PLA Basic Black, Refill (1.000 g)')), 'PLA Basic|10101')
        self.assertEqual(self.m.key(self.m.match('PETG HF Färg: Black (33102) Bambu Lab filament')), 'PETG HF|33102')
        self.assertIsNone(self.m.match('Bambu Lab A1 mini Combo'))

    def test_jake_crawl_variants_and_stock(self):
        base = 'https://www.3djake.se/bambu-lab/pla-basic-black'
        other = 'https://www.3djake.se/bambu-lab/pla-basic-white'
        pages = {
            'https://www.3djake.se/bambu-lab/bambu-lab-filament': '<a href="/bambu-lab/pla-basic-black">x</a>',
            base: jake_page('PLA Basic Black', '1', 242, other=[('2', 'Refill')], links=['/bambu-lab/pla-basic-white', '/bambu-lab/a1-mini']),
            base + '?sai=2': jake_page('PLA Basic Black', '2', 204).replace('Spole (1.000 g)', 'Refill (1.000 g)'),
            other: jake_page('PLA Basic White', '3', 242, stock=False),
        }
        http = FakeHttp(pages)
        offers = jake.run(http, self.m, {'jake_urls': [base]}, self.log.append, 'NOW')
        self.assertEqual(sorted((o['key'], o['pack'], o['price']) for o in offers),
                         [('PLA Basic|10101', 'Refill', 204), ('PLA Basic|10101', 'Spole', 242)])
        self.assertNotIn('https://www.3djake.se/bambu-lab/a1-mini', http.calls)  # bara filamentlänkar följs
        self.assertIn(other, http.calls)  # syskonfärg upptäcks

    def test_prima(self):
        url = 'https://www.3dprima.com/se/x_1_2'
        page = ld({'@type': 'Product', 'offers': {'@type': 'Offer', 'price': '339.00',
                                                  'availability': 'http://schema.org/InStock'}}) + \
            'Lagersaldo: <strong> 50+ </strong>'
        t = {'url': url, 'key': 'PLA Basic|10101', 'title': 'T', 'pack': 'Spole', 'weight': 1000}
        offers = prima.run(FakeHttp({url: page}), self.m, {'prima_targets': [t]}, self.log.append, 'NOW')
        self.assertEqual(offers[0]['price'], 339)
        self.assertIn('50+', offers[0]['sourceNote'])

    def test_tradera(self):
        listing = {'itemId': 1, 'price': 399, 'shortDescription': 'PETG HF Färg: Black (33102) Bambu Lab filament',
                   'itemUrl': 'https://www.tradera.com/item/1/1/x', 'isActive': True, 'sellerCountryCodeIso2': 'SE',
                   'shippingOptions': [{'cost': 64}], 'attributes': [{'name': 'condition', 'values': ['Oanvänt']}]}
        foreign = dict(listing, itemId=2, sellerCountryCodeIso2='DE', itemUrl='https://www.tradera.com/item/1/2/x')
        flight = ('x"discover/receiveSearchResults","payload":{"result":{"items":' + json.dumps([listing, foreign]) +
                  ',"pagination":{"pageIndex":1,"pageCount":1,"pageSize":80}}}')
        search = f'<script>self.__next_f.push([1,{json.dumps(flight)}])</script>'
        details = {'itemId': 1, 'categoryId': 9, 'title': listing['shortDescription'], 'description': '$38',
                   'isActive': True, 'hasEnded': False, 'fixedPrice': 419, 'canBeBought': True,
                   'endDate': '2099-01-01T00:00:00Z', 'seller': {'alias': 'Säljare'},
                   'shippingOptions': [{'cost': 74, 'toCountryCodeIso2': 'SE'}],
                   'paymentCalculations': {'isBuyerProtectionMandatory': True, 'buyerProtectionCostForFixedPrice': 23}}
        item = (f'<script>self.__next_f.push([1,{json.dumps("y" + json.dumps(details, separators=(",", ":")))}])</script>' +
                ld({'@type': 'Product', 'description': 'Nettovikt: 1 kg/rulle. Levereras med spole.'}))
        pages = {'https://www.tradera.com/search?q=bambu+lab+filament': search, listing['itemUrl']: item}
        http = FakeHttp(pages)
        offers = tradera.run(http, self.m, {}, self.log.append, 'NOW')
        self.assertEqual(len(offers), 1)
        o = offers[0]
        self.assertEqual((o['key'], o['price'], o['fee'], o['shipping'], o['weight'], o['pack'], o['kind'], o['condition']),
                         ('PETG HF|33102', 419, 23, 74, 1000, 'Spole', 'fixed', 'Oanvänt'))
        self.assertNotIn(foreign['itemUrl'], http.calls)


class UpdateMerge(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.old = (config.DATA_DIR, config.WWW_DIR, config.STATE_DIR, config.PRICE_SOURCES, config.CATALOG_PATH)
        config.DATA_DIR, config.WWW_DIR, config.STATE_DIR = self.tmp, self.tmp / 'www', self.tmp / 'state'
        config.CATALOG_PATH = R / 'dist' / 'data.json'
        config.WWW_DIR.mkdir()
        shutil.copy(R / 'dist' / 'offers.json', config.WWW_DIR / 'offers.json')
        self.prev = json.loads((R / 'dist' / 'offers.json').read_text(encoding='utf-8'))['offers']

    def tearDown(self):
        config.DATA_DIR, config.WWW_DIR, config.STATE_DIR, config.PRICE_SOURCES, config.CATALOG_PATH = self.old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_failed_and_empty_sources_keep_previous(self):
        def boom(*a, **k):
            raise FetchError('nere')

        orig = (jake.run, prima.run, tradera.run)
        jake.run, prima.run, tradera.run = boom, (lambda *a, **k: []), (lambda *a, **k: [])
        try:
            status, summary = update.run_update(lambda m: None, ['3djake', '3dprima', 'tradera'])
        finally:
            jake.run, prima.run, tradera.run = orig
        out = json.loads((config.WWW_DIR / 'offers.json').read_text(encoding='utf-8'))['offers']
        self.assertEqual(summary['3djake']['status'], 'failed')
        self.assertEqual(summary['tradera']['status'], 'failed')  # 0 träffar men tidigare många = misstänkt
        self.assertEqual(status, 'partial' if summary['3dprima']['status'] == 'ok' else 'failed')
        # 3DJake (fel) och Tradera (misstänkt tomt) behålls; 3D Prima hade < 5 och får bli tomt (t.ex. slut i lager).
        kept = {(o['key'], o['url'], o['pack']) for o in self.prev if o['shop'] != prima.SHOP}
        self.assertEqual(len(out), len(kept))
        self.assertTrue((config.WWW_DIR / 'offers.json.gz').exists())


if __name__ == '__main__':
    unittest.main()

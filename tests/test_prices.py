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
from katalog.prices import bambustore, experten, jake, prima, tradera, update, verkstan  # noqa: E402
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


class BambuStore(unittest.TestCase):
    def test_saved_eu_store_data(self):
        products = json.loads((R / 'sources' / 'eu.json').read_text(encoding='utf-8'))['products']
        offers = bambustore.products_to_offers(products, CATALOG, 11.0, 'NOW')
        self.assertGreater(len(offers), 30)
        o = next(x for x in offers if x['key'] == 'PLA Translucent|13612')
        self.assertEqual((o['price'], o['pack'], o['weight'], o['shop']), (308, 'Spole', 1000, 'Bambu Lab EU'))
        self.assertTrue(o['url'].startswith('https://eu.store.bambulab.com/products/pla-translucent?variant='))
        self.assertTrue(all(x['listPrice'] is None or x['listPrice'] > x['price'] for x in offers))

    PAGE = ld({'@context': 'https://schema.org', '@type': 'ProductGroup', 'name': 'PLA Basic',
               'url': bambustore.BASE + '/products/pla-basic-filament',
               'hasVariant': [
                   {'@type': 'Product', 'name': 'PLA Basic - Black (10101) / Refill / 1kg',
                    'offers': {'@type': 'Offer', 'price': '15.99', 'priceCurrency': 'EUR',
                               'availability': 'https://schema.org/InStock',
                               'url': bambustore.BASE + '/products/pla-basic-filament?id=1'}},
                   {'@type': 'Product', 'name': 'PLA Basic - Black (10101) / Filament with spool / 1kg',
                    'offers': {'@type': 'Offer', 'price': '18.99', 'priceCurrency': 'EUR',
                               'availability': 'https://schema.org/InStock',
                               'url': bambustore.BASE + '/products/pla-basic-filament?id=2'}},
                   {'@type': 'Product', 'name': 'PLA Basic - Jade White (10100) / Refill / 1kg',
                    'offers': {'@type': 'Offer', 'price': '15.99', 'priceCurrency': 'EUR',
                               'availability': 'https://schema.org/OutOfStock',
                               'url': bambustore.BASE + '/products/pla-basic-filament?id=3'}},
                   {'@type': 'Product', 'name': 'PLA Basic - Black (10101) / Refill / 1kg',
                    'offers': {'@type': 'Offer', 'price': '1', 'priceCurrency': 'EUR',
                               'availability': 'https://schema.org/InStock',
                               'url': 'https://evil.example/x'}},
               ]})

    def test_product_page_json_ld(self):
        offers = bambustore.page_to_offers(self.PAGE, CATALOG, {'EUR': 11.0, 'SEK': 1.0}, 'NOW')
        self.assertEqual(sorted((o['key'], o['pack'], o['price'], o['weight']) for o in offers),
                         [('PLA Basic|10101', 'Refill', 176, 1000), ('PLA Basic|10101', 'Spole', 209, 1000)])
        self.assertTrue(all(o['shop'] == 'Bambu Lab EU' and o['listPrice'] is None for o in offers))

    def test_run_product_pages_and_redirect(self):
        ecb = '<gesmes:Envelope xmlns:gesmes="x"><Cube><Cube time="2026-10-10"><Cube currency="SEK" rate="11.12"/></Cube></Cube></gesmes:Envelope>'
        page_url = bambustore.BASE + '/products/pla-basic-filament'
        http = FakeHttp({bambustore.ECB: ecb, page_url: self.PAGE})
        offers = bambustore.run(http, Matcher(CATALOG), {}, lambda m: None, 'NOW')
        self.assertEqual(len(offers), 2)
        self.assertIn(page_url, http.calls)
        self.assertFalse(any('products.json' in u for u in http.calls))
        with self.assertRaises(FetchError):  # inga sidor alls
            bambustore.run(FakeHttp({bambustore.ECB: ecb}), Matcher(CATALOG), {}, lambda m: None, 'NOW')
        pages = {bambustore.ECB: ecb, page_url: FetchError('omdirigering till otillåten adress nekad')}
        with self.assertRaises(FetchError) as cm:
            bambustore.run(FakeHttp(pages), Matcher(CATALOG), {}, lambda m: None, 'NOW')
        self.assertIn('europeiska', str(cm.exception))
        state = {}
        self.assertEqual(bambustore.eur_to_sek(FakeHttp({bambustore.ECB: ecb}), state, lambda m: None), 11.12)


class Verkstan(unittest.TestCase):
    PRODUCT = {'id': 1, 'name': 'Bambu Lab PLA Basic with Spool &#8211; 1 kg / 1.75 mm', 'type': 'variable',
               'permalink': verkstan.BASE + '/produkt/pla-basic/', 'sku': '', 'is_in_stock': True,
               'categories': [{'slug': 'bambu-lab-filament'}], 'prices': {'price': '20000', 'currency_minor_unit': 2}}

    def page(self):
        import html as h
        variations = [
            {'attributes': {'attribute_pa_farg': 'black'}, 'sku': 'A00-K0-1.75-1000-spl', 'display_price': 200,
             'display_regular_price': 200, 'is_in_stock': True, 'availability_html': '<p>261 i lager</p>', 'variation_id': 11},
            {'attributes': {'attribute_pa_farg': 'jade-white'}, 'sku': '', 'display_price': 180,
             'display_regular_price': 200, 'is_in_stock': True, 'availability_html': '<p>3 i lager</p>', 'variation_id': 12},
            {'attributes': {'attribute_pa_farg': 'red'}, 'sku': 'A00-R0-1.75-1000-spl', 'display_price': 200,
             'display_regular_price': 200, 'is_in_stock': False, 'availability_html': '', 'variation_id': 13},
        ]
        table = ('<div class="awdr-bulk-customizable-table"><table><tbody>'
                 '<tr><td>5 - 9</td><td>225 SEK</td><td>10%</td></tr><tr><td>10 - 19</td><td>x</td><td>15%</td></tr>'
                 '</tbody></table></div>')
        return f'<form data-product_variations="{h.escape(json.dumps(variations))}"></form>{table}'

    def test_variations_vat_sku_and_bulk(self):
        offers = verkstan.page_to_offers(self.page(), self.PRODUCT, Matcher(CATALOG), 'NOW')
        got = sorted((o['key'], o['price'], o['listPrice'], o['pack']) for o in offers)
        self.assertEqual(got, [('PLA Basic|10100', 225, 250, 'Spole'), ('PLA Basic|10101', 250, None, 'Spole')])
        o = offers[0]
        self.assertEqual(o['bulk'], [[5, 10.0], [10, 15.0]])
        self.assertTrue(o['url'].startswith(verkstan.BASE + '/produkt/pla-basic/?attribute_pa_farg='))
        self.assertEqual(len({x['url'] for x in offers}), 2)

    def test_run(self):
        api = verkstan.API.format(1)
        other = dict(self.PRODUCT, id=2, name='Bambu Lab Hotend', categories=[{'slug': 'reservdelar'}],
                     permalink=verkstan.BASE + '/produkt/hotend/')
        http = FakeHttp({api: json.dumps([self.PRODUCT, other]), self.PRODUCT['permalink']: self.page()})
        offers = verkstan.run(http, Matcher(CATALOG), {}, lambda m: None, 'NOW')
        self.assertEqual(len(offers), 2)
        self.assertNotIn(other['permalink'], http.calls)


class Experten(unittest.TestCase):
    def test_translate_and_offers(self):
        def p(i, title, price, cmp=None, available=True, handle=None):
            return {'id': i, 'title': title, 'vendor': 'Bambu Lab', 'handle': handle or f'h{i}',
                    'variants': [{'id': 100 + i, 'price': str(price), 'compare_at_price': cmp, 'available': available}]}
        products = [
            p(1, 'Bambu Lab - PLA Matt - Karamell - 1,75 mm - 1 kg - Refill', '340.00'),
            p(2, '(UTGÅNG) - Bambu Lab - PLA Basic - Svart - 1,75 mm - 1 kg', '299.00', '349.00'),
            p(3, 'Bambu Lab - PETG HF - Lake Blue - 1.75mm - 1kg', '330.00', available=False),
            p(4, 'Bambu Lab - Hotend - X1', '500.00'),
            p(5, 'Bambu Lab - PLA Pure - Apricot - 1.75mm - 1kg', '325.00'),
            p(6, 'Bambu Lab - PLA Basic - Stark rosa - 1,75 mm - 1 kg - Refill', '299.00'),
            p(7, 'Bambu Lab - PLA Basic - Okänd ton rosa - 1,75 mm - 1 kg - Refill', '299.00'),
        ]
        offers = experten.products_to_offers(products, Matcher(CATALOG), 'NOW')
        got = sorted((o['key'], o['price'], o['listPrice'], o['pack'], o['weight']) for o in offers)
        hot = next((d for d in CATALOG if d['type'] == 'PLA Basic' and d['name'] == 'Hot Pink'), None)
        want = [('PLA Basic|10101', 299, 349, 'Spole', 1000), ('PLA Matte|11803', 340, None, 'Refill', 1000)]
        if hot:
            want.append((Matcher.key(hot), 299, None, 'Refill', 1000))
        self.assertEqual(got, sorted(want))     # 'Okänd ton rosa' får inte bli Pink
        self.assertTrue(all(o['url'].startswith(experten.BASE + '/products/h') for o in offers))

    def test_run_collections(self):
        colls = json.dumps({'collections': [{'handle': 'bambu-lab-pla-basic-pla-matte'}, {'handle': 'reservdelar'}]})
        prod = {'id': 1, 'title': 'Bambu Lab - PLA Basic - Orange - 1,75 mm - 1 kg - Refill', 'vendor': 'Bambu Lab',
                'handle': 'orange', 'variants': [{'id': 9, 'price': '299.00', 'available': True}]}
        pages = {experten.BASE + '/collections.json?limit=250': colls,
                 experten.BASE + '/collections/bambu-lab-pla-basic-pla-matte/products.json?limit=250&page=1':
                     json.dumps({'products': [prod]}),
                 experten.BASE + '/products.json?limit=250&page=1': json.dumps({'products': [prod]})}
        http = FakeHttp(pages)
        offers = experten.run(http, Matcher(CATALOG), {}, lambda m: None, 'NOW')
        self.assertEqual([(o['key'], o['pack']) for o in offers], [('PLA Basic|10300', 'Refill')])
        self.assertFalse(any('reservdelar' in u for u in http.calls))


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

"""3DVerkstan (WooCommerce): Bambu Lab-filament per färg med pris, lagersaldo och volymrabatt.

Butikens API ger produktlistan; varje produktsida innehåller alla färgvarianter (artikelnummer, pris
exklusive moms, lager) och tabellen för volymrabatt. Färgen kopplas via Bambu Labs artikelnummer
(t.ex. A00-K0 = PLA Basic Black 10101), annars via färgnamnet.
"""
import html
import json
import re
from pathlib import Path

from .common import FetchError, text

SHOP = '3DVerkstan'
BASE = 'https://3dverkstan.se'
API = BASE + '/wp-json/wc/store/v1/products?search=bambu&per_page=100&page={}'
CATEGORY = 'bambu-lab-filament'
VAT = 1.25          # butiken visar priser exklusive moms; privatpersoner betalar 25 % moms
MAX_PAGES = 5
SKU_CODES = json.loads(Path(__file__).with_name('bambu_skus.json').read_text(encoding='utf-8'))


def _weight(name, sku):
    m = re.search(r'-(\d{3,4})-(?:SPL|RFL|REF)', sku or '', re.I)
    if m:
        return int(m.group(1))
    m = re.search(r'(\d+(?:[.,]\d+)?)\s*(kg|g)\b', name or '', re.I)
    if not m:
        return None
    v = float(m.group(1).replace(',', '.'))
    return round(v * 1000) if m.group(2).lower() == 'kg' else round(v)


def _pack(name):
    n = (name or '').lower()
    if 'refill' in n:
        return 'Refill'
    if 'spool' in n or 'spole' in n or n.replace('bambu lab', '').strip(' -–').startswith('support for'):
        return 'Spole'
    return None


def bulk_tiers(page):
    """Volymrabatt-tabellen: [(från antal, rabatt i procent), ...]."""
    tiers = []
    table = re.search(r'awdr-bulk-customizable-table(.*?)</table>', page, re.S)
    for row in re.findall(r'<tr>(.*?)</tr>', table.group(1) if table else '', re.S):
        cells = [text(c) for c in re.findall(r'<td[^>]*>(.*?)</td>', row, re.S)]
        if len(cells) >= 3:
            n = re.match(r'(\d+)', cells[0])
            pct = re.search(r'(\d+(?:[.,]\d+)?)\s*%', cells[-1])
            if n and pct and 1 < int(n.group(1)) < 1000 and 0 < float(pct.group(1).replace(',', '.')) < 90:
                tiers.append([int(n.group(1)), float(pct.group(1).replace(',', '.'))])
    return sorted(tiers)


def page_to_offers(page, product, matcher, now):
    """Färgvarianterna på en produktsida (data-product_variations) som erbjudanden."""
    name = text(product.get('name', ''))
    pack = _pack(name)
    if not pack or re.search(r'\d\s*[×x]\s*\d', name):    # flerpack (t.ex. CMYK 4×1 kg) hoppas över
        return []
    m = re.search(r'data-product_variations="([^"]*)"', page)
    try:
        variations = json.loads(html.unescape(m.group(1))) if m else []
    except ValueError:
        variations = []
    if not isinstance(variations, list):
        variations = []
    if not variations:       # enkel produkt utan färgval
        prices = product.get('prices') or {}
        try:
            ex = int(prices['price']) / 10 ** int(prices.get('currency_minor_unit', 2))
        except (KeyError, TypeError, ValueError):
            return []
        variations = [{'sku': product.get('sku', ''), 'display_price': ex, 'is_in_stock': product.get('is_in_stock'),
                       'attributes': {}, 'availability_html': (product.get('stock_availability') or {}).get('text', '')}]
    tiers = bulk_tiers(page)
    url = product.get('permalink', '')
    if not url.startswith(BASE + '/'):
        return []
    offers = []
    for v in variations:
        if not isinstance(v, dict) or not v.get('is_in_stock'):
            continue
        sku = str(v.get('sku') or '').upper()
        color = ' '.join(str(x) for x in (v.get('attributes') or {}).values()).replace('-', ' ')
        d = None
        pre = re.match(r'([A-Z]\d{2}-[A-Z]\d{1,2})-', sku)
        code = SKU_CODES.get(pre.group(1)) if pre else None
        if code:
            d = next((x for x in matcher.data if x.get('code') == code), None)
        if not d:
            d = matcher.match(f'{name} {color}')
        if not d:
            continue
        try:
            ex = float(v['display_price'])
            reg = float(v.get('display_regular_price') or ex)
        except (KeyError, TypeError, ValueError):
            continue
        price = round(ex * VAT)
        if price <= 0:
            continue
        weight = _weight(name, sku)
        if not weight:
            continue
        stock = text(v.get('availability_html', ''))
        vid = v.get('variation_id')
        link = url + (f'?attribute_pa_farg={next(iter((v.get("attributes") or {}).values()), "")}' if vid else '')
        offers.append(dict(
            key=matcher.key(d), shop=SHOP, title=f'{name} · {d["name"]}', url=link, price=price,
            listPrice=round(reg * VAT) if reg > ex else None, shipping=None, fee=0, weight=weight, pack=pack,
            kind='fixed', condition='Ny', endAt=None, bulk=tiers or None, bulkGroup=url if tiers else None,
            shippingNote='Frakt visas i kassan.',
            sourceNote=(f'{stock}. ' if stock else '') + 'Pris inkl. 25 % moms (butiken visar exkl. moms).'
            + (' Volymrabatt: ' + ', '.join(f'{n}+ st −{p:g} %' for n, p in tiers) + '.' if tiers else ''),
            checkedAt=now))
    return offers


def run(http, matcher, state, log, now):
    products, seen = [], set()
    for page_no in range(1, MAX_PAGES + 1):
        _, body = http.get(API.format(page_no))
        try:
            batch = json.loads(body)
        except ValueError:
            raise FetchError('ogiltigt svar från 3DVerkstans produkt-API') from None
        if not isinstance(batch, list) or not batch:
            break
        for p in batch:
            if not isinstance(p, dict) or p.get('id') in seen:
                continue
            seen.add(p.get('id'))
            if any(c.get('slug') == CATEGORY for c in p.get('categories') or [] if isinstance(c, dict)):
                products.append(p)
        if len(batch) < 100:
            break
    if not products:
        raise FetchError('inga Bambu Lab-filament hittades hos 3DVerkstan')
    offers, ok, errors = [], 0, 0
    for p in products:
        if not _pack(text(p.get('name', ''))) or not str(p.get('permalink', '')).startswith(BASE + '/'):
            continue
        try:
            _, page = http.get(p['permalink'])
        except FetchError as e:
            errors += 1
            log(f"  3DVerkstan: kunde inte hämta {p['permalink']}: {e}")
            continue
        ok += 1
        offers += page_to_offers(page, p, matcher, now)
    if ok == 0:
        raise FetchError('inga produktsidor kunde hämtas från 3DVerkstan')
    log(f'  3DVerkstan: {ok} produktsidor, {len(offers)} erbjudanden i lager, {errors} fel')
    return offers

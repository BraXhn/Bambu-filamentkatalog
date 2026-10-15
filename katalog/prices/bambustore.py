"""Bambu Lab EU-butik: pris och lagerstatus per färg och förpackning, från produktsidornas JSON-LD.

Butiken prissätter i euro; priset räknas om till kronor med ECB:s dagskurs. Butiken släpper bara
igenom europeiska besökare (andra skickas till USA-butiken), vilket hämtningen kontrollerar.
"""
import json
import re
import xml.etree.ElementTree as ET

from .. import config
from .common import FetchError

SHOP = 'Bambu Lab EU'
BASE = 'https://eu.store.bambulab.com'
ECB = 'https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml'


def eur_to_sek(http, state, log):
    try:
        _, xml = http.get(ECB)
        root = ET.fromstring(xml)
        for el in root.iter():
            if el.attrib.get('currency') == 'SEK':
                rate = float(el.attrib['rate'])
                if 5 < rate < 30:
                    state['eur_sek'] = rate
                    return rate
    except (FetchError, ET.ParseError, ValueError, KeyError) as e:
        log(f'  Bambu Lab: växelkursen kunde inte hämtas ({e}); använder senast kända')
    rate = state.get('eur_sek')
    if not rate:
        raise FetchError('ingen växelkurs EUR→SEK tillgänglig')
    return rate


def _weight(text):
    m = re.search(r'(\d+(?:[.,]\d+)?)\s*(kg|g)\b', text or '', re.I)
    if not m:
        return None
    v = float(m.group(1).replace(',', '.'))
    return round(v * 1000) if m.group(2).lower() == 'kg' else round(v)


def _pack(text):
    t = (text or '').lower()
    if 'refill' in t:
        return 'Refill'
    if 'spool' in t:
        return 'Spole'
    return None


def products_to_offers(products, catalog, rate, now):
    """Shopify-format (products.json) – används för sparade underlag och om API:t öppnas igen."""
    by_code = {d['code']: d for d in catalog if d.get('code')}
    offers = []
    for p in products:
        handle = p.get('handle', '')
        for v in p.get('variants') or []:
            if not v.get('available'):
                continue
            opts = [v.get(f'option{i}') or '' for i in (1, 2, 3)]
            try:
                eur = float(v['price'])
                cmp = float(v.get('compare_at_price') or 0)
            except (KeyError, TypeError, ValueError):
                continue
            o = _offer(by_code, p.get('title', ''), opts, eur, cmp, rate,
                       f'{BASE}/products/{handle}?variant={v.get("id")}' if v.get('id') else f'{BASE}/products/{handle}',
                       'EUR', now)
            if o:
                offers.append(o)
    return offers


def _offer(by_code, product_title, opts, amount, compare, rate, url, currency, now):
    code = next((m.group(1) for o in opts for m in [re.search(r'\((\d{5})\)', o)] if m), None)
    d = by_code.get(code)
    if not d or amount <= 0:
        return None
    pack = next((_pack(o) for o in opts if _pack(o)), None)
    weight = next((_weight(o) for o in opts if _weight(o)), None)
    if not pack or not weight:
        return None
    factor = 1.0 if currency == 'SEK' else rate
    price = round(amount * factor)
    list_price = round(compare * factor) if compare and compare > amount else None
    color = re.sub(r'\s*\(\d{5}\)', '', next(o for o in opts if code in o))
    color = re.sub(r'^.*? - ', '', color).strip()
    size = f'{weight / 1000:g} kg'.replace('.', ',') if weight >= 1000 else f'{weight} g'
    if currency == 'SEK':
        note = 'I lager enligt butiken.'
    else:
        sym = '€' if currency == 'EUR' else currency
        note = f'Pris {amount:.2f} {sym} ≈ {price} kr (kurs {factor:.2f})'.replace('.', ',') + '. I lager enligt butiken.'
    return dict(key=d['type'] + '|' + (d['code'] or d['name']), shop=SHOP,
                title=f'Bambu Lab {product_title} {color} · {size} · {pack}', url=url,
                price=price, listPrice=list_price, fx=round(factor, 4), shipping=None, fee=0, weight=weight, pack=pack, kind='fixed',
                condition='Ny', endAt=None,
                shippingNote='Frakt visas i kassan.' + ('' if currency == 'SEK' else ' Betalas i euro.'),
                sourceNote=note, checkedAt=now)


def page_to_offers(page, catalog, rates, now):
    """Produktsidans JSON-LD (ProductGroup med hasVariant): 'PLA Basic - Black (10101) / Refill / 1kg'."""
    by_code = {d['code']: d for d in catalog if d.get('code')}
    offers = []
    for block in re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', page, re.S | re.I):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        for g in data if isinstance(data, list) else [data]:
            if not isinstance(g, dict) or g.get('@type') != 'ProductGroup':
                continue
            for v in g.get('hasVariant') or []:
                offer = (v or {}).get('offers') or {}
                if isinstance(offer, list):
                    offer = offer[0] if offer else {}
                if not str(offer.get('availability', '')).endswith('InStock'):
                    continue
                currency = str(offer.get('priceCurrency') or 'EUR').upper()
                if currency not in rates:
                    continue
                try:
                    amount = float(offer.get('price'))
                except (TypeError, ValueError):
                    continue
                url = offer.get('url') or g.get('url') or ''
                if not url.startswith(BASE + '/'):
                    continue
                opts = [x.strip() for x in str(v.get('name', '')).split('/')]
                o = _offer(by_code, g.get('name', ''), opts, amount, 0, rates[currency], url, currency, now)
                if o:
                    offers.append(o)
    return offers


def run(http, matcher, state, log, now):
    rate = eur_to_sek(http, state, log)
    rates = {'EUR': rate, 'SEK': 1.0}
    handles = sorted({m.group(1) for d in matcher.data
                      for m in [re.search(r'/products/([a-z0-9-]+)', d.get('source') or '')] if m})
    offers, ok, missing = [], 0, 0
    for handle in handles:
        try:
            _, page = http.get(f'{BASE}/products/{handle}')
        except FetchError as e:
            if 'omdirigering' in str(e):
                raise FetchError('Bambu Lab skickade vidare till en annan butik (butiken svarar bara europeiska '
                                 'besökare)') from None
            missing += 1
            continue
        ok += 1
        offers += page_to_offers(page, matcher.data, rates, now)
    if ok == 0:
        raise FetchError('inga produktsidor kunde hämtas från Bambu Lab-butiken')
    log(f'  Bambu Lab: {ok} produktsidor ({missing} saknas i EU-butiken), {len(offers)} erbjudanden i lager '
        f'(1 € = {rate:.2f} kr)')
    return offers

"""Bambu Lab EU-butik (Shopify): pris, ordinarie pris och lagerstatus per färg och förpackning.

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
MAX_PAGES = 12


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
    by_code = {d['code']: d for d in catalog if d.get('code')}
    offers = []
    for p in products:
        handle = p.get('handle', '')
        for v in p.get('variants') or []:
            if not v.get('available'):
                continue
            opts = [v.get(f'option{i}') or '' for i in (1, 2, 3)]
            code = next((m.group(1) for o in opts for m in [re.search(r'\((\d{5})\)', o)] if m), None)
            d = by_code.get(code)
            if not d:
                continue
            pack = next((_pack(o) for o in opts if _pack(o)), None)
            weight = next((_weight(o) for o in opts if _weight(o)), None)
            if not pack or not weight:
                continue
            try:
                eur = float(v['price'])
            except (KeyError, TypeError, ValueError):
                continue
            if eur <= 0:
                continue
            price = round(eur * rate)
            list_price = None
            try:
                cmp = float(v.get('compare_at_price') or 0)
                if cmp > eur:
                    list_price = round(cmp * rate)
            except (TypeError, ValueError):
                pass
            color = re.sub(r'\s*\(\d{5}\)', '', next(o for o in opts if code in o)).strip()
            size = f'{weight / 1000:g} kg'.replace('.', ',') if weight >= 1000 else f'{weight} g'
            offers.append(dict(
                key=d['type'] + '|' + (d['code'] or d['name']), shop=SHOP,
                title=f"Bambu Lab {p.get('title', '')} {color} · {size} · {pack}",
                url=f'{BASE}/products/{handle}?variant={v.get("id")}' if v.get('id') else f'{BASE}/products/{handle}',
                price=price, listPrice=list_price, shipping=None, fee=0, weight=weight, pack=pack, kind='fixed',
                condition='Ny', endAt=None,
                shippingNote='Frakt visas i kassan. Betalas i euro.',
                sourceNote=(f'Pris {eur:.2f} € ≈ {price} kr (ECB-kurs {rate:.2f}). '.replace('.', ',')
                            + 'I lager enligt butiken.'),
                checkedAt=now))
    return offers


def run(http, matcher, state, log, now):
    rate = eur_to_sek(http, state, log)
    products, page = [], 1
    while page <= MAX_PAGES:
        try:
            _, body = http.get(f'{BASE}/products.json?limit=250&page={page}')
        except FetchError as e:
            if 'omdirigering' in str(e):
                raise FetchError('Bambu Lab skickade vidare till en annan butik (butiken svarar bara europeiska '
                                 'besökare)') from None
            raise
        try:
            batch = json.loads(body).get('products') or []
        except ValueError:
            raise FetchError('oväntat svar från Bambu Lab-butiken') from None
        if not batch:
            break
        products += batch
        page += 1
    offers = products_to_offers(products, matcher.data, rate, now)
    log(f'  Bambu Lab: {len(products)} produkter, {len(offers)} erbjudanden i lager (1 € = {rate:.2f} kr)')
    return offers

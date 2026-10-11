"""Tradera: sökresultat + annonssidor (inbäddad Next.js-data), samma regler som originalets build_prices.py."""
import html
import json
import re
import urllib.parse

from .common import FetchError, json_ld

ITEM_URL = re.compile(r'^https://www\.tradera\.com/item/\d+/\d+/[A-Za-z0-9%_-]*$')
_PUSH = re.compile(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)')


def _flight(page):
    out = []
    for chunk in _PUSH.findall(page):
        try:
            out.append(json.loads(chunk))
        except ValueError:
            pass
    return ''.join(out)


def _decode_at(s, start):
    try:
        return json.JSONDecoder().raw_decode(s, start)[0]
    except ValueError:
        return None


def search_items(page):
    f = _flight(page)
    i = f.find('"discover/receiveSearchResults"')
    j = f.find('"items":[', i if i >= 0 else 0)
    if j < 0:
        return [], 0
    items = _decode_at(f, j + len('"items":')) or []
    pc = re.search(r'"pagination":\{"pageIndex":\d+,"pageCount":(\d+)', f)
    return [x for x in items if isinstance(x, dict) and 'itemId' in x], int(pc.group(1)) if pc else 1


def item_details(page, item_id):
    f = _flight(page)
    j = f.find('{"itemId":%d,"categoryId"' % int(item_id))
    return _decode_at(f, j) if j >= 0 else None


def _weight(title, desc):
    w = re.search(r'\b(\d+(?:[.,]\d+)?)\s*kg\b', title, re.I)
    if not w:
        w = re.search(r'(?:netto\s?vikt(?:en)?(?:\s*är)?|vikt)\s*:?\s*(\d+(?:[.,]\d+)?)\s*kg', desc, re.I)
    if not w:
        w = re.search(r'\b(1)\s*kg\b', desc, re.I)
    weight = round(float(w.group(1).replace(',', '.')) * 1000) if w else None
    multi = re.search(r'(\d+)\s*[x×]\s*(\d+(?:[.,]\d+)?)\s*kg', title, re.I)
    if multi:
        weight = round(int(multi.group(1)) * float(multi.group(2).replace(',', '.')) * 1000)
    if re.search(r'\b(?:[2-9]\s*(?:st|rullar|rolls)|paket)\b', title, re.I) and not multi \
            and not re.search(r'\b[2-9]\s*kg', title, re.I):
        weight = None
    return weight


def _pack(title, desc):
    if re.search(r'refill', title, re.I):
        return 'Refill'
    if re.search(r'spole', title, re.I):
        return 'Spole'
    if re.search(r'\brefill\b', desc, re.I):
        return 'Refill'
    if re.search(r'(?:med|återanvändbar)\s+spole', desc, re.I):
        return 'Spole'
    return 'Ej angiven'


def run(http, matcher, state, log, now):
    from .. import config
    candidates, pages = {}, 0
    for q in config.TRADERA_QUERIES:
        page_count = 1
        p = 1
        while p <= min(page_count, config.TRADERA_MAX_PAGES):
            url = 'https://www.tradera.com/search?' + urllib.parse.urlencode({'q': q, **({'paging': p} if p > 1 else {})})
            try:
                _, page = http.get(url)
            except FetchError as e:
                log(f'  Tradera: sökning misslyckades ({q}, sida {p}): {e}')
                break
            items, page_count = search_items(page)
            pages += 1
            if not items:
                break
            for it in items:
                if not it.get('isActive') or it.get('sellerCountryCodeIso2') != 'SE':
                    continue
                if not ITEM_URL.match(str(it.get('itemUrl', ''))):
                    continue
                d = matcher.match(it.get('shortDescription', ''))
                if d:
                    candidates[it['itemId']] = (it, matcher.key(d))
            p += 1
    if pages == 0:
        raise FetchError('Traderas sökning kunde inte hämtas')
    log(f'  Tradera: {pages} söksidor, {len(candidates)} matchande annonser')

    offers, errors = [], 0
    for item_id, (listing, key) in candidates.items():
        try:
            _, page = http.get(listing['itemUrl'])
        except FetchError as e:
            errors += 1
            continue
        a = item_details(page, item_id)
        if not a or not a.get('isActive') or a.get('hasEnded'):
            continue
        desc = a.get('description')
        if not isinstance(desc, str) or desc.startswith('$'):
            # Beskrivningen ligger som referens i Next.js-datan; JSON-LD har hela texten.
            ld = json_ld(page)
            a['description'] = ld[0].get('description', '') if ld else ''
        offer = _offer(a, listing, key, matcher, now)
        if offer:
            offers.append(offer)
    log(f'  Tradera: {len(offers)} erbjudanden, {errors} annonser kunde inte hämtas')
    return offers


def _offer(a, i, key, matcher, now):
    title = a.get('title') or i.get('shortDescription', '')
    desc_full = html.unescape(re.sub(r'<[^>]+>', ' ', a.get('description') or ''))
    desc = re.split(r'Sökord|sökord', desc_full)[0]
    d = matcher.match(title)
    if not d or matcher.key(d) != key:
        return None
    # Om beskrivningen nämner en annan färgkod i katalogen tilldelas inte annonsen.
    codes = set(re.findall(r'\b\d{5}\b', desc)) & matcher.valid_codes
    if (codes and d['code'] not in codes) or len(codes) > 1:
        return None
    weight = _weight(title, desc)
    pack = _pack(title, desc)
    shipping = [s.get('cost') for s in a.get('shippingOptions', []) if s.get('toCountryCodeIso2', 'SE') == 'SE'
                and not s.get('isTakeaway') and not s.get('isNotSpecified') and s.get('cost') is not None]
    if not shipping:
        shipping = [s.get('cost') for s in i.get('shippingOptions', []) if s.get('cost') is not None]
    ship = min(shipping) if shipping else None
    pay = a.get('paymentCalculations') or {}
    fee = pay.get('buyerProtectionCostForFixedPrice', 0) if pay.get('isBuyerProtectionMandatory') else 0
    attrs = json.dumps(a.get('attributes', []), ensure_ascii=False) + json.dumps(i.get('attributes', []), ensure_ascii=False)
    condition = 'Oanvänt' if 'Oanvänt' in attrs else 'Kontrollera skick'
    kind = 'fixed' if (a.get('fixedPrice') or 0) > 0 and a.get('canBeBought') else 'auction'
    price = a.get('fixedPrice') if kind == 'fixed' else i.get('price')
    if not price:
        return None
    seller = (a.get('seller') or {}).get('alias') or i.get('sellerAlias', '')
    list_price = None
    before = a.get('priceBeforeDiscount') or i.get('priceBeforeDiscount') or 0
    if kind == 'fixed' and isinstance(before, (int, float)) and price < before < price * 3:
        list_price = round(before)
    return dict(key=key, shop='Tradera · ' + seller, title=title, url=i['itemUrl'], price=price, listPrice=list_price,
                shipping=ship, fee=fee,
                weight=weight, pack=pack, kind=kind, condition=condition, endAt=a.get('endDate'),
                shippingNote='Billigaste angivna frakt inom Sverige. ' + (
                    'Obligatoriskt köparskydd inkluderat.' if fee else 'Eventuellt valfritt köparskydd tillkommer.'),
                sourceNote='Annons kontrollerad. ' + (
                    'Bud/utropspris är inte slutpris.' if kind == 'auction' else 'Köp nu-pris.'),
                checkedAt=now)

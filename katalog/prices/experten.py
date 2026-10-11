"""3DExperten (Shopify, svensk sida av danska 3DEksperten): Bambu Lab-filament, en produkt per färg.

Produktlistor hämtas som JSON (pris i kronor inkl. moms, jämförpris och lagerstatus). Titlarna är
blandat svenska/engelska ("Bambu Lab - PLA Matt - Karamell - 1,75 mm - 1 kg - Refill"), så typ och färg
översätts innan de matchas mot katalogen.
"""
import json
import re

from .common import FetchError

SHOP = '3DExperten'
BASE = 'https://3dexperten.se'
MAX_PAGES = 8

# Svenska ord i titlarna -> katalogens engelska namn (gemener; hela ord).
WORDS = {
    'svart': 'black', 'vit': 'white', 'grå': 'gray', 'röd': 'red', 'gul': 'yellow', 'grön': 'green', 'blå': 'blue',
    'brun': 'brown', 'rosa': 'pink', 'lila': 'purple', 'guld': 'gold', 'silver': 'silver', 'orange': 'orange',
    'beige': 'beige', 'mörk': 'dark', 'mörkgrå': 'dark gray',
    'ljus': 'light', 'ljusgrå': 'light gray', 'ljusblå': 'light blue', 'mörkblå': 'dark blue', 'mörkgrön': 'dark green',
    'turkos': 'turquoise', 'karamell': 'caramel', 'elfenben': 'ivory', 'lerbrun': 'clay brown',
    'kol': 'charcoal', 'träkol': 'charcoal', 'körsbär': 'cherry', 'aska': 'ash', 'askgrå': 'ash gray',
    'kakao': 'cocoa', 'mandel': 'almond', 'sand': 'sand', 'oliv': 'olive', 'citron': 'lemon', 'mint': 'mint',
    'himmelsblå': 'sky blue', 'is': 'ice', 'jade': 'jade', 'magenta': 'magenta', 'cyan': 'cyan', 'bronz': 'bronze',
    'brons': 'bronze', 'koppar': 'copper', 'mörkbrun': 'dark brown', 'ljusbrun': 'light brown',
    'elfenbensvit': 'ivory white', 'benvit': 'bone white', 'mörkröd': 'dark red', 'scharlakansröd': 'scarlet red',
    'citrongul': 'lemon yellow', 'gräsgrön': 'grass green', 'äppelgrön': 'apple green', 'sakurarosa': 'sakura pink',
    'plommon': 'plum', 'terrakotta': 'terracotta', 'mandarinorange': 'mandarin orange', 'isblå': 'ice blue',
    'mörkchoklad': 'dark chocolate', 'lattebrun': 'latte brown', 'syrenlila': 'lilac purple', 'kolgrå': 'charcoal',
    'marinblå': 'marine blue', 'körsbärsrosa': 'cherry pink', 'lavendel': 'lavender', 'krämvit': 'cream',
    'skoggrön': 'forest green', 'limegrön': 'lime green', 'jordnötsbrun': 'peanut brown', 'champagne': 'champagne',
    'roséguld': 'rose gold', 'titangrå': 'titan gray',
}
TYPES = {
    'pla matt': 'pla matte', 'pla-trä': 'pla wood', 'pla trä': 'pla wood', 'pla silke+': 'pla silk+',
    'pla silke': 'pla silk+', 'pla metall': 'pla metal', 'pla glitter': 'pla sparkle', 'pla marmor': 'pla marble',
    'pla självlysande': 'pla glow', 'pla-cf': 'pla-cf', 'petg-hf': 'petg hf',
}


def translate(title):
    """'Bambu Lab - PLA Matt - Karamell - 1,75 mm - 1 kg - Refill' -> 'PLA Matte Caramel' (+ pack, vikt)."""
    t = re.sub(r'^\s*\([^)]*\)\s*-\s*', '', title)            # "(UTGÅNG) - " m.m.
    parts = [p.strip() for p in re.split(r'\s+-\s+', t) if p.strip()]
    if parts and parts[0].lower().startswith('bambu'):
        parts = parts[1:]
    if len(parts) < 2:
        return None
    typ = parts[0].lower()
    typ = TYPES.get(typ, typ)
    color = ' '.join(WORDS.get(w, w) for w in re.findall(r"[\wåäöÅÄÖ+]+", parts[1].lower()))
    return f'{typ} {color}'


def _weight(title):
    m = re.search(r'(\d+(?:[.,]\d+)?)\s*(kg|g)\b(?!\S)', title, re.I)
    if not m:
        return None
    v = float(m.group(1).replace(',', '.'))
    return round(v * 1000) if m.group(2).lower() == 'kg' else round(v)


def products_to_offers(products, matcher, now):
    offers = []
    for p in products:
        if not isinstance(p, dict) or 'bambu' not in str(p.get('vendor', '')).lower():
            continue
        title = str(p.get('title', ''))
        if not re.search(r'1[.,]75\s*mm', title):
            continue
        name = translate(title)
        d = matcher.match(name) if name else None
        if not d:
            continue
        weight = _weight(title.replace('1,75 mm', '').replace('1.75mm', '').replace('1.75 mm', ''))
        pack = 'Refill' if re.search(r'\brefill\b', title, re.I) else 'Spole'
        handle = str(p.get('handle', ''))
        if not weight or not re.fullmatch(r'[a-z0-9-]+', handle):
            continue
        for v in p.get('variants') or []:
            if not isinstance(v, dict) or not v.get('available'):
                continue
            try:
                price = round(float(v['price']))
                cmp = float(v.get('compare_at_price') or 0)
            except (KeyError, TypeError, ValueError):
                continue
            if price <= 0:
                continue
            url = f'{BASE}/products/{handle}' + (f'?variant={int(v["id"])}' if len(p['variants']) > 1 and v.get('id') else '')
            offers.append(dict(
                key=matcher.key(d), shop=SHOP, title=re.sub(r'^\s*\([^)]*\)\s*-\s*', '', title), url=url, price=price,
                listPrice=round(cmp) if cmp > price else None, shipping=None, fee=0, weight=weight, pack=pack,
                kind='fixed', condition='Ny', endAt=None,
                shippingNote='Frakt visas i kassan. Fri frakt till paketombud från 739 kr.',
                sourceNote='I lager enligt butiken.' + (' Utgående vara.' if 'UTGÅNG' in title.upper() else ''),
                checkedAt=now))
    return offers


def _collect(http, path, seen, out):
    for page in range(1, MAX_PAGES + 1):
        try:
            _, body = http.get(f'{BASE}{path}products.json?limit=250&page={page}')
        except FetchError as e:
            if 'HTTP 404' in str(e):
                return
            raise
        try:
            batch = json.loads(body).get('products') or []
        except (ValueError, AttributeError):
            raise FetchError('ogiltigt svar från 3DExperten') from None
        for p in batch:
            if isinstance(p, dict) and p.get('id') not in seen:
                seen.add(p.get('id'))
                out.append(p)
        if len(batch) < 250:
            return


def run(http, matcher, state, log, now):
    seen, products = set(), []
    # Butikens samlingar med Bambu Lab-filament (hittas automatiskt), annars hela sortimentet.
    try:
        _, body = http.get(f'{BASE}/collections.json?limit=250')
        colls = [c.get('handle', '') for c in json.loads(body).get('collections') or [] if isinstance(c, dict)]
    except (FetchError, ValueError, AttributeError):
        colls = []
    wanted = [c for c in colls if re.fullmatch(r'[a-z0-9-]+', c) and 'bambu' in c
              and re.search(r'pla|petg|abs|asa|tpu|filament|^bambu-lab$', c)]
    for c in wanted[:12]:
        _collect(http, f'/collections/{c}/', seen, products)
    _collect(http, '/', seen, products)
    if not products:
        raise FetchError('inga produkter kunde hämtas från 3DExperten')
    offers = products_to_offers(products, matcher, now)
    bambu = sum(1 for p in products if 'bambu' in str(p.get('vendor', '')).lower() and re.search(r'1[.,]75', str(p.get('title'))))
    log(f'  3DExperten: {bambu} Bambu Lab-filament, {len(offers)} erbjudanden i lager som matchar katalogen')
    return offers

"""3D Prima Sverige: kända produktlänkar, pris och lagerstatus från JSON-LD."""
import re

from .common import FetchError, json_ld, text

SHOP = '3D Prima Sverige'


def run(http, matcher, state, log, now):
    targets = state.get('prima_targets', [])   # [{url, key, title, pack, weight}]
    offers, ok, errors = [], 0, 0
    for t in targets:
        try:
            _, page = http.get(t['url'])
        except FetchError as e:
            errors += 1
            log(f"  3D Prima: kunde inte hämta {t['url']}: {e}")
            continue
        ok += 1
        products = json_ld(page)
        if not products:
            continue
        p = products[0]
        offer = p.get('offers') or {}
        if isinstance(offer, list):
            offer = offer[0] if offer else {}
        try:
            price = round(float(str(offer.get('price')).replace(',', '.')))
        except (TypeError, ValueError):
            continue
        list_price = None
        specs = offer.get('priceSpecification') or []
        for spec in [specs] if isinstance(specs, dict) else specs:
            try:
                if isinstance(spec, dict) and str(spec.get('priceType') or '').endswith('ListPrice'):
                    lp = round(float(str(spec.get('price')).replace(',', '.')))
                    list_price = lp if price < lp < price * 3 else None
            except (TypeError, ValueError):
                pass
        stock = re.search(r'Lagersaldo:\s*<strong>\s*([^<]+?)\s*</strong>', page)
        in_stock = str(offer.get('availability', '')).endswith('InStock') and not (stock and stock.group(1) in ('0', '-'))
        if not in_stock or price <= 0:
            continue
        stock_txt = f'Lagersaldo {text(stock.group(1))}.' if stock else 'I lager.'
        offers.append(dict(key=t['key'], shop=SHOP, title=t['title'], url=t['url'], price=price, listPrice=list_price, shipping=None, fee=0,
                           weight=t.get('weight', 1000), pack=t.get('pack', 'Spole'), kind='fixed', condition='Ny',
                           endAt=None, shippingNote='Frakt visas i kassan. Fri frakt från 1 000 kr.',
                           sourceNote=stock_txt, checkedAt=now))
    if targets and ok == 0:
        raise FetchError('inga sidor kunde hämtas från 3D Prima')
    log(f'  3D Prima: {ok} sidor, {len(offers)} erbjudanden i lager, {errors} fel')
    return offers

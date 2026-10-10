"""3DJake Sverige: produktsidor med JSON-LD per variant (Spole/Refill).

Startpunkter är kända produktlänkar. Produktsidorna länkar till andra färger i samma serie,
så nya färger hittas automatiskt. Kända länkar sparas mellan körningarna.
"""
import re
import urllib.parse

from .common import FetchError, json_ld, text

SHOP = '3DJake Sverige'
BASE = 'https://www.3djake.se'
FILAMENT_SLUG = re.compile(r'^/bambu-lab/(pla|petg|abs|asa|tpu|pc|pa|ppa|pps|pet|support|pva|bambu-lab-filament)[a-z0-9-]*$')


def _base(url):
    p = urllib.parse.urlsplit(url)
    return BASE + p.path


def _variants(page):
    """[(sai, namn)] från variantväljaren."""
    out = []
    for sai, name in re.findall(
            r'id="p-variants__button(\d+)".*?data-name="([^"]*)"', page, re.S):
        out.append((sai, text(name)))
    return out


def _links(page):
    links = set()
    for href in re.findall(r'href="(/bambu-lab/[^"#?]+)', page):
        if FILAMENT_SLUG.match(href):
            links.add(BASE + href)
    return links


def run(http, matcher, state, log, now):
    known = set(state.get('jake_urls', []))
    queue = sorted({_base(u) for u in known} | {BASE + '/bambu-lab/bambu-lab-filament'})
    seen, offers, pages_ok, errors = set(), [], 0, 0
    keep_urls = set()
    from .. import config
    while queue and len(seen) < config.JAKE_MAX_PAGES:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        try:
            _, page = http.get(url)
        except FetchError as e:
            errors += 1
            if url in {_base(u) for u in known}:
                log(f'  3DJake: kunde inte hämta {url}: {e}')
            continue
        pages_ok += 1
        for link in _links(page):
            if link not in seen and link not in queue:
                queue.append(link)
        if not json_ld(page):
            continue  # kategorisida
        variants = _variants(page) or [(None, '')]
        for sai, vname in variants:
            vurl = url + (f'?sai={sai}' if sai else '')
            try:
                vpage = page if (sai is None or f'checked id="p-variants__button{sai}"' in page) else http.get(vurl)[1]
            except FetchError as e:
                errors += 1
                log(f'  3DJake: kunde inte hämta {vurl}: {e}')
                continue
            offer = _parse(vpage, vurl, matcher, now)
            if offer == 'nomatch':
                continue
            keep_urls.add(url)
            if offer:
                offers.append(offer)
    state['jake_urls'] = sorted(keep_urls | {_base(u) for u in known})
    if pages_ok == 0:
        raise FetchError('inga sidor kunde hämtas från 3DJake')
    log(f'  3DJake: {pages_ok} sidor, {len(offers)} erbjudanden i lager, {errors} fel')
    return offers


def _parse(page, url, matcher, now):
    products = json_ld(page)
    if not products:
        return None
    p = products[0]
    h1 = re.search(r'<h1[^>]*>(.*?)</h1>', page, re.S)
    title = text(h1.group(1)) if h1 else text(p.get('name', ''))
    if 'bambu' not in title.lower():
        title = 'Bambu Lab ' + title
    d = matcher.match(title)
    if not d:
        return 'nomatch'
    offer = p.get('offers') or {}
    if isinstance(offer, list):
        offer = offer[0] if offer else {}
    try:
        price = round(float(str(offer.get('price')).replace(',', '.')))
    except (TypeError, ValueError):
        return None
    in_stock = str(offer.get('availability', '')).endswith('InStock') or \
        'p-delivery__stock--available' in page
    if not in_stock or price <= 0:
        return None
    w = re.search(r'\((\d[\d.]*)\s*g\)', title) or re.search(r'(\d[\d.]*)\s*g\b', title)
    if not w:
        return None
    weight = int(w.group(1).replace('.', ''))
    pack = 'Refill' if 'refill' in title.lower() else 'Spole'
    ship = 115
    try:
        ship = int(offer['shippingDetails']['shippingRate']['value'])
    except (KeyError, TypeError, ValueError):
        pass
    shipping = 0 if price >= 1099 else ship
    return dict(key=matcher.key(d), shop=SHOP, title=title, url=offer.get('url') or url, price=price,
                shipping=shipping, fee=0, weight=weight, pack=pack, kind='fixed', condition='Ny', endAt=None,
                shippingNote='Standardfrakt i Sverige. Fri frakt från 1 099 kr.',
                sourceNote='Produktsida och svenska fraktvillkor. I lager vid kontroll.', checkedAt=now)

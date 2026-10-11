"""Inköpslista: väljer var varje filament bör köpas så att totalen (inkl. frakt) blir så låg som möjligt.

Fraktmodell (uppskattning):
- 3DJake: 115 kr per order, fri frakt från 1 099 kr.
- 3D Prima: frakt visas först i kassan; uppskattas till 79 kr, fri frakt från 1 000 kr.
- Tradera: en frakt per säljare (samfrakt antas, högsta angivna frakten bland varorna) + köparskydd per köp.
- Bambu Lab EU: frakt uppskattas (BAMBU_SHIPPING_SEK). Mängdrabatten (t.ex. 4+ och 6+ rullar av samma
  filamenttyp, färger får blandas) räknas in: rullarna får nivåns pris per rulle om det är lägre än dagspriset.
Tradera-annonser räknas som en vara per annons; butiker har obegränsat antal.
"""
import datetime
import json
from collections import defaultdict

from . import config

SHOPS = {
    '3DJake Sverige': {'shipping': 115, 'free_from': 1099, 'home': 'https://www.3djake.se/bambu-lab'},
    '3D Prima Sverige': {'shipping': None, 'estimate': 79, 'free_from': 1000, 'home': 'https://www.3dprima.com/se/'},
    'Bambu Lab EU': {'shipping': None, 'estimate': config.BAMBU_SHIPPING_SEK,
                     'free_from': config.BAMBU_FREE_FROM_SEK or 10 ** 9, 'home': 'https://eu.store.bambulab.com/'},
}
UNKNOWN_SHIPPING_ESTIMATE = 79


def _live(o, now):
    end = o.get('endAt')
    if not end:
        return True
    try:
        return datetime.datetime.fromisoformat(end.replace('Z', '+00:00')) > now
    except ValueError:
        return False


def candidates(key, pack, offers, now):
    """Köpbara erbjudanden för en färg: fast pris, nytt/oanvänt, rätt förpackning; helst 1 kg."""
    ok = [o for o in offers if o['key'] == key and o.get('kind') == 'fixed' and _live(o, now)
          and o.get('condition') in ('Ny', 'Oanvänt') and isinstance(o.get('price'), (int, float))
          and (pack == 'any' or o.get('pack') == pack)]
    one_kg = [o for o in ok if o.get('weight') == 1000]
    return one_kg or ok


def _group(o):
    return o['shop']


BULK_SHOP = 'Bambu Lab EU'


def bulk_discount(group, lines):
    """Bambu Lab EU:s mängdrabatt. Returnerar (rabatt_kr, detaljer, {id(erbjudande): pris_per_rulle}).

    Rullar (1 kg) av samma filamenttyp räknas ihop oavsett färg och förpackning; nivån med flest rullar som
    uppnås ger sitt pris per rulle (euro omräknat med samma kurs som erbjudandet). Dagspriset används om det
    redan är lägre.
    """
    tiers = config.BAMBU_BULK_TIERS
    if group != BULK_SHOP or not tiers:
        return 0, [], {}
    by_type = defaultdict(list)
    for o in lines:
        typ = o['key'].split('|')[0]
        if typ in config.BAMBU_BULK_TYPES and o.get('weight') == 1000 and o.get('fx'):
            by_type[typ].append(o)
    total, details, unit = 0, [], {}
    for typ, offers in sorted(by_type.items()):
        n = len(offers)
        reached = [t for t in tiers if n >= t[0]]
        upcoming = next((t for t in tiers if n < t[0]), None)
        info = {'type': typ, 'rolls': n, 'tier': None, 'saved': 0, 'prices': {}, 'next': None}
        fx = offers[0]['fx']
        if upcoming:
            info['next'] = {'min': upcoming[0], 'missing': upcoming[0] - n,
                            'prices': {k: round(v * fx) for k, v in upcoming[1].items() if v}}
        if reached:
            min_n, eur = reached[-1]
            info['tier'] = min_n
            info['prices'] = {k: (v, round(v * fx)) for k, v in eur.items() if v}
            for o in offers:
                tier_eur = eur.get(o.get('pack'))
                if not tier_eur:
                    continue
                price = min(o['price'], round(tier_eur * o['fx']))
                unit[id(o)] = price
                info['saved'] += o['price'] - price
        total += info['saved']
        details.append(info)
    return total, details, unit


def shop_cost(group, lines):
    """(varor, frakt, köparskydd, fraktnotering, frakt_är_uppskattad, mängdrabatt) för en butik/säljare.

    varor är före mängdrabatt; att betala = varor - mängdrabatt + frakt + köparskydd.
    """
    disc = bulk_discount(group, lines)[0]
    gross = sum(o['price'] for o in lines)
    r = _shop_cost(group, lines, gross - disc)
    return (gross,) + r[1:] + (disc,)


def _shop_cost(group, lines, goods):
    fee = sum(o.get('fee') or 0 for o in lines)
    rule = SHOPS.get(group)
    if rule:
        if goods >= rule['free_from']:
            return goods, 0, fee, f'Fri frakt från {rule["free_from"]:,} kr'.replace(',', ' '), False
        if rule['shipping'] is None:
            left = rule['free_from'] - goods
            free = (f' Fri frakt från {rule["free_from"]:,} kr – {left:,} kr kvar'.replace(',', ' ')
                    if rule['free_from'] < 10 ** 8 else '')
            return goods, rule['estimate'], fee, f'Frakt visas i kassan (uppskattad).{free}', True
        left = rule['free_from'] - goods
        return goods, rule['shipping'], fee, (f'Fri frakt från {rule["free_from"]:,} kr – {left:,} kr kvar'
                                             ).replace(',', ' '), False
    ships = [o.get('shipping') for o in lines]
    known = [s for s in ships if isinstance(s, (int, float))]
    if group.startswith('Tradera'):
        note = 'Samfrakt antas när flera annonser köps av samma säljare – kontrollera med säljaren.' if len(lines) > 1 \
            else 'Billigaste angivna frakten.'
        if known:
            return goods, max(known), fee, note, False
        return goods, UNKNOWN_SHIPPING_ESTIMATE, fee, 'Frakt ej angiven (uppskattad).', True
    if known:
        return goods, max(known), fee, '', False
    return goods, UNKNOWN_SHIPPING_ESTIMATE, fee, 'Frakt ej angiven (uppskattad).', True


def _total(assign):
    groups = defaultdict(list)
    for o in assign:
        if o is not None:
            groups[_group(o)].append(o)
    t = 0
    for g, lines in groups.items():
        goods, ship, fee, _, _, disc = shop_cost(g, lines)
        t += goods - disc + ship + fee
    return t


def plan(items, offers, catalog, now=None):
    """items: [(key, qty, pack)] -> dict med butiker, rader, saknade och summor."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    by_key = {d['type'] + '|' + (d['code'] or d['name']): d for d in catalog}
    units, cands, missing = [], [], []
    for key, qty, pack in items:
        c = candidates(key, pack, offers, now)
        if not c:
            missing.append({'key': key, 'qty': qty, 'pack': pack, 'item': by_key.get(key)})
            continue
        for _ in range(qty):
            units.append((key, pack))
            cands.append(c)

    def capacity_ok(assign, i, o):
        if not o['shop'].startswith('Tradera'):
            return True
        return all(a is not o for j, a in enumerate(assign) if j != i)

    # Start: billigaste vara + egen frakt per enhet, med hänsyn till att en annons bara räcker till en enhet.
    assign = [None] * len(units)
    for i, c in enumerate(cands):
        for o in sorted(c, key=lambda o: o['price'] + (o.get('fee') or 0) + (o.get('shipping') or 0)):
            if capacity_ok(assign, i, o):
                assign[i] = o
                break
    def single_moves(assign, best):
        """Flytta en enhet i taget så länge totalen (inkl. frakt) sjunker."""
        for _ in range(25):
            improved = False
            for i, c in enumerate(cands):
                current = assign[i]
                for o in c:
                    if o is current or not capacity_ok(assign, i, o):
                        continue
                    assign[i] = o
                    t = _total(assign)
                    if t < best - 0.5:
                        best, current, improved = t, o, True
                    else:
                        assign[i] = current
            if not improved:
                break
        return best

    best = single_moves(assign, _total(assign))
    # Samlingsdrag: prova att lägga allt en butik har hos den butiken (fri frakt/samfrakt kräver flera varor
    # på en gång, vilket enkelflyttar aldrig hittar). Förbättra sedan vidare med enkelflyttar.
    # För Bambu Lab provas också att samla en filamenttyp i taget (mängdrabatten gäller per typ).
    shops_all = sorted({_group(o) for c in cands for o in c})
    trials = [(g, None) for g in shops_all]
    if BULK_SHOP in shops_all:
        trials += [(BULK_SHOP, t) for t in sorted({k.split('|')[0] for k, _ in units})]
    for _ in range(10):
        improved = False
        for g, typ in trials:
            trial = list(assign)
            for i, c in enumerate(cands):
                if typ and units[i][0].split('|')[0] != typ:
                    continue
                for o in sorted((o for o in c if _group(o) == g), key=lambda o: o['price']):
                    prev = trial[i]
                    trial[i] = o
                    if capacity_ok(trial, i, o):
                        break
                    trial[i] = prev
            t = single_moves(trial, _total(trial))
            if t < best - 0.5:
                assign[:] = trial
                best, improved = t, True
        if not improved:
            break

    # Enheter som inte fick något (alla annonser slut) räknas som saknade.
    groups = defaultdict(list)
    lines_by_offer = {}
    for i, o in enumerate(assign):
        key, pack = units[i]
        if o is None:
            missing.append({'key': key, 'qty': 1, 'pack': pack, 'item': by_key.get(key)})
            continue
        ident = id(o)
        if ident not in lines_by_offer:
            line = {'key': key, 'item': by_key.get(key), 'offer': o, 'qty': 0}
            lines_by_offer[ident] = line
            groups[_group(o)].append(line)
        lines_by_offer[ident]['qty'] += 1
    shops = []
    totals = {'goods': 0, 'discount': 0, 'shipping': 0, 'fee': 0, 'total': 0, 'estimated': False, 'units': 0}
    for g, lines in groups.items():
        flat = [l['offer'] for l in lines for _ in range(l['qty'])]
        goods, ship, fee, note, est, disc = shop_cost(g, flat)
        _, bulk, unit = bulk_discount(g, flat)
        for l in lines:
            if id(l['offer']) in unit and unit[id(l['offer'])] < l['offer']['price']:
                l['bulkPrice'] = unit[id(l['offer'])]
        rule = SHOPS.get(g)
        shops.append({'shop': g, 'lines': lines, 'goods': goods, 'shipping': ship, 'fee': fee, 'note': note,
                      'discount': disc, 'bulk': bulk,
                      'estimated': est, 'total': goods - disc + ship + fee,
                      'home': rule['home'] if rule else (lines[0]['offer']['url'] if g.startswith('Tradera') else '')})
        totals['goods'] += goods
        totals['discount'] += disc
        totals['shipping'] += ship
        totals['fee'] += fee
        totals['total'] += goods - disc + ship + fee
        totals['estimated'] |= est
        totals['units'] += len(flat)
    shops.sort(key=lambda s: -s['total'])
    merged = {}
    for m in missing:
        k = (m['key'], m['pack'])
        if k in merged:
            merged[k]['qty'] += m['qty']
        else:
            merged[k] = dict(m)
    missing = list(merged.values())
    return {'shops': shops, 'missing': missing, 'totals': totals}


def load_offers():
    for p in (config.WWW_DIR / 'offers.json', config.SEED_DIR / 'offers.json'):
        try:
            d = json.loads(p.read_text(encoding='utf-8'))
            return d.get('offers', []), d.get('checkedAt')
        except (OSError, ValueError):
            continue
    return [], None

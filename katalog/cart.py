"""Inköpslista: väljer var varje filament bör köpas så att totalen (inkl. frakt) blir så låg som möjligt.

Fraktmodell (uppskattning):
- 3DJake: 115 kr per order, fri frakt från 1 099 kr.
- 3D Prima: frakt visas först i kassan; uppskattas till 79 kr, fri frakt från 1 000 kr.
- Tradera: en frakt per säljare (samfrakt antas, högsta angivna frakten bland varorna) + köparskydd per köp.
- Bambu Lab EU: frakt uppskattas (BAMBU_SHIPPING_SEK). Mängdrabatten (t.ex. 4+ och 6+ rullar av samma
  filamenttyp, färger får blandas) räknas in: rullarna får nivåns pris per rulle om det är lägre än dagspriset.
- 3DVerkstan: frakt uppskattas (VERKSTAN_SHIPPING_SEK); volymrabatt per produkt (5+/10+/20+ st) räknas in.
- 3DExperten: frakt uppskattas (EXPERTEN_SHIPPING_SEK), fri frakt till paketombud från 739 kr.
Tradera-annonser räknas som en vara per annons; butiker har obegränsat antal.
"""
import datetime
import json
import queue
import threading
import time
from collections import defaultdict

from . import config

SHOPS = {
    '3DJake Sverige': {'shipping': 115, 'free_from': 1099, 'home': 'https://www.3djake.se/bambu-lab'},
    '3D Prima Sverige': {'shipping': None, 'estimate': 79, 'free_from': 1000, 'home': 'https://www.3dprima.com/se/'},
    'Bambu Lab EU': {'shipping': None, 'estimate': config.BAMBU_SHIPPING_SEK,
                     'free_from': config.BAMBU_FREE_FROM_SEK or 10 ** 9, 'home': 'https://eu.store.bambulab.com/'},
    '3DVerkstan': {'shipping': None, 'estimate': config.VERKSTAN_SHIPPING_SEK,
                   'free_from': config.VERKSTAN_FREE_FROM_SEK or 10 ** 9,
                   'home': 'https://3dverkstan.se/shop/filament/bambu-lab-filament/'},
    '3DExperten': {'shipping': None, 'estimate': config.EXPERTEN_SHIPPING_SEK,
                   'free_from': config.EXPERTEN_FREE_FROM_SEK or 10 ** 9, 'home': 'https://3dexperten.se/collections/bambu-lab'},
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


def _kr(n):
    return f'{round(n):,} kr'.replace(',', ' ')


def _eur(v):
    return f'{v:.2f} €'.replace('.', ',')


def _rolls(n):
    return f'{n} {"rulle" if n == 1 else "rullar"}'


def _bambu_bulk(lines):
    """Bambu Lab EU: fasta nivåpriser i euro per rulle, rullar av samma filamenttyp räknas ihop."""
    tiers = config.BAMBU_BULK_TIERS
    by_type = defaultdict(list)
    for o in lines:
        typ = o['key'].split('|')[0]
        if tiers and typ in config.BAMBU_BULK_TYPES and o.get('weight') == 1000 and o.get('fx'):
            by_type[typ].append(o)
    for typ, offers in sorted(by_type.items()):
        n, fx = len(offers), offers[0]['fx']
        reached = [t for t in tiers if n >= t[0]]
        upcoming = next((t for t in tiers if n < t[0]), None)
        info = {'label': typ, 'rolls': n, 'reached': bool(reached), 'desc': '', 'saved': 0, 'next': ''}
        unit = {}
        if reached:
            min_n, eur = reached[-1]
            info['desc'] = f'nivå {min_n}+ (' + ' · '.join(
                f'{k.lower()} {_eur(v)} ≈ {_kr(v * fx)}/rulle' for k, v in eur.items() if v) + ')'
            for o in offers:
                tier_eur = eur.get(o.get('pack'))
                if tier_eur:
                    unit[id(o)] = min(o['price'], round(tier_eur * o['fx']))
        if upcoming:
            info['next'] = (f'Lägg till {_rolls(upcoming[0] - n)} {typ} (valfri färg) för {upcoming[0]}+-priset: '
                            + ' · '.join(f'{k.lower()} {_kr(v * fx)}' for k, v in upcoming[1].items() if v)
                            + ' per rulle.')
        yield info, offers, unit


def _percent_bulk(lines):
    """Butikens egen volymrabatt i procent (t.ex. 3DVerkstan), räknad per produkt (färger blandas)."""
    groups = defaultdict(list)
    for o in lines:
        if o.get('bulk') and o.get('bulkGroup'):
            groups[o['bulkGroup']].append(o)
    for _, offers in sorted(groups.items()):
        tiers = sorted((int(n), float(pct)) for n, pct in offers[0]['bulk'])
        n = len(offers)
        reached = [t for t in tiers if n >= t[0]]
        upcoming = next((t for t in tiers if n < t[0]), None)
        label = offers[0].get('title', '').split(' · ')[0] or offers[0]['key'].split('|')[0]
        info = {'label': label, 'rolls': n, 'reached': bool(reached), 'desc': '', 'saved': 0, 'next': ''}
        unit = {}
        if reached:
            min_n, pct = reached[-1]
            info['desc'] = f'nivå {min_n}+ st: −{pct:g} %'
            for o in offers:
                unit[id(o)] = round(o['price'] * (1 - pct / 100))
        if upcoming:
            info['next'] = (f'Lägg till {upcoming[0] - n} st till av samma produkt (valfri färg) '
                            f'för −{upcoming[1]:g} %.')
        yield info, offers, unit


def bulk_discount(group, lines):
    """Mängd-/volymrabatt för en butik. Returnerar (rabatt_kr, detaljer, {id(erbjudande): pris_per_st}).

    Bambu Lab EU: nivåpris i euro per rulle för 4+/6+ rullar av samma filamenttyp (BAMBU_BULK_*).
    Butiker som anger egen volymrabatt per produkt (3DVerkstan): procent på produktens alla färger ihop.
    Dagspriset används om det redan är lägre.
    """
    gen = _bambu_bulk(lines) if group == BULK_SHOP else _percent_bulk(lines)
    total, details, units = 0, [], {}
    for info, offers, unit in gen:
        for o in offers:
            if id(o) in unit and unit[id(o)] < o['price']:
                info['saved'] += o['price'] - unit[id(o)]
                units[id(o)] = unit[id(o)]
        total += info['saved']
        details.append(info)
    return total, details, units


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


def plan(items, offers, catalog, now=None, budget=2.0):
    """items: [(key, qty, pack)] -> dict med butiker, rader, saknade och summor."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    # Förbättringsstegen växer snabbt med antalet enheter och håller GIL: efter tidsbudgeten används
    # bästa lösningen hittills så att en stor lista inte låser hela servern.
    deadline = time.monotonic() + budget
    timed_out = []
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
                if time.monotonic() > deadline:
                    timed_out.append(1)
                    return best
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
    # Butiker med mängdrabatt: prova också att samla en filamenttyp i taget hos dem.
    shops_all = sorted({_group(o) for c in cands for o in c})
    trials = [(g, None) for g in shops_all]
    bulk_shops = sorted({_group(o) for c in cands for o in c if o.get('bulk') or _group(o) == BULK_SHOP})
    trials += [(g, t) for g in bulk_shops for t in sorted({k.split('|')[0] for k, _ in units})]
    for _ in range(10):
        improved = False
        for g, typ in trials:
            if time.monotonic() > deadline:
                timed_out.append(1)
                break
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
    return {'shops': shops, 'missing': missing, 'totals': totals, 'complete': not timed_out}


def load_offers():
    for p in (config.WWW_DIR / 'offers.json', config.SEED_DIR / 'offers.json'):
        try:
            d = json.loads(p.read_text(encoding='utf-8'))
            return d.get('offers', []), d.get('checkedAt')
        except (OSError, ValueError):
            continue
    return [], None


# --- Budget: vilka enheter som får vänta ---
def within_budget(items, offers, catalog, limit, base=None, now=None):
    """Delar listan i det som ryms i budgeten och det som får vänta.

    Väntar gör i första hand det som inte är på rea just nu, dyraste först (då är chansen störst att ett
    erbjudande dyker upp senare). Returnerar (plan för det som köps nu, [(key, qty, pack)] som väntar).
    """
    now = now or datetime.datetime.now(datetime.timezone.utc)
    base = base or plan(items, offers, catalog, now)
    if base['totals']['total'] <= limit:
        return base, []
    unit_cost = {}
    for sh in base['shops']:
        for l in sh['lines']:
            o = l['offer']
            unit_cost[l['key']] = (bool(o.get('discount')), l.get('bulkPrice') or o['price'])
    # Enheter i den ordning de ska vänta.
    order = []
    for key, qty, pack in items:
        if key in unit_cost:
            order += [(key, pack)] * qty
    order.sort(key=lambda u: (unit_cost[u[0]][0], -unit_cost[u[0]][1]))
    waiting = defaultdict(int)
    current = base
    deadline = time.monotonic() + 3
    while order and current['totals']['total'] > limit and time.monotonic() < deadline:
        # Skjut upp så många enheter som behövs för att täcka överskottet, sedan räkna om (frakten ändras).
        over = current['totals']['total'] - limit
        while order and over > 0:
            key, pack = order.pop(0)
            waiting[(key, pack)] += 1
            over -= unit_cost[key][1]
        rest = [(k, q - waiting[(k, p)], p) for k, q, p in items if q - waiting[(k, p)] > 0]
        current = plan(rest, offers, catalog, now, budget=0.3)
    wait = [(k, q, p) for (k, p), q in waiting.items() if q > 0]
    return current, wait


# --- Cache och beräkning i bakgrunden ---
_cache = {}                 # user_id -> (signatur, plan)
_cache_lock = threading.Lock()
_queue = queue.Queue()
_worker = None
BACKGROUND_BUDGET = 20.0


def _signature(items, checked):
    return (tuple(items), checked)


def cached_plan(user_id, items, offers, catalog, checked):
    """Planen för användarens lista: färdig från cachen om den finns, annars en snabb plan nu och en
    grundligare i bakgrunden (sidan visar då att den kan bli bättre)."""
    sig = _signature(items, checked)
    with _cache_lock:
        hit = _cache.get(user_id)
    if hit and hit[0] == sig:
        return hit[1]
    p = plan(items, offers, catalog)
    with _cache_lock:
        _cache[user_id] = (sig, p)
    if not p['complete']:
        refresh(user_id, items)
    return p


def refresh(user_id, items=None):
    """Lägg en grundlig omräkning i kö (items=None: läs listan ur databasen i bakgrundstråden)."""
    global _worker
    with _cache_lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_work, name='cart-plans', daemon=True)
            _worker.start()
    _queue.put((user_id, items))


def refresh_all():
    """Efter en prisuppdatering: räkna om alla listor så att sidan visar färdiga planer direkt."""
    from . import db
    for uid in db.carts_in_use():
        refresh(uid)


def _work():
    from . import db
    while True:
        user_id, items = _queue.get()
        try:
            if items is None:
                items = [(r['key'], r['qty'], r['pack']) for r in db.get_cart(user_id)]
            offers, checked = load_offers()
            catalog = json.loads(config.CATALOG_PATH.read_text(encoding='utf-8'))
            sig = _signature(items, checked)
            with _cache_lock:
                hit = _cache.get(user_id)
            if hit and hit[0] == sig and hit[1]['complete']:
                continue
            p = plan(items, offers, catalog, budget=BACKGROUND_BUDGET)
            p['complete'] = True        # bästa vi hittar: visa inte "förbättras" i all oändlighet
            with _cache_lock:
                _cache[user_id] = (sig, p)
        except Exception as ex:  # noqa: BLE001
            print(f'[inköpslista] fel i bakgrundsberäkning: {ex!r}', flush=True)
            with _cache_lock:           # sidan ska inte ladda om i väntan på något som aldrig kommer
                hit = _cache.get(user_id)
                if hit:
                    hit[1]['complete'] = True

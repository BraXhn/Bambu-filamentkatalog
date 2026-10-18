"""Kostnadsräknare för en utskrift: filament (efter katalogens priser eller vad du betalat) + el + valfritt slitage."""
import datetime

from . import alerts, energy

# Ungefärlig medeleffekt under en utskrift (bädd och munstycke varma, PLA). ABS/ASA med varmare bädd drar mer.
PRINTERS = [
    ('a1mini', 'A1 mini', 80),
    ('a1', 'A1', 100),
    ('p1', 'P1P / P1S', 120),
    ('x1', 'X1 Carbon / X1E', 130),
    ('h2d', 'H2D', 200),
]
MODES = {
    'nu': 'Lägsta pris nu (varupris)',
    'frakt': 'Lägsta pris nu inkl. frakt (en spole)',
    'betalt': 'Vad jag betalade senast',
    'eget': 'Eget pris per kg',
}
SOURCES = {'tibber': 'Tibber', 'spot': 'Spotpris (elprisetjustnu.se)', 'fast': 'Fast pris'}
MAX_ROWS = 8


def _per_kg_now(key, offers, now):
    best = None
    for o in offers:
        if o.get('key') != key or o.get('kind') != 'fixed' or not alerts._live(o, now) or \
                o.get('condition') not in ('Ny', 'Oanvänt') or not o.get('weight') or not isinstance(o.get('price'), (int, float)):
            continue
        per_kg = o['price'] / o['weight'] * 1000
        if best is None or per_kg < best[0]:
            best = (per_kg, o)
    return best


def price_per_kg(key, mode, offers, paid, custom, now=None):
    """(kr/kg, förklaring) för en färg, eller (None, varför inte)."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if mode == 'eget' or not key:
        return (custom, 'eget pris') if custom else (None, 'ange eget pris per kg')
    if mode == 'betalt' and key in paid:
        price, weight = paid[key]
        return price / weight * 1000, 'senaste köpet'
    if mode == 'frakt':
        info = alerts.best(key, offers, now)
        if info['best']:
            o, total = info['best']
            return total / o['weight'] * 1000, f"{o['shop'].split(' · ')[0]} inkl. frakt"
    hit = _per_kg_now(key, offers, now)
    if hit:
        note = f"{hit[1]['shop'].split(' · ')[0]}, varupris"
        if mode == 'betalt':
            note += ' (inget eget köp sparat)'
        return hit[0], note
    if custom:
        return custom, 'eget pris (inget pris i katalogen)'
    return None, 'inget pris i katalogen – ange eget pris per kg'


def calculate(rows, mode, custom_per_kg, hours, watts, intervals, start, wear_per_h, offers, paid, now=None):
    """rows: [(katalognyckel eller '', gram)]. intervals: elpriser [(start, slut, kr/kWh)]."""
    lines, filament, grams_total, missing = [], 0.0, 0.0, False
    for key, grams in rows:
        per_kg, note = price_per_kg(key, mode, offers, paid, custom_per_kg, now)
        cost = grams / 1000 * per_kg if per_kg is not None else None
        missing |= cost is None
        lines.append({'key': key, 'grams': grams, 'per_kg': per_kg, 'note': note, 'cost': cost})
        filament += cost or 0
        grams_total += grams
    el = energy.window_cost(intervals, start, hours, watts) if hours > 0 else \
        {'kwh': 0.0, 'cost': 0.0, 'covered': 1.0, 'low': None, 'high': None}
    wear = wear_per_h * hours
    total = filament + el['cost'] + wear
    cheap = energy.cheapest_start(intervals, hours, watts, now or start) if len(intervals) > 1 else None
    return {'lines': lines, 'filament': filament, 'grams': grams_total, 'el': el, 'wear': wear, 'total': total,
            'missing': missing, 'cheapest': cheap, 'per_gram': total / grams_total if grams_total else None}

"""Utskriftslogg: sparade utskrifter (från kostnadsräknaren eller Bambu-molnets historik), summor och förbrukningsprognos."""
import datetime
import json
import time
from collections import defaultdict

from . import bambu, cart, config, costcalc, db, energy

STATUS = {2: 'ok', 3: 'failed', 4: 'cancelled'}      # Bambu-molnets uppgiftsstatus; övriga = pågår (hoppas över)
USAGE_DAYS = 60                                       # prognosen bygger på förbrukningen de senaste 60 dagarna


def _printer_watts(model, default):
    m = (model or '').lower().replace(' ', '')
    for needle, watts in (('a1mini', 80), ('n1', 80), ('a1', 100), ('n2s', 100), ('p1', 120), ('x1', 130), ('h2', 200)):
        if needle in m:
            return watts
    return default


def _settings(uid):
    def f(name, default):
        try:
            return float(str(db.get_pref(uid, name) or default).replace(',', '.'))
        except ValueError:
            return default
    return {'watts': f('cost_watts', 120), 'extra': f('cost_extra', 0), 'fixed': f('cost_fixed', 2.0), 'wear': f('cost_wear', 0),
            'kg': f('cost_kg', 0) or None, 'mode': db.get_pref(uid, 'cost_mode') or 'nu',
            'source': db.get_pref(uid, 'cost_source') or 'spot', 'area': db.get_pref(uid, 'cost_area') or 'SE3',
            'vat': (db.get_pref(uid, 'cost_vat') or '1') == '1'}


def _intervals_for(st, start, hours):
    """Elpris för en utskrift i efterhand: spotpris för dagarna (även för Tibber-användare), annars fast pris."""
    if st['source'] == 'fast':
        return [(start - datetime.timedelta(days=1), start + datetime.timedelta(days=3), st['fixed'] + st['extra'])]
    out = []
    day = start.astimezone(energy.TZ).date()
    end = (start + datetime.timedelta(hours=hours)).astimezone(energy.TZ).date()
    while day <= end:
        try:
            out += [(s, e, round(p * (energy.VAT if st['vat'] else 1) + st['extra'], 4)) for s, e, p in energy.spot_day(st['area'], day)]
        except energy.EnergyError:
            pass
        day += datetime.timedelta(days=1)
    return out or [(start - datetime.timedelta(days=1), start + datetime.timedelta(days=3), st['fixed'] + st['extra'])]


def _parse(ts):
    try:
        t = datetime.datetime.fromisoformat(str(ts).replace('Z', '+00:00'))
        return t if t.tzinfo else t.replace(tzinfo=datetime.timezone.utc)
    except ValueError:
        return None


def import_bambu(user_id, link=None):
    """Hämtar avslutade utskrifter från Bambu-molnet som inte redan finns i loggen. Returnerar antal nya."""
    link = link or db.get_bambu(user_id)
    if not link or not link['token']:
        return 0
    tasks = bambu.get_tasks(link['token'], link['region'])
    have = db.print_ext_ids(user_id)
    fresh = [t for t in tasks if str(t.get('id')) not in have and t.get('status') in STATUS]
    if not fresh:
        return 0
    catalog = json.loads(config.CATALOG_PATH.read_text(encoding='utf-8'))
    matcher = bambu.SpoolMatcher(catalog, bambu.color_db())
    offers, _ = cart.load_offers()
    paid = db.last_paid(user_id)
    st = _settings(user_id)
    n = 0
    for t in fresh:
        start = _parse(t.get('startTime'))
        if not start:
            continue
        try:
            hours = max(0.0, float(t.get('costTime') or 0) / 3600)
            weight = float(t.get('weight') or 0)
        except (TypeError, ValueError):
            continue
        grams = defaultdict(float)
        for slot in t.get('amsDetailMapping') or []:
            if not isinstance(slot, dict):
                continue
            try:
                g = float(slot.get('weight') or 0)
            except (TypeError, ValueError):
                continue
            d = matcher.match({'filamentName': slot.get('filamentType'), 'filamentId': slot.get('filamentId'),
                               'color': '#' + str(slot.get('sourceColor') or slot.get('targetColor') or '').lstrip('#')})
            grams[matcher.key(d) if d else ''] += g
        if not grams and weight:
            grams[''] = weight
        watts = _printer_watts(t.get('deviceModel'), st['watts'])
        res = costcalc.calculate(list(grams.items()), st['mode'], st['kg'], hours, watts, _intervals_for(st, start, hours),
                                 start, st['wear'], offers, paid, start)
        name = str(t.get('designTitle') or t.get('title') or 'Utskrift')
        printer = ' · '.join(x for x in (str(t.get('deviceName') or ''), str(t.get('deviceModel') or '')) if x) or None
        if db.add_print(user_id, start.timestamp(), name, printer, hours, {k: round(v, 2) for k, v in grams.items()},
                        res['filament'], res['el']['cost'], res['el']['kwh'], res['wear'], res['total'],
                        STATUS[t['status']], 'bambu', str(t.get('id'))):
            n += 1
    return n


def stats(user_id, now=None):
    """Summor för loggen: i år, per månad (12 senaste), per färg och per skrivare."""
    now = now or datetime.datetime.now(energy.TZ)
    rows = db.get_prints(user_id)
    year = now.year
    out = {'year': {'n': 0, 'grams': 0.0, 'total': 0.0, 'filament': 0.0, 'el': 0.0, 'failed': 0}, 'months': {}, 'colors': {},
           'printers': {}}
    for r in rows:
        t = datetime.datetime.fromtimestamp(r['ts'], energy.TZ)
        grams = json.loads(r['grams'] or '{}')
        g = sum(grams.values())
        if t.year == year:
            y = out['year']
            y['n'] += 1
            y['grams'] += g
            y['total'] += r['total']
            y['filament'] += r['filament']
            y['el'] += r['el']
            y['failed'] += r['status'] != 'ok'
        m = out['months'].setdefault(t.strftime('%Y-%m'), {'n': 0, 'grams': 0.0, 'total': 0.0})
        m['n'] += 1
        m['grams'] += g
        m['total'] += r['total']
        share = r['filament'] / g if g else 0
        for k, v in grams.items():
            c = out['colors'].setdefault(k, {'grams': 0.0, 'cost': 0.0})
            c['grams'] += v
            c['cost'] += v * share
        p = out['printers'].setdefault(r['printer'] or 'Okänd skrivare', {'n': 0, 'hours': 0.0, 'kwh': 0.0, 'total': 0.0})
        p['n'] += 1
        p['hours'] += r['hours']
        p['kwh'] += r['kwh']
        p['total'] += r['total']
    out['months'] = dict(sorted(out['months'].items(), reverse=True)[:12])
    return out


def forecast(user_id, left_by_key, now=None):
    """{katalognyckel: {'perDay': gram/dag, 'days': dagar kvar}} utifrån loggen och gram kvar enligt Bambu-biblioteket."""
    now = now or time.time()
    since = now - USAGE_DAYS * 86400
    rows = db.get_prints(user_id, since=since)
    if not rows:
        return {}
    first = min(r['ts'] for r in rows)
    span = max(14.0, (now - first) / 86400)          # minst två veckor, så att en enda utskrift inte ger orimliga tal
    used = defaultdict(float)
    for r in rows:
        for k, v in json.loads(r['grams'] or '{}').items():
            if k:
                used[k] += v
    out = {}
    for k, g in used.items():
        per_day = g / span
        left = left_by_key.get(k)
        if per_day > 0:
            out[k] = {'perDay': round(per_day, 1), 'days': round(left / per_day) if left is not None else None}
    return out

"""Spolhantering: fysiska spolar (även andra märken och spolar utan RFID) med vikt, öppnad/torkad och plats.

- Vägning: bruttovikt på köksvågen minus spolens tomvikt ger gram kvar.
- Utskrifter i loggen drar av gram från en öppnad spole av rätt färg (den med minst kvar först).
- Torkpåminnelse: materialguidens `open_days` räknat från öppnad/torkad.
"""
import datetime
import json

from . import db, materials

# Ungefärlig tomvikt (gram) för vanliga spolar; användaren kan ange egen.
TARES = [
    ('bambu', 'Bambu Lab plastspole', 250),
    ('bambu-refill', 'Bambu Lab återanvändbar spole (refill)', 250),
    ('kartong', 'Kartongspole', 140),
    ('plast-stor', 'Annan plastspole', 220),
    ('ingen', 'Utan spole', 0),
]


def today():
    from .energy import TZ
    return datetime.datetime.now(TZ).date()          # svensk tid, inte containerns UTC


def dry_status(spool, now=None):
    """(behöver torkas, dagar sedan öppnad/torkad, gräns i dagar) – None om okänt."""
    info = materials.for_type(spool['material'] or (spool['key'] or '').split('|')[0])
    since = spool['dried'] or spool['opened']
    if not info or not since:
        return None
    try:
        days = ((now or today()) - datetime.date.fromisoformat(since)).days
    except ValueError:
        return None
    return days >= info['open_days'], days, info['open_days']


def summary(user_id):
    """{katalognyckel: {'grams', 'spools'}} för spolar med katalogfärg (för "Jag har" och prognos)."""
    out = {}
    for s in db.list_spools(user_id):
        if s['key'] and s['remaining_g'] > 0:
            e = out.setdefault(s['key'], {'grams': 0, 'spools': 0})
            e['grams'] = round(e['grams'] + s['remaining_g'])
            e['spools'] += 1
    return out


def needs_drying(user_id):
    return [s for s in db.list_spools(user_id) if s['opened'] and (dry_status(s) or (False,))[0]]


def deduct(user_id, grams_by_key, ts=None, only_opened=False):
    """Drar av förbrukningen från öppnade spolar (minst kvar först); öppnar en ny spole om det behövs.
    Med ts räknas bara spolar som fanns när utskriften gjordes (gamla utskrifter från Bambu-molnet drar inget)."""
    used = {}
    for key, grams in grams_by_key.items():
        if not key or grams <= 0:
            continue
        left = float(grams)
        candidates = sorted((s for s in db.list_spools(user_id) if s['key'] == key and s['remaining_g'] > 0
                             and (ts is None or s['created'] <= ts) and (s['opened'] or not only_opened)),
                            key=lambda s: (not s['opened'], s['remaining_g']))
        for s in candidates:
            if left <= 0:
                break
            take = min(left, s['remaining_g'])
            fields = {'remaining_g': round(s['remaining_g'] - take, 1)}
            if not s['opened']:
                fields['opened'] = today().isoformat()
            db.update_spool(user_id, s['id'], **fields)
            used[s['id']] = take
            left -= take
    return used


def from_weight(gross, tare):
    return max(0.0, float(gross) - float(tare))


def export_rows(user_id):
    return [dict(s) for s in db.list_spools(user_id)] + [dict(s) for s in db.list_spools(user_id, archived=True)]


def dumps(rows):
    return json.dumps(rows, ensure_ascii=False)


def available(user_id):
    """Gram kvar per katalogfärg med en enda regel: det största av Bambu-biblioteket, Spoolman och egna spolar
    (samma spole kan finnas registrerad på flera ställen, så värdena summeras inte)."""
    have = {}
    link = db.get_bambu(user_id)
    for k, v in (json.loads(link['matched'] or '{}') if link else {}).items():
        have[k] = max(have.get(k, 0), v.get('grams') or 0)
    try:
        for k, v in json.loads(db.get_pref(user_id, 'spoolman_matched') or '{}').items():
            have[k] = max(have.get(k, 0), v.get('grams') or 0)
    except ValueError:
        pass
    for k, v in summary(user_id).items():
        have[k] = max(have.get(k, 0), v['grams'])
    return have

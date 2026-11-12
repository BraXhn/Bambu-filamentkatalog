"""Nya färger hos Bambu Lab: jämför katalogen och Bambu Studios färgtabell med vad som setts tidigare.

Första körningen sparar allt som redan finns (inget visas som nytt). Därefter markeras färger som dyker upp i
katalogen eller i Bambus tabell som nya i NEW_DAYS dagar, och användare som vill aviseras via ntfy.
"""
import json
import time

from . import alerts, bambu, config, db

NEW_DAYS = 60
STATE = 'new_colors.json'


def _path():
    return config.STATE_DIR / STATE


def _load():
    try:
        return json.loads(_path().read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def _save(state):
    from .prices.update import write_json_atomic
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    write_json_atomic(_path(), state, gz=False)


def _catalog():
    try:
        return json.loads(config.CATALOG_PATH.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return []


def _key(d):
    return d['type'] + '|' + (d['code'] or d['name'])


def _name(entry):
    n = entry.get('fila_color_name')
    if isinstance(n, dict):
        return str(n.get('sv') or n.get('en') or next((v for v in n.values() if v), '') or '')
    return str(n or '')


def _bambu_entry(e):
    return {'code': str(e.get('fila_color_code') or ''), 'type': str(e.get('fila_type') or ''), 'name': _name(e)[:60],
            'hex': [h for h in (e.get('fila_color') or []) if isinstance(h, str)][:6], 'id': str(e.get('fila_id') or '')}


def check(colors=None, catalog=None, now=None):
    """Uppdaterar vad som setts. Returnerar nyheterna från den här körningen: [{'kind': 'katalog'|'bambu', ...}]."""
    now = now or time.time()
    catalog = _catalog() if catalog is None else catalog
    colors = bambu.color_db(max_age=86400) if colors is None else colors
    state = _load()
    first_run = not state.get('seeded')
    seen_cat, seen_bambu = state.setdefault('catalog', {}), state.setdefault('bambu', {})
    stamp = 0 if first_run else now           # 0 = fanns redan när bevakningen började
    fresh = []
    for d in catalog:
        k = _key(d)
        if k not in seen_cat:
            seen_cat[k] = stamp
            if not first_run:
                fresh.append({'kind': 'katalog', 'key': k, 'type': d['type'], 'name': d['name'], 'code': d['code'] or ''})
    codes = {d['code'] for d in catalog if d['code']}
    for e in colors:
        if not isinstance(e, dict):
            continue
        b = _bambu_entry(e)
        if not b['code'] or not b['type']:
            continue
        if b['code'] not in seen_bambu:
            seen_bambu[b['code']] = dict(b, first=stamp)
            if not first_run and b['code'] not in codes:
                fresh.append(dict(b, kind='bambu'))
    if first_run:
        state['seeded'] = now
    _save(state)
    return fresh


def summary(now=None, catalog=None):
    """För katalogen: nya katalogfärger {nyckel: tid}, nya färger hos Bambu som saknas i katalogen, och alla som saknas."""
    now = now or time.time()
    state = _load()
    cutoff = now - NEW_DAYS * 86400
    catalog = _catalog() if catalog is None else catalog
    keys = {_key(d) for d in catalog}
    codes = {d['code'] for d in catalog if d['code']}
    recent = {k: t for k, t in state.get('catalog', {}).items() if t and t >= cutoff and k in keys}
    missing = [v for v in state.get('bambu', {}).values() if v.get('code') not in codes]
    upcoming = sorted((v for v in missing if v.get('first') and v['first'] >= cutoff), key=lambda v: -v['first'])
    missing.sort(key=lambda v: (v.get('type', ''), v.get('name', '')))
    return {'recent': recent, 'upcoming': upcoming, 'missing': missing, 'since': state.get('seeded')}


def _line(n):
    code = f" ({n['code']})" if n.get('code') else ''
    where = '' if n['kind'] == 'katalog' else ' – finns hos Bambu Lab, inte i katalogen än'
    return f"{n['type']} {n['name'] or 'ny färg'}{code}{where}"


def notify(fresh, log=print):
    """Aviserar användare som valt det (pref notify_new) via deras ntfy-ämne."""
    if not fresh:
        return
    try:
        from . import homeassistant
        homeassistant.event('nya_farger', {'farger': [_line(n) for n in fresh[:30]]})
    except Exception:  # noqa: BLE001
        pass
    lines = [_line(n) for n in fresh[:15]] + ([f'… och {len(fresh) - 15} till'] if len(fresh) > 15 else [])
    title = f"{len(fresh)} {'ny färg' if len(fresh) == 1 else 'nya färger'} hos Bambu Lab"
    click = config.PUBLIC_URL + '/?nytt=1' if config.PUBLIC_URL else None
    for u in db.list_users():
        if db.get_pref(u['id'], 'notify_new') != '1':
            continue
        target = db.get_pref(u['id'], 'notify')
        if target:
            err = alerts.send(target, title, '\n'.join(lines), click)
            if err:
                log(f'[nyheter] användare {u["id"]}: {err}')

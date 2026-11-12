"""Export och import av en användares data (JSON för flytt, CSV för Excel). Hemligheter exporteras aldrig."""
import csv
import io
import json
import time
import zipfile

from . import db

VERSION = 1
SECRET_PREFS = {'tibber_token', 'bambu_uid'}
PREF_KEYS = {'budget', 'low_grams', 'notify', 'notify_new', 'samkop_ok', 'cost_watts', 'cost_source', 'cost_area', 'cost_extra',
             'cost_fixed', 'cost_vat', 'cost_mode', 'cost_kg', 'cost_wear'}


def export(user):
    uid = user['id']
    rows = lambda q, *a: [dict(r) for r in db.conn().execute(q, a).fetchall()]  # noqa: E731
    return {
        'format': 'bambu-filamentkatalog', 'version': VERSION, 'exported': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'username': user['username'],
        'owned': db.get_owned(uid) or [],
        'wish': db.get_wish(uid),
        'alerts': rows('SELECT key, target, min_discount FROM alerts WHERE user_id=?', uid),
        'cart': rows('SELECT key, qty, pack FROM cart WHERE user_id=?', uid),
        'purchases': rows('SELECT ts, shop, key, qty, unit_price, pack, url, weight FROM purchases WHERE user_id=? ORDER BY ts', uid),
        'prints': [dict(r, grams=json.loads(r['grams'] or '{}')) for r in rows(
            'SELECT ts, name, printer, hours, grams, filament, el, kwh, wear, total, status, reason, source, ext_id, '
            '(SELECT name FROM projects WHERE id=prints.project_id) AS project FROM prints WHERE user_id=? ORDER BY ts', uid)],
        'projects': [dict(r, colors=json.loads(r['colors'] or '[]')) for r in rows('SELECT name, customer, colors, note FROM projects WHERE user_id=?', uid)],
        'prefs': {r['key']: r['value'] for r in db.conn().execute('SELECT key, value FROM prefs WHERE user_id=?', (uid,))
                  if r['key'] in PREF_KEYS},
    }


def export_csv_zip(user, names):
    data = export(user)
    buf = io.BytesIO()
    label = lambda k: f"{k.split('|')[0]} {names.get(k, '')}".strip() if k else 'Annan färg'  # noqa: E731
    tables = {
        'markeringar.csv': (['Färg', 'Nyckel'], [[label(k), k] for k in data['owned']]),
        'onskelista.csv': (['Färg', 'Målpris (kr)', 'Minsta rabatt (%)'],
                           [[label(k), next((a['target'] for a in data['alerts'] if a['key'] == k), ''),
                             next((a['min_discount'] for a in data['alerts'] if a['key'] == k), '')] for k in data['wish']]),
        'inkopslista.csv': (['Färg', 'Antal', 'Förpackning'], [[label(c['key']), c['qty'], c['pack']] for c in data['cart']]),
        'kop.csv': (['Datum', 'Butik', 'Färg', 'Antal', 'Styckpris (kr)', 'Förpackning'],
                    [[time.strftime('%Y-%m-%d', time.localtime(p['ts'])), p['shop'], label(p['key']), p['qty'], p['unit_price'], p['pack'] or '']
                     for p in data['purchases']]),
        'utskrifter.csv': (['Datum', 'Namn', 'Skrivare', 'Projekt', 'Resultat', 'Orsak', 'Tid (h)', 'Filament (g)', 'Filament (kr)', 'El (kr)', 'Totalt (kr)'],
                           [[time.strftime('%Y-%m-%d %H:%M', time.localtime(p['ts'])), p['name'], p['printer'] or '', p['project'] or '', p['status'],
                             p['reason'] or '', f"{p['hours']:.2f}", f"{sum(p['grams'].values()):.1f}", f"{p['filament']:.2f}", f"{p['el']:.2f}",
                             f"{p['total']:.2f}"] for p in data['prints']]),
        'projekt.csv': (['Projekt', 'Kund', 'Färger'], [[p['name'], p['customer'] or '', ', '.join(label(k) for k in p['colors'])] for p in data['projects']]),
    }
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        for name, (head, body) in tables.items():
            s = io.StringIO()
            w = csv.writer(s, delimiter=';')
            w.writerow(head)
            w.writerows(body)
            z.writestr(name, '﻿' + s.getvalue())
    return buf.getvalue()


def import_data(uid, data, names):
    """Lägger till och uppdaterar (tar aldrig bort). Returnerar antal per del. Okända färger hoppas över."""
    if not isinstance(data, dict) or data.get('format') != 'bambu-filamentkatalog':
        raise ValueError('Filen är inte en export från filamentkatalogen.')
    ok = lambda k: isinstance(k, str) and k in names  # noqa: E731
    n = {}
    owned = set(db.get_owned(uid) or [])
    new = {k for k in data.get('owned') or [] if ok(k)} - owned
    if new:
        db.set_owned(uid, sorted(owned | new))
    n['markeringar'] = len(new)
    wish = set(db.get_wish(uid))
    neww = {k for k in data.get('wish') or [] if ok(k)} - wish
    if neww:
        db.set_wish(uid, sorted(wish | neww))
    n['önskelista'] = len(neww)
    na = 0
    for a in data.get('alerts') or []:
        if isinstance(a, dict) and ok(a.get('key')) and a['key'] in (wish | neww):
            t, d = a.get('target'), a.get('min_discount')
            t = int(t) if isinstance(t, (int, float)) and 0 < t <= 100_000 else None
            d = int(d) if isinstance(d, (int, float)) and 0 < d <= 90 else None
            if t or d:
                db.set_alert(uid, a['key'], t, d)
                na += 1
    n['bevakningar'] = na
    have = {r['key']: r['qty'] for r in db.get_cart(uid)}
    nc = 0
    for c in data.get('cart') or []:
        if isinstance(c, dict) and ok(c.get('key')) and isinstance(c.get('qty'), int) and c['qty'] > have.get(c['key'], 0):
            if db.set_cart_item(uid, c['key'], min(c['qty'], db.CART_MAX_QTY), c.get('pack') if c.get('pack') in ('any', 'Spole', 'Refill') else 'any'):
                nc += 1
    n['inköpslista'] = nc
    seen = {(r['ts'], r['shop'], r['key']) for r in db.get_purchases(uid, 100_000)}
    npu = 0
    with db.tx() as c:
        for p in data.get('purchases') or []:
            if not (isinstance(p, dict) and isinstance(p.get('ts'), (int, float)) and isinstance(p.get('shop'), str)
                    and isinstance(p.get('key'), str) and isinstance(p.get('qty'), int) and isinstance(p.get('unit_price'), (int, float))):
                continue
            ident = (float(p['ts']), p['shop'][:80], p['key'][:120])
            if ident in seen:
                continue
            seen.add(ident)
            url = p.get('url') if str(p.get('url') or '').startswith('https://') else None
            c.execute('INSERT INTO purchases(user_id,ts,shop,key,qty,unit_price,pack,url,weight) VALUES(?,?,?,?,?,?,?,?,?)',
                      (uid, *ident, max(1, min(p['qty'], 500)), int(p['unit_price']), p.get('pack') if isinstance(p.get('pack'), str) else None,
                       url, p.get('weight') if isinstance(p.get('weight'), int) else None))
            npu += 1
    n['köp'] = npu
    projects = {p['name']: p['id'] for p in db.list_projects(uid)}
    npj = 0
    for p in data.get('projects') or []:
        if isinstance(p, dict) and isinstance(p.get('name'), str) and p['name'].strip() and p['name'] not in projects:
            pid = db.create_project(uid, p['name'].strip(), p.get('customer') if isinstance(p.get('customer'), str) else None)
            db.update_project(uid, pid, colors=[k for k in p.get('colors') or [] if ok(k)][:40],
                              note=p.get('note')[:2000] if isinstance(p.get('note'), str) else None)
            projects[p['name']] = pid
            npj += 1
    n['projekt'] = npj
    existing = {(r['ts'], r['name']) for r in db.get_prints(uid, limit=100_000)}
    npr = 0
    for p in data.get('prints') or []:
        if not isinstance(p, dict) or not isinstance(p.get('ts'), (int, float)) or not isinstance(p.get('name'), str):
            continue
        if (p['ts'], p['name'][:120]) in existing:
            continue
        grams = {k: float(v) for k, v in (p.get('grams') or {}).items() if (k == '' or ok(k)) and isinstance(v, (int, float))}
        num = lambda x: float(x) if isinstance(x, (int, float)) else 0.0  # noqa: E731
        src = p.get('source') if p.get('source') in ('manual', 'bambu') else 'manual'
        new_id = db.add_print(uid, float(p['ts']), p['name'], p.get('printer') if isinstance(p.get('printer'), str) else None, num(p.get('hours')),
                              grams, num(p.get('filament')), num(p.get('el')), num(p.get('kwh')), num(p.get('wear')), num(p.get('total')),
                              p.get('status') if p.get('status') in ('ok', 'failed', 'cancelled') else 'ok', src,
                              str(p['ext_id']) if src == 'bambu' and p.get('ext_id') else None)
        if new_id:
            npr += 1
            fields = {}
            if p.get('reason') and isinstance(p['reason'], str):
                fields['reason'] = p['reason'][:80]
            if p.get('project') in projects:
                fields['project_id'] = projects[p['project']]
            if fields:
                db.update_print(uid, new_id, **fields)
    n['utskrifter'] = npr
    np_ = 0
    for k, v in (data.get('prefs') or {}).items():
        if k in PREF_KEYS and k not in SECRET_PREFS and isinstance(v, str) and len(v) <= 200:
            db.set_pref(uid, k, v)
            np_ += 1
    n['inställningar'] = np_
    return n

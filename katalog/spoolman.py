"""Synk med Spoolman (egen server för spolhantering), inställd av en administratör för en användare.

Hämta: Spoolmans spolar kopplas till katalogens färger och visas som "Jag har" med gram kvar.
Skicka (valfritt): spolarna i Bambu-biblioteket skapas/uppdateras i Spoolman. Kopplingen sparas i spolens
kommentar ("bambu:<id>"), så att samma spole uppdateras nästa gång i stället för att dubbleras.
"""
import ipaddress
import json
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

from . import bambu, config, db

TIMEOUT = 15


class SpoolmanError(Exception):
    pass


def settings():
    return {'url': (db.get_setting('spoolman_url') or '').rstrip('/'), 'user_id': db.get_setting('spoolman_user_id'),
            'push': db.get_setting('spoolman_push') == '1'}


def enabled():
    s = settings()
    return bool(s['url'] and s['user_id'])


def check_url(url):
    """Spoolman ska ligga i det egna nätet: http(s) till en privat adress (skydd mot att servern används mot internet)."""
    try:
        p = urllib.parse.urlsplit(url)
    except ValueError:
        return 'Ogiltig adress.'
    if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.query or p.fragment:
        return 'Ange adressen som http://192.168.1.10:7912'
    try:
        infos = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == 'https' else 80), proto=socket.IPPROTO_TCP)
    except OSError:
        return f'Hittar inte {p.hostname}.'
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not (ip.is_private or ip.is_loopback) or ip.is_link_local:
            return 'Spoolman måste ligga i det lokala nätet (privat IP-adress).'
    return None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def _call(base, method, path, body=None):
    err = check_url(base)
    if err:
        raise SpoolmanError(err)
    req = urllib.request.Request(base + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={'Content-Type': 'application/json', 'Accept': 'application/json', 'User-Agent': 'BambuKatalog/1.0'})
    try:
        with urllib.request.build_opener(_NoRedirect).open(req, timeout=TIMEOUT) as r:
            raw = r.read(20_000_000)
    except urllib.error.HTTPError as e:
        raise SpoolmanError(f'Spoolman svarade {e.code} på {path}') from None
    except (urllib.error.URLError, OSError) as e:
        raise SpoolmanError(f'Kunde inte nå Spoolman ({getattr(e, "reason", e)})') from None
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        raise SpoolmanError('Oväntat svar från Spoolman') from None


def _norm(s):
    return re.sub(r'[^a-z0-9+]', '', str(s or '').lower())


def _matcher():
    catalog = json.loads(config.CATALOG_PATH.read_text(encoding='utf-8'))
    types = sorted({d['type'] for d in catalog}, key=lambda t: -len(t))
    return bambu.SpoolMatcher(catalog, bambu.color_db()), types


def match_spool(sp, matcher, types):
    f = sp.get('filament') or {}
    text = _norm(' '.join(str(x or '') for x in ((f.get('vendor') or {}).get('name'), f.get('name'), f.get('material'))))
    typ = next((t for t in types if _norm(t) in text), f.get('material'))
    hexes = f.get('multi_color_hexes') or f.get('color_hex') or ''
    colors = ['#' + h.strip().lstrip('#') for h in str(hexes).split(',') if h.strip()]
    m = re.search(r'bambu-id:([A-Za-z0-9]+)', str(sp.get('comment') or ''))
    return matcher.match({'filamentName': typ, 'colors': colors, 'filamentId': m.group(1) if m else None})


def pull(base, uid):
    spools = _call(base, 'GET', '/api/v1/spool?allow_archived=false') or []
    matcher, types = _matcher()
    matched, unmatched = {}, 0
    for sp in spools:
        if not isinstance(sp, dict) or sp.get('archived'):
            continue
        d = match_spool(sp, matcher, types)
        grams = float(sp.get('remaining_weight') or 0)
        if not d:
            unmatched += 1
            continue
        k = matcher.key(d)
        e = matched.setdefault(k, {'grams': 0, 'spools': 0})
        e['grams'] = round(e['grams'] + grams)
        e['spools'] += 1
    db.set_pref(uid, 'spoolman_matched', json.dumps(matched))
    return len(spools), len(matched), unmatched


def push(base, uid):
    """Bambu-bibliotekets spolar -> Spoolman (skapar tillverkare/filament/spole vid behov, uppdaterar gram kvar)."""
    link = db.get_bambu(uid)
    if not link or not link['token']:
        return 0, 0
    spools = bambu.get_spools(link['token'], link['region'])
    catalog = json.loads(config.CATALOG_PATH.read_text(encoding='utf-8'))
    matcher = bambu.SpoolMatcher(catalog, bambu.color_db())
    existing = _call(base, 'GET', '/api/v1/spool?allow_archived=true') or []
    by_bambu = {}
    for sp in existing:
        m = re.search(r'bambu:(\w+)', str(sp.get('comment') or ''))
        if m:
            by_bambu[m.group(1)] = sp
    vendors = _call(base, 'GET', '/api/v1/vendor') or []
    vendor = next((v for v in vendors if _norm(v.get('name')) == 'bambulab'), None) or \
        _call(base, 'POST', '/api/v1/vendor', {'name': 'Bambu Lab'})
    filaments = {(f.get('name'), (f.get('vendor') or {}).get('id')): f for f in (_call(base, 'GET', '/api/v1/filament') or [])}
    created = updated = 0
    for s in spools:
        if s.get('status') not in (0, None) or not s.get('id'):
            continue
        d = matcher.match(s)
        if not d:
            continue
        remaining = float(s.get('netWeight') or 0)
        sid = str(s['id'])
        if sid in by_bambu:
            if abs(float(by_bambu[sid].get('remaining_weight') or 0) - remaining) >= 1:
                _call(base, 'PATCH', f"/api/v1/spool/{by_bambu[sid]['id']}", {'remaining_weight': remaining})
                updated += 1
            continue
        name = f"{d['type']} {d['name']}"
        fil = filaments.get((name, vendor['id']))
        if not fil:
            hexes = [h.lstrip('#')[:6] for h in d.get('hex') or []]
            body = {'name': name, 'vendor_id': vendor['id'], 'material': d['type'].split(' ')[0], 'density': 1.24, 'diameter': 1.75,
                    'weight': float(s.get('totalNetWeight') or 1000), 'article_number': d['code'] or None}
            if len(hexes) > 1:
                body.update(multi_color_hexes=','.join(hexes), multi_color_direction='longitudinal')
            elif hexes:
                body['color_hex'] = hexes[0]
            fil = _call(base, 'POST', '/api/v1/filament', body)
            filaments[(name, vendor['id'])] = fil
        _call(base, 'POST', '/api/v1/spool', {'filament_id': fil['id'], 'remaining_weight': remaining,
                                              'comment': f"bambu:{sid} bambu-id:{s.get('filamentId') or ''} (från filamentkatalogen)"})
        created += 1
    return created, updated


def sync(log=print):
    s = settings()
    if not enabled():
        return None
    uid = int(s['user_id'])
    msg = []
    try:
        if s['push']:
            c, u = push(s['url'], uid)
            msg.append(f'{c} spolar skapade och {u} uppdaterade i Spoolman')
        n, k, x = pull(s['url'], uid)
        msg.append(f'{n} spolar hämtade från Spoolman, {k} färger kopplade till katalogen' + (f', {x} utan motsvarighet' if x else ''))
        status = 'ok'
    except (SpoolmanError, bambu.BambuError) as ex:
        msg.append(str(ex))
        status = 'error'
    text = '. '.join(msg) + '.'
    db.set_setting('spoolman_status', json.dumps({'status': status, 'msg': text, 'ts': time.time()}, ensure_ascii=False))
    log(f'[spoolman] {text}')
    return status, text

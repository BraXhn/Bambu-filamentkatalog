"""Prishistorik per färg för diagrammet i prisdialogen (läser prishämtarens price_history.json)."""
import json
import os
import threading
import urllib.parse

from . import config

_lock = threading.Lock()
_cache = {'mtime': None, 'by_key': {}}

# Reserv när erbjudandet inte längre finns i offers.json (butiksnamn från adressen).
HOSTS = {'www.3djake.se': '3DJake', 'www.3dprima.com': '3D Prima', 'www.tradera.com': 'Tradera',
         'eu.store.bambulab.com': 'Bambu Lab EU', '3dverkstan.se': '3DVerkstan', 'www.3dverkstan.se': '3DVerkstan',
         '3dexperten.se': '3DExperten'}


def _load():
    path = config.STATE_DIR / 'price_history.json'
    try:
        mtime = os.stat(path).st_mtime
    except OSError:
        return {}
    with _lock:
        if _cache['mtime'] != mtime:
            try:
                raw = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                raw = {}
            by_key = {}
            for oid, days in raw.items():
                parts = oid.split('|')
                if len(parts) < 4 or not isinstance(days, dict):
                    continue
                key, url, pack = '|'.join(parts[:2]), '|'.join(parts[2:-1]), parts[-1]
                by_key.setdefault(key, []).append((url, pack, days))
            _cache.update(mtime=mtime, by_key=by_key)
        return _cache['by_key']


def series(key, offers):
    """[{shop, pack, points: [[datum, lägsta varupris], ...]}] – en serie per butik och förpackning."""
    shop_of = {o['url']: o['shop'].split(' · ')[0] for o in offers if o.get('url')}
    merged = {}
    for url, pack, days in _load().get(key, []):
        shop = shop_of.get(url) or HOSTS.get(urllib.parse.urlsplit(url).hostname or '', 'Okänd butik')
        if shop.startswith('Tradera'):
            shop = 'Tradera'
        pts = merged.setdefault((shop, pack), {})
        for d, v in days.items():
            if isinstance(v, (int, float)) and v > 0:
                pts[d] = min(v, pts.get(d, v))
    out = [{'shop': s, 'pack': p, 'points': sorted([d, v] for d, v in pts.items())}
           for (s, p), pts in merged.items() if pts]
    out.sort(key=lambda x: (x['shop'], x['pack']))
    return out

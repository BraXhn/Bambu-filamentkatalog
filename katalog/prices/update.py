"""Kör prisuppdateringen: hämtar från alla källor och skriver offers.json atomiskt."""
import datetime
import gzip
import json
import os
import tempfile
import time
import traceback
from pathlib import Path

from .. import config
from . import jake, prima, tradera
from .common import Http, Matcher

SOURCES = {
    '3djake': ('3DJake', jake, lambda o: o['shop'] == jake.SHOP),
    '3dprima': ('3D Prima', prima, lambda o: o['shop'] == prima.SHOP),
    'tradera': ('Tradera', tradera, lambda o: o['shop'].startswith('Tradera')),
}
SEED_STATE = Path(__file__).with_name('seed_state.json')


def write_json_atomic(path, obj, gz=True):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(obj, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    for target, data in [(path, raw)] + ([(path.with_name(path.name + '.gz'), gzip.compress(raw, 9, mtime=0))] if gz else []):
        fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix='.tmp-')
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
        os.chmod(tmp, 0o644)
        os.replace(tmp, target)
    # Samma mtime på .json och .json.gz så att ETag/Last-Modified stämmer.
    if gz:
        st = os.stat(path)
        os.utime(path.with_name(path.name + '.gz'), (st.st_atime, st.st_mtime))


def load_state():
    p = config.STATE_DIR / 'state.json'
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        pass
    try:
        return json.loads(SEED_STATE.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def run_update(log=print, sources=None):
    """Returnerar (status, summary). status: ok | partial | failed."""
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')
    catalog = json.loads(config.CATALOG_PATH.read_text(encoding='utf-8'))
    matcher = Matcher(catalog)
    try:
        previous = json.loads((config.WWW_DIR / 'offers.json').read_text(encoding='utf-8')).get('offers', [])
    except (OSError, ValueError):
        previous = []
    state = load_state()
    http = Http(log)
    offers, summary = [], {}
    selected = [s for s in (sources or config.PRICE_SOURCES) if s in SOURCES]
    for sid, (label, module, owns) in SOURCES.items():
        prev = [o for o in previous if owns(o)]
        if sid not in selected:
            offers += prev
            summary[sid] = dict(label=label, status='skipped', offers=len(prev), previous=len(prev))
            continue
        t0 = time.time()
        log(f'{label}: hämtar…')
        try:
            got = module.run(http, matcher, state, log, now)
            if not got and len(prev) >= 5:
                raise RuntimeError('0 erbjudanden hittades (tidigare %d). Sidans format kan ha ändrats; '
                                   'tidigare priser behålls.' % len(prev))
            offers += got
            summary[sid] = dict(label=label, status='ok', offers=len(got), previous=len(prev),
                                seconds=round(time.time() - t0))
        except Exception as e:  # en trasig källa ska inte stoppa de andra
            log(f'{label}: FEL – {e}')
            log(traceback.format_exc(limit=3))
            offers += prev
            summary[sid] = dict(label=label, status='failed', offers=len(prev), previous=len(prev),
                                error=str(e)[:300], seconds=round(time.time() - t0))
    unique = {(o['key'], o['url'], o['pack']): o for o in offers}
    offers = list(unique.values())
    write_json_atomic(config.WWW_DIR / 'offers.json', {'checkedAt': now, 'offers': offers})
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    write_json_atomic(config.STATE_DIR / 'state.json', state, gz=False)
    statuses = {v['status'] for v in summary.values() if v['status'] != 'skipped'}
    status = 'ok' if statuses <= {'ok'} else ('failed' if statuses == {'failed'} else 'partial')
    summary['_total'] = dict(offers=len(offers), colors=len({o['key'] for o in offers}), requests=http.requests)
    log(f'Klart: {len(offers)} erbjudanden för {summary["_total"]["colors"]} färger ({http.requests} anrop).')
    return status, summary

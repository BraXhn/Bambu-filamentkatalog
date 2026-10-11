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
from . import bambustore, experten, jake, prima, tradera, verkstan
from .common import Http, Matcher, allowed_url

SOURCES = {
    '3djake': ('3DJake', jake, lambda o: o['shop'] == jake.SHOP),
    '3dprima': ('3D Prima', prima, lambda o: o['shop'] == prima.SHOP),
    'tradera': ('Tradera', tradera, lambda o: o['shop'].startswith('Tradera')),
    'bambulab': ('Bambu Lab EU', bambustore, lambda o: o['shop'] == bambustore.SHOP),
    '3dverkstan': ('3DVerkstan', verkstan, lambda o: o['shop'] == verkstan.SHOP),
    '3dexperten': ('3DExperten', experten, lambda o: o['shop'] == experten.SHOP),
}
SEED_STATE = Path(__file__).with_name('seed_state.json')
HISTORY_DAYS = 60          # hur länge prishistoriken sparas
REGULAR_WINDOW = 30        # ordinarie pris = typiskt pris de senaste 30 dagarna
MIN_HISTORY_DAYS = 7       # minst så många dagars historik innan rabatt räknas fram ur historiken
MIN_DISCOUNT = 5           # procent; mindre prisskillnader räknas inte som erbjudande


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
    # Bara https-länkar till kända butiker publiceras (skydd mot manipulerade källsidor).
    offers = [o for o in offers if allowed_url(o.get('url')) and 'ecb.europa.eu' not in o.get('url', '')]
    unique = {(o['key'], o['url'], o['pack']): o for o in offers}
    offers = list(unique.values())
    deals = apply_history(offers, now)
    log(f'Erbjudanden med rabatt mot ordinarie pris: {deals}')
    write_json_atomic(config.WWW_DIR / 'offers.json', {'checkedAt': now, 'offers': offers})
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    write_json_atomic(config.STATE_DIR / 'state.json', state, gz=False)
    statuses = {v['status'] for v in summary.values() if v['status'] != 'skipped'}
    status = 'ok' if statuses <= {'ok'} else ('failed' if statuses == {'failed'} else 'partial')
    summary['_total'] = dict(offers=len(offers), colors=len({o['key'] for o in offers}), requests=http.requests)
    log(f'Klart: {len(offers)} erbjudanden för {summary["_total"]["colors"]} färger ({http.requests} anrop).')
    return status, summary


def _offer_id(o):
    return f"{o['key']}|{o['url']}|{o['pack']}"


def apply_history(offers, now_iso, history_path=None):
    """Sparar dagens pris per erbjudande och sätter ordinarie pris + rabatt.

    Ordinarie pris tas i första hand från butiken (listpris/pris före rabatt). Annars används
    medianpriset de senaste 30 dagarna (minst 7 dagars historik). Returnerar antal erbjudanden med rabatt.
    """
    import statistics
    path = Path(history_path or (config.STATE_DIR / 'price_history.json'))
    try:
        history = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        history = {}
    today = now_iso[:10]
    day = datetime.date.fromisoformat(today)
    cutoff_keep = (day - datetime.timedelta(days=HISTORY_DAYS)).isoformat()
    cutoff_window = (day - datetime.timedelta(days=REGULAR_WINDOW)).isoformat()
    deals = 0
    for o in offers:
        o.pop('regular', None); o.pop('regularSource', None); o.pop('discount', None)
        if o.get('kind') != 'fixed' or not isinstance(o.get('price'), (int, float)):
            continue
        h = history.setdefault(_offer_id(o), {})
        past = [v for d, v in h.items() if cutoff_window <= d < today and isinstance(v, (int, float))]
        h[today] = o['price']
        regular, source = None, None
        lp = o.get('listPrice')
        if isinstance(lp, (int, float)) and lp > o['price']:
            regular, source = lp, 'butik'
        elif len(past) >= MIN_HISTORY_DAYS:
            med = statistics.median(past)
            if med > o['price']:
                regular, source = round(med), 'historik'
        if regular:
            pct = round(100 * (regular - o['price']) / regular)
            if pct >= MIN_DISCOUNT:
                o['regular'], o['regularSource'], o['discount'] = regular, source, pct
                deals += 1
    live_ids = {_offer_id(o) for o in offers}
    for k in list(history):
        h = {d: v for d, v in history[k].items() if d >= cutoff_keep}
        if h and (k in live_ids or max(h) >= cutoff_window):
            history[k] = h
        else:
            del history[k]
    write_json_atomic(path, history, gz=False)
    return deals

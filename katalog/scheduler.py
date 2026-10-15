"""Schemalagd prisuppdatering (standard 01:00 Europe/Stockholm) och manuell start från admin."""
import datetime
import io
import threading
import time
from zoneinfo import ZoneInfo

from . import config, db

try:
    TZ = ZoneInfo(config.TIMEZONE)
except Exception:  # noqa: BLE001
    TZ = ZoneInfo('UTC')

_wake = threading.Event()
_pending = []
_lock = threading.Lock()
_last_bambu = time.time() - 3000  # första automatiska synken ca 10 min efter start


def _parse_time():
    try:
        h, m = (int(x) for x in config.UPDATE_TIME.split(':'))
        if 0 <= h < 24 and 0 <= m < 60:
            return h, m
    except (ValueError, AttributeError):
        pass
    return None


def next_run(now=None):
    hm = _parse_time()
    if not hm:
        return None
    now = now or datetime.datetime.now(TZ)
    cand = now.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)
    if cand <= now:
        cand = (cand + datetime.timedelta(days=1)).replace(hour=hm[0], minute=hm[1])
    return cand


def next_run_text():
    n = next_run()
    return n.strftime('%Y-%m-%d %H:%M') if n else 'Avstängd (PRICE_UPDATE_TIME)'


def trigger(kind='manual'):
    """Begär en körning. Returnerar False om en redan pågår."""
    with _lock:
        if db.running_run() or _pending:
            return False
        _pending.append(kind)
    _wake.set()
    return True


def run_now(trigger_name):
    run_id = db.start_run(trigger_name)
    if run_id is None:
        return None
    buf = io.StringIO()

    def log(msg):
        line = f'{datetime.datetime.now(TZ):%H:%M:%S} {msg}'
        print('[priser] ' + line, flush=True)
        buf.write(line + '\n')

    status, summary = 'failed', {}
    try:
        from .prices.update import run_update
        status, summary = run_update(log)
    except Exception as ex:  # noqa: BLE001
        import traceback
        log(f'Avbröts: {ex}')
        log(traceback.format_exc(limit=5))
    db.finish_run(run_id, status, summary, buf.getvalue()[-60000:])
    return status


def _loop():
    global _last_bambu
    db.init()  # egen anslutning i tråden
    if config.UPDATE_ON_START:
        trigger('start')
    nxt = next_run()
    while True:
        # Behåll en passerad tid tills den körts: annars hoppas dagens körning över om förra varvet
        # (t.ex. Bambu-synken) pågick över klockslaget.
        if not (nxt and datetime.datetime.now(TZ) >= nxt):
            nxt = next_run()
        timeout = 60.0
        if nxt:
            timeout = max(1.0, min(60.0, (nxt - datetime.datetime.now(TZ)).total_seconds()))
        _wake.wait(timeout)
        _wake.clear()
        kind = None
        with _lock:
            if _pending:
                kind = _pending.pop(0)
        if not kind and nxt and datetime.datetime.now(TZ) >= nxt:
            kind = 'schedule'
            nxt = None
        if kind:
            try:
                run_now(kind)
            except Exception as ex:  # noqa: BLE001
                print(f'[priser] fel: {ex}', flush=True)
        # Bambu Lab-synk för kopplade konton.
        if config.BAMBU_SYNC_HOURS > 0 and time.time() - _last_bambu >= config.BAMBU_SYNC_HOURS * 3600:
            _last_bambu = time.time()
            try:
                from . import bambu
                bambu.sync_all(lambda m: print('[bambu] ' + m, flush=True))
            except Exception as ex:  # noqa: BLE001
                print(f'[bambu] fel: {ex}', flush=True)
        # Städa gamla sessioner en gång per varv.
        try:
            now = time.time()
            db.purge_sessions(now - config.SESSION_IDLE_DAYS * 86400, now - config.SESSION_MAX_DAYS * 86400)
        except Exception:  # noqa: BLE001
            pass


def start():
    t = threading.Thread(target=_loop, name='scheduler', daemon=True)
    t.start()
    print(f'[priser] nästa schemalagda uppdatering: {next_run_text()} ({config.TIMEZONE})', flush=True)
    return t

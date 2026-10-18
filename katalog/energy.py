"""Elpris för kostnadsräknaren: Tibber (eget API-token), elprisetjustnu.se (spotpris per elområde) eller fast pris.

Priser hanteras som intervall [(start, slut, kr/kWh)] med tidszonsmedvetna tider. elprisetjustnu.se anger
spotpriset utan moms; Tibbers "total" är energipris + energiskatt + moms men utan nätavgift.
"""
import datetime
import hashlib
import json
import threading
import time
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo

from . import config

try:
    TZ = ZoneInfo(config.TIMEZONE)
except Exception:  # noqa: BLE001
    TZ = ZoneInfo('Europe/Stockholm')

AREAS = ('SE1', 'SE2', 'SE3', 'SE4')
VAT = 1.25
_PROD = {'elpris': 'https://www.elprisetjustnu.se', 'tibber': 'https://api.tibber.com/v1-beta/gql'}


def _base(value, prod):
    """Testadresser godtas bara mot 127.0.0.1 – i drift går anropen alltid till tjänsternas riktiga adresser."""
    return value if value == prod or value.startswith('http://127.0.0.1:') else prod


class EnergyError(Exception):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def _request(url, data=None, headers=None, timeout=10):
    req = urllib.request.Request(url, data=data, method='POST' if data else 'GET',
                                 headers={'User-Agent': 'BambuKatalog/1.0 (kostnadsräknare)', 'Accept': 'application/json',
                                          **(headers or {})})
    try:
        with urllib.request.build_opener(_NoRedirect).open(req, timeout=timeout) as r:
            return r.status, r.read(2_000_000)
    except urllib.error.HTTPError as e:
        return e.code, b''
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise EnergyError(f'kunde inte nå elpristjänsten ({e})') from None


_cache = {}
_lock = threading.Lock()


def _cached(key, ttl, fn):
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    value = fn()
    with _lock:
        if len(_cache) > 200:
            _cache.clear()
        _cache[key] = (now, value)
    return value


def _parse_time(s):
    t = datetime.datetime.fromisoformat(str(s).replace('Z', '+00:00'))
    return t if t.tzinfo else t.replace(tzinfo=TZ)


# --- elprisetjustnu.se ---
def spot_day(area, day):
    """Spotpris (kr/kWh utan moms) för ett dygn i ett elområde. Tom lista om priserna inte är publicerade än."""
    if area not in AREAS:
        raise EnergyError('okänt elområde')
    base = _base(config.ELPRIS_BASE, _PROD['elpris'])
    url = f'{base}/api/v1/prices/{day:%Y}/{day:%m-%d}_{area}.json'

    def fetch():
        status, body = _request(url)
        if status == 404:
            return []
        if status != 200:
            raise EnergyError(f'elprisetjustnu.se svarade {status}')
        try:
            rows = json.loads(body)
            return [(_parse_time(r['time_start']), _parse_time(r['time_end']), float(r['SEK_per_kWh'])) for r in rows]
        except (ValueError, KeyError, TypeError):
            raise EnergyError('oväntat svar från elprisetjustnu.se') from None
    # Morgondagens priser publiceras runt 13:00 – fråga om oftare så länge de saknas.
    return _cached(('spot', area, day.isoformat()), 3 * 3600, fetch) or _cached(('spot0', area, day.isoformat(), int(time.time() // 900)), 900, fetch)


def spot_prices(area, now=None, vat=True, extra=0.0):
    now = (now or datetime.datetime.now(TZ)).astimezone(TZ)
    out = []
    for d in (now.date() - datetime.timedelta(days=1), now.date(), now.date() + datetime.timedelta(days=1)):
        out += [(s, e, round(p * (VAT if vat else 1) + extra, 4)) for s, e, p in spot_day(area, d)]
    return sorted(out)


# --- Tibber ---
TIBBER_QUERY = ('{ viewer { homes { id appNickname address { address1 } currentSubscription { priceInfo { '
                'today { total startsAt } tomorrow { total startsAt } } } } } }')


def tibber_prices(token, extra=0.0):
    """Tibbers totalpris (energi + skatt + moms) för i dag och i morgon, per timme. Nätavgift läggs till med extra."""
    if not token:
        raise EnergyError('inget Tibber-token sparat')
    url = _base(config.TIBBER_URL, _PROD['tibber'])

    def fetch():
        status, body = _request(url, json.dumps({'query': TIBBER_QUERY}).encode(),
                                {'Content-Type': 'application/json', 'Authorization': f'Bearer {token}'})
        if status in (400, 401, 403):
            raise EnergyError('Tibber godkände inte token – kontrollera det under Inställningar')
        if status != 200:
            raise EnergyError(f'Tibber svarade {status}')
        try:
            data = json.loads(body)
            if data.get('errors'):
                raise EnergyError('Tibber: ' + str(data['errors'][0].get('message', 'fel'))[:120])
            homes = [h for h in data['data']['viewer']['homes'] if h.get('currentSubscription')]
            if not homes:
                raise EnergyError('Tibber-kontot har inget aktivt elavtal')
            info = homes[0]['currentSubscription']['priceInfo']
            pts = [(_parse_time(p['startsAt']), float(p['total'])) for p in (info.get('today') or []) + (info.get('tomorrow') or [])
                   if p and p.get('total') is not None]
        except (ValueError, KeyError, TypeError, AttributeError):
            raise EnergyError('oväntat svar från Tibber') from None
        pts.sort()
        out = []
        for i, (s, p) in enumerate(pts):
            step = pts[i + 1][0] - s if i + 1 < len(pts) else (pts[i][0] - pts[i - 1][0] if i else datetime.timedelta(hours=1))
            out.append((s, s + step, p))
        return out
    key = ('tibber', hashlib.sha256(token.encode()).hexdigest())
    return [(s, e, round(p + extra, 4)) for s, e, p in _cached(key, 1800, fetch)]


def check_tibber(token):
    """Används när token sparas: hämtar priserna direkt (utan cache) så att fel syns på en gång."""
    with _lock:
        _cache.pop(('tibber', hashlib.sha256(token.encode()).hexdigest()), None)
    return tibber_prices(token)


# --- Beräkning ---
def window_cost(intervals, start, hours, watts):
    """Kostnad för en utskrift som börjar start och pågår hours timmar med medeleffekten watts.

    Returnerar dict: kwh, cost, covered (andel av tiden med kända priser), low/high (kr/kWh i fönstret).
    Tid utan kända priser räknas med medelpriset för den kända delen (eller, om inget är känt, utan kostnad).
    """
    end = start + datetime.timedelta(hours=hours)
    kw = watts / 1000
    cost, known_h, prices = 0.0, 0.0, []
    for s, e, p in intervals:
        a, b = max(s, start), min(e, end)
        if b > a:
            h = (b - a).total_seconds() / 3600
            cost += h * kw * p
            known_h += h
            prices.append(p)
    kwh = kw * hours
    if 0 < known_h < hours:
        cost += (hours - known_h) * kw * (cost / (known_h * kw))
    return {'kwh': kwh, 'cost': cost, 'covered': known_h / hours if hours else 1.0,
            'low': min(prices) if prices else None, 'high': max(prices) if prices else None}


def cheapest_start(intervals, hours, watts, earliest):
    """Billigaste starttid (på ett intervallbyte) från earliest där hela utskriften ryms inom kända priser."""
    if not intervals or hours <= 0:
        return None
    last = max(e for _, e, _ in intervals)
    starts = sorted({max(s, earliest) for s, _, _ in intervals if s + datetime.timedelta(hours=hours) <= last and
                     max(s, earliest) + datetime.timedelta(hours=hours) <= last})
    best = None
    for st in starts:
        c = window_cost(intervals, st, hours, watts)
        if c['covered'] >= 0.999 and (best is None or c['cost'] < best[1]['cost'] - 1e-9):
            best = (st, c)
    return best

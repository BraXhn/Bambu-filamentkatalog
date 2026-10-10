"""Envägssynk från Bambu Lab-kontots filamentbibliotek (Filament Manager) till "Jag har".

Bambu Lab har inget officiellt API för biblioteket. Anropen nedan är samma som Bambu Studio
och Bambu Handy använder (dokumenterade av OpenBambuAPI). De kan sluta fungera om Bambu
ändrar något; synken misslyckas då med ett tydligt fel och senast kända innehav behålls.

Bara läsning: ingenting skrivs någonsin till Bambu-kontot.
"""
import base64
import http.cookies
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from . import config, db

API = {'global': 'https://api.bambulab.com', 'china': 'https://api.bambulab.cn'}
WEB = {'global': 'https://bambulab.com', 'china': 'https://bambulab.cn'}
COLOR_DB_URL = ('https://raw.githubusercontent.com/bambulab/BambuStudio/master/'
                'resources/profiles/BBL/filament/filaments_color_codes.json')
COLOR_DB_MAX_AGE = 7 * 86400
TOKEN_LIFETIME = 90 * 86400
PAGE = 100
MAX_PAGES = 50
UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36'
_ALLOWED = {'api.bambulab.com', 'api.bambulab.cn', 'bambulab.com', 'bambulab.cn', 'raw.githubusercontent.com'}

# Endast för automatiska tester: peka alla Bambu-anrop mot en lokal låtsasserver.
_TEST_BASE = __import__('os').environ.get('KATALOG_BAMBU_TEST_BASE', '')
if _TEST_BASE.startswith('http://127.0.0.1:'):
    API = {'global': _TEST_BASE, 'china': _TEST_BASE}
    WEB = {'global': _TEST_BASE, 'china': _TEST_BASE}
    COLOR_DB_URL = _TEST_BASE + '/colors.json'


class BambuError(Exception):
    """Fel som kan visas för användaren (på svenska)."""


class TokenExpired(BambuError):
    pass


class NeedCode(BambuError):
    pass


class NeedTfa(BambuError):
    def __init__(self, tfa_key):
        super().__init__('Tvåstegsverifiering krävs')
        self.tfa_key = tfa_key


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def _request(method, url, body=None, token=None, headers=None, max_bytes=8_000_000):
    host = urllib.parse.urlsplit(url).hostname
    if not (url.startswith('https://') and host in _ALLOWED) and not (_TEST_BASE and url.startswith(_TEST_BASE)):
        raise BambuError('Otillåten adress.')
    h = {'User-Agent': UA, 'Accept': 'application/json', 'Accept-Language': 'sv-SE,sv;q=0.9,en;q=0.5'}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h['Content-Type'] = 'application/json'
    if token:
        h['Authorization'] = f'Bearer {token}'
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with _opener.open(req, timeout=25) as r:
            return r.status, r.headers, r.read(max_bytes)
    except urllib.error.HTTPError as e:
        raw = e.read(65536)
        if e.code in (403, 429, 503) and b'cloudflare' in raw.lower():
            raise BambuError('Bambu Labs bot-skydd (Cloudflare) blockerade anropet. Försök igen senare.') from None
        return e.code, e.headers, raw
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise BambuError(f'Kunde inte nå Bambu Lab ({getattr(e, "reason", e)}).') from None


def _json(raw):
    try:
        return json.loads(raw or b'{}')
    except ValueError:
        raise BambuError('Oväntat svar från Bambu Lab.') from None


def token_expiry(token):
    """Läser utgångstiden ur JWT-token (utan att verifiera den); annars ~90 dagar framåt."""
    try:
        payload = token.split('.')[1]
        payload += '=' * (-len(payload) % 4)
        exp = json.loads(base64.urlsafe_b64decode(payload)).get('exp')
        if isinstance(exp, (int, float)) and exp > time.time():
            return float(exp)
    except (IndexError, ValueError, AttributeError):
        pass
    return time.time() + TOKEN_LIFETIME


# ---------------------------------------------------------------- inloggning
def login(account, password, region='global'):
    """Returnerar token, eller kastar NeedCode/NeedTfa/BambuError. Lösenordet sparas aldrig."""
    status, _, raw = _request('POST', API[region] + '/v1/user-service/user/login',
                              {'account': account, 'password': password, 'apiError': ''})
    data = _json(raw)
    if status == 200 and data.get('accessToken'):
        return data['accessToken']
    if status == 200 and data.get('loginType') == 'verifyCode':
        raise NeedCode('En kod har skickats till din e-post.')
    if status == 200 and data.get('loginType') == 'tfa':
        raise NeedTfa(data.get('tfaKey', ''))
    if status in (400, 401, 403):
        raise BambuError('Bambu Lab godkände inte e-post/lösenord.')
    raise BambuError(f'Inloggningen misslyckades (HTTP {status}).')


def request_code(account, region='global'):
    if '@' in account:
        url, body = '/v1/user-service/user/sendemail/code', {'email': account, 'type': 'codeLogin'}
    else:
        url, body = '/v1/user-service/user/sendsmscode', {'phone': account, 'type': 'codeLogin'}
    status, _, _ = _request('POST', API[region] + url, body)
    if status != 200:
        raise BambuError(f'Kunde inte skicka en ny kod (HTTP {status}).')


def login_with_code(account, code, region='global'):
    status, _, raw = _request('POST', API[region] + '/v1/user-service/user/login', {'account': account, 'code': code})
    data = _json(raw)
    if status == 200 and data.get('accessToken'):
        return data['accessToken']
    raise BambuError('Koden godkändes inte. Kontrollera den eller begär en ny.')


def _cookie(headers, name):
    for value in headers.get_all('Set-Cookie') or []:
        c = http.cookies.SimpleCookie()
        try:
            c.load(value)
        except http.cookies.CookieError:
            continue
        if name in c:
            return c[name].value
    return None


def login_with_tfa(tfa_key, code, region='global'):
    status, headers, _ = _request('GET', WEB[region] + '/api/csrf')
    csrf = _cookie(headers, 'bbl_csrf_token')
    if status not in (200, 204) or not csrf:
        raise BambuError('Kunde inte starta tvåstegsverifieringen. Försök igen.')
    status, headers, _ = _request('POST', WEB[region] + '/api/sign-in/tfa', {'tfaKey': tfa_key, 'tfaCode': code},
                                  headers={'x-bbl-csrf-token': csrf, 'Cookie': f'bbl_csrf_token={csrf}'})
    token = _cookie(headers, 'token')
    if status == 200 and token:
        return token
    raise BambuError('Verifieringskoden godkändes inte.')


# ---------------------------------------------------------------- biblioteket
def get_spools(token, region='global'):
    spools, offset = [], 0
    for _ in range(MAX_PAGES):
        status, _, raw = _request('GET', API[region] + f'/v1/design-user-service/my/filament/v2?offset={offset}&limit={PAGE}',
                                  token=token)
        if status == 401:
            raise TokenExpired('Inloggningen hos Bambu Lab har gått ut. Koppla kontot igen.')
        if status != 200:
            raise BambuError(f'Kunde inte hämta filamentbiblioteket (HTTP {status}).')
        data = _json(raw)
        hits = [h for h in (data.get('hits') or []) if isinstance(h, dict)]
        spools += hits
        offset += len(hits)
        total = data.get('total')
        if not hits or not isinstance(total, int) or offset >= total:
            break
    return spools


# ---------------------------------------------------------------- färgkoder
def _hex6(value):
    v = str(value or '').strip().lstrip('#').upper()
    return '#' + v[:6] if re.fullmatch(r'[0-9A-F]{6}([0-9A-F]{2})?', v) else ''


def _color_key(colors):
    return tuple(sorted({h for h in (_hex6(c) for c in colors or []) if h}))


def color_db():
    """Bambu Studios publika färgkodstabell, hämtad vid behov och cachad en vecka i /data/state."""
    path = config.STATE_DIR / 'bambu_color_codes.json'
    cached = None
    try:
        cached = json.loads(path.read_text(encoding='utf-8'))
        if time.time() - path.stat().st_mtime < COLOR_DB_MAX_AGE:
            return cached
    except (OSError, ValueError):
        pass
    try:
        status, _, raw = _request('GET', COLOR_DB_URL)
        data = json.loads(raw).get('data') if status == 200 else None
        if isinstance(data, list) and data:
            config.STATE_DIR.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
            return data
    except (BambuError, ValueError, OSError):
        pass
    return cached or []


def _norm_type(s):
    return re.sub(r'[^a-z0-9+]', '', str(s or '').lower().replace('grey', 'gray'))


class SpoolMatcher:
    """Kopplar en spole i Bambu-biblioteket till en färg i katalogen."""

    def __init__(self, catalog, colors):
        self.catalog = catalog
        self.by_type = {}
        for d in catalog:
            self.by_type.setdefault(_norm_type(d['type']), []).append(d)
            # Kombinerade katalogtyper, t.ex. "TPU 85A / TPU 90A", nås även via delarna.
            if ' / ' in d['type']:
                for part in d['type'].split(' / '):
                    self.by_type.setdefault(_norm_type(part), []).append(d)
        self.code_by_id_color = {}
        self.code_by_type_color = {}
        for e in colors:
            if not isinstance(e, dict) or not e.get('fila_color_code'):
                continue
            key = _color_key(e.get('fila_color'))
            if e.get('fila_id'):
                self.code_by_id_color[(e['fila_id'], key)] = str(e['fila_color_code'])
            if e.get('fila_type'):
                self.code_by_type_color[(_norm_type(e['fila_type']), key)] = str(e['fila_color_code'])

    @staticmethod
    def key(d):
        return d['type'] + '|' + (d['code'] or d['name'])

    def match(self, spool):
        ftype = _norm_type(spool.get('filamentName'))
        colors = spool.get('colors') or [spool.get('color')]
        ckey = _color_key(colors)
        candidates = self.by_type.get(ftype, [])
        # 1) Färgkod via Bambu Studios tabell (filamentId + hex), exaktast.
        code = self.code_by_id_color.get((spool.get('filamentId'), ckey)) or self.code_by_type_color.get((ftype, ckey))
        if code:
            hits = [d for d in candidates if d['code'] == code] or [d for d in self.catalog if d['code'] == code]
            # Känd färgkod: antingen exakt träff eller ingen alls (gissa inte på en liknande färg).
            return hits[0] if len(hits) == 1 else None
        if not candidates or not ckey:
            return None
        # 2) Samma typ och exakt samma färg(er).
        hits = [d for d in candidates if tuple(sorted({h.upper() for h in d['hex']})) == ckey]
        if len(hits) == 1:
            return hits[0]
        # 3) Samma typ där spolens enda färg finns bland katalogens färger.
        if len(ckey) == 1:
            hits = [d for d in candidates if ckey[0] in {h.upper() for h in d['hex']}]
            if len(hits) == 1:
                return hits[0]
        return None


def summarize(spools, matcher):
    """-> (matched {nyckel: {grams, total, spools}}, unmatched [...]) för aktiva spolar med filament kvar."""
    matched, unmatched = {}, []
    for s in spools:
        if s.get('status') not in (0, None):        # 0 = aktiv; tomma/arkiverade räknas inte
            continue
        grams = s.get('netWeight')
        grams = int(grams) if isinstance(grams, (int, float)) else None
        if grams is not None and grams <= 0:
            continue
        total = s.get('totalNetWeight')
        total = int(total) if isinstance(total, (int, float)) and total > 0 else None
        d = matcher.match(s)
        if d:
            m = matched.setdefault(matcher.key(d), {'grams': 0, 'total': 0, 'spools': 0})
            m['spools'] += 1
            m['grams'] += grams or 0
            m['total'] += total or 0
        else:
            color = (_color_key(s.get('colors') or [s.get('color')]) or ('',))[0]
            unmatched.append({'name': ' '.join(str(x) for x in (s.get('filamentVendor'), s.get('filamentName')) if x)[:80],
                              'color': color, 'grams': grams})
    unmatched.sort(key=lambda u: u['name'])
    return matched, unmatched[:200]


def _catalog():
    return json.loads(config.CATALOG_PATH.read_text(encoding='utf-8'))


def sync_user(user_id):
    """Synkar en användare. Returnerar (ok, meddelande)."""
    link = db.get_bambu(user_id)
    if not link or not link['token']:
        return False, 'Inget Bambu Lab-konto är kopplat.'
    try:
        spools = get_spools(link['token'], link['region'])
        matched, unmatched = summarize(spools, SpoolMatcher(_catalog(), color_db()))
    except TokenExpired as e:
        db.save_bambu_sync(user_id, 'expired', str(e))
        return False, str(e)
    except BambuError as e:
        db.save_bambu_sync(user_id, 'error', str(e))
        return False, str(e)
    db.save_bambu_sync(user_id, 'ok', matched=matched, unmatched=unmatched)
    n = sum(m['spools'] for m in matched.values())
    total = n + len(unmatched)
    msg = f'{total} {"spole" if total == 1 else "spolar"} i biblioteket: {n} kopplade till {len(matched)} ' \
          f'{"färg" if len(matched) == 1 else "färger"} i katalogen'
    return True, msg + (f', {len(unmatched)} utan motsvarighet i katalogen.' if unmatched else '.')


def sync_all(log=print):
    for link in db.list_bambu():
        ok, msg = sync_user(link['user_id'])
        log(f'Bambu-synk användare {link["user_id"]}: {"ok" if ok else "fel"} – {msg}')

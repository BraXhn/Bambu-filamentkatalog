"""Gemensamt för prishämtarna: HTTP, JSON-LD och matchning mot katalogens färger."""
import html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

from .. import config


class FetchError(Exception):
    pass


ALLOWED_HOSTS = {'www.3djake.se', 'www.3dprima.com', 'www.tradera.com', 'eu.store.bambulab.com', 'www.ecb.europa.eu',
                 '3dverkstan.se', '3dexperten.se'}


def allowed_url(url):
    try:
        p = urllib.parse.urlsplit(str(url or ''))
        return p.scheme == 'https' and (p.hostname or '') in ALLOWED_HOSTS and not p.username and not p.password \
            and p.port in (None, 443)
    except ValueError:
        return False


class _SameSiteRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not allowed_url(newurl):
            raise FetchError(f'omdirigering till otillåten adress nekad: {newurl[:120]}')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_SameSiteRedirects)


def _inflate(raw, wbits, limit):
    d = zlib.decompressobj(wbits)
    out = d.decompress(raw, limit + 1)
    if len(out) > limit or d.unconsumed_tail:
        raise FetchError('uppackat svar är för stort')
    return out


class Http:
    """Artig HTTP-klient: fördröjning per värd, återförsök, gzip, storleksgräns."""

    def __init__(self, log, delay=None):
        self.log = log
        self.delay = config.HTTP_DELAY if delay is None else delay
        self._last = {}
        self.requests = 0

    def get(self, url, retries=2, max_bytes=6_000_000):
        if not allowed_url(url):
            raise FetchError(f'otillåten adress: {url[:120]}')
        host = urllib.parse.urlsplit(url).hostname or ''
        for attempt in range(retries + 1):
            wait = self._last.get(host, 0) + self.delay - time.time()
            if wait > 0:
                time.sleep(wait)
            self._last[host] = time.time()
            req = urllib.request.Request(url, headers={
                'User-Agent': config.USER_AGENT,
                'Accept': 'text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8',
                'Accept-Language': 'sv-SE,sv;q=0.9,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate',
            })
            try:
                self.requests += 1
                with _opener.open(req, timeout=30) as r:
                    raw = r.read(max_bytes + 1)
                    if len(raw) > max_bytes:
                        raise FetchError(f'för stort svar från {host}')
                    enc = (r.headers.get('Content-Encoding') or '').lower()
                    if enc == 'gzip':
                        raw = _inflate(raw, 16 + zlib.MAX_WBITS, max_bytes * 4)
                    elif enc == 'deflate':
                        raw = _inflate(raw, zlib.MAX_WBITS, max_bytes * 4)
                    if not allowed_url(r.geturl()):
                        raise FetchError('svaret kom från otillåten adress')
                    charset = r.headers.get_content_charset() or 'utf-8'
                    return r.geturl(), raw.decode(charset, errors='replace')
            except urllib.error.HTTPError as e:
                if e.code in (404, 410):
                    raise FetchError(f'HTTP {e.code}') from None
                err = f'HTTP {e.code}'
                if e.code == 429:
                    time.sleep(20 * (attempt + 1))
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                err = str(getattr(e, 'reason', e))
            if attempt < retries:
                time.sleep(3 * (attempt + 1))
        raise FetchError(err)


def json_ld(page, type_name='Product'):
    out = []
    for block in re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', page, re.S | re.I):
        try:
            data = json.loads(block)
        except ValueError:
            try:  # vissa butiker har radbrytningar/tabbar i strängar
                data = json.loads(re.sub(r'[\r\n\t]+', ' ', block))
            except ValueError:
                continue
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict) and item.get('@type') == type_name:
                out.append(item)
    return out


def text(s):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', s or ''))).strip()


class Matcher:
    """Kopplar en produkttitel till en färg i katalogen (samma regler som originalets prices.py)."""

    def __init__(self, catalog):
        self.data = catalog
        self.valid_codes = {d['code'] for d in catalog if d['code']}

    @staticmethod
    def norm(s):
        s = html.unescape(s).lower().replace('grey', 'gray').replace('silk+', 'silkplus').replace('filament', ' ') \
            .replace('bambu lab', ' ').replace('spule', 'spole')
        return re.sub(r'[^a-z0-9]+', ' ', s).strip()

    @staticmethod
    def key(d):
        return d['type'] + '|' + (d['code'] or d['name'])

    def match(self, title):
        codes = re.findall(r'\b\d{5}\b', title)
        found = [d for d in self.data if d['code'] and d['code'] in codes]
        if len(found) == 1:
            return found[0]
        norm = self.norm
        t = ' ' + norm(title) + ' '
        types = [d['type'] for d in self.data if ' ' + norm(d['type']) + ' ' in t]
        if not types:
            return None
        typ = max(types, key=lambda x: len(norm(x)))
        t = t.replace(' ' + norm(typ) + ' ', ' ')
        found = []
        for d in self.data:
            if d['type'] != typ:
                continue
            c = re.sub(r'\([^)]*\)', '', d['name']).strip()
            c = re.sub(r'^(Matte |ABS |Translucent |Glow )', '', c).replace(' Sparkle', '').replace(' Metallic', '')
            if typ == 'PLA Marble' and c == 'White Marble':
                c = 'White'
            if ' ' + norm(c) + ' ' in t:
                found.append((len(norm(c)), d))
        if not found:
            return None
        found.sort(key=lambda x: x[0], reverse=True)
        return found[0][1] if len(found) == 1 or found[0][0] > found[1][0] else None

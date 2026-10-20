"""Statiska filer från minnet: förkomprimerade, med ETag och korrekt innehållstyp."""
import gzip
import hashlib
import os
import threading
from pathlib import Path

from . import config

TYPES = {
    '.html': 'text/html; charset=utf-8', '.js': 'application/javascript; charset=utf-8',
    '.json': 'application/json; charset=utf-8', '.css': 'text/css; charset=utf-8',
    '.svg': 'image/svg+xml', '.png': 'image/png', '.ico': 'image/x-icon', '.txt': 'text/plain; charset=utf-8',
    '.webmanifest': 'application/manifest+json; charset=utf-8',
}
COMPRESSIBLE = {'.html', '.js', '.json', '.css', '.svg', '.txt', '.webmanifest'}


class File:
    __slots__ = ('body', 'gz', 'ctype', 'etag', 'mtime')

    def __init__(self, body, gz, ctype, mtime):
        self.body, self.gz, self.ctype, self.mtime = body, gz, ctype, mtime
        self.etag = '"' + hashlib.sha256(body).hexdigest()[:20] + '"'


def _load(path):
    path = Path(path)
    ext = path.suffix.lower()
    body = path.read_bytes()
    gz = None
    if ext in COMPRESSIBLE and len(body) > 512:
        pre = path.with_name(path.name + '.gz')
        try:
            if pre.stat().st_mtime >= path.stat().st_mtime:
                gz = pre.read_bytes()
        except OSError:
            pass
        if gz is None:
            gz = gzip.compress(body, 6, mtime=0)
        if len(gz) >= len(body):
            gz = None
    return File(body, gz, TYPES.get(ext, 'application/octet-stream'), path.stat().st_mtime)


class Store:
    """Filer i imagen laddas en gång; offers.json på datavolymen laddas om när den ändras."""

    def __init__(self):
        self._files = {}
        self._dynamic = {}
        self._lock = threading.Lock()

    def load(self):
        files = {}
        for base, prefix in ((config.SEED_DIR, '/'), (config.ASSETS_DIR, '/assets/')):
            if not base.is_dir():
                continue
            for p in base.iterdir():
                if p.is_file() and not p.name.endswith('.gz') and p.suffix.lower() in TYPES:
                    files[prefix + p.name] = _load(p)
        # Versionsmärk skripten i index.html (app.js?v=<hash>), så att webbläsare och Cloudflare
        # aldrig använder en gammal kopia efter en uppdatering.
        idx = files.get('/index.html')
        if idx:
            html = idx.body.decode('utf-8')
            for name in ('app.js', 'prices-ui.js'):
                f = files.get('/' + name)
                if f:
                    html = html.replace(f'src="{name}"', f'src="{name}?v={f.etag.strip(chr(34))[:12]}"')
            body = html.encode('utf-8')
            files['/index.html'] = File(body, gzip.compress(body, 9, mtime=0), idx.ctype, idx.mtime)
        files['/'] = files.get('/index.html')
        if '/assets/icon.svg' in files:
            files['/favicon.ico'] = files['/assets/icon.svg']
        self._files = {k: v for k, v in files.items() if v}
        return len(self._files)

    def get(self, path):
        if path == '/offers.json':
            return self._offers()
        return self._files.get(path)

    def _offers(self):
        live = config.WWW_DIR / 'offers.json'
        src = live if live.exists() else config.SEED_DIR / 'offers.json'
        try:
            mtime = os.stat(src).st_mtime
        except OSError:
            return None
        cached = self._dynamic.get(str(src))
        if cached and cached.mtime == mtime:
            return cached
        with self._lock:
            try:
                f = _load(src)
            except OSError:
                return cached
            self._dynamic = {str(src): f}
            return f


store = Store()

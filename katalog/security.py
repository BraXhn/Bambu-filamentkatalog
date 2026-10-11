"""Lösenordshashning, tokens och inloggningsbegränsning."""
import hashlib
import hmac
import re
import secrets
import threading
import time
import unicodedata

from . import config

# scrypt: N=2^15, r=8, p=1 ≈ 32 MB minne och ~50–100 ms per försök.
_N, _R, _P, _DKLEN = 2 ** 15, 8, 1, 32
_MAXMEM = 64 * 1024 * 1024


def _kdf(password, salt, n=_N, r=_R, p=_P):
    pw = unicodedata.normalize('NFKC', password).encode('utf-8')
    return hashlib.scrypt(pw, salt=salt, n=n, r=r, p=p, dklen=_DKLEN, maxmem=_MAXMEM)


def hash_password(password):
    salt = secrets.token_bytes(16)
    return f'scrypt${_N}${_R}${_P}${salt.hex()}${_kdf(password, salt).hex()}'


def verify_password(password, stored):
    try:
        algo, n, r, p, salt, digest = stored.split('$')
        if algo != 'scrypt':
            return False
        calc = _kdf(password, bytes.fromhex(salt), int(n), int(r), int(p))
        return hmac.compare_digest(calc, bytes.fromhex(digest))
    except (ValueError, TypeError):
        return False


# Används för okända användarnamn, så svarstiden inte avslöjar om kontot finns.
_DUMMY = None


def dummy_verify(password):
    global _DUMMY
    if _DUMMY is None:
        _DUMMY = hash_password(secrets.token_urlsafe(16))
    verify_password(password, _DUMMY)


def new_token():
    return secrets.token_urlsafe(32)


def token_hash(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def safe_equal(a, b):
    return hmac.compare_digest((a or '').encode(), (b or '').encode())


USERNAME_RE = re.compile(r'^[A-Za-z0-9._@-]{2,40}$')


def validate_username(username):
    if not USERNAME_RE.match(username or ''):
        return 'Användarnamnet måste vara 2–40 tecken: bokstäver a–z, siffror och . _ @ -'
    return None


_COMMON = {'password', 'password1', 'password123', 'qwerty123', 'qwertyuiop', '1234567890', '12345678910',
           'lösenord', 'losenord', 'losenord123', 'sommar2026', 'vinter2026', 'abc12345678', 'iloveyou',
           'administrator', 'bambulab', 'bambu12345', 'filament123', 'welcome123', 'välkommen', 'valkommen1'}


def validate_password(password, username=''):
    if len(password) < config.PASSWORD_MIN_LENGTH:
        return f'Lösenordet måste vara minst {config.PASSWORD_MIN_LENGTH} tecken.'
    if len(password) > 256:
        return 'Lösenordet får vara högst 256 tecken.'
    low = password.lower()
    if low in _COMMON or (username and username.lower() in low) or len(set(password)) < 4:
        return 'Lösenordet är för lätt att gissa. Välj en längre fras eller en blandning av tecken.'
    return None


def generate_password():
    """Lättläst slumpat lösenord, t.ex. för återställning från admin."""
    alphabet = 'abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789'
    groups = [''.join(secrets.choice(alphabet) for _ in range(4)) for _ in range(4)]
    return '-'.join(groups)


class RateLimiter:
    """Glidande fönster i minnet: max `limit` händelser per `window` sekunder per nyckel."""

    def __init__(self, limit, window):
        self.limit, self.window = limit, window
        self._hits = {}
        self._lock = threading.Lock()

    def _prune(self, key, now):
        hits = [t for t in self._hits.get(key, []) if now - t < self.window]
        if hits:
            self._hits[key] = hits
        else:
            self._hits.pop(key, None)
        return hits

    def blocked(self, key):
        with self._lock:
            return len(self._prune(key, time.time())) >= self.limit

    def hit(self, key):
        with self._lock:
            now = time.time()
            self._prune(key, now)
            self._hits.setdefault(key, []).append(now)
            if len(self._hits) > 50000:  # skydd mot minnesöverbelastning
                for k in [k for k, v in self._hits.items() if now - v[-1] >= self.window]:
                    del self._hits[k]
                while len(self._hits) > 40000:   # släng äldsta nycklarna (insättningsordning)
                    del self._hits[next(iter(self._hits))]

    def forgive(self, key):
        """Tar bort den senaste träffen (för lyckade försök)."""
        with self._lock:
            hits = self._hits.get(key)
            if hits:
                hits.pop()

    def reset(self, key):
        with self._lock:
            self._hits.pop(key, None)


# Max 20 misslyckade inloggningar per IP och kvart.
ip_failures = RateLimiter(20, 15 * 60)
# Spärr per konto+adress: 8 misslyckade försök per kvart.
pair_failures = RateLimiter(8, 15 * 60)
# Global kontospärr (15 min) bara vid storskalig gissning från många adresser.
ACCOUNT_MAX_FAILS = 50
ACCOUNT_LOCK_SECONDS = 15 * 60

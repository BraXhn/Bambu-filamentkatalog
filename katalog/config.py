"""Inställningar från miljövariabler. Alla har säkra standardvärden."""
import ipaddress
import os
from pathlib import Path


def _bool(name, default):
    v = os.environ.get(name)
    if v is None or v == '':
        return default
    return v.strip().lower() in ('1', 'true', 'yes', 'ja', 'on')


def _int(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


APP_DIR = Path(os.environ.get('KATALOG_APP_DIR', Path(__file__).resolve().parent.parent))
DATA_DIR = Path(os.environ.get('KATALOG_DATA_DIR', '/data'))
WWW_DIR = DATA_DIR / 'www'              # offers.json (+ .gz) som prisuppdateringen skriver
STATE_DIR = DATA_DIR / 'state'          # prishämtarens tillstånd (kända produktlänkar m.m.)
DB_PATH = DATA_DIR / 'katalog.db'
SEED_DIR = Path(os.environ.get('KATALOG_SEED_DIR', APP_DIR / 'www'))  # webbappen + inbyggd data från imagen
CATALOG_PATH = SEED_DIR / 'data.json'
ASSETS_DIR = APP_DIR / 'assets'

LISTEN_PORT = _int('PORT', 8080)
APP_PORT = _int('KATALOG_APP_PORT', 9000)
START_NGINX = _bool('KATALOG_START_NGINX', True)

SITE_NAME = os.environ.get('SITE_NAME', 'Bambu filamentkatalog')
TIMEZONE = os.environ.get('TZ', 'Europe/Stockholm') or 'Europe/Stockholm'
UPDATE_TIME = os.environ.get('PRICE_UPDATE_TIME', '01:00')   # HH:MM lokal tid, tom = av
UPDATE_ON_START = _bool('PRICE_UPDATE_ON_START', False)

# Sessioner
SESSION_IDLE_DAYS = _int('SESSION_IDLE_DAYS', 14)
SESSION_MAX_DAYS = _int('SESSION_MAX_DAYS', 60)
# auto = Secure-flagga när förfrågan kom via https (X-Forwarded-Proto från omvänd proxy)
COOKIE_SECURE = os.environ.get('COOKIE_SECURE', 'auto').lower()
PASSWORD_MIN_LENGTH = max(8, _int('PASSWORD_MIN_LENGTH', 10))
ALLOW_PUBLIC_SETUP = _bool('ALLOW_SETUP_WITHOUT_CODE', False)

# Första administratör (valfritt). Annars visas en engångskod i loggen för /setup.
ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME', '').strip()
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', '')

# Omvända proxyer vars X-Forwarded-For/-Proto litas på. Standard: bara loopback.
# Bakom t.ex. Nginx Proxy Manager/Traefik: ange proxyns adress eller nät, t.ex. 172.18.0.0/16.
_DEFAULT_PROXIES = '127.0.0.1/32,::1/128'
TRUSTED_PROXIES = []
for part in os.environ.get('TRUSTED_PROXIES', _DEFAULT_PROXIES).split(','):
    part = part.strip()
    if not part:
        continue
    try:
        TRUSTED_PROXIES.append(str(ipaddress.ip_network(part, strict=False)))
    except ValueError:
        print(f'[varning] Ogiltig TRUSTED_PROXIES-post ignoreras: {part}')

# Prishämtning
PRICE_SOURCES = [s.strip() for s in os.environ.get('PRICE_SOURCES', '3djake,3dprima,tradera').split(',') if s.strip()]
HTTP_DELAY = float(os.environ.get('PRICE_HTTP_DELAY', '1.2'))   # sekunder mellan anrop per sajt
TRADERA_QUERIES = [q.strip() for q in os.environ.get('TRADERA_QUERIES', 'bambu lab filament').split(';') if q.strip()]
TRADERA_MAX_PAGES = _int('TRADERA_MAX_PAGES', 8)
JAKE_MAX_PAGES = _int('JAKE_MAX_PAGES', 400)
USER_AGENT = os.environ.get(
    'PRICE_USER_AGENT',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36 BambuKatalog/1.0')

"""Gemensamma konstanter, gränser och hjälpfunktioner för webbservern och dess sidmoduler."""
import datetime
import http.cookies
import ipaddress
import os
import json
import re
import socket
import threading
import time
import urllib.parse
from pathlib import Path



from . import config, db, security  # noqa: F401

COOKIE = 'katalog_sid'
PRE_COOKIE = 'katalog_pre'
MAX_BODY = 64 * 1024
TOUCH_EVERY = 300
# scrypt kräver ~32 MB per beräkning; begränsa samtidiga beräkningar.
_hash_slots = threading.BoundedSemaphore(2)


def _hash(pw):
    with _hash_slots:
        return security.hash_password(pw)


def _verify(pw, stored):
    with _hash_slots:
        return security.verify_password(pw, stored)


def _dummy(pw):
    with _hash_slots:
        security.dummy_verify(pw)


bambu_attempts = security.RateLimiter(6, 15 * 60)
print_fetches = security.RateLimiter(6, 15 * 60)
api_token_attempts = security.RateLimiter(20, 15 * 60)    # felaktiga API-nycklar per IP       # "Hämta utskrifter" per användare
login_posts = security.RateLimiter(10, 60)          # högst 10 inloggningsförsök per minut och IP
MAX_CONNECTIONS = 128                                # samtidiga anslutningar totalt (en tråd var)
MAX_PER_IP = 16                                      # samtidiga anslutningar per klient (ej betrodd proxy)
HEADER_LIMIT = 16 * 1024                             # max storlek på förfrågningsrad + rubriker
REQUEST_DEADLINE = 10.0                              # sekunder för att skicka rubriker och kropp
_CL = re.compile(r'[0-9]{1,6}')
_proxy_like = set()   # adresser som skickar X-Forwarded-* utan att vara betrodda
_trusted = [ipaddress.ip_network(n) for n in config.TRUSTED_PROXIES]
CSP = ("default-src 'self'; img-src 'self' https://cdn.shopify.com data:; style-src 'self' 'unsafe-inline'; "
       "script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; "
       "frame-ancestors 'none'")
PUBLIC = {'/healthz', '/robots.txt', '/favicon.ico'}
APP_PATH = re.compile(r'^/(login|setup|logout|konto|admin|api|inkopslista|delad|onskelista|kostnad|utskrifter|kvitto|skrivare|projekt|samkop|material|spolar)(/|$)')


def _is_trusted(ip):
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(a in n for n in _trusted)


def _is_private(ip):
    try:
        return ipaddress.ip_address(ip).is_private
    except ValueError:
        return False
_names_cache = {}


def _catalog_names():
    """{katalognyckel: färgnamn} för kontosidan (cachas per filversion)."""
    try:
        mtime = config.CATALOG_PATH.stat().st_mtime
        if _names_cache.get('mtime') != mtime:
            data = json.loads(config.CATALOG_PATH.read_text(encoding='utf-8'))
            _names_cache.update(mtime=mtime, names={d['type'] + '|' + (d['code'] or d['name']): d['name'] for d in data})
        return _names_cache['names']
    except (OSError, ValueError):
        return {}


def _catalog():
    try:
        return json.loads(config.CATALOG_PATH.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return []


def _bambu_summary(user_id):
    link = db.get_bambu(user_id)
    if not link:
        return None
    try:
        matched = json.loads(link['matched'] or '{}')
    except ValueError:
        matched = {}
    return {'status': link['status'], 'lastSync': link['last_sync'], 'items': matched}


def safe_next(url):
    """Bara relativa sökvägar inom sajten (skydd mot öppen omdirigering)."""
    if not url or not url.startswith('/') or url.startswith('//') or '\\' in url or any(ord(c) < 32 for c in url):
        return '/'
    if re.match(r'^/(login|logout|setup|auth)(\b|/|\?|$)', url):
        return '/'
    # Location skrivs som latin-1: procentkoda allt utanför ASCII så att svaret inte går sönder.
    return urllib.parse.quote(url[:500], safe="/?=&#%:;,+@!$'()*~-._")


def _int(v, default):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


LOW_GRAMS_DEFAULT = 200

__all__ = ['APP_PATH', 'COOKIE', 'CSP', 'HEADER_LIMIT', 'LOW_GRAMS_DEFAULT', 'MAX_BODY', 'MAX_CONNECTIONS', 'MAX_PER_IP', 'PRE_COOKIE', 'PUBLIC', 'REQUEST_DEADLINE', 'TOUCH_EVERY', '_CL', '_bambu_summary', '_catalog', '_catalog_names', '_dummy', '_hash', '_hash_slots', '_int', '_is_private', '_is_trusted', '_names_cache', '_proxy_like', '_trusted', '_verify', 'api_token_attempts', 'bambu_attempts', 'login_posts', 'print_fetches', 'safe_next']

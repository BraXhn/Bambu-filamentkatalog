"""JSON-API för webbappen."""
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



from .. import alerts, backup, bambu, cart, config, costcalc, db, energy, history, homeassistant, newcolors, pages, printers, printlog, scheduler, security  # noqa: F401
from ..webcommon import *  # noqa: F401,F403


class ApiRoutes:
    def api_new(self):
        u = self.user()
        if not u or u['must_change_pw']:
            return self.json(401, {'error': 'inte inloggad'})
        sm = newcolors.summary()
        keep = ('code', 'type', 'name', 'hex', 'first')
        self.json(200, {'recent': sm['recent'], 'since': sm['since'], 'days': newcolors.NEW_DAYS,
                        'upcoming': [{k: v.get(k) for k in keep} for v in sm['upcoming']],
                        'missing': [{k: v.get(k) for k in keep} for v in sm['missing']]})

    def api_skus(self):
        u = self.user()
        if not u or u['must_change_pw']:
            return self.json(401, {'error': 'inte inloggad'})
        try:
            skus = json.loads((Path(__file__).parent.parent / 'prices' / 'bambu_skus.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            skus = {}
        self.json(200, {'skus': skus})

    def api_tibber_live(self):
        u = self.user()
        if not u or u['must_change_pw']:
            return self.json(401, {'error': 'inte inloggad'})
        token = db.get_pref(u['id'], 'tibber_token')
        if not token:
            return self.json(404, {'error': 'Tibber är inte kopplat'})
        try:
            live = energy.tibber_live(token)
        except energy.EnergyError as ex:
            return self.json(502, {'error': str(ex)})
        self.json(200, {'live': live})

    # ---------- API ----------
    def api_me(self):
        u = self.user()
        if not u:
            return self.json(401, {'error': 'inte inloggad'})
        self.json(200, {'username': u['username'], 'isAdmin': u['is_admin'], 'csrf': self.session()['csrf'],
                        'prefs': {'lowGrams': _int(db.get_pref(u['id'], 'low_grams'), LOW_GRAMS_DEFAULT)}})

    def api_owned_get(self):
        u = self.user()
        if not u or u['must_change_pw']:
            return self.json(401, {'error': 'inte inloggad'})
        items = db.get_owned(u['id'])
        bs = _bambu_summary(u['id'])
        from .. import spools as spoolmod
        left = spoolmod.available(u['id'])
        try:
            spool = json.loads(db.get_pref(u['id'], 'spoolman_matched') or '{}')
        except ValueError:
            spool = {}
        ams = {}
        if db.list_printers(u['id']):
            for p in self._printer_status(u['id'], with_prices=False):
                for t in p['trays']:
                    if t['key']:
                        ams.setdefault(t['key'], []).append(f"{p['name']} · {t['slot']}")
        own_spools = spoolmod.summary(u['id'])
        self.json(200, {'items': items or [], 'saved': items is not None, 'bambu': bs, 'spoolman': spool, 'ams': ams, 'spools': own_spools,
                        'forecast': printlog.forecast(u['id'], left)})

    def api_owned_put(self):
        u = self.user()
        if not u or u['must_change_pw']:
            return self.json(401, {'error': 'inte inloggad'})
        if not self.check_csrf():
            return self.json(403, {'error': 'ogiltig token'})
        if not (self.headers.get('Content-Type') or '').startswith('application/json'):
            return self.json(415, {'error': 'JSON krävs'})
        if getattr(self, '_cl', 0) > MAX_BODY:
            return self.json(413, {'error': 'för stor'})
        try:
            data = json.loads(self.read_body(MAX_BODY))
            items = data['items']
            if not isinstance(items, list) or len(items) > 5000 or \
                    not all(isinstance(x, str) and 0 < len(x) <= 120 for x in items):
                raise ValueError
        except (ValueError, KeyError, TypeError):
            return self.json(400, {'error': 'ogiltig data'})
        items = sorted(set(items))
        db.set_owned(u['id'], items)
        self.json(200, {'items': items, 'saved': True})

    def _json_body(self, limit=MAX_BODY):
        """Gemensam kontroll för JSON-API:er som ändrar något. Returnerar (användare, data) eller (None, None)."""
        u = self.user()
        if not u or u['must_change_pw']:
            self.json(401, {'error': 'inte inloggad'})
            return None, None
        if not self.check_csrf():
            self.json(403, {'error': 'ogiltig token'})
            return None, None
        if not (self.headers.get('Content-Type') or '').startswith('application/json'):
            self.json(415, {'error': 'JSON krävs'})
            return None, None
        if getattr(self, '_cl', 0) > limit:
            self.json(413, {'error': 'för stor'})
            return None, None
        try:
            data = json.loads(self.read_body(limit))
            if not isinstance(data, dict):
                raise ValueError
        except ValueError:
            self.json(400, {'error': 'ogiltig data'})
            return None, None
        return u, data

    def api_match(self):
        """Slicerns filament (Bambu filament-id, typ, färg) -> katalogfärg, med samma matchning som Bambu-synken."""
        u, data = self._json_body(16 * 1024)
        if not u:
            return
        items = data.get('items')
        if not isinstance(items, list) or len(items) > 16:
            return self.json(400, {'error': 'ogiltig data'})
        matcher = bambu.SpoolMatcher(_catalog(), bambu.color_db())
        out = []
        for it in items:
            if not isinstance(it, dict):
                return self.json(400, {'error': 'ogiltig data'})
            spool = {'filamentName': str(it.get('type') or '')[:40], 'filamentId': str(it.get('id') or '')[:20],
                     'color': str(it.get('color') or '')[:12]}
            d = matcher.match(spool)
            out.append(matcher.key(d) if d else None)
        self.json(200, {'keys': out})

    def api_history(self):
        u = self.user()
        if not u or u['must_change_pw']:
            return self.json(401, {'error': 'inte inloggad'})
        key = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get('key', [''])[0]
        if key not in _catalog_names():
            return self.json(400, {'error': 'okänd färg'})
        offers, _ = cart.load_offers()
        from .. import season
        tips = [t for t in (season.advice(key), season.calendar_hint(datetime.date.today())) if t]
        self.json(200, {'key': key, 'series': history.series(key, offers), 'tips': tips})

    def api_materials(self):
        from .. import materials
        u = self.user()
        if not u or u['must_change_pw']:
            return self.json(401, {'error': 'inte inloggad'})
        typ = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get('type', [''])[0]
        info = materials.for_type(typ)
        if not info:
            return self.json(404, {'error': 'okänd typ'})
        self.json(200, dict(info, nozzle=list(info['nozzle']), bed=list(info['bed']), dry=list(info['dry'])))

    def get_materials(self):
        from .. import materials
        u = self.user()
        types = {k.split('|')[0] for k in _catalog_names()}
        self.send(200, pages.materials_page(u, self.session()['csrf'], materials.all_types(types)))

"""Skrivare."""
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


class PrintersRoutes:
    # ---------- skrivare ----------
    def _printer_status(self, uid, with_prices=True):
        catalog = _catalog()
        matcher = bambu.SpoolMatcher(catalog, bambu.color_db())
        iv = []
        if with_prices:
            try:
                src = db.get_pref(uid, 'cost_source') or 'spot'
                extra = float(str(db.get_pref(uid, 'cost_extra') or '0').replace(',', '.'))
                if src == 'tibber':
                    iv = energy.tibber_prices(db.get_pref(uid, 'tibber_token'), extra)
                elif src == 'spot':
                    iv = energy.spot_prices(db.get_pref(uid, 'cost_area') or 'SE3', None, (db.get_pref(uid, 'cost_vat') or '1') == '1', extra)
                else:
                    fixed = float(str(db.get_pref(uid, 'cost_fixed') or '2').replace(',', '.'))
                    iv = [(datetime.datetime(2000, 1, 1, tzinfo=energy.TZ), datetime.datetime(2100, 1, 1, tzinfo=energy.TZ), fixed + extra)]
            except (energy.EnergyError, ValueError):
                iv = []
        return printers.status(uid, matcher, iv)

    def get_printers(self, kind='', msg='', code=200):
        u = self.user()
        link = db.get_bambu(u['id'])
        self.send(code, pages.printers_page(u, self.session()['csrf'], self._printer_status(u['id']), _catalog_names(),
                                            bool(link and link['token']), kind, msg))

    def get_printer_cards(self):
        u = self.user()
        self.send(200, pages.printer_cards(self._printer_status(u['id']), _catalog_names(), self.session()['csrf']), cache='no-store')

    def api_printers(self):
        u = self.api_user()
        if not u:
            return self.json(401, {'error': 'inte inloggad'})
        self.json(200, {'printers': self._printer_status(u['id'])})

    def printers_post(self, path):
        f = self.form()
        if not self.check_csrf(f):
            return self.forbidden()
        uid = self.user()['id']
        if path == '/skrivare/hamta':
            link = db.get_bambu(uid)
            if not link or not link['token']:
                return self.get_printers('err', 'Koppla ditt Bambu Lab-konto under Mitt konto först.', 400)
            try:
                devices = bambu.get_devices(link['token'], link['region'])
            except bambu.BambuError as ex:
                return self.get_printers('err', str(ex), 502)
            known = {p['serial'] for p in db.list_printers(uid)}
            for d in devices:
                db.save_printer(uid, d['serial'], d['name'], d['model'], 'cloud' if d['serial'] not in known else
                                next(p['mode'] for p in db.list_printers(uid) if p['serial'] == d['serial']),
                                None, d['access_code'] or None)
            for p in db.list_printers(uid):
                printers.start(p['id'])
            return self.get_printers('ok', f'{len(devices)} {"skrivare" if len(devices) == 1 else "skrivare"} hittades på Bambu Lab-kontot.'
                                     if devices else 'Inga skrivare är kopplade till Bambu Lab-kontot.')
        if path == '/skrivare/ny':
            name, serial = f.get('name', '').strip()[:60], f.get('serial', '').strip().upper()
            host, code = f.get('host', '').strip(), f.get('code', '').strip()
            err = self._check_lan(serial, host, code)
            if err:
                return self.get_printers('err', err, 400)
            db.save_printer(uid, serial, name or serial, None, 'lan', host, code)
            p = next(p for p in db.list_printers(uid) if p['serial'] == serial)
            printers.start(p['id'])
            return self.get_printers('ok', f'{name or serial} är tillagd. Statusen visas när skrivaren svarar.')
        m = re.fullmatch(r'/skrivare/(\d+)/(andra|ta-bort)', path)
        if not m or not db.get_printer(uid, int(m.group(1))):
            return self.not_found()
        pid = int(m.group(1))
        if m.group(2) == 'ta-bort':
            printers.stop_one(pid)
            db.delete_printer(uid, pid)
            return self.get_printers('ok', 'Skrivaren är borttagen.')
        row = db.get_printer(uid, pid)
        mode = f.get('mode') if f.get('mode') in ('cloud', 'lan') else row['mode']
        host = f.get('host', '').strip() or row['host']
        code = f.get('code', '').strip() or row['access_code']
        if mode == 'lan':
            err = self._check_lan(row['serial'], host, code)
            if err:
                return self.get_printers('err', err, 400)
        watts = f.get('watts', '').strip()
        db.update_printer(uid, pid, mode=mode, host=host, access_code=code,
                          watts=int(watts) if watts.isdigit() and 10 <= int(watts) <= 3000 else row['watts'])
        printers.start(pid)
        return self.get_printers('ok', 'Skrivaren är uppdaterad.')

    @staticmethod
    def _check_lan(serial, host, code):
        if not re.fullmatch(r'[A-Z0-9]{6,32}', serial):
            return 'Serienumret ska bestå av bokstäver och siffror (finns på skrivarens skärm och i Bambu Handy).'
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            return 'Ange skrivarens IP-adress i det lokala nätet, t.ex. 192.168.1.50.'
        if not (ip.is_private or (os.environ.get('KATALOG_MQTT_TEST_PLAIN') == '1' and ip.is_loopback)) or ip.is_loopback and \
                os.environ.get('KATALOG_MQTT_TEST_PLAIN') != '1':
            return 'Bara adresser i det lokala nätet tillåts.'
        if not re.fullmatch(r'[A-Za-z0-9]{6,16}', code):
            return 'Åtkomstkoden (8 tecken) finns i skrivarens nätverksinställningar.'
        return None

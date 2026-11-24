"""Inloggning, installation, konto, API-nycklar och svar för genvägar."""
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


class AccountRoutes:
    # ---------- inloggning ----------
    def get_login(self):
        if db.user_count() == 0:
            return self.redirect('/setup')
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        nxt = safe_next(q.get('next', ['/'])[0])
        if self.user():
            return self.redirect(nxt)
        tok, ck = self.pre_csrf()
        info = 'Du är utloggad.' if q.get('ut') else ''
        self.send(200, pages.login(tok, nxt, info=info), headers=[('Set-Cookie', ck)])

    def post_login(self):
        f = self.form()
        nxt = safe_next(f.get('next', '/'))
        username = (f.get('username') or '').strip()[:40]
        password = f.get('password') or ''
        tok, ck = self.pre_csrf()

        def fail(msg, code=400):
            self.send(code, pages.login(tok, nxt, error=msg, username=username), headers=[('Set-Cookie', ck)])

        if not self.check_pre_csrf(f):
            return fail('Sidan hade gått ut. Försök igen.', 403)
        ip = self.client_ip
        generic = ('Fel användarnamn eller lösenord. Efter flera misslyckade försök spärras kontot '
                   'i 15 minuter; en administratör kan låsa upp det.')
        if security.ip_failures.blocked(ip):
            return fail('För många misslyckade försök från din adress. Vänta en stund och försök igen.', 429)
        # Räkna försöket innan den långsamma kontrollen, så samtidiga försök inte slinker förbi.
        security.ip_failures.hit(ip)
        user = db.get_user_by_name(username) if username else None
        now = time.time()
        pair = f'{username.lower()}|{ip}'
        # Spärr per konto+adress (8 fel/kvart) – en angripare kan inte låsa ute ägaren från andra adresser.
        # Global kontospärr bara vid storskalig gissning från många adresser (ACCOUNT_MAX_FAILS).
        # Många misslyckanden mot kontot från olika adresser: sakta ned i stället för att låsa ute ägaren.
        recent = security.account_failures.count(username.lower())
        if recent >= 20:
            time.sleep(min(5.0, recent / 20))
        if security.pair_failures.blocked(pair) or (user and user['locked_until'] > now):
            _dummy(password)
            print(f'[inloggning] spärrat försök mot "{username}" från {ip}', flush=True)
            return fail(generic, 401)
        ok = _verify(password, user['pw_hash']) if user else (_dummy(password) or False)
        if not ok:
            security.pair_failures.hit(pair)
            security.account_failures.hit(username.lower())
            if user:
                db.register_failure(user['id'], security.ACCOUNT_MAX_FAILS, security.ACCOUNT_LOCK_SECONDS, now)
            print(f'[inloggning] misslyckad för "{username}" från {ip}', flush=True)
            return fail(generic, 401)
        if not db.complete_login(user['id'], time.time()):
            return fail(generic, 401)  # kontot spärrades medan lösenordet kontrollerades
        security.pair_failures.reset(pair)
        security.ip_failures.forgive(ip)
        print(f'[inloggning] {user["username"]} loggade in från {ip}', flush=True)
        cookie = self.start_session(user)
        self.redirect('/konto' if user['must_change_pw'] else nxt,
                      headers=[('Set-Cookie', cookie), ('Set-Cookie', self.cookie_header(PRE_COOKIE, '', 0))])

    def post_logout(self):
        f = self.form()
        s = self.session()
        if s and not self.check_csrf(f):
            return self.redirect('/')  # utloggning från annan sajt ignoreras
        if s:
            db.delete_session(s['id'])
        # Töm webbläsarens lagring och offline-cache (markeringar, katalogdata) oavsett var man loggar ut.
        self.redirect('/login?ut=1', headers=[('Set-Cookie', self.cookie_header(COOKIE, '', 0)),
                                              ('Clear-Site-Data', '"cache", "storage"')])

    # ---------- första start ----------
    def get_setup(self):
        if db.user_count() > 0:
            return self.redirect('/login')
        tok, ck = self.pre_csrf()
        self.send(200, pages.setup(tok, need_code=not config.ALLOW_PUBLIC_SETUP), headers=[('Set-Cookie', ck)])

    def post_setup(self):
        f = self.form()
        if db.user_count() > 0:
            return self.redirect('/login')
        tok, ck = self.pre_csrf()

        def fail(msg, code=400):
            self.send(code, pages.setup(tok, msg, {'username': f.get('username', ''), 'code': f.get('code', '')},
                                        need_code=not config.ALLOW_PUBLIC_SETUP), headers=[('Set-Cookie', ck)])

        if not self.check_pre_csrf(f):
            return fail('Sidan hade gått ut. Försök igen.', 403)
        if security.ip_failures.blocked(self.client_ip):
            return fail('För många försök. Vänta en stund.', 429)
        if not config.ALLOW_PUBLIC_SETUP:
            code = db.get_setting('setup_code')
            if not code or not security.safe_equal(f.get('code', '').strip().upper(), code):
                security.ip_failures.hit(self.client_ip)
                return fail('Fel installationskod. Koden står i containerns logg.', 403)
        username = (f.get('username') or '').strip()
        err = security.validate_username(username) or security.validate_password(f.get('password', ''), username)
        if not err and f.get('password') != f.get('password2'):
            err = 'Lösenorden matchar inte.'
        if err:
            return fail(err)
        with db._lock:
            if db.user_count() > 0:
                return self.redirect('/login')
            uid = db.create_user(username, _hash(f['password']), is_admin=True)
        db.delete_setting('setup_code')
        print(f'[konto] första administratören "{username}" skapades', flush=True)
        cookie = self.start_session(db.get_user(uid))
        self.redirect('/admin', headers=[('Set-Cookie', cookie)])

    # ---------- konto ----------
    def _sessions_count(self, uid):
        return db.conn().execute('SELECT COUNT(*) FROM sessions WHERE user_id=?', (uid,)).fetchone()[0]

    def get_account(self, kind='', msg='', code=200, bkind='', bmsg='', new_token=None, tkind='', tmsg=''):
        u = self.user()
        csrf = self.session()['csrf']
        section = ''
        if not u['must_change_pw']:
            section = pages.bambu_section(csrf, db.get_bambu(u['id']), db.get_bambu_pending(u['id']),
                                          _catalog_names(), bkind, bmsg)
            section += pages.api_tokens_section(csrf, db.list_api_tokens(u['id']), new_token, self._base_url(), tkind, tmsg)
            section += pages.data_section(csrf, db.get_pref(u['id'], 'integrations_ok') == '1')
        self.send(code, pages.account(u, csrf, self._sessions_count(u['id']), kind, msg,
                                      must_change=u['must_change_pw'], bambu=section))

    def account_export(self, as_zip):
        from .. import dataio
        u = self.user()
        stamp = time.strftime('%Y%m%d')
        if as_zip:
            return self.send(200, dataio.export_csv_zip(u, _catalog_names()), 'application/zip',
                             headers=[('Content-Disposition', f'attachment; filename="filamentkatalog-{stamp}.zip"')])
        body = json.dumps(dataio.export(u), ensure_ascii=False, indent=1)
        self.send(200, body, 'application/json; charset=utf-8',
                  headers=[('Content-Disposition', f'attachment; filename="filamentkatalog-{stamp}.json"')])

    def api_import(self):
        from .. import dataio
        u, data = self._json_body(4 * 1024 * 1024)
        if not u:
            return
        try:
            n = dataio.import_data(u['id'], data, _catalog_names())
        except ValueError as ex:
            return self.json(400, {'error': str(ex)})
        self.json(200, {'imported': n})

    def integrations_consent(self):
        f = self.form()
        if not self.check_csrf(f):
            return self.forbidden()
        on = f.get('on') == '1'
        db.set_pref(self.user()['id'], 'integrations_ok', '1' if on else None)
        return self.get_account(tkind='ok', tmsg='Administratören får nu välja din data för Home Assistant och Spoolman.' if on
                                else 'Din data skickas inte längre till Home Assistant eller Spoolman (om administratören valt den, stängs det av).')

    def _base_url(self):
        return config.PUBLIC_URL or f"{'https' if self.browser_https else 'http'}://{self.headers.get('Host') or 'localhost'}"

    def api_token_post(self, path):
        f = self.form()
        if not self.check_csrf(f):
            return self.forbidden()
        uid = self.user()['id']
        m = re.fullmatch(r'/konto/api-nyckel/(\d+)/ta-bort', path)
        if m:
            db.delete_api_token(uid, int(m.group(1)))
            return self.get_account(tkind='ok', tmsg='API-nyckeln är borttagen och slutar fungera direkt.')
        if len(db.list_api_tokens(uid)) >= 10:
            return self.get_account(tkind='err', tmsg='Högst 10 API-nycklar. Ta bort en du inte använder.', code=400)
        name = f.get('name', '').strip()[:60] or 'API-nyckel'
        token = 'kat_' + security.new_token()
        db.create_api_token(uid, name, security.token_hash(token))
        return self.get_account(new_token=token, tkind='ok', tmsg='Kopiera nyckeln nu. Den visas bara en gång.')

    # ---------- svar för genvägar och röstassistenter (API-nyckel eller inloggning) ----------
    def api_v1(self, path):
        u = self.api_user()
        if not u:
            return self.json(401, {'error': 'Ogiltig eller saknad API-nyckel'})
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        want_json = q.get('format', ['text'])[0] == 'json'
        try:
            data, text = self._v1_answer(u, path, q.get('q', [''])[0].strip()[:80])
        except LookupError:
            return self.json(404, {'error': 'okänd fråga'})
        if want_json:
            return self.json(200, dict(data, svar=text))
        self.send(200, text + '\n', 'text/plain; charset=utf-8')

    def _v1_answer(self, u, path, query):
        uid = u['id']
        names = _catalog_names()
        if path == '/api/v1/elpris':
            st = self._cost_settings(uid, {})
            extra = float((st['extra'] or '0').replace(',', '.') or 0)
            try:
                iv = (energy.tibber_prices(db.get_pref(uid, 'tibber_token'), extra) if st['source'] == 'tibber'
                      else energy.spot_prices(st['area'], None, st['vat'] == '1', extra))
            except energy.EnergyError:
                iv = []
            o = energy.price_outlook(iv)
            if not o:
                return {}, 'Elpriset kunde inte hämtas just nu.'
            text = f"Elpriset är {o['now']:.2f} kronor per kilowattimme just nu.".replace('.', ',', 1)
            if o['low']:
                text += f" Lägst de närmaste 12 timmarna: {o['low'][1]:.2f} kronor klockan {o['low'][0]:%H:%M}.".replace('.', ',', 1)
            return {'nu': o['now'], 'lagst': o['low'][1] if o['low'] else None}, text
        if path == '/api/v1/filament':
            have, _ = self._available(uid)
            words = [w for w in re.split(r'\s+', query.lower()) if w]
            sv = {'svart': 'black', 'vit': 'white', 'grå': 'gray', 'röd': 'red', 'blå': 'blue', 'grön': 'green', 'gul': 'yellow', 'orange': 'orange', 'rosa': 'pink', 'lila': 'purple', 'brun': 'brown'}
            words = [sv.get(w, w) for w in words]
            hits = [(k, g) for k, g in have.items() if all(w in (k.split('|')[0] + ' ' + names.get(k, '')).lower() for w in words)]
            hits.sort(key=lambda x: -x[1])
            if not hits:
                return {'traffar': []}, f'Hittade inget filament som matchar "{query}".' if query else 'Inga spolar är synkade.'
            fc = printlog.forecast(uid, have)
            parts = []
            for k, g in hits[:5]:
                days = fc.get(k, {}).get('days')
                parts.append(f"{k.split('|')[0]} {names.get(k, '')}: {g:.0f} gram" + (f', räcker cirka {days} dagar' if days is not None else ''))
            return {'traffar': [{'key': k, 'gram': g} for k, g in hits[:20]]}, 'Du har ' + '; '.join(parts) + '.'
        if path == '/api/v1/skrivare':
            st = self._printer_status(uid, with_prices=False)
            if not st:
                return {'skrivare': []}, 'Inga skrivare är tillagda.'
            parts = []
            for p in st:
                if p['state'] in ('RUNNING', 'PAUSE'):
                    parts.append(f"{p['name']} {p['stateText'].lower()} {p['job'] or ''}, {p['percent'] or 0:.0f} procent klart"
                                 + (f", {int(p['left'] or 0)} minuter kvar" if p['left'] is not None else ''))
                else:
                    parts.append(f"{p['name']}: {p['stateText'].lower()}")
            return {'skrivare': [{k: v for k, v in p.items() if k not in ('host', 'error')} for p in st]}, '. '.join(parts) + '.'
        if path == '/api/v1/sammanfattning':
            offers, _ = cart.user_offers(uid)
            hits = alerts.hits(uid, offers)
            low = [k for k, g in self._available(uid)[0].items() if g < int(db.get_pref(uid, 'low_grams') or 200)]
            n_cart = sum(r['qty'] for r in db.get_cart(uid))
            text = (f"{len(hits)} bevakningar har nått ditt pris. {len(low)} färger är snart slut. "
                    f"Inköpslistan har {n_cart} spolar.")
            return {'malpris': sorted(hits), 'snartSlut': low, 'inkopslista': n_cart}, text
        raise LookupError

    def post_password(self):
        f = self.form()
        if not self.check_csrf(f):
            return self.forbidden()
        u = self.user()
        row = db.get_user(u['id'])
        if security.ip_failures.blocked(self.client_ip):
            return self.get_account('err', 'För många försök. Vänta en stund.', 429)
        security.ip_failures.hit(self.client_ip)
        if not _verify(f.get('current', ''), row['pw_hash']):
            return self.get_account('err', 'Nuvarande lösenord stämmer inte.', 400)
        new = f.get('new', '')
        err = security.validate_password(new, u['username'])
        if not err and new != f.get('new2'):
            err = 'De nya lösenorden matchar inte.'
        if not err and _verify(new, row['pw_hash']):
            err = 'Välj ett annat lösenord än det nuvarande.'
        if err:
            return self.get_account('err', err, 400)
        db.update_user(u['id'], pw_hash=_hash(new), must_change_pw=0)
        db.delete_user_sessions(u['id'], except_id=self.session()['id'])
        self._sess = db.get_session(self.session()['id'])
        self.get_account('ok', 'Lösenordet är bytt. Andra enheter har loggats ut.')

    def post_logout_others(self):
        f = self.form()
        if not self.check_csrf(f):
            return self.forbidden()
        db.delete_user_sessions(self.user()['id'], except_id=self.session()['id'])
        self.get_account('ok', 'Övriga enheter är utloggade.')

    # ---------- Bambu Lab-koppling ----------
    def bambu_post(self, path):
        f = self.form()
        if not self.check_csrf(f):
            return self.forbidden()
        uid = self.user()['id']
        action = path.rsplit('/', 1)[-1]
        if action not in ('koppla', 'kod', 'ny-kod', 'synka', 'koppla-fran', 'avbryt'):
            return self.not_found()
        if action == 'koppla-fran':
            db.delete_bambu(uid)
            print(f'[bambu] användare {uid} kopplade från sitt Bambu-konto', flush=True)
            return self.get_account(bkind='ok', bmsg='Bambu Lab-kontot är frånkopplat. Synkade markeringar är borttagna.')
        if action == 'avbryt':
            db.clear_bambu_pending(uid)
            return self.redirect('/konto#bambu')
        # Skydda både Bambu-kontot och servern: högst 6 inloggnings-/synkförsök per kvart och användare.
        if bambu_attempts.blocked(uid):
            return self.get_account(bkind='err', bmsg='För många försök. Vänta en kvart och försök igen.', code=429)
        bambu_attempts.hit(uid)
        try:
            if action == 'synka':
                ok, msg = bambu.sync_user(uid)
                return self.get_account(bkind='ok' if ok else 'err', bmsg=msg)
            if action == 'koppla':
                account = (f.get('account') or '').strip()[:120]
                region = f.get('region') if f.get('region') in bambu.API else 'global'
                if not account or not f.get('password'):
                    return self.get_account(bkind='err', bmsg='Fyll i e-post och lösenord.', code=400)
                try:
                    token = bambu.login(account, f['password'], region)
                except bambu.NeedCode:
                    db.set_bambu_pending(uid, account, region, 'code')
                    return self.get_account()
                except bambu.NeedTfa as t:
                    db.set_bambu_pending(uid, account, region, 'tfa', t.tfa_key)
                    return self.get_account()
                return self._bambu_linked(uid, account, region, token)
            pending = db.get_bambu_pending(uid)
            if not pending:
                return self.get_account(bkind='err', bmsg='Inloggningen hos Bambu Lab hann gå ut. Börja om.', code=400)
            if action == 'ny-kod':
                bambu.request_code(pending['account'], pending['region'])
                db.set_bambu_pending(uid, pending['account'], pending['region'], 'code')
                return self.get_account(bkind='ok', bmsg='En ny kod har skickats.')
            code = re.sub(r'\s', '', f.get('code') or '')[:12]
            if pending['step'] == 'tfa':
                token = bambu.login_with_tfa(pending['tfa_key'], code, pending['region'])
            else:
                token = bambu.login_with_code(pending['account'], code, pending['region'])
            return self._bambu_linked(uid, pending['account'], pending['region'], token)
        except bambu.BambuError as ex:
            return self.get_account(bkind='err', bmsg=str(ex), code=400)

    def _bambu_linked(self, uid, account, region, token):
        db.save_bambu_token(uid, account, region, token, bambu.token_expiry(token))
        print(f'[bambu] användare {uid} kopplade Bambu-kontot', flush=True)
        ok, msg = bambu.sync_user(uid)
        return self.get_account(bkind='ok' if ok else 'err', bmsg=('Kontot är kopplat. ' if ok else 'Kontot är kopplat, men ') + msg)

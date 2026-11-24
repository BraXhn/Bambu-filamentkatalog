"""Administration."""
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


class AdminRoutes:
    # ---------- admin ----------
    def _admin_page(self, kind='', msg='', secret=None, code=200):
        u = self.user()
        runs = db.recent_runs(10)
        try:
            off = json.loads((config.WWW_DIR / 'offers.json').read_text(encoding='utf-8'))
            o = off.get('offers', [])
            offers_info = f"{len(o)} för {len({x['key'] for x in o})} färger"
        except (OSError, ValueError):
            offers_info = '—'
        self.send(code, pages.admin(u, self.session()['csrf'], db.list_users(), runs, scheduler.next_run_text(),
                                    offers_info, kind, msg, secret, backups=backup.list_backups(),
                                    integrations=self._integrations()))

    def _integrations(self):
        from .. import spoolman
        ha = homeassistant.settings()
        sm = spoolman.settings()
        try:
            st = json.loads(db.get_setting('spoolman_status') or 'null')
        except ValueError:
            st = None
        return {'ha': {**ha, 'password': bool(ha['password'])}, 'spoolman': {**sm, 'status': st},
                'users': [(u['id'], u['username']) for u in self._integration_users()]}

    def _integration_users(self):
        """Vems data som får skickas till Home Assistant/Spoolman: administratören själv och användare som samtyckt."""
        me = self.user()['id']
        return [u for u in db.list_users() if u['id'] == me or db.get_pref(u['id'], 'integrations_ok') == '1']

    def get_admin(self):
        if not self.user()['is_admin']:
            return self.forbidden()
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        msgs = {'start': ('ok', 'Prisuppdateringen har startat.'), 'busy': ('warn', 'En uppdatering pågår redan.')}
        kind, msg = msgs.get(q.get('m', [''])[0], ('', ''))
        self._admin_page(kind, msg)

    def get_backup_file(self, name):
        if not self.user()['is_admin']:
            return self.forbidden()
        p = backup.path_of(name)
        if not p:
            return self.not_found()
        self.send(200, p.read_bytes(), 'application/zip', headers=[('Content-Disposition', f'attachment; filename="{name}"')])

    def admin_backup(self):
        import io
        buf = io.BytesIO()
        backup.make_zip(buf)
        name = time.strftime('bambu-filamentkatalog-%Y%m%d-%H%M.zip')
        print(f'[admin] {self.user()["username"]} laddade ner en säkerhetskopia', flush=True)
        self.send(200, buf.getvalue(), 'application/zip',
                  headers=[('Content-Disposition', f'attachment; filename="{name}"')])

    def get_confirm_delete(self, uid):
        u = self.user()
        if not u['is_admin']:
            return self.forbidden()
        target = db.get_user(uid)
        if not target or target['id'] == u['id']:
            return self.redirect('/admin')
        self.send(200, pages.confirm_delete(u, self.session()['csrf'], target))

    def admin_post(self, path):
        u = self.user()
        f = self.form()
        if not u['is_admin'] or not self.check_csrf(f):
            return self.forbidden()
        if path == '/admin/priser/uppdatera':
            started = scheduler.trigger('manual')
            return self.redirect('/admin?m=' + ('start' if started else 'busy') + '#priser')
        if path == '/admin/anvandare':
            return self.admin_create(f)
        if path == '/admin/sakerhetskopia':
            return self.admin_backup()
        if path in ('/admin/homeassistant', '/admin/homeassistant/testa'):
            if path.endswith('/testa'):
                try:
                    n = homeassistant.publish_all(force=True)
                except Exception as ex:  # noqa: BLE001
                    return self._admin_page('err', f'Home Assistant: {ex}', code=502)
                return self._admin_page('ok', f'{n} sensorer skickade till Home Assistant.' if n else 'Home Assistant är inte inställt.')
            host = f.get('host', '').strip()
            if host and not re.fullmatch(r'[A-Za-z0-9.-]{1,253}', host):
                return self._admin_page('err', 'Ogiltig adress till MQTT-brokern.', code=400)
            port = f.get('port', '').strip() or '1883'
            uid_ = f.get('user_id', '').strip()
            allowed_ids = {str(u['id']) for u in self._integration_users()}
            if not port.isdigit() or not 1 <= int(port) <= 65535 or (uid_ and uid_ not in allowed_ids):
                return self._admin_page('err', 'Kontrollera port och användare (andras data kräver att de samtyckt under Mitt konto).', code=400)
            if host:
                err = homeassistant.check_host(host, int(port))
                if err:
                    return self._admin_page('err', err, code=400)
            for k, v in (('host', host), ('port', port), ('user', f.get('user', '').strip()[:100]), ('user_id', uid_),
                         ('prefix', (f.get('prefix', '').strip() or 'homeassistant')[:60])):
                if v:
                    db.set_setting('ha_' + k, v)
                else:
                    db.delete_setting('ha_' + k)
            if f.get('password'):
                db.set_setting('ha_password', f['password'][:200])
            if f.get('clear_password'):
                db.delete_setting('ha_password')
            return self._admin_page('ok', 'Home Assistant-inställningarna är sparade.' if host else 'Home Assistant är avstängt.')
        if path in ('/admin/spoolman', '/admin/spoolman/synka'):
            from .. import spoolman
            if path.endswith('/synka'):
                res = spoolman.sync()
                if not res:
                    return self._admin_page('err', 'Spoolman är inte inställt.', code=400)
                return self._admin_page('ok' if res[0] == 'ok' else 'err', res[1], code=200 if res[0] == 'ok' else 502)
            url = f.get('url', '').strip().rstrip('/')
            uid_ = f.get('user_id', '').strip()
            if url:
                err = spoolman.check_url(url)
                if err:
                    return self._admin_page('err', err, code=400)
                if uid_ not in {str(u['id']) for u in self._integration_users()}:
                    return self._admin_page('err', 'Välj vems katalog Spoolman ska synkas med (andras data kräver att de samtyckt under Mitt konto).', code=400)
                db.set_setting('spoolman_url', url)
                db.set_setting('spoolman_user_id', uid_)
                db.set_setting('spoolman_push', '1' if f.get('push') == '1' else '0')
            else:
                for k in ('spoolman_url', 'spoolman_user_id', 'spoolman_push', 'spoolman_status'):
                    db.delete_setting(k)
            return self._admin_page('ok', 'Spoolman-inställningarna är sparade.' if url else 'Spoolman-synken är avstängd.')
        if path == '/admin/sakerhetskopior/ny':
            name = backup.create()
            print(f'[admin] {u["username"]} skapade säkerhetskopian {name}', flush=True)
            return self._admin_page('ok', f'Säkerhetskopian {name} är sparad i /data/backups.')
        m = re.fullmatch(r'/admin/sakerhetskopior/([\w.-]+)/aterstall', path)
        if m:
            if f.get('confirm', '').strip().upper() != 'ÅTERSTÄLL':
                return self._admin_page('err', 'Skriv ÅTERSTÄLL i rutan för att bekräfta återställningen.', code=400)
            if not backup.path_of(m.group(1)):
                return self._admin_page('err', 'Säkerhetskopian finns inte.', code=404)
            backup.create()   # nuvarande läge sparas först, så att återställningen kan ångras
            backup.restore(m.group(1))
            cart._cache.clear()
            print(f'[admin] {u["username"]} återställde säkerhetskopian {m.group(1)}', flush=True)
            return self.redirect('/login')
        m = re.fullmatch(r'/admin/anvandare/(\d+)/(losenord|roll|las-upp|logga-ut|ta-bort)', path)
        if not m:
            return self.not_found()
        target = db.get_user(int(m.group(1)))
        action = m.group(2)
        if not target:
            return self._admin_page('err', 'Användaren finns inte.', code=404)
        if target['id'] == u['id']:
            return self._admin_page('err', 'Ändra ditt eget konto under Mitt konto.', code=400)
        name = target['username']
        if action == 'losenord':
            pw = security.generate_password()
            db.update_user(target['id'], pw_hash=_hash(pw), must_change_pw=1, failed_logins=0, locked_until=0)
            db.delete_user_sessions(target['id'])
            print(f'[admin] {u["username"]} återställde lösenordet för "{name}"', flush=True)
            return self._admin_page(secret=(f'Nytt lösenord för {name}', pw))
        if action == 'roll':
            if not db.toggle_admin_keep_one(target['id']):
                return self._admin_page('err', 'Det måste finnas minst en administratör.', code=400)
            print(f'[admin] {u["username"]} ändrade roll för "{name}"', flush=True)
            return self._admin_page('ok', f'{name} är nu {"vanlig användare" if target["is_admin"] else "administratör"}.')
        if action == 'las-upp':
            db.update_user(target['id'], failed_logins=0, locked_until=0)
            return self._admin_page('ok', f'{name} är upplåst.')
        if action == 'logga-ut':
            db.delete_user_sessions(target['id'])
            return self._admin_page('ok', f'{name} är utloggad från alla enheter.')
        if action == 'ta-bort':
            if not db.delete_user_keep_admin(target['id']):
                return self._admin_page('err', 'Det måste finnas minst en administratör.', code=400)
            print(f'[admin] {u["username"]} tog bort "{name}"', flush=True)
            return self._admin_page('ok', f'{name} är borttagen.')

    def admin_create(self, f):
        username = (f.get('username') or '').strip()
        err = security.validate_username(username)
        if not err and db.get_user_by_name(username):
            err = f'Användarnamnet {username} finns redan.'
        pw = f.get('password') or ''
        generated = not pw
        if generated:
            pw = security.generate_password()
        elif not err:
            err = security.validate_password(pw, username)
        if err:
            return self._admin_page('err', err, code=400)
        db.create_user(username, _hash(pw), is_admin=f.get('role') == 'admin', must_change_pw=True)
        print(f'[admin] {self.user()["username"]} skapade "{username}"', flush=True)
        if generated:
            return self._admin_page(secret=(f'{username} är tillagd. Lösenord:', pw))
        return self._admin_page('ok', f'{username} är tillagd. Användaren byter lösenord vid första inloggningen.')

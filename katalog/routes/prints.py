"""Utskriftslogg, kvitton och projekt."""
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


class PrintsRoutes:
    # ---------- utskriftslogg ----------
    def get_prints(self, kind='', msg='', code=200):
        u = self.user()
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        if not kind and q.get('m') == ['sparad']:
            kind, msg = 'ok', 'Utskriften är sparad i loggen.'
        link = db.get_bambu(u['id'])
        bambu_left = {k: v.get('grams') for k, v in (_bambu_summary(u['id']) or {}).get('items', {}).items()} if link else {}
        self.send(code, pages.prints_page(u, self.session()['csrf'], db.get_prints(u['id'], limit=300), printlog.stats(u['id']),
                                          printlog.forecast(u['id'], bambu_left), _catalog_names(), bool(link and link['token']),
                                          kind, msg, projects=db.list_projects(u['id'])))

    def prints_post(self, path):
        f = self.form()
        if not self.check_csrf(f):
            return self.forbidden()
        uid = self.user()['id']
        if path == '/utskrifter/hamta':
            if print_fetches.blocked(uid):
                return self.get_prints('err', 'För många försök. Vänta en stund och försök igen.', 429)
            print_fetches.hit(uid)
            try:
                n = printlog.import_bambu(uid)
            except bambu.BambuError as ex:
                return self.get_prints('err', str(ex), 502)
            return self.get_prints('ok', f'{n} nya utskrifter hämtade från Bambu Lab.' if n else 'Inga nya avslutade utskrifter hos Bambu Lab.')
        m = re.fullmatch(r'/utskrifter/(\d+)/ta-bort', path)
        if m:
            db.delete_print(uid, int(m.group(1)))
            return self.redirect('/utskrifter')
        m = re.fullmatch(r'/utskrifter/(\d+)/andra', path)
        if m:
            status = f.get('status') if f.get('status') in ('ok', 'failed', 'cancelled') else None
            reason = f.get('reason') if f.get('reason') in printlog.REASONS else None
            fields = {'status': status, 'reason': reason if status and status != 'ok' else None}
            if 'project' in f:
                pid = f.get('project', '')
                fields['project_id'] = int(pid) if pid.isdigit() and db.get_project(uid, int(pid)) else None
            db.update_print(uid, int(m.group(1)), **{k: v for k, v in fields.items() if k != 'status' or v})
            return self.redirect('/utskrifter#u' + m.group(1))
        return self.not_found()

    def api_receipt_put(self, purchase_id):
        u = self.user()
        if not u or u['must_change_pw']:
            return self.json(401, {'error': 'inte inloggad'})
        if not self.check_csrf():
            return self.json(403, {'error': 'ogiltig token'})
        ctype = (self.headers.get('Content-Type') or '').split(';')[0].strip().lower()
        ext = self.RECEIPT_TYPES.get(ctype)
        if not ext:
            return self.json(415, {'error': 'Bara JPEG, PNG, WebP eller PDF.'})
        if getattr(self, '_cl', 0) > self.RECEIPT_MAX:
            self.close_connection = True
            return self.json(413, {'error': 'Filen är för stor (högst 3 MB).'})
        rows = db.purchase_batch(u['id'], purchase_id)
        if not rows:
            return self.json(404, {'error': 'köpet finns inte'})
        data = self.read_body(self.RECEIPT_MAX)
        magic = {'jpg': (b'\xff\xd8\xff',), 'png': (b'\x89PNG',), 'webp': (b'RIFF',), 'pdf': (b'%PDF',)}[ext]
        if not data.startswith(magic):
            return self.json(415, {'error': 'Filen stämmer inte med sin typ.'})
        folder = config.RECEIPT_DIR / str(u['id'])
        folder.mkdir(parents=True, exist_ok=True)
        name = f'{rows[0]["id"]}.{ext}'
        for old in folder.glob(f'{rows[0]["id"]}.*'):
            old.unlink()
        tmp = folder / ('.tmp-' + name)
        tmp.write_bytes(data)
        os.replace(tmp, folder / name)
        db.set_receipt(u['id'], purchase_id, name)
        self.json(200, {'ok': True, 'url': f'/kvitto/{rows[0]["id"]}'})

    def get_receipt(self, purchase_id):
        u = self.user()
        rows = db.purchase_batch(u['id'], purchase_id)
        name = rows[0]['receipt'] if rows else None
        path = config.RECEIPT_DIR / str(u['id']) / name if name and re.fullmatch(r'\d+\.(jpg|png|webp|pdf)', name) else None
        if not path or not path.is_file():
            return self.not_found()
        ctype = {v: k for k, v in self.RECEIPT_TYPES.items()}[name.rsplit('.', 1)[1]]
        self.send(200, path.read_bytes(), ctype, cache='private, no-store',
                  headers=[('Content-Disposition', f'inline; filename="kvitto-{name}"')])

    def receipt_post(self, path):
        f = self.form()
        if not self.check_csrf(f):
            return self.forbidden()
        uid = self.user()['id']
        m = re.fullmatch(r'/kvitto/(\d+)/ta-bort', path)
        if not m:
            return self.not_found()
        rows = db.purchase_batch(uid, int(m.group(1)))
        if rows and rows[0]['receipt']:
            try:
                (config.RECEIPT_DIR / str(uid) / rows[0]['receipt']).unlink()
            except OSError:
                pass
            db.set_receipt(uid, int(m.group(1)), None)
        return self.redirect('/inkopslista#kophistorik')

    # ---------- projekt ----------
    def get_projects(self, kind='', msg='', code=200):
        u = self.user()
        projs = db.list_projects(u['id'])
        prints = db.get_prints(u['id'])
        sums = {}
        for r in prints:
            if r['project_id']:
                s_ = sums.setdefault(r['project_id'], {'n': 0, 'total': 0.0})
                s_['n'] += 1
                s_['total'] += r['total']
        self.send(code, pages.projects_page(u, self.session()['csrf'], projs, sums, kind, msg))

    def get_project(self, pid, csv_export=False, kind='', msg='', code=200):
        u = self.user()
        p = db.get_project(u['id'], pid)
        if not p:
            return self.not_found()
        prints = [r for r in db.get_prints(u['id']) if r['project_id'] == pid]
        names = _catalog_names()
        if csv_export:
            import csv
            import io
            buf = io.StringIO()
            w = csv.writer(buf, delimiter=';')
            w.writerow(['Datum', 'Utskrift', 'Resultat', 'Tid (h)', 'Filament (g)', 'Filament (kr)', 'El (kr)', 'Slitage (kr)', 'Totalt (kr)'])
            for r in sorted(prints, key=lambda r: r['ts']):
                g = sum(json.loads(r['grams'] or '{}').values())
                w.writerow([pages.fmt_time(r['ts']), r['name'], {'ok': 'Lyckades', 'failed': 'Misslyckades', 'cancelled': 'Avbruten'}.get(r['status'], r['status']),
                            f"{r['hours']:.2f}".replace('.', ','), f'{g:.1f}'.replace('.', ','), f"{r['filament']:.2f}".replace('.', ','),
                            f"{r['el']:.2f}".replace('.', ','), f"{r['wear']:.2f}".replace('.', ','), f"{r['total']:.2f}".replace('.', ',')])
            fname = re.sub(r'[^A-Za-z0-9_-]+', '-', p['name']).strip('-') or 'projekt'
            return self.send(200, '\ufeff' + buf.getvalue(), 'text/csv; charset=utf-8',
                             headers=[('Content-Disposition', f'attachment; filename="{fname}.csv"')])
        catalog = _catalog()
        owned = set(db.get_owned(u['id']) or [])
        have, _ = self._available(u['id'])
        owned |= set(have)
        colors_ = json.loads(p['colors'] or '[]')
        by_key = {d['type'] + '|' + (d['code'] or d['name']): d for d in catalog}
        offers, _ = cart.load_offers()
        palette = []
        from .. import colors as colormath
        for k in colors_:
            d = by_key.get(k)
            if not d:
                continue
            info = alerts.best(k, offers)
            similar = [] if k in owned else [(round(dist), x) for dist, x in colormath.nearest(d['hex'][0] if d['hex'] else '', catalog, 3,
                                                                                                 among=owned, exclude={k}) if dist < 25]
            palette.append({'key': k, 'item': d, 'owned': k in owned, 'grams': have.get(k), 'best': info['best'], 'similar': similar})
        self.send(code, pages.project_page(u, self.session()['csrf'], p, palette, prints, names, kind, msg))

    def projects_post(self, path):
        f = self.form()
        if not self.check_csrf(f):
            return self.forbidden()
        uid = self.user()['id']
        if path == '/projekt/ny':
            name = f.get('name', '').strip()
            if not name:
                return self.get_projects('err', 'Ge projektet ett namn.', 400)
            pid = db.create_project(uid, name, f.get('customer', '').strip())
            return self.redirect(f'/projekt/{pid}')
        m = re.fullmatch(r'/projekt/(\d+)/(farg|ta-bort-farg|ta-bort|andra|saknade)', path)
        if not m or not db.get_project(uid, int(m.group(1))):
            return self.not_found()
        pid, action = int(m.group(1)), m.group(2)
        p = db.get_project(uid, pid)
        cols = json.loads(p['colors'] or '[]')
        if action == 'farg':
            key = f.get('key', '')
            if key in _catalog_names() and key not in cols and len(cols) < 40:
                db.update_project(uid, pid, colors=cols + [key])
        elif action == 'ta-bort-farg':
            db.update_project(uid, pid, colors=[k for k in cols if k != f.get('key')])
        elif action == 'andra':
            db.update_project(uid, pid, name=(f.get('name', '').strip() or p['name'])[:80], customer=f.get('customer', '').strip()[:120] or None,
                              note=f.get('note', '').strip()[:2000] or None)
        elif action == 'ta-bort':
            db.delete_project(uid, pid)
            return self.redirect('/projekt')
        elif action == 'saknade':
            have, _ = self._available(uid)
            owned = set(db.get_owned(uid) or []) | set(have)
            in_cart = {r['key'] for r in db.get_cart(uid)}
            n = 0
            for k in cols:
                if k not in owned and k not in in_cart and db.set_cart_item(uid, k, 1, 'any'):
                    n += 1
            return self.get_project(pid, kind='ok', msg=f'{n} saknade färger lagda i inköpslistan.' if n else 'Inget saknas som inte redan finns i inköpslistan.')
        return self.redirect(f'/projekt/{pid}')

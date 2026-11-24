"""Spolar: egna fysiska spolar med vägning, öppnad/torkad, plats och torkpåminnelse."""
import datetime
import re

from .. import db, pages, spools
from ..webcommon import *  # noqa: F401,F403


class SpoolsRoutes:
    def get_spools(self, kind='', msg='', code=200):
        u = self.user()
        active = db.list_spools(u['id'])
        rows = [(s, spools.dry_status(s)) for s in active]
        self.send(code, pages.spools_page(u, self.session()['csrf'], rows, db.list_spools(u['id'], archived=True),
                                          _catalog_names(), spools.TARES, kind, msg))

    @staticmethod
    def _date(v):
        v = (v or '').strip()
        if not v:
            return None
        try:
            return datetime.date.fromisoformat(v).isoformat()
        except ValueError:
            raise ValueError('Datum anges som ÅÅÅÅ-MM-DD.') from None

    @staticmethod
    def _grams(v, what, hi=10000):
        v = (v or '').replace(',', '.').replace(' ', '').replace('g', '').strip()
        if not v:
            return None
        try:
            x = float(v)
        except ValueError:
            raise ValueError(f'{what} anges i gram.') from None
        if not 0 <= x <= hi:
            raise ValueError(f'{what} ligger utanför rimliga värden.')
        return x

    def spools_post(self, path):
        f = self.form()
        if not self.check_csrf(f):
            return self.forbidden()
        uid = self.user()['id']
        names = _catalog_names()
        try:
            if path == '/spolar/ny':
                key = f.get('key', '')
                if key and key not in names:
                    raise ValueError('Okänd färg.')
                name = f.get('name', '').strip()[:80] or (f"{key.split('|')[0]} {names[key]}" if key else '')
                if not name:
                    raise ValueError('Välj en färg ur katalogen eller skriv ett namn.')
                color = f.get('color', '').strip()
                if key:   # katalogfärg: använd katalogens färg, inte formulärets standardfärg
                    item = next((d for d in _catalog() if d['type'] + '|' + (d['code'] or d['name']) == key), None)
                    color = (item['hex'][0][:7] if item and item.get('hex') else '')
                if color and not re.fullmatch(r'#[0-9A-Fa-f]{6}', color):
                    color = ''
                tare_choice = f.get('tare', 'bambu')
                tare = next((g for k, _, g in spools.TARES if k == tare_choice), None)
                tare = self._grams(f.get('tare_g'), 'Tomvikt', 2000) if tare_choice == 'egen' else tare
                net = self._grams(f.get('net_g'), 'Vikt som ny', 10000) or 1000
                gross = self._grams(f.get('gross'), 'Vägd vikt', 12000)
                remaining = spools.from_weight(gross, tare or 0) if gross is not None else net
                count = max(1, min(20, int(f.get('count') or 1))) if str(f.get('count') or '1').isdigit() else 1
                material = (f.get('material', '').strip()[:30] or (key.split('|')[0] if key else '')) or None
                for _ in range(count):
                    db.add_spool(uid, key=key or None, name=name, material=material, color=color or None, net_g=int(net),
                                 tare_g=int(tare or 0), remaining_g=round(remaining, 1), opened=self._date(f.get('opened')),
                                 dried=self._date(f.get('dried')), location=f.get('location', '').strip()[:60] or None,
                                 note=f.get('note', '').strip()[:300] or None)
                return self.get_spools('ok', 'Spolen är tillagd.' if count == 1 else f'{count} spolar tillagda.')
            m = re.fullmatch(r'/spolar/(\d+)/(vag|andra|torkad|oppnad|arkivera|ateraktivera|ta-bort)', path)
            if not m or not db.get_spool(uid, int(m.group(1))):
                return self.not_found()
            sid, action = int(m.group(1)), m.group(2)
            s = db.get_spool(uid, sid)
            if action == 'vag':
                gross = self._grams(f.get('gross'), 'Vägd vikt', 12000)
                if gross is None:
                    raise ValueError('Ange vad spolen väger.')
                left = spools.from_weight(gross, s['tare_g'])
                db.update_spool(uid, sid, remaining_g=round(left, 1), opened=s['opened'] or spools.today().isoformat())
                return self.get_spools('ok', f'{s["name"]}: {left:.0f} g kvar.')
            if action == 'torkad':
                db.update_spool(uid, sid, dried=spools.today().isoformat())
            elif action == 'oppnad':
                db.update_spool(uid, sid, opened=spools.today().isoformat())
            elif action == 'arkivera':
                db.update_spool(uid, sid, archived=1)
            elif action == 'ateraktivera':
                db.update_spool(uid, sid, archived=0)
            elif action == 'ta-bort':
                db.delete_spool(uid, sid)
            elif action == 'andra':
                fields = {'location': f.get('location', '').strip()[:60] or None, 'note': f.get('note', '').strip()[:300] or None,
                          'opened': self._date(f.get('opened')), 'dried': self._date(f.get('dried'))}
                tare = self._grams(f.get('tare_g'), 'Tomvikt', 2000)
                if tare is not None:
                    fields['tare_g'] = int(tare)
                left = self._grams(f.get('remaining_g'), 'Gram kvar', 10000)
                if left is not None:
                    fields['remaining_g'] = left
                db.update_spool(uid, sid, **fields)
            return self.redirect('/spolar#s' + str(sid))
        except ValueError as ex:
            return self.get_spools('err', str(ex), 400)

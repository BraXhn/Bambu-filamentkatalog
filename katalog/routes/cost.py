"""Kostnadsräknare."""
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


class CostRoutes:
    def _cost_settings(self, uid, q):
        """Formulärets värden: från adressen (q) i första hand, annars användarens sparade standardvärden."""
        saved = {k: db.get_pref(uid, k) for k in self.COST_PREFS}

        def get(name, pref, default=''):
            v = q.get(name, [None])[-1]
            return (v if v is not None else (saved[pref] if saved[pref] is not None else default)).strip()
        return {
            'watts': get('w', 'cost_watts', '120'), 'source': get('src', 'cost_source', 'spot'), 'area': get('area', 'cost_area', 'SE3'),
            'extra': get('extra', 'cost_extra', ''), 'fixed': get('fixed', 'cost_fixed', '2,00'), 'vat': get('vat', 'cost_vat', '1'),
            'mode': get('mode', 'cost_mode', 'nu'), 'kg': get('kg', 'cost_kg', ''), 'wear': get('wear', 'cost_wear', ''),
        }

    def get_cost(self, kind='', msg='', code=200, q=None):
        u = self.user()
        if q is None:
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query, keep_blank_values=True)
        form, result, intervals, errors, st = self._cost_compute(u, q)
        has_tibber = bool(db.get_pref(u['id'], 'tibber_token'))
        live = None
        if st['source'] != 'fast':
            if not intervals:   # inget uträknat än: hämta elpriset ändå för "Just nu"
                try:
                    extra = float((st['extra'] or '0').replace(',', '.'))
                    intervals = (energy.tibber_prices(db.get_pref(u['id'], 'tibber_token'), extra) if st['source'] == 'tibber'
                                 else energy.spot_prices(st['area'], None, st['vat'] == '1', extra))
                except (energy.EnergyError, ValueError):
                    intervals = []
            live = energy.price_outlook(intervals)
            if not result:
                intervals = []
        self.send(code, pages.cost_page(u, self.session()['csrf'], form, _catalog_names(), result,
                                        intervals if st['source'] != 'fast' else [], errors, has_tibber, kind, msg, live=live,
                                        projects=db.list_projects(u['id'])))

    def _cost_compute(self, u, q):
        st = self._cost_settings(u['id'], q)
        names = _catalog_names()
        rows, errors = [], []

        def num(v, what, lo=0.0, hi=1e6):
            v = (v or '').replace(',', '.').replace(' ', '').strip()
            if not v:
                return None
            try:
                x = float(v)
            except ValueError:
                errors.append(f'{what} måste vara ett tal.')
                return None
            if not lo <= x <= hi:
                errors.append(f'{what} ligger utanför rimliga värden.')
                return None
            return x
        form = {'rows': [], 'h': q.get('h', [''])[0], 'm': q.get('m', [''])[0], 'start': q.get('start', [''])[0], **st}
        for i in range(1, costcalc.MAX_ROWS + 1):
            key, g = q.get(f'k{i}', [''])[0], q.get(f'g{i}', [''])[0]
            form['rows'].append((key, g))
            if key and key not in names:
                errors.append(f'Rad {i}: okänd färg.')
                continue
            grams = num(g, f'Rad {i}: gram', 0, 100_000)
            if grams:
                rows.append((key, grams))
        result, intervals, src_note = None, [], ''
        computing = bool(rows) or bool(form['h'] or form['m'])
        if computing:
            hours = (num(form['h'], 'Timmar', 0, 2000) or 0) + (num(form['m'], 'Minuter', 0, 59_999) or 0) / 60
            watts = num(st['watts'], 'Effekt', 1, 5000) or 0
            extra = num(st['extra'], 'Tillägg per kWh', 0, 50) or 0
            per_kg = num(st['kg'], 'Eget pris per kg', 0, 100_000)
            wear = num(st['wear'], 'Slitage per timme', 0, 10_000) or 0
            now = datetime.datetime.now(energy.TZ)
            start = now
            if form['start']:
                try:
                    start = datetime.datetime.fromisoformat(form['start']).replace(tzinfo=energy.TZ)
                except ValueError:
                    errors.append('Starttiden kunde inte läsas.')
            try:
                if st['source'] == 'tibber':
                    intervals = energy.tibber_prices(db.get_pref(u['id'], 'tibber_token'), extra)
                    src_note = 'Tibber (energipris inkl. skatt och moms' + (f' + {extra:.2f} kr/kWh tillägg'.replace('.', ',') if extra else '') + ')'
                elif st['source'] == 'spot':
                    intervals = energy.spot_prices(st['area'], now, st['vat'] == '1', extra)
                    src_note = (f"Spotpris {st['area']} från elprisetjustnu.se" + (' inkl. moms' if st['vat'] == '1' else ' utan moms') +
                                (f' + {extra:.2f} kr/kWh tillägg'.replace('.', ',') if extra else ''))
                else:
                    fixed = num(st['fixed'], 'Fast elpris', 0, 50) or 0
                    far = datetime.datetime(2100, 1, 1, tzinfo=energy.TZ)
                    intervals = [(datetime.datetime(2000, 1, 1, tzinfo=energy.TZ), far, fixed + extra)]
                    src_note = f'Fast pris {fixed + extra:.2f} kr/kWh'.replace('.', ',')
            except energy.EnergyError as ex:
                errors.append(f'Elpriset kunde inte hämtas: {ex}. Välj en annan källa eller fast pris.')
            offers, _ = cart.user_offers(u['id'])
            result = costcalc.calculate(rows, st['mode'], per_kg, hours, watts, intervals, start, wear, offers,
                                        db.last_paid(u['id']), now)
            result.update(start=start, hours=hours, watts=watts, source=src_note, now=now)
            result['shortage'] = self._shortage(u['id'], rows)
        form.update(project=q.get('project', [''])[-1][:10], printer=q.get('printer', [''])[-1][:20], name=q.get('name', [''])[-1][:120], markup=q.get('markup', [''])[-1], fail=q.get('fail', [''])[-1],
                    qvat=q.get('qvat', ['0'])[-1], rnd=q.get('rnd', ['1'])[-1], customer=q.get('customer', [''])[-1][:120])
        return form, result, intervals, errors, st

    def _available(self, uid):
        """Gram kvar per katalogfärg: Bambu-biblioteket och Spoolman (det största värdet per färg)."""
        from .. import spools
        link = db.get_bambu(uid)
        have = spools.available(uid)
        return have, bool(link) or bool(db.get_pref(uid, 'spoolman_matched')) or bool(db.list_spools(uid))

    def _shortage(self, uid, rows):
        """[(nyckel, behövs, finns)] för färger där det som finns kvar inte räcker till utskriften."""
        have, known = self._available(uid)
        if not known:
            return []
        need = {}
        for k, g in rows:
            if k:
                need[k] = need.get(k, 0) + g
        return [(k, g, have.get(k, 0)) for k, g in need.items() if g > have.get(k, 0)]

    def cost_post(self, path):
        f = self.form()
        if not self.check_csrf(f):
            return self.forbidden()
        uid = self.user()['id']
        if path == '/kostnad/rakna':
            q = {k: [v] for k, v in f.items() if k != 'csrf'}
            if f.get('do') == 'spara':
                self._save_cost_prefs(uid, f)
                return self.get_cost('ok', 'Uträknat. Inställningarna är sparade som standard.', q=q)
            if f.get('do') in ('logga', 'offert'):
                u = self.user()
                form, res, _, errors, _ = self._cost_compute(u, q)
                if errors or not res or not res['grams']:
                    return self.get_cost('err', 'Fyll i minst en färg med gram (och rätta eventuella fel) först.', 400, q=q)
                if f.get('do') == 'offert':
                    try:
                        quote = costcalc.quote(res, *(float((form[k] or '0').replace(',', '.')) for k in ('markup', 'fail')),
                                               form['qvat'] == '1', int(form['rnd'] or 1))
                    except ValueError:
                        return self.get_cost('err', 'Påslag och marginal anges i procent, t.ex. 30.', 400, q=q)
                    return self.send(200, pages.quote_page(u, self.session()['csrf'], form, res, quote, _catalog_names()))
                grams = {}
                for l in res['lines']:
                    grams[l['key']] = round(grams.get(l['key'], 0) + l['grams'], 2)
                printer = next((n for pid, n, _ in costcalc.PRINTERS if pid == form.get('printer')), None)
                new_id = db.add_print(uid, res['start'].timestamp(), form['name'] or 'Utskrift', printer, res['hours'], grams,
                                      res['filament'], res['el']['cost'], res['el']['kwh'], res['wear'], res['total'])
                proj = f.get('project', '')
                if new_id:
                    from .. import spools
                    spools.deduct(uid, grams)
                if new_id and proj.isdigit() and db.get_project(uid, int(proj)):
                    db.update_print(uid, new_id, project_id=int(proj))
                return self.redirect('/utskrifter?m=sparad')
            return self.get_cost(q=q)
        if path == '/kostnad/tibber':
            token = f.get('token', '').strip()
            if f.get('do') == 'ta-bort':
                db.set_pref(uid, 'tibber_token', None)
                if db.get_pref(uid, 'cost_source') == 'tibber':
                    db.set_pref(uid, 'cost_source', 'spot')
                return self.get_cost('ok', 'Tibber-kopplingen är borttagen.')
            if not re.fullmatch(r'[A-Za-z0-9_.-]{20,200}', token):
                return self.get_cost('err', 'Klistra in ditt Tibber-token (finns på developer.tibber.com under Your token).', 400)
            try:
                n = len(energy.check_tibber(token))
            except energy.EnergyError as ex:
                return self.get_cost('err', f'Tokenet fungerade inte: {ex}.', 400)
            db.set_pref(uid, 'tibber_token', token)
            db.set_pref(uid, 'cost_source', 'tibber')
            return self.get_cost('ok', f'Tibber är kopplat: {n} priser hämtade. Elpriset tas nu från Tibber.')
        return self.not_found()

    def _save_cost_prefs(self, uid, f):
        mapping = {'w': 'cost_watts', 'src': 'cost_source', 'area': 'cost_area', 'extra': 'cost_extra', 'fixed': 'cost_fixed',
                   'vat': 'cost_vat', 'mode': 'cost_mode', 'kg': 'cost_kg', 'wear': 'cost_wear'}
        allowed = {'src': set(costcalc.SOURCES), 'area': set(energy.AREAS), 'mode': set(costcalc.MODES), 'vat': {'0', '1'}}
        for field, pref in mapping.items():
            if field not in f:
                continue
            v = f.get(field, '').strip()[:20]
            if field in allowed and v not in allowed[field]:
                continue
            if field not in allowed and v and not re.fullmatch(r'[0-9]+([.,][0-9]+)?', v):
                continue
            db.set_pref(uid, pref, v or None)

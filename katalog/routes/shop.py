"""Inköpslista, önskelista, delning och samköp."""
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


class ShopRoutes:
    # ---------- samköp ----------
    def get_groupbuy(self, kind='', msg='', code=200, chosen=None):
        from .. import groupbuy
        u = self.user()
        parts = groupbuy.participants(u['id'])
        result = None
        if chosen:
            ids = [p['id'] for p in parts if p['id'] in chosen]
            if u['id'] not in ids:
                ids.insert(0, u['id'])
            offers, checked = cart.load_offers()
            together, people = groupbuy.plan(ids, offers, _catalog())
            result = {'plan': together, 'people': people, 'checked': checked, 'ids': ids}
        names = {p['id']: p['username'] for p in parts}
        self.send(code, pages.groupbuy_page(u, self.session()['csrf'], parts, names, result, _catalog_names(),
                                            db.get_pref(u['id'], 'samkop_ok') == '1', kind, msg))

    def groupbuy_post(self, path):
        f = self.form()
        if not self.check_csrf(f):
            return self.forbidden()
        uid = self.user()['id']
        if path == '/samkop/tillat':
            on = f.get('on') == '1'
            db.set_pref(uid, 'samkop_ok', '1' if on else None)
            return self.get_groupbuy('ok', 'Andra kan nu ta med din inköpslista i ett samköp.' if on else 'Din inköpslista tas inte längre med i andras samköp.')
        if path == '/samkop/rakna':
            raw = urllib.parse.parse_qs(self._last_form_raw, keep_blank_values=True).get('user', []) if hasattr(self, '_last_form_raw') else []
            chosen = {int(x) for x in raw if x.isdigit()}
            return self.get_groupbuy(chosen=chosen or {uid})
        return self.not_found()

    # ---------- inköpslista ----------
    def _cart_plans(self, uid):
        items = [(r['key'], r['qty'], r['pack']) for r in db.get_cart(uid)]
        offers, checked = cart.load_offers()
        catalog = _catalog()
        plan = cart.cached_plan(uid, items, offers, catalog, checked) if items else cart.plan([], offers, catalog)
        budget = _int(db.get_pref(uid, 'budget'), None)
        buy, wait = (cart.within_budget(items, offers, catalog, budget, base=plan) if budget and items else (plan, []))
        return items, plan, buy, wait, budget, checked

    def get_cart(self, kind='', msg='', code=200, share_url=None):
        u = self.user()
        items, plan, buy, wait, budget, checked = self._cart_plans(u['id'])
        self.send(code, pages.shopping_list(u, self.session()['csrf'], buy, items, _catalog_names(), checked, kind, msg,
                                            budget=budget, waiting=wait, full=plan, shared=bool(db.get_share(u['id'])),
                                            share_url=share_url, purchases=db.get_purchases(u['id'], 60)))

    def _mark_bought(self, uid, shop):
        """Butikens rader i den visade planen: räkna av från listan, lägg till under Jag har och spara i köphistoriken."""
        items, plan, buy, *_ = self._cart_plans(uid)
        sh = next((x for x in buy['shops'] if x['shop'] == shop), None)
        if not sh:
            return self.get_cart('err', 'Planen har ändrats sedan sidan visades. Kontrollera listan och försök igen.', 409)
        rows, bought = [], {}
        for l in sh['lines']:
            o = l['offer']
            rows.append((shop, l['key'], l['qty'], round(l.get('bulkPrice') or o['price']), o.get('pack'), o.get('url'),
                         o.get('weight') or 1000))
            bought[l['key']] = bought.get(l['key'], 0) + l['qty']
        for key, qty, pack in items:
            if key in bought:
                db.set_cart_item(uid, key, max(0, qty - bought[key]), pack)
        owned = set(db.get_owned(uid) or [])
        db.set_owned(uid, sorted(owned | set(bought)))
        db.add_purchases(uid, rows)
        n = sum(bought.values())
        return self.get_cart('ok', f'Markerat som köpt hos {shop}: {n} {"spole" if n == 1 else "spolar"}. '
                                   f'Färgerna är markerade under Jag har och borttagna ur listan.')

    def get_wish(self, kind='', msg='', code=200):
        from .. import season
        u = self.user()
        offers, checked = cart.load_offers()
        keys = db.get_wish(u['id'])
        al = db.get_alerts(u['id'])
        rows = []
        for k in keys:
            info = alerts.best(k, offers)
            low = [p[1] for s in history.series(k, offers) for p in s['points']]
            rows.append({'key': k, 'info': info, 'alert': al.get(k), 'hit': bool(al.get(k)) and alerts.is_hit(al[k], info),
                         'low90': min(low) if low else None, 'tip': season.advice(k)})
        self.send(code, pages.wish_page(u, self.session()['csrf'], rows, _catalog_names(), db.get_pref(u['id'], 'notify'),
                                        bool(config.NOTIFY_HOSTS), checked, kind, msg,
                                        notify_new=db.get_pref(u['id'], 'notify_new') == '1',
                                        calendar=season.calendar_hint(datetime.date.today())))

    def wish_post(self, path):
        f = self.form()
        if not self.check_csrf(f):
            return self.forbidden()
        uid = self.user()['id']
        if path == '/onskelista/bevaka':
            key = f.get('key', '')
            if key not in db.get_wish(uid):
                return self.get_wish('err', 'Färgen finns inte på önskelistan.', 400)

            def num(name, hi):
                raw = re.sub(r'[\s%kr]', '', f.get(name, ''))
                if not raw:
                    return None
                if not raw.isdigit() or not 0 < int(raw) <= hi:
                    raise ValueError
                return int(raw)
            try:
                target, disc = num('target', 100_000), num('discount', 90)
            except ValueError:
                return self.get_wish('err', 'Ange målpriset i hela kronor och rabatten i procent (1–90).', 400)
            db.set_alert(uid, key, target, disc)
            return self.redirect('/onskelista')
        if path == '/onskelista/ta-bort':
            key = f.get('key', '')
            db.set_wish(uid, [k for k in db.get_wish(uid) if k != key])
            db.set_alert(uid, key, None, None)
            return self.redirect('/onskelista')
        if path == '/onskelista/nyheter':
            on = f.get('on') == '1'
            db.set_pref(uid, 'notify_new', '1' if on else None)
            return self.get_wish('ok', 'Du aviseras när Bambu Lab får nya färger.' if on else 'Aviseringar om nya färger är avstängda.')
        if path == '/onskelista/avisering':
            if f.get('do') == 'ta-bort' or (not f.get('target', '').strip() and f.get('do') != 'generera'):
                db.set_pref(uid, 'notify', None)
                return self.get_wish('ok', 'Aviseringar är avstängda. Träffar visas fortfarande här och i katalogen.')
            raw = alerts.random_topic() if f.get('do') == 'generera' else f.get('target', '')
            target = alerts.parse_target(raw)
            if target and alerts.topic_too_short(target):
                return self.get_wish('err', f'Välj ett längre, svårgissat ämne (minst {alerts.MIN_PUBLIC_TOPIC} tecken) – ntfy.sh är öppet, '
                                            'så den som gissar ämnet kan läsa aviseringarna. Eller tryck Generera ämne.', 400)
            if not target:
                return self.get_wish('err', 'Ange ett ntfy-ämne (bokstäver, siffror, - och _) eller en adress hos en tillåten '
                                            f'ntfy-server ({", ".join(sorted(config.NOTIFY_HOSTS)) or "ingen"}).', 400)
            db.set_pref(uid, 'notify', target)
            if f.get('do') == 'testa':
                err = alerts.send(target, 'Test från filamentkatalogen', 'Aviseringar för din önskelista fungerar.')
                return self.get_wish('err' if err else 'ok', err or 'Testaviseringen är skickad. Syns den inte, kontrollera ämnet i ntfy-appen.')
            return self.get_wish('ok', 'Aviseringar sparade. Du får en avisering när en bevakning når ditt pris.')
        return self.not_found()

    def get_shared(self, token):
        uid = db.share_user(security.token_hash(token))
        owner = db.get_user(uid) if uid else None
        if not owner:
            return self.send(404, pages.error_page(404, 'Länken gäller inte längre', 'Be om en ny länk till inköpslistan.'))
        items = [(r['key'], r['qty'], r['pack']) for r in db.get_cart(uid)]
        offers, checked = cart.load_offers()
        plan = cart.cached_plan(uid, items, offers, _catalog(), checked) if items else None
        self.send(200, pages.shared_list(plan, checked))

    def _share_url(self, token):
        host = self.headers.get('Host') or 'localhost'
        return f"{'https' if self.browser_https else 'http'}://{host}/delad/{token}"

    def cart_post(self, path):
        f = self.form()
        if not self.check_csrf(f):
            return self.forbidden()
        uid = self.user()['id']
        if path == '/inkopslista/tom':
            db.clear_cart(uid)
            return self.get_cart('ok', 'Inköpslistan är tömd.')
        if path == '/inkopslista/budget':
            raw = re.sub(r'[\s ]', '', f.get('budget', '')).replace('kr', '')
            if f.get('do') == 'ta-bort' or not raw:
                db.set_pref(uid, 'budget', None)
                return self.redirect('/inkopslista')
            if not raw.isdigit() or not 0 < int(raw) <= 1_000_000:
                return self.get_cart('err', 'Ange budgeten som ett belopp i kronor, t.ex. 1500.', 400)
            db.set_pref(uid, 'budget', int(raw))
            return self.redirect('/inkopslista')
        if path == '/inkopslista/dela':
            token = security.new_token()
            db.set_share(uid, security.token_hash(token))
            return self.get_cart('ok', 'Delningslänken är skapad. Kopiera den nu – den visas bara en gång. '
                                       'En tidigare länk slutar gälla.', share_url=self._share_url(token))
        if path == '/inkopslista/kopt':
            return self._mark_bought(uid, f.get('shop', ''))
        if path == '/inkopslista/sluta-dela':
            db.delete_share(uid)
            return self.get_cart('ok', 'Inköpslistan delas inte längre. Den gamla länken fungerar inte.')
        if path != '/inkopslista/rad':
            return self.not_found()
        key = f.get('key', '')
        if key not in _catalog_names():
            return self.get_cart('err', 'Okänt filament.', 400)
        pack = f.get('pack') if f.get('pack') in ('any', 'Spole', 'Refill') else 'any'
        try:
            qty = max(0, min(db.CART_MAX_QTY, int(f.get('qty', '1'))))
        except ValueError:
            qty = 1
        if f.get('do') == 'ta-bort':
            qty = 0
        db.set_cart_item(uid, key, qty, pack)
        return self.redirect('/inkopslista')

    def api_cart_get(self):
        u = self.user()
        if not u or u['must_change_pw']:
            return self.json(401, {'error': 'inte inloggad'})
        self.json(200, {'items': [{'key': r['key'], 'qty': r['qty'], 'pack': r['pack']} for r in db.get_cart(u['id'])]})

    def api_cart_put(self):
        u = self.user()
        if not u or u['must_change_pw']:
            return self.json(401, {'error': 'inte inloggad'})
        if not self.check_csrf():
            return self.json(403, {'error': 'ogiltig token'})
        if not (self.headers.get('Content-Type') or '').startswith('application/json'):
            return self.json(415, {'error': 'JSON krävs'})
        try:
            data = json.loads(self.read_body(4096))
            key, qty = data['key'], int(data.get('qty', 1))
            if 'pack' in data:
                pack = data['pack']
            else:   # t.ex. "+ Inköpslista" i katalogen: behåll radens valda förpackning
                pack = next((r['pack'] for r in db.get_cart(u['id']) if r['key'] == key), 'any')
            if not isinstance(key, str) or key not in _catalog_names() or pack not in ('any', 'Spole', 'Refill'):
                raise ValueError
        except (ValueError, KeyError, TypeError, OverflowError):
            return self.json(400, {'error': 'ogiltig data'})
        if not db.set_cart_item(u['id'], key, max(0, min(db.CART_MAX_QTY, qty)), pack):
            return self.json(409, {'error': 'Inköpslistan är full'})
        self.api_cart_get()

    def api_wish_get(self):
        u = self.user()
        if not u or u['must_change_pw']:
            return self.json(401, {'error': 'inte inloggad'})
        offers, _ = cart.load_offers()
        self.json(200, {'items': db.get_wish(u['id']), 'hits': sorted(alerts.hits(u['id'], offers))})

    def api_wish_put(self):
        u, data = self._json_body()
        if not u:
            return
        items = data.get('items')
        names = _catalog_names()
        if not isinstance(items, list) or len(items) > 2000 or not all(isinstance(x, str) and x in names for x in items):
            return self.json(400, {'error': 'ogiltig data'})
        items = sorted(set(items))
        for k in set(db.get_wish(u['id'])) - set(items):
            db.set_alert(u['id'], k, None, None)      # bevakningen följer med när färgen tas bort
        db.set_wish(u['id'], items)
        self.json(200, {'items': items})

    def api_prefs_put(self):
        u, data = self._json_body(4096)
        if not u:
            return
        g = data.get('lowGrams')
        if not isinstance(g, int) or isinstance(g, bool) or not 0 <= g <= 1000:
            return self.json(400, {'error': 'ogiltig data'})
        db.set_pref(u['id'], 'low_grams', g)
        self.json(200, {'lowGrams': g})

    def api_cart_add(self):
        """Lägg flera färger i listan på en gång (t.ex. alla som snart är slut). Finns de redan ändras inget."""
        u, data = self._json_body()
        if not u:
            return
        keys = data.get('keys')
        names = _catalog_names()
        if not isinstance(keys, list) or len(keys) > db.CART_MAX_ITEMS or not all(isinstance(k, str) and k in names for k in keys):
            return self.json(400, {'error': 'ogiltig data'})
        have = {r['key'] for r in db.get_cart(u['id'])}
        added = 0
        for k in dict.fromkeys(keys):
            if k in have:
                continue
            if not db.set_cart_item(u['id'], k, 1, 'any'):
                break
            added += 1
        self.json(200, {'added': added, 'items': [{'key': r['key'], 'qty': r['qty'], 'pack': r['pack']}
                                                 for r in db.get_cart(u['id'])]})

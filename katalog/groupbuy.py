"""Samköp: flera användares inköpslistor planeras som en beställning (fri frakt och mängdrabatt tillsammans).

Bara användare som tillåtit samköp (pref samkop_ok) kan tas med. Varje enhet följer sin ägare; varor betalas av
ägaren och butikens frakt, köparskydd och mängdrabatt delas i proportion till varuvärdet.
"""
from collections import defaultdict

from . import cart, db


def participants(user_id):
    return [u for u in db.list_users() if u['id'] == user_id or db.get_pref(u['id'], 'samkop_ok') == '1']


def plan(user_ids, offers, catalog):
    owners = defaultdict(list)        # nyckel -> [användar-id per enhet]
    merged = defaultdict(int)
    for uid in user_ids:
        for r in db.get_cart(uid):
            merged[(r['key'], r['pack'])] += r['qty']
            owners[r['key']] += [uid] * r['qty']
    items = [(k, q, p) for (k, p), q in merged.items()]
    together = cart.plan(items, offers, catalog, budget=4.0)
    queue = {k: list(v) for k, v in owners.items()}
    people = defaultdict(lambda: {'goods': 0.0, 'shared': 0.0, 'units': 0, 'shops': set()})
    for sh in together['shops']:
        per = defaultdict(float)
        for line in sh['lines']:
            unit = line.get('bulkPrice') or line['offer']['price']
            who = []
            for _ in range(line['qty']):
                q = queue.get(line['key']) or []
                who.append(q.pop(0) if q else None)
            line['owners'] = who
            for uid in who:
                if uid is not None:
                    per[uid] += unit
                    people[uid]['units'] += 1
                    people[uid]['shops'].add(sh['shop'])
        goods = sum(per.values()) or 1
        extra = sh['shipping'] + sh['fee']
        for uid, g in per.items():
            people[uid]['goods'] += g
            people[uid]['shared'] += extra * g / goods
    for uid, p in people.items():
        p['total'] = p['goods'] + p['shared']
        p['shops'] = sorted(p['shops'])
        alone = cart.plan([(r['key'], r['qty'], r['pack']) for r in db.get_cart(uid)], offers, catalog, budget=1.0)
        p['alone'] = alone['totals']['total']
    return together, dict(people)

"""Veckosammanfattning via ntfy (söndagar kl. 18): bevakningar som nått målpriset, färger som snart tar slut,
spolar som bör torkas, underhåll att göra, veckans utskrifter och nya färger hos Bambu Lab."""
import datetime
import json
import time

from . import alerts, cart, config, db, newcolors, printers, printlog, spools

SEND_DAY, SEND_HOUR = 6, 18          # söndag 18:00


def build(uid, names, now=None):
    now = now or time.time()
    lines = []
    offers, _ = cart.user_offers(uid)
    hits = alerts.hits(uid, offers)
    if hits:
        lines.append(f"🔔 {len(hits)} på önskelistan har nått ditt pris: " +
                     ', '.join(f"{k.split('|')[0]} {names.get(k, '')}" for k in sorted(hits)[:6]))
    link = db.get_bambu(uid)
    have = {k: v.get('grams', 0) for k, v in (json.loads(link['matched'] or '{}') if link else {}).items()}
    for k, v in spools.summary(uid).items():
        have[k] = have.get(k, 0) + v['grams']
    fc = printlog.forecast(uid, have)
    low_g = int(db.get_pref(uid, 'low_grams') or 200)
    low = sorted(k for k, g in have.items() if g < low_g or (fc.get(k, {}).get('days') is not None and fc[k]['days'] < 14))
    if low:
        lines.append(f"📉 Snart slut: " + ', '.join(f"{k.split('|')[0]} {names.get(k, '')}" for k in low[:8]))
    dry = spools.needs_drying(uid)
    if dry:
        lines.append('💧 Bör torkas: ' + ', '.join(s['name'] for s in dry[:6]))
    due = []
    for row in db.list_printers(uid):
        items, _ = printers.maintenance(uid, row)
        due += [f"{row['name']}: {i['text'].lower()}" for i in items if i['due']]
    if due:
        lines.append('🔧 Underhåll: ' + '; '.join(due[:5]))
    week = [r for r in db.get_prints(uid, since=now - 7 * 86400)]
    if week:
        g = sum(sum(json.loads(r['grams'] or '{}').values()) for r in week)
        failed = sum(r['status'] != 'ok' for r in week)
        lines.append(f"🖨 {len(week)} utskrifter i veckan, {g:.0f} g för {sum(r['total'] for r in week):.0f} kr"
                     + (f" ({failed} misslyckade)" if failed else '') + '.')
    new = [v for v in newcolors.summary(now).get('upcoming', []) if v.get('first', 0) >= now - 7 * 86400]
    if new:
        lines.append('🆕 Nytt hos Bambu Lab: ' + ', '.join(f"{v['type']} {v['name']}" for v in new[:6]))
    return lines


def due(now_dt, last_week):
    return now_dt.weekday() == SEND_DAY and now_dt.hour >= SEND_HOUR and last_week != now_dt.strftime('%G-%V')


def run(now_dt, log=print):
    if not due(now_dt, db.get_setting('weekly_sent')):
        return 0
    db.set_setting('weekly_sent', now_dt.strftime('%G-%V'))
    catalog = json.loads(config.CATALOG_PATH.read_text(encoding='utf-8'))
    names = {d['type'] + '|' + (d['code'] or d['name']): d['name'] for d in catalog}
    sent = 0
    for u in db.list_users():
        target = db.get_pref(u['id'], 'notify')
        if db.get_pref(u['id'], 'weekly') != '1' or not target:
            continue
        lines = build(u['id'], names)
        if not lines:
            lines = ['Inget särskilt att rapportera den här veckan.']
        err = alerts.send(target, 'Veckan i filamentkatalogen', '\n'.join(lines))
        if err:
            log(f'[vecka] {err}')
        else:
            sent += 1
    return sent

"""Prisbevakning på önskelistan: målpris (kr inkl. frakt) och/eller minsta rabatt per färg.

Efter varje prisuppdatering jämförs bevakningarna med de nya priserna. Nya träffar aviseras via ntfy
(om användaren har angett ett ämne) och visas alltid i katalogen och på sidan Önskelista.
"""
import datetime
import json
import re
import urllib.error
import urllib.parse
import urllib.request

from . import config, db

TOPIC = re.compile(r'[A-Za-z0-9_-]{1,64}')


def _live(o, now):
    end = o.get('endAt')
    if not end:
        return True
    try:
        return datetime.datetime.fromisoformat(end.replace('Z', '+00:00')) > now
    except ValueError:
        return False


def total(o):
    return None if o.get('shipping') is None else o['price'] + o['shipping'] + (o.get('fee') or 0)


def best(key, offers, now=None):
    """Samma regler som katalogens prisrad: lägsta pris per kg bland jämförbara erbjudanden, och största rabatt.
    Returnerar {'best': (erbjudande, totalt) | None, 'deal': erbjudande | None}."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    b, bkg, deal = None, None, None
    for o in offers:
        if o.get('key') != key or not _live(o, now) or o.get('kind') != 'fixed':
            continue
        t = total(o)
        if o.get('weight') and o.get('pack') != 'Ej angiven' and o.get('condition') in ('Ny', 'Oanvänt') and t is not None:
            per_kg = t / o['weight'] * 1000
            if bkg is None or per_kg < bkg:
                b, bkg = (o, t), per_kg
        if (o.get('discount') or 0) > 0 and (deal is None or o['discount'] > deal['discount']):
            deal = o
    return {'best': b, 'deal': deal}


def is_hit(alert, info):
    """Träff om lägsta priset når målpriset eller någon butik har minst den rabatten."""
    target, min_disc = alert['target'], alert['min_discount']
    if target is not None and info['best'] and info['best'][1] <= target:
        return True
    return bool(min_disc is not None and info['deal'] and info['deal']['discount'] >= min_disc)


def hits(user_id, offers, now=None):
    """{katalognyckel: info} för bevakningar som är uppfyllda just nu."""
    out = {}
    for key, a in db.get_alerts(user_id).items():
        info = best(key, offers, now)
        if is_hit(a, info):
            out[key] = info
    return out


def _signature(info):
    return f"{info['best'][1] if info['best'] else ''}|{info['deal']['discount'] if info['deal'] else ''}"


def _better(sig, old):
    """Avisera igen bara om träffen blivit bättre (lägre pris eller större rabatt) än den förra aviseringen."""
    if not old:
        return True
    (p, d), (op, od) = sig.split('|'), old.split('|')
    return (p and (not op or int(p) < int(op))) or (d and (not od or int(d) > int(od)))


# --- ntfy ---
def parse_target(text):
    """'mitt-ämne' eller 'https://ntfy.sh/mitt-ämne' -> 'https://ntfy.sh/mitt-ämne' (bara tillåtna värdar), annars None."""
    text = (text or '').strip()
    if TOPIC.fullmatch(text) and config.NOTIFY_DEFAULT_HOST in config.NOTIFY_HOSTS:
        return f'https://{config.NOTIFY_DEFAULT_HOST}/{text}'
    try:
        p = urllib.parse.urlsplit(text)
    except ValueError:
        return None
    host = (p.hostname or '').lower()
    topic = p.path.strip('/')
    if p.scheme == 'https' and host in config.NOTIFY_HOSTS and p.port in (None, 443) and not p.username \
            and not p.query and TOPIC.fullmatch(topic):
        return f'https://{host}/{topic}'
    return None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def send(target, title, message, click=None):
    """Skickar en ntfy-avisering (JSON-publicering). Returnerar None vid lyckat, annars felmeddelande."""
    url = parse_target(target)
    if not url:
        return 'Aviseringsadressen är inte tillåten.'
    base, topic = url.rsplit('/', 1)
    body = {'topic': topic, 'title': title[:200], 'message': message[:3000], 'tags': ['shopping_cart']}
    if click:
        body['click'] = click
    req = urllib.request.Request(base + '/', data=json.dumps(body).encode('utf-8'), method='POST',
                                 headers={'Content-Type': 'application/json', 'User-Agent': 'BambuKatalog/1.0'})
    try:
        with urllib.request.build_opener(_NoRedirect).open(req, timeout=10) as r:
            r.read(1024)
        return None
    except (urllib.error.URLError, OSError, ValueError) as e:
        return f'Aviseringen kunde inte skickas ({e}).'


def _label(key, names):
    return f"{key.split('|')[0]} {names.get(key, key.split('|')[-1])}"


def _describe(key, info, names):
    parts = []
    if info['best']:
        o, t = info['best']
        parts.append(f"{t} kr inkl. frakt hos {o['shop'].split(' · ')[0]}")
    if info['deal']:
        parts.append(f"−{info['deal']['discount']} % hos {info['deal']['shop'].split(' · ')[0]}")
    return f"{_label(key, names)}: " + ', '.join(parts)


def notify_all(offers, names, log=print):
    """Efter en prisuppdatering: avisera nya eller bättre träffar per användare (en avisering per användare)."""
    for uid in db.alert_users():
        try:
            alerts = db.get_alerts(uid)
            target = db.get_pref(uid, 'notify')
            fresh = []
            for key, a in alerts.items():
                info = best(key, offers)
                if not is_hit(a, info):
                    if a['notified']:
                        db.set_alert_notified(uid, key, None)   # nästa träff aviseras igen
                    continue
                sig = _signature(info)
                if _better(sig, a['notified']):
                    fresh.append((key, info, sig))
            if not fresh:
                continue
            err = None
            if target:
                lines = [_describe(k, i, names) for k, i, _ in fresh]
                title = f"{len(fresh)} {'färg' if len(fresh) == 1 else 'färger'} på önskelistan har nått ditt pris"
                err = send(target, title, '\n'.join(lines), config.PUBLIC_URL + '/onskelista' if config.PUBLIC_URL else None)
                if err:
                    log(f'[bevakning] användare {uid}: {err}')
            if not err:
                for key, _, sig in fresh:
                    db.set_alert_notified(uid, key, sig)
        except Exception as ex:  # noqa: BLE001 – en användare ska inte stoppa de andra
            log(f'[bevakning] fel för användare {uid}: {ex!r}')

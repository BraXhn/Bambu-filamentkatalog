"""HTML för inloggning, konto och administration. Vanliga formulär; bara små skript från /assets (strikt CSP)."""
import datetime
import json
import re
import urllib.parse
from html import escape as e
from zoneinfo import ZoneInfo

from . import config

try:
    TZ = ZoneInfo(config.TIMEZONE)
except Exception:  # noqa: BLE001
    TZ = ZoneInfo('UTC')


def fmt_time(ts, empty='—'):
    if not ts:
        return empty
    return datetime.datetime.fromtimestamp(ts, TZ).strftime('%Y-%m-%d %H:%M')


def fmt_rel(ts):
    if not ts:
        return 'aldrig'
    d = datetime.datetime.now().timestamp() - ts
    if d < 90:
        return 'nyss'
    if d < 3600:
        return f'{int(d // 60)} min sedan'
    if d < 86400:
        return f'{int(d // 3600)} tim sedan'
    return fmt_time(ts)


def layout(title, body, user=None, csrf='', active='', narrow=False, refresh=None, scripts=()):
    nav = ''
    if user:
        items = [('/', 'Katalog', 'katalog'), ('/onskelista', 'Önskelista', 'onske'), ('/inkopslista', 'Inköpslista', 'inkop'),
                 ('/kostnad', 'Kostnad', 'kostnad'), ('/utskrifter', 'Utskrifter', 'utskrifter'), ('/skrivare', 'Skrivare', 'skrivare'),
                 ('/projekt', 'Projekt', 'projekt'), ('/konto', 'Mitt konto', 'konto')]
        if user['is_admin']:
            items.append(('/admin', 'Administration', 'admin'))
        links = ''.join(f'<a href="{h}"{" aria-current=page" if k == active else ""}>{t}</a>' for h, t, k in items)
        nav = (f'<nav class="menu" aria-label="Meny"><span class="who">Inloggad som <b>{e(user["username"])}</b></span>'
               f'{links}<form method="post" action="/logout"><input type="hidden" name="csrf" value="{e(csrf)}">'
               f'<button type="submit">Logga ut</button></form></nav>')
    meta = f'<meta http-equiv="refresh" content="{int(refresh)}">' if refresh else ''
    return f"""<!doctype html><html lang="sv"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow">
<meta name="referrer" content="same-origin">{meta}
<title>{e(title)} · {e(config.SITE_NAME)}</title><link rel="stylesheet" href="/assets/ui.css">
<link rel="icon" href="/assets/icon.svg" type="image/svg+xml">{''.join(f'<script src="{e(x)}" defer></script>' for x in scripts)}</head><body>
<header class="top"><a class="brand" href="/"><i>▥</i> BAMBU <span>/ filamentkatalog</span></a>{nav}</header>
<main class="page{' narrow' if narrow else ''}">{body}</main>
<footer class="foot">{e(config.SITE_NAME)}<br>Created by Helicon &amp; BraXhn</footer></body></html>"""


def message(kind, text):
    return f'<div class="msg {kind}" role="{"alert" if kind == "err" else "status"}">{text}</div>' if text else ''


# ---------- inloggning ----------
def login(pre_csrf, next_url='/', error='', info='', username=''):
    body = f"""<h1>Logga in</h1><p class="lead">Katalogen är bara öppen för inloggade användare.</p>
{message('err', e(error))}{message('ok', e(info))}
<form class="card" method="post" action="/login" autocomplete="on">
<input type="hidden" name="csrf" value="{e(pre_csrf)}"><input type="hidden" name="next" value="{e(next_url)}">
<div class="field"><label for="u">Användarnamn</label>
<input id="u" name="username" type="text" autocomplete="username" autocapitalize="none" spellcheck="false" required maxlength="40" value="{e(username)}" autofocus></div>
<div class="field"><label for="p">Lösenord</label>
<input id="p" name="password" type="password" autocomplete="current-password" required maxlength="256"></div>
<button class="btn block" type="submit">Logga in</button>
<p class="muted mt" style="font-size:13px;margin-bottom:0">Glömt lösenordet? Be en administratör att återställa det.</p>
</form>"""
    return layout('Logga in', body, narrow=True)


def setup(pre_csrf, error='', values=None, need_code=True):
    v = values or {}
    code = (f'<div class="field"><label for="c">Installationskod</label><input id="c" name="code" type="text" required '
            f'autocomplete="off" spellcheck="false" value="{e(v.get("code", ""))}"><small>Koden står i containerns logg '
            f'(t.ex. <code>docker logs katalog</code> eller loggfönstret i Unraid).</small></div>') if need_code else ''
    body = f"""<h1>Välkommen!</h1><p class="lead">Skapa det första administratörskontot. Därefter kan du lägga till fler användare under Administration.</p>
{message('err', e(error))}
<form class="card" method="post" action="/setup" autocomplete="off">
<input type="hidden" name="csrf" value="{e(pre_csrf)}">{code}
<div class="field"><label for="u">Användarnamn</label><input id="u" name="username" type="text" required maxlength="40"
autocapitalize="none" spellcheck="false" autocomplete="username" value="{e(v.get('username', ''))}"></div>
<div class="field"><label for="p">Lösenord</label><input id="p" name="password" type="password" required autocomplete="new-password" maxlength="256">
<small>Minst {config.PASSWORD_MIN_LENGTH} tecken. En lång fras är både säkrare och lättare att komma ihåg.</small></div>
<div class="field"><label for="p2">Upprepa lösenord</label><input id="p2" name="password2" type="password" required autocomplete="new-password" maxlength="256"></div>
<button class="btn block" type="submit">Skapa administratör</button></form>"""
    return layout('Kom igång', body, narrow=True)


# ---------- konto ----------
def account(user, csrf, sessions_count, msg_kind='', msg='', must_change=False, bambu=''):
    warn = message('warn', 'Du behöver byta lösenord innan du kan använda katalogen.') if must_change else ''
    body = f"""<h1>Mitt konto</h1><p class="lead">Hantera ditt lösenord och dina inloggningar.</p>
{warn}{message(msg_kind, e(msg))}
<section class="card"><h2>Byt lösenord</h2><p class="sub">Minst {config.PASSWORD_MIN_LENGTH} tecken. Andra enheter loggas ut när du byter.</p>
<form method="post" action="/konto/losenord" autocomplete="on">
<input type="hidden" name="csrf" value="{e(csrf)}">
<input type="text" name="username" value="{e(user['username'])}" autocomplete="username" hidden>
<div class="field"><label for="cur">Nuvarande lösenord</label><input id="cur" name="current" type="password" autocomplete="current-password" required maxlength="256"></div>
<div class="field"><label for="n1">Nytt lösenord</label><input id="n1" name="new" type="password" autocomplete="new-password" required maxlength="256"></div>
<div class="field"><label for="n2">Upprepa nytt lösenord</label><input id="n2" name="new2" type="password" autocomplete="new-password" required maxlength="256"></div>
<button class="btn" type="submit">Spara nytt lösenord</button></form></section>
<section class="card"><h2>Inloggningar</h2><p class="sub">Du är inloggad på {sessions_count} {'enhet' if sessions_count == 1 else 'enheter'}.
Har du loggat in på en dator du inte litar på kan du logga ut överallt utom här.</p>
<form method="post" action="/konto/logga-ut-andra"><input type="hidden" name="csrf" value="{e(csrf)}">
<button class="btn ghost" type="submit"{' disabled' if sessions_count <= 1 else ''}>Logga ut andra enheter</button></form></section>
{bambu}"""
    return layout('Mitt konto', body, user, csrf, 'konto', scripts=('/assets/account.js',))


# ---------- administration ----------
STATUS = {'ok': ('ok', 'Lyckades'), 'partial': ('warn', 'Delvis'), 'failed': ('bad', 'Misslyckades'),
          'running': ('run', 'Pågår…'), 'skipped': ('', 'Hoppades över')}
TRIGGER = {'schedule': 'Schemalagd', 'manual': 'Manuell', 'start': 'Vid start', 'cli': 'Kommandorad'}


def _badge(status):
    cls, text = STATUS.get(status, ('', status))
    return f'<span class="badge {cls}">{e(text)}</span>'


def _run_sources(summary):
    try:
        s = json.loads(summary or '{}')
    except ValueError:
        return ''
    out = []
    for k, v in s.items():
        if k.startswith('_') or v.get('status') == 'skipped':
            continue
        cls = STATUS.get(v.get('status'), ('', ''))[0]
        title = f' title="{e(v["error"])}"' if v.get('error') else ''
        out.append(f'<span class="badge {cls}"{title}>{e(v.get("label", k))}: {v.get("offers", 0)}</span>')
    return '<div class="sources">' + ''.join(out) + '</div>'


ALERT_RUNS = 2   # så många misslyckade körningar i rad innan en källa varnas


def source_alerts(runs):
    """[(etikett, antal misslyckade i rad, senaste fel)] för källor som fallerat i de senaste körningarna."""
    streak, label, err, done = {}, {}, {}, set()
    for r in runs:                      # nyast först
        if r['status'] == 'running':
            continue
        try:
            summ = json.loads(r['summary'] or '{}')
        except ValueError:
            continue
        for sid, v in summ.items():
            if sid.startswith('_') or not isinstance(v, dict) or v.get('status') == 'skipped' or sid in done:
                continue
            if v.get('status') == 'failed':
                streak[sid] = streak.get(sid, 0) + 1
                label.setdefault(sid, v.get('label', sid))
                err.setdefault(sid, v.get('error', ''))
            else:
                done.add(sid)
    return [(label[k], n, err[k]) for k, n in streak.items() if n >= ALERT_RUNS]


def admin(user, csrf, users, runs, next_run, offers_info, msg_kind='', msg='', secret=None, backups=None, integrations=None):
    running = any(r['status'] == 'running' for r in runs)
    last_done = next((r for r in runs if r['status'] != 'running'), None)
    secret_html = ''
    if secret:
        secret_html = (f'<div class="msg ok" role="status"><b>{e(secret[0])}</b><br>Ge lösenordet till användaren på ett säkert sätt. '
                       f'Det visas bara nu och måste bytas vid första inloggningen.<br><span class="secret">{e(secret[1])}</span></div>')

    # --- priser ---
    last_txt = f'{fmt_time(last_done["finished"])} {_badge(last_done["status"])}' if last_done else 'Ingen körning än'
    rows = ''
    for r in runs:
        dur = f'{int((r["finished"] or r["started"]) - r["started"])} s' if r['finished'] else '—'
        log = (f'<details><summary>Visa logg</summary><pre>{e(r["log"] or "")}</pre></details>') if r['log'] else ''
        rows += (f'<tr><td>{fmt_time(r["started"])}<br><span class="muted" style="font-size:12px">{e(TRIGGER.get(r["trigger"], r["trigger"]))}'
                 f' · {dur}</span></td><td>{_badge(r["status"])}</td><td>{_run_sources(r["summary"])}{log}</td></tr>')
    alerts = ''.join(f'<div class="msg warn" role="status"><b>{e(lbl)}</b> har misslyckats {n} körningar i rad. '
                     f'Butiken kan ha ändrat sitt sidformat; tidigare priser visas tills vidare.'
                     f'{"<br><span class=muted>" + e(err) + "</span>" if err else ""}</div>' for lbl, n, err in source_alerts(runs))
    runs_table = (f'<div class="tablewrap mt"><table><thead><tr><th>Startad</th><th>Resultat</th><th>Källor (antal erbjudanden)</th></tr>'
                  f'</thead><tbody>{rows}</tbody></table></div>') if rows else ''
    prices = f"""<section class="card" id="priser"><h2>Prisuppdatering</h2>
<p class="sub">Priser hämtas automatiskt från 3DJake, 3D Prima, 3DVerkstan, 3DExperten, Bambu Lab EU och Tradera. Om en källa inte svarar behålls dess senaste priser.</p>
<div class="stats"><div class="stat"><span>Senaste körning</span><strong>{last_txt}</strong></div>
<div class="stat"><span>Nästa schemalagda</span><strong>{e(next_run)}</strong></div>
<div class="stat"><span>Erbjudanden just nu</span><strong>{e(offers_info)}</strong></div></div>
<form method="post" action="/admin/priser/uppdatera"><input type="hidden" name="csrf" value="{e(csrf)}">
<button class="btn" type="submit"{' disabled' if running else ''}>{'Uppdatering pågår…' if running else 'Uppdatera priser nu'}</button>
<span class="muted" style="font-size:13px;margin-left:8px">{'Sidan laddas om automatiskt.' if running else 'Tar normalt 10–20 minuter.'}</span></form>
{alerts}{runs_table}</section>"""

    # --- användare ---
    now = datetime.datetime.now().timestamp()
    urows = ''
    for u in users:
        me = u['id'] == user['id']
        tags = ['<span class="badge admin">Admin</span>' if u['is_admin'] else '<span class="badge">Användare</span>']
        if u['locked_until'] > now:
            tags.append('<span class="badge bad">Låst</span>')
        if u['must_change_pw']:
            tags.append('<span class="badge warn">Måste byta lösenord</span>')
        hidden = f'<input type="hidden" name="csrf" value="{e(csrf)}">'
        act = []
        if not me:
            act.append(f'<form method="post" action="/admin/anvandare/{u["id"]}/losenord">{hidden}<button class="btn ghost small" type="submit">Nytt lösenord</button></form>')
            act.append(f'<form method="post" action="/admin/anvandare/{u["id"]}/roll">{hidden}<button class="btn ghost small" type="submit">'
                       f'{"Ta bort admin" if u["is_admin"] else "Gör till admin"}</button></form>')
            if u['locked_until'] > now:
                act.append(f'<form method="post" action="/admin/anvandare/{u["id"]}/las-upp">{hidden}<button class="btn ghost small" type="submit">Lås upp</button></form>')
            if u['sessions']:
                act.append(f'<form method="post" action="/admin/anvandare/{u["id"]}/logga-ut">{hidden}<button class="btn ghost small" type="submit">Logga ut</button></form>')
            act.append(f'<a class="btn danger small" href="/admin/anvandare/{u["id"]}/ta-bort">Ta bort</a>')
        else:
            act.append('<span class="muted" style="font-size:13px">Det här är du · <a href="/konto">Mitt konto</a></span>')
        urows += (f'<tr><td><b>{e(u["username"])}</b><div class="mt" style="margin-top:6px">{" ".join(tags)}</div></td>'
                  f'<td class="hide-sm">{fmt_rel(u["last_login"])}</td><td class="hide-sm">{fmt_time(u["created"])}</td>'
                  f'<td><div class="actions">{"".join(act)}</div></td></tr>')
    users_html = f"""<section class="card" id="anvandare"><h2>Användare</h2>
<p class="sub">Varje användare har egna markeringar för ”Jag har detta filament”. Administratörer kan hantera användare och priser.</p>
<div class="tablewrap"><table><thead><tr><th>Användare</th><th class="hide-sm">Senast inloggad</th><th class="hide-sm">Skapad</th><th class="right">Åtgärder</th></tr></thead>
<tbody>{urows}</tbody></table></div>
<h2 class="mt" style="margin-top:26px">Lägg till användare</h2>
<form method="post" action="/admin/anvandare" autocomplete="off" class="mt"><input type="hidden" name="csrf" value="{e(csrf)}">
<div class="row"><div class="field"><label for="nu">Användarnamn</label><input id="nu" name="username" type="text" required maxlength="40" autocapitalize="none" spellcheck="false" autocomplete="off"></div>
<div class="field"><label for="np">Lösenord <span class="muted" style="font-weight:400">(valfritt)</span></label>
<input id="np" name="password" type="password" maxlength="256" autocomplete="new-password" placeholder="Lämna tomt för att slumpa"></div>
<div class="field" style="max-width:170px"><label for="nr">Roll</label><select id="nr" name="role"><option value="user">Användare</option><option value="admin">Administratör</option></select></div>
<div><button class="btn" type="submit">Lägg till</button></div></div>
<p class="muted" style="font-size:12px;margin:10px 0 0">Användaren får byta lösenord vid första inloggningen.</p></form></section>"""

    backup = f"""<section class="card" id="sakerhetskopia"><h2>Säkerhetskopia</h2>
<p class="sub">Laddar ner databasen (användare, markeringar, inköpslistor, Bambu-kopplingar) och prishistoriken som en zip-fil.
Filen innehåller lösenordshashar och inloggningar till Bambu Lab – förvara den säkert.
Återställ med knappen nedan (lägg en nedladdad kopia i <code>/data/backups</code> först), eller stoppa containern och packa upp filen i datamappen.</p>
<form method="post" action="/admin/sakerhetskopia"><input type="hidden" name="csrf" value="{e(csrf)}">
<button class="btn ghost" type="submit">Ladda ner säkerhetskopia</button></form>{backups_html(csrf, backups or [], config.BACKUP_TIME, config.BACKUP_KEEP)}</section>"""

    body = f"""<h1>Administration</h1><p class="lead">Användare, prisuppdatering och säkerhetskopia.</p>
{message(msg_kind, e(msg))}{secret_html}{prices}{users_html}{integrations_html(csrf, integrations) if integrations else ''}{backup}"""
    return layout('Administration', body, user, csrf, 'admin', refresh=15 if running else None)


def confirm_delete(user, csrf, target):
    body = f"""<h1>Ta bort användare</h1>
<section class="card"><p>Vill du ta bort <b>{e(target['username'])}</b>? Användarens inloggningar och markeringar raderas. Det går inte att ångra.</p>
<form method="post" action="/admin/anvandare/{target['id']}/ta-bort" class="row mt"><input type="hidden" name="csrf" value="{e(csrf)}">
<button class="btn danger" type="submit">Ja, ta bort {e(target['username'])}</button> <a class="btn ghost" href="/admin">Avbryt</a></form></section>"""
    return layout('Ta bort användare', body, user, csrf, 'admin')


def error_page(code, title, text, user=None, csrf=''):
    body = f'<h1>{e(title)}</h1><section class="card"><p>{e(text)}</p><p class="mt"><a class="btn ghost" href="/">Till katalogen</a></p></section>'
    return layout(title, body, user, csrf, narrow=True)


# ---------- Bambu Lab-koppling (på Mitt konto) ----------
def bambu_section(csrf, link, pending, catalog_names, msg_kind='', msg=''):
    h = f'<input type="hidden" name="csrf" value="{e(csrf)}">'
    intro = ('<p class="sub">Hämtar spolarna i ditt Bambu Lab-kontos filamentbibliotek (det du ser under '
             '<i>Filament</i> i Bambu Handy och Bambu Studio) och markerar dem som ”Jag har” i katalogen, '
             'tillsammans med hur mycket filament som finns kvar. Synken går bara åt ett håll: ingenting '
             'ändras i ditt Bambu-konto.</p>')
    note = ('<p class="muted" style="font-size:12px;margin:14px 0 0">Bambu Lab har inget officiellt API för biblioteket. '
            'Synken använder samma anrop som Bambu Studio och kan sluta fungera om Bambu ändrar något. '
            'Ditt Bambu-lösenord skickas bara till Bambu Lab och sparas aldrig här; inloggningen gäller i cirka 90 dagar.</p>')
    out = message(msg_kind, e(msg))
    if pending:
        if pending['step'] == 'tfa':
            label, help_txt, resend = 'Kod från din autentiseringsapp', 'Ange den sexsiffriga koden från appen du använder för tvåstegsverifiering hos Bambu Lab.', ''
        else:
            label = 'Kod från e-post'
            help_txt = f'Bambu Lab har skickat en kod till <b>{e(pending["account"])}</b>. Den kommer från Bambu Lab, inte från den här sajten.'
            resend = (f'<form method="post" action="/konto/bambu/ny-kod" style="display:inline">{h}'
                      f'<button class="btn ghost" type="submit">Skicka ny kod</button></form>')
        body = f"""<p class="sub">{help_txt}</p>
<form method="post" action="/konto/bambu/kod" autocomplete="off">{h}
<div class="field" style="max-width:260px"><label for="bc">{label}</label>
<input id="bc" name="code" type="text" inputmode="numeric" autocomplete="one-time-code" required maxlength="12" autofocus></div>
<div class="row"><button class="btn" type="submit">Bekräfta</button></div></form>
<div class="row mt">{resend}<form method="post" action="/konto/bambu/avbryt" style="display:inline">{h}
<button class="btn ghost" type="submit">Avbryt</button></form></div>"""
        return f'<section class="card" id="bambu"><h2>Bambu Lab-konto</h2>{out}{body}{note}</section>'
    if not link:
        body = f"""{intro}
<form method="post" action="/konto/bambu/koppla" autocomplete="off">{h}
<div class="row"><div class="field"><label for="ba">E-post hos Bambu Lab</label>
<input id="ba" name="account" type="text" inputmode="email" autocapitalize="none" spellcheck="false" required maxlength="120"></div>
<div class="field"><label for="bp">Lösenord hos Bambu Lab</label>
<input id="bp" name="password" type="password" autocomplete="off" required maxlength="256"></div>
<div class="field" style="max-width:150px"><label for="br">Region</label><select id="br" name="region">
<option value="global">Global</option><option value="china">Kina</option></select></div></div>
<div class="row mt"><button class="btn" type="submit">Koppla Bambu Lab-konto</button></div></form>"""
        return f'<section class="card" id="bambu"><h2>Bambu Lab-konto</h2>{out}{body}{note}</section>'

    # Kopplat konto
    try:
        matched = json.loads(link['matched'] or '{}')
        unmatched = json.loads(link['unmatched'] or '[]')
    except ValueError:
        matched, unmatched = {}, []
    spools = sum(m.get('spools', 0) for m in matched.values()) + len(unmatched)
    grams = sum(m.get('grams', 0) for m in matched.values()) + sum(u.get('grams') or 0 for u in unmatched)
    status = link['status']
    if status == 'expired':
        banner = message('warn', 'Inloggningen hos Bambu Lab har gått ut (det sker ungefär var 90:e dag). '
                                 'Koppla kontot igen nedan. Dina senast synkade spolar visas tills vidare.')
    elif status == 'error':
        banner = message('err', 'Senaste synken misslyckades: ' + e(link['error'] or 'okänt fel') +
                         '. Senast kända spolar visas tills vidare.')
    else:
        banner = ''
    days = int(((link['token_expires'] or 0) - datetime.datetime.now().timestamp()) // 86400)
    expiry = (f'{fmt_time(link["token_expires"])[:10]} ({days} dagar kvar)' if link['token'] and link['token_expires']
              else 'Utgången')
    stats = f"""<div class="stats"><div class="stat"><span>Konto</span><strong>{e(link['account'])}</strong></div>
<div class="stat"><span>Senast synkad</span><strong>{fmt_rel(link['last_sync'])}</strong></div>
<div class="stat"><span>Spolar i biblioteket</span><strong>{spools} st · {f'{grams / 1000:.1f}'.replace('.', ',')} kg kvar</strong></div>
<div class="stat"><span>Inloggning gäller till</span><strong>{e(expiry)}</strong></div></div>"""
    rows = ''
    for key, m in sorted(matched.items()):
        typ, _, rest = key.partition('|')
        name = catalog_names.get(key, rest)
        left = f'{m["grams"]} g' + (f' av {m["total"]} g' if m.get('total') else '')
        rows += (f'<tr><td>{e(typ)}</td><td>{e(name)}</td><td class="right">{m["spools"]}</td>'
                 f'<td class="right">{e(left)}</td></tr>')
    table = (f'<details class="mt"><summary>Visa {len(matched)} {"färg" if len(matched) == 1 else "färger"} som markerats som ”Jag har”</summary>'
             f'<div class="tablewrap mt"><table><thead><tr><th>Typ</th><th>Färg</th><th class="right">Spolar</th>'
             f'<th class="right">Kvar</th></tr></thead><tbody>{rows}</tbody></table></div></details>') if rows else ''
    urows = ''.join(
        f'<tr><td>{e(u.get("name") or "Okänd")}</td><td>'
        + (f'<span class="color-dot" style="background:{e(u["color"])}"></span> {e(u["color"])}' if re.fullmatch(r'#[0-9A-F]{6}', u.get('color') or '') else '—')
        + f'</td><td class="right">{"" if u.get("grams") is None else str(u["grams"]) + " g"}</td></tr>' for u in unmatched)
    utable = (f'<details class="mt"><summary>{len(unmatched)} {"spole finns" if len(unmatched) == 1 else "spolar finns"} inte i katalogen</summary>'
              f'<p class="muted" style="font-size:13px">Till exempel filament från andra tillverkare eller Bambu-serier som katalogen saknar.</p>'
              f'<div class="tablewrap"><table><thead><tr><th>Filament</th><th>Färg</th><th class="right">Kvar</th></tr></thead>'
              f'<tbody>{urows}</tbody></table></div></details>') if unmatched else ''
    relink = ''
    if status == 'expired' or not link['token']:
        relink = f"""<form method="post" action="/konto/bambu/koppla" autocomplete="off" class="mt">{h}
<input type="hidden" name="account" value="{e(link['account'])}"><input type="hidden" name="region" value="{e(link['region'])}">
<div class="row"><div class="field"><label for="bp">Lösenord hos Bambu Lab för {e(link['account'])}</label>
<input id="bp" name="password" type="password" autocomplete="off" required maxlength="256"></div>
<div><button class="btn" type="submit">Logga in igen</button></div></div></form>"""
    actions = f"""<div class="row mt">{'' if not link['token'] else f'<form method="post" action="/konto/bambu/synka">{h}<button class="btn" type="submit">Synka nu</button></form>'}
<form method="post" action="/konto/bambu/koppla-fran">{h}<button class="btn danger" type="submit">Koppla från</button></form></div>"""
    return (f'<section class="card" id="bambu"><h2>Bambu Lab-konto</h2>{intro}{out}{banner}{stats}{relink}{actions}'
            f'{table}{utable}{note}</section>')


# ---------- inköpslista ----------
def _kr(n):
    return f'{round(n):,} kr'.replace(',', ' ')


def _safe_href(url):
    return e(url) if str(url or '').startswith('https://') else '#'


def _eur(v):
    return f'{v:.2f} €'.replace('.', ',')


def _bulk_box(sh):
    """Tydlig redovisning av butikens mängd-/volymrabatt och hur nära nästa nivå man är."""
    from . import config
    if not sh.get('bulk'):
        return ''
    items = ''
    for b in sh['bulk']:
        rolls = f'{b["rolls"]} {"st" if sh["shop"] != "Bambu Lab EU" else ("rulle" if b["rolls"] == 1 else "rullar")}'
        if b['reached']:
            items += f'<li><b>{e(b["label"])}</b>: {rolls} → {e(b["desc"])}. <b>Du sparar {_kr(b["saved"])}.</b>'
        else:
            items += f'<li><b>{e(b["label"])}</b>: {rolls} – ingen rabatt ännu.'
        if b['next']:
            items += f' <span class="muted">{e(b["next"])}</span>'
        items += '</li>'
    if sh['shop'] == 'Bambu Lab EU':
        tiers = ', '.join(f'{n}+ rullar: spole {_eur(p["Spole"])} / refill {_eur(p["Refill"])}' for n, p in config.BAMBU_BULK_TIERS)
        terms = (f'Bambu drar rabatten automatiskt i kassan. Villkor: {e(tiers)} per rulle à 1 kg av '
                 f'{e(", ".join(config.BAMBU_BULK_TYPES))}. Kalkylen räknar ihop rullar av samma filamenttyp '
                 f'(färger och spole/refill blandas) – kontrollera beloppet i kassan.')
        name = 'Mängdrabatt hos Bambu Lab'
    else:
        terms = ('Butikens volymrabatt gäller per produkt; kalkylen räknar ihop alla färger av samma produkt. '
                 'Rabatten dras i varukorgen – kontrollera beloppet i kassan.')
        name = f'Volymrabatt hos {sh["shop"]}'
    head = f'{name}: −{_kr(sh["discount"])}, inräknad i summan.' if sh.get('discount') else f'{name}: inte uppnådd ännu.'
    return (f'<div class="msg ok" style="margin:0 0 12px"><b>{e(head)}</b>'
            f'<ul style="margin:6px 0;padding-left:18px">{items}</ul>'
            f'<span style="font-size:12px">{terms}</span></div>')


def _when(checked_at):
    if checked_at:
        try:
            return datetime.datetime.fromisoformat(checked_at.replace('Z', '+00:00')).astimezone(TZ).strftime('%Y-%m-%d %H:%M')
        except ValueError:
            pass
    return ''


def timing_badge(o):
    """'Lägsta på 90 dagar' / 'Brukar kosta ca X kr' från prishistoriken för erbjudandet."""
    if o.get('low90'):
        return ' <span class="badge ok" title="Dagens pris är det lägsta för erbjudandet de senaste 90 dagarna">Lägsta på 90 dagar</span>'
    if o.get('usual'):
        return (f' <span class="badge warn" title="Typiskt pris de senaste 30 dagarna">Brukar kosta ca {_kr(o["usual"])}</span>')
    return ''


def _plan_html(plan, checked_at, title_total='Uppskattat totalt', csrf=None):
    """Summering, butiker och saknade varor – samma på den egna och den delade inköpslistan."""
    t = plan['totals']
    est = ' (uppskattad frakt ingår)' if t['estimated'] else ''
    when = _when(checked_at)
    summary = f"""<section class="card"><div class="stats">
<div class="stat"><span>{e(title_total)}</span><strong style="font-size:24px">{_kr(t['total'])}</strong></div>
<div class="stat"><span>Varor</span><strong>{_kr(t['goods'])}</strong></div>
{f'<div class="stat"><span>Mängdrabatter</span><strong style="color:var(--ok,#16a34a)">−{_kr(t["discount"])}</strong></div>' if t.get('discount') else ''}
<div class="stat"><span>Frakt{' (delvis uppskattad)' if t['estimated'] else ''}</span><strong>{_kr(t['shipping'])}</strong></div>
<div class="stat"><span>Köparskydd (Tradera)</span><strong>{_kr(t['fee'])}</strong></div></div>
<p class="sub" style="margin:0">{t['units']} {'spole' if t['units'] == 1 else 'spolar'} hos {len(plan['shops'])} {'butik/säljare' if len(plan['shops']) == 1 else 'butiker/säljare'}{est}.
Butiker väljs så att totalen inklusive frakt blir så låg som möjligt, t.ex. genom att samla köp för fri frakt.
Priser från {e(when) or 'senaste sökningen'} – kontrollera pris och lager innan du betalar.</p></section>"""
    shops = ''
    for sh in plan['shops']:
        rows = ''
        for l in sh['lines']:
            o, d = l['offer'], l['item'] or {}
            name = f"{d.get('type', l['key'].split('|')[0])} · {d.get('name', l['key'].split('|')[-1])}"
            disc = (f' <span class="badge bad">−{o["discount"]} %</span>' if o.get('discount') else '') + timing_badge(o)
            rows += (f'<tr><td><b>{e(name)}</b>{disc}<br><span class="muted" style="font-size:12px">{e(o.get("title", ""))}</span></td>'
                     f'<td class="hide-sm">{e(o.get("pack", ""))} {o.get("weight") or "?"} g</td>'
                     + (f'<td class="right"><span style="white-space:nowrap">{l["qty"]} × <s class="muted">{_kr(o["price"])}</s></span> '
                        f'<span style="white-space:nowrap">{_kr(l["bulkPrice"])}</span>'
                        f'<br><span class="badge ok">mängdrabatt</span></td>'
                        f'<td class="right"><b>{_kr(l["bulkPrice"] * l["qty"])}</b></td>' if l.get('bulkPrice') else
                        f'<td class="right">{l["qty"]} × {_kr(o["price"])}</td><td class="right"><b>{_kr(o["price"] * l["qty"])}</b></td>')
                     + f'<td class="right"><a class="btn small" href="{_safe_href(o.get("url"))}" target="_blank" rel="noopener">Köp här ↗</a>'
                       f'<span class="print-only">{e(o.get("url", ""))}</span></td></tr>')
        bulk = _bulk_box(sh)
        ship = f'{_kr(sh["shipping"])}{" (uppskattad)" if sh["estimated"] else ""}'
        fee = f' · köparskydd {_kr(sh["fee"])}' if sh['fee'] else ''
        home = f' <a href="{_safe_href(sh["home"])}" target="_blank" rel="noopener" style="font-size:13px">Till butiken ↗</a>' \
            if sh['home'] and not sh['shop'].startswith('Tradera') else ''
        bought = (f'<form method="post" action="/inkopslista/kopt" class="no-print" style="margin-top:12px"><input type="hidden" name="csrf" value="{e(csrf)}">'
                  f'<input type="hidden" name="shop" value="{e(sh["shop"])}"><button class="btn ghost small" type="submit">✓ Köpt här</button>'
                  f' <span class="muted" style="font-size:12px">Tar bort raderna ur listan, markerar färgerna under Jag har och sparar köpet.</span></form>') if csrf else ''
        shops += f"""<section class="card"><div class="row" style="justify-content:space-between;align-items:baseline">
<h2>{e(sh['shop'])}{home}</h2><strong style="font-size:18px">{_kr(sh['total'])}</strong></div>
<p class="sub">Varor {_kr(sh['goods'])}{f" · mängdrabatt −{_kr(sh['discount'])}" if sh.get('discount') else ''} · frakt {ship}{fee}. {e(sh['note'])}</p>{bulk}
<div class="tablewrap"><table><thead><tr><th>Filament</th><th class="hide-sm">Förpackning</th><th class="right">Antal</th>
<th class="right">Summa</th><th></th></tr></thead><tbody>{rows}</tbody></table></div>{bought}</section>"""
    missing = ''
    if plan['missing']:
        mrows = ''
        for m in plan['missing']:
            d = m['item'] or {}
            q = urllib.parse.quote(f"Bambu Lab {d.get('type', '')} {d.get('name', '')}".strip())
            mrows += (f'<li><b>{e(d.get("type", m["key"]))} · {e(d.get("name", ""))}</b> ({m["qty"]} st'
                      f'{", " + e(m["pack"]) if m["pack"] != "any" else ""}) – '
                      f'<a href="https://www.prisjakt.nu/search?search={q}" target="_blank" rel="noopener">sök på Prisjakt ↗</a> · '
                      f'<a href="https://www.tradera.com/search?q={q}" target="_blank" rel="noopener">Tradera ↗</a></li>')
        missing = (f'<section class="card"><h2>Inget köpbart erbjudande just nu</h2><p class="sub">Ingår inte i totalen.</p>'
                   f'<ul style="margin:0;padding-left:18px;line-height:1.9">{mrows}</ul></section>')
    return summary + shops + missing


def _print_button():
    return '<button class="btn ghost small no-print" type="button" data-print>Skriv ut</button>'


def shopping_list(user, csrf, plan, items, names, checked_at, msg_kind='', msg='', budget=None, waiting=(),
                  full=None, shared=False, share_url=None, purchases=()):
    h = f'<input type="hidden" name="csrf" value="{e(csrf)}">'
    history_html = _purchases_html(purchases, names, csrf)
    if not items:
        body = f"""<h1>Inköpslista</h1><p class="lead">Lägg till filament med knappen <b>+ Inköpslista</b> på korten i katalogen.
Här ser du sedan var varje filament är billigast att köpa och vad allt kostar.</p>{message(msg_kind, e(msg))}
<p><a class="btn" href="/">Till katalogen</a></p>{history_html}"""
        return layout('Inköpslista', body, user, csrf, 'inkop', scripts=('/assets/receipt.js',))
    full = full or plan
    pending = not full.get('complete', True)
    pending_html = ('<div class="msg warn no-print" role="status">Listan är stor, så planen räknas fortfarande fram i bakgrunden. '
                    'Sidan laddas om automatiskt när en billigare fördelning kan finnas.</div>') if pending else ''

    # --- budget ---
    wait_html = ''
    if budget and waiting:
        wrows = ''.join(f'<li><b>{e(k.split("|")[0])} · {e(names.get(k, k.split("|")[-1]))}</b> ({q} st'
                        f'{", " + e(p) if p != "any" else ""})</li>' for k, q, p in waiting)
        wait_html = (f'<section class="card"><h2>Kan vänta</h2><p class="sub">Ryms inte i budgeten på {_kr(budget)}. '
                     f'Hela listan kostar {_kr(full["totals"]["total"])}. Det som inte är på rea just nu och är dyrast väntar först – '
                     f'håll utkik efter erbjudanden i katalogen.</p><ul style="margin:0;padding-left:18px;line-height:1.9">{wrows}</ul></section>')
    elif budget:
        wait_html = f'<div class="msg ok" role="status">Hela listan ryms i budgeten på {_kr(budget)}.</div>'
    budget_form = f"""<form method="post" action="/inkopslista/budget" class="row no-print" style="margin-top:12px">{h}
<div class="field" style="max-width:220px"><label for="budget">Budget (kr)</label>
<input id="budget" name="budget" type="text" inputmode="numeric" maxlength="9" value="{e(str(budget or ''))}" placeholder="Ingen gräns"></div>
<div><button class="btn ghost small" type="submit" name="do" value="spara">Spara budget</button>
{'<button class="btn danger small" type="submit" name="do" value="ta-bort">Ta bort budget</button>' if budget else ''}</div></form>"""

    # --- delning ---
    if share_url:
        share_box = (f'<p class="sub">Din länk (visas bara nu):</p><p><input type="text" readonly value="{e(share_url)}" '
                     f'aria-label="Delningslänk" style="width:100%"></p>')
    else:
        share_box = ('<p class="sub">Listan delas just nu via en länk. Skapa en ny länk om du har tappat bort den – den gamla slutar då gälla.</p>'
                     if shared else '<p class="sub">Skapa en länk som visar listan utan inloggning, t.ex. så att någon annan kan beställa åt dig. '
                                    'Den som har länken ser listan men kan inte ändra något.</p>')
    share_html = f"""<section class="card no-print"><h2>Dela listan</h2>{share_box}<div class="row">
<form method="post" action="/inkopslista/dela">{h}<button class="btn ghost small" type="submit">{'Skapa ny länk' if shared or share_url else 'Skapa delningslänk'}</button></form>
{f'<form method="post" action="/inkopslista/sluta-dela">{h}<button class="btn danger small" type="submit">Sluta dela</button></form>' if shared or share_url else ''}
{_print_button()}<a class="btn ghost small" href="/samkop">Samköp med andra</a></div></section>"""

    edit_rows = ''
    for key, qty, pack in items:
        label = names.get(key, key.split('|')[-1])
        typ = key.split('|')[0]
        opts = ''.join(f'<option value="{v}"{" selected" if v == pack else ""}>{t}</option>'
                       for v, t in (('any', 'Spole eller refill'), ('Spole', 'Spole'), ('Refill', 'Refill')))
        edit_rows += f"""<tr class="cart-row"><td><b>{e(typ)} · {e(label)}</b></td><td>
<form method="post" action="/inkopslista/rad" class="row cart-edit">{h}<input type="hidden" name="key" value="{e(key)}">
<input type="number" name="qty" value="{qty}" min="0" max="50" aria-label="Antal" style="width:74px;padding:8px">
<select name="pack" aria-label="Förpackning" style="width:auto;padding:8px">{opts}</select>
<button class="btn ghost small" type="submit" name="do" value="spara">Spara</button>
<button class="btn danger small" type="submit" name="do" value="ta-bort">Ta bort</button></form></td></tr>"""
    edit = f"""<section class="card no-print"><h2>Ändra listan</h2><p class="sub">Antal 0 tar bort raden. Välj förpackning om du bara vill ha spole eller refill.
Med en budget visas vad du kan köpa nu och vad som kan vänta.</p>
<div class="tablewrap"><table><tbody>{edit_rows}</tbody></table></div>{budget_form}
<form method="post" action="/inkopslista/tom" class="mt">{h}<button class="btn danger small" type="submit">Töm listan</button></form></section>"""
    plan_html = _plan_html(plan, checked_at, 'Köp nu' if budget and waiting else 'Uppskattat totalt', csrf) \
        if plan['totals']['units'] or plan['missing'] else ''
    body = (f'<h1>Inköpslista</h1><p class="lead">Var du bör köpa varje filament och vad det kostar totalt.</p>'
            f'{message(msg_kind, e(msg))}{pending_html}{wait_html}{plan_html}{edit}{share_html}{history_html}')
    return layout('Inköpslista', body, user, csrf, 'inkop', refresh=8 if pending else None, scripts=('/assets/print.js', '/assets/receipt.js'))


def shared_list(owner, plan, checked_at):
    if not plan:
        body = f'<h1>Inköpslista</h1><p class="lead">{e(owner)}s inköpslista är tom just nu.</p>'
    else:
        body = (f'<h1>Inköpslista</h1><p class="lead">Delad av <b>{e(owner)}</b>. Var varje filament är billigast att köpa och vad det kostar. '
                f'Listan uppdateras när {e(owner)} ändrar den.</p><p class="no-print">{_print_button()}</p>{_plan_html(plan, checked_at)}')
    return layout('Delad inköpslista', body, scripts=('/assets/print.js',))


def _purchases_html(purchases, names, csrf=''):
    """Köphistorik grupperad per köp (tid + butik), med kvitto per köp."""
    if not purchases:
        return ''
    year = datetime.datetime.now(TZ).year
    spent = sum(p['qty'] * p['unit_price'] for p in purchases
                if datetime.datetime.fromtimestamp(p['ts'], TZ).year == year)
    groups = {}
    for p in purchases:
        groups.setdefault((p['ts'], p['shop']), []).append(p)
    rows = ''
    for (ts, shop), ps in groups.items():
        first = ps[0]
        items = '<br>'.join(f'<b>{e(p["key"].split("|")[0])} · {e(names.get(p["key"], p["key"].split("|")[-1]))}</b>'
                            f'<span class="muted" style="font-size:12px"> {e(p["pack"] or "")} · {p["qty"]} × {_kr(p["unit_price"])}</span>'
                            for p in ps)
        if first['receipt']:
            receipt = (f'<a href="/kvitto/{first["id"]}" target="_blank" rel="noopener">📎 Visa kvitto</a>'
                       f'<form method="post" action="/kvitto/{first["id"]}/ta-bort" style="display:inline;margin-left:8px">'
                       f'<input type="hidden" name="csrf" value="{e(csrf)}"><button class="btn danger small" type="submit">Ta bort</button></form>')
        else:
            receipt = (f'<label class="btn ghost small receipt-upload">📎 Lägg till kvitto<input type="file" accept="image/*,application/pdf" '
                       f'data-receipt="{first["id"]}" hidden></label><span class="muted receipt-status" style="font-size:12px"></span>')
        rows += (f'<tr><td>{fmt_time(ts)[:10]}</td><td>{e(shop)}</td><td>{items}</td>'
                 f'<td class="right">{_kr(sum(p["qty"] * p["unit_price"] for p in ps))}</td><td class="right">{receipt}</td></tr>')
    return (f'<section class="card no-print" id="kophistorik"><details><summary><b>Köphistorik</b> · {_kr(spent)} på filament i år (utan frakt)</summary>'
            f'<p class="sub mt">Fotografera eller ladda upp kvittot för garanti och reklamationer. Bilder förminskas i webbläsaren innan de sparas.</p>'
            f'<div class="tablewrap"><table><thead><tr><th>Datum</th><th>Butik</th><th>Filament</th><th class="right">Summa</th><th class="right">Kvitto</th></tr></thead>'
            f'<tbody>{rows}</tbody></table></div></details></section>')


def wish_page(user, csrf, rows, names, notify, notify_enabled, checked_at, msg_kind='', msg='', notify_new=False, calendar=None):
    h = f'<input type="hidden" name="csrf" value="{e(csrf)}">'
    lead = ('<p class="lead">Färger du vill köpa senare. Sätt ett målpris (inklusive frakt) eller en minsta rabatt, '
            'så bevakas priset vid varje prisuppdatering.</p>')
    if notify_enabled:
        notify_html = f"""<section class="card"><h2>Avisering till mobilen</h2>
<p class="sub">Installera appen <b>ntfy</b> (gratis, Android och iPhone), prenumerera på ett eget, svårgissat ämne och skriv in det här.
Du får en avisering när en bevakning når ditt pris – en gång per träff, och igen bara om priset sjunker mer.
Ämnet är inte hemligt för ntfy, så välj ett namn som inte avslöjar vem du är.</p>
<form method="post" action="/onskelista/avisering" class="row">{h}
<div class="field" style="max-width:360px"><label for="nt">ntfy-ämne eller adress</label>
<input id="nt" name="target" type="text" maxlength="200" value="{e(notify or '')}" placeholder="t.ex. filament-a8k2m" autocomplete="off" spellcheck="false"></div>
<div><button class="btn ghost small" type="submit" name="do" value="spara">Spara</button>
<button class="btn ghost small" type="submit" name="do" value="testa">Spara och skicka test</button>
{'<button class="btn danger small" type="submit" name="do" value="ta-bort">Stäng av</button>' if notify else ''}</div></form>
<form method="post" action="/onskelista/nyheter" class="mt">{h}<input type="hidden" name="on" value="{'0' if notify_new else '1'}">
<p class="sub" style="margin-bottom:8px">{'Du aviseras när Bambu Lab får nya färger eller filamenttyper.' if notify_new else 'Vill du också få en avisering när Bambu Lab får nya färger eller filamenttyper?'}
{'' if notify else ' Ange ett ntfy-ämne ovan först.'}</p>
<button class="btn ghost small" type="submit"{'' if notify or notify_new else ' disabled'}>{'Sluta avisera om nya färger' if notify_new else 'Avisera om nya färger'}</button></form></section>"""
    else:
        notify_html = ''
    if not rows:
        body = (f'<h1>Önskelista</h1>{lead}{message(msg_kind, e(msg))}<p>Lägg till färger med knappen <b>♡ Önskelista</b> '
                f'i katalogen.</p><p><a class="btn" href="/">Till katalogen</a></p>{notify_html}')
        return layout('Önskelista', body, user, csrf, 'onske')
    rows = sorted(rows, key=lambda r: (not r['hit'], r['key']))
    trs = ''
    for r in rows:
        k, info, a = r['key'], r['info'], r['alert']
        name = f'{e(k.split("|")[0])} · {e(names.get(k, k.split("|")[-1]))}'
        if info['best']:
            o, t = info['best']
            now = (f'<b>{_kr(t)}</b>{timing_badge(o)}<br><span class="muted" style="font-size:12px">{e(o["shop"].split(" · ")[0])} · '
                   f'{e(o.get("pack", ""))} · inkl. frakt</span> <a href="{_safe_href(o.get("url"))}" target="_blank" rel="noopener" '
                   f'style="font-size:12px">Till butiken ↗</a>')
        else:
            now = '<span class="muted">Inget jämförbart pris just nu</span>'
        if info['deal']:
            now += f'<br><span class="badge bad">−{info["deal"]["discount"]} %</span> <span class="muted" style="font-size:12px">{e(info["deal"]["shop"].split(" · ")[0])}</span>'
        low = _kr(r['low90']) if r['low90'] else '—'
        if r.get('tip'):
            now += f'<br><span class="badge run" style="white-space:normal">📅 {e(r["tip"])}</span>'
        hit = '<span class="badge ok">🔔 Ditt pris är nått</span><br>' if r['hit'] else ''
        tv = '' if not a or a['target'] is None else str(a['target'])
        dv = '' if not a or a['min_discount'] is None else str(a['min_discount'])
        trs += f"""<tr{' class="hit"' if r['hit'] else ''}><td><b>{name}</b></td><td>{now}</td><td class="hide-sm">{low}</td><td>{hit}
<form method="post" action="/onskelista/bevaka" class="row cart-edit">{h}<input type="hidden" name="key" value="{e(k)}">
<input name="target" type="text" inputmode="numeric" maxlength="7" value="{e(tv)}" placeholder="Målpris kr" aria-label="Målpris i kronor inklusive frakt" style="width:110px;padding:8px">
<input name="discount" type="text" inputmode="numeric" maxlength="3" value="{e(dv)}" placeholder="Rabatt %" aria-label="Minsta rabatt i procent" style="width:90px;padding:8px">
<button class="btn ghost small" type="submit">Spara</button></form>
<form method="post" action="/onskelista/ta-bort" style="margin-top:6px">{h}<input type="hidden" name="key" value="{e(k)}"><button class="btn danger small" type="submit">Ta bort</button></form></td></tr>"""
    n_hits = sum(r['hit'] for r in rows)
    summary = (f'<div class="msg ok" role="status">{n_hits} {"färg" if n_hits == 1 else "färger"} har nått ditt pris just nu.</div>'
               if n_hits else '')
    table = (f'<section class="card"><div class="tablewrap"><table><thead><tr><th>Färg</th><th>Lägsta pris nu</th>'
             f'<th class="hide-sm">Lägst 90 dagar</th><th>Bevakning</th></tr></thead><tbody>{trs}</tbody></table></div>'
             f'<p class="muted" style="font-size:12px;margin:12px 0 0">Målpriset gäller lägsta jämförbara pris inklusive frakt (som i katalogen). '
             f'Lämna båda fälten tomma för att sluta bevaka. Priser från {e(_when(checked_at)) or "senaste sökningen"}.</p></section>')
    cal = message('warn', '📅 ' + e(calendar) + ' Vänta med det som inte är bråttom.') if calendar else ''
    body = f'<h1>Önskelista</h1>{lead}{message(msg_kind, e(msg))}{summary}{cal}{table}{notify_html}'
    return layout('Önskelista', body, user, csrf, 'onske')


# ---------- kostnadsräknare ----------
def _num(v, d=2):
    return f'{v:,.{d}f}'.replace(',', ' ').replace('.', ',')


def _hm(hours):
    h, m = divmod(round(hours * 60), 60)
    return f'{h} h {m:02d} min' if h else f'{m} min'


def _day_time(t, now):
    days = (t.date() - now.date()).days
    day = {0: 'i dag', 1: 'i morgon', -1: 'i går'}.get(days, t.strftime('%Y-%m-%d'))
    return f'{day} {t:%H:%M}'


def _price_chart(intervals, res):
    """Elpris per intervall från nu och framåt; utskriftens fönster och det billigaste fönstret markeras."""
    now = res['now']
    pts = [(s, e, p) for s, e, p in intervals if e > now - datetime.timedelta(hours=1)]
    if len(pts) < 2:
        return ''
    x0, x1 = pts[0][0], pts[-1][1]
    lo, hi = min(0, min(p for _, _, p in pts)), max(p for _, _, p in pts) or 1
    W, H, L, B, T = 760, 170, 46, 22, 8
    X = lambda t: L + (t - x0).total_seconds() / (x1 - x0).total_seconds() * (W - L - 4)  # noqa: E731
    Y = lambda v: T + (1 - (v - lo) / (hi - lo or 1)) * (H - T - B)  # noqa: E731
    ws, we = res['start'], res['start'] + datetime.timedelta(hours=res['hours'])
    cs = res['cheapest'][0] if res.get('cheapest') else None
    ce = cs + datetime.timedelta(hours=res['hours']) if cs else None
    bars = ''
    for s, e, p in pts:
        cls = 'win' if s < we and e > ws else ('cheap' if cs and s < ce and e > cs else 'base')
        bars += (f'<rect class="{cls}" x="{X(s) + .5:.1f}" y="{Y(p):.1f}" width="{max(.8, X(e) - X(s) - 1):.1f}" '
                 f'height="{max(0, Y(lo) - Y(p)):.1f}" rx="1"><title>{s:%H:%M}–{e:%H:%M}: {_num(p)} kr/kWh</title></rect>')
    ticks = ''
    step = (hi - lo) / 3
    for i in range(4):
        v = lo + step * i
        ticks += f'<line x1="{L}" x2="{W}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" class="grid"/><text x="{L - 6}" y="{Y(v) + 4:.1f}" text-anchor="end" class="axis">{_num(v)}</text>'
    t = x0.replace(minute=0, second=0, microsecond=0)
    while t <= x1:
        if t >= x0 and t.hour % 6 == 0:
            ticks += f'<text x="{X(t):.1f}" y="{H - 6}" text-anchor="middle" class="axis">{t:%H}:00{" " + t.strftime("%d/%m") if t.hour == 0 else ""}</text>'
        t += datetime.timedelta(hours=1)
    legend = ('<ul class="legend"><li><span class="dot win"></span>Din utskrift</li>'
              + ('<li><span class="dot cheap"></span>Billigaste starttid</li>' if cs and cs != ws else '') +
              '<li><span class="dot base"></span>Övriga tider</li></ul>')
    rows = ''.join(f'<tr><td>{_day_time(s, now)}</td><td class="right">{_num(p)} kr/kWh</td></tr>' for s, e, p in pts)
    return (f'<svg viewBox="0 0 {W} {H}" class="el-chart" role="img" aria-label="Elpris per kvart eller timme">{ticks}{bars}</svg>{legend}'
            f'<details><summary>Visa elpriserna som tabell</summary><div class="tablewrap mt" style="max-height:260px;overflow:auto">'
            f'<table><tbody>{rows}</tbody></table></div></details>')


def cost_page(user, csrf, form, names, res, intervals, errors, has_tibber, msg_kind='', msg='', live=None, projects=()):
    from . import costcalc, energy
    h = f'<input type="hidden" name="csrf" value="{e(csrf)}">'
    by_type = {}
    for k in sorted(names, key=lambda k: (k.split('|')[0], names[k])):
        by_type.setdefault(k.split('|')[0], []).append(k)

    def color_select(i, selected):
        opts = ''.join(f'<optgroup label="{e(t)}">' + ''.join(
            f'<option value="{e(k)}"{" selected" if k == selected else ""}>{e(t)} · {e(names[k])}</option>' for k in ks) + '</optgroup>'
            for t, ks in by_type.items())
        return (f'<select name="k{i}" aria-label="Färg {i}" class="cost-color"><option value="">Annan färg / eget pris</option>{opts}</select>')
    rows = ''
    for i, (k, g) in enumerate(form['rows'], 1):
        row = (f'<tr class="cost-row"><td>{color_select(i, k)}</td><td><input name="g{i}" type="text" inputmode="decimal" maxlength="8" '
               f'value="{e(g)}" aria-label="Gram filament {i}" placeholder="gram" style="width:100px"></td></tr>')
        rows += row if i <= 4 else ''
    more = ''.join(
        f'<tr class="cost-row"><td>{color_select(i, k)}</td><td><input name="g{i}" type="text" inputmode="decimal" maxlength="8" '
        f'value="{e(g)}" aria-label="Gram filament {i}" placeholder="gram" style="width:100px"></td></tr>'
        for i, (k, g) in enumerate(form['rows'], 1) if i > 4)
    more_open = any(g for k, g in form['rows'][4:])
    sel = lambda name, options, cur: ''.join(f'<option value="{e(v)}"{" selected" if v == cur else ""}>{e(t)}</option>' for v, t in options)  # noqa: E731
    printers = ''.join(f'<option value="{e(pid)}" data-watts="{w}"{" selected" if pid == form.get("printer") else ""}>{e(n)} · ca {w} W</option>'
                       for pid, n, w in costcalc.PRINTERS)
    sources = [(v, t + ('' if v != 'tibber' or has_tibber else ' – koppla nedan')) for v, t in costcalc.SOURCES.items()]
    form_html = f"""<form method="post" action="/kostnad/rakna" id="costForm">{h}
<section class="card"><h2>Utskriften</h2>
<p class="sub">Skriv in vad slicern (Bambu Studio, OrcaSlicer) visar, eller läs in filen nedan. Flera färger = en rad per färg.</p>
<div class="row" style="margin-bottom:14px"><div class="field"><label for="costFile">Läs in från slicer-fil (.gcode.3mf eller .gcode)</label>
<input id="costFile" type="file" accept=".3mf,.gcode,.gco"></div></div><p id="costFileStatus" class="muted" role="status" style="font-size:13px;margin:-6px 0 12px"></p>
<div class="tablewrap"><table><thead><tr><th>Färg</th><th>Gram</th></tr></thead><tbody id="costRows">{rows}</tbody></table></div>
<details{' open' if more_open else ''}><summary>Fler färger (5–8)</summary><div class="tablewrap mt"><table><tbody>{more}</tbody></table></div></details>
<div class="row mt"><div class="field" style="max-width:120px"><label for="ch">Tid, timmar</label><input id="ch" name="h" type="text" inputmode="numeric" maxlength="5" value="{e(form['h'])}"></div>
<div class="field" style="max-width:120px"><label for="cm">minuter</label><input id="cm" name="m" type="text" inputmode="numeric" maxlength="5" value="{e(form['m'])}"></div>
<div class="field" style="max-width:240px"><label for="cs">Starttid</label><input id="cs" name="start" type="datetime-local" value="{e(form['start'])}"></div></div>
<p class="muted" style="font-size:12px;margin:6px 0 0">Lämna starttiden tom för att starta nu.</p></section>
<section class="card"><h2>Skrivare och el</h2>
<div class="row"><div class="field"><label for="cp">Skrivare</label><select id="cp" name="printer" class="cost-printer"><option value="">Välj för att fylla i effekt…</option>{printers}</select></div>
<div class="field" style="max-width:160px"><label for="cw">Medeleffekt (W)</label><input id="cw" name="w" type="text" inputmode="numeric" maxlength="5" value="{e(form['watts'])}"></div></div>
<p class="muted" style="font-size:12px;margin:8px 0 14px">Ungefärlig medeleffekt med PLA. Varm bädd (ABS/ASA, kammare) drar mer – mät gärna med en energimätare för exakt värde.</p>
<div class="row"><div class="field"><label for="csrc">Elpris från</label><select id="csrc" name="src">{sel('src', sources, form['source'])}</select></div>
<div class="field" style="max-width:150px"><label for="carea">Elområde</label><select id="carea" name="area">{sel('area', [(a, a) for a in energy.AREAS], form['area'])}</select></div>
<div class="field" style="max-width:220px"><label for="cvat">Spotpris</label><select id="cvat" name="vat">{sel('vat', [('1', 'Lägg på 25 % moms'), ('0', 'Utan moms')], form['vat'])}</select></div></div>
<div class="row mt"><div class="field" style="max-width:260px"><label for="cextra">Tillägg (kr/kWh)</label><input id="cextra" name="extra" type="text" inputmode="decimal" maxlength="6" value="{e(form['extra'])}" placeholder="t.ex. 0,85"></div>
<div class="field" style="max-width:200px"><label for="cfixed">Fast pris (kr/kWh)</label><input id="cfixed" name="fixed" type="text" inputmode="decimal" maxlength="6" value="{e(form['fixed'])}"></div></div>
<p class="muted" style="font-size:12px;margin:8px 0 0">Tillägget läggs på varje kWh: nätavgift (rörlig del) och – för spotpris – energiskatt och elhandlarens påslag.
Tibbers pris innehåller redan energiskatt och moms men inte nätavgiften.</p></section>
<section class="card"><h2>Filamentpris</h2>
<div class="row"><div class="field"><label for="cmode">Räkna med</label><select id="cmode" name="mode">{sel('mode', list(costcalc.MODES.items()), form['mode'])}</select></div>
<div class="field" style="max-width:200px"><label for="ckg">Eget pris (kr/kg)</label><input id="ckg" name="kg" type="text" inputmode="decimal" maxlength="7" value="{e(form['kg'])}" placeholder="t.ex. 229"></div>
<div class="field" style="max-width:220px"><label for="cwear">Slitage (kr/timme)</label><input id="cwear" name="wear" type="text" inputmode="decimal" maxlength="6" value="{e(form['wear'])}" placeholder="valfritt"></div></div>
<p class="muted" style="font-size:12px;margin:8px 0 0">Eget pris används för rader utan färg och när katalogen saknar pris. Slitage: t.ex. munstycke, PEI-platta och avskrivning per drifttimme.</p></section>
<div class="row" style="margin-bottom:22px"><button class="btn" type="submit" name="do" value="rakna">Räkna ut</button>
<button class="btn ghost" type="submit" name="do" value="spara">Räkna ut och spara som standard</button></div>
<section class="card"><h2>Spara eller sälj</h2>
<div class="row"><div class="field"><label for="cname">Namn på utskriften</label><input id="cname" name="name" type="text" maxlength="120" value="{e(form.get('name', ''))}" placeholder="t.ex. Hylla till verkstan"></div>
{f'<div class="field" style="max-width:220px"><label for="cproj">Projekt</label><select id="cproj" name="project"><option value="">Inget</option>' + ''.join(f'<option value="{p["id"]}"{" selected" if str(p["id"]) == form.get("project") else ""}>{e(p["name"])}</option>' for p in projects) + '</select></div>' if projects else ''}
<div><button class="btn ghost" type="submit" name="do" value="logga">Spara i utskriftsloggen</button></div></div>
<h3 class="mt" style="margin-top:22px">Offert</h3><p class="sub">Självkostnad plus marginal för misslyckade utskrifter och påslag – för utskrifter du säljer.</p>
<div class="row"><div class="field"><label for="qcust">Kund / beskrivning</label><input id="qcust" name="customer" type="text" maxlength="120" value="{e(form.get('customer', ''))}"></div>
<div class="field" style="max-width:140px"><label for="qmark">Påslag (%)</label><input id="qmark" name="markup" type="text" inputmode="decimal" maxlength="5" value="{e(form.get('markup') or '50')}"></div>
<div class="field" style="max-width:170px"><label for="qfail">Misslyckanden (%)</label><input id="qfail" name="fail" type="text" inputmode="decimal" maxlength="5" value="{e(form.get('fail') or '10')}"></div>
<div class="field" style="max-width:170px"><label for="qvat">Moms</label><select id="qvat" name="qvat">{sel('qvat', [('0', 'Utan moms'), ('1', 'Lägg på 25 %')], form.get('qvat', '0'))}</select></div>
<div class="field" style="max-width:150px"><label for="qrnd">Avrunda till</label><select id="qrnd" name="rnd">{sel('rnd', [('1', '1 kr'), ('5', '5 kr'), ('10', '10 kr')], form.get('rnd', '1'))}</select></div></div>
<div class="mt"><button class="btn ghost" type="submit" name="do" value="offert">Skapa offert</button></div></section></form>"""

    result = ''
    if errors:
        result += ''.join(message('err', e(x)) for x in errors)
    if res:
        el = res['el']
        lines = ''
        for l in res['lines']:
            k = l['key']
            name = f'{e(k.split("|")[0])} · {e(names.get(k, ""))}' if k else 'Annan färg'
            lines += (f'<tr><td><b>{name}</b><br><span class="muted" style="font-size:12px">{e(l["note"])}</span></td>'
                      f'<td class="right">{_num(l["grams"], 1)} g</td>'
                      f'<td class="right">{_num(l["per_kg"], 0) + " kr/kg" if l["per_kg"] is not None else "—"}</td>'
                      f'<td class="right"><b>{_num(l["cost"]) + " kr" if l["cost"] is not None else "—"}</b></td></tr>')
        cheap = ''
        c = res.get('cheapest')
        if c and res['hours']:
            saving = el['cost'] - c[1]['cost']
            if saving > 0.005 and abs((c[0] - res['start']).total_seconds()) > 60:
                cheap = (f'<div class="msg ok" role="status">💡 Billigast att starta <b>{_day_time(c[0], res["now"])}</b>: elen kostar då '
                         f'{_num(c[1]["cost"])} kr, {_num(saving)} kr mindre ({_num(100 * saving / el["cost"], 0) if el["cost"] else "–"} %).</div>')
            else:
                cheap = '<div class="msg ok" role="status">Starttiden är redan den billigaste bland kända elpriser.</div>'
        covered = '' if el['covered'] >= 0.999 or not res['source'] else (
            f'<div class="msg warn" role="status">Elpriser finns bara för {_num(100 * el["covered"], 0)} % av utskriften – '
            f'resten är uppskattad med medelpriset. Morgondagens priser publiceras runt kl. 13.</div>')
        end = res['start'] + datetime.timedelta(hours=res['hours'])
        rng = f' · {_num(el["low"])}–{_num(el["high"])} kr/kWh' if el['low'] is not None else ''
        result += f"""<section class="card" id="resultat"><h2>Kostnad för utskriften</h2><div class="stats">
<div class="stat"><span>Totalt</span><strong style="font-size:24px">{_num(res['total'])} kr</strong></div>
<div class="stat"><span>Filament ({_num(res['grams'], 1)} g)</span><strong>{_num(res['filament'])} kr</strong></div>
<div class="stat"><span>El ({_num(el['kwh'], 2)} kWh)</span><strong>{_num(el['cost'])} kr</strong></div>
{f'<div class="stat"><span>Slitage</span><strong>{_num(res["wear"])} kr</strong></div>' if res['wear'] else ''}
{f'<div class="stat"><span>Per gram</span><strong>{_num(res["per_gram"], 3)} kr</strong></div>' if res['per_gram'] else ''}</div>
{message('warn', 'Några rader saknar pris och ingår inte i totalen – ange ett eget pris per kg.') if res['missing'] else ''}
{''.join(message('err', f'⚠ {k.split("|")[0]} {names.get(k, "")} räcker inte: utskriften behöver {_num(need, 0)} g, du har {_num(have, 0)} g kvar enligt Bambu-biblioteket/Spoolman.') for k, need, have in res.get('shortage') or [])}
{f'<div class="tablewrap"><table><thead><tr><th>Filament</th><th class="right">Mängd</th><th class="right">Pris</th><th class="right">Kostnad</th></tr></thead><tbody>{lines}</tbody></table></div>' if lines else ''}
<p class="sub" style="margin:14px 0 0">El: {_hm(res['hours'])} × {_num(res['watts'], 0)} W, {_day_time(res['start'], res['now'])}–{end:%H:%M}{rng}.
{e(res['source']) or 'Inget elpris.'}</p></section>"""
        if res['hours'] and intervals:
            result += f'<section class="card"><h2>Elpris</h2>{cheap}{covered}{_price_chart(intervals, res)}</section>'
        elif cheap or covered:
            result += f'<section class="card">{cheap}{covered}</section>'

    now_html = ''
    if live or has_tibber:
        parts = []
        if live:
            parts.append(f'<div class="stat"><span>Elpris just nu</span><strong>{_num(live["now"])} kr/kWh</strong></div>')
            if live['low']:
                parts.append(f'<div class="stat"><span>Lägst närmaste 12 h</span><strong>{_num(live["low"][1])} kr/kWh</strong>'
                             f'<span>{_day_time(live["low"][0], live["at"])}</span></div>')
        if has_tibber:
            parts.append('<div class="stat" id="livePower" hidden><span>Hemmet drar nu (Tibber Pulse)</span><strong>–</strong><span></span></div>')
        advice = ''
        if live and live['drop']:
            t, p = live['drop']
            mins = max(1, round((t - live['at']).total_seconds() / 60))
            advice = message('warn', f'⏳ Vänta med att starta? Elpriset sjunker {_day_time(t, live["at"])} (om {mins} min) till '
                                     f'{_num(p)} kr/kWh, {_num(100 * (1 - p / live["now"]), 0)} % billigare än nu.')
        elif live:
            advice = message('ok', 'Elpriset sjunker inte nämnvärt de närmaste timmarna – bra läge att starta nu.')
        now_html = f'<section class="card" id="justnu"><h2>Just nu</h2><div class="stats">{"".join(parts)}</div>{advice}</section>'
    tibber = f"""<section class="card" id="tibber"><h2>Tibber</h2>
<p class="sub">{'Tibber är kopplat. Tokenet visas aldrig igen; ta bort och klistra in ett nytt för att byta.' if has_tibber else
'Hämta ditt eget elpris från Tibber: logga in på <a href="https://developer.tibber.com" target="_blank" rel="noopener">developer.tibber.com</a>, '
'välj <i>Your token</i> och klistra in det här. Tokenet ger läsåtkomst till ditt Tibber-konto och sparas bara på ditt konto här.'}</p>
<form method="post" action="/kostnad/tibber" class="row">{h}
{'' if has_tibber else '<div class="field" style="max-width:420px"><label for="tt">Tibber-token</label><input id="tt" name="token" type="password" autocomplete="off" maxlength="200"></div><div><button class="btn ghost small" type="submit" name="do" value="spara">Koppla Tibber</button></div>'}
{'<div><button class="btn danger small" type="submit" name="do" value="ta-bort">Ta bort Tibber-kopplingen</button></div>' if has_tibber else ''}</form></section>"""
    body = (f'<h1>Kostnadsräknare</h1><p class="lead">Vad kostar en utskrift? Filament efter katalogens priser eller vad du betalat, '
            f'och el efter timpriset under utskriften – med förslag på billigaste starttid.</p>{message(msg_kind, e(msg))}'
            f'{now_html}{result}{form_html}{tibber}')
    return layout('Kostnadsräknare', body, user, csrf, 'kostnad', scripts=('/assets/cost.js',))


# ---------- utskriftslogg och offert ----------
PRINT_STATUS = {'ok': '', 'failed': '<span class="badge bad">Misslyckad</span>', 'cancelled': '<span class="badge warn">Avbruten</span>'}


def prints_page(user, csrf, prints, stats, forecast, names, has_bambu, msg_kind='', msg='', projects=()):
    from .printlog import REASONS
    h = f'<input type="hidden" name="csrf" value="{e(csrf)}">'
    label = lambda k: f'{e(k.split("|")[0])} · {e(names.get(k, k.split("|")[-1]))}' if k else 'Annan färg'  # noqa: E731
    y = stats['year']
    fetch = (f'<form method="post" action="/utskrifter/hamta" class="no-print" style="margin-bottom:18px">{h}'
             f'<button class="btn ghost" type="submit">Hämta utskrifter från Bambu Lab</button> '
             f'<span class="muted" style="font-size:13px">Hämtas även automatiskt vid varje Bambu-synk.</span></form>') if has_bambu else \
        '<p class="sub">Koppla ditt Bambu Lab-konto under <a href="/konto">Mitt konto</a>, så hämtas dina utskrifter automatiskt.</p>'
    if not prints:
        body = (f'<h1>Utskrifter</h1><p class="lead">Logg över dina utskrifter med kostnad för filament och el.</p>{message(msg_kind, e(msg))}'
                f'{fetch}<p>Inga utskrifter än. Spara en från <a href="/kostnad">kostnadsräknaren</a>.</p>')
        return layout('Utskrifter', body, user, csrf, 'utskrifter')
    summary = f"""<section class="card"><h2>I år</h2><div class="stats">
<div class="stat"><span>Utskrifter</span><strong>{y['n']}</strong>{f'<span>{y["failed"]} misslyckade/avbrutna</span>' if y['failed'] else ''}</div>
<div class="stat"><span>Filament</span><strong>{_num(y['grams'] / 1000, 2)} kg</strong></div>
<div class="stat"><span>Kostnad totalt</span><strong>{_num(y['total'], 0 if y['total'] >= 100 else 2)} kr</strong></div>
<div class="stat"><span>varav el</span><strong>{_num(y['el'], 0 if y['el'] >= 100 else 2)} kr</strong></div>
{f'<div class="stat"><span>Bortkastat i misslyckade</span><strong>{_num(y["wasted"], 0 if y["wasted"] >= 100 else 2)} kr</strong><span>{_num(y["wasted_g"], 0)} g filament</span></div>' if y['failed'] else ''}</div></section>"""
    fails = ''
    if stats['reasons']:
        rr = ''.join(f'<tr><td>{e(k)}</td><td class="right">{v["n"]}</td><td class="right">{_num(v["grams"], 0)} g</td><td class="right">{_num(v["cost"])} kr</td></tr>'
                     for k, v in sorted(stats['reasons'].items(), key=lambda kv: -kv[1]['n']))
        ft = ''.join(f'<tr><td>{e(k)}</td><td class="right">{v["failed"]} av {v["n"]}</td><td class="right">{_num(100 * v["failed"] / v["n"], 0)} %</td></tr>'
                     for k, v in sorted(stats['fail_types'].items(), key=lambda kv: -(kv[1]['failed'] / kv[1]['n'])) if v['n'])
        pf = ''.join(f'<tr><td>{e(k)}</td><td class="right">{v["failed"]} av {v["n"]}</td><td class="right">{_num(100 * v["failed"] / v["n"], 0)} %</td></tr>'
                     for k, v in stats['printers'].items() if v['n'])
        fails = f"""<section class="card"><h2>Misslyckade utskrifter</h2><p class="sub">Ange orsak på varje misslyckad utskrift nedan, så syns var problemen finns.</p>
<div class="tablewrap"><table><thead><tr><th>Orsak</th><th class="right">Antal</th><th class="right">Filament</th><th class="right">Kostnad</th></tr></thead><tbody>{rr}</tbody></table></div>
<div class="row mt" style="align-items:flex-start"><div class="tablewrap" style="flex:1;min-width:260px"><table><thead><tr><th>Material</th><th class="right">Misslyckade</th><th class="right">Andel</th></tr></thead><tbody>{ft}</tbody></table></div>
<div class="tablewrap" style="flex:1;min-width:260px"><table><thead><tr><th>Skrivare</th><th class="right">Misslyckade</th><th class="right">Andel</th></tr></thead><tbody>{pf}</tbody></table></div></div></section>"""
    months = ''.join(f'<tr><td>{e(m)}</td><td class="right">{v["n"]}</td><td class="right">{_num(v["grams"], 0)} g</td>'
                     f'<td class="right">{_num(v["total"])} kr</td></tr>' for m, v in stats['months'].items())
    colors = ''.join(
        f'<tr><td><b>{label(k)}</b></td><td class="right">{_num(v["grams"], 0)} g</td><td class="right">{_num(v["cost"])} kr</td>'
        f'<td class="right">{(_num(forecast[k]["perDay"], 1) + " g/dag") if k in forecast else "—"}</td>'
        f'<td class="right">{("ca " + str(forecast[k]["days"]) + " dagar") if forecast.get(k, {}).get("days") is not None else "—"}</td></tr>'
        for k, v in sorted(stats['colors'].items(), key=lambda kv: -kv[1]['grams']))
    printers = ''.join(f'<tr><td>{e(p)}</td><td class="right">{v["n"]}</td><td class="right">{_num(v["hours"], 1)} h</td>'
                       f'<td class="right">{_num(v["kwh"], 1)} kWh</td><td class="right">{_num(v["total"])} kr</td></tr>'
                       for p, v in stats['printers'].items())
    rows = ''
    for r in prints:
        grams = json.loads(r['grams'] or '{}')
        reason = f' <span class="muted" style="font-size:12px">({e(r["reason"])})</span>' if r['reason'] else ''
        proj = next((f' <span class="badge">{e(pr["name"])}</span>' for pr in projects if pr['id'] == r['project_id']), '')
        rows += (f'<tr id="u{r["id"]}"><td>{fmt_time(r["ts"])}</td><td><b>{e(r["name"])}</b> {PRINT_STATUS.get(r["status"], "")}{reason}{proj}'
                 f'<br><span class="muted" style="font-size:12px">{" · ".join(f"{label(k)} {_num(g, 1)} g" for k, g in grams.items())}'
                 f'{" · " + e(r["printer"]) if r["printer"] else ""}{" · från Bambu Lab" if r["source"] == "bambu" else ""}</span></td>'
                 f'<td class="right hide-sm">{_hm(r["hours"])}</td><td class="right">{_num(r["total"])} kr'
                 f'<br><span class="muted" style="font-size:12px">el {_num(r["el"])} kr</span></td>'
                 f'<td class="right">{_print_edit(r, h, REASONS, projects)}<form method="post" action="/utskrifter/{r["id"]}/ta-bort" style="margin-top:6px">{h}'
                 f'<button class="btn danger small" type="submit" aria-label="Ta bort {e(r["name"])}">Ta bort</button></form></td></tr>')
    body = f"""<h1>Utskrifter</h1><p class="lead">Logg över dina utskrifter med kostnad för filament och el, och hur länge filamentet räcker.</p>
{message(msg_kind, e(msg))}{fetch}{summary}{fails}
<section class="card"><h2>Förbrukning per färg</h2><p class="sub">Prognosen bygger på de senaste {60} dagarnas utskrifter och vad som finns kvar enligt ditt Bambu-bibliotek.</p>
<div class="tablewrap"><table><thead><tr><th>Färg</th><th class="right">Totalt</th><th class="right">Kostnad</th><th class="right">Takt</th><th class="right">Räcker</th></tr></thead><tbody>{colors}</tbody></table></div></section>
<section class="card"><h2>Per månad</h2><div class="tablewrap"><table><thead><tr><th>Månad</th><th class="right">Utskrifter</th><th class="right">Filament</th><th class="right">Kostnad</th></tr></thead><tbody>{months}</tbody></table></div>
<h2 class="mt" style="margin-top:22px">Per skrivare</h2><div class="tablewrap"><table><thead><tr><th>Skrivare</th><th class="right">Utskrifter</th><th class="right">Tid</th><th class="right">El</th><th class="right">Kostnad</th></tr></thead><tbody>{printers}</tbody></table></div></section>
<section class="card"><h2>Alla utskrifter</h2><div class="tablewrap"><table><thead><tr><th>Start</th><th>Utskrift</th><th class="right hide-sm">Tid</th><th class="right">Kostnad</th><th></th></tr></thead><tbody>{rows}</tbody></table></div>
<p class="muted" style="font-size:12px;margin:12px 0 0">Utskrifter från Bambu Lab räknas med dina standardvärden i kostnadsräknaren och spotpriset för dagen (Tibbers historiska priser hämtas inte).</p></section>"""
    return layout('Utskrifter', body, user, csrf, 'utskrifter')


def quote_page(user, csrf, form, res, q, names):
    lines = ''.join(
        f'<tr><td>{(e(l["key"].split("|")[0]) + " · " + e(names.get(l["key"], ""))) if l["key"] else "Annat filament"}</td>'
        f'<td class="right">{_num(l["grams"], 1)} g</td></tr>' for l in res['lines'])
    vat_row = f'<tr><td>Moms 25 %</td><td class="right">{_num(q["vat"])} kr</td></tr>' if q['vat'] else ''
    today = datetime.datetime.now(TZ).strftime('%Y-%m-%d')
    body = f"""<p class="no-print"><a href="/kostnad">← Tillbaka till kostnadsräknaren</a> <button class="btn ghost small" type="button" data-print>Skriv ut / spara som PDF</button></p>
<section class="card quote"><div class="row" style="justify-content:space-between;align-items:baseline"><h1 style="margin:0">Offert</h1><span class="muted">{today}</span></div>
{f'<p class="lead" style="margin:8px 0 18px">{e(form.get("customer"))}</p>' if form.get('customer') else ''}
<p><b>{e(form.get('name') or '3D-utskrift')}</b> · {_hm(res['hours'])} utskriftstid · {_num(res['grams'], 1)} g filament</p>
<div class="tablewrap"><table><tbody>{lines}</tbody></table></div>
<div class="tablewrap mt"><table><tbody>
<tr><td>Material, el och slitage</td><td class="right">{_num(q['cost'])} kr</td></tr>
{f'<tr><td>Marginal för misslyckade utskrifter ({_num(q["fail_pct"], 0)} %)</td><td class="right">{_num(q["risk"])} kr</td></tr>' if q['risk'] else ''}
{f'<tr><td>Arbete och påslag ({_num(q["markup_pct"], 0)} %)</td><td class="right">{_num(q["markup"])} kr</td></tr>' if q['markup'] else ''}
<tr><td>Summa{' exkl. moms' if q['vat'] else ''}</td><td class="right">{_num(q['ex_vat'])} kr</td></tr>{vat_row}
<tr><td><b>Att betala{' inkl. moms' if q['vat'] else ''}</b></td><td class="right"><b style="font-size:20px">{_num(q['rounded'], 0)} kr</b></td></tr>
</tbody></table></div>
<p class="muted" style="font-size:12px;margin-top:14px">Självkostnaden bygger på aktuella filament- och elpriser. {'Priset är avrundat uppåt till hela ' + str(q['rounding']) + ' kr.' if q['rounding'] > 1 else ''}</p></section>"""
    return layout('Offert', body, user, csrf, 'kostnad', scripts=('/assets/print.js',))


def backups_html(csrf, backups, backup_time, keep):
    h = f'<input type="hidden" name="csrf" value="{e(csrf)}">'
    rows = ''.join(
        f'<tr><td>{fmt_time(b["mtime"])}</td><td class="hide-sm">{e(b["name"])}</td><td class="right">{_num(b["size"] / 1024, 0)} kB</td>'
        f'<td class="right"><div class="actions"><a class="btn ghost small" href="/admin/sakerhetskopior/{e(b["name"])}">Ladda ner</a>'
        f'<form method="post" action="/admin/sakerhetskopior/{e(b["name"])}/aterstall" class="row" style="gap:6px">{h}'
        f'<input name="confirm" type="text" placeholder="Skriv ÅTERSTÄLL" aria-label="Bekräfta återställning" style="width:150px;padding:7px">'
        f'<button class="btn danger small" type="submit">Återställ</button></form></div></td></tr>' for b in backups)
    sched = (f'Görs automatiskt varje natt kl. {e(backup_time)}; de {keep} senaste sparas i <code>/data/backups</code>.'
             if backup_time else 'Automatiska säkerhetskopior är avstängda (BACKUP_TIME).')
    return (f'<h3 class="mt" style="margin-top:22px">Sparade säkerhetskopior</h3><p class="sub">{sched} '
            f'Återställning ersätter alla användare, listor och inställningar med kopians innehåll – nuvarande läge sparas först som en ny kopia. '
            f'Alla blir utloggade.</p>'
            f'<form method="post" action="/admin/sakerhetskopior/ny" style="margin-bottom:12px">{h}<button class="btn ghost small" type="submit">Säkerhetskopiera nu</button></form>'
            + (f'<div class="tablewrap"><table><thead><tr><th>Skapad</th><th class="hide-sm">Fil</th><th class="right">Storlek</th><th></th></tr></thead>'
               f'<tbody>{rows}</tbody></table></div>' if rows else '<p class="muted">Inga sparade kopior än.</p>'))


# ---------- skrivare ----------
def printer_cards(statuses, names, csrf):
    h = f'<input type="hidden" name="csrf" value="{e(csrf)}">'
    if not statuses:
        return '<p class="muted">Inga skrivare tillagda än.</p>'
    out = ''
    for p in statuses:
        conn = ('<span class="badge ok">Ansluten</span>' if p['connected'] else
                f'<span class="badge bad" title="{e(p["error"] or "")}">Inte ansluten</span>' if p['error'] else '<span class="badge">Ansluter…</span>')
        prog = ''
        if p['state'] in ('RUNNING', 'PAUSE', 'PREPARE'):
            pct = p['percent'] or 0
            left = f' · {_hm((p["left"] or 0) / 60)} kvar' if p['left'] is not None else ''
            layer = f' · lager {p["layer"]}/{p["layers"]}' if p['layers'] else ''
            prog = (f'<p style="margin:10px 0 4px"><b>{e(p["job"] or "Utskrift")}</b>{layer}{left}</p>'
                    f'<div class="progress" role="progressbar" aria-valuenow="{pct:.0f}" aria-valuemin="0" aria-valuemax="100"><span style="width:{pct:.0f}%"></span></div>'
                    f'<p class="muted" style="font-size:12px;margin:4px 0 0">{pct:.0f} % klart</p>')
            if p['live'] and p['live']['elSoFar'] is not None:
                prog += (f'<p class="sub" style="margin:8px 0 0">El hittills ca <b>{_num(p["live"]["elSoFar"])} kr</b>, hela utskriften ca '
                         f'{_num(p["live"]["elTotal"])} kr ({_num(p["live"]["watts"], 0)} W).</p>')
        temps = ' · '.join(x for x in (f'munstycke {p["nozzle"]:.0f} °C' if p['nozzle'] is not None else '',
                                        f'bädd {p["bed"]:.0f} °C' if p['bed'] is not None else '') if x)
        trays = ''
        for t in p['trays']:
            sw = f'<span class="color-dot big" style="background:{e(t["color"])}"></span>' if re.fullmatch(r'#[0-9A-Fa-f]{6}', t['color'] or '') else ''
            name = f'{e(t["key"].split("|")[0])} · {e(names.get(t["key"], ""))}' if t['key'] else e(t['type'] or '')
            rem = f' · {t["remain"]:.0f} % kvar' if t['remain'] is not None and t['remain'] >= 0 else ''
            trays += f'<li{" class=active" if t["active"] else ""}>{sw}<span><b>{name}</b><br><span class="muted" style="font-size:12px">{e(t["slot"])}{rem}{" · används nu" if t["active"] else ""}</span></span></li>'
        mode = 'lokalt' if p['mode'] == 'lan' else 'via Bambu-molnet'
        out += f"""<section class="card printer"><div class="row" style="justify-content:space-between;align-items:baseline">
<h2>{e(p['name'])} <span class="muted" style="font-size:13px;font-weight:400">{e(p['model'] or '')} · {mode}</span></h2>{conn}</div>
<p style="margin:6px 0 0"><b>{e(p['stateText'])}</b>{(' · ' + temps) if temps else ''}</p>{prog}
{f'<ul class="ams">{trays}</ul>' if trays else ''}
<details class="mt no-print"><summary>Inställningar</summary><form method="post" action="/skrivare/{p['id']}/andra" class="row mt">{h}
<div class="field" style="max-width:190px"><label>Anslutning</label><select name="mode"><option value="cloud"{' selected' if p['mode'] == 'cloud' else ''}>Bambu-molnet</option><option value="lan"{' selected' if p['mode'] == 'lan' else ''}>Lokalt (IP)</option></select></div>
<div class="field" style="max-width:170px"><label>IP-adress</label><input name="host" type="text" value="{e(p['host'] or '')}" placeholder="192.168.1.50"></div>
<div class="field" style="max-width:170px"><label>Åtkomstkod</label><input name="code" type="password" autocomplete="off" placeholder="oförändrad"></div>
<div class="field" style="max-width:140px"><label>Effekt (W)</label><input name="watts" type="text" inputmode="numeric" placeholder="auto"></div>
<div><button class="btn ghost small" type="submit">Spara</button></div></form>
<form method="post" action="/skrivare/{p['id']}/ta-bort" class="mt">{h}<button class="btn danger small" type="submit">Ta bort skrivaren</button></form></details></section>"""
    return out


def printers_page(user, csrf, statuses, names, has_bambu, msg_kind='', msg=''):
    h = f'<input type="hidden" name="csrf" value="{e(csrf)}">'
    cloud = (f'<form method="post" action="/skrivare/hamta" style="margin-bottom:18px">{h}<button class="btn" type="submit">Hämta skrivare från Bambu Lab-kontot</button></form>'
             if has_bambu else '<p class="sub">Koppla ditt Bambu Lab-konto under <a href="/konto">Mitt konto</a> för att hitta skrivarna automatiskt, eller lägg till en skrivare lokalt nedan.</p>')
    body = f"""<h1>Skrivare</h1><p class="lead">Status i realtid, vad som sitter i AMS och vad den pågående utskriften kostar i el.</p>
{message(msg_kind, e(msg))}{cloud}<div id="printerCards" aria-live="polite">{printer_cards(statuses, names, csrf)}</div>
<section class="card"><h2>Lägg till skrivare lokalt</h2>
<p class="sub">Snabbast och fungerar utan Bambu-molnet. IP-adress och åtkomstkod finns på skrivarens skärm under nätverksinställningar
(på A1/P1: <i>Inställningar → WLAN</i>). Serienumret finns under <i>Inställningar → Enhet</i>. Katalogen läser bara status och skickar inga kommandon.</p>
<form method="post" action="/skrivare/ny" class="row" autocomplete="off">{h}
<div class="field" style="max-width:200px"><label for="pn">Namn</label><input id="pn" name="name" type="text" maxlength="60" placeholder="t.ex. Verkstan"></div>
<div class="field" style="max-width:220px"><label for="ps">Serienummer</label><input id="ps" name="serial" type="text" maxlength="32" required></div>
<div class="field" style="max-width:170px"><label for="pi">IP-adress</label><input id="pi" name="host" type="text" maxlength="40" required placeholder="192.168.1.50"></div>
<div class="field" style="max-width:150px"><label for="pc">Åtkomstkod</label><input id="pc" name="code" type="password" maxlength="16" required></div>
<div><button class="btn ghost" type="submit">Lägg till</button></div></form></section>"""
    return layout('Skrivare', body, user, csrf, 'skrivare', scripts=('/assets/printers.js',))


def _print_edit(r, h, reasons, projects):
    st = ''.join(f'<option value="{v}"{" selected" if r["status"] == v else ""}>{t}</option>'
                 for v, t in (('ok', 'Lyckades'), ('failed', 'Misslyckades'), ('cancelled', 'Avbruten')))
    rs = '<option value="">Orsak…</option>' + ''.join(f'<option{" selected" if r["reason"] == x else ""}>{e(x)}</option>' for x in reasons)
    pj = '<option value="">Inget projekt</option>' + ''.join(
        f'<option value="{p["id"]}"{" selected" if r["project_id"] == p["id"] else ""}>{e(p["name"])}</option>' for p in projects)
    return (f'<details class="no-print"><summary>Ändra</summary><form method="post" action="/utskrifter/{r["id"]}/andra" class="print-edit">{h}'
            f'<select name="status" aria-label="Resultat">{st}</select><select name="reason" aria-label="Orsak">{rs}</select>'
            f'<select name="project" aria-label="Projekt">{pj}</select><button class="btn ghost small" type="submit">Spara</button></form></details>')


# ---------- projekt ----------
def projects_page(user, csrf, projects, sums, msg_kind='', msg=''):
    h = f'<input type="hidden" name="csrf" value="{e(csrf)}">'
    rows = ''.join(
        f'<tr><td><a href="/projekt/{p["id"]}"><b>{e(p["name"])}</b></a>{"<br><span class=muted style=font-size:12px>" + e(p["customer"]) + "</span>" if p["customer"] else ""}</td>'
        f'<td class="right">{len(json.loads(p["colors"] or "[]"))}</td><td class="right">{sums.get(p["id"], {}).get("n", 0)}</td>'
        f'<td class="right">{_num(sums.get(p["id"], {}).get("total", 0))} kr</td></tr>' for p in projects)
    body = f"""<h1>Projekt</h1><p class="lead">Färgpalett per projekt och vad utskrifterna kostar – per projekt eller kund, med export till CSV.</p>
{message(msg_kind, e(msg))}
<section class="card"><h2>Nytt projekt</h2><form method="post" action="/projekt/ny" class="row">{h}
<div class="field"><label for="pjn">Namn</label><input id="pjn" name="name" type="text" maxlength="80" required></div>
<div class="field"><label for="pjc">Kund (valfritt)</label><input id="pjc" name="customer" type="text" maxlength="120"></div>
<div><button class="btn" type="submit">Skapa</button></div></form></section>
{f'<section class="card"><div class="tablewrap"><table><thead><tr><th>Projekt</th><th class="right">Färger</th><th class="right">Utskrifter</th><th class="right">Kostnad</th></tr></thead><tbody>{rows}</tbody></table></div></section>' if rows else ''}"""
    return layout('Projekt', body, user, csrf, 'projekt')


def project_page(user, csrf, p, palette, prints, names, msg_kind='', msg=''):
    h = f'<input type="hidden" name="csrf" value="{e(csrf)}">'
    by_type = {}
    for k in sorted(names, key=lambda k: (k.split('|')[0], names[k])):
        by_type.setdefault(k.split('|')[0], []).append(k)
    opts = ''.join(f'<optgroup label="{e(t)}">' + ''.join(f'<option value="{e(k)}">{e(t)} · {e(names[k])}</option>' for k in ks) + '</optgroup>'
                   for t, ks in by_type.items())

    def swatch(d, size='big'):
        hx = [x[:7] for x in d.get('hex') or [] if re.fullmatch(r'#[0-9A-Fa-f]{6,8}', x)]
        bg = f'linear-gradient(120deg,{",".join(hx)})' if len(hx) > 1 else (hx[0] if hx else '#ccc')
        return f'<span class="swatch-tile {size}" style="background:{bg}"></span>'
    tiles = ''
    for c in palette:
        d = c['item']
        status = (f'<span class="badge ok">Har{" · " + _num(c["grams"], 0) + " g" if c["grams"] else ""}</span>' if c['owned'] else
                  '<span class="badge warn">Saknas</span>')
        price = f'<br><span class="muted" style="font-size:12px">från {_kr(c["best"][1])} inkl. frakt</span>' if c['best'] and not c['owned'] else ''
        similar = ''.join(f'<li>{swatch(x, "small")} {e(x["type"])} · {e(x["name"])} <span class="muted">ΔE {dist}</span></li>' for dist, x in c['similar'])
        tiles += (f'<div class="palette-tile">{swatch(d)}<div><b>{e(d["name"])}</b><br><span class="muted" style="font-size:12px">{e(d["type"])} · {e(d["code"] or "")}</span><br>'
                  f'{status}{price}' + (f'<p class="sub" style="margin:6px 0 0">Liknande som du har:</p><ul class="similar">{similar}</ul>' if similar else '') +
                  f'<form method="post" action="/projekt/{p["id"]}/ta-bort-farg">{h}<input type="hidden" name="key" value="{e(c["key"])}">'
                  f'<button class="btn danger small" type="submit">Ta bort</button></form></div></div>')
    missing = sum(not c['owned'] for c in palette)
    strip = ''.join(swatch(c['item'], 'strip') for c in palette)
    total = sum(r['total'] for r in prints)
    prow = ''.join(f'<tr><td>{fmt_time(r["ts"])[:10]}</td><td>{e(r["name"])} {PRINT_STATUS.get(r["status"], "")}</td><td class="right">{_num(r["total"])} kr</td></tr>'
                   for r in prints)
    body = f"""<p class="no-print"><a href="/projekt">← Alla projekt</a></p><h1>{e(p['name'])}</h1>
{f'<p class="lead">{e(p["customer"])}</p>' if p['customer'] else ''}{message(msg_kind, e(msg))}
<section class="card"><h2>Färgpalett</h2>{f'<div class="palette-strip">{strip}</div>' if strip else ''}
<div class="palette">{tiles or '<p class="muted">Inga färger än.</p>'}</div>
<form method="post" action="/projekt/{p['id']}/farg" class="row mt no-print">{h}<div class="field"><label for="pk">Lägg till färg</label><select id="pk" name="key">{opts}</select></div>
<div><button class="btn ghost" type="submit">Lägg till</button></div></form>
{f'<form method="post" action="/projekt/{p["id"]}/saknade" class="mt no-print">{h}<button class="btn" type="submit">Lägg {missing} saknade färger i inköpslistan</button></form>' if missing else ''}</section>
<section class="card"><div class="row" style="justify-content:space-between;align-items:baseline"><h2>Utskrifter i projektet</h2><strong>{_num(total)} kr</strong></div>
<p class="sub">Koppla utskrifter till projektet under <a href="/utskrifter">Utskrifter</a> (Ändra) eller när du sparar från kostnadsräknaren.</p>
{f'<div class="tablewrap"><table><tbody>{prow}</tbody></table></div><p class="mt"><a class="btn ghost small" href="/projekt/{p["id"]}.csv">Exportera till CSV (fakturaunderlag)</a> <button class="btn ghost small no-print" type="button" data-print>Skriv ut</button></p>' if prow else ''}</section>
<section class="card no-print"><details><summary>Ändra projektet</summary><form method="post" action="/projekt/{p['id']}/andra" class="mt">{h}
<div class="row"><div class="field"><label>Namn</label><input name="name" type="text" maxlength="80" value="{e(p['name'])}"></div>
<div class="field"><label>Kund</label><input name="customer" type="text" maxlength="120" value="{e(p['customer'] or '')}"></div></div>
<div class="field mt"><label>Anteckning</label><input name="note" type="text" maxlength="2000" value="{e(p['note'] or '')}"></div>
<button class="btn ghost small mt" type="submit">Spara</button></form>
<form method="post" action="/projekt/{p['id']}/ta-bort" class="mt">{h}<button class="btn danger small" type="submit">Ta bort projektet</button></form></details></section>"""
    return layout(p['name'], body, user, csrf, 'projekt', scripts=('/assets/print.js',))


# ---------- samköp ----------
def groupbuy_page(user, csrf, parts, names, result, catalog_names, allowed, msg_kind='', msg=''):
    h = f'<input type="hidden" name="csrf" value="{e(csrf)}">'
    chosen = set(result['ids']) if result else {user['id']}
    boxes = ''.join(f'<label class="check"><input type="checkbox" name="user" value="{p["id"]}"{" checked" if p["id"] in chosen else ""}'
                    f'{" disabled checked" if p["id"] == user["id"] else ""}> {e(p["username"])}{" (du)" if p["id"] == user["id"] else ""}</label>'
                    for p in parts)
    me_hidden = f'<input type="hidden" name="user" value="{user["id"]}">'
    consent = (f'<form method="post" action="/samkop/tillat" class="mt">{h}<input type="hidden" name="on" value="{"0" if allowed else "1"}">'
               f'<p class="sub" style="margin-bottom:8px">{"Andra användare kan ta med din inköpslista i sina samköp." if allowed else "Din inköpslista kan inte tas med i andras samköp."}</p>'
               f'<button class="btn ghost small" type="submit">{"Sluta tillåta samköp" if allowed else "Tillåt samköp med min lista"}</button></form>')
    out = ''
    if result:
        pl, people = result['plan'], result['people']
        prow = ''.join(
            f'<tr><td><b>{e(names.get(uid, "?"))}</b><br><span class="muted" style="font-size:12px">{p["units"]} st · {e(", ".join(p["shops"]))}</span></td>'
            f'<td class="right">{_kr(p["goods"])}</td><td class="right">{_kr(p["shared"])}</td><td class="right"><b>{_kr(p["total"])}</b></td>'
            f'<td class="right">{_kr(p["alone"])}{' · <span class="badge ok">sparar ' + _kr(p["alone"] - p["total"]) + '</span>' if p["alone"] - p["total"] > 1 else ''}</td></tr>'
            for uid, p in people.items())
        shops = ''
        for sh in pl['shops']:
            lines = ''.join(f'<li>{l["qty"]} × {e(l["key"].split("|")[0])} · {e(catalog_names.get(l["key"], ""))} – '
                            f'{e(", ".join(sorted({names.get(o, "?") for o in l.get("owners", []) if o})))} '
                            f'<a href="{_safe_href(l["offer"].get("url"))}" target="_blank" rel="noopener">Köp ↗</a></li>' for l in sh['lines'])
            shops += (f'<section class="card"><div class="row" style="justify-content:space-between"><h2>{e(sh["shop"])}</h2><strong>{_kr(sh["total"])}</strong></div>'
                      f'<p class="sub">Frakt {_kr(sh["shipping"])}{" · mängdrabatt −" + _kr(sh["discount"]) if sh.get("discount") else ""}. {e(sh["note"])}</p>'
                      f'<ul style="margin:0;padding-left:18px;line-height:1.9">{lines}</ul></section>')
        out = (f'<section class="card"><h2>Fördelning</h2><div class="stats"><div class="stat"><span>Samköp totalt</span><strong>{_kr(pl["totals"]["total"])}</strong></div>'
               f'<div class="stat"><span>Om alla köper själva</span><strong>{_kr(sum(p["alone"] for p in people.values()))}</strong></div></div>'
               f'<div class="tablewrap"><table><thead><tr><th>Person</th><th class="right">Varor</th><th class="right">Andel frakt m.m.</th><th class="right">Att betala</th><th class="right">Själv</th></tr></thead>'
               f'<tbody>{prow}</tbody></table></div><p class="muted" style="font-size:12px;margin:10px 0 0">Varje person betalar sina varor; frakt och köparskydd delas efter varuvärde per butik.</p></section>{shops}')
    body = f"""<h1>Samköp</h1><p class="lead">Lägg ihop inköpslistor med andra användare så att fri frakt och mängdrabatter nås tillsammans.</p>
{message(msg_kind, e(msg))}<section class="card"><h2>Vilka är med?</h2>
<form method="post" action="/samkop/rakna">{h}{me_hidden}<div class="checks">{boxes}</div>
{'' if len(parts) > 1 else '<p class="muted">Ingen annan har tillåtit samköp än.</p>'}<button class="btn mt" type="submit">Räkna ut samköp</button></form>{consent}</section>{out}"""
    return layout('Samköp', body, user, csrf, 'inkop')


def api_tokens_section(csrf, tokens, new_token, base_url, msg_kind='', msg=''):
    h = f'<input type="hidden" name="csrf" value="{e(csrf)}">'
    rows = ''.join(f'<tr><td>{e(t["name"])}</td><td class="hide-sm">{fmt_time(t["created"])}</td><td>{fmt_rel(t["last_used"])}</td>'
                   f'<td class="right"><form method="post" action="/konto/api-nyckel/{t["id"]}/ta-bort">{h}<button class="btn danger small" type="submit">Ta bort</button></form></td></tr>'
                   for t in tokens)
    shown = (f'<p><input type="text" readonly value="{e(new_token)}" aria-label="Ny API-nyckel" style="width:100%;font-family:monospace"></p>'
             if new_token else '')
    ex = e(base_url)
    return f"""<section class="card" id="api"><h2>Genvägar och röstassistenter</h2>
<p class="sub">Skapa en personlig API-nyckel och fråga katalogen från iOS Genvägar, Android (HTTP Shortcuts/Tasker) eller Home Assistant.
Nyckeln ger bara läsåtkomst till dina egna uppgifter. Svaret är en mening som Siri eller Google kan läsa upp (lägg till <code>?format=json</code> för JSON).</p>
{message(msg_kind, e(msg))}{shown}
<ul style="font-size:13px;line-height:1.8;margin:0 0 14px;padding-left:18px">
<li><code>{ex}/api/v1/filament?q=svart pla</code> – hur mycket som finns kvar och hur länge det räcker</li>
<li><code>{ex}/api/v1/elpris</code> – elpriset nu och lägst närmaste 12 timmarna</li>
<li><code>{ex}/api/v1/skrivare</code> – vad skrivarna gör just nu</li>
<li><code>{ex}/api/v1/sammanfattning</code> – bevakningar, snart slut och inköpslistan</li></ul>
<p class="sub">Skicka nyckeln i rubriken <code>Authorization: Bearer &lt;nyckel&gt;</code>.</p>
{f'<div class="tablewrap"><table><thead><tr><th>Namn</th><th class="hide-sm">Skapad</th><th>Senast använd</th><th></th></tr></thead><tbody>{rows}</tbody></table></div>' if rows else ''}
<form method="post" action="/konto/api-nyckel" class="row mt">{h}<div class="field" style="max-width:280px"><label for="tkn">Namn på nyckeln</label>
<input id="tkn" name="name" type="text" maxlength="60" placeholder="t.ex. iPhone Genvägar"></div><div><button class="btn ghost" type="submit">Skapa API-nyckel</button></div></form></section>"""


def data_section(csrf):
    return f"""<section class="card" id="data"><h2>Exportera och importera</h2>
<p class="sub">Alla dina uppgifter (markeringar, önskelista och bevakningar, inköpslista, köp, utskrifter, projekt och inställningar utan lösenord och nycklar)
som JSON för att flytta till en annan server, eller som CSV-filer för Excel. Importen lägger till och uppdaterar – inget tas bort.</p>
<div class="row"><a class="btn ghost" href="/konto/export.json">Exportera som JSON</a><a class="btn ghost" href="/konto/export.zip">Exportera som CSV (zip)</a></div>
<div class="row mt"><div class="field"><label for="importFile">Importera från JSON-export</label><input id="importFile" type="file" accept="application/json,.json" data-csrf="{e(csrf)}"></div></div>
<p id="importStatus" class="muted" role="status" style="font-size:13px"></p></section>"""


def integrations_html(csrf, it):
    h = f'<input type="hidden" name="csrf" value="{e(csrf)}">'
    ha, sm = it['ha'], it['spoolman']
    users = lambda cur: ''.join(f'<option value="{i}"{" selected" if str(i) == str(cur) else ""}>{e(n)}</option>' for i, n in it['users'])  # noqa: E731
    st = sm.get('status') or {}
    st_html = (f'<p class="muted" style="font-size:13px">Senaste synk {fmt_rel(st.get("ts"))}: {e(st.get("msg", ""))}</p>' if st else '')
    return f"""<section class="card" id="integrationer"><h2>Home Assistant</h2>
<p class="sub">Skickar sensorer (elpris nu, skrivarnas status och elkostnad, filament kvar per färg, antal som snart tar slut) till Home Assistant
via MQTT med automatisk upptäckt, var 5:e minut. Händelser (målpris nått, nya färger) skickas till ämnet <code>katalog/event</code> för automationer.
Port 8883 används med TLS. Lämna adressen tom för att stänga av.</p>
<form method="post" action="/admin/homeassistant" autocomplete="off">{h}<div class="row">
<div class="field"><label>MQTT-broker (adress)</label><input name="host" type="text" value="{e(ha['host'] or '')}" placeholder="t.ex. 192.168.1.20"></div>
<div class="field" style="max-width:110px"><label>Port</label><input name="port" type="text" inputmode="numeric" value="{e(str(ha['port']))}"></div>
<div class="field" style="max-width:200px"><label>Användarnamn</label><input name="user" type="text" value="{e(ha['user'] or '')}"></div>
<div class="field" style="max-width:200px"><label>Lösenord</label><input name="password" type="password" placeholder="{'oförändrat' if ha['password'] else ''}"></div></div>
<div class="row mt"><div class="field" style="max-width:220px"><label>Vems data</label><select name="user_id"><option value="">Välj…</option>{users(ha['user_id'])}</select></div>
<div class="field" style="max-width:220px"><label>Upptäcktsprefix</label><input name="prefix" type="text" value="{e(ha['prefix'])}"></div>
<div><button class="btn ghost small" type="submit">Spara</button></div></div></form>
<form method="post" action="/admin/homeassistant/testa" class="mt">{h}<button class="btn ghost small" type="submit">Skicka sensorer nu</button></form>
<h2 class="mt" style="margin-top:26px">Spoolman</h2>
<p class="sub">Hämtar spolarna i din Spoolman-server och visar dem som ”Jag har” med gram kvar. Kan också skicka spolarna i Bambu-biblioteket till Spoolman
(skapar tillverkare, filament och spolar och uppdaterar gram kvar). Synkas med Bambu-synken. Spoolman måste ligga i det lokala nätet.</p>
<form method="post" action="/admin/spoolman">{h}<div class="row">
<div class="field"><label>Spoolman-adress</label><input name="url" type="text" value="{e(sm['url'])}" placeholder="http://192.168.1.10:7912"></div>
<div class="field" style="max-width:220px"><label>Vems katalog</label><select name="user_id"><option value="">Välj…</option>{users(sm['user_id'])}</select></div></div>
<label class="check mt"><input type="checkbox" name="push" value="1"{' checked' if sm['push'] else ''}> Skicka även Bambu-bibliotekets spolar till Spoolman</label>
<div class="mt"><button class="btn ghost small" type="submit">Spara</button></div></form>
<form method="post" action="/admin/spoolman/synka" class="mt">{h}<button class="btn ghost small" type="submit">Synka med Spoolman nu</button></form>{st_html}</section>"""

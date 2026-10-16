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
        items = [('/', 'Katalog', 'katalog'), ('/inkopslista', 'Inköpslista', 'inkop'), ('/konto', 'Mitt konto', 'konto')]
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
    return layout('Mitt konto', body, user, csrf, 'konto')


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


def admin(user, csrf, users, runs, next_run, offers_info, msg_kind='', msg='', secret=None):
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
Återställ genom att stoppa containern, packa upp filen i datamappen och starta igen.</p>
<form method="post" action="/admin/sakerhetskopia"><input type="hidden" name="csrf" value="{e(csrf)}">
<button class="btn ghost" type="submit">Ladda ner säkerhetskopia</button></form></section>"""

    body = f"""<h1>Administration</h1><p class="lead">Användare, prisuppdatering och säkerhetskopia.</p>
{message(msg_kind, e(msg))}{secret_html}{prices}{users_html}{backup}"""
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


def _plan_html(plan, checked_at, title_total='Uppskattat totalt'):
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
            disc = f' <span class="badge bad">−{o["discount"]} %</span>' if o.get('discount') else ''
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
        shops += f"""<section class="card"><div class="row" style="justify-content:space-between;align-items:baseline">
<h2>{e(sh['shop'])}{home}</h2><strong style="font-size:18px">{_kr(sh['total'])}</strong></div>
<p class="sub">Varor {_kr(sh['goods'])}{f" · mängdrabatt −{_kr(sh['discount'])}" if sh.get('discount') else ''} · frakt {ship}{fee}. {e(sh['note'])}</p>{bulk}
<div class="tablewrap"><table><thead><tr><th>Filament</th><th class="hide-sm">Förpackning</th><th class="right">Antal</th>
<th class="right">Summa</th><th></th></tr></thead><tbody>{rows}</tbody></table></div></section>"""
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
                  full=None, shared=False, share_url=None):
    h = f'<input type="hidden" name="csrf" value="{e(csrf)}">'
    if not items:
        body = f"""<h1>Inköpslista</h1><p class="lead">Lägg till filament med knappen <b>+ Inköpslista</b> på korten i katalogen.
Här ser du sedan var varje filament är billigast att köpa och vad allt kostar.</p>{message(msg_kind, e(msg))}
<p><a class="btn" href="/">Till katalogen</a></p>"""
        return layout('Inköpslista', body, user, csrf, 'inkop')
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
{_print_button()}</div></section>"""

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
    plan_html = _plan_html(plan, checked_at, 'Köp nu' if budget and waiting else 'Uppskattat totalt') if plan['totals']['units'] or plan['missing'] else ''
    body = (f'<h1>Inköpslista</h1><p class="lead">Var du bör köpa varje filament och vad det kostar totalt.</p>'
            f'{message(msg_kind, e(msg))}{pending_html}{wait_html}{plan_html}{edit}{share_html}')
    return layout('Inköpslista', body, user, csrf, 'inkop', refresh=8 if pending else None, scripts=('/assets/print.js',))


def shared_list(owner, plan, checked_at):
    if not plan:
        body = f'<h1>Inköpslista</h1><p class="lead">{e(owner)}s inköpslista är tom just nu.</p>'
    else:
        body = (f'<h1>Inköpslista</h1><p class="lead">Delad av <b>{e(owner)}</b>. Var varje filament är billigast att köpa och vad det kostar. '
                f'Listan uppdateras när {e(owner)} ändrar den.</p><p class="no-print">{_print_button()}</p>{_plan_html(plan, checked_at)}')
    return layout('Delad inköpslista', body, scripts=('/assets/print.js',))

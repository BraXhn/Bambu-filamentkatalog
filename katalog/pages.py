"""HTML för inloggning, konto och administration. Inga skript: vanliga formulär fungerar med strikt CSP."""
import datetime
import json
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


def layout(title, body, user=None, csrf='', active='', narrow=False, refresh=None):
    nav = ''
    if user:
        items = [('/', 'Katalog', 'katalog'), ('/konto', 'Mitt konto', 'konto')]
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
<link rel="icon" href="/assets/icon.svg" type="image/svg+xml"></head><body>
<header class="top"><a class="brand" href="/"><i>▥</i> BAMBU <span>/ filamentkatalog</span></a>{nav}</header>
<main class="page{' narrow' if narrow else ''}">{body}</main>
<footer class="foot">{e(config.SITE_NAME)}</footer></body></html>"""


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
def account(user, csrf, sessions_count, msg_kind='', msg='', must_change=False):
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
<button class="btn ghost" type="submit"{' disabled' if sessions_count <= 1 else ''}>Logga ut andra enheter</button></form></section>"""
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
    runs_table = (f'<div class="tablewrap mt"><table><thead><tr><th>Startad</th><th>Resultat</th><th>Källor (antal erbjudanden)</th></tr>'
                  f'</thead><tbody>{rows}</tbody></table></div>') if rows else ''
    prices = f"""<section class="card" id="priser"><h2>Prisuppdatering</h2>
<p class="sub">Priser hämtas automatiskt från 3DJake, 3D Prima och Tradera. Om en källa inte svarar behålls dess senaste priser.</p>
<div class="stats"><div class="stat"><span>Senaste körning</span><strong>{last_txt}</strong></div>
<div class="stat"><span>Nästa schemalagda</span><strong>{e(next_run)}</strong></div>
<div class="stat"><span>Erbjudanden just nu</span><strong>{e(offers_info)}</strong></div></div>
<form method="post" action="/admin/priser/uppdatera"><input type="hidden" name="csrf" value="{e(csrf)}">
<button class="btn" type="submit"{' disabled' if running else ''}>{'Uppdatering pågår…' if running else 'Uppdatera priser nu'}</button>
<span class="muted" style="font-size:13px;margin-left:8px">{'Sidan laddas om automatiskt.' if running else 'Tar normalt 10–20 minuter.'}</span></form>
{runs_table}</section>"""

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

    body = f"""<h1>Administration</h1><p class="lead">Användare och prisuppdatering.</p>
{message(msg_kind, e(msg))}{secret_html}{prices}{users_html}"""
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

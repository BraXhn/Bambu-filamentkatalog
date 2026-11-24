"""Bevakning av priskällorna i containern: när en butik misslyckats flera nätter i rad aviseras administratörer
(ntfy och Home Assistant) en gång, och igen när den fungerar.

Ersätter den tidigare kontrollen i GitHub Actions; allt sker nu i containern, från samma nät som hämtningen.
"""
import json

from . import alerts, config, db, pages

STATE = 'source_alerts'


def check(log=print):
    runs = db.recent_runs(10)
    failing = {lbl: (n, err) for lbl, n, err in pages.source_alerts(runs)}
    try:
        before = set(json.loads(db.get_setting(STATE) or '[]'))
    except ValueError:
        before = set()
    new = sorted(set(failing) - before)
    fixed = sorted(before - set(failing))
    db.set_setting(STATE, json.dumps(sorted(failing), ensure_ascii=False))
    if not new and not fixed:
        return [], []
    lines = [f'{lbl} har misslyckats {failing[lbl][0]} körningar i rad: {failing[lbl][1] or "okänt fel"}' for lbl in new]
    lines += [f'{lbl} fungerar igen.' for lbl in fixed]
    if new:
        lines.append('Tidigare priser visas tills vidare. Se Administration, Prisuppdatering.')
    title = 'Prishämtningen har problem' if new else 'Prishämtningen fungerar igen'
    log('[priser] ' + ' '.join(lines))
    click = config.PUBLIC_URL + '/admin#priser' if config.PUBLIC_URL else None
    for u in db.list_users():
        target = db.get_pref(u['id'], 'notify') if u['is_admin'] else None
        if target:
            err = alerts.send(target, title, '\n'.join(lines), click)
            if err:
                log(f'[priser] avisering till {u["username"]}: {err}')
    try:
        from . import homeassistant
        homeassistant.event('priskalla', {'fel': new, 'fungerar_igen': fixed})
    except Exception:  # noqa: BLE001
        pass
    return new, fixed

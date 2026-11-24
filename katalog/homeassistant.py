"""Home Assistant via MQTT (automatisk upptäckt): sensorer för elpris, skrivare och filament kvar, och händelser.

Ställs in av en administratör (broker, inloggning och vems data som visas). Sensorerna skickas var 5:e minut som
"retained" meddelanden; händelser (målpris nått, nya färger, snart slut) skickas till katalog/<prefix>/event.
"""
import json
import re
import time

from . import bambu, config, db, energy, mqtt, printers

INTERVAL = 300
_last = {'ts': 0}
DEVICE = {'identifiers': ['bambu_filamentkatalog'], 'name': 'Bambu filamentkatalog', 'manufacturer': 'Helicon & BraXhn'}


def settings():
    s = {k: db.get_setting('ha_' + k) for k in ('host', 'port', 'user', 'password', 'user_id', 'prefix')}
    s['port'] = int(s['port'] or 1883)
    s['prefix'] = s['prefix'] or 'homeassistant'
    return s


def enabled():
    s = settings()
    return bool(s['host'] and s['user_id'])


def check_host(host, port):
    """Brokern ska ligga i det egna nätet: sensorerna innehåller skrivarnamn och lager och skickas som retained."""
    import ipaddress
    import socket
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except OSError:
        return f'Hittar inte {host}.'
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not (ip.is_private or ip.is_loopback) or ip.is_link_local:
            return 'MQTT-brokern måste ligga i det lokala nätet (privat IP-adress).'
    return None


def _client(s):
    tls = s['port'] == 8883
    return mqtt.Client(s['host'], s['port'], s['user'] or None, s['password'] or None, tls=tls, verify=False, timeout=8).connect()


def _slug(s):
    return re.sub(r'[^a-z0-9]+', '_', s.lower()).strip('_')[:60]


def collect(uid):
    """[(id, namn, värde, enhet, ikon)] för användaren."""
    out = []
    try:
        src = db.get_pref(uid, 'cost_source') or 'spot'
        extra = float(str(db.get_pref(uid, 'cost_extra') or '0').replace(',', '.'))
        if src == 'tibber':
            iv = energy.tibber_prices(db.get_pref(uid, 'tibber_token'), extra)
        elif src == 'spot':
            iv = energy.spot_prices(db.get_pref(uid, 'cost_area') or 'SE3', None, (db.get_pref(uid, 'cost_vat') or '1') == '1', extra)
        else:
            iv = []
        o = energy.price_outlook(iv)
        if o:
            out.append(('elpris_nu', 'Elpris nu', round(o['now'], 3), 'SEK/kWh', 'mdi:flash'))
            if o['low']:
                out.append(('elpris_lagst_12h', 'Elpris lägst 12 h', round(o['low'][1], 3), 'SEK/kWh', 'mdi:flash-outline'))
    except (energy.EnergyError, ValueError):
        iv = []
    catalog = json.loads(config.CATALOG_PATH.read_text(encoding='utf-8'))
    matcher = bambu.SpoolMatcher(catalog, bambu.color_db())
    for p in printers.status(uid, matcher, iv):
        base = 'skrivare_' + _slug(p['name'])
        out.append((base + '_status', f"{p['name']} status", p['stateText'], None, 'mdi:printer-3d'))
        out.append((base + '_klart', f"{p['name']} klart", p['percent'] if p['percent'] is not None else 0, '%', 'mdi:progress-clock'))
        out.append((base + '_tid_kvar', f"{p['name']} tid kvar", p['left'] or 0, 'min', 'mdi:timer-sand'))
        if p['live'] and p['live']['elSoFar'] is not None:
            out.append((base + '_el_kostnad', f"{p['name']} elkostnad pågående", round(p['live']['elSoFar'], 2), 'SEK', 'mdi:cash'))
    names = {d['type'] + '|' + (d['code'] or d['name']): f"{d['type']} {d['name']}" for d in catalog}
    link = db.get_bambu(uid)
    matched = json.loads(link['matched'] or '{}') if link else {}
    low = 0
    for key, v in matched.items():
        out.append(('filament_' + _slug(key), f"Filament kvar {names.get(key, key)}", v.get('grams', 0), 'g', 'mdi:printer-3d-nozzle'))
        low += (v.get('grams') or 0) < int(db.get_pref(uid, 'low_grams') or 200)
    out.append(('filament_snart_slut', 'Filament snart slut', low, None, 'mdi:alert'))
    return out


def _clear_retained(s):
    """Tomma retained-meddelanden tar bort tidigare skickade sensorer (konfiguration och värden) ur brokern."""
    try:
        topics = json.loads(db.get_setting('ha_published') or '[]')
    except ValueError:
        topics = []
    if not topics:
        return
    try:
        c = _client(s)
        try:
            for t in topics:
                c.publish(t, b'', retain=True)
        finally:
            c.close()
        db.delete_setting('ha_published')
    except (mqtt.MqttError, OSError) as ex:
        print(f'[home assistant] sensorer kunde inte tas bort: {ex}', flush=True)


def publish_all(force=False):
    if not enabled() or (not force and time.time() - _last['ts'] < INTERVAL):
        return None
    _last['ts'] = time.time()
    s = settings()
    if check_host(s['host'], s['port']):
        return None
    if not _consented(s):
        _clear_retained(s)
        return None   # användaren har dragit tillbaka sitt samtycke: ta bort sensorerna ur brokern
    uid = int(s['user_id'])
    sensors = collect(uid)
    published = []
    c = _client(s)
    try:
        for sid, name, value, unit, icon in sensors:
            uniq = f'bambu_katalog_{sid}'
            state_topic = f'katalog/{sid}/state'
            cfg = {'name': name, 'unique_id': uniq, 'object_id': uniq, 'state_topic': state_topic, 'icon': icon, 'device': DEVICE}
            if unit:
                cfg['unit_of_measurement'] = unit
                cfg['state_class'] = 'measurement'
            c.publish(f"{s['prefix']}/sensor/{uniq}/config", json.dumps(cfg, ensure_ascii=False), retain=True)
            c.publish(state_topic, str(value), retain=True)
            published += [f"{s['prefix']}/sensor/{uniq}/config", state_topic]
    finally:
        c.close()
    db.set_setting('ha_published', json.dumps(published))
    return len(sensors)


def _consented(s):
    owner = db.get_user(int(s['user_id'])) if str(s['user_id'] or '').isdigit() else None
    return bool(owner) and (owner['is_admin'] or db.get_pref(owner['id'], 'integrations_ok') == '1')


def event(kind, data, uid=None):
    """Händelse till Home Assistant (används i automationer): ämnet katalog/event, JSON med type + data.
    Personliga händelser (uid) skickas bara för den användare som är vald för Home Assistant och har samtyckt."""
    if not enabled():
        return
    s = settings()
    if check_host(s['host'], s['port']) or (uid is not None and (str(uid) != str(s['user_id']) or not _consented(s))):
        return
    try:
        c = _client(s)
        try:
            c.publish('katalog/event', json.dumps({'type': kind, **data}, ensure_ascii=False))
        finally:
            c.close()
    except (mqtt.MqttError, OSError) as ex:
        print(f'[home assistant] händelsen kunde inte skickas: {ex}', flush=True)

"""Skrivarstatus i realtid via MQTT: lokalt (skrivarens IP + åtkomstkod) eller via Bambu-molnet (kopplat konto).

En bakgrundstråd per skrivare håller anslutningen öppen, slår ihop rapporterna (P1/A1 skickar bara ändringar)
och återansluter med fördröjning vid fel. Statusen hålls i minnet; inget skrivs till skrivaren utom
begäran om fullständig status ("pushall").
"""
import datetime
import json
import os
import threading
import time

from . import bambu, config, db, energy, mqtt

_TEST_PLAIN = os.environ.get('KATALOG_MQTT_TEST_PLAIN') == '1'   # tester: okrypterad MQTT mot 127.0.0.1
_states = {}          # printer_id -> dict
_threads = {}         # printer_id -> (Thread, stop-Event)
_lock = threading.Lock()
STATES = {'RUNNING': 'Skriver ut', 'PREPARE': 'Förbereder', 'PAUSE': 'Pausad', 'FINISH': 'Klar', 'FAILED': 'Misslyckades',
          'IDLE': 'Ledig', 'SLICING': 'Slicar'}
DEFAULT_WATTS = {'a1mini': 80, 'a1': 100, 'p1': 120, 'x1': 130, 'h2': 200}


def lan_ip_ok(host):
    """Skrivarens adress måste vara en vanlig IPv4-adress i det lokala nätet (inte loopback, 0.0.0.0, länklokal
    eller multicast) – annars kan funktionen användas för att nå containern själv eller andra tjänster."""
    import ipaddress
    try:
        ip = ipaddress.ip_address(str(host or ''))
    except ValueError:
        return False
    if _TEST_PLAIN and str(ip) == '127.0.0.1':
        return True
    return (ip.version == 4 and ip.is_private and not (ip.is_loopback or ip.is_link_local or ip.is_unspecified
            or ip.is_multicast or ip.is_reserved) and not str(ip).startswith('0.'))


def _merge(dst, src):
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _merge(dst[k], v)
        else:
            dst[k] = v


def _watts(row):
    if row['watts']:
        return row['watts']
    m = (row['model'] or '').lower().replace(' ', '')
    for needle, w in (('a1mini', 80), ('a1', 100), ('p1', 120), ('x1', 130), ('h2', 200)):
        if needle in m:
            return w
    return 120


def _connection(row):
    """(värd, port, användare, lösenord, tls, verifiera certifikat)."""
    if row['mode'] == 'lan':
        if not row['host'] or not row['access_code']:
            raise mqtt.MqttError('IP-adress och åtkomstkod krävs för lokal anslutning')
        if not lan_ip_ok(row['host']):
            raise mqtt.MqttError('Skrivarens adress är inte en adress i det lokala nätet')
        plain = _TEST_PLAIN and row['host'] == '127.0.0.1'
        port = int(os.environ.get('KATALOG_MQTT_TEST_PORT', '8883')) if plain else 8883
        return row['host'], port, 'bblp', row['access_code'], not plain, False
    link = db.get_bambu(row['user_id'])
    if not link or not link['token']:
        raise mqtt.MqttError('Bambu Lab-kontot är inte kopplat (eller inloggningen har gått ut)')
    uid = db.get_pref(row['user_id'], 'bambu_uid')
    if not uid:
        uid = bambu.get_uid(link['token'], link['region'])
        db.set_pref(row['user_id'], 'bambu_uid', uid)
    return bambu.MQTT_HOSTS.get(link['region'], bambu.MQTT_HOSTS['global']), 8883, f'u_{uid}', link['token'], True, True


def _run(printer_id, stop):
    delay = 15
    while not stop.is_set():
        row = None
        try:
            db.close_thread_conn()
            row = next((p for p in db.list_printers() if p['id'] == printer_id), None)
            if not row:
                return
            host, port, user, pw, tls, verify = _connection(row)
            client = mqtt.Client(host, port, user, pw, tls=tls, verify=verify, timeout=10).connect()
            try:
                client.subscribe(f'device/{row["serial"]}/report')
                client.publish(f'device/{row["serial"]}/request',
                               json.dumps({'pushing': {'sequence_id': '0', 'command': 'pushall', 'version': 1, 'push_target': 1}}))
                with _lock:
                    st = _states.setdefault(printer_id, {'report': {}})
                    st.update(connected=True, error=None)
                delay = 15
                for msg in client.messages(idle=1.0):
                    if stop.is_set():
                        break
                    if msg is None:
                        continue
                    _on_report(printer_id, row, msg[1])
            finally:
                client.close()
        except (mqtt.MqttError, OSError, bambu.BambuError, ValueError) as ex:
            with _lock:
                st = _states.setdefault(printer_id, {'report': {}})
                st.update(connected=False, error=str(ex)[:200])
        if stop.wait(delay):
            return
        delay = min(300, delay * 2)


def _on_report(printer_id, row, payload):
    try:
        data = json.loads(payload)
    except ValueError:
        return
    if not isinstance(data, dict) or not isinstance(data.get('print'), dict):
        return
    finished = None
    with _lock:
        st = _states.setdefault(printer_id, {'report': {}})
        before = st['report'].get('gcode_state')
        _merge(st['report'], data['print'])
        st['updated'] = time.time()
        st['connected'] = True
        now_state = st['report'].get('gcode_state')
        if now_state == 'RUNNING' and before != 'RUNNING':
            st['started'] = st.get('started') if before in ('PAUSE',) else time.time()
        if before in ('RUNNING', 'PAUSE') and now_state in ('FINISH', 'FAILED'):
            finished = row['user_id']
            report_copy, started = dict(st['report']), st.get('started')
    if finished:      # utskriften är klar: hämta den till loggen om en stund (molnet behöver tid på sig)
        threading.Thread(target=_notify_finished, args=(row, report_copy, now_state, started), daemon=True).start()
        threading.Timer(120, _import_later, args=(finished,)).start()


def _import_later(user_id):
    try:
        from . import printlog
        db.close_thread_conn()
        printlog.import_bambu(user_id)
    except Exception as ex:  # noqa: BLE001
        print(f'[skrivare] utskriften kunde inte hämtas till loggen: {ex!r}', flush=True)


def start(printer_id):
    stop_one(printer_id)
    ev = threading.Event()
    t = threading.Thread(target=_run, args=(printer_id, ev), name=f'skrivare-{printer_id}', daemon=True)
    with _lock:
        _threads[printer_id] = (t, ev)
    t.start()


def stop_one(printer_id):
    with _lock:
        t = _threads.pop(printer_id, None)
        _states.pop(printer_id, None)
    if t:
        t[1].set()


def start_all():
    for p in db.list_printers():
        start(p['id'])


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def status(user_id, matcher=None, intervals=None, now=None):
    """Sammanfattad status för användarens skrivare (för sidan Skrivare och /api/printers)."""
    now = now or time.time()
    out = []
    for row in db.list_printers(user_id):
        with _lock:
            st = json.loads(json.dumps(_states.get(row['id'], {})))
        r = st.get('report') or {}
        state = r.get('gcode_state') or ''
        pct = _num(r.get('mc_percent'))
        left = _num(r.get('mc_remaining_time'))          # minuter
        trays, units = [], []
        ams = r.get('ams') or {}
        for unit in (ams.get('ams') or []) if isinstance(ams, dict) else []:
            raw = _num(unit.get('humidity_raw'))
            units.append({'name': f"AMS {int(unit.get('id', 0)) + 1}", 'humidity': raw, 'level': unit.get('humidity'), 'temp': _num(unit.get('temp'))})
            for t in unit.get('tray') or []:
                if not isinstance(t, dict) or not t.get('tray_type'):
                    continue
                color = '#' + str(t.get('tray_color') or '')[:6]
                d = matcher.match({'filamentName': t.get('tray_sub_brands') or t.get('tray_type'), 'filamentId': t.get('tray_info_idx'),
                                   'color': color}) if matcher else None
                trays.append({'slot': f"AMS {int(unit.get('id', 0)) + 1} · fack {int(t.get('id', 0)) + 1}", 'type': t.get('tray_sub_brands') or t.get('tray_type'),
                              'color': color if len(color) == 7 else '', 'remain': _num(t.get('remain')),
                              'key': matcher.key(d) if d else None, 'active': str(ams.get('tray_now')) == str(int(unit.get('id', 0)) * 4 + int(t.get('id', 0)))})
        ext = r.get('vt_tray')
        if isinstance(ext, dict) and ext.get('tray_type'):
            color = '#' + str(ext.get('tray_color') or '')[:6]
            d = matcher.match({'filamentName': ext.get('tray_sub_brands') or ext.get('tray_type'), 'filamentId': ext.get('tray_info_idx'),
                               'color': color}) if matcher else None
            trays.append({'slot': 'Extern spole', 'type': ext.get('tray_sub_brands') or ext.get('tray_type'), 'color': color if len(color) == 7 else '',
                          'remain': None, 'key': matcher.key(d) if d else None, 'active': False})
        live = None
        if state in ('RUNNING', 'PAUSE') and st.get('started'):
            watts = _watts(row)
            elapsed_h = (now - st['started']) / 3600
            total_h = elapsed_h + (left or 0) / 60
            start_dt = datetime.datetime.fromtimestamp(st['started'], energy.TZ)
            if intervals:
                so_far = energy.window_cost(intervals, start_dt, elapsed_h, watts)['cost']
                whole = energy.window_cost(intervals, start_dt, total_h, watts)['cost']
            else:
                so_far = whole = None
            live = {'elapsedH': elapsed_h, 'totalH': total_h, 'elSoFar': so_far, 'elTotal': whole, 'watts': watts}
        out.append({'id': row['id'], 'name': row['name'], 'model': row['model'], 'mode': row['mode'], 'host': row['host'],
                    'connected': bool(st.get('connected')), 'error': st.get('error'), 'updated': st.get('updated'),
                    'state': state, 'stateText': STATES.get(state, state.title() if state else 'Väntar på status'),
                    'percent': pct, 'left': left, 'job': r.get('subtask_name'),
                    'layer': r.get('layer_num'), 'layers': r.get('total_layer_num'),
                    'nozzle': _num(r.get('nozzle_temper')), 'bed': _num(r.get('bed_temper')), 'trays': trays, 'ams': units, 'live': live})
    return out


# --- Underhåll: riktvärden, räknat i drifttimmar (från utskriftsloggen) eller dagar ---
MAINTENANCE = [
    ('platta', 'Rengör byggplattan med diskmedel och varmt vatten', 50, None),
    ('axlar', 'Rengör och smörj axlar och linjärskenor', 200, 90),
    ('zaxel', 'Smörj Z-axelns spindlar', 300, 180),
    ('remmar', 'Kontrollera remmarnas spänning', 300, 180),
    ('munstycke', 'Kontrollera munstycket (oftare med slipande material)', 500, None),
    ('ams', 'Byt eller torka torkmedlet i AMS', None, 60),
    ('kolfilter', 'Byt kolfiltret (skrivare med kammare)', None, 120),
]


def print_hours(user_id, row):
    """Drifttimmar för skrivaren enligt utskriftsloggen (utskrifter vars skrivarnamn innehåller skrivarens namn)."""
    name = (row['name'] or '').strip().lower()
    if not name:
        return 0.0
    r = db.conn().execute("SELECT COALESCE(SUM(hours), 0) AS h FROM prints WHERE user_id=? AND "
                          "(lower(printer)=? OR lower(printer) LIKE ?)", (user_id, name, name + ' · %')).fetchone()
    return float(r['h'])


def maintenance(user_id, row, now=None):
    now = now or time.time()
    hours = print_hours(user_id, row)
    done = db.maintenance_done(row['id'])
    out = []
    for task, text, every_h, every_days in MAINTENANCE:
        d = done.get(task)
        base_h = d['done_hours'] if d else 0.0
        base_t = d['done_ts'] if d else row['created']
        since_h = hours - base_h
        since_d = (now - base_t) / 86400
        frac = max(since_h / every_h if every_h else 0, since_d / every_days if every_days else 0)
        out.append({'task': task, 'text': text, 'since_h': since_h, 'since_d': since_d, 'every_h': every_h, 'every_days': every_days,
                    'due': frac >= 1, 'soon': 0.8 <= frac < 1, 'done': d['done_ts'] if d else None})
    return out, hours


# --- Avisering när en utskrift är klar eller misslyckas ---
def _notify_finished(row, report, state, started):
    try:
        db.close_thread_conn()
        if db.get_pref(row['user_id'], 'notify_prints') != '1':
            return
        target = db.get_pref(row['user_id'], 'notify')
        from . import alerts
        if not target or alerts.topic_too_short(target):
            return
        job = report.get('subtask_name') or 'Utskriften'
        dur = ''
        if started:
            mins = round((time.time() - started) / 60)
            dur = f' efter {mins // 60} h {mins % 60:02d} min' if mins >= 60 else f' efter {mins} min'
        if state == 'FINISH':
            title, msg = f'{job} är klar', f'{job} är klar på {row["name"]}{dur}.'
        else:
            title, msg = f'{job} misslyckades', f'{job} misslyckades på {row["name"]}{dur}. Ange gärna orsaken under Utskrifter.'
        alerts.send(target, title, msg)
        try:
            from . import homeassistant
            homeassistant.event('utskrift', {'skrivare': row['name'], 'utskrift': job, 'status': 'klar' if state == 'FINISH' else 'misslyckades'},
                                uid=row['user_id'])
        except Exception:  # noqa: BLE001
            pass
    except Exception as ex:  # noqa: BLE001
        print(f'[skrivare] avisering: {ex!r}', flush=True)


# --- Kamerabild (A1/P1 i lokalt läge: TLS på port 6000, JPEG-ramar) ---
_snapshots = {}
CAMERA_PORT = 6000


def snapshot(row, max_age=5):
    """En JPEG-bild från skrivarens kamera, eller None. Bara lokal anslutning (åtkomstkoden krävs)."""
    import socket
    import ssl
    import struct
    if row['mode'] != 'lan' or not row['host'] or not row['access_code'] or not lan_ip_ok(row['host']):
        return None
    model = (row['model'] or '').upper()
    if model.startswith(('X1', 'H2')):
        return None        # X1/H2 strömmar RTSP/H.264, inte JPEG
    hit = _snapshots.get(row['id'])
    if hit and time.time() - hit[0] < max_age:
        return hit[1]          # även misslyckade försök cachas en stund (None), så att sidan inte öppnar en ny anslutning per bild
    plain = _TEST_PLAIN and row['host'] == '127.0.0.1'
    port = int(os.environ.get('KATALOG_CAMERA_TEST_PORT', CAMERA_PORT)) if plain else CAMERA_PORT
    try:
        raw = socket.create_connection((row['host'], port), timeout=6)
        if not plain:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE      # skrivarens självsignerade certifikat i det lokala nätet
            raw = ctx.wrap_socket(raw, server_hostname=row['host'])
        with raw as s:
            s.sendall(struct.pack('<IIII', 0x40, 0x3000, 0, 0) + b'bblp'.ljust(32, b'\0') + row['access_code'].encode()[:32].ljust(32, b'\0'))
            head = b''
            while len(head) < 16:
                chunk = s.recv(16 - len(head))
                if not chunk:
                    return None
                head += chunk
            size = struct.unpack('<I', head[:4])[0]
            if not 0 < size <= 2_000_000:
                return None
            img = b''
            while len(img) < size:
                chunk = s.recv(min(65536, size - len(img)))
                if not chunk:
                    return None
                img += chunk
    except (OSError, ssl.SSLError, struct.error):
        _snapshots[row['id']] = (time.time(), None)
        return None
    if not (img.startswith(b'\xff\xd8') and img.rstrip(b'\0').endswith(b'\xff\xd9')):
        _snapshots[row['id']] = (time.time(), None)
        return None
    _snapshots[row['id']] = (time.time(), img)
    return img

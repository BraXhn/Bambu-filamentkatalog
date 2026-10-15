"""Start och kommandon.

  python -m katalog serve                     # startar allt (används av containern)
  python -m katalog update                    # kör prisuppdatering direkt
  python -m katalog users                     # lista användare
  python -m katalog add-user NAMN [--admin]   # lägg till (lösenord frågas eller slumpas)
  python -m katalog reset-password NAMN       # nytt slumpat lösenord
  python -m katalog unlock NAMN               # lås upp konto
"""
import argparse
import getpass
import os
import secrets
import shutil
import signal
import sys
import threading
import time

from . import __version__, config, db, security


def _data_dir_error():
    """None om datamappen är skrivbar, annars ett felmeddelande med lösning."""
    try:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        probe = config.DATA_DIR / '.write-test'
        probe.write_text('ok')
        probe.unlink()
        _warn_fuse()
        return None
    except OSError as e:
        return (f'Containern kan inte skriva till datamappen {config.DATA_DIR} ({e.strerror or e}). '
                f'Den kör som användare {os.getuid()}:{os.getgid()}. Ge den skrivrätt på servern, t.ex. i Unraids '
                f'terminal: chown -R {os.getuid()}:{os.getgid()} /mnt/user/appdata/bambu-filamentkatalog '
                f'– och starta sedan om containern.')


def _check_data_dir():
    err = _data_dir_error()
    if err:
        sys.exit('FEL: ' + err)


def _warn_fuse():
    """Varnar om /data ligger på ett FUSE-filsystem (t.ex. Unraids /mnt/user)."""
    try:
        best = ('', '')
        for line in open('/proc/mounts', encoding='utf-8'):
            parts = line.split()
            if len(parts) > 2 and str(config.DATA_DIR).startswith(parts[1]) and len(parts[1]) > len(best[0]):
                best = (parts[1], parts[2])
        if best[1].startswith('fuse'):
            print(f'[varning] {config.DATA_DIR} ligger på {best[1]} (t.ex. Unraids /mnt/user). Det fungerar, men '
                  'databasen blir stabilare och snabbare på en direkt disk, t.ex. /mnt/cache/appdata/... '
                  'eller /mnt/disk1/appdata/...', flush=True)
    except OSError:
        pass


def _seed():
    """Första start: kopiera inbyggda priser till datavolymen."""
    config.WWW_DIR.mkdir(parents=True, exist_ok=True)
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    if not (config.WWW_DIR / 'offers.json').exists():
        for name in ('offers.json', 'offers.json.gz'):
            if (config.SEED_DIR / name).exists():
                shutil.copy2(config.SEED_DIR / name, config.WWW_DIR / name)
        print('[app] första start: inbyggda priser kopierade till datavolymen', flush=True)


def _bootstrap_admin():
    if db.user_count() > 0:
        db.delete_setting('setup_code')
        return
    if config.ADMIN_USERNAME and config.ADMIN_PASSWORD:
        err = security.validate_username(config.ADMIN_USERNAME) or \
            security.validate_password(config.ADMIN_PASSWORD, config.ADMIN_USERNAME)
        if err:
            sys.exit(f'FEL i ADMIN_USERNAME/ADMIN_PASSWORD: {err}')
        db.create_user(config.ADMIN_USERNAME, security.hash_password(config.ADMIN_PASSWORD), is_admin=True)
        print(f'[konto] administratören "{config.ADMIN_USERNAME}" skapades från miljövariabler. '
              f'Ta gärna bort ADMIN_PASSWORD ur konfigurationen nu.', flush=True)
        return
    if config.ALLOW_PUBLIC_SETUP:
        print('[konto] Inga användare än. Öppna /setup i webbläsaren för att skapa en administratör.', flush=True)
        return
    code = db.get_setting('setup_code')
    if not code:
        alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
        code = '-'.join(''.join(secrets.choice(alphabet) for _ in range(4)) for _ in range(3))
        db.set_setting('setup_code', code)
    bar = '=' * 64
    print(f'\n{bar}\n  Inga användare än. Öppna katalogen i webbläsaren och ange\n'
          f'  installationskoden:  {code}\n{bar}\n', flush=True)


def serve():
    from . import scheduler, web
    from .static import store
    bar = '=' * 68
    print(f'{bar}\n  Bambu filamentkatalog {__version__}\n{bar}', flush=True)
    print(f'[start] användare {os.getuid()}:{os.getgid()}, data i {config.DATA_DIR}, tidszon {config.TIMEZONE}', flush=True)
    err = _data_dir_error()
    if err:
        web.STARTUP_ERROR = err
        print('[FEL] ' + err, flush=True)
        print('[FEL] Webbservern startar ändå och visar felet i webbläsaren.', flush=True)
    else:
        db.init()
        db.recover_runs()
        _seed()
        _bootstrap_admin()
    n = store.load()
    srv = web.make_server()
    print(f'[start] {n} filer i minnet, lyssnar på port {config.LISTEN_PORT}', flush=True)
    for ip in web.local_addresses():
        print(f'[start] Öppna katalogen på:  http://{ip}:{config.LISTEN_PORT}', flush=True)
    print('[start] (Delar containern Unraids IP används i stället porten du mappat.) Varje sidvisning loggas nedan.', flush=True)

    def stop(*_):
        print('[stopp] avslutar', flush=True)
        threading.Thread(target=srv.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    if not err:
        scheduler.start()
    try:
        srv.serve_forever(poll_interval=0.5)
    finally:
        srv.server_close()


def _ask_password(username):
    if not sys.stdin.isatty():
        return None
    while True:
        pw = getpass.getpass('Lösenord (tomt = slumpa): ')
        if not pw:
            return None
        err = security.validate_password(pw, username)
        if err:
            print(err)
            continue
        if getpass.getpass('Upprepa: ') == pw:
            return pw
        print('Lösenorden matchar inte.')


def main(argv=None):
    p = argparse.ArgumentParser(prog='katalog', description='Bambu filamentkatalog')
    sub = p.add_subparsers(dest='cmd', required=True)
    sub.add_parser('serve')
    up = sub.add_parser('update')
    up.add_argument('--sources', help='kommaseparerat: 3djake,3dprima,tradera')
    sub.add_parser('users')
    a = sub.add_parser('add-user')
    a.add_argument('username')
    a.add_argument('--admin', action='store_true')
    for name in ('reset-password', 'unlock', 'delete-user'):
        sub.add_parser(name).add_argument('username')
    args = p.parse_args(argv)

    if args.cmd == 'serve':
        return serve()
    _check_data_dir()
    db.init()
    if args.cmd == 'update':
        if args.sources:
            from .prices.update import SOURCES
            config.PRICE_SOURCES = [s.strip() for s in args.sources.split(',') if s.strip()]
            unknown = [s for s in config.PRICE_SOURCES if s not in SOURCES]
            if unknown:
                sys.exit(f"Okänd källa: {', '.join(unknown)}. Välj bland: {', '.join(SOURCES)}")
        from .scheduler import run_now
        status = run_now('cli')
        if status is None:
            sys.exit('En uppdatering pågår redan.')
        print('Status:', status)
        return
    if args.cmd == 'users':
        for u in db.list_users():
            print(f"{u['username']:<24} {'admin' if u['is_admin'] else 'användare':<10} "
                  f"{'LÅST' if u['locked_until'] > time.time() else ''}")
        return
    if args.cmd == 'add-user':
        err = security.validate_username(args.username)
        if err:
            sys.exit(err)
        if db.get_user_by_name(args.username):
            sys.exit('Användaren finns redan.')
        pw = _ask_password(args.username)
        gen = pw is None
        pw = pw or security.generate_password()
        db.create_user(args.username, security.hash_password(pw), is_admin=args.admin, must_change_pw=gen)
        print(f'Skapade {args.username}' + (f'. Lösenord: {pw} (måste bytas vid inloggning)' if gen else '.'))
        return
    u = db.get_user_by_name(args.username)
    if not u:
        sys.exit('Användaren finns inte.')
    if args.cmd == 'reset-password':
        pw = security.generate_password()
        db.update_user(u['id'], pw_hash=security.hash_password(pw), must_change_pw=1, failed_logins=0, locked_until=0)
        db.delete_user_sessions(u['id'])
        print(f'Nytt lösenord för {u["username"]}: {pw} (måste bytas vid inloggning)')
    elif args.cmd == 'unlock':
        db.update_user(u['id'], failed_logins=0, locked_until=0)
        print('Upplåst.')
    elif args.cmd == 'delete-user':
        if u['is_admin'] and db.admin_count() <= 1:
            sys.exit('Kan inte ta bort den sista administratören.')
        db.delete_user(u['id'])
        print('Borttagen.')


if __name__ == '__main__':
    main()

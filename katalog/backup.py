"""Säkerhetskopior: zip med databasen (konsekvent kopia), prishistorik/tillstånd, priser och kvitton.

En kopia görs automatiskt varje natt (BACKUP_TIME) till /data/backups; de BACKUP_KEEP senaste sparas.
Återställning läser tillbaka databasen med SQLite backup-API och skriver tillbaka filerna.
"""
import datetime
import os
import re
import tempfile
import zipfile
from pathlib import Path

from . import config, db

NAME = re.compile(r'katalog-\d{8}-\d{4}\.zip')


def make_zip(target):
    """Skriver säkerhetskopian till target (sökväg eller filobjekt)."""
    with tempfile.TemporaryDirectory() as tmp:
        dbfile = os.path.join(tmp, 'katalog.db')
        db.backup_to(dbfile)
        with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as z:
            z.write(dbfile, 'katalog.db')
            for sub in (config.STATE_DIR, config.WWW_DIR):
                if sub.is_dir():
                    for fp in sorted(sub.glob('*.json')):
                        z.write(fp, f'{sub.name}/{fp.name}')
            if config.RECEIPT_DIR.is_dir():
                for fp in sorted(config.RECEIPT_DIR.rglob('*')):
                    if fp.is_file():
                        z.write(fp, 'receipts/' + fp.relative_to(config.RECEIPT_DIR).as_posix())


def create(now=None):
    """Ny säkerhetskopia i BACKUP_DIR; gamla rensas. Returnerar filnamnet."""
    if now is None:
        from zoneinfo import ZoneInfo
        try:
            now = datetime.datetime.now(ZoneInfo(config.TIMEZONE))
        except Exception:  # noqa: BLE001
            now = datetime.datetime.now()
    config.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    name = f'katalog-{now:%Y%m%d-%H%M}.zip'
    tmp = config.BACKUP_DIR / ('.tmp-' + name)
    make_zip(tmp)
    os.chmod(tmp, 0o600)   # innehåller lösenordshashar och inloggningar
    os.replace(tmp, config.BACKUP_DIR / name)
    for old in list_backups()[config.BACKUP_KEEP:]:
        try:
            (config.BACKUP_DIR / old['name']).unlink()
        except OSError:
            pass
    return name


def list_backups():
    if not config.BACKUP_DIR.is_dir():
        return []
    out = []
    for p in config.BACKUP_DIR.iterdir():
        if NAME.fullmatch(p.name):
            st = p.stat()
            out.append({'name': p.name, 'size': st.st_size, 'mtime': st.st_mtime})
    return sorted(out, key=lambda x: x['name'], reverse=True)


def path_of(name):
    return config.BACKUP_DIR / name if NAME.fullmatch(name or '') and (config.BACKUP_DIR / name).is_file() else None


def due(now, last_name):
    """Ska nattens kopia göras nu? (efter BACKUP_TIME och ingen kopia gjord i dag)."""
    m = re.fullmatch(r'(\d{1,2}):(\d{2})', config.BACKUP_TIME or '')
    if not m:
        return False
    at = now.replace(hour=int(m[1]), minute=int(m[2]), second=0, microsecond=0)
    return now >= at and not (last_name or '').startswith(f'katalog-{now:%Y%m%d}')


def restore(name):
    """Återställer en säkerhetskopia. Filer i zip-filen skrivs bara till kända mappar (ingen sökvägsflykt)."""
    path = path_of(name)
    if not path:
        raise ValueError('Säkerhetskopian finns inte.')
    targets = {'state': config.STATE_DIR, 'www': config.WWW_DIR, 'receipts': config.RECEIPT_DIR}
    with zipfile.ZipFile(path) as z, tempfile.TemporaryDirectory() as tmp:
        if 'katalog.db' not in z.namelist():
            raise ValueError('Säkerhetskopian saknar databasen.')
        z.extract('katalog.db', tmp)
        db.restore_from(Path(tmp) / 'katalog.db')
        for info in z.infolist():
            parts = info.filename.split('/')
            if info.is_dir() or len(parts) < 2 or parts[0] not in targets or any(p in ('', '.', '..') for p in parts[1:]):
                continue
            dest = targets[parts[0]].joinpath(*parts[1:])
            dest.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(dest, 'wb') as out:
                out.write(src.read())

"""SQLite-lagring för användare, sessioner, ägda filament och prisuppdateringar."""
import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager

from . import config

_lock = threading.RLock()
_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    pw_hash TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0,
    created REAL NOT NULL,
    last_login REAL,
    failed_logins INTEGER NOT NULL DEFAULT 0,
    locked_until REAL NOT NULL DEFAULT 0,
    must_change_pw INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,                -- sha256 av sessionstoken (själva token lagras aldrig)
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    csrf TEXT NOT NULL,
    created REAL NOT NULL,
    last_seen REAL NOT NULL,
    ip TEXT,
    user_agent TEXT
);
CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);
CREATE TABLE IF NOT EXISTS owned (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    items TEXT NOT NULL,
    updated REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS cart (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key TEXT NOT NULL,                  -- katalognyckel, t.ex. "PLA Basic|10101"
    qty INTEGER NOT NULL DEFAULT 1,
    pack TEXT NOT NULL DEFAULT 'any',   -- any | Spole | Refill
    added REAL NOT NULL,
    PRIMARY KEY (user_id, key)
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    started REAL NOT NULL,
    finished REAL,
    trigger TEXT NOT NULL,
    status TEXT NOT NULL,              -- running | ok | partial | failed
    summary TEXT,                      -- JSON per källa
    log TEXT
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS bambu_links (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    account TEXT NOT NULL,
    region TEXT NOT NULL DEFAULT 'global',
    token TEXT,                         -- Bambu-token (~90 dagar), visas aldrig i gränssnittet
    token_expires REAL,
    linked REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'ok',  -- ok | expired | error
    error TEXT,
    last_sync REAL,
    last_attempt REAL,
    matched TEXT,                       -- JSON {katalognyckel: {grams, total, spools}}
    unmatched TEXT                      -- JSON [{name, color, grams}]
);
CREATE TABLE IF NOT EXISTS bambu_pending (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    account TEXT NOT NULL,
    region TEXT NOT NULL,
    step TEXT NOT NULL,                 -- code | tfa
    tfa_key TEXT,
    created REAL NOT NULL
);
"""


def _connect():
    conn = sqlite3.connect(str(config.DB_PATH), timeout=15, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    # WAL kräver delat minne (mmap) som Unraids /mnt/user (FUSE) inte hanterar pålitligt.
    # Vanlig journal räcker gott för den här mängden skrivningar.
    conn.execute('PRAGMA journal_mode=' + ('WAL' if os.environ.get('SQLITE_WAL', '').lower() == 'true' else 'DELETE'))
    conn.execute('PRAGMA busy_timeout=15000')
    conn.execute('PRAGMA synchronous=NORMAL')
    return conn


def close_thread_conn():
    """Stänger trådens anslutning (anropas när en förfrågan är klar)."""
    c = getattr(_local, 'conn', None)
    if c is not None:
        _local.conn = None
        try:
            c.close()
        except Exception:  # noqa: BLE001
            pass


def conn():
    c = getattr(_local, 'conn', None)
    if c is None:
        c = _connect()
        _local.conn = c
    return c


@contextmanager
def tx():
    c = conn()
    with _lock:
        c.execute('BEGIN IMMEDIATE')
        try:
            yield c
            c.execute('COMMIT')
        except BaseException:
            c.execute('ROLLBACK')
            raise


def init():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    with _lock:
        conn().executescript(SCHEMA)
        try:  # databasen innehåller inloggningsnycklar: bara containeranvändaren får läsa den
            import os
            os.chmod(config.DB_PATH, 0o600)
        except OSError:
            pass
        # Avbrutna körningar (t.ex. omstart mitt i) markeras som misslyckade.
        conn().execute("UPDATE runs SET status='failed', finished=?, log=COALESCE(log,'')||'\nAvbruten (omstart).' "
                       "WHERE status='running'", (time.time(),))


# --- settings ---
def get_setting(key, default=None):
    r = conn().execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
    return r['value'] if r else default


def set_setting(key, value):
    with tx() as c:
        c.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                  (key, value))


def delete_setting(key):
    with tx() as c:
        c.execute('DELETE FROM settings WHERE key=?', (key,))


# --- users ---
def user_count():
    return conn().execute('SELECT COUNT(*) FROM users').fetchone()[0]


def admin_count():
    return conn().execute('SELECT COUNT(*) FROM users WHERE is_admin=1').fetchone()[0]


def get_user(user_id):
    return conn().execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()


def get_user_by_name(username):
    return conn().execute('SELECT * FROM users WHERE username=? COLLATE NOCASE', (username,)).fetchone()


def list_users():
    return conn().execute(
        'SELECT u.*, (SELECT COUNT(*) FROM sessions s WHERE s.user_id=u.id) AS sessions '
        'FROM users u ORDER BY u.username COLLATE NOCASE').fetchall()


def create_user(username, pw_hash, is_admin=False, must_change_pw=False):
    with tx() as c:
        cur = c.execute('INSERT INTO users(username,pw_hash,is_admin,created,must_change_pw) VALUES(?,?,?,?,?)',
                        (username, pw_hash, 1 if is_admin else 0, time.time(), 1 if must_change_pw else 0))
        return cur.lastrowid


def update_user(user_id, **fields):
    allowed = {'pw_hash', 'is_admin', 'last_login', 'failed_logins', 'locked_until', 'must_change_pw'}
    keys = [k for k in fields if k in allowed]
    if not keys:
        return
    with tx() as c:
        c.execute(f"UPDATE users SET {', '.join(k + '=?' for k in keys)} WHERE id=?",
                  [fields[k] for k in keys] + [user_id])


def delete_user(user_id):
    with tx() as c:
        c.execute('DELETE FROM users WHERE id=?', (user_id,))


def delete_user_keep_admin(user_id):
    """Tar bort användaren om det fortfarande finns minst en annan administratör. Atomiskt."""
    with tx() as c:
        return c.execute('DELETE FROM users WHERE id=? AND (is_admin=0 OR '
                         '(SELECT COUNT(*) FROM users WHERE is_admin=1) > 1)', (user_id,)).rowcount == 1


def toggle_admin_keep_one(user_id):
    """Växlar admin-roll; nekar om det skulle lämna noll administratörer. Atomiskt."""
    with tx() as c:
        return c.execute('UPDATE users SET is_admin = 1 - is_admin WHERE id=? AND (is_admin=0 OR '
                         '(SELECT COUNT(*) FROM users WHERE is_admin=1) > 1)', (user_id,)).rowcount == 1


def register_failure(user_id, max_fails, lock_seconds, now):
    """Atomisk ökning av misslyckade försök; låser kontot vid gränsen."""
    with tx() as c:
        c.execute('UPDATE users SET failed_logins = failed_logins + 1 WHERE id=?', (user_id,))
        c.execute('UPDATE users SET locked_until=?, failed_logins=0 WHERE id=? AND failed_logins>=?',
                  (now + lock_seconds, user_id, max_fails))


# --- sessions ---
def create_session(sid_hash, user_id, csrf, ip, user_agent):
    now = time.time()
    with tx() as c:
        c.execute('INSERT INTO sessions(id,user_id,csrf,created,last_seen,ip,user_agent) VALUES(?,?,?,?,?,?,?)',
                  (sid_hash, user_id, csrf, now, now, ip, (user_agent or '')[:200]))


def get_session(sid_hash):
    return conn().execute(
        'SELECT s.*, u.username, u.is_admin, u.must_change_pw FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.id=?',
        (sid_hash,)).fetchone()


def touch_session(sid_hash, ts):
    with tx() as c:
        c.execute('UPDATE sessions SET last_seen=? WHERE id=?', (ts, sid_hash))


def delete_session(sid_hash):
    with tx() as c:
        c.execute('DELETE FROM sessions WHERE id=?', (sid_hash,))


def delete_user_sessions(user_id, except_id=None):
    with tx() as c:
        if except_id:
            c.execute('DELETE FROM sessions WHERE user_id=? AND id<>?', (user_id, except_id))
        else:
            c.execute('DELETE FROM sessions WHERE user_id=?', (user_id,))


def purge_sessions(idle_before, created_before):
    with tx() as c:
        c.execute('DELETE FROM sessions WHERE last_seen<? OR created<?', (idle_before, created_before))


# --- owned ---
def get_owned(user_id):
    r = conn().execute('SELECT items FROM owned WHERE user_id=?', (user_id,)).fetchone()
    return json.loads(r['items']) if r else None


def set_owned(user_id, items):
    with tx() as c:
        c.execute('INSERT INTO owned(user_id,items,updated) VALUES(?,?,?) '
                  'ON CONFLICT(user_id) DO UPDATE SET items=excluded.items, updated=excluded.updated',
                  (user_id, json.dumps(items, ensure_ascii=False), time.time()))


# --- runs ---
def start_run(trigger):
    with tx() as c:
        if c.execute("SELECT 1 FROM runs WHERE status='running'").fetchone():
            return None
        return c.execute("INSERT INTO runs(started,trigger,status) VALUES(?,?,'running')",
                         (time.time(), trigger)).lastrowid


def finish_run(run_id, status, summary, log):
    with tx() as c:
        c.execute('UPDATE runs SET finished=?, status=?, summary=?, log=? WHERE id=?',
                  (time.time(), status, json.dumps(summary, ensure_ascii=False), log, run_id))
        # Spara de 60 senaste körningarna.
        c.execute('DELETE FROM runs WHERE id NOT IN (SELECT id FROM runs ORDER BY id DESC LIMIT 60)')


def recent_runs(limit=10):
    return conn().execute('SELECT * FROM runs ORDER BY id DESC LIMIT ?', (limit,)).fetchall()


def running_run():
    return conn().execute("SELECT * FROM runs WHERE status='running' ORDER BY id DESC LIMIT 1").fetchone()


def complete_login(user_id, now):
    """Registrerar lyckad inloggning, men bara om kontot inte hann spärras under kontrollen."""
    with tx() as c:
        return c.execute('UPDATE users SET failed_logins=0, last_login=? WHERE id=? AND locked_until<=?',
                         (now, user_id, now)).rowcount == 1


# --- Bambu Lab-koppling ---
def get_bambu(user_id):
    return conn().execute('SELECT * FROM bambu_links WHERE user_id=?', (user_id,)).fetchone()


def list_bambu():
    return conn().execute('SELECT * FROM bambu_links WHERE token IS NOT NULL').fetchall()


def save_bambu_token(user_id, account, region, token, expires):
    now = time.time()
    with tx() as c:
        c.execute('INSERT INTO bambu_links(user_id,account,region,token,token_expires,linked,status,error) '
                  "VALUES(?,?,?,?,?,?,'ok',NULL) ON CONFLICT(user_id) DO UPDATE SET account=excluded.account, "
                  "region=excluded.region, token=excluded.token, token_expires=excluded.token_expires, "
                  "status='ok', error=NULL", (user_id, account, region, token, expires, now))
        c.execute('DELETE FROM bambu_pending WHERE user_id=?', (user_id,))


def save_bambu_sync(user_id, status, error=None, matched=None, unmatched=None):
    now = time.time()
    with tx() as c:
        if status == 'ok':
            c.execute("UPDATE bambu_links SET status='ok', error=NULL, last_sync=?, last_attempt=?, matched=?, "
                      'unmatched=? WHERE user_id=?', (now, now, json.dumps(matched, ensure_ascii=False),
                                                      json.dumps(unmatched, ensure_ascii=False), user_id))
        else:
            # Behåll senast synkade innehav; bara status/fel ändras.
            c.execute('UPDATE bambu_links SET status=?, error=?, last_attempt=?' +
                      (', token=NULL' if status == 'expired' else '') + ' WHERE user_id=?',
                      (status, (error or '')[:300], now, user_id))


def delete_bambu(user_id):
    with tx() as c:
        c.execute('DELETE FROM bambu_links WHERE user_id=?', (user_id,))
        c.execute('DELETE FROM bambu_pending WHERE user_id=?', (user_id,))


def set_bambu_pending(user_id, account, region, step, tfa_key=None):
    with tx() as c:
        c.execute('INSERT INTO bambu_pending(user_id,account,region,step,tfa_key,created) VALUES(?,?,?,?,?,?) '
                  'ON CONFLICT(user_id) DO UPDATE SET account=excluded.account, region=excluded.region, '
                  'step=excluded.step, tfa_key=excluded.tfa_key, created=excluded.created',
                  (user_id, account, region, step, tfa_key, time.time()))


def get_bambu_pending(user_id, max_age=900):
    r = conn().execute('SELECT * FROM bambu_pending WHERE user_id=?', (user_id,)).fetchone()
    if r and r['created'] < time.time() - max_age:
        with tx() as c:
            c.execute('DELETE FROM bambu_pending WHERE user_id=?', (user_id,))
        return None
    return r


def clear_bambu_pending(user_id):
    with tx() as c:
        c.execute('DELETE FROM bambu_pending WHERE user_id=?', (user_id,))


# --- inköpslista ---
CART_MAX_ITEMS = 200
CART_MAX_QTY = 50


def get_cart(user_id):
    return conn().execute('SELECT key, qty, pack FROM cart WHERE user_id=? ORDER BY added', (user_id,)).fetchall()


def set_cart_item(user_id, key, qty, pack='any'):
    """qty <= 0 tar bort raden. Returnerar False om listan är full."""
    with tx() as c:
        if qty <= 0:
            c.execute('DELETE FROM cart WHERE user_id=? AND key=?', (user_id, key))
            return True
        exists = c.execute('SELECT 1 FROM cart WHERE user_id=? AND key=?', (user_id, key)).fetchone()
        if not exists and c.execute('SELECT COUNT(*) FROM cart WHERE user_id=?', (user_id,)).fetchone()[0] >= CART_MAX_ITEMS:
            return False
        c.execute('INSERT INTO cart(user_id,key,qty,pack,added) VALUES(?,?,?,?,?) '
                  'ON CONFLICT(user_id,key) DO UPDATE SET qty=excluded.qty, pack=excluded.pack',
                  (user_id, key, min(int(qty), CART_MAX_QTY), pack, time.time()))
        return True


def clear_cart(user_id):
    with tx() as c:
        c.execute('DELETE FROM cart WHERE user_id=?', (user_id,))

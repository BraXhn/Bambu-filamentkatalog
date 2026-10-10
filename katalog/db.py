"""SQLite-lagring för användare, sessioner, ägda filament och prisuppdateringar."""
import json
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
"""


def _connect():
    conn = sqlite3.connect(str(config.DB_PATH), timeout=15, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=NORMAL')
    return conn


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

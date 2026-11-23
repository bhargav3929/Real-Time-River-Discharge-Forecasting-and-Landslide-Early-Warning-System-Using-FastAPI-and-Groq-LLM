"""Embedded SQLite (WAL): saved key, history, demo run counts."""
import json
import sqlite3
import time
from contextlib import contextmanager

from .config import S

DAY = 86400


@contextmanager
def conn():
    S.DATA_DIR.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(S.DB_PATH, timeout=10, isolation_level=None)
    c.row_factory = sqlite3.Row
    try:
        yield c
    finally:
        c.close()


def init_db() -> None:
    with conn() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS kv (name TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT, ts REAL,
                kind TEXT, title TEXT, payload TEXT);
            CREATE INDEX IF NOT EXISTS idx_history_owner ON history(owner, ts);
            CREATE TABLE IF NOT EXISTS demo_runs (tid TEXT PRIMARY KEY, runs INTEGER, first_ts REAL);
            CREATE TABLE IF NOT EXISTS demo_daily (day TEXT PRIMARY KEY, n INTEGER);
            """
        )


# ---- saved Groq key -------------------------------------------------------
def get_saved_key() -> str:
    with conn() as c:
        r = c.execute("SELECT value FROM kv WHERE name='groq_api_key'").fetchone()
    return r["value"] if r else ""


def set_saved_key(key: str) -> None:
    with conn() as c:
        c.execute("INSERT INTO kv(name,value) VALUES('groq_api_key',?) "
                  "ON CONFLICT(name) DO UPDATE SET value=excluded.value", (key,))


def delete_saved_key() -> None:
    with conn() as c:
        c.execute("DELETE FROM kv WHERE name='groq_api_key'")


# ---- history --------------------------------------------------------------
def add_history(owner: str, kind: str, title: str, payload: dict) -> int:
    with conn() as c:
        cur = c.execute("INSERT INTO history(owner,ts,kind,title,payload) VALUES(?,?,?,?,?)",
                        (owner, time.time(), kind, title, json.dumps(payload)))
        return cur.lastrowid


def list_history(owner: str, limit: int = 40) -> list:
    with conn() as c:
        rows = c.execute("SELECT id,ts,kind,title FROM history WHERE owner=? ORDER BY id DESC LIMIT ?",
                         (owner, limit)).fetchall()
    return [dict(r) for r in rows]


def get_history(owner: str, hid: int):
    with conn() as c:
        r = c.execute("SELECT id,ts,kind,title,payload FROM history WHERE owner=? AND id=?", (owner, hid)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["payload"] = json.loads(d["payload"])
    return d


def clear_history(owner: str) -> None:
    with conn() as c:
        c.execute("DELETE FROM history WHERE owner=?", (owner,))


# ---- demo run counting ----------------------------------------------------
def purge_demo() -> None:
    cutoff = time.time() - DAY
    with conn() as c:
        old = [r["tid"] for r in c.execute("SELECT tid FROM demo_runs WHERE first_ts<?", (cutoff,)).fetchall()]
        for tid in old:
            c.execute("DELETE FROM history WHERE owner=?", (tid,))
        c.execute("DELETE FROM demo_runs WHERE first_ts<?", (cutoff,))
        c.execute("DELETE FROM history WHERE ts<? AND owner<>'local'", (cutoff,))


def demo_runs_used(tid: str) -> int:
    with conn() as c:
        r = c.execute("SELECT runs FROM demo_runs WHERE tid=?", (tid,)).fetchone()
    return r["runs"] if r else 0


def demo_daily_count() -> int:
    with conn() as c:
        r = c.execute("SELECT n FROM demo_daily WHERE day=?", (time.strftime("%Y-%m-%d", time.gmtime()),)).fetchone()
    return r["n"] if r else 0


def demo_record_run(tid: str) -> None:
    day = time.strftime("%Y-%m-%d", time.gmtime())
    with conn() as c:
        c.execute("INSERT INTO demo_runs(tid,runs,first_ts) VALUES(?,1,?) "
                  "ON CONFLICT(tid) DO UPDATE SET runs=runs+1", (tid, time.time()))
        c.execute("INSERT INTO demo_daily(day,n) VALUES(?,1) ON CONFLICT(day) DO UPDATE SET n=n+1", (day,))

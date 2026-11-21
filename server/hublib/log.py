"""The hub's own records, one SQLite file per user: events apps send, which nudges fired, and the
health records the phone sends (hublib/health.py has those).

Everything else the hub shows is read fresh from the other services, so this is the only data here
that needs a backup. Events and nudges are kept 400 days; health records are kept for good, since
there's no other copy.
"""
import json
import os
import re
import sqlite3
import threading
import time

from . import health

KEEP_DAYS = 400
APP_NAME = re.compile(r"xrn[a-z]{2,20}")
KIND_NAME = re.compile(r"[a-z][a-z0-9_.-]{0,39}")
MAX_TITLE = 120
MAX_DETAIL = 2000
MAX_NUMBER = 2**53  # past this a page can't read a number exactly, and Infinity and NaN aren't JSON at all
SCHEMA = """
CREATE TABLE IF NOT EXISTS events(
    id INTEGER PRIMARY KEY, app TEXT NOT NULL, kind TEXT NOT NULL, at INTEGER NOT NULL,
    title TEXT NOT NULL DEFAULT '', value REAL, detail TEXT NOT NULL DEFAULT '{}');
CREATE INDEX IF NOT EXISTS events_at ON events(at);
CREATE INDEX IF NOT EXISTS events_app ON events(app, at);
CREATE TABLE IF NOT EXISTS fired(
    rule TEXT NOT NULL, day TEXT NOT NULL, name TEXT NOT NULL, at INTEGER NOT NULL,
    why TEXT NOT NULL, action TEXT NOT NULL, PRIMARY KEY(rule, day));
"""


class BadEvent(ValueError):
    pass


def clean_event(raw):
    """An app's event as it may be stored. Raises BadEvent. The hub keeps metadata: a mail client
    says how many arrived, never who from."""
    if not isinstance(raw, dict):
        raise BadEvent("an event must be an object")
    app, kind, at = raw.get("app"), raw.get("kind"), raw.get("at")
    if not isinstance(app, str) or not APP_NAME.fullmatch(app):
        raise BadEvent("bad app")
    if not isinstance(kind, str) or not KIND_NAME.fullmatch(kind):
        raise BadEvent("bad kind")
    if isinstance(at, bool) or not isinstance(at, (int, float)) or not 0 < at < MAX_NUMBER:
        raise BadEvent("bad time")
    title = raw.get("title", "")
    if not isinstance(title, str) or len(title) > MAX_TITLE:
        raise BadEvent("bad title")
    value = raw.get("value")
    if value is not None and (
        isinstance(value, bool) or not isinstance(value, (int, float)) or not -MAX_NUMBER < value < MAX_NUMBER
    ):
        raise BadEvent("bad value")
    try:
        detail = json.dumps(raw.get("detail") or {}, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except ValueError:
        raise BadEvent("bad detail") from None
    if not isinstance(raw.get("detail") or {}, dict) or len(detail) > MAX_DETAIL:
        raise BadEvent("bad detail")
    return app, kind, int(at), title, value, detail


class HubLog:
    def __init__(self, data_dir):
        self.data_dir = data_dir
        self.local = threading.local()
        self.prepared = set()  # users whose database has had the schema run since the start
        self.prepare_lock = threading.Lock()

    def db(self, user):
        """This thread's connection to [user]'s database. Each connection to the hub is a thread
        of its own, so the schema runs once per user, not at every thread's first use; WAL mode
        stays set in the file."""
        pool = getattr(self.local, "pool", None)
        if pool is None:
            pool = self.local.pool = {}
        if user not in pool:
            folder = os.path.join(self.data_dir, "users", user)
            os.makedirs(folder, mode=0o700, exist_ok=True)
            conn = sqlite3.connect(os.path.join(folder, "hub.sqlite"), timeout=5)
            with self.prepare_lock:
                if user not in self.prepared:
                    conn.execute("PRAGMA journal_mode=WAL")
                    conn.executescript(SCHEMA + health.SCHEMA)
                    if "dismissed" not in [c[1] for c in conn.execute("PRAGMA table_info(fired)")]:
                        conn.execute("ALTER TABLE fired ADD COLUMN dismissed INTEGER NOT NULL DEFAULT 0")
                    self.prepared.add(user)
            pool[user] = conn
        return pool[user]

    def add(self, user, events):
        rows = [clean_event(e) for e in events]
        db = self.db(user)
        with db:
            db.executemany(
                "INSERT INTO events(app, kind, at, title, value, detail) VALUES(?,?,?,?,?,?)", rows,
            )
        return len(rows)

    def events(self, user, since_ms):
        rows = self.db(user).execute(
            "SELECT at, app, kind, title, value FROM events WHERE at >= ? ORDER BY at DESC LIMIT 400",
            (since_ms,),
        )
        return [{"at": a, "app": p, "kind": k, "title": t, "value": v} for a, p, k, t, v in rows]

    def between(self, user, lo_ms, hi_ms, app=None, limit=2000):
        """Events from lo_ms up to (not including) hi_ms, oldest first, with their detail; for the API."""
        sql = "SELECT at, app, kind, title, value, detail FROM events WHERE at >= ? AND at < ?"
        params = [lo_ms, hi_ms]
        if app:
            sql += " AND app = ?"
            params.append(app)
        rows = self.db(user).execute(sql + " ORDER BY at, id LIMIT ?", (*params, limit))
        found = []
        for at, name, kind, title, value, detail in rows:
            try:
                extra = json.loads(detail)
            except ValueError:
                extra = {}
            found.append({"at": at, "app": name, "kind": kind, "title": title, "value": value, "detail": extra})
        return found

    def latest(self, user, app):
        """The newest event from [app], or None; xrnmail's says when the next batch lands."""
        row = self.db(user).execute(
            "SELECT at, kind, value, detail FROM events WHERE app = ? ORDER BY at DESC LIMIT 1", (app,),
        ).fetchone()
        if not row:
            return None
        try:
            detail = json.loads(row[3])
        except ValueError:
            detail = {}
        return {"at": row[0], "kind": row[1], "value": row[2], "detail": detail}

    def note_fired(self, user, rule, name, day, reasons, action):
        """True the first time a rule fires on a day."""
        db = self.db(user)
        with db:
            cur = db.execute(
                "INSERT OR IGNORE INTO fired(rule, day, name, at, why, action) VALUES(?,?,?,?,?,?)",
                (rule, day, name, int(time.time() * 1000), json.dumps(reasons, ensure_ascii=False), action),
            )
        return cur.rowcount > 0

    def dismiss(self, user, rule, day):
        db = self.db(user)
        with db:
            db.execute("UPDATE fired SET dismissed = 1 WHERE rule = ? AND day = ?", (rule, day))

    def dismissed(self, user, day):
        rows = self.db(user).execute("SELECT rule FROM fired WHERE day = ? AND dismissed = 1", (day,))
        return {r[0] for r in rows}

    def prune(self, user):
        cut = int((time.time() - KEEP_DAYS * 86400) * 1000)
        db = self.db(user)
        with db:
            db.execute("DELETE FROM events WHERE at < ?", (cut,))
            db.execute("DELETE FROM fired WHERE at < ?", (cut,))

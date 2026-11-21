"""The monitor's memory: one SQLite file for the machine, not per user.

Numbers are kept a minute at a time for eight days, then an hour at a time for 400. A row in an hour
holds the same columns as a row in a minute: `n` says how many minutes went into it, so "up 58 of 60"
stays true after rolling up.
"""
import os
import sqlite3
import threading
from datetime import datetime, timedelta

MINUTE = 60
HOUR = 3600
KEEP_MINUTES_S = 8 * 86400
KEEP_HOURS_S = 400 * 86400
KEEP_DAYS = 90
# name: (how far back, bucket on the chart, which rows to read)
RANGES = {
    "1h": (3600, 60, MINUTE),
    "6h": (6 * 3600, 300, MINUTE),
    "24h": (86400, 900, MINUTE),
    "7d": (7 * 86400, 3600, MINUTE),
    "30d": (30 * 86400, 6 * 3600, HOUR),
    "90d": (90 * 86400, 86400, HOUR),
}
SCHEMA = """
CREATE TABLE IF NOT EXISTS system(
    span INTEGER NOT NULL, at INTEGER NOT NULL, n INTEGER NOT NULL DEFAULT 1,
    load1 REAL, cpu REAL, speed REAL, mem_used INTEGER, swap_used INTEGER, cpu_temp REAL, batt_temp REAL,
    battery REAL, rssi REAL, disk_used INTEGER, net_in INTEGER, net_out INTEGER, net_relayed INTEGER,
    PRIMARY KEY(span, at)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS service(
    span INTEGER NOT NULL, at INTEGER NOT NULL, name TEXT NOT NULL, n INTEGER NOT NULL DEFAULT 1,
    up INTEGER NOT NULL DEFAULT 0, cpu REAL, rss INTEGER, read_b INTEGER, written_b INTEGER, disk_w INTEGER,
    PRIMARY KEY(span, at, name)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS traffic(
    span INTEGER NOT NULL, at INTEGER NOT NULL, name TEXT NOT NULL, reqs INTEGER NOT NULL DEFAULT 0,
    polls INTEGER NOT NULL DEFAULT 0, writes INTEGER NOT NULL DEFAULT 0, refused INTEGER NOT NULL DEFAULT 0,
    errors INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(span, at, name)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS incidents(
    id INTEGER PRIMARY KEY, kind TEXT NOT NULL, target TEXT NOT NULL, started INTEGER NOT NULL,
    ended INTEGER, detail TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS incidents_started ON incidents(started);
CREATE TABLE IF NOT EXISTS paths(
    day TEXT NOT NULL, name TEXT NOT NULL, path TEXT NOT NULL, status INTEGER NOT NULL,
    count INTEGER NOT NULL, PRIMARY KEY(day, name, path, status)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS clients(
    day TEXT NOT NULL, ip TEXT NOT NULL, reqs INTEGER NOT NULL, refused INTEGER NOT NULL,
    PRIMARY KEY(day, ip)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS logins(
    at INTEGER NOT NULL, name TEXT NOT NULL, ok INTEGER NOT NULL, who TEXT NOT NULL, ip TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS logins_at ON logins(at);
CREATE TABLE IF NOT EXISTS data_days(
    day TEXT NOT NULL, app TEXT NOT NULL, bytes INTEGER NOT NULL, records INTEGER NOT NULL,
    PRIMARY KEY(day, app)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS log_state(
    name TEXT PRIMARY KEY, inode INTEGER NOT NULL, position INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS log_counts(
    span INTEGER NOT NULL, at INTEGER NOT NULL, name TEXT NOT NULL, level TEXT NOT NULL, n INTEGER NOT NULL,
    PRIMARY KEY(span, at, name, level)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS log_groups(
    at INTEGER NOT NULL, name TEXT NOT NULL, level TEXT NOT NULL, sig TEXT NOT NULL, n INTEGER NOT NULL,
    last INTEGER NOT NULL, sample TEXT NOT NULL, PRIMARY KEY(at, name, level, sig)) WITHOUT ROWID;
"""
SYSTEM_GAUGES = ("load1", "cpu", "speed", "mem_used", "swap_used", "cpu_temp", "batt_temp", "battery", "rssi", "disk_used")
SYSTEM_SUMS = ("net_in", "net_out", "net_relayed")
SERVICE_SUMS = ("read_b", "written_b", "disk_w")
TRAFFIC_SUMS = ("reqs", "polls", "writes", "refused", "errors")
OWN_POLL = "%/api/server/now"
LOG_LEVELS = ("info", "warn", "error")  # this page asking; nobody wants it topping the list of what's busy


# Services renamed since rows were written under their old names: old -> new.
RENAMED = {"xrnwallet": "xrnwal"}
# Each table, and its column naming a service or an app.
NAMED = (
    ("service", "name"), ("traffic", "name"), ("incidents", "target"), ("paths", "name"), ("logins", "name"),
    ("data_days", "app"), ("log_state", "name"), ("log_counts", "name"), ("log_groups", "name"),
)


def local_day(stamp):
    return datetime.fromtimestamp(stamp).date().isoformat()


class Store:
    def __init__(self, data_dir):
        os.makedirs(data_dir, mode=0o700, exist_ok=True)
        self.lock = threading.Lock()
        self.db = sqlite3.connect(os.path.join(data_dir, "monitor.sqlite"), timeout=5, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(SCHEMA)
        self.path = os.path.join(data_dir, "monitor.sqlite")
        self.carry_over_renames()

    def carry_over_renames(self):
        """Rows kept under a service's old name take its new one, once, so its charts, traffic, logs
        and incidents go on as one history. Where the new name has a row for the same moment already,
        that one stays and the old one goes."""
        with self.lock, self.db:
            for old, new in RENAMED.items():
                done = f"renamed_{old}"
                if self.db.execute("SELECT 1 FROM meta WHERE key = ?", (done,)).fetchone():
                    continue
                for table, column in NAMED:
                    self.db.execute(f"UPDATE OR IGNORE {table} SET {column} = ? WHERE {column} = ?", (new, old))
                    self.db.execute(f"DELETE FROM {table} WHERE {column} = ?", (old,))
                self.db.execute("INSERT INTO meta(key, value) VALUES(?, 1)", (done,))

    def rows(self, sql, args=()):
        with self.lock:
            return self.db.execute(sql, args).fetchall()

    def write(self, sql, args=()):
        with self.lock, self.db:
            return self.db.execute(sql, args)

    def write_many(self, sql, rows):
        with self.lock, self.db:
            self.db.executemany(sql, rows)

    # ---- samples -------------------------------------------------------------------------------

    def add_minute(self, at, system, services):
        """One minute's sample: `system` by column, `services` by name."""
        cols = SYSTEM_GAUGES + SYSTEM_SUMS
        with self.lock, self.db:
            self.db.execute(
                f"INSERT OR REPLACE INTO system(span, at, n, {', '.join(cols)}) VALUES({MINUTE}, ?, 1, {', '.join('?' * len(cols))})",
                (at, *(system.get(c) for c in cols)),
            )
            self.db.executemany(
                f"INSERT OR REPLACE INTO service(span, at, name, n, up, cpu, rss, read_b, written_b, disk_w) "
                f"VALUES({MINUTE}, ?, ?, 1, ?, ?, ?, ?, ?, ?)",
                [(at, name, s.get("up", 0), s.get("cpu"), s.get("rss"), s.get("read_b"), s.get("written_b"), s.get("disk_w"))
                 for name, s in services.items()],
            )

    def add_traffic(self, rows):
        """Adds to per-minute counts: {(minute, name): {"reqs": ..}}. A log can carry old minutes
        the first time it's read, so the hour rows are added to here and not rolled up later: by
        then the minutes they came from may be gone."""
        hours = {}
        for (at, name), counts in rows.items():
            tally = hours.setdefault((at // HOUR * HOUR, name), dict.fromkeys(TRAFFIC_SUMS, 0))
            for key in TRAFFIC_SUMS:
                tally[key] += counts.get(key, 0)
        sql = (
            "INSERT INTO traffic(span, at, name, reqs, polls, writes, refused, errors) VALUES(?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(span, at, name) DO UPDATE SET reqs = reqs + excluded.reqs, polls = polls + excluded.polls, "
            "writes = writes + excluded.writes, refused = refused + excluded.refused, errors = errors + excluded.errors"
        )
        with self.lock, self.db:
            for span, group in ((MINUTE, rows), (HOUR, hours)):
                self.db.executemany(sql, [
                    (span, at, name, *(c.get(k, 0) for k in TRAFFIC_SUMS)) for (at, name), c in group.items()
                ])

    def add_log_counts(self, rows):
        """Adds to how many lines each service logged at each level: {(minute, name, level): n}. As
        with traffic, the hour rows are added to at once."""
        hours = {}
        for (at, name, level), n in rows.items():
            key = (at // HOUR * HOUR, name, level)
            hours[key] = hours.get(key, 0) + n
        sql = (
            "INSERT INTO log_counts(span, at, name, level, n) VALUES(?, ?, ?, ?, ?) "
            "ON CONFLICT(span, at, name, level) DO UPDATE SET n = n + excluded.n"
        )
        with self.lock, self.db:
            for span, group in ((MINUTE, rows), (HOUR, hours)):
                self.db.executemany(sql, [(span, *key, n) for key, n in group.items()])

    def add_log_groups(self, rows):
        """Warnings and errors by what they say: {(hour, name, level, signature): [n, last ms, latest line]}."""
        self.write_many(
            "INSERT INTO log_groups(at, name, level, sig, n, last, sample) VALUES(?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(at, name, level, sig) DO UPDATE SET n = n + excluded.n, "
            "sample = CASE WHEN excluded.last >= last THEN excluded.sample ELSE sample END, last = max(last, excluded.last)",
            [(*key, n, last, sample) for key, (n, last, sample) in rows.items()],
        )

    def add_paths(self, counts):
        self.write_many(
            "INSERT INTO paths(day, name, path, status, count) VALUES(?, ?, ?, ?, ?) "
            "ON CONFLICT(day, name, path, status) DO UPDATE SET count = count + excluded.count",
            [(*key, count) for key, count in counts.items()],
        )

    def add_clients(self, counts):
        self.write_many(
            "INSERT INTO clients(day, ip, reqs, refused) VALUES(?, ?, ?, ?) "
            "ON CONFLICT(day, ip) DO UPDATE SET reqs = reqs + excluded.reqs, refused = refused + excluded.refused",
            [(day, ip, c[0], c[1]) for (day, ip), c in counts.items()],
        )

    def add_logins(self, rows):
        self.write_many("INSERT INTO logins(at, name, ok, who, ip) VALUES(?, ?, ?, ?, ?)", rows)

    def set_data_day(self, day, sizes):
        self.write_many(
            "INSERT OR REPLACE INTO data_days(day, app, bytes, records) VALUES(?, ?, ?, ?)",
            [(day, app, s["bytes"], s["records"]) for app, s in sizes.items()],
        )

    # ---- keeping it small ------------------------------------------------------------------------

    def roll_up(self, now):
        """Folds finished hours of minutes into hour rows, then drops what's past keeping."""
        hour_end = now // HOUR * HOUR
        since = self.meta("rolled") or 0
        gauges = ", ".join(f"avg({c})" for c in SYSTEM_GAUGES)
        sums = ", ".join(f"sum({c})" for c in SYSTEM_SUMS)
        cols = ", ".join(SYSTEM_GAUGES + SYSTEM_SUMS)
        with self.lock, self.db:
            self.db.execute(
                f"INSERT OR REPLACE INTO system(span, at, n, {cols}) SELECT {HOUR}, at / {HOUR} * {HOUR}, sum(n), {gauges}, {sums} "
                f"FROM system WHERE span = {MINUTE} AND at >= ? AND at < ? GROUP BY at / {HOUR}", (since, hour_end))
            self.db.execute(
                f"INSERT OR REPLACE INTO service(span, at, name, n, up, cpu, rss, read_b, written_b, disk_w) "
                f"SELECT {HOUR}, at / {HOUR} * {HOUR}, name, sum(n), sum(up), avg(cpu), avg(rss), sum(read_b), sum(written_b), sum(disk_w) "
                f"FROM service WHERE span = {MINUTE} AND at >= ? AND at < ? GROUP BY at / {HOUR}, name", (since, hour_end))
            self.db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('rolled', ?)", (hour_end,))
            for table in ("system", "service", "traffic", "log_counts"):
                self.db.execute(f"DELETE FROM {table} WHERE span = {MINUTE} AND at < ?", (now - KEEP_MINUTES_S,))
                self.db.execute(f"DELETE FROM {table} WHERE span = {HOUR} AND at < ?", (now - KEEP_HOURS_S,))
            cut = (datetime.fromtimestamp(now) - timedelta(days=KEEP_DAYS)).date().isoformat()
            self.db.execute("DELETE FROM paths WHERE day < ?", (cut,))
            self.db.execute("DELETE FROM clients WHERE day < ?", (cut,))
            self.db.execute("DELETE FROM logins WHERE at < ?", ((now - KEEP_DAYS * 86400) * 1000,))
            self.db.execute("DELETE FROM log_groups WHERE at < ?", (now - KEEP_DAYS * 86400,))

    # ---- reading ---------------------------------------------------------------------------------

    def chart_rows(self, span, first):
        """Which rows a chart reads. Hour rows stop at the last roll-up; the rest are still minutes."""
        if span == MINUTE:
            return f"span = {MINUTE} AND at >= ?", (first,)
        return f"((span = {HOUR} AND at >= ?) OR (span = {MINUTE} AND at >= ?))", (first, max(first, self.meta("rolled") or 0))

    def history(self, range_name, now, only=None):
        """Series on one time axis, with None where nothing was recorded, so a chart shows the gap."""
        seconds, bucket, span = RANGES[range_name]
        first = (now - seconds) // bucket * bucket + bucket
        earliest = self.earliest()
        if earliest:  # a chart of what's been watched, not months of nothing leading up to it
            first = max(first, earliest // bucket * bucket)
        stamps = list(range(first, now // bucket * bucket + bucket, bucket))
        slots = {at: i for i, at in enumerate(stamps)}

        def blank():
            return [None] * len(stamps)

        system = {c: blank() for c in SYSTEM_GAUGES + SYSTEM_SUMS}
        select = ", ".join([f"avg({c})" for c in SYSTEM_GAUGES] + [f"sum({c})" for c in SYSTEM_SUMS])
        rows_wanted, rows_args = self.chart_rows(span, first)
        for row in self.rows(
            f"SELECT at / {bucket} * {bucket}, {select} FROM system WHERE {rows_wanted} GROUP BY 1", rows_args,
        ):
            if row[0] in slots:
                for col, value in zip(SYSTEM_GAUGES + SYSTEM_SUMS, row[1:]):
                    system[col][slots[row[0]]] = value
        services = {}
        where = " AND name = ?" if only else ""
        args = (span, first, only) if only else (span, first)
        for at, name, up, n, cpu, rss, disk_w in self.rows(
            f"SELECT at / {bucket} * {bucket}, name, sum(up), sum(n), avg(cpu), avg(rss), sum(disk_w) FROM service "
            f"WHERE {rows_wanted}{where} GROUP BY 1, 2", (*rows_args, only) if only else rows_args,
        ):
            if at in slots:
                series = services.setdefault(name, {"up": blank(), "cpu": blank(), "rss": blank(), "diskW": blank()})
                i = slots[at]
                series["up"][i], series["cpu"][i], series["rss"][i], series["diskW"][i] = (up / n if n else None), cpu, rss, disk_w
        traffic = {}
        for at, name, reqs, polls, writes, refused, errors in self.rows(
            f"SELECT at / {bucket} * {bucket}, name, sum(reqs), sum(polls), sum(writes), sum(refused), sum(errors) FROM traffic "
            f"WHERE span = ? AND at >= ?{where} GROUP BY 1, 2", args,
        ):
            if at in slots:
                series = traffic.setdefault(name, {"reqs": blank(), "polls": blank(), "writes": blank(), "refused": blank(), "errors": blank()})
                i = slots[at]
                series["reqs"][i], series["polls"][i], series["writes"][i] = reqs, polls, writes
                series["refused"][i], series["errors"][i] = refused, errors
        return {"range": range_name, "bucket": bucket, "at": stamps, "system": system, "services": services, "traffic": traffic}

    def log_history(self, range_name, now, only=None):
        """Lines logged at each level, in total and by service, on the same buckets as `history`,
        from when counting began. Routine requests aren't among them: Traffic counts those."""
        seconds, bucket, span = RANGES[range_name]
        first = (now - seconds) // bucket * bucket + bucket
        begun = self.rows("SELECT min(at) FROM log_counts WHERE span = ?", (span,))[0][0]
        if begun:
            first = max(first, begun // bucket * bucket)
        stamps = list(range(first, now // bucket * bucket + bucket, bucket)) if begun else []
        slots = {at: i for i, at in enumerate(stamps)}
        levels = {level: [0] * len(stamps) for level in LOG_LEVELS}
        services = {}
        where = " AND name = ?" if only else ""
        for at, name, level, n in self.rows(
            f"SELECT at / {bucket} * {bucket}, name, level, sum(n) FROM log_counts WHERE span = ? AND at >= ?{where} GROUP BY 1, 2, 3",
            (span, first, only) if only else (span, first),
        ):
            if at in slots and level in levels:
                levels[level][slots[at]] += n
                series = services.setdefault(name, {lvl: [0] * len(stamps) for lvl in LOG_LEVELS})
                series[level][slots[at]] += n
        return {"range": range_name, "bucket": bucket, "at": stamps, "levels": levels, "services": services}

    def log_groups(self, since, only=None, limit=40):
        """Warnings and errors since [since], one row per message, errors first, then the most often;
        each with its latest line."""
        where = " AND name = ?" if only else ""
        rows = self.rows(
            f"SELECT name, level, sig, sum(n), max(last), sample FROM log_groups WHERE at >= ?{where} "
            "GROUP BY name, level, sig ORDER BY level = 'error' DESC, sum(n) DESC, max(last) DESC LIMIT ?",
            (since // HOUR * HOUR, only, limit) if only else (since // HOUR * HOUR, limit),
        )
        return [{"service": n, "level": lv, "sig": sig, "count": c, "last": last, "text": text}
                for n, lv, sig, c, last, text in rows]

    def recent_points(self, first, last):
        """Minute rows as live points, so a chart opened after a quiet spell isn't empty."""
        rows = self.rows(
            f"SELECT at, load1, speed, cpu, mem_used, cpu_temp, batt_temp, net_in, net_out FROM system "
            f"WHERE span = {MINUTE} AND at >= ? AND at < ? ORDER BY at", (first, last),
        )
        return [{
            "at": at * 1000, "load": load, "speed": speed, "cpu": cpu, "memUsed": mem, "cpuTemp": hot, "battTemp": warm,
            "netIn": net_in / MINUTE if net_in is not None else None,
            "netOut": net_out / MINUTE if net_out is not None else None,
        } for at, load, speed, cpu, mem, hot, warm, net_in, net_out in rows]

    def uptime_days(self, now, days=30):
        """{name: [share of sampled minutes up, or None, for each of the last days]}, oldest first."""
        today = datetime.fromtimestamp(now).date()
        wanted = [today - timedelta(days=i) for i in range(days - 1, -1, -1)]
        slots = {d: i for i, d in enumerate(wanted)}
        start = int(datetime(wanted[0].year, wanted[0].month, wanted[0].day).timestamp())
        tallies = {}
        cut = max(start, now - KEEP_MINUTES_S)
        rows = self.rows(
            f"SELECT at / {HOUR} * {HOUR}, name, sum(up), sum(n) FROM service WHERE span = {MINUTE} AND at >= ? GROUP BY 1, 2", (cut,),
        )
        rows += self.rows(
            f"SELECT at, name, up, n FROM service WHERE span = {HOUR} AND at >= ? AND at < ?", (start, cut // HOUR * HOUR),
        )
        for at, name, up, n in rows:
            day = datetime.fromtimestamp(at).date()
            if day in slots:
                tally = tallies.setdefault(name, [[0, 0] for _ in wanted])[slots[day]]
                tally[0] += up
                tally[1] += n
        return {name: [round(u / n, 4) if n else None for u, n in days_of] for name, days_of in tallies.items()}

    def totals(self, seconds, now):
        """Requests by service over a stretch of time."""
        cut = now - seconds
        span = MINUTE if seconds <= KEEP_MINUTES_S else HOUR
        rows = self.rows(
            "SELECT name, sum(reqs), sum(polls), sum(writes), sum(refused), sum(errors) FROM traffic "
            "WHERE span = ? AND at >= ? GROUP BY name",
            (span, cut),
        )
        return {r[0]: dict(zip(TRAFFIC_SUMS, r[1:])) for r in rows}

    def daily_traffic(self, days, now):
        """{day: {name: {"reqs", "writes"}}}, for the Data page. Hour rows are complete at once."""
        rows = self.rows(
            f"SELECT at, name, reqs, writes FROM traffic WHERE span = {HOUR} AND at >= ?", (now - days * 86400,),
        )
        found = {}
        for at, name, reqs, writes in rows:
            tally = found.setdefault(local_day(at), {}).setdefault(name, {"reqs": 0, "writes": 0})
            tally["reqs"] += reqs
            tally["writes"] += writes
        return found

    def daily_network(self, days, now):
        """{day: [bytes in, bytes out]}. The latest hours are still in minute rows."""
        rolled = self.meta("rolled") or 0
        rows = self.rows(
            f"SELECT at, net_in, net_out FROM system WHERE (span = {HOUR} AND at >= ?) OR (span = {MINUTE} AND at >= ?)",
            (now - days * 86400, rolled),
        )
        found = {}
        for at, net_in, net_out in rows:
            tally = found.setdefault(local_day(at), [0, 0])
            tally[0] += net_in or 0
            tally[1] += net_out or 0
        return found

    def status_mix(self, since_day):
        return dict(self.rows("SELECT status, sum(count) FROM paths WHERE day >= ? GROUP BY status", (since_day,)))

    def top_paths(self, since_day, limit=8, refused=False):
        where = "status >= 400 AND status < 500" if refused else f"status < 400 AND path NOT LIKE '{OWN_POLL}'"
        rows = self.rows(
            f"SELECT name, path, sum(count), max(status) FROM paths WHERE day >= ? AND {where} GROUP BY 1, 2 ORDER BY 3 DESC LIMIT ?",
            (since_day, limit),
        )
        return [{"service": n, "path": p, "count": c, "status": s} for n, p, c, s in rows]

    def top_clients(self, since_day, limit=8, refused=False):
        """Who called most, or with `refused`, who got turned away most."""
        rank = "HAVING sum(refused) > 0 ORDER BY 3 DESC" if refused else "ORDER BY 2 DESC"
        rows = self.rows(
            f"SELECT ip, sum(reqs), sum(refused), count(*), max(day) FROM clients WHERE day >= ? GROUP BY ip {rank} LIMIT ?",
            (since_day, limit),
        )
        return [{"ip": ip, "reqs": r, "refused": x, "days": d, "last": last} for ip, r, x, d, last in rows]

    def login_rows(self, since_ms, limit=20):
        """Each who-from-where once, failures first: the laptop signs in by ssh all day, and one
        stranger's attempt shouldn't be buried under thousands of those."""
        rows = self.rows(
            "SELECT max(at), name, ok, who, ip, count(*) FROM logins WHERE at >= ? "
            "GROUP BY name, ok, who, ip ORDER BY ok, max(at) DESC LIMIT ?", (since_ms, limit),
        )
        return [{"at": a, "service": n, "ok": bool(o), "who": w, "ip": i, "count": c} for a, n, o, w, i, c in rows]

    def login_counts(self, since_ms):
        rows = self.rows("SELECT ok, count(*) FROM logins WHERE at >= ? GROUP BY ok", (since_ms,))
        counts = dict(rows)
        return {"ok": counts.get(1, 0), "refused": counts.get(0, 0)}

    def data_history(self, since_day):
        return self.rows("SELECT day, app, bytes, records FROM data_days WHERE day >= ? ORDER BY day", (since_day,))

    # ---- incidents -------------------------------------------------------------------------------

    def open_incident(self, kind, target, at, detail=""):
        cur = self.write("INSERT INTO incidents(kind, target, started, detail) VALUES(?, ?, ?, ?)", (kind, target, at, detail))
        return cur.lastrowid

    def close_incident(self, row, at):
        self.write("UPDATE incidents SET ended = ? WHERE id = ?", (at, row))

    def note_incident(self, kind, target, at, detail="", ended=None):
        """Something that happened at a moment, or over a stretch already finished."""
        self.write(
            "INSERT INTO incidents(kind, target, started, ended, detail) VALUES(?, ?, ?, ?, ?)",
            (kind, target, at, at if ended is None else ended, detail),
        )

    def open_incidents(self):
        rows = self.rows("SELECT id, kind, target FROM incidents WHERE ended IS NULL")
        return {(kind, target): row for row, kind, target in rows}

    def incidents(self, limit=60, open_only=False, since=None):
        """Newest first. With [since], only those still open or that ended after it."""
        where = "WHERE ended IS NULL " if open_only else ""
        args = (limit,)
        if since is not None:
            where = "WHERE (ended IS NULL OR ended >= ?) "
            args = (since, limit)
        rows = self.rows(f"SELECT kind, target, started, ended, detail FROM incidents {where}ORDER BY started DESC LIMIT ?", args)
        return [{"kind": k, "target": t, "started": s, "ended": e, "detail": d} for k, t, s, e, d in rows]

    # ---- where we'd got to -----------------------------------------------------------------------

    def log_position(self, name):
        rows = self.rows("SELECT inode, position FROM log_state WHERE name = ?", (name,))
        return rows[0] if rows else (0, 0)

    def set_log_positions(self, rows):
        self.write_many("INSERT OR REPLACE INTO log_state(name, inode, position) VALUES(?, ?, ?)", rows)

    def last_sample(self):
        rows = self.rows(f"SELECT max(at) FROM system WHERE span = {MINUTE}")
        return rows[0][0] if rows else None

    def earliest(self):
        """The oldest moment anything is known about. Logs go back further than the samples."""
        rows = self.rows("SELECT (SELECT min(at) FROM system), (SELECT min(at) FROM traffic)")
        known = [at for at in rows[0] if at]
        return min(known) if known else None

    def meta(self, key):
        rows = self.rows("SELECT value FROM meta WHERE key = ?", (key,))
        return rows[0][0] if rows else None

    def set_meta(self, key, value):
        self.write("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)", (key, value))

    def size(self):
        total = 0
        for suffix in ("", "-wal"):
            try:
                total += os.path.getsize(self.path + suffix)
            except OSError:
                pass
        return total

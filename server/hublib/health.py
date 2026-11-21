"""Health records: steps, distance, active energy, heart rate, sleep, weight, SpO2 and stress, kept in
hub.sqlite for good.

xrnfit posts them to /api/health from the phone: the watch's, which it now fetches from the watch
itself (source xrnfit-watch; before 2026-11-16 Gadgetbridge did, source gadgetbridge), the
scale's, and whatever else is in Health Connect. The history importer posts what the old apps held.
Unlike everything else the hub shows, there's nowhere else to read these from: this table is the
copy, so it's never pruned, and it lives in hub.sqlite, which the laptop's backup copies.

A record is a sender's whole reading under its own id. Sending it again replaces every row it had,
so a resend after a lost answer changes nothing, and a delete takes all of them. A heart-rate, SpO2
or stress record is a run of samples and keeps one row each.

The same day can come from two sources: the watch through xrnfit and through Gadgetbridge on the
day xrnfit took over, or the watch and an export that covers the weeks before. Adding them would count that day twice, so for each day and kind one
source counts, the first in PRIORITY that has something that day. Weighings don't add up, so every
source's are listed.
"""
import json
import math
import re
import sqlite3
import time
from datetime import datetime

SOURCE = re.compile(r"[a-z][a-z0-9.-]{0,59}")
RECORD_ID = re.compile(r"[A-Za-z0-9._:-]{1,100}")
STAGES = ("awake", "light", "deep", "rem", "asleep")
KINDS = ("steps", "heart_rate", "resting_heart_rate", "sleep", "weight", "spo2", "stress", "distance", "active_energy")
# Kinds sent as an amount over a stretch of time, which add up over a day, and their names in a day's
# summary.
AMOUNTS = {"steps": "steps", "distance": "distance", "active_energy": "activeEnergy"}
# Kinds sent as runs of [epoch ms, value] samples, one row each, and what a sample's value is.
SAMPLED = {"heart_rate": "bpm", "spo2": "percent", "stress": "level"}
PRIORITY = ("xrnfit-watch", "gadgetbridge", "xrnfit-scale", "import-mifitness", "import-renpho", "import-googlefit")
MAX_RECORDS = 1000
MAX_SAMPLES = 2000
MAX_STAGES = 200
DAY_MS = 86_400_000
EARLIEST_MS = 946_684_800_000  # 2000-01-01T00:00:00Z
MINUTE_MS = 60_000
LIMITS = {  # kind: the values a reading can plausibly have
    "steps": (0, 100_000),
    "heart_rate": (20, 250),
    "resting_heart_rate": (20, 200),
    "weight": (2, 300),
    "spo2": (50, 100),  # %: under 50 is a failed reading, not a person walking about
    "stress": (1, 100),  # the watch's score; its 0 means "not measured", and xrnfit leaves those out
    "distance": (0, 200_000),  # metres in one record, at most a day long
    "active_energy": (0, 10_000),  # kcal burnt moving, resting metabolism left out
}
MAX_IMPEDANCE = 5000
# A scale app's own body-composition figures for a weighing (Renpho's export has all of these), and
# the range each can have. They're kept as sent: an importer brings the old app's numbers along.
BODY = {
    "fat": (1, 75), "fatKg": (0, 300), "fatFreeKg": (0, 300), "muscleKg": (0, 300), "musclePercent": (0, 100),
    "skeletalPercent": (0, 100), "skeletalKg": (0, 300), "boneKg": (0, 20), "bonePercent": (0, 100),
    "proteinPercent": (0, 100), "waterPercent": (0, 100), "subcutaneousPercent": (0, 100),
    "visceral": (0, 60), "bmr": (300, 10_000), "metabolicAge": (5, 120), "bmi": (5, 100),
}
SCHEMA = """
CREATE TABLE IF NOT EXISTS health(
    source TEXT NOT NULL, rid TEXT NOT NULL, kind TEXT NOT NULL, start INTEGER NOT NULL, end INTEGER,
    day TEXT NOT NULL, value REAL NOT NULL, detail TEXT NOT NULL DEFAULT '{}', received INTEGER NOT NULL,
    PRIMARY KEY(source, rid, start));
CREATE INDEX IF NOT EXISTS health_day ON health(kind, day, source);
CREATE INDEX IF NOT EXISTS health_kind_start ON health(kind, start);
CREATE INDEX IF NOT EXISTS health_received ON health(received);
"""


class BadHealth(ValueError):
    pass


# ---- what may be stored ------------------------------------------------------------------------


def day_of(ms):
    """The day [ms] falls on, on this machine's clock, as 2026-11-15."""
    return datetime.fromtimestamp(ms / 1000).date().isoformat()


def is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def moment_ms(value, now_ms, what):
    """[value] as a time a reading can have: whole milliseconds since 2000, at most a day ahead."""
    if isinstance(value, bool) or not isinstance(value, int) or not EARLIEST_MS <= value <= now_ms + DAY_MS:
        raise BadHealth(f"bad {what}")
    return value


def reading(value, kind, what="value"):
    lo, hi = LIMITS[kind]
    if not is_number(value) or not lo <= value <= hi:
        raise BadHealth(f"bad {what} for {kind}")
    return float(value)


def span(raw, now_ms, kind):
    start = moment_ms(raw.get("start"), now_ms, "start")
    end = moment_ms(raw.get("end"), now_ms, "end")
    if end <= start:
        raise BadHealth(f"{kind} ends before it starts")
    if end - start > DAY_MS:
        raise BadHealth(f"{kind} longer than a day")
    return start, end


def stage_list(raw, start, end, now_ms):
    stages = raw.get("stages", [])
    if not isinstance(stages, list) or len(stages) > MAX_STAGES:
        raise BadHealth("bad stages")
    out = []
    for stage in stages:
        if not isinstance(stage, list) or len(stage) != 3 or stage[2] not in STAGES:
            raise BadHealth("bad stage")
        lo, hi = moment_ms(stage[0], now_ms, "stage start"), moment_ms(stage[1], now_ms, "stage end")
        if not start <= lo < hi <= end:
            raise BadHealth("a stage outside its sleep")
        out.append([lo, hi, stage[2]])
    return out


def asleep_ms(start, end, stages):
    """Time asleep: every stage but awake, or the whole session when it has no stages."""
    if not stages:
        return end - start
    return sum(hi - lo for lo, hi, name in stages if name != "awake")


def clean_record(raw, now_ms):
    """A record as rows: (rid, kind, start, end, day, value, detail), one per sample for the SAMPLED
    kinds. Raises BadHealth."""
    if not isinstance(raw, dict):
        raise BadHealth("a record must be an object")
    rid, kind = raw.get("id"), raw.get("kind")
    if not isinstance(rid, str) or not RECORD_ID.fullmatch(rid):
        raise BadHealth("bad id")
    if kind not in KINDS:
        raise BadHealth("bad kind")
    if kind in AMOUNTS:
        start, end = span(raw, now_ms, kind)
        return rid, [(rid, kind, start, end, day_of(start), reading(raw.get("value"), kind), "{}")]
    if kind in SAMPLED:
        samples = raw.get("samples")
        if not isinstance(samples, list) or not 1 <= len(samples) <= MAX_SAMPLES:
            raise BadHealth("bad samples")
        rows = {}
        for sample in samples:
            if not isinstance(sample, list) or len(sample) != 2:
                raise BadHealth("bad sample")
            at = moment_ms(sample[0], now_ms, "sample time")
            rows[at] = (rid, kind, at, None, day_of(at), reading(sample[1], kind, SAMPLED[kind]), "{}")
        return rid, list(rows.values())
    if kind == "sleep":
        start, end = span(raw, now_ms, kind)
        stages = stage_list(raw, start, end, now_ms)
        detail = json.dumps({"stages": stages}, separators=(",", ":")) if stages else "{}"
        minutes = asleep_ms(start, end, stages) / MINUTE_MS
        return rid, [(rid, kind, start, end, day_of(end), minutes, detail)]
    start = moment_ms(raw.get("start"), now_ms, "start")
    value = reading(raw.get("value"), kind)
    extra = {}
    if kind == "weight" and raw.get("impedance") is not None:
        impedance = raw["impedance"]
        if not is_number(impedance) or not 0 <= impedance <= MAX_IMPEDANCE:
            raise BadHealth("bad impedance")
        extra["impedance"] = impedance
    if kind == "weight" and raw.get("body") is not None:
        extra["body"] = clean_body(raw["body"])
    detail = json.dumps(extra, separators=(",", ":"), allow_nan=False) if extra else "{}"
    return rid, [(rid, kind, start, None, day_of(start), value, detail)]


def clean_body(raw):
    """A weighing's body-composition figures as they may be kept: known names, plausible values."""
    if not isinstance(raw, dict) or not raw:
        raise BadHealth("bad body")
    for name, value in raw.items():
        if name not in BODY:
            raise BadHealth(f"bad body figure {name}"[:60])
        lo, hi = BODY[name]
        if not is_number(value) or not lo <= value <= hi:
            raise BadHealth(f"bad body {name}")
    return dict(raw)


def clean_batch(doc, now_ms):
    """(source, [(rid, rows)], [rid]) from a POST body. Raises BadHealth: one bad record refuses the
    whole batch, so a sender never has to work out which half landed."""
    if not isinstance(doc, dict):
        raise BadHealth('body must be {"source", "upsert": [...], "delete": [...]}')
    source = doc.get("source")
    if not isinstance(source, str) or not SOURCE.fullmatch(source):
        raise BadHealth("bad source")
    upsert, delete = doc.get("upsert", []), doc.get("delete", [])
    if upsert is None:
        upsert = []
    if delete is None:
        delete = []
    if not isinstance(upsert, list) or not isinstance(delete, list):
        raise BadHealth("upsert and delete must be lists")
    if not upsert and not delete:
        raise BadHealth("nothing to upsert or delete")
    if len(upsert) > MAX_RECORDS or len(delete) > MAX_RECORDS:
        raise BadHealth(f"at most {MAX_RECORDS} records at once")
    records = [clean_record(r, now_ms) for r in upsert]
    for rid in delete:
        if not isinstance(rid, str) or not RECORD_ID.fullmatch(rid):
            raise BadHealth("bad id to delete")
    return source, records, list(dict.fromkeys(delete))


def write(log, user, doc, now_ms=None):
    """Applies a POST body in one transaction: the upserts, then the deletes, so an id in both ends
    up gone. Returns (records upserted, records deleted that were here). Raises BadHealth."""
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    source, records, delete = clean_batch(doc, now_ms)
    db = log.db(user)
    deleted = 0
    with db:
        for rid, rows in records:
            db.execute("DELETE FROM health WHERE source = ? AND rid = ?", (source, rid))
            db.executemany(
                "INSERT OR REPLACE INTO health(source, rid, kind, start, end, day, value, detail, received)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                [(source, *row, now_ms) for row in rows],
            )
        for rid in delete:
            deleted += db.execute("DELETE FROM health WHERE source = ? AND rid = ?", (source, rid)).rowcount > 0
    return len(records), deleted


# ---- reading it back ---------------------------------------------------------------------------


def rank(source):
    """Sort key: the sources in PRIORITY first, in its order, then the rest alphabetically."""
    return (PRIORITY.index(source), "") if source in PRIORITY else (len(PRIORITY), source)


def chosen(db, kind, lo_day, hi_day):
    """{day: the source that counts that day} for [kind]."""
    best = {}
    rows = db.execute(
        "SELECT DISTINCT day, source FROM health WHERE kind = ? AND day BETWEEN ? AND ?", (kind, lo_day, hi_day),
    )
    for day, source in rows:
        if day not in best or rank(source) < rank(best[day]):
            best[day] = source
    return best


def whole(value):
    """A number as an int when it is one, so 8123.0 steps read as 8123."""
    return int(value) if float(value).is_integer() else value


def stages_of(detail):
    try:
        found = json.loads(detail).get("stages", [])
    except (ValueError, AttributeError):
        return []
    return [s for s in found if isinstance(s, list) and len(s) == 3] if isinstance(found, list) else []


def sleep_sessions(db, lo_day, hi_day):
    """The sessions that count, each as (day, source, start, end, stages), oldest first."""
    best = chosen(db, "sleep", lo_day, hi_day)
    rows = db.execute(
        "SELECT day, source, start, end, detail FROM health WHERE kind = 'sleep' AND day BETWEEN ? AND ?"
        " ORDER BY start", (lo_day, hi_day),
    )
    return [(day, source, start, end, stages_of(detail)) for day, source, start, end, detail in rows
            if best.get(day) == source]


def stage_minutes(start, end, stages):
    """Minutes in each stage, and asleep (everything but awake) as `minutes`."""
    spent = dict.fromkeys(STAGES, 0)
    if stages:
        for lo, hi, name in stages:
            spent[name] += hi - lo
    else:
        spent["asleep"] = end - start
    out = {"minutes": round(sum(v for k, v in spent.items() if k != "awake") / MINUTE_MS)}
    out.update({k: round(v / MINUTE_MS) for k, v in spent.items()})
    return out


def blank_day():
    """A day with nothing in it, as days() and the API write one."""
    return {"steps": None, "sleep": None, "heartRate": None, "weightKg": None, "spo2": None, "stress": None,
            "distance": None, "activeEnergy": None}


def spread(db, kind, lo_day, hi_day):
    """{day: (min, mean, max)} of a SAMPLED kind, from the source that counts each day."""
    best = chosen(db, kind, lo_day, hi_day)
    rows = db.execute(
        "SELECT day, source, MIN(value), AVG(value), MAX(value) FROM health"
        " WHERE kind = ? AND day BETWEEN ? AND ? GROUP BY day, source", (kind, lo_day, hi_day),
    )
    return {day: (low, mean, high) for day, source, low, mean, high in rows if best.get(day) == source}


def days(db, lo_day, hi_day):
    """{day: {"steps", "sleep", "heartRate", "weightKg", "spo2", "stress"}} for the days that have
    anything; times in the sleep stay epoch ms for the caller to write out."""
    out = {}

    def at(day):
        return out.setdefault(day, blank_day())

    for kind, key in AMOUNTS.items():
        best = chosen(db, kind, lo_day, hi_day)
        for day, source, total in db.execute(
            "SELECT day, source, SUM(value) FROM health WHERE kind = ? AND day BETWEEN ? AND ? GROUP BY day, source",
            (kind, lo_day, hi_day),
        ):
            if best.get(day) == source:
                at(day)[key] = round(total)
    for day, (low, mean, high) in spread(db, "heart_rate", lo_day, hi_day).items():
        at(day)["heartRate"] = {"min": whole(low), "avg": round(mean), "max": whole(high), "resting": None}
    best = chosen(db, "resting_heart_rate", lo_day, hi_day)
    for day, source, value in db.execute(
        "SELECT day, source, value FROM health WHERE kind = 'resting_heart_rate' AND day BETWEEN ? AND ?"
        " ORDER BY start", (lo_day, hi_day),
    ):
        if best.get(day) == source:
            heart = at(day)["heartRate"]
            if heart is None:
                heart = at(day)["heartRate"] = {"min": None, "avg": None, "max": None, "resting": None}
            heart["resting"] = whole(value)  # the day's last, as rows come oldest first
    for kind in ("spo2", "stress"):
        for day, (low, mean, high) in spread(db, kind, lo_day, hi_day).items():
            at(day)[kind] = {"min": whole(low), "avg": round(mean), "max": whole(high)}
    for day, _, start, end, stages in sleep_sessions(db, lo_day, hi_day):
        night = stage_minutes(start, end, stages)
        sleep = at(day)["sleep"]
        if sleep is None:
            at(day)["sleep"] = {**night, "start": start, "end": end}
        else:  # a nap and the night, from the same source: the day's sleep is both
            for key, value in night.items():
                sleep[key] += value
            sleep["start"], sleep["end"] = min(sleep["start"], start), max(sleep["end"], end)
    for day, value in db.execute(
        "SELECT day, value FROM health WHERE kind = 'weight' AND day BETWEEN ? AND ? ORDER BY start", (lo_day, hi_day),
    ):
        at(day)["weightKg"] = whole(value)
    return out


def samples(db, kind, lo_day, hi_day):
    """[(ms, value)] of a SAMPLED kind from the source that counts each day, oldest first."""
    best = chosen(db, kind, lo_day, hi_day)
    rows = db.execute(
        "SELECT day, source, start, value FROM health WHERE kind = ? AND day BETWEEN ? AND ? ORDER BY start",
        (kind, lo_day, hi_day),
    )
    return [(start, whole(value)) for day, source, start, value in rows if best.get(day) == source]


def heart_rate(db, lo_day, hi_day):
    """[(ms, bpm)] from the source that counts each day, oldest first."""
    return samples(db, "heart_rate", lo_day, hi_day)


def midnight_ms(day):
    """Local midnight at the start of [day] (2026-11-15), in epoch ms."""
    return int(datetime.fromisoformat(day).timestamp() * 1000)


def step_buckets(db, lo_day, hi_day, every_minutes):
    """[(bucket start ms, steps)]: the steps of the source that counts each day, summed in buckets of
    [every_minutes] counted from each day's local midnight, oldest first, empty buckets left out.
    A record longer than a minute (an old export's hour) is shared out over the buckets it covers by
    how much of it falls in each, so an hour's steps don't all land in its first minute. Each day's
    buckets add up to its total in days()."""
    every = every_minutes * MINUTE_MS
    best = chosen(db, "steps", lo_day, hi_day)
    sums = {}
    rows = db.execute(
        "SELECT day, source, start, end, value FROM health WHERE kind = 'steps' AND day BETWEEN ? AND ?",
        (lo_day, hi_day),
    )
    for day, source, start, end, value in rows:
        if best.get(day) != source:
            continue
        zero = midnight_ms(day)
        end = end if end and end > start else start + 1
        lo = zero + (start - zero) // every * every
        while lo < end:
            hi = lo + every
            part = min(end, hi) - max(start, lo)
            if part > 0:
                sums[lo] = sums.get(lo, 0) + value * part / (end - start)
            lo = hi
    # Whole steps that still add up to the day's total: each bucket gets the rounded running sum's
    # step, so two half hours of 2160.5 come out 2160 and 2161, not 2160 twice.
    out, day, run = [], None, 0.0
    for at, total in sorted(sums.items()):
        if day_of(at) != day:
            day, run = day_of(at), 0.0
        whole_steps = round(run + total) - round(run)
        run += total
        if whole_steps > 0:
            out.append((at, whole_steps))
    return out


def impedance_of(detail):
    try:
        value = json.loads(detail).get("impedance")
    except (ValueError, AttributeError):
        return None
    return whole(value) if is_number(value) else None


def body_of(detail):
    """The scale app's own figures kept with a weighing, or None."""
    try:
        body = json.loads(detail).get("body")
    except (ValueError, AttributeError):
        return None
    return body if isinstance(body, dict) and body else None


def weighings(db, lo_day, hi_day):
    """[(ms, kg, impedance or None, source, body or None)], every source's, oldest first."""
    rows = db.execute(
        "SELECT start, value, detail, source FROM health WHERE kind = 'weight' AND day BETWEEN ? AND ? ORDER BY start",
        (lo_day, hi_day),
    )
    return [(start, whole(value), impedance_of(detail), source, body_of(detail)) for start, value, detail, source in rows]


def latest(db, kind):
    """(ms, value, detail) of the newest reading of [kind], or None."""
    return db.execute(
        "SELECT start, value, detail FROM health WHERE kind = ? ORDER BY start DESC LIMIT 1", (kind,),
    ).fetchone()


def latest_resting(db):
    """The newest day's resting heart rate, from the source that counts that day."""
    found = resting_day(db)
    return found[1] if found else None


def resting_day(db):
    """(day, bpm) of the newest day with a resting heart rate, from the source that counts that
    day, or None."""
    row = db.execute("SELECT MAX(day) FROM health WHERE kind = 'resting_heart_rate'").fetchone()
    if not row or row[0] is None:
        return None
    best = chosen(db, "resting_heart_rate", row[0], row[0])[row[0]]
    value = db.execute(
        "SELECT value FROM health WHERE kind = 'resting_heart_rate' AND day = ? AND source = ? ORDER BY start DESC LIMIT 1",
        (row[0], best),
    ).fetchone()
    return row[0], whole(value[0])


WEEK_BACK = (4, 14)  # days before the newest weighing that can stand for "a week ago"


def weight_trend(db):
    """The newest weighing as {"at", "kg"}, with "change" against the weighing nearest a week before
    it, and "since" (that one's time); the two are None when nothing was weighed 4 to 14 days before.
    Every source's weighings count, as everywhere for weight. None when nothing was ever weighed."""
    last = latest(db, "weight")
    if not last:
        return None
    at, kg = last[0], whole(last[1])
    lo, hi = WEEK_BACK
    rows = db.execute(
        "SELECT start, value FROM health WHERE kind = 'weight' AND start BETWEEN ? AND ?",
        (at - hi * DAY_MS, at - lo * DAY_MS),
    ).fetchall()
    before = min(rows, key=lambda r: abs(at - 7 * DAY_MS - r[0]), default=None)
    return {
        "at": at, "kg": kg,
        "change": whole(round(last[1] - before[1], 1)) if before else None,
        "since": before[0] if before else None,
    }


def held(db):
    """How much is kept: rows of each kind (a run of samples counts each one) and the first and last
    day."""
    counts = dict(db.execute("SELECT kind, COUNT(*) FROM health GROUP BY kind").fetchall())
    first, last = db.execute("SELECT MIN(day), MAX(day) FROM health").fetchone()
    return {
        "steps": counts.get("steps", 0), "heartRate": counts.get("heart_rate", 0), "sleep": counts.get("sleep", 0),
        "weight": counts.get("weight", 0), "spo2": counts.get("spo2", 0), "stress": counts.get("stress", 0),
        "distance": counts.get("distance", 0), "activeEnergy": counts.get("active_energy", 0),
        "first": first, "last": last,
    }


def stored_bytes(db):
    """What the health table and its indexes take in the database file, or None where SQLite was
    built without its dbstat table."""
    try:
        row = db.execute(
            "SELECT SUM(pgsize) FROM dbstat WHERE name IN (SELECT name FROM sqlite_master WHERE tbl_name = 'health')"
        ).fetchone()
    except sqlite3.OperationalError:  # no such table: dbstat
        return None
    return row[0] or 0


def received_by_day(db, since_ms):
    """{received ms: rows} for the batches written since [since_ms]: one write is one moment."""
    return dict(db.execute(
        "SELECT received, COUNT(*) FROM health WHERE received >= ? GROUP BY received", (since_ms,),
    ).fetchall())


def last_received(db):
    """When the newest record arrived, in ms, or None when none ever has. Asked on its own, MAX
    reads one entry of health_received; beside COUNT(*) it read the whole table."""
    return db.execute("SELECT MAX(received) FROM health").fetchone()[0]


def size(db):
    """(rows, when the newest arrived in ms or None), for the API's index. The count reads every
    row of an index: Today and the Overview ask last_received instead."""
    return db.execute("SELECT COUNT(*) FROM health").fetchone()[0], last_received(db)

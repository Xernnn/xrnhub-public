"""Read-only views of the other xrn services' data, from their files on this machine.

The hub runs next to them, so it reads what they wrote instead of asking for it over HTTP with a
stored password. Their writes swap files in by rename, so a read sees one whole version; the habits
database opens read-only, which SQLite allows beside a live writer. Nothing here opens a file for
writing. A service that isn't set up, or a file that won't parse, reads as empty: the hub shows
less, it never falls over.

What leaves this module is what the pages need and no more. In particular xrnrule's settings hold
the sentence that buys extra minutes, and they stay here.
"""
import json
import os
import sqlite3
import threading
import time
from datetime import date, timedelta
from urllib.parse import quote

from . import ical
from .machine import home_folder

MAX_ICS = 1024 * 1024  # one event; anything bigger isn't a calendar entry
CAL_REFRESH_S = 300
CAL_MEMO_SIZE = 16  # the same few ranges are asked for over and over between sweeps
RACY_S = 2  # a file read this soon after it changed may change again under the same mtime


def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


class Sources:
    def __init__(self, home, user):
        self.home = home
        self.user = user
        self._json_cache = {}
        self.calendar = CalendarIndex(os.path.join(home, "radicale", "collections", "collection-root", user))
        self.entry_times = {}
        self.entry_notes = {}
        self._habits_hit = None  # (the files' stamps, when read, (habits, entries, start hour), times, notes)
        self._rule_lock = threading.Lock()
        self._rule_log = None  # (device, inode, bytes read, the last line read, the events in them)

    def path(self, service, *parts):
        return os.path.join(home_folder(self.home, f"{service}-sync"), "data", "users", self.user, *parts)

    def cached_json(self, path):
        """A parsed file, read again only when it changes; xrnrule's is big and asked for often."""
        try:
            stamp = os.stat(path).st_mtime_ns
        except OSError:
            return None
        hit = self._json_cache.get(path)
        if hit and hit[0] == stamp:
            return hit[1]
        doc = read_json(path)
        self._json_cache[path] = (stamp, doc)
        return doc

    # ---- xrnloops ----------------------------------------------------------------------------

    def habits(self):
        """(habits, {habit uuid: {date string: value}}, the hour a habit day starts). Read again
        only when the database or its write-ahead log changes: Today, Review and the nudges each
        ask, every minute, and every entry ever made is in it. Callers only read what comes back."""
        path = self.path("xrnloops", "loop.sqlite")
        stamp = file_stamp(path), file_stamp(path + "-wal")
        if stamp[0] is None:
            return [], {}, 3
        hit = self._habits_hit
        if hit and hit[0] == stamp and hit[1] - max(s[0] for s in stamp if s) / 1e9 > RACY_S:
            self.entry_times, self.entry_notes = hit[3], hit[4]
            return hit[2]
        read_at = time.time()
        try:
            db = sqlite3.connect(f"file:{quote(path)}?mode=ro", uri=True, timeout=2)
        except sqlite3.Error:
            return [], {}, 3
        try:
            meta = dict(db.execute("SELECT key, value FROM meta"))
            habits = []
            for uuid, data in db.execute("SELECT uuid, data FROM habits WHERE deleted = 0"):
                try:
                    h = json.loads(data)
                except ValueError:
                    continue
                if not isinstance(h, dict):
                    continue
                habits.append({
                    "id": uuid,
                    "name": str(h.get("name") or ""),
                    "question": str(h.get("question") or ""),
                    "description": str(h.get("description") or ""),
                    "archived": bool(h.get("archived")),
                    "position": h.get("position") or 0,
                    "type": h.get("type") or 0,
                    "targetType": h.get("targetType") or 0,
                    "targetValue": h.get("targetValue") or 0,
                    "unit": str(h.get("unit") or ""),
                    "freqNum": h.get("freqNum") or 1,
                    "freqDen": h.get("freqDen") or 1,
                    "color": h.get("color"),
                })
            habits.sort(key=lambda h: h["position"])
            entries = {}
            times = {}
            notes = {}
            for habit, day, value, text, mtime in db.execute("SELECT habit, date, value, notes, mtime FROM entries"):
                entries.setdefault(habit, {})[day] = value
                times[(habit, day)] = mtime
                if text:
                    notes[(habit, day)] = text
            # A tick from the page has to carry these back: a newer time to win, the note to keep it.
            self.entry_times = times
            self.entry_notes = notes
            found = habits, entries, meta.get("day_start_hour", 3)
            self._habits_hit = (stamp, read_at, found, times, notes)
            return found
        except sqlite3.Error:
            return [], {}, 3
        finally:
            db.close()

    # ---- xrnrule -----------------------------------------------------------------------------

    def rule_state(self):
        doc = self.cached_json(self.path("xrnrule", "state.json"))
        data = (doc or {}).get("data") if isinstance(doc, dict) else None
        return data if isinstance(data, dict) else {}

    def rule_events(self, since_ms=0):
        """The devices' log: blocked opens, habits done, unlocks. Newest last. xrnrule only appends
        to the file, or writes a new one when it drops old events, so each line is parsed once: a
        later read takes the lines added since, unless the file was replaced or no longer holds
        the last line read where it was."""
        path = self.path("xrnrule", "events.jsonl")
        with self._rule_lock:
            try:
                with open(path, "rb") as f:
                    st = os.fstat(f.fileno())
                    seen = self._rule_log
                    if not (seen and seen[:2] == (st.st_dev, st.st_ino) and st.st_size >= seen[2]
                            and still_there(f, seen[2], seen[3])):
                        seen = (st.st_dev, st.st_ino, 0, b"", [])
                    f.seek(seen[2])
                    data = f.read()
            except OSError:
                self._rule_log = None
                return []
            end = data.rfind(b"\n") + 1  # a line still being written waits for its newline
            lines = data[:end].splitlines(keepends=True)
            found = seen[4]
            for line in lines:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if isinstance(e, dict) and isinstance(e.get("at"), (int, float)):
                    found.append(e)
            self._rule_log = (*seen[:2], seen[2] + end, lines[-1] if lines else seen[3], found)
            return [e for e in found if e["at"] >= since_ms]

    # ---- xrnwal ---------------------------------------------------------------------------

    def wallet(self):
        doc = self.cached_json(self.path("xrnwal", "state.json"))
        data = (doc or {}).get("data") if isinstance(doc, dict) else None
        return data if isinstance(data, dict) else {}

    # ---- xrntube ------------------------------------------------------------------------------

    def channel_count(self):
        doc = self.cached_json(self.path("xrntube", "state.json"))
        data = (doc or {}).get("data", doc) if isinstance(doc, dict) else None
        channels = data.get("channels") if isinstance(data, dict) else None
        return len(channels) if isinstance(channels, (list, dict)) else 0

    # ---- xrnhome -----------------------------------------------------------------------------

    def inventory(self):
        doc = self.cached_json(self.path("xrnhome", "state.json"))
        data = (doc or {}).get("data") if isinstance(doc, dict) else None
        items = data.get("items") if isinstance(data, dict) else None
        return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


def file_stamp(path):
    """(mtime ns, size, inode) of a file, or None when it isn't there."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size, st.st_ino


def still_there(f, offset, line):
    """Whether [line] still ends at [offset] in the open file [f]."""
    if offset < len(line):
        return False
    f.seek(offset - len(line))
    return f.read(len(line)) == line


def insta_songs(home):
    """(songs, holds) from xrninsta, the song-of-the-day bot, oldest day first. It's one person's list
    and isn't kept per user, so the API shows it to the server's owner only."""
    path = os.path.join(home, "xrninsta", "data", "xrninsta.sqlite")
    if not os.path.exists(path):
        return [], []
    try:
        db = sqlite3.connect(f"file:{quote(path)}?mode=ro", uri=True, timeout=2)
    except sqlite3.Error:
        return [], []
    try:
        db.row_factory = sqlite3.Row
        songs = [dict(r) for r in db.execute("SELECT id, title, artists, day, genre, added FROM songs ORDER BY day, id")]
        holds = [dict(r) for r in db.execute("SELECT id, title, artists, note, added FROM holds ORDER BY id")]
        return songs, holds
    except sqlite3.Error:
        return [], []
    finally:
        db.close()


class CalendarIndex:
    """Every event, parsed once and read again only when its file changes.

    A sweep stats some 8,000 files, so it runs in the background, every CAL_REFRESH_S (refresh), and
    a request only ever reads the last finished list. A sweep that finds nothing changed keeps the
    list and what between() worked out from it. `ready` stays False until the first sweep ends."""

    def __init__(self, root):
        self.root = root
        self.lock = threading.Lock()
        self.parsed = {}  # path -> (mtime, events)
        self.events = []
        self.calendars = {}  # folder -> {"name", "color"}
        self.memo = {}  # (first, last) -> what between() found; callers only read it
        self.ready = False
        self.swept_at = 0
        self.checked = 0  # time.monotonic() of the last sweep

    def refresh(self):
        """A sweep, when the last is CAL_REFRESH_S old or none has finished yet."""
        if self.ready and time.monotonic() - self.checked < CAL_REFRESH_S:
            return False
        self.sweep()
        return True

    def sweep(self):
        parsed = {}
        calendars = {}
        events = []
        changed = False
        try:
            folders = [e for e in os.scandir(self.root) if e.is_dir() and not e.name.startswith(".")]
        except OSError:
            folders = []
        for folder in folders:
            props = read_json(os.path.join(folder.path, ".Radicale.props")) or {}
            calendars[folder.name] = {
                "name": str(props.get("D:displayname") or folder.name),
                "color": normal_color(props.get("ICAL:calendar-color")),
            }
            try:
                files = [e for e in os.scandir(folder.path) if e.name.endswith(".ics") and e.is_file()]
            except OSError:
                continue
            for entry in files:
                try:
                    st = entry.stat()
                except OSError:
                    continue
                hit = self.parsed.get(entry.path)
                if hit and hit[0] == st.st_mtime_ns:
                    parsed[entry.path] = hit
                else:
                    parsed[entry.path] = (st.st_mtime_ns, read_ics(entry.path, st.st_size, folder.name))
                    changed = True
                events += parsed[entry.path][1]
        # Every file found was one parsed before, unchanged: only a file gone or a calendar renamed
        # or recoloured is left to tell.
        changed = changed or len(parsed) != len(self.parsed) or calendars != self.calendars
        with self.lock:
            if changed or not self.ready:
                self.parsed, self.events, self.calendars = parsed, events, calendars
                self.memo = {}
            self.ready = True
            self.swept_at = time.time()
            self.checked = time.monotonic()

    def between(self, first, last):
        """Occurrences touching days first..last, soonest first, as plain dicts."""
        with self.lock:
            hit = self.memo.get((first, last))
            events, calendars = self.events, self.calendars
        if hit is not None:
            return hit
        found = []
        for ev in events:
            for lo, hi in ical.occurrences(ev, first, last):
                cal = calendars.get(ev["calendar"], {})
                found.append({
                    "start": lo,
                    "end": hi,
                    "allDay": ev["all_day"],
                    "title": ev["title"],
                    "calendar": cal.get("name", ev["calendar"]),
                    "color": cal.get("color"),
                    "location": ev.get("location", ""),
                    "description": ev.get("description", ""),
                })
        found.sort(key=lambda o: (o["start"], not o["allDay"], o["title"]))
        with self.lock:
            if len(self.memo) >= CAL_MEMO_SIZE:
                self.memo = {}
            self.memo[(first, last)] = found
        return found


def read_ics(path, size, calendar_id):
    if size > MAX_ICS:
        return []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            events = ical.parse_ics(f.read())
    except OSError:
        return []
    for ev in events:
        ev["calendar"] = calendar_id
    return events


def normal_color(value):
    """#rrggbb from Radicale's #rrggbbaa; anything else isn't passed on to a page."""
    if isinstance(value, str) and len(value) in (7, 9) and value.startswith("#"):
        hexa = value[1:]
        if all(c in "0123456789abcdefABCDEF" for c in hexa):
            return value[:7]
    return None


def days_between(first, last):
    span = (last - first).days
    return [first + timedelta(days=i) for i in range(span + 1)]


def parse_day(text):
    try:
        return date.fromisoformat(str(text)[:10])
    except ValueError:
        return None

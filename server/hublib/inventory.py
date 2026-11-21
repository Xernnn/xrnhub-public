"""How much the phone holds for each app, and how much came in each day.

Records come with their own dates, so "what came in" is true from the first sweep with no history
to wait for: when a calendar file was last changed, when a habit was logged, when a device reported.
Sizes are what `du` would say, since disk is what runs out.

xrnfit has no server of its own: its health records live in the hub's hub.sqlite. They're counted
as xrnfit's all the same, a row a reading (each heart-rate, SpO2 or stress sample is one, as the
API's `held` counts them), "came in" on the day they were written, and the bytes they take moved
from the hub's share to xrnfit's.
"""
import glob
import os
import re
import sqlite3
from collections import Counter
from datetime import datetime, timedelta

from . import health
from .machine import BY_NAME, SERVICES, home_folder
from .sources import insta_songs

FED_DAYS = 30
RULE_STAMP = re.compile(rb'"at":\s*(\d{10,})')
CALENDAR_CACHE = ".Radicale.cache"  # rebuilt by Radicale whenever it likes: not your data
UNITS = {
    "calendar": "events",
    "xrnloops": "check-ins",
    "xrnrule": "reports",
    "xrntube": "channels",
    "xrnhome": "items",
    "xrnwal": "bills",
    "xrninsta": "songs",
    "xrnimg": "items",
    "xrnnote": "files",
    "xrnhub": "events",
    "xrnfit": "readings",
}


# Apps whose data the hub must not open, counted by their files instead: every note, task, folder and
# attachment xrnnote holds is ciphertext, so its files' number, sizes and times are all there is.
BY_FILES = {"xrnnote"}


def photo_items(home, user):
    """When each of a user's xrnimg items arrived (ms), trash left out, read from its database
    read-only; nothing when there is none or it can't be read."""
    path = os.path.join(home_folder(home, "xrnimg-sync"), "data", "users", user, "photos.sqlite")
    if not os.path.isfile(path):
        return []
    try:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
        try:
            return [r[0] for r in db.execute("SELECT created FROM items WHERE trashed_at IS NULL")]
        finally:
            db.close()
    except sqlite3.Error:
        return []


def on_disk(stat):
    blocks = getattr(stat, "st_blocks", 0)
    return blocks * 512 if blocks else stat.st_size


def walk(top):
    """Every file under a folder, as (path, stat). Links are skipped: they point somewhere counted."""
    stack = [top]
    while stack:
        try:
            entries = list(os.scandir(stack.pop()))
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    stack.append(entry.path)
                else:
                    yield entry.path, entry.stat(follow_symlinks=False)
            except OSError:
                continue


def day_of(seconds):
    """The local day a stamp falls on, or None for one no calendar reaches: an app can sync any
    number as a time, and one of those mustn't end the sweep."""
    try:
        return datetime.fromtimestamp(seconds).date().isoformat()
    except (OverflowError, ValueError, OSError):
        return None


class Inventory:
    def __init__(self, home, users, source_of, prefix, log=None):
        self.home = home
        self.users = users  # returns the user names
        self.source_of = source_of
        self.log = log  # the hub's own records, where xrnfit's health is kept
        self.log_root = os.path.join(prefix, "var", "log", "sv")
        self.rule_logs = {}  # path -> [bytes read, {day: reports}], so only what's new gets read

    def data_root(self, service):
        if service.folder == "radicale":
            return os.path.join(self.home, "radicale", "collections") + os.sep
        if service.folder == "xrninsta":  # one person's list, not kept per user
            return os.path.join(self.home, "xrninsta", "data") + os.sep
        return os.path.join(home_folder(self.home, service.folder), "data", "users") + os.sep

    def measure(self, service, fed):
        """What one app's folder holds, counting calendar files into `fed` by the day they changed."""
        root = self.data_root(service)
        found = {"bytes": 0, "extra": 0, "files": 0, "events": 0}
        for path, st in walk(home_folder(self.home, service.folder)):
            if not path.startswith(root) or CALENDAR_CACHE in path:
                found["extra"] += on_disk(st)
                continue
            found["bytes"] += on_disk(st)
            found["files"] += 1
            if path.endswith(".ics"):
                found["events"] += 1
                day = day_of(st.st_mtime)
                if day in fed:
                    fed[day]["calendar"] += 1
        return found

    def rule_reports(self, path):
        """{day: lines} of a device log that only ever grows."""
        seen = self.rule_logs.setdefault(path, [0, Counter()])
        try:
            if os.path.getsize(path) < seen[0]:
                seen[0], seen[1] = 0, Counter()
            with open(path, "rb") as f:
                f.seek(seen[0])
                data = f.read()
        except OSError:
            return seen[1]
        end = data.rfind(b"\n") + 1
        for line in data[:end].splitlines():
            match = RULE_STAMP.search(line)
            if match:
                seen[1][day_of(int(match[1]) / 1000)] += 1
        seen[0] += end
        return seen[1]

    def records(self, fed):
        """How many things each app holds, adding the dated ones to `fed`."""

        def came_in(app, seconds, count=1):
            day = day_of(seconds)
            if day in fed:
                fed[day][app] += count

        counts = Counter()
        for user in self.users():
            src = self.source_of(user)
            _, entries, _ = src.habits()
            counts["xrnloops"] += sum(len(per) for per in entries.values())
            for stamp in src.entry_times.values():
                came_in("xrnloops", stamp / 1000)
            reports = self.rule_reports(src.path("xrnrule", "events.jsonl"))
            counts["xrnrule"] += sum(reports.values())
            for day, count in reports.items():
                if day in fed:
                    fed[day]["xrnrule"] += count
            bills = src.wallet().get("bills") or []
            counts["xrnwal"] += len(bills)
            for bill in bills:
                at = bill.get("atMillis") if isinstance(bill, dict) else None
                if isinstance(at, (int, float)):
                    came_in("xrnwal", at / 1000)
            counts["xrnhome"] += len(src.inventory())
            counts["xrntube"] += src.channel_count()
        for user in self.users():
            for created in photo_items(self.home, user):
                counts["xrnimg"] += 1
                came_in("xrnimg", created / 1000)
        songs, _ = insta_songs(self.home)
        counts["xrninsta"] += len(songs)
        for song in songs:
            if isinstance(song.get("added"), (int, float)):
                came_in("xrninsta", song["added"] / 1000)
        if self.log:
            first = min(fed, default=None)
            since = datetime.fromisoformat(first).timestamp() * 1000 if first else 0
            for user in self.users():
                db = self.log.db(user)
                counts["xrnfit"] += health.size(db)[0]
                for received, count in health.received_by_day(db, since).items():
                    came_in("xrnfit", received / 1000, count)
        return counts

    def health_bytes(self):
        """(bytes the health records take, of which in files under the hub's own folder), so they
        can be moved from the hub's share to xrnfit's."""
        total = inside = 0
        hub_root = os.path.realpath(self.data_root(BY_NAME["xrnhub"]))
        for user in self.users():
            took = health.stored_bytes(self.log.db(user)) or 0
            total += took
            path = os.path.realpath(os.path.join(self.log.data_dir, "users", user, "hub.sqlite"))
            if path.startswith(hub_root + os.sep):  # else it wasn't measured as the hub's, as in the demo
                inside += took
        return total, inside

    def backup(self):
        """The newest backup copy the laptop has made here: it leaves one beside each database."""
        found = glob.glob(os.path.join(self.home, "*", "data", "users", "*", "*.backup.sqlite"))
        found += glob.glob(os.path.join(self.home, "*", "data", "*.backup.sqlite"))  # xrninsta's
        stamps = []
        for path in found:
            try:
                stamps.append(os.path.getmtime(path))
            except OSError:
                pass
        return max(stamps) if stamps else None

    def logs(self):
        return sum(on_disk(st) for _, st in walk(self.log_root))

    def sweep(self, now):
        today = datetime.fromtimestamp(now).date()
        days = [(today - timedelta(days=i)).isoformat() for i in range(FED_DAYS - 1, -1, -1)]
        fed = {day: Counter() for day in days}
        apps = {}
        for service in SERVICES:
            if service.folder:
                size = self.measure(service, fed)
                apps[service.name] = {
                    "name": service.name, "bytes": size["bytes"], "extra": size["extra"], "files": size["files"],
                    "records": size["files"] if service.name in BY_FILES else size["events"], "unit": UNITS[service.name],
                }
        if self.log:
            took, inside = self.health_bytes()
            apps["xrnfit"] = {"name": "xrnfit", "bytes": took, "extra": 0, "files": 0, "records": 0, "unit": UNITS["xrnfit"]}
            apps["xrnhub"]["bytes"] = max(apps["xrnhub"]["bytes"] - inside, 0)
        for name, count in self.records(fed).items():
            apps[name]["records"] += count
        return {
            "at": now,
            "apps": sorted(apps.values(), key=lambda a: -a["bytes"]),
            "total": sum(a["bytes"] for a in apps.values()),
            "extra": sum(a["extra"] for a in apps.values()),
            "fed": {"days": days, "apps": {
                name: [fed[day][name] for day in days]
                for name in apps if any(fed[day][name] for day in days)
            }},
            "logs": self.logs(),
            "backup": self.backup(),
        }

"""A made-up home folder laid out as the home server's is, for the tests. All the data is invented."""
import json
import os
import sqlite3
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))

USER = "tester"
ICS_HEAD = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
ICS_TAIL = "END:VCALENDAR\r\n"


def ms(*when):
    return int(datetime(*when).timestamp() * 1000)


def ics(*lines):
    return ICS_HEAD + "BEGIN:VEVENT\r\n" + "\r\n".join(lines) + "\r\nEND:VEVENT\r\n" + ICS_TAIL


def write_json(path, doc):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f)


def habit(uuid, name, **over):
    data = {
        "name": name, "archived": False, "position": 0, "type": 0, "targetType": 0, "targetValue": 0,
        "unit": "", "freqNum": 1, "freqDen": 1, "color": 1, "description": "", "question": "", "reminder": None,
    }
    data.update(over)
    return uuid, data


def make_loops(home, habits, entries, day_start=3, user=USER):
    folder = os.path.join(home, "xrnloops-sync", "data", "users", user)
    os.makedirs(folder, exist_ok=True)
    db = sqlite3.connect(os.path.join(folder, "loop.sqlite"))
    db.executescript("""
        CREATE TABLE meta(key TEXT PRIMARY KEY, value INTEGER NOT NULL);
        CREATE TABLE habits(uuid TEXT PRIMARY KEY, data TEXT NOT NULL, mtime INTEGER NOT NULL,
                            deleted INTEGER NOT NULL DEFAULT 0, seq INTEGER NOT NULL);
        CREATE TABLE entries(habit TEXT NOT NULL, date TEXT NOT NULL, value INTEGER NOT NULL,
                             notes TEXT NOT NULL DEFAULT '', mtime INTEGER NOT NULL, seq INTEGER NOT NULL DEFAULT 0,
                             PRIMARY KEY(habit, date));
    """)
    db.execute("INSERT INTO meta VALUES('day_start_hour', ?)", (day_start,))
    for uuid, data in habits:
        db.execute("INSERT INTO habits VALUES(?,?,?,?,?)", (uuid, json.dumps(data), 1, 0, 1))
    for uuid, day, value in entries:
        db.execute("INSERT INTO entries(habit, date, value, mtime) VALUES(?,?,?,?)",
                   (uuid, day, value, ms(*map(int, day.split("-")), 12)))
    db.commit()
    db.close()


def make_rule(home, data, events=(), user=USER):
    folder = os.path.join(home, "xrnrule-sync", "data", "users", user)
    write_json(os.path.join(folder, "state.json"), {"version": 1, "updated": "", "by": "", "data": data})
    with open(os.path.join(folder, "events.jsonl"), "w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


def make_wallet(home, data, user=USER):
    write_json(os.path.join(home, "xrnwal-sync", "data", "users", user, "state.json"),
               {"version": 1, "updated": "", "by": "", "data": data})


def make_home(home, items, user=USER):
    write_json(os.path.join(home, "xrnhome-sync", "data", "users", user, "state.json"),
               {"version": 1, "updated": "", "by": "", "data": {"items": items}})


def make_insta(home, songs, holds=()):
    """xrninsta's database as its server makes it: songs (title, artists, day, genre, added ms or None)
    and holds (title, artists, note)."""
    folder = os.path.join(home, "xrninsta", "data")
    os.makedirs(folder, exist_ok=True)
    db = sqlite3.connect(os.path.join(folder, "xrninsta.sqlite"))
    db.executescript("""
        CREATE TABLE songs(id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, artists TEXT NOT NULL,
            day INTEGER NOT NULL, genre TEXT NOT NULL, added INTEGER, updated INTEGER NOT NULL);
        CREATE TABLE holds(id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, artists TEXT NOT NULL DEFAULT '',
            note TEXT NOT NULL DEFAULT '', added INTEGER, updated INTEGER NOT NULL);
    """)
    db.executemany("INSERT INTO songs(title, artists, day, genre, added, updated) VALUES(?,?,?,?,?,1)", songs)
    db.executemany("INSERT INTO holds(title, artists, note, added, updated) VALUES(?,?,?,NULL,1)", holds)
    db.commit()
    db.close()


def make_calendar(home, name, files, display=None, color="#1c6fd8ff", user=USER):
    folder = os.path.join(home, "radicale", "collections", "collection-root", user, name)
    os.makedirs(folder, exist_ok=True)
    write_json(os.path.join(folder, ".Radicale.props"),
               {"D:displayname": display or name, "ICAL:calendar-color": color, "tag": "VCALENDAR"})
    for i, text in enumerate(files):
        with open(os.path.join(folder, f"{i}.ics"), "w", encoding="utf-8") as f:
            f.write(text)


class FakePhone:
    """A made-up phone: the /proc, /sys, runit and log trees the monitor reads, in a temp folder,
    with everything Android lets an app see and invented numbers in it."""

    def __init__(self, root):
        self.root = root
        self.prefix = os.path.join(root, "usr")
        self.proc = os.path.join(root, "proc")
        self.sys = os.path.join(root, "sys")
        self.home = os.path.join(root, "home")
        self.commands = {}  # program name -> (exit code, output)
        self.api = {}  # local API path -> text
        self.ran = []
        for folder in (self.proc, self.sys, self.home):
            os.makedirs(folder, exist_ok=True)
        self.put(os.path.join(self.proc, "meminfo"),
                 "MemTotal:  8000000 kB\nMemFree: 100000 kB\nMemAvailable: 3000000 kB\nSwapTotal: 1000 kB\nSwapFree: 400 kB\n")

    def put(self, path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def zone(self, index, kind, milli):
        folder = os.path.join(self.sys, "class", "thermal", f"thermal_zone{index}")
        self.put(os.path.join(folder, "type"), kind + "\n")
        self.put(os.path.join(folder, "temp"), f"{milli}\n")

    def core(self, index, now_khz, max_khz):
        folder = os.path.join(self.sys, "devices", "system", "cpu", f"cpu{index}", "cpufreq")
        self.put(os.path.join(folder, "scaling_cur_freq"), f"{now_khz}\n")
        self.put(os.path.join(folder, "cpuinfo_max_freq"), f"{max_khz}\n")

    def service(self, runit, pid, state="run", since=None):
        folder = os.path.join(self.prefix, "var", "service", runit, "supervise")
        self.put(os.path.join(folder, "stat"), state + "\n")
        self.put(os.path.join(folder, "pid"), f"{pid}\n" if pid else "")
        if since:
            os.utime(os.path.join(folder, "pid"), (since, since))

    def process(self, pid, ticks=100, rss_pages=1000, rchar=5000, wchar=2000, disk=4096, name="python (3.14)",
                parent=0, argv=None):
        tail = ["S", str(parent)] + ["0"] * 9 + [str(ticks), "0"] + ["0"] * 4 + ["5"] + ["0"] * 3 + [str(rss_pages)] + ["0"] * 20
        self.put(os.path.join(self.proc, str(pid), "stat"), f"{pid} ({name}) " + " ".join(tail) + "\n")
        if argv is not None:
            with open(os.path.join(self.proc, str(pid), "cmdline"), "wb") as f:
                f.write(b"".join(a.encode() + b"\0" for a in argv))
        self.put(os.path.join(self.proc, str(pid), "io"),
                 f"rchar: {rchar}\nwchar: {wchar}\nsyscr: 1\nsyscw: 1\nread_bytes: 0\nwrite_bytes: {disk}\n")

    def supervised(self, services, first_pid=100, root=9000):
        """runit as the phone runs it: one runsvdir, a runsv for each service under it, and each
        service's program under its runsv, at first_pid, first_pid + 1, ..."""
        self.process(root, parent=1, name="runsvdir", argv=[f"{self.prefix}/bin/runsvdir", f"{self.prefix}/var/service"])
        for i, service in enumerate(services):
            self.process(root + 1 + i, parent=root, name="runsv", argv=["runsv", service.runit])
            self.program(service, first_pid + i, parent=root + 1 + i)

    def program(self, service, pid, parent, **counters):
        """A service's own process, run the way its runit run file runs it."""
        argv = ["python", f"{self.home}/{service.folder}/{service.program}", "--port", str(service.port)]
        if service.name == "calendar":
            argv = [f"{self.prefix}/bin/python3.14", f"{self.prefix}/bin/radicale"]
        elif service.port is None:
            argv = [service.program, "-D"]
        self.process(pid, parent=parent, argv=argv, **counters)

    def log(self, runit, *lines, name="current"):
        path = os.path.join(self.prefix, "var", "log", "sv", runit, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.writelines(line + "\n" for line in lines)
        return path

    def run(self, args, timeout=None):
        self.ran.append(args)
        return self.commands.get(args[0], (None, ""))

    def machine(self):
        from hublib.machine import Machine

        phone = self

        class Fake(Machine):
            def answers(self, service):
                return phone.commands.get(f"answers:{service.name}", True)

        return Fake(self.home, self.prefix, proc=self.proc, sys_dir=self.sys, run=self.run, local_api=self.api.get)


def stamp_of(when):
    """A datetime as svlogd writes it: in UTC."""
    from datetime import timezone

    return when.astimezone(timezone.utc).strftime("%Y-%m-%d_%H:%M:%S.12345")

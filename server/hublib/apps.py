"""The Apps page: every project in ~/Documents/Projects, and what the hub can see of each.

Where an app runs and whether its repo is public live nowhere a program could read, so the
catalogue is written here by hand. What changes on its own is joined in from what the hub
already has, and nothing new is scanned for it:

    up, since    the monitor's last look at each service (watcher.current)
    records      the inventory's last sweep (watcher.data), by the same name the Data page uses
    week         of those, how many came in over the last 7 days, for apps whose records carry dates
    lastIn       when the app last wrote here: its own files' times, the newest event it sent the hub,
                 the newest health record (xrnfit)
    released     the newest release the server hands out, from a service's update/ folder: a phone
                 app's app.json, an extension's updates.xml

The live part reads the owner's files and sums every account's records, so it's for the owner, as
the monitor is; any other account gets the catalogue, up or down, and the releases. An answer is
kept for a little while, since the page asks again every minute and the facts change slower.

xrnfit has no service: what it sends is kept by the hub. Its row names the hub as its server, so the
card says which one it talks to, as every other app's does, and is up or down with the hub. The
Server page's Overview has a card for it, fit_card(), built from the same lastIn and week as its row
here, and Services lists it from that card.
"""
import json
import os
import re
import threading
import time
from collections import namedtuple

from . import daybook, health
from .log import APP_NAME
from .machine import BY_NAME as SERVICES, home_folder

CACHE_S = 30
WEEK_DAYS = 7
# xrnfit sends every few minutes while the watch is near the phone. Hours of nothing means it has
# stopped: HyperOS refusing its service without Autostart, or the watch out of reach.
LATE_S = 6 * 3600
GITHUB = "https://github.com/Xernnn/"
RUNS_ON = ("Phone", "Laptop", "Browser", "Server", "Telegram")
MAX_RELEASE_BYTES = 64 * 1024
# A version as the apps number them. Anything else in a release file is left out rather than shown.
VERSION = re.compile(r"[0-9]{1,6}(?:\.[0-9]{1,6}){0,3}")
UPDATE_CHECK = re.compile(r"<updatecheck\b[^>]*?\bversion=['\"]([^'\"]{1,40})['\"]")

Group = namedtuple("Group", "id title")
# service: the monitor's name for its server, if it has one. holds: the inventory's name for what it
# keeps there. package: its Android application id. web: its own page on this host. kept_by: the
# service that keeps its data when it has none of its own.
App = namedtuple("App", "name group runs repo public logo service holds package web kept_by",
                 defaults=(None, None, None, None, None))

GROUPS = (
    Group("time", "Time and habits"),
    Group("life", "Money, home and health"),
    Group("focus", "Focus, mail and typing"),
    Group("play", "Music and play"),
    Group("server", "The home server"),
)

APPS = (
    App("xrncal", "time",
        ("Laptop", "Server"), "xrncal-desktop", True, "apps/xrncal.svg", service="calendar", holds="calendar"),
    App("xrnloops", "time",
        ("Phone", "Laptop", "Server"), "xrnloops", True, "apps/xrnloops.svg", service="xrnloops",
        holds="xrnloops", package="com.xern.xrnloops", web="/xrnloops/"),
    App("xrnnote", "time",
        ("Phone", "Laptop", "Server"), "xrnnote", False, "apps/xrnnote.svg", service="xrnnote",
        holds="xrnnote", package="com.xern.xrnnote", web="/xrnnote/"),
    App("xrnrule", "time",
        ("Phone", "Laptop", "Browser", "Server"), "xrnrule", True, "apps/xrnrule.svg", service="xrnrule",
        holds="xrnrule", package="com.xern.xrnrule", web="/xrnrule/"),
    App("xrnwal", "life",
        ("Phone", "Server"), "xrnwal", True, "apps/xrnwal.svg", service="xrnwal", holds="xrnwal",
        package="com.xern.xrnwal"),
    App("xrnhome", "life",
        ("Phone", "Laptop", "Server"), "xrnhome", True, "apps/xrnhome.svg", service="xrnhome", holds="xrnhome",
        package="com.xern.xrnhome", web="/xrnhome/"),
    App("xrnimg", "life",
        ("Phone", "Laptop", "Server"), "xrnimg", False, "apps/xrnimg.svg", service="xrnimg",
        holds="xrnimg", package="com.xern.xrnimg", web="/xrnimg/"),
    App("xrnfit", "life",
        ("Phone", "Server"), "xrnfit", False, "apps/xrnfit.svg", holds="xrnfit", package="com.xern.xrnfit",
        kept_by="xrnhub"),
    App("xrnmail", "focus",
        ("Laptop",), "xrnmail", True, "apps/xrnmail.svg"),
    App("xrntube", "focus",
        ("Browser", "Server"), "xrntube", True, "apps/xrntube.svg", service="xrntube", holds="xrntube"),
    App("xrnsoc", "focus",
        ("Browser",), "xrnsoc", True, "apps/xrnsoc.svg"),
    App("xrnmoni", "focus",
        ("Laptop", "Browser"), "xrnmoni", True, "apps/xrnmoni.svg"),
    App("xrnkey", "focus",
        ("Phone", "Laptop"), "xrnkey", True, "apps/xrnkey.svg", package="com.xern.xrnkey"),
    App("xrnsheet", "play",
        ("Laptop",), "xrnsheet", True, "apps/xrnsheet.svg"),
    App("xrninsta", "play",
        ("Server", "Telegram"), "xrninsta", True, "apps/xrninsta.svg", service="xrninsta", holds="xrninsta",
        web="/xrninsta/"),
    App("xrnchips", "play",
        ("Phone",), "xrnchips", True, "apps/xrnchips.svg", package="com.xern.xrnchips"),
    App("xrnlatro", "play",
        ("Laptop",), "xrnlatro", False, "apps/xrnlatro.svg"),
    App("xrnhub", "server",
        ("Server", "Phone", "Laptop"), "xrnhub", False, "icon.svg", service="xrnhub", package="com.xern.xrnhub"),
    App("home-server", "server",
        ("Laptop",), "home-server", False, "apps/home-server.svg"),
)
BY_NAME = {a.name: a for a in APPS}


def server_of(app):
    """The service an app talks to on the home server: its own, or the one that keeps its data."""
    return app.service or app.kept_by


def catalogue_row(app):
    return {
        "name": app.name, "group": app.group, "runs": list(app.runs), "repo": app.repo,
        "repoUrl": GITHUB + app.repo, "public": app.public, "logo": app.logo, "service": app.service,
        "server": server_of(app), "package": app.package, "web": app.web,
    }


def stamp(path):
    try:
        return os.stat(path).st_mtime
    except OSError:
        return None


def newest(*stamps):
    """The latest of some times in seconds, as a whole second, or None if none is known."""
    found = [s for s in stamps if s is not None]
    return int(max(found)) if found else None


class Releases:
    """What each service hands out in its update/ folder, read again only when a file changes."""

    def __init__(self, home):
        self.home = home
        self.lock = threading.Lock()
        self.seen = {}  # path -> (mtime_ns, size, what it said)

    def read(self, path, parse):
        try:
            st = os.stat(path)
        except OSError:
            return None
        key = (st.st_mtime_ns, st.st_size)
        with self.lock:
            hit = self.seen.get(path)
        if hit and hit[0] == key:
            return hit[1]
        found = None
        if st.st_size <= MAX_RELEASE_BYTES:
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    found = parse(f.read())
            except OSError:
                found = None
        with self.lock:
            self.seen[path] = (key, found)
        return found

    def of(self, folder):
        """[{kind, version, code?}]: the phone app's release, then the extension's."""
        top = os.path.join(home_folder(self.home, folder), "data", "update")
        found = []
        phone = self.read(os.path.join(top, "app.json"), phone_release)
        if phone:
            found.append(phone)
        extension = self.read(os.path.join(top, "updates.xml"), extension_release)
        if extension:
            found.append(extension)
        return found


def phone_release(text):
    """An app's app.json, as scripts/release.mjs writes it: {"versionName", "versionCode", ...}."""
    try:
        doc = json.loads(text)
    except ValueError:
        return None
    if not isinstance(doc, dict):
        return None
    name, code = doc.get("versionName"), doc.get("versionCode")
    if not isinstance(name, str) or not VERSION.fullmatch(name):
        return None
    if isinstance(code, bool) or not isinstance(code, int) or not 0 < code < 2**31:
        code = None
    return {"kind": "phone", "version": name, "code": code}


def extension_release(text):
    """A browser extension's updates.xml, the file a browser's own updater reads."""
    match = UPDATE_CHECK.search(text)
    if not match or not VERSION.fullmatch(match[1]):
        return None
    return {"kind": "extension", "version": match[1], "code": None}


def written(src, name):
    """When [name] last wrote one of its files here for this user, in seconds, or None. Each app keeps
    different files; these are the ones every write of its changes."""
    if name == "xrncal":
        # Radicale swaps an event in by rename and removes one by unlinking it, so a calendar's folder
        # changes with every write. Its .Radicale.cache is a folder of its own and doesn't touch it.
        try:
            folders = [e for e in os.scandir(src.calendar.root) if e.is_dir() and not e.name.startswith(".")]
        except OSError:
            return None
        return newest(*(stamp(f.path) for f in folders))
    files = {
        "xrnloops": ("loop.sqlite", "loop.sqlite-wal"),
        "xrnrule": ("state.json", "events.jsonl"),
        "xrnwal": ("state.json",),
        "xrnhome": ("state.json",),
        "xrntube": ("state.json",),
        "xrnnote": ("notes.sqlite", "notes.sqlite-wal"),  # ciphertext: only its times are read
    }.get(name)
    if files:
        return newest(*(stamp(src.path(name, f)) for f in files))
    if name == "xrninsta":
        db = os.path.join(src.home, "xrninsta", "data", "xrninsta.sqlite")
        return newest(stamp(db), stamp(db + "-wal"))
    return None


def came_in(fed, name):
    """How many of [name]'s dated records came in over the last week, or None if it has no dates."""
    days = (fed or {}).get("apps", {}).get(name)
    if not days:
        return None
    return sum(v or 0 for v in days[-WEEK_DAYS:])


class AppsPage:
    def __init__(self, home, clock=time.time):
        self.home = home
        self.clock = clock
        self.releases = Releases(home)
        self.lock = threading.Lock()
        self.kept = {}  # (user, owner) -> (monotonic, answer)

    def answer(self, user, owner, watcher, log, src):
        key = (user, owner)
        with self.lock:
            hit = self.kept.get(key)
        if hit and time.monotonic() - hit[0] < CACHE_S:
            return hit[1]
        doc = self.build(user, owner, watcher, log, src)
        with self.lock:
            self.kept[key] = (time.monotonic(), doc)
        return doc

    def build(self, user, owner, watcher, log, src):
        services = {row["name"]: row for row in (watcher.current or {}).get("services") or []}
        data = watcher.data or {}
        holdings = {row["name"]: row for row in data.get("apps") or []}
        apps = []
        for app in APPS:
            row = catalogue_row(app)
            service = services.get(server_of(app)) if server_of(app) else None
            row["up"] = (service["state"] == "run" and service.get("answers") is not False) if service else None
            folder = SERVICES[app.service].folder if app.service in SERVICES else None
            row["released"] = self.releases.of(folder) if folder else []
            if owner:
                row.update(self.live(app, service, holdings, data, log, user, src))
            apps.append(row)
        return {
            "at": int(self.clock()),
            "counted": data.get("at"),
            "owner": owner,
            "groups": [g._asdict() for g in GROUPS],
            "apps": apps,
        }

    def live(self, app, service, holdings, data, log, user, src):
        held = holdings.get(app.holds) if app.holds else None
        return {
            "since": newest(service.get("since")) if service and service["state"] == "run" else None,
            "records": held["records"] if held else None,
            "unit": held["unit"] if held else None,
            "week": came_in(data.get("fed"), app.holds) if held else None,
            "lastIn": last_in(app, log, user, src),
            "backup": data.get("backup") if app.name == "home-server" else None,
        }


def last_in(app, log, user, src):
    """When [app] last wrote here for [user], in whole seconds, or None: the newest of its own files'
    times, the newest event it sent the hub, and for xrnfit the newest health record."""
    reported = None
    if APP_NAME.fullmatch(app.name):
        last = log.latest(user, app.name)
        reported = last["at"] / 1000 if last else None
    received = None
    if app.name == "xrnfit":
        received = health.last_received(log.db(user))
        received = received / 1000 if received else None
    return newest(written(src, app.name), reported, received)


def fit_card(log, user, src, data, now):
    """xrnfit on the Overview: when it last sent something and how many readings came in this week,
    as its row on the Apps page has them, and today's figures as the Today page's health card works
    them out, with the newest SpO2 and stress beside them. `late` once nothing has come in for
    LATE_S; `lastIn` is None when nothing ever has. [data] is the inventory's last sweep, or None
    before the first, and the week is None with it. Times in the figures are epoch ms, as on Today."""
    db = log.db(user)
    day = daybook.habit_day(now, src.habits()[2])
    body = daybook.body_view(db, day) or {}
    last = last_in(BY_NAME["xrnfit"], log, user, src)
    week = came_in(data.get("fed"), "xrnfit") if data else None
    spo2, stress = health.latest(db, "spo2"), health.latest(db, "stress")
    return {
        "lastIn": last,
        "late": last is not None and now.timestamp() - last > LATE_S,
        "week": week if week is not None or not data else 0,
        "day": day.isoformat(),
        "steps": body.get("steps"),
        "sleep": body.get("sleep"),
        "resting": body.get("resting"),
        "weight": body.get("weight"),
        "spo2": {"at": spo2[0], "percent": health.whole(spo2[1])} if spo2 else None,
        "stress": {"at": stress[0], "level": health.whole(stress[1])} if stress else None,
    }

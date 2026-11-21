#!/usr/bin/env python3
"""The real hub on a made-up phone, for trying the page or the Android app without the real server.

    python3 tests/demo_server.py /tmp/some-empty-folder --port 15238
    adb reverse tcp:15238 tcp:15238     # then sign in to http://127.0.0.1:15238/xrnhub

Everything it serves is invented. The password is random, in <folder>/password, for the user
`tester`. With --fixtures <folder> it writes each API answer there and stops: those are the app's
contract tests, so regenerate them when an answer changes shape.

For trying alerts, SIGUSR1 stops xrnrule and pulls the charger out, and the next one mends both.
The hub notices within a minute.
"""
import argparse
import json
import math
import os
import random
import signal
import sys
import threading
import time
from datetime import datetime, timedelta

import bcrypt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import support  # noqa: E402
import xrnhub  # noqa: E402
from hublib import health, machine  # noqa: E402

DAY_S = 86400
METRICS = """tailscaled_inbound_bytes_total{{path="derp"}} 91000000
tailscaled_inbound_bytes_total{{path="direct_ipv4"}} {down}
tailscaled_outbound_bytes_total{{path="derp"}} 50000000
tailscaled_outbound_bytes_total{{path="direct_ipv6"}} {up}
"""
STATUS = {
    "BackendState": "Running", "Version": "1.90.1-demo", "Health": [],
    "Self": {"Online": True, "Relay": "syd"},
    "Peer": {
        "a": {"HostName": "funnel-ingress-node-1", "Online": True},
        "b": {"HostName": "funnel-ingress-node-2", "Online": True},
        "c": {"HostName": "laptop", "OS": "linux", "Online": True, "CurAddr": "198.51.100.4:41641", "Relay": "syd"},
        "d": {"HostName": "old-phone", "OS": "android", "Online": False, "Relay": "syd"},
    },
}
BATTERY = {"percentage": 86, "plugged": "PLUGGED_AC", "status": "CHARGING", "temperature": 27.0}
WIFI = {"supplicant_state": "COMPLETED", "rssi": -58, "link_speed_mbps": 433, "frequency_mhz": 5220}
FUNNEL = """https://example.ts.net (Funnel on)
|-- /          proxy http://127.0.0.1:5232
|-- /xrnloops  proxy http://127.0.0.1:5234
|-- /xrnhub    proxy http://127.0.0.1:5238
"""
LOG_TEXT = [
    "GET /api/state HTTP/1.1 200", "backup finished in 3s", "sync from phone: 4 changes", "listening on 127.0.0.1",
    "warning: slow query took 1.2s", "error: upstream timed out",
]
# xrnfit's requests, which are in the hub's log and listed on xrnfit's own page.
FIT_REQUESTS = ["POST /api/health HTTP/1.1", "GET /api/v1/health/steps?every=30 HTTP/1.1", "POST /api/health HTTP/1.1",
                "GET /api/v1/health/spo2 HTTP/1.1", "POST /api/health HTTP/1.1"]
PATHS = [("xrnrule", "/state"), ("xrntube", "/state"), ("xrnloops", "/api/changes"), ("xrnhome", "/photos/milk.jpg"),
         ("xrnfit", "/api/health")]
CLIENTS = [("203.0.113.9", 1400, 2), ("2001:db8::77", 420, 0), ("198.51.100.200", 21, 21)]
HABITS = [("run", "Run", 3), ("read", "Read 20 pages", 11), ("water", "Drink water", 8), ("sleep", "Sleep by midnight", 13)]


def stock_phone(phone, now, rng):
    phone.commands.update({
        "termux-battery-status": (0, json.dumps(BATTERY)),
        "termux-wifi-connectioninfo": (0, json.dumps(WIFI)),
        "tailscale": (0, FUNNEL),
        "getprop": (0, "Demo phone\n"),
        "sv": (0, "ok: run\n"),
    })
    phone.api.update({
        "/localapi/v0/usermetrics": METRICS.format(down=2900000000, up=7400000000),
        "/localapi/v0/status": json.dumps(STATUS),
    })
    phone.zone(0, "cpu-0-0-usr", 38000)
    phone.zone(1, "battery", 27000)
    phone.core(0, 900000, 1800000)
    phone.supervised(machine.SERVICES)
    for i, service in enumerate(machine.SERVICES):
        pid = 100 + i
        phone.service(service.runit, pid, since=now - (20 + i) * DAY_S)
        phone.program(service, pid, parent=9001 + i, ticks=9000 + i * 400, rss_pages=6000 + i * 1800, rchar=5 << 20,
                      wchar=2 << 20, disk=1 << 20)
        stamp = datetime.fromtimestamp(now)
        phone.log(service.runit, *[
            f"{support.stamp_of(stamp - timedelta(minutes=n * 7 + i))} {rng.choice(LOG_TEXT)}" for n in range(12, 0, -1)
        ])
        if service.name == "xrnhub":
            phone.log(service.runit, *[
                f'{support.stamp_of(stamp - timedelta(minutes=n * 5 + 2))} 203.0.113.9 "{request}" 200 -'
                for n, request in enumerate(FIT_REQUESTS)
            ])


def stock_apps(home, now):
    today = datetime.fromtimestamp(now).date()
    entries = []
    for offset in range(300):
        day = (today - timedelta(days=offset)).isoformat()
        for index, (uuid, _, _) in enumerate(HABITS):
            if (offset * 7 + index * 3) % 5:
                entries.append((uuid, day, 2))
    support.make_loops(home, [support.habit(u, n, color=c) for u, n, c in HABITS], entries)
    blocked = [
        {"t": "blocked", "name": name, "at": (now - hours * 3600) * 1000}
        for hours, name in zip(range(3, 300, 11), ["Game", "Video", "Social", "Game"] * 30)
    ]
    support.make_rule(home, demo_blocks(now), blocked)
    support.make_wallet(home, demo_wallet(now))
    support.make_home(home, demo_items(now))
    support.write_json(os.path.join(home, "xrntube-sync", "data", "users", support.USER, "state.json"),
                       {"channels": list(range(120))})
    events = []
    for offset in range(70):
        start = datetime.fromtimestamp(now) - timedelta(days=offset, hours=2)
        events.append(support.ics(
            f"UID:demo-{offset}", f"DTSTART:{start:%Y%m%dT%H%M%S}", f"DTEND:{start + timedelta(hours=2):%Y%m%dT%H%M%S}",
            f"SUMMARY:Lecture {offset}",
        ))
    support.make_calendar(home, "uni", events + demo_events_ahead(now), display="Uni", color="#1c6fd8ff")
    support.make_calendar(home, "work", events[::3], display="Work", color="#eb6834ff")
    support.make_calendar(home, "deadlines", demo_deadlines(now), display="Deadlines", color="#d6336cff")


def demo_blocks(now):
    """Three blocks for Today: two with a daily allowance, partly used, and one waiting on habits."""
    midnight = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0)
    day_start = int(midnight.timestamp() * 1000)
    minutes = 60_000
    return {
        "blocks": {
            "b1": {"id": "b1", "name": "Video", "mode": "ALLOWANCE", "dailyAllowanceMs": 45 * minutes},
            "b2": {"id": "b2", "name": "Social", "mode": "ALLOWANCE", "dailyAllowanceMs": 20 * minutes},
            "b3": {"id": "b3", "name": "Games", "mode": "HABIT"},
        },
        "allowance": {
            "b1": {"laptop": {"ms": 28 * minutes, "dayStartWall": day_start}},
            "b2": {"phone": {"ms": 17 * minutes, "dayStartWall": day_start}},
        },
        "items": {
            "i1": {"blockId": "b3", "habitId": "run", "text": "Run", "order": 1},
            "i2": {"blockId": "b3", "habitId": "read", "text": "Read", "order": 2},
        },
    }


WALLET_CATEGORIES = [(1, "Food", 0), (2, "Transport", 0), (3, "Shopping", 0), (4, "Fun", 0), (5, "Bills", 0),
                     (6, "Rent", 0), (7, "Salary", 1)]
SPENDING = [(1, 900, 3200), (2, 300, 1200), (3, 1500, 9000), (4, 800, 4500), (5, 4000, 12000)]


def demo_wallet(now):
    """Thirteen months of made-up spending against a monthly budget, with the rent marked as outside
    the budget the way xrnwal marks it (inBudget false), so the hub's figures leave it out too."""
    rng = random.Random(5)
    today = datetime.fromtimestamp(now)
    bills = []

    def bill(kind, category, cents, when, in_budget=True):
        bills.append({"id": len(bills) + 1, "ledgerId": 1, "type": kind, "categoryId": category, "accountId": 1,
                      "amountCents": cents, "currency": "AUD", "rate": 1.0, "atMillis": int(when.timestamp() * 1000),
                      "inBudget": in_budget, "note": ""})

    for back in range(400, -1, -1):
        day = (today - timedelta(days=back)).replace(hour=12, minute=0, second=0, microsecond=0)
        if back == 0:
            day = today - timedelta(minutes=40)
        for category, low, high in SPENDING:
            if rng.random() < (0.9 if category == 1 else 0.18):
                bill(0, category, rng.randrange(low, high), day)
        if day.day == 1:
            bill(0, 6, 185000, day.replace(hour=9), in_budget=False)
        if day.day in (1, 15):
            bill(1, 7, 260000, day.replace(hour=8))
    return {
        "settings": json.dumps({"baseCurrency": "AUD", "startDay": 1, "includeFuture": True, "budgetEnabled": True}),
        "ledgers": [{"id": 1, "name": "Daily"}],
        "accounts": [{"id": 1, "name": "Card", "currency": "AUD", "openingCents": 500000}],
        "categories": [{"id": i, "name": n, "type": t} for i, n, t in WALLET_CATEGORIES],
        "budgets": [{"id": 1, "categoryId": None, "amountCents": 160000}],
        "bills": bills,
    }


def demo_items(now):
    today = datetime.fromtimestamp(now).date()
    items = [{"id": i, "name": f"Item {i}", "quantity": 1} for i in range(24)]
    items += [{"id": 100 + i, "name": n, "wanted": True, "quantity": 0} for i, n in enumerate(["Milk", "Eggs", "Rice", "Coffee"])]
    items += [{"id": 200 + i, "name": n, "quantity": 1, "expiryDate": (today + timedelta(days=d)).isoformat()}
              for i, (n, d) in enumerate([("Yoghurt", 1), ("Spinach", 3)])]
    return items


def demo_events_ahead(now):
    """Something on now, later today and tomorrow, so Today's day has a shape."""
    start = datetime.fromtimestamp(now).replace(second=0, microsecond=0)
    tomorrow = start.replace(hour=0, minute=0) + timedelta(days=1)
    spans = [(start - timedelta(minutes=30), 90, "Study group"), (start + timedelta(hours=2), 60, "Call"),
             (tomorrow + timedelta(hours=9), 120, "Lecture"), (tomorrow + timedelta(hours=14), 60, "Tutorial")]
    return [support.ics(f"UID:ahead-{i}", f"DTSTART:{at:%Y%m%dT%H%M%S}",
                        f"DTEND:{at + timedelta(minutes=length):%Y%m%dT%H%M%S}", f"SUMMARY:{title}")
            for i, (at, length, title) in enumerate(spans)]


def demo_deadlines(now):
    today = datetime.fromtimestamp(now).date()
    return [support.ics(f"UID:due-{i}", f"DTSTART;VALUE=DATE:{today + timedelta(days=d):%Y%m%d}",
                        f"DTEND;VALUE=DATE:{today + timedelta(days=d + 1):%Y%m%d}", f"SUMMARY:{title}")
            for i, (d, title) in enumerate([(3, "Essay due"), (12, "Exam"), (40, "Project")])]


def stock_history(store, now, rng):
    """Weeks of made-up history, so every chart and range has something to draw."""
    names = [s.name for s in machine.SERVICES]
    first = now - 9 * DAY_S
    for at in range(first // 60 * 60, now // 60 * 60, 300):
        wave = math.sin(at / 9000)
        system = {
            "load1": round(0.4 + wave * 0.25 + rng.random() * 0.2, 2), "cpu": round(4 + wave * 2 + rng.random() * 2, 2),
            "speed": round(0.45 + wave * 0.1, 3), "mem_used": 2.9e9 + wave * 2e8, "cpu_temp": 34 + wave * 4,
            "batt_temp": 27 + wave, "net_in": 40000 + rng.randrange(90000), "net_out": 90000 + rng.randrange(200000),
        }
        services = {
            n: {"up": 1, "cpu": round(0.4 + rng.random() * 1.2, 2), "rss": 2.3e7 + i * 7e6}
            for i, n in enumerate(names)
        }
        store.add_minute(at, system, services)
    traffic = {}
    for at in range(now // 60 * 60 - 30 * DAY_S, now // 60 * 60, 600):
        for name, _ in PATHS:
            reqs = rng.randrange(1, 40)
            traffic[(at, name)] = {
                "reqs": reqs, "polls": reqs // 2, "writes": rng.randrange(0, 3), "refused": int(rng.random() < 0.02), "errors": 0,
            }
    store.add_traffic(traffic)
    store.roll_up(now)
    paths, clients = {}, {}
    today = datetime.fromtimestamp(now).date()
    for offset in range(30):
        day = (today - timedelta(days=offset)).isoformat()
        for name, path in PATHS:
            paths[(day, name, path, 200)] = rng.randrange(40, 900)
            paths[(day, name, path, 304)] = rng.randrange(0, 300)
        paths[(day, "xrnloops", "/wp-admin", 404)] = rng.randrange(1, 6)
        paths[(day, "xrnrule", "/state", 401)] = rng.randrange(0, 3)
        for ip, reqs, refused in CLIENTS:
            clients[(day, ip)] = (reqs, refused)
        store.set_data_day(day, {
            "calendar": {"bytes": 390000 - offset * 3000, "records": 90 - offset},
            "xrnloops": {"bytes": 60000 - offset * 500, "records": 900 - offset * 4},
            "xrnrule": {"bytes": 37000, "records": 60},
        })
    store.add_paths(paths)
    store.add_clients(clients)
    store.add_logins([
        ((now - 3600) * 1000, "ssh", 1, "tester", "198.51.100.4"),
        ((now - 7200) * 1000, "ssh", 1, "tester", "198.51.100.4"),
        ((now - 9 * 3600) * 1000, "calendar", 0, "tester", "203.0.113.50"),
    ])
    store.note_incident("restart", "xrnhub", now - 3 * 3600)
    store.note_incident("gap", "", now - 2 * DAY_S, ended=now - 2 * DAY_S + 7200)
    store.note_incident("down", "xrnrule", now - 20 * DAY_S, ended=now - 20 * DAY_S + 900)


def break_on_signal(phone, now):
    index, service = next((i, s) for i, s in enumerate(machine.SERVICES) if s.name == "xrnrule")
    broken = [False]

    def flip(*_):
        broken[0] = not broken[0]
        if broken[0]:
            phone.service(service.runit, None, state="down")
        else:
            phone.service(service.runit, 100 + index, since=now)
        plugged = "UNPLUGGED" if broken[0] else BATTERY["plugged"]
        phone.commands["termux-battery-status"] = (0, json.dumps({**BATTERY, "plugged": plugged}))

    signal.signal(signal.SIGUSR1, flip)


def keep_calling(phone):
    """A few requests a minute in each service's log, as Funnel's visitors would leave them."""
    rng = random.Random()
    down, up = 2900000000, 7400000000
    while True:
        down += rng.randrange(20000, 90000)
        up += rng.randrange(60000, 300000)
        phone.api["/localapi/v0/usermetrics"] = METRICS.format(down=down, up=up)
        name, path = rng.choice(PATHS)
        # xrnfit has no service: its requests land in the hub's log.
        runit = next(s.runit for s in machine.SERVICES if s.name == ("xrnhub" if name == "xrnfit" else name))
        status = rng.choice((200, 200, 200, 304, 404))
        ip = rng.choice(CLIENTS)[0]
        phone.log(runit, f'{support.stamp_of(datetime.now())} {ip} "GET {path} HTTP/1.1" {status} -')
        time.sleep(2)


SONGS = [("Song one", "Band A", "Pop"), ("Second song", "Singer B", "Indie"), ("Third", "Band A", "Rock"),
         ("Four", "Duo C", "Ballad"), ("Fifth song", "Singer B", "Pop")]
UPDATES_XML = """<?xml version='1.0' encoding='UTF-8'?>
<gupdate xmlns='http://www.google.com/update2/response' protocol='2.0'>
  <app appid='{appid}'>
    <updatecheck codebase='https://example.test/{name}-{version}.crx' version='{version}' hash_sha256='00' />
  </app>
</gupdate>
"""


def stock_for_apps(home, log, now):
    """What the Apps page joins in that the rest doesn't make: the releases the server hands out, the
    song list, a report from xrnmail, a backup the laptop left, and each app's last write."""
    def release(folder, name, phone=None, extension=None):
        top = os.path.join(home, folder, "data", "update")
        os.makedirs(top, exist_ok=True)
        if phone:
            support.write_json(os.path.join(top, "app.json"),
                               {"versionCode": phone[1], "versionName": phone[0], "apk": f"{name}-{phone[1]}.apk"})
        if extension:
            with open(os.path.join(top, "updates.xml"), "w", encoding="utf-8") as f:
                f.write(UPDATES_XML.format(appid=name[0] * 32, name=name, version=extension))

    release("xrnrule-sync", "xrnrule", phone=("0.2.17", 17), extension="2.3.6")
    release("xrnhome-sync", "xrnhome", phone=("1.1.0", 7))
    release("xrntube-sync", "xrntube", extension="2.1.10")
    first = datetime.fromtimestamp(now).date() - timedelta(days=89)
    songs = [(*SONGS[i % len(SONGS)][:2], i + 1, SONGS[i % len(SONGS)][2],
              int((datetime.combine(first + timedelta(days=i), datetime.min.time()).timestamp() + 30000) * 1000))
             for i in range(90)]
    support.make_insta(home, songs)
    log.add(support.USER, [{"app": "xrnmail", "kind": "batch", "at": (now - 1500) * 1000, "value": 3},
                           {"app": "xrnmail", "kind": "state.held", "at": (now - 600) * 1000, "value": 4,
                            "detail": {"held": 4, "nextBatch": (now + 5400) * 1000}}])
    users = os.path.join(home, "{}", "data", "users", support.USER)
    backup = os.path.join(users.format("xrnloops-sync"), "loop.backup.sqlite")
    open(backup, "wb").close()
    for path, seconds in ((backup, 6 * 3600), (os.path.join(users.format("xrnloops-sync"), "loop.sqlite"), 300),
                          (os.path.join(users.format("xrnrule-sync"), "state.json"), 1200),
                          (os.path.join(users.format("xrnrule-sync"), "events.jsonl"), 1200),
                          (os.path.join(users.format("xrnwal-sync"), "state.json"), 2 * 3600),
                          (os.path.join(users.format("xrnhome-sync"), "state.json"), DAY_S + 3600),
                          (os.path.join(users.format("xrntube-sync"), "state.json"), 3 * DAY_S)):
        os.utime(path, (now - seconds, now - seconds))


def write_fixtures(folder, port, password):
    import base64
    import http.client

    os.makedirs(folder, exist_ok=True)
    pair = base64.b64encode(f"{support.USER}:{password}".encode()).decode()
    day = (datetime.now().date() - timedelta(days=1)).isoformat()  # a whole day, with its steps and sleep in
    wanted = {
        "now": "/api/server/now?since=0&feed=1", "history": "/api/server/history?range=24h",
        "history-service": "/api/server/history?range=24h&service=xrnloops", "services": "/api/server/services",
        "traffic": "/api/server/traffic?range=7d", "data": "/api/server/data", "security": "/api/server/security",
        "logs": "/api/server/logs?limit=5", "review": "/api/review", "day": f"/api/day?date={day}",
        "apps": "/api/apps", "logstats": "/api/server/logstats?range=24h",
        "logstats-service": "/api/server/logstats?range=7d&service=xrnloops",
        "logs-quiet": "/api/server/logs?limit=5&routine=0",
    }

    def ask(path):
        link = http.client.HTTPConnection("127.0.0.1", port)
        link.request("GET", path, headers={"Authorization": f"Basic {pair}"})
        res = link.getresponse()
        body = res.read()
        if res.status != 200:
            raise SystemExit(f"{path}: {res.status}")
        return body

    # Being watched is what turns on quick sampling, and rates need two samples close together.
    ask(wanted["now"])
    time.sleep(8)
    for name, path in wanted.items():
        with open(os.path.join(folder, f"{name}.json"), "wb") as f:
            f.write(ask(path))


def stock_health(log, now):
    """Four months of made-up health: the Mi Fitness export before the watch moved to Gadgetbridge
    (with three days both have, so the priority shows), Gadgetbridge until xrnfit took the watch over
    for the last fortnight, with its SpO2 and stress, and Renpho's weighings before the scale's."""
    rng = random.Random(11)
    today = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0)
    batches = {}
    received = now * 1000

    def add(source, record):
        # The exports came in at once; the watch's and the scale's on their day, as xrnfit sends them.
        when = now * 1000 if source.startswith("import-") else received
        batches.setdefault((source, when), []).append(record)

    for back in range(120, -1, -1):
        midnight = today - timedelta(days=back)
        received = min(int((midnight + timedelta(hours=23)).timestamp() * 1000), now * 1000)
        at = lambda h, m=0: int((midnight + timedelta(hours=h, minutes=m)).timestamp() * 1000)  # noqa: E731
        if back > 60:
            sources = ["import-mifitness"]
        elif back > 13:
            sources = ["gadgetbridge"] + (["import-mifitness"] if back > 57 else [])
        else:
            sources = ["xrnfit-watch"] + (["gadgetbridge"] if back == 13 else [])
        weekend = midnight.weekday() >= 5
        for source in sources:
            tag = f"{source[:3]}{back}"
            last_hour = min(22, datetime.fromtimestamp(now).hour) if back == 0 else 22
            for hour in range(7, last_hour + 1):
                busy = 1400 if hour in (8, 12, 18) and not weekend else 350
                add(source, {"id": f"{tag}s{hour}", "kind": "steps", "start": at(hour), "end": at(hour, 59),
                             "value": int(busy * rng.uniform(0.4, 1.6))})
            every = 5 if back <= 1 else 30
            samples = []
            for minute in range(0, 24 * 60, every):
                t = at(0, minute)
                if t > now * 1000:
                    break
                awake = 7 * 60 <= minute <= 23 * 60
                base = 72 if awake else 54
                samples.append([t, int(base + rng.uniform(-6, 12) + (40 if minute == 18 * 60 + 30 and not weekend else 0))])
            if samples:
                add(source, {"id": f"{tag}h", "kind": "heart_rate", "samples": samples})
            add(source, {"id": f"{tag}r", "kind": "resting_heart_rate", "start": at(6),
                         "value": int(55 + rng.uniform(-3, 3))})
            if source == "xrnfit-watch":
                # SpO2 through the night, stress through the day, a sample each half hour.
                oxygen = [[at(0, m), int(rng.uniform(93, 99))] for m in range(0, 7 * 60, 30) if at(0, m) <= now * 1000]
                calm = [[at(8, m), int(min(95, max(1, rng.gauss(32 if not weekend else 22, 14))))]
                        for m in range(0, 14 * 60, 30) if at(8, m) <= now * 1000]
                if oxygen:
                    add(source, {"id": f"{tag}o", "kind": "spo2", "samples": oxygen})
                if calm:
                    add(source, {"id": f"{tag}t", "kind": "stress", "samples": calm})
            bed = at(-1, int(rng.uniform(-40, 50)))  # the evening before
            stages, t = [], bed
            for cycle in range(5):
                for name, minutes in (("light", 45), ("deep", 25 if cycle < 3 else 5), ("rem", 15 + cycle * 5)):
                    length = int(minutes * rng.uniform(0.7, 1.3)) * 60_000
                    stages.append([t, t + length, name])
                    t += length
                if cycle == 2:
                    stages.append([t, t + 6 * 60_000, "awake"])
                    t += 6 * 60_000
            if t < now * 1000:
                add(source, {"id": f"{tag}z", "kind": "sleep", "start": bed, "end": t, "stages": stages})
        if back % 3 == 0:
            kg = round(74.5 - (120 - back) * 0.018 + rng.uniform(-0.4, 0.4), 2)
            if back > 60:
                add("import-renpho", {"id": f"w{back}", "kind": "weight", "start": at(7, 10), "value": kg})
            elif at(7, 10) < now * 1000:
                add("xrnfit-scale", {"id": f"w{back}", "kind": "weight", "start": at(7, 10), "value": kg,
                                     "impedance": int(500 + rng.uniform(-20, 20))})
    for (source, when), records in sorted(batches.items(), key=lambda item: item[0][1]):
        for i in range(0, len(records), health.MAX_RECORDS):
            health.write(log, support.USER, {"source": source, "upsert": records[i:i + health.MAX_RECORDS]}, when)


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("folder")
    p.add_argument("--port", type=int, default=15238)
    p.add_argument("--fixtures")
    args = p.parse_args()
    root = os.path.abspath(args.folder)
    os.makedirs(root, exist_ok=True)
    if os.listdir(root):
        raise SystemExit(f"{root} isn't empty: this builds a new made-up phone each time")
    now = int(time.time())
    rng = random.Random(7)
    phone = support.FakePhone(os.path.join(root, "phone"))
    stock_phone(phone, now, rng)
    stock_apps(phone.home, now)
    break_on_signal(phone, now)
    password = os.urandom(12).hex()
    with open(os.path.join(root, "password"), "w") as f:
        f.write(password)
    users = os.path.join(root, "users")
    with open(users, "w") as f:
        f.write(f"{support.USER}:{bcrypt.hashpw(password.encode(), bcrypt.gensalt(4)).decode()}\n")
    hub = xrnhub.Hub(os.path.join(root, "data"), users, phone.home, os.path.join(support.ROOT, "web"), machine=phone.machine())
    stock_history(hub.monitor.store, now, rng)
    stock_health(hub.log, now)
    stock_for_apps(phone.home, hub.log, now)
    xrnhub.Handler.hub = hub
    server = xrnhub.HubServer(("127.0.0.1", args.port), xrnhub.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    hub.monitor.minute_tick(now)
    hub.monitor.sweep(now)
    threading.Thread(target=hub.monitor.run, daemon=True).start()
    threading.Thread(target=xrnhub.background, args=(hub,), daemon=True).start()
    threading.Thread(target=keep_calling, args=(phone,), daemon=True).start()
    if args.fixtures:
        time.sleep(9)  # long enough for the calendars to load and a second sample to give rates
        write_fixtures(args.fixtures, args.port, password)
        return
    sys.stderr.write(f"ready on http://127.0.0.1:{args.port}/xrnhub as {support.USER}; password in {root}/password\n")
    threading.Event().wait()


if __name__ == "__main__":
    main()

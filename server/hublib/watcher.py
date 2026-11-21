"""The monitor's heartbeat: one thread that samples the phone and remembers what it saw.

A sample a minute goes to the database, all the time. While someone has the page open it also
samples every few seconds, for the live charts; with nobody looking, the phone isn't asked. Every
pass reads only new log lines. Reading is what this module does: the one exception is `restart`,
which someone has to ask for.
"""
import sys
import threading
import time
from collections import Counter, deque
from datetime import datetime, timedelta

from . import logscan
from .machine import BY_NAME, SERVICES
from .metrics import KEEP_MINUTES_S, RANGES, local_day

LIVE_S = 3
MINUTE_S = 60
VIEWER_S = 20  # how long after its last request a page still counts as open
WIFI_EVERY_S = 300
SWEEP_S = 600
ROLLUP_S = 3600
RING_SIZE = 240
LIVE_WINDOW_S = 600
FEED_SIZE = 150
FEED_MAX_AGE_S = 900
GAP_S = 180
REBOOT_DRIFT_S = 120
RESTART_DRIFT_S = 2
HEALTH_PATHS = {s.name: s.health for s in SERVICES if s.health}
DOWN_AFTER_S = 90  # a restart takes a few seconds, and that isn't an outage
RESTART_GAP_S = 60
MAX_NAMES_A_DAY = 2000  # paths and addresses: scanners make up as many as they like
OTHER_PATH = "(other)"
MAX_MESSAGES_A_DAY = 500  # warnings by what they say: a scanner's 404s are one each
OTHER_MESSAGE = "(other)"
COUNT_BEFORE_BYTES = 1024 * 1024  # how much of each log already read gets counted the first time
TIMELINE_DAYS = 31
LOCAL_ADDRESSES = ("127.0.0.1", "::1", "-")
# xrnfit has no service of its own: it sends the watch's and the scale's readings to the hub and reads
# them back from the personal API. Those requests are its traffic and its log lines, so they go under
# its name, and the Server page lists it among the services, kept by the hub.
FIT = "xrnfit"
HEALTH_ROUTES = ("/api/health", "/api/v1/health")
LEVELS = {"info": 0, "warn": 1, "error": 2}
# The same limits the laptop's daily check uses.
BATTERY_LOW = 30
HOT_C = 42
DISK_FULL = 0.9
BACKUP_STALE_S = 3 * 86400
RUNWAY_MIN_DAYS = 3
TRAFFIC_BLANK = ("reqs", "polls", "writes", "refused", "errors")


def rate(new, old, seconds):
    """Change per second in a counter that restarts from zero when its process does."""
    if new is None or old is None or seconds <= 0:
        return None
    return max(new - old, 0) / seconds


def grew(new, old):
    if new is None or old is None:
        return None
    return new - old if new >= old else new


class BadRestart(Exception):
    pass


class TooSoon(Exception):
    pass



def supervision_problems(seen):
    """What's wrong with runit, from Machine.supervision(): {(kind, target): detail}.

    - ("supervisor", "phone"): not exactly one runsvdir, so a service that stops stays stopped, or
      two try to run everything at once. Detail "" when there's none, or how many are running.
    - ("unsupervised", service): its runsv is missing or left over from an earlier runsvdir, or a
      copy of its program runs with no runsv over it (its parent is pid 1). That copy can hold the
      port while the supervised one fails to start, and a restart from here won't reach it.
    """
    wrong = {}
    roots = seen["runsvdir"]
    if len(roots) != 1:
        wrong[("supervisor", "phone")] = f"{len(roots)} running" if roots else ""
    for service in SERVICES:
        if not service.runit:
            continue
        parents = seen["runsv"].get(service.runit)
        if not parents and service.formerly:  # not moved to its new name yet
            parents = seen["runsv"].get(service.formerly.runit)
        if not parents:
            wrong[("unsupervised", service.name)] = "no runsv"
        elif len(roots) == 1 and roots[0] not in parents:
            wrong[("unsupervised", service.name)] = "runsv left over"
    for pid, parent, names in seen["programs"]:
        if parent != 1:
            continue
        for service in SERVICES:
            programs = (service.program, service.formerly.program if service.formerly else None)
            if any(p and p in names for p in programs):
                wrong[("unsupervised", service.name)] = f"pid {pid}"
    return wrong


def traffic_name(service, path):
    """Whose traffic a request is: the service's, except health on the hub, which is xrnfit's."""
    if service == "xrnhub":
        bare = path.removeprefix("/xrnhub")
        if any(bare == r or bare.startswith(r + "/") for r in HEALTH_ROUTES):
            return FIT
    return service


def line_owner(service, text):
    """Whose a log line (its text, after the stamp) is: a request's goes where its traffic does,
    anything else is the service's."""
    if service != "xrnhub":
        return service
    request = logscan.parse_request(text)
    return traffic_name(service, request["path"]) if request else service

class Watcher:
    def __init__(self, machine, store, inventory, clock=time.time):
        self.machine = machine
        self.store = store
        self.inventory = inventory
        self.clock = clock
        self.wake = threading.Event()
        self.ring = deque(maxlen=RING_SIZE)
        self.feed = deque(maxlen=FEED_SIZE)
        self.current = {}
        self.about = {}
        self.data = None
        self.last_viewer = 0
        self.last_live = 0
        self.last_minute = 0
        self.last_wifi = 0
        self.last_sweep = 0
        self.last_rollup = 0
        self.live_seen = {}
        self.minute_seen = {}
        self.live_net = None
        self.minute_net = None
        self.battery = None
        self.wifi = None
        self.tailnet = None
        self.funnel_paths = None
        self.answering = {}
        self.supervision = None  # runit's processes, read each minute
        self.failing = {}
        self.restarted = {}
        self.started = {}
        self.hour_traffic = {}
        self.followers = {
            s.name: logscan.Follower(machine.log_dir(s), *store.log_position(s.name))
            for s in SERVICES if s.name != "tailscale"  # its log is chatter about the network
        }
        self.lines = {name: logscan.Lines() for name in self.followers}
        self.new_traffic = {}
        self.new_paths = Counter()
        self.new_clients = {}
        self.new_logins = []
        self.new_log_counts = {}
        self.new_log_groups = {}
        self.names_day = ""
        self.names_seen = (set(), set())
        self.messages_day = ""
        self.messages_seen = set()

    # ---- reading ---------------------------------------------------------------------------------

    def read_services(self, now, seen):
        """A row per service, each with its changes since the last call that used `seen`."""
        rows = []
        for service in SERVICES:
            state = self.machine.service(service)
            pid = state["pid"]
            process = self.machine.process(pid) if pid and state["state"] == "run" else None
            row = {
                "name": service.name, "state": state["state"], "since": state["since"],
                "port": service.port, "restartable": service.restartable,
            }
            if process:
                before = seen.get(service.name)
                same = before if before and before["pid"] == pid else None
                seconds = now - same["at"] if same else 0
                cpu = rate(process["cpuS"], same["cpuS"] if same else None, seconds)
                row.update(
                    rss=process["rss"], threads=process["threads"], cpuTotal=process["cpuS"],
                    cpu=round(cpu * 100, 2) if cpu is not None else None,
                    read=process.get("rchar"), written=process.get("wchar"), diskWritten=process.get("write_bytes"),
                    readNew=grew(process.get("rchar"), same.get("rchar") if same else None),
                    writtenNew=grew(process.get("wchar"), same.get("wchar") if same else None),
                    diskNew=grew(process.get("write_bytes"), same.get("write_bytes") if same else None),
                )
                seen[service.name] = {"pid": pid, "at": now, **process}
            else:
                seen.pop(service.name, None)
            rows.append(row)
        return rows

    def read_gauges(self):
        m = self.machine
        return {"system": m.system(), "memory": m.memory(), "temps": m.temps(), "cores": m.cores(), "disk": m.disk()}

    def read_logs(self, now):
        for name, follower in self.followers.items():
            # A renamed service's log moves to a folder of its own name when its deploy lands; the
            # new folder's lines are read from its first.
            follower.folder = self.machine.log_dir(BY_NAME[name])
            lines = self.lines[name]
            for raw in follower.read():
                parsed = logscan.parse_line(raw)
                if parsed:
                    self.take_line(name, parsed[0], parsed[1], now)
                    self.count_lines(name, lines.feed(*parsed))
            self.count_lines(name, lines.flush(int(now * 1000)))

    def take_line(self, name, at_ms, text, now):
        request = logscan.parse_request(text)
        if request:
            # The monitor's own check, once a minute: not traffic, so it isn't counted as any.
            if request["path"] == HEALTH_PATHS.get(name) and request["ip"] in LOCAL_ADDRESSES:
                return
            self.count_request(traffic_name(name, request["path"]), at_ms, request, now)
            return
        login = logscan.parse_login(text, name)
        if login:
            self.new_logins.append((at_ms, name, int(login[0]), login[1][:60], login[2][:60]))

    def count_lines(self, name, entries):
        """What [name]'s log said, from logscan.Lines: routine requests are Traffic's to count."""
        for at_ms, text, level, routine in entries:
            if not routine:
                self.count_line(line_owner(name, text), at_ms, text, level)

    def count_line(self, name, at_ms, text, level):
        """A line by its level, and a warning or an error by what it says too."""
        at = at_ms // 1000
        key = (at // MINUTE_S * MINUTE_S, name, level)
        self.new_log_counts[key] = self.new_log_counts.get(key, 0) + 1
        if level == "info":
            return
        day = local_day(at)
        if day != self.messages_day:
            self.messages_day, self.messages_seen = day, set()
        said = logscan.signature(text)
        if said not in self.messages_seen:
            if len(self.messages_seen) < MAX_MESSAGES_A_DAY:
                self.messages_seen.add(said)
            else:
                said = OTHER_MESSAGE
        group = self.new_log_groups.setdefault((at // 3600 * 3600, name, level, said), [0, 0, ""])
        group[0] += 1
        if at_ms >= group[1]:
            group[1], group[2] = at_ms, logscan.clean(text)

    def count_what_was_read(self):
        """Once, the first time this monitor counts lines: the lines it had already read before it
        did, up to a limit, so the Logs page starts with some history."""
        if self.store.meta("log_counts_from") is not None:
            return
        now = self.clock()
        for name, follower in self.followers.items():
            lines = logscan.Lines()
            for raw in logscan.before(follower.folder, follower.inode, follower.offset, COUNT_BEFORE_BYTES):
                parsed = logscan.parse_line(raw)
                if parsed:
                    self.count_lines(name, lines.feed(*parsed))
            self.count_lines(name, lines.flush())
        self.store.set_meta("log_counts_from", int(now))

    def count_request(self, name, at_ms, request, now):
        at = at_ms // 1000
        day = local_day(at)
        if day != self.names_day:
            self.names_day = day
            self.names_seen = (set(), set())
        status = request["status"]
        tally = self.new_traffic.setdefault((at // MINUTE_S * MINUTE_S, name), dict.fromkeys(TRAFFIC_BLANK, 0))
        tally["reqs"] += 1
        tally["polls"] += request["poll"]
        tally["writes"] += request["write"]
        tally["refused"] += 400 <= status < 500
        tally["errors"] += status >= 500
        paths, clients = self.names_seen
        path = request["path"]
        if path not in paths:
            if len(paths) < MAX_NAMES_A_DAY:
                paths.add(path)
            else:
                path = OTHER_PATH
        self.new_paths[(day, name, path, status)] += 1
        ip = request["ip"]
        if ip not in LOCAL_ADDRESSES:
            if ip in clients or len(clients) < MAX_NAMES_A_DAY:
                clients.add(ip)
                counts = self.new_clients.setdefault((day, ip), [0, 0])
                counts[0] += 1
                counts[1] += 400 <= status < 500
        if now - at <= FEED_MAX_AGE_S:
            self.feed.append({
                "at": at_ms, "service": name, "method": request["method"], "path": request["path"],
                "status": status, "ip": ip, "poll": request["poll"],
            })

    def flush_logs(self):
        """What the logs said goes to the database with how far they've been read, so a restart
        picks up from the same line."""
        store = self.store
        if self.new_traffic:
            store.add_traffic(self.new_traffic)
        if self.new_paths:
            store.add_paths(self.new_paths)
        if self.new_clients:
            store.add_clients(self.new_clients)
        if self.new_logins:
            store.add_logins(self.new_logins)
        if self.new_log_counts:
            store.add_log_counts(self.new_log_counts)
        if self.new_log_groups:
            store.add_log_groups(self.new_log_groups)
        store.set_log_positions([(n, f.inode, f.offset) for n, f in self.followers.items()])
        self.new_traffic, self.new_paths, self.new_clients, self.new_logins = {}, Counter(), {}, []
        self.new_log_counts, self.new_log_groups = {}, {}

    # ---- the passes ------------------------------------------------------------------------------

    def sample(self, now):
        """The quick pass: gauges, services and new log lines, with one point for the live charts."""
        gauges = self.read_gauges()
        services = self.read_services(now, self.live_seen)
        for row in services:
            row["answers"] = self.answering.get(row["name"], True)
        network = self.machine.network()
        seconds = now - self.last_live if self.last_live else 0
        speed = {}
        if network and self.live_net and seconds > 0:
            speed = {"in": rate(network["in"], self.live_net["in"], seconds), "out": rate(network["out"], self.live_net["out"], seconds)}
        self.live_net = network or self.live_net
        self.last_live = now
        self.read_logs(now)
        system, memory, temps, cores = gauges["system"], gauges["memory"], gauges["temps"], gauges["cores"]
        cpu = [r["cpu"] for r in services if r.get("cpu") is not None]
        point = {
            "at": int(now * 1000),
            "load": system["load"][0] if system else None,
            "speed": cores["speed"] if cores else None,
            "cpu": round(sum(cpu), 2) if cpu else None,
            "memUsed": memory["total"] - memory["available"] if memory else None,
            "cpuTemp": temps["cpu"] if temps else None,
            "battTemp": temps["battery"] if temps else None,
            "netIn": speed.get("in"),
            "netOut": speed.get("out"),
        }
        self.ring.append(point)
        self.current = {
            **gauges, "services": services, "network": network, "speed": speed, "battery": self.battery,
            "wifi": self.wifi, "tailnet": self.tailnet, "hour": self.hour_traffic, "at": point["at"],
        }
        return point

    def minute_tick(self, now):
        at = int(now) // MINUTE_S * MINUTE_S
        if not self.about:
            self.about = self.machine.about()
        self.battery = self.machine.battery() or self.battery
        if now - self.last_wifi >= WIFI_EVERY_S:
            self.wifi = self.machine.wifi()
            self.tailnet = self.machine.tailnet()
            self.last_wifi = now
        self.answering = {s.name: self.machine.answers(s) for s in SERVICES if s.port}
        self.supervision = self.machine.supervision()
        self.count_what_was_read()
        point = self.sample(now)
        self.flush_logs()
        self.hour_traffic = self.store.totals(3600, at)
        self.current["hour"] = self.hour_traffic
        system = self.current["system"]
        self.note_gaps(now, system)
        services = self.read_services(now, self.minute_seen)
        self.note_restarts(services)
        self.store.add_minute(at, self.minute_row(point, services), {
            r["name"]: {
                "up": int(r["state"] == "run" and self.answering.get(r["name"], True)),
                "cpu": r.get("cpu"), "rss": r.get("rss"), "read_b": r.get("readNew"),
                "written_b": r.get("writtenNew"), "disk_w": r.get("diskNew"),
            } for r in services
        })
        self.check_problems(now, services)
        self.last_minute = now

    def minute_row(self, point, services):
        current = self.current
        memory, disk, battery, wifi = current["memory"], current["disk"], self.battery, self.wifi
        network = current["network"]
        before = self.minute_net
        cpu = [r["cpu"] for r in services if r.get("cpu") is not None]
        row = {
            "load1": point["load"], "cpu": round(sum(cpu), 2) if cpu else None, "speed": point["speed"],
            "mem_used": point["memUsed"],
            "swap_used": memory["swapTotal"] - memory["swapFree"] if memory else None,
            "cpu_temp": point["cpuTemp"],
            "batt_temp": battery["temp"] if battery and battery.get("temp") is not None else point["battTemp"],
            "battery": battery["percent"] if battery else None,
            "rssi": wifi["rssi"] if wifi else None,
            "disk_used": disk["used"] if disk else None,
        }
        if network and before:
            row.update(
                net_in=grew(network["in"], before["in"]), net_out=grew(network["out"], before["out"]),
                net_relayed=grew(network["relayed"], before["relayed"]),
            )
        self.minute_net = network or before
        return row

    def sweep(self, now):
        doc = self.inventory.sweep(now)
        self.data = doc
        self.store.set_data_day(local_day(now), {a["name"]: a for a in doc["apps"]})
        self.last_sweep = now
        self.funnel_paths = self.machine.funnel()

    def step(self):
        now = self.clock()
        if int(now) // MINUTE_S != int(self.last_minute) // MINUTE_S:
            self.minute_tick(now)
        elif now - self.last_viewer < VIEWER_S and now - self.last_live >= LIVE_S:
            self.sample(now)
        if now - self.last_sweep >= SWEEP_S:
            self.sweep(now)
        if now - self.last_rollup >= ROLLUP_S:
            self.store.roll_up(int(now))
            self.last_rollup = now

    def run(self):
        while True:
            try:
                self.step()
            except Exception as e:  # noqa: BLE001 - a bad file must not end the loop
                sys.stderr.write(f"monitor: {type(e).__name__}: {e}\n")
            now = self.clock()
            quick = now - self.last_viewer < VIEWER_S
            self.wake.wait(min(LIVE_S if quick else MINUTE_S, MINUTE_S - now % MINUTE_S + 0.05))
            self.wake.clear()

    # ---- what went wrong -------------------------------------------------------------------------

    def note_gaps(self, now, system):
        store = self.store
        last = store.last_sample()
        if last and now - last > GAP_S:
            store.note_incident("gap", "monitor", last, ended=int(now))
        if not system:
            return
        boot = int(now - system["uptime"])
        saved = store.meta("boot")
        if saved and abs(boot - saved) > REBOOT_DRIFT_S:
            store.note_incident("reboot", "phone", boot)
        if not saved or abs(boot - saved) > REBOOT_DRIFT_S:
            store.set_meta("boot", boot)

    def note_restarts(self, services):
        for row in services:
            since = int(row["since"] or 0)
            if not since:
                continue
            key = f"since:{row['name']}"
            saved = self.started.get(key)
            if saved is None:
                saved = self.store.meta(key)
            if saved and abs(since - saved) > RESTART_DRIFT_S:
                self.store.note_incident("restart", row["name"], since)
            if saved is None or abs(since - saved) > RESTART_DRIFT_S:
                self.store.set_meta(key, since)
                saved = since
            self.started[key] = saved

    def conditions(self, now, services):
        """({(kind, target): detail} wrong right now, the kinds nothing could be learned about)."""
        wrong = {}
        unknown = set()
        for row in services:
            name = row["name"]
            if row["state"] == "run" and self.answering.get(name, True):
                self.failing.pop(name, None)
            elif now - self.failing.setdefault(name, now) >= DOWN_AFTER_S or row["state"] == "down":
                wrong[("down", name)] = ""
        battery = self.battery
        if battery:
            if not battery["plugged"]:
                wrong[("unplugged", "phone")] = f"{battery['percent']}%"
            if battery["percent"] < BATTERY_LOW:
                wrong[("battery", "phone")] = f"{battery['percent']}%"
            if (battery.get("temp") or 0) > HOT_C:
                wrong[("hot", "phone")] = f"{battery['temp']:.0f} °C"
        else:
            unknown |= {"unplugged", "battery", "hot"}
        disk = self.current.get("disk")
        if disk:
            if disk["used"] / disk["total"] > DISK_FULL:
                wrong[("disk", "phone")] = f"{disk['free'] // 2**30} GB left"
        else:
            unknown.add("disk")
        backup = self.data["backup"] if self.data else None
        if backup:
            if now - backup > BACKUP_STALE_S:
                wrong[("backup", "laptop")] = f"{int((now - backup) // 86400)} days ago"
        else:
            unknown.add("backup")
        tailnet = self.tailnet
        if tailnet:
            if tailnet["state"] != "Running":
                wrong[("tailscale", "network")] = tailnet["state"]
        else:
            unknown.add("tailscale")
        if self.supervision is None:
            unknown |= {"supervisor", "unsupervised"}
        else:
            wrong.update(supervision_problems(self.supervision))
        return wrong, unknown

    def check_problems(self, now, services):
        wrong, unknown = self.conditions(now, services)
        open_now = self.store.open_incidents()
        for key, detail in wrong.items():
            if key not in open_now:
                started = self.failing.get(key[1], now) if key[0] == "down" else now
                self.store.open_incident(key[0], key[1], int(started), detail)
        for key, row in open_now.items():
            if key not in wrong and key[0] not in unknown:
                self.store.close_incident(row, int(now))

    def restart(self, name, now=None):
        """Restarts a service someone picked. (worked, what `sv` said)."""
        now = now or self.clock()
        service = BY_NAME.get(name) if isinstance(name, str) else None
        if not service or not service.restartable:
            raise BadRestart(name)
        if now - self.restarted.get(name, 0) < RESTART_GAP_S:
            raise TooSoon(name)
        self.restarted[name] = now
        worked, said = self.machine.restart(service)
        self.store.note_incident("restarted", name, int(now), "by hand" if worked else "failed")
        self.wake.set()
        return worked, said

    # ---- what the pages ask for ------------------------------------------------------------------

    def viewer(self):
        now = self.clock()
        quiet = now - self.last_viewer >= VIEWER_S
        self.last_viewer = now
        if quiet:
            self.wake.set()

    def now_doc(self, since_ms, feed=True):
        self.viewer()
        return {
            **self.current, "about": self.about,
            "problems": self.store.incidents(limit=200, open_only=True),
            "samples": self.points_since(since_ms),
            "feed": list(self.feed) if feed else None,
        }

    def points_since(self, since_ms):
        # Copied first, in one step: the monitor thread appends while a page asks, and a deque
        # changed under a loop over it raises.
        points = [p for p in list(self.ring) if p["at"] > since_ms]
        if since_ms:
            return points
        now = self.clock()
        first = points[0]["at"] / 1000 if points else now
        # A minute's row carries that minute's sample, which the ring may hold too.
        return self.store.recent_points(int(now - LIVE_WINDOW_S), int(first) - MINUTE_S + 1) + points

    def history_doc(self, range_name, service):
        return self.store.history(range_name, int(self.clock()), service)

    def services_doc(self):
        now = int(self.clock())
        return {
            "uptime": self.store.uptime_days(now),
            "incidents": self.store.incidents(),
            "fitDays": self.fit_days(now),
            # Everything over the strips' days, for the timeline: `incidents` is only the latest.
            "timeline": self.store.incidents(limit=2000, since=now - TIMELINE_DAYS * 86400),
        }

    def fit_days(self, now, days=30):
        """xrnfit's row in place of an uptime strip: how many readings came in on each of the last
        [days], oldest first, from the inventory's last sweep. None before the first day anything
        came in, and for a day that sweep didn't cover, as an uptime strip has for a day not watched."""
        fed = (self.data or {}).get("fed") or {}
        counted = dict(zip(fed.get("days") or [], (fed.get("apps") or {}).get(FIT) or []))
        today = datetime.fromtimestamp(now).date()
        wanted = [(today - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]
        found = [counted.get(day) for day in wanted]
        first = next((i for i, n in enumerate(found) if n), len(found))
        return [None] * first + found[first:]

    def traffic_doc(self, range_name):
        now = int(self.clock())
        seconds = RANGES[range_name][0]
        first = local_day(now - seconds)
        return {
            "range": range_name,
            "totals": self.store.totals(seconds, now),
            "paths": self.store.top_paths(first),
            "clients": self.store.top_clients(first),
            "status": self.store.status_mix(first),
        }

    def data_doc(self):
        now = int(self.clock())
        doc = self.data
        if not doc:
            return None
        today = datetime.fromtimestamp(now).date()
        store = self.store
        days = [(today - timedelta(days=i)).isoformat() for i in range(29, -1, -1)]
        sizes = {}
        records = {}
        for day, app, size, count in store.data_history(days[0]):
            sizes.setdefault(app, {})[day] = size
            records.setdefault(app, {})[day] = count
        traffic = store.daily_traffic(30, now)
        network = store.daily_network(30, now)
        kept = sorted({d for per in sizes.values() for d in per})
        week_ago = (today - timedelta(days=7)).isoformat()
        apps = []
        for app in doc["apps"]:
            past = sizes.get(app["name"], {})
            start = next((d for d in kept if d >= week_ago and d in past), None)
            apps.append({
                **app,
                "grew": app["bytes"] - past[start] if start and start != today.isoformat() else None,
                "grewSince": start,
                "added": app["records"] - records[app["name"]][start] if start and start != today.isoformat() else None,
            })
        return {
            **doc, "apps": apps,
            "growth": {"days": kept, "apps": {name: [per.get(d) for d in kept] for name, per in sizes.items()}},
            "daily": {
                "days": days,
                "writes": {
                    name: [traffic.get(d, {}).get(name, {}).get("writes", 0) for d in days]
                    for name in sorted({n for per in traffic.values() for n in per})
                },
                "in": [network.get(d, [None, None])[0] for d in days],
                "out": [network.get(d, [None, None])[1] for d in days],
            },
            "disk": self.current.get("disk"),
            "network": self.current.get("network"),
            "services": [
                {k: r.get(k) for k in ("name", "read", "written", "diskWritten", "since")}
                for r in self.current.get("services", []) if r.get("read") is not None
            ],
            "runway": self.runway(kept, sizes),
            "monitorBytes": store.size(),
            "keepMinutesDays": KEEP_MINUTES_S // 86400,
        }

    def runway(self, days, sizes):
        """Days until the disk is full at the rate your data has been growing, or None until
        there's enough history to say."""
        disk = self.current.get("disk")
        if len(days) < RUNWAY_MIN_DAYS or not disk:
            return None
        total = [sum(per.get(d) or 0 for per in sizes.values()) for d in (days[0], days[-1])]
        per_day = (total[1] - total[0]) / (len(days) - 1)
        return {"perDay": per_day, "days": disk["free"] / per_day if per_day > 0 else None}

    def security_doc(self):
        now = int(self.clock())
        week = local_day(now - 7 * 86400)
        ms = now * 1000
        store = self.store
        return {
            "day": store.login_counts(ms - 86400000),
            "week": store.login_counts(ms - 7 * 86400000),
            "logins": store.login_rows(ms - 30 * 86400000),
            "refused": store.top_paths(week, refused=True),
            "status": store.status_mix(week),
            "clients": store.top_clients(week, refused=True),
            "tailnet": self.tailnet,
            "funnel": self.funnel_paths,
        }

    def log_stats(self, range_name, only):
        """The Logs page's figures: lines by level and service over time, and the warnings and
        errors by what they say, newest log lines aside."""
        now = int(self.clock())
        doc = self.store.log_history(range_name, now, only)
        doc["groups"] = self.store.log_groups(now - RANGES[range_name][0], only)
        return doc

    def log_lines(self, only, level, text, limit, routine=True, said=""):
        """The newest lines, newest first, a traceback as its one exception line. Without `routine`,
        routine requests (logscan.Lines) are left out; with `said`, only lines with that signature
        are kept."""
        wanted = LEVELS.get(level, 0)
        needle = text.lower()
        found = []
        # xrnfit's lines are in the hub's log.
        reads = "xrnhub" if only == FIT else only
        for service in SERVICES:
            if reads and service.name != reads:
                continue
            lines = logscan.Lines()
            entries = [e for at, line in logscan.tail(self.machine.log_dir(service)) for e in lines.feed(at, line)]
            for at, line, rank, usual in entries + lines.flush():
                owner = line_owner(service.name, line)
                if only and owner != only:
                    continue
                if usual and not routine:
                    continue
                if said and logscan.signature(line) != said:
                    continue
                shown = logscan.clean(line)
                if LEVELS[rank] >= wanted and (not needle or needle in shown.lower()):
                    found.append({"at": at, "service": owner, "level": rank, "text": shown})
        found.sort(key=lambda row: row["at"], reverse=True)
        return found[:limit]

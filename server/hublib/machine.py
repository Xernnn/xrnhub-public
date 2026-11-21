"""What the phone will say about itself.

Android shuts most of /proc to apps: there's no overall CPU figure, no network counters and no view
of other apps' processes. What's left still adds up to a good picture, and this module is the only
file that knows where it all is:

    sysinfo(2)              uptime, load averages, process count
    /proc/meminfo           memory
    /proc/<pid>/stat, io    CPU, memory and bytes for our own services, which share one Linux user
    /sys/class/thermal      temperatures
    /sys/.../cpufreq        how fast each core is running
    runit's supervise/      whether each service is up, and since when
    tailscaled's socket     every byte in and out, the devices, the public paths
    termux-*                battery and Wi-Fi

A reader that gets no answer returns None, so the page shows less and nothing falls over, whatever
the Android version.
"""
import ctypes
import glob
import http.client
import json
import os
import re
import shutil
import socket
import subprocess
from collections import namedtuple

# `program` is what its process runs, by file name, so a copy no supervisor looks after can be found.
# `formerly` is its runit service, folder and program as they were named before a rename.
Service = namedtuple("Service", "name runit port health folder restartable program formerly", defaults=(None, None))
Former = namedtuple("Former", "runit folder program")

# The hub can't restart itself mid-answer, and restarting Tailscale or ssh could cut you off from
# the phone with no way back but walking over to it.
SERVICES = (
    Service("calendar", "radicale", 5232, "/.web/", "radicale", True, "radicale"),
    Service("xrntube", "xrntube-sync", 5233, "/health", "xrntube-sync", True, "xrntube-sync.py"),
    Service("xrnloops", "xrnloops-sync", 5234, "/health", "xrnloops-sync", True, "xrnloops-sync.py"),
    Service("xrnrule", "xrnrule-sync", 5235, "/health", "xrnrule-sync", True, "xrnrule-sync.py"),
    Service("xrnhome", "xrnhome-sync", 5236, "/health", "xrnhome-sync", True, "xrnhome-sync.py"),
    Service("xrnwal", "xrnwal-sync", 5237, "/health", "xrnwal-sync", True, "xrnwal-sync.py",
            Former("xrnwallet-sync", "xrnwallet-sync", "xrnwallet-sync.py")),
    Service("xrnhub", "xrnhub", 5238, "/health", "xrnhub", False, "xrnhub.py"),
    Service("xrninsta", "xrninsta", 5239, "/health", "xrninsta", True, "xrninsta.py"),
    Service("xrnphotos", "xrnphotos-sync", 5242, "/health", "xrnphotos-sync", True, "xrnphotos-sync.py"),
    Service("tailscale", "tailscaled", None, None, None, False, "tailscaled"),
    Service("ssh", "sshd", None, None, None, False, "sshd"),
)
BY_NAME = {s.name: s for s in SERVICES}
FORMER_FOLDERS = {s.folder: s.formerly.folder for s in SERVICES if s.formerly}
COMMAND_TIMEOUT_S = 8
RESTART_TIMEOUT_S = 15
SOCKET_TIMEOUT_S = 2
HEALTH_TIMEOUT_S = 3  # a service that takes longer than this to say "ok" isn't answering
MAX_ANSWER_BYTES = 1 << 20  # more than any health answer; the rest is left unread
HOTTEST_REAL_C = 120  # a few sensors read nonsense when they're asleep
METRIC = re.compile(r'^tailscaled_(inbound|outbound)_bytes_total\{path="([a-z0-9_]+)"\} (\d+)')
FUNNEL_PATH = re.compile(r"\|-- (\S+)\s+proxy\s+\S+")


class _SysInfo(ctypes.Structure):
    _fields_ = [
        ("uptime", ctypes.c_long),
        ("loads", ctypes.c_ulong * 3),
        ("totalram", ctypes.c_ulong),
        ("freeram", ctypes.c_ulong),
        ("sharedram", ctypes.c_ulong),
        ("bufferram", ctypes.c_ulong),
        ("totalswap", ctypes.c_ulong),
        ("freeswap", ctypes.c_ulong),
        ("procs", ctypes.c_ushort),
        ("pad", ctypes.c_ushort),
        ("totalhigh", ctypes.c_ulong),
        ("freehigh", ctypes.c_ulong),
        ("mem_unit", ctypes.c_uint),
        ("reserved", ctypes.c_char * 256),
    ]


class _UnixHTTP(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__("local-tailscaled.sock", timeout=SOCKET_TIMEOUT_S)
        self.socket_path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.socket_path)


def answers(port, path, timeout):
    """Whether a service on this phone replies to a GET within `timeout` seconds with anything short
    of a server error. A login wall is an answer."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        # Asking for the connection to close, and reading the answer before hanging up, lets the
        # service end it cleanly. Hanging up on an unread answer resets the connection, and the
        # service logs that as an error.
        conn.request("GET", path, headers={"Connection": "close"})
        response = conn.getresponse()
        up = response.status < 500
        try:
            response.read(MAX_ANSWER_BYTES)
        except (OSError, http.client.HTTPException):
            pass
        return up
    except (OSError, http.client.HTTPException):
        return False
    finally:
        conn.close()


def settled(path, old):
    """[path], or [old] while only that one is there. A renamed service moves its folders in its
    own deploy, which can land before the hub's or after it."""
    if old is not None and not os.path.exists(path) and os.path.exists(old):
        return old
    return path


def home_folder(home, folder):
    """Where a service's folder is under [home]: ~/<folder>, or its old name until it moves."""
    old = FORMER_FOLDERS.get(folder)
    return settled(os.path.join(home, folder), os.path.join(home, old) if old else None)


def read_text(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def run_command(args, timeout=COMMAND_TIMEOUT_S):
    """(exit code, output); (None, "") when it can't run at all."""
    if not shutil.which(args[0]):
        return None, ""
    try:
        done = subprocess.run(args, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None, ""
    return done.returncode, done.stdout


def plausible_temp(raw):
    """Celsius from a sensor file, which may hold millidegrees; None if it can't be real."""
    try:
        value = int(raw.strip())
    except ValueError:
        return None
    celsius = value / 1000 if abs(value) >= 1000 else float(value)
    return celsius if 0 < celsius < HOTTEST_REAL_C else None


class Machine:
    def __init__(self, home, prefix, proc="/proc", sys_dir="/sys", run=run_command, local_api=None):
        self.home = home
        self.prefix = prefix
        self.proc = proc
        self.sys_dir = sys_dir
        self.run = run
        self.local_api = local_api or self.ask_tailscaled
        self.socket_path = os.path.join(prefix, "var", "run", "tailscale", "tailscaled.sock")
        self.tick_hz = os.sysconf("SC_CLK_TCK")
        self.page_size = os.sysconf("SC_PAGE_SIZE")
        self.cpu_zones = None
        self.battery_zone = None
        self.max_khz = {}

    # ---- the whole phone -------------------------------------------------------------------------

    def system(self):
        info = _SysInfo()
        try:
            if ctypes.CDLL(None).sysinfo(ctypes.byref(info)) != 0:
                return None
        except (OSError, AttributeError):
            return None
        return {
            "uptime": info.uptime,
            "load": [round(value / 65536, 2) for value in info.loads],
            "procs": info.procs,
        }

    def memory(self):
        text = read_text(os.path.join(self.proc, "meminfo"))
        if not text:
            return None
        fields = {}
        for line in text.splitlines():
            key, _, rest = line.partition(":")
            parts = rest.split()
            if parts and parts[0].isdigit():
                fields[key] = int(parts[0]) * 1024
        if "MemTotal" not in fields:
            return None
        return {
            "total": fields["MemTotal"],
            "available": fields.get("MemAvailable", fields.get("MemFree", 0)),
            "swapTotal": fields.get("SwapTotal", 0),
            "swapFree": fields.get("SwapFree", 0),
        }

    def find_zones(self):
        self.cpu_zones = []
        for folder in glob.glob(os.path.join(self.sys_dir, "class", "thermal", "thermal_zone*")):
            kind = (read_text(os.path.join(folder, "type")) or "").strip().lower()
            path = os.path.join(folder, "temp")
            if kind == "battery":
                self.battery_zone = path
            elif kind.startswith("cpu") or "pkg" in kind:
                self.cpu_zones.append(path)

    def temps(self):
        """The hottest CPU sensor and the battery. There are dozens of zones, so they're looked
        for once and only those two kinds read after that."""
        if self.cpu_zones is None:
            self.find_zones()
        cpu = [plausible_temp(read_text(p) or "") for p in self.cpu_zones]
        cpu = [c for c in cpu if c is not None]
        battery = plausible_temp(read_text(self.battery_zone) or "") if self.battery_zone else None
        if not cpu and battery is None:
            return None
        return {"cpu": max(cpu) if cpu else None, "battery": battery}

    def cores(self):
        """Each core's speed in MHz, and how much of the phone's top speed is in use: with no CPU
        total to read, the speed the kernel chose is the next best sign of how hard it's working."""
        now, top = [], []
        for path in sorted(glob.glob(os.path.join(self.sys_dir, "devices", "system", "cpu", "cpu[0-9]*"))):
            current = read_text(os.path.join(path, "cpufreq", "scaling_cur_freq"))
            if path not in self.max_khz:
                self.max_khz[path] = read_text(os.path.join(path, "cpufreq", "cpuinfo_max_freq"))
            if current and current.strip().isdigit() and (self.max_khz[path] or "").strip().isdigit():
                now.append(int(current) // 1000)
                top.append(int(self.max_khz[path]) // 1000)
        if not now:
            return None
        return {"mhz": now, "maxMhz": top, "speed": round(sum(now) / sum(top), 3)}

    def disk(self):
        try:
            st = os.statvfs(self.home)
        except OSError:
            return None
        total = st.f_blocks * st.f_frsize
        free = st.f_bavail * st.f_frsize
        return {"total": total, "free": free, "used": total - free}

    # ---- services --------------------------------------------------------------------------------

    def runit_path(self, service, *where):
        """A runit folder of [service] under $PREFIX/<where>, by its old name until it moves."""
        top = os.path.join(self.prefix, *where)
        old = os.path.join(top, service.formerly.runit) if service.formerly else None
        return settled(os.path.join(top, service.runit), old)

    def service_dir(self, service):
        return self.runit_path(service, "var", "service")

    def log_dir(self, service):
        return self.runit_path(service, "var", "log", "sv")

    def service(self, service):
        """runit's view: `state` is "run", "down" or "missing"."""
        supervise = os.path.join(self.service_dir(service), "supervise")
        state = (read_text(os.path.join(supervise, "stat")) or "").replace(",", " ").split()
        if not state:
            return {"state": "missing", "pid": 0, "since": None}
        pid_file = os.path.join(supervise, "pid")
        pid = (read_text(pid_file) or "").strip()
        try:
            since = os.stat(pid_file if pid else os.path.join(supervise, "stat")).st_mtime
        except OSError:
            since = None
        return {"state": state[0], "pid": int(pid) if pid.isdigit() else 0, "since": since}

    def process(self, pid):
        """Counters for one of our own processes, or None once it's gone."""
        stat = read_text(os.path.join(self.proc, str(pid), "stat"))
        if not stat or ")" not in stat:
            return None
        fields = stat[stat.rindex(")") + 2:].split()
        try:
            found = {
                "cpuS": (int(fields[11]) + int(fields[12])) / self.tick_hz,
                "threads": int(fields[17]),
                "rss": int(fields[21]) * self.page_size,
            }
        except (IndexError, ValueError):
            return None
        for line in (read_text(os.path.join(self.proc, str(pid), "io")) or "").splitlines():
            key, _, value = line.partition(":")
            if key in ("rchar", "wchar", "write_bytes") and value.strip().isdigit():
                found[key] = int(value)
        return found

    def answers(self, service):
        """Whether it answers a request. Only an answer counts: on 2026-11-15 two services left
        behind by a killed supervisor still held their ports and took connections, but never
        replied, and a check that only connected called them up for half an hour while the
        public address gave 502. Any answer short of a server error counts, a login wall
        included. It leaves a line in the service's log, which the log reader counts as a poll."""
        return answers(service.port, service.health or "/", HEALTH_TIMEOUT_S)

    def supervision(self):
        """runit as the process list shows it, or None if the list can't be read:
        {"runsvdir": [pids], "runsv": {service dir name: [parents]}, "programs": [(pid, parent,
        file names of its first arguments)]}. Android's process trim once killed runsvdir and
        some runsv, and left their programs running with nobody to restart or stop them."""
        try:
            pids = [int(e) for e in os.listdir(self.proc) if e.isdigit()]
        except OSError:
            return None
        runsvdir, runsv, programs = [], {}, []
        for pid in pids:
            try:
                with open(os.path.join(self.proc, str(pid), "cmdline"), "rb") as f:
                    argv = [a.decode("utf-8", "replace") for a in f.read().split(b"\0") if a]
            except OSError:
                continue
            stat = read_text(os.path.join(self.proc, str(pid), "stat")) or ""
            try:
                parent = int(stat[stat.rindex(")") + 2:].split()[1])
            except (ValueError, IndexError):
                continue
            if not argv:  # a zombie, or a kernel thread
                continue
            first = os.path.basename(argv[0])
            if first == "runsvdir":
                runsvdir.append(pid)
            elif first == "runsv" and len(argv) > 1:
                runsv.setdefault(argv[1], []).append(parent)
            else:
                programs.append((pid, parent, [os.path.basename(a) for a in argv[:3]]))
        return {"runsvdir": sorted(runsvdir), "runsv": runsv, "programs": programs}

    def restart(self, service):
        """(worked, what `sv` said). Touches no data; only runit's supervision of the process."""
        code, output = self.run(["sv", "restart", self.service_dir(service)], timeout=RESTART_TIMEOUT_S)
        return code == 0, output.strip()[:200]

    # ---- network ---------------------------------------------------------------------------------

    def ask_tailscaled(self, path):
        conn = _UnixHTTP(self.socket_path)
        try:
            conn.request("GET", path, headers={"Sec-Tailscale": "localapi"})
            res = conn.getresponse()
            return res.read().decode("utf-8", "replace") if res.status == 200 else None
        except OSError:
            return None
        finally:
            conn.close()

    def network(self):
        """Bytes through Tailscale since it started, which is all the phone's public traffic, and
        how much of it went through a relay instead of directly."""
        text = self.local_api("/localapi/v0/usermetrics")
        if not text:
            return None
        found = {"in": 0, "out": 0, "relayed": 0}
        seen = False
        for line in text.splitlines():
            match = METRIC.match(line)
            if match:
                seen = True
                value = int(match[3])
                found["in" if match[1] == "inbound" else "out"] += value
                if match[2] == "derp":
                    found["relayed"] += value
        return found if seen else None

    def tailnet(self):
        """Your devices, and the state of Tailscale. Funnel's ingress nodes are peers too, two dozen
        of them, and they're only counted."""
        text = self.local_api("/localapi/v0/status")
        try:
            doc = json.loads(text) if text else None
        except ValueError:
            doc = None
        if not isinstance(doc, dict):
            return None
        me = doc.get("Self") or {}
        devices = []
        ingress = 0
        for peer in (doc.get("Peer") or {}).values():
            name = str(peer.get("HostName") or "")
            if name.startswith("funnel-ingress"):
                ingress += 1
                continue
            devices.append({
                "name": name,
                "os": str(peer.get("OS") or ""),
                "online": bool(peer.get("Online")),
                "direct": bool(peer.get("CurAddr")),
                "relay": str(peer.get("Relay") or ""),
                "lastSeen": str(peer.get("LastSeen") or ""),
                "in": peer.get("RxBytes") or 0,
                "out": peer.get("TxBytes") or 0,
            })
        return {
            "state": str(doc.get("BackendState") or ""),
            "online": bool(me.get("Online")),
            "relay": str(me.get("Relay") or ""),
            "version": str(doc.get("Version") or "").split("-")[0],
            "health": [str(h) for h in doc.get("Health") or []],
            "devices": devices,
            "ingress": ingress,
        }

    def funnel(self):
        """The paths open to the internet."""
        code, output = self.run(["tailscale", "funnel", "status"])
        return FUNNEL_PATH.findall(output) if code == 0 else None

    def battery(self):
        code, output = self.run(["termux-battery-status"])
        try:
            doc = json.loads(output) if code == 0 else None
        except ValueError:
            return None
        if not isinstance(doc, dict) or "percentage" not in doc:
            return None
        return {
            "percent": doc.get("percentage"),
            "plugged": doc.get("plugged") not in (None, "UNPLUGGED"),
            "status": str(doc.get("status") or ""),
            "health": str(doc.get("health") or ""),
            "temp": doc.get("temperature"),
            "voltage": doc.get("voltage"),
        }

    def wifi(self):
        code, output = self.run(["termux-wifi-connectioninfo"])
        try:
            doc = json.loads(output) if code == 0 else None
        except ValueError:
            return None
        if not isinstance(doc, dict) or doc.get("supplicant_state") != "COMPLETED":
            return None
        return {
            "rssi": doc.get("rssi"),
            "linkMbps": doc.get("link_speed_mbps"),
            "ghz": round((doc.get("frequency_mhz") or 0) / 1000, 1),
        }

    def about(self):
        """What doesn't change: asked once."""
        found = {"cores": os.cpu_count()}
        for key, prop in (("model", "ro.product.model"), ("android", "ro.build.version.release")):
            code, output = self.run(["getprop", prop])
            found[key] = output.strip() if code == 0 else ""
        return found

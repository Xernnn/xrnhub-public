#!/usr/bin/env python3
"""xrnhub: one page over every xrn app on the home server.

The server reads the other services' files read-only and writes nothing back; its own records are
the events apps send it, the nudges that fired, the health records xrnfit sends (kept for good:
there's no other copy), and the monitor's history of the phone it runs on.
The page ticks habits itself, through xrnloops' own API on the same origin. Same habits as the
other sync services: standard library plus `bcrypt`, published under a path (/xrnhub) by a reverse
proxy such as Tailscale Funnel, which may or may not strip that prefix.

    GET    /health                  200 "ok", no login (for monitoring)
    POST   /api/session             with Basic auth: sets the shared sign-in cookie
    DELETE /api/session             signs this browser out, and ends its cookie for good on every
                                    server that shares it (hublib/login.py)
    GET    /api/today               today across every app
    GET    /api/day?date=D          one day in full
    GET    /api/review[?fresh=1]    the long view: a year, each habit, hours, temptations
    GET    /api/services            which services answer
    GET    /api/apps                every project, with what the hub can see of each: up or down, what
                                    it holds, when it last wrote here, its released versions
    GET    /api/rules               the nudge rules, which put cards on Today
    PUT    /api/rules               replaces the rules; needs If-Match: "<version>" (a card's Turn off)
    POST   /api/nudges/dismiss      {"rule"}: hides that nudge for the rest of the day
    POST   /api/events              {"events": [{app, kind, at, title?, value?, detail?}]}
    POST   /api/health              {"source", "upsert": [{id, kind, ...}], "delete": [id]}: steps,
                                    heart rate, sleep, weight, SpO2 and stress, replaced whole by
                                    (source, id)

    The personal API, read-only (docs/api.md). A read-only token works here and nowhere else:
    GET    /api/v1                        what's here, and what each source holds
    GET    /api/v1/habits                 each habit, with today's mark and its streak
    GET    /api/v1/habits/entries         ?from=&to=&habit=
    GET    /api/v1/calendar               ?from=&to=, repeats worked out
    GET    /api/v1/money                  accounts and balances, categories, budgets, repeats, savings
    GET    /api/v1/money/transactions     ?from=&to=
    GET    /api/v1/inventory              the home inventory and shopping list
    GET    /api/v1/songs                  the song-of-the-day list and what's on hold (the owner's)
    GET    /api/v1/blocks                 xrnrule's blocks and today's use
    GET    /api/v1/blocks/events          ?from=&to=, what the devices logged
    GET    /api/v1/events                 ?from=&to=&app=, what apps reported here
    GET    /api/v1/health                 today's steps, distance and active energy, last night's sleep,
                                          heart rate, latest weight, SpO2 and stress
    GET    /api/v1/health/days            ?from=&to=, each day's steps, distance, active energy, sleep,
                                          heart rate, weight, SpO2 and stress
    GET    /api/v1/health/heart-rate      ?from=&to=, every sample (at most 7 days)
    GET    /api/v1/health/spo2            ?from=&to=, every SpO2 sample (at most 7 days)
    GET    /api/v1/health/stress          ?from=&to=, every stress sample (at most 7 days)
    GET    /api/v1/health/steps           ?from=&to=&every=, steps in buckets of `every` minutes
    GET    /api/v1/health/sleep           ?from=&to=, each sleep with its stages
    GET    /api/v1/health/weight          ?from=&to=, every weighing

    The monitor, for the first user in the users file only:
    GET    /api/server/now?since=MS       every gauge and service, live points since MS, and xrnfit's
                                          card (`health`); &feed=1 adds the latest requests
    GET    /api/server/history?range=R    charts; R is 1h 6h 24h 7d 30d 90d; &service=NAME for one,
                                          xrnfit too (its requests to the hub)
    GET    /api/server/services           30 days of uptime, what went wrong, xrnfit's readings a day
    GET    /api/server/traffic?range=R    requests by service, busiest paths, who's calling
    GET    /api/server/data               what each app holds, what came in, how long the disk lasts
    GET    /api/server/security           sign-ins, refused requests, devices, what's public
    GET    /api/server/logs               ?service=&level=&q=&limit=; xrnfit's are the hub's health lines;
                                          &routine=0 leaves out requests answered 2xx/3xx, &sig= keeps one message
    GET    /api/server/logstats?range=R   lines by level and service over time, warnings and errors by
                                          message; &service=NAME for one
    POST   /api/server/restart            {"service"}: restarts it; the hub, ssh and Tailscale can't be

Everything under /api needs Basic auth, or the sign-in cookie with an X-Xrn-Client header; /api/v1
also takes `Authorization: Bearer <token>`. Any other path serves web/.

    python3 xrnhub.py --data ~/xrnhub/data --users ~/.config/xrn-sync/users --home ~
    python3 xrnhub.py token add|list|revoke ... --data ~/xrnhub/data --users ~/.config/xrn-sync/users
"""
import argparse
import hashlib
import ipaddress
import json
import os
import re
import sys
import threading
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hublib import api, apps, daybook, health, login, logscan, metrics, review, rules, tokens  # noqa: E402
from hublib.inventory import Inventory  # noqa: E402
from hublib.log import BadEvent, HubLog  # noqa: E402
from hublib.machine import BY_NAME, SERVICES, Machine, answers  # noqa: E402
from hublib.sources import Sources, parse_day, read_json  # noqa: E402
from hublib.watcher import FIT, LEVELS, BadRestart, TooSoon, Watcher  # noqa: E402

MAX_BODY = 256 * 1024
# How many lists and objects deep a request body may go. The apps' own nest a handful. Within
# MAX_BODY, json.loads raises RecursionError past some 10,000 levels (52,000 on Python 3.14), and on
# 3.14 json.dumps, which stores an event's detail, gives up on objects some 29,000 deep.
MAX_DEPTH = 64
PREFIX = "/xrnhub"
TICK_S = 60  # how often the background work looks for something to do
RULES_EVERY_S = 300
PRUNE_EVERY_S = 86400
REVIEW_CACHE_S = 60
SERVICE_CACHE_S = 30
MONITOR_STALE_S = 180  # the monitor checks every minute; past this its word is too old to repeat
FIT_CACHE_S = 60  # the Overview asks every 3 seconds; xrnfit sends every few minutes
WEB_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".woff2": "font/woff2",
    ".webmanifest": "application/manifest+json",
}
WEB_NAME = re.compile(r"(?:fonts/|apps/)?[A-Za-z0-9][A-Za-z0-9._-]{0,80}")
MAX_LOG_LINES = 300
MAX_LOG_SEARCH = 80
BASE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; font-src 'self'; "
        "connect-src 'self'; manifest-src 'self'; base-uri 'self'; form-action 'none'; frame-ancestors 'none'"
    ),
}


class Hub:
    """What the handler and the background thread share."""

    def __init__(self, data, users, home, web, machine=None):
        self.data = data
        self.home = home
        self.web = web
        self.login = login.Login(users, data)
        self.tokens = tokens.Tokens(data, self.login.names)
        self.log = HubLog(data)
        self.sources = {}
        self.sources_lock = threading.Lock()
        self.rules_lock = threading.Lock()
        self.review_cache = {}
        self.fit_cache = {}
        self.service_cache = (0, [])
        self.last_rules = 0
        self.last_prune = 0
        prefix = os.environ.get("PREFIX", sys.prefix)
        self.machine = machine or Machine(home, prefix)
        self.monitor = Watcher(
            self.machine, metrics.Store(data),
            Inventory(home, self.login.names, self.source, self.machine.prefix, self.log),
        )
        self.apps = apps.AppsPage(home)

    def source(self, user):
        with self.sources_lock:
            if user not in self.sources:
                self.sources[user] = Sources(self.home, user)
            return self.sources[user]

    def rules_path(self, user):
        return os.path.join(self.data, "users", user, "rules.json")

    def read_rules(self, user):
        doc = read_json(self.rules_path(user))
        if isinstance(doc, dict) and isinstance(doc.get("rules"), list):
            try:
                return {"version": int(doc.get("version", 1)), "rules": rules.clean_rules(doc["rules"])}
            except (rules.BadRule, ValueError, TypeError):
                pass
        return {"version": 0, "rules": rules.starter_rules()}

    def write_rules(self, user, version, new_rules):
        path = self.rules_path(user)
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        tmp = f"{path}.{threading.get_ident()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"version": version, "rules": new_rules}, f, ensure_ascii=False, separators=(",", ":"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)

    def run_rules(self, user, now=None):
        now = now or datetime.now()
        src = self.source(user)
        today = daybook.today_view(src, self.log, now)
        f = rules.facts(today, rules.soon_list(src, now), now)
        return rules.run_due(self.read_rules(user)["rules"], f, today["day"], self.log, user)

    def today(self, user, now=None):
        """Today's page, with the nudges that hold this minute. Checking them here as well means a
        card goes the moment you tick the last habit, not at the next background pass."""
        now = now or datetime.now()
        src = self.source(user)
        view = daybook.today_view(src, self.log, now)
        f = rules.facts(view, rules.soon_list(src, now), now)
        saved = self.read_rules(user)["rules"]
        rules.run_due(saved, f, view["day"], self.log, user)
        view["nudges"] = rules.live(saved, f, self.log.dismissed(user, view["day"]))
        # How many are turned off: Today lists the rules to turn back on only while any is.
        view["nudgesOff"] = sum(1 for rule in saved if not rule["on"])
        return view

    def review(self, user, fresh=False):
        hit = self.review_cache.get(user)
        if hit and not fresh and time.monotonic() - hit[0] < REVIEW_CACHE_S:
            return hit[1]
        now = datetime.now()
        src = self.source(user)
        result = review.build(src, now, daybook.day_table(src, now), self.log.db(user))
        self.review_cache[user] = (time.monotonic(), result)
        return result

    def fit_card(self, user):
        """xrnfit's card on the Overview, kept a minute: it counts the health table, and the page
        asks every few seconds. A batch xrnfit sends drops it, so "last sent" is never a minute
        behind what came in."""
        hit = self.fit_cache.get(user)
        if hit and time.monotonic() - hit[0] < FIT_CACHE_S:
            return hit[1]
        card = apps.fit_card(self.log, user, self.source(user), self.monitor.data, datetime.now())
        self.fit_cache[user] = (time.monotonic(), card)
        return card

    def services(self):
        """Which services answer, as the monitor found at its last minute's pass, so the header
        and the Overview agree. Probed here only before that pass has run, or once it is
        MONITOR_STALE_S old, and then kept SERVICE_CACHE_S."""
        answering = self.monitor.answering
        if answering and self.monitor.clock() - self.monitor.last_minute < MONITOR_STALE_S:
            return [{"name": s.name, "up": answering.get(s.name, True)}
                    for s in SERVICES if s.health and s.name != "xrnhub"]
        at, known = self.service_cache
        if time.monotonic() - at < SERVICE_CACHE_S and known:
            return known
        found = [
            {"name": s.name, "up": answers(s.port, s.health, 1.5)} for s in SERVICES if s.health and s.name != "xrnhub"
        ]
        self.service_cache = (time.monotonic(), found)
        return found

    def background_tick(self):
        """One pass: the calendar and rules, each when due. Nothing here stops the next."""
        names = self.login.names()
        for user in names:
            self.source(user).calendar.refresh()
        clock = time.monotonic()
        rules_due = clock - self.last_rules >= RULES_EVERY_S or not self.last_rules
        prune_due = clock - self.last_prune >= PRUNE_EVERY_S or not self.last_prune
        for user in names:
            if rules_due:
                self.run_rules(user)
            if prune_due:
                self.log.prune(user)
        self.last_rules = clock if rules_due else self.last_rules
        self.last_prune = clock if prune_due else self.last_prune


def background(hub):
    while True:
        try:
            hub.background_tick()
        except Exception as e:  # noqa: BLE001 - a bad file must not end the loop
            sys.stderr.write(f"background: {type(e).__name__}: {e}\n")
        time.sleep(TICK_S)


def client_address(handler):
    """Who a wrong login is counted for. Behind the local proxy that's the last X-Forwarded-For
    hop, the one the proxy added: anything before it came from the caller, who could make up a
    new address for every guess. The header is only trusted from a loopback peer, for the same
    reason. An IPv6 address counts as its /64: a line comes with at least that many, so counted
    one by one its owner had a new address for every guess and never reached the limit."""
    addr = caller(handler)
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return addr
    if ip.version == 4:
        return addr
    if ip.ipv4_mapped:
        return str(ip.ipv4_mapped)
    return f"{ipaddress.IPv6Address(int(ip) >> 64 << 64)}/64"


def caller(handler):
    """The caller's address as the proxy saw it: the last X-Forwarded-For hop, from a loopback peer
    only (client_address says why). The log names the same, so a proxy that appends can't make a
    line the monitor's reader doesn't take for a request."""
    peer = handler.client_address[0]
    forwarded = handler.headers.get("X-Forwarded-For")
    return forwarded.split(",")[-1].strip() if forwarded and peer in ("127.0.0.1", "::1") else peer


def from_another_site(headers):
    """Whether a request came from another site's page. A browser that was once given the password
    in its own sign-in box sends it again with a form any site posts here, and a text/plain form
    body can be JSON. The browser says where the page was in Sec-Fetch-Site; one too old for that
    still sends Origin with a POST, held against this host's name (or the proxy's X-Forwarded-Host).
    The apps, xrnmail and the bar widgets send neither."""
    origin = (headers.get("Origin") or "").strip().lower()
    if origin.startswith(("chrome-extension://", "moz-extension://")):
        return False
    site = (headers.get("Sec-Fetch-Site") or "").strip().lower()
    if site:
        return site in ("cross-site", "same-site")
    scheme, _, host = origin.partition("://")
    if scheme not in ("http", "https"):
        return False  # no Origin, or "null"
    ours = [headers.get("Host") or "", *(headers.get("X-Forwarded-Host") or "").split(",")]
    return host_key(host) not in {host_key(h) for h in ours if h.strip()}


def host_key(host):
    """A host as Origin and Host both write it: lower case, without the default port."""
    return host.strip().lower().removesuffix(":443").removesuffix(":80")


def nested_within(value, limit):
    """Whether no list or object in [value] sits more than [limit] deep, the outermost being 1.
    Walked with a list of its own, not by recursion, so any depth is measured without a stack."""
    todo = [(value, 1)] if isinstance(value, (dict, list)) else []
    while todo:
        value, depth = todo.pop()
        if depth > limit:
            return False
        for v in value.values() if isinstance(value, dict) else value:
            if isinstance(v, (dict, list)):
                todo.append((v, depth + 1))
    return True


ETAGS = {}  # web file -> ((mtime ns, size, inode), its ETag)


def etag_of(path):
    """A web file's ETag, hashed again only when the file changes: a page load revalidates some
    20 files, fonts among them, and most answers are a 304."""
    st = os.stat(path)
    stamp = st.st_mtime_ns, st.st_size, st.st_ino
    hit = ETAGS.get(path)
    if hit and hit[0] == stamp:
        return hit[1]
    with open(path, "rb") as f:
        tag = '"' + hashlib.sha256(f.read()).hexdigest()[:16] + '"'
    ETAGS[path] = (stamp, tag)
    return tag


# The request line and X-Forwarded-For come from the caller. Written to the log as they are, a
# control character in them could rewrite the terminal the log is read in, or (a \r) start a line
# of its own for the monitor's log reader (hublib/logscan.py). Escaped as http.server's own
# log_message, replaced here, does.
LOG_ESCAPES = str.maketrans({**{c: f"\\x{c:02x}" for c in (*range(0x20), *range(0x7F, 0xA0))}, "\\": "\\\\"})


class Handler(BaseHTTPRequestHandler):
    server_version = "xrnhub/1"
    sys_version = ""  # the Server header named the Python version too
    protocol_version = "HTTP/1.1"
    timeout = 60
    hub = None
    served = 0  # requests answered on this connection

    def handle_one_request(self):
        """After the first request, waiting for the next is waiting on a kept-alive connection:
        Tailscale Funnel's proxy holds idle ones open, and they're closed after `timeout`. One that
        times out with no new request begun has simply ended, so it closes without a word. That was
        some 30 lines an hour, each under the address of the connection's last caller. A timeout
        on the first request, or partway through one, is still logged by http.server."""
        if self.served:
            try:
                begun = self.rfile.peek(1)
            except TimeoutError:
                begun = b""
            if not begun:
                self.close_connection = True
                return
        super().handle_one_request()
        self.served += 1

    def parse_request(self):
        self.body_read = False
        if not super().parse_request():
            return False
        # Before the login, which a page elsewhere can have the browser pass for it.
        if self.command not in ("GET", "HEAD", "OPTIONS") and from_another_site(self.headers):
            self.send(403, "refused: sent from another site's page")
            return False
        return True

    def log_message(self, fmt, *a):
        headers = getattr(self, "headers", None)  # none yet on a request line that didn't parse
        who = "".join(caller(self).split()) or "-" if headers else "-"
        sys.stderr.write(f"{who} {fmt % a}".translate(LOG_ESCAPES) + "\n")

    def route(self):
        path = self.path.partition("?")[0]
        if path == PREFIX or path.startswith(PREFIX + "/"):
            path = path[len(PREFIX):] or "/"
        return path

    def query(self, name, fallback):
        for part in self.path.partition("?")[2].split("&"):
            key, _, value = part.partition("=")
            if key == name:
                return value
        return fallback

    def send(self, code, body=b"", ctype="text/plain; charset=utf-8", headers=None, cache="no-store"):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Cache-Control", cache)
        for k, v in BASE_HEADERS.items():
            self.send_header(k, v)
        if code != 304:
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        if self.headers.get("Transfer-Encoding") or (
                self.headers.get("Content-Length") not in (None, "0") and not self.body_read):
            # Refused before reading it, or sent chunked, which body() never reads: left in the
            # pipe, it would pass for the next request.
            self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD" and code != 304:
            self.wfile.write(body)

    def send_json(self, doc, headers=None):
        body = json.dumps(doc, ensure_ascii=False, separators=(",", ":"))
        self.send(200, body, "application/json; charset=utf-8", headers)

    def body(self):
        """The request's JSON, or None once an error has been answered."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self.send(400, "bad Content-Length")
            return None
        if length <= 0 or length > MAX_BODY:
            self.send(413, "body missing or too large")
            return None
        raw = self.rfile.read(length)
        self.body_read = True
        try:
            doc = json.loads(raw)
            deep = not nested_within(doc, MAX_DEPTH)
        except ValueError:
            self.send(400, "body must be JSON")
            return None
        except RecursionError:  # past the parser's depth: it ended with no answer, and a traceback
            deep = True
        if deep:
            # Or parsed, and too deep to store: an event's detail is written with json.dumps, which
            # raised RecursionError on 3.14. Refused before anything is done with it.
            self.send(400, f"body nested deeper than {MAX_DEPTH} levels")
            return None
        return doc

    def who(self, token_ok=False):
        """The signed-in user, or None once a refusal has been answered. [token_ok] lets a read-only
        API token stand in for the password: only the personal API's reads pass it."""
        auth = self.hub.login
        header = self.headers.get("Authorization")
        if not header and self.headers.get("X-Xrn-Client"):
            user = auth.check_session(self.headers.get("Cookie"))
            if user:
                return user
        addr = client_address(self)
        # A password holds a place in the count while it is checked (reserve_guess).
        if not (auth.reserve_guess(addr) if header else not auth.locked_out(addr)):
            if header:  # as below: a request with no password guessed nothing, and isn't held
                time.sleep(login.FAIL_DELAY_S)
            self.send(429, "too many wrong passwords; try again later",
                      headers={"Retry-After": str(login.FAIL_WINDOW_S)})
            return None
        right = None
        try:
            # A password already found right is a session, not a login, and a token is never a
            # password: neither settles anything. A device sending its password with every request,
            # or a script using a token, cleared the count for its whole address, so a guesser
            # behind the same one never ran out of guesses.
            user = auth.cached_basic(header)
            if not user:
                user = auth.check_basic(header)
                right = True if user else None
            if not user and token_ok:
                user = self.hub.tokens.user_for(header)
            if not user:
                right = False
        finally:
            if header:
                auth.settle_guess(addr, login.basic_login(header)[0], right)
        if not user:
            if header:
                time.sleep(login.FAIL_DELAY_S)
            # A page's own fetch must see a plain 401, not the browser's password box.
            challenge = {} if self.headers.get("X-Xrn-Client") else {"WWW-Authenticate": 'Basic realm="xrnhub"'}
            self.send(401, "login required", headers=challenge)
            return None
        return user

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = self.route()
        if path == "/health":
            return self.send(200, "ok")
        if not path.startswith("/api/"):
            return self.serve_web(path)
        if path.startswith("/api/server/"):
            return self.get_server(path)
        if path == "/api/v1" or path.startswith("/api/v1/"):
            return self.get_v1(path)
        known = ("/api/today", "/api/day", "/api/review", "/api/services", "/api/rules", "/api/apps")
        if path not in known:
            return self.send(404, "not found")
        user = self.who()
        if not user:
            return None
        hub = self.hub
        if path == "/api/today":
            return self.send_json(hub.today(user))
        if path == "/api/day":
            day = self.parse_past_day(self.query("date", ""))
            if day is None:
                return self.send(400, "bad date")
            return self.send_json(daybook.day_view(hub.source(user), hub.log, datetime.now(), day))
        if path == "/api/review":
            return self.send_json(hub.review(user, fresh=self.query("fresh", "") == "1"))
        if path == "/api/services":
            return self.send_json({"services": hub.services()})
        if path == "/api/apps":
            owner = user == next(iter(hub.login.names()), None)
            return self.send_json(hub.apps.answer(user, owner, hub.monitor, hub.log, hub.source(user)))
        doc = hub.read_rules(user)
        return self.send_json({"rules": doc["rules"]}, {"ETag": f'"{doc["version"]}"'})

    def get_v1(self, path):
        user = self.who(token_ok=True)
        if not user:
            return None
        ask = api.Ask(self.hub.source(user), self.hub.log, user, datetime.now(),
                      lambda name, fallback: unquote(self.query(name, fallback), errors="replace"),
                      owner=user == next(iter(self.hub.login.names()), None))
        try:
            doc = api.answer(path, ask)
        except api.BadRequest as e:
            return self.send(400, str(e))
        if doc is None:
            return self.send(404, "not found")
        return self.send_json(doc)

    def owner(self):
        """The signed-in owner, or None once a refusal has been answered. The monitor shows every
        visitor's address and can restart things, which a second account has no business doing."""
        user = self.who()
        if user and user != next(iter(self.hub.login.names()), None):
            self.send(403, "only the server's owner can see this")
            return None
        return user

    def get_server(self, path):
        known = ("now", "history", "services", "traffic", "data", "security", "logs", "logstats")
        page = path.removeprefix("/api/server/")
        if page not in known:
            return self.send(404, "not found")
        user = self.owner()
        if not user:
            return None
        watcher = self.hub.monitor
        if page == "now":
            try:
                since = int(self.query("since", "0"))
            except ValueError:
                return self.send(400, "bad since")
            doc = watcher.now_doc(since, feed=self.query("feed", "") == "1")
            # xrnfit has no service to list, so it comes as a card of its own.
            doc["health"] = self.hub.fit_card(user)
            return self.send_json(doc)
        if page == "services":
            return self.send_json(watcher.services_doc())
        if page == "data":
            return self.send_json(watcher.data_doc() or {"pending": True})
        if page == "security":
            return self.send_json(watcher.security_doc())
        if page == "logs":
            return self.get_logs(watcher)
        range_name = self.query("range", "24h")
        if range_name not in metrics.RANGES:
            return self.send(400, "bad range")
        if page == "traffic":
            return self.send_json(watcher.traffic_doc(range_name))
        service = self.query("service", "")
        if service and service not in BY_NAME and service != FIT:
            return self.send(400, "bad service")
        if page == "logstats":
            return self.send_json(watcher.log_stats(range_name, service or None))
        return self.send_json(watcher.history_doc(range_name, service or None))

    def get_logs(self, watcher):
        service = self.query("service", "")
        level = self.query("level", "info")
        if (service and service not in BY_NAME and service != FIT) or level not in LEVELS:
            return self.send(400, "bad filter")
        try:
            limit = min(max(int(self.query("limit", "150")), 1), MAX_LOG_LINES)
        except ValueError:
            return self.send(400, "bad limit")
        text = unquote(self.query("q", ""), errors="replace")[:MAX_LOG_SEARCH]
        said = unquote(self.query("sig", ""), errors="replace")[:logscan.MAX_SIGNATURE]
        routine = self.query("routine", "1") != "0"
        return self.send_json({"lines": watcher.log_lines(service, level, text, limit, routine, said)})

    def post_restart(self):
        if not self.owner():
            return None
        doc = self.body()
        if doc is None:
            return None
        name = doc.get("service") if isinstance(doc, dict) else None
        try:
            worked, said = self.hub.monitor.restart(name)
        except BadRestart:
            return self.send(400, "that service can't be restarted from here")
        except TooSoon:
            return self.send(429, "restarted a moment ago")
        return self.send_json({"ok": worked, "said": said})

    def do_POST(self):
        path = self.route()
        if path == "/api/server/restart":
            return self.post_restart()
        if path == "/api/session":
            user = self.who()
            if not user:
                return None
            cookie = login.session_cookie(self.hub.login.make_session(user), login.SESSION_S)
            return self.send(200, '{"ok":true}', "application/json", {"Set-Cookie": cookie})
        if path not in ("/api/events", "/api/nudges/dismiss", "/api/health"):
            return self.send(404, "not found")
        user = self.who()
        if not user:
            return None
        doc = self.body()
        if doc is None:
            return None
        if path == "/api/nudges/dismiss":
            return self.post_dismiss(user, doc)
        if path == "/api/health":
            return self.post_health(user, doc)
        events = doc.get("events") if isinstance(doc, dict) else None
        if not isinstance(events, list) or not 1 <= len(events) <= 200:
            return self.send(400, 'body must be {"events": [...]}')
        try:
            added = self.hub.log.add(user, events)
        except BadEvent as e:
            return self.send(400, str(e))
        return self.send_json({"added": added})

    def post_health(self, user, doc):
        try:
            upserted, deleted = health.write(self.hub.log, user, doc)
        except health.BadHealth as e:
            return self.send(400, str(e))
        self.hub.fit_cache.pop(user, None)
        return self.send_json({"upserted": upserted, "deleted": deleted})

    def parse_past_day(self, text):
        """A date that has happened, inside what the hub keeps; None otherwise."""
        day = parse_day(text) if isinstance(text, str) else None
        now = datetime.now()
        if day is None or day > now.date() or day < now.date() - timedelta(days=daybook.HISTORY_DAYS):
            return None
        return day

    def post_dismiss(self, user, doc):
        rule = doc.get("rule") if isinstance(doc, dict) else None
        if not isinstance(rule, str) or not rules.RULE_ID.fullmatch(rule):
            return self.send(400, "bad rule")
        hub = self.hub
        src = hub.source(user)
        _, _, start_hour = src.habits()
        hub.log.dismiss(user, rule, daybook.habit_day(datetime.now(), start_hour).isoformat())
        return self.send_json({"ok": True})

    def do_PUT(self):
        if self.route() != "/api/rules":
            return self.send(404, "not found")
        user = self.who()
        if not user:
            return None
        doc = self.body()
        if doc is None:
            return None
        try:
            cleaned = rules.clean_rules(doc.get("rules") if isinstance(doc, dict) else None)
        except rules.BadRule as e:
            return self.send(400, str(e))
        want = (self.headers.get("If-Match") or "").strip().removeprefix("W/").strip('"')
        if not want:
            return self.send(428, "If-Match required")
        hub = self.hub
        with hub.rules_lock:
            cur = hub.read_rules(user)
            if want != str(cur["version"]):
                return self.send(412, "changed since you read it", headers={"ETag": f'"{cur["version"]}"'})
            version = cur["version"] + 1
            hub.write_rules(user, version, cleaned)
        return self.send_json({"ok": True, "rules": cleaned}, {"ETag": f'"{version}"'})

    def do_DELETE(self):
        if self.route() != "/api/session":
            return self.send(404, "not found")
        # Emptying the browser's cookie alone left any copy of it signing in for its full 30 days.
        self.hub.login.end_session(self.headers.get("Cookie"))
        return self.send(200, '{"ok":true}', "application/json",
                         {"Set-Cookie": login.session_cookie("", 0)})

    def serve_web(self, path):
        name = "index.html" if path in ("", "/") else path.lstrip("/")
        if not WEB_NAME.fullmatch(name) or ".." in name:
            return self.send(404, "not found")
        ctype = WEB_TYPES.get(os.path.splitext(name)[1])
        full = os.path.join(self.hub.web, name)
        if ctype is None or not os.path.isfile(full):
            return self.send(404, "not found")
        tag = etag_of(full)
        if self.headers.get("If-None-Match") == tag:
            return self.send(304, headers={"ETag": tag}, cache="no-cache")
        with open(full, "rb") as f:
            return self.send(200, f.read(), ctype, {"ETag": tag}, cache="no-cache")


class HubServer(ThreadingHTTPServer):
    """The server, quiet about callers that hang up. A connection reset or a broken pipe is the
    other end going away, mid-answer or between requests: one line says so. socketserver printed
    some 20 lines of traceback for each, which the monitor's log view showed as an error. Anything
    else is a fault here and keeps its traceback."""

    daemon_threads = True

    def handle_error(self, request, client_address):
        error = sys.exc_info()[1]
        if isinstance(error, (ConnectionError, TimeoutError)):
            sys.stderr.write(f"{client_address[0]} connection dropped: {error!r}".translate(LOG_ESCAPES) + "\n")
            return
        super().handle_error(request, client_address)


def main():
    if sys.argv[1:2] == ["token"]:
        return tokens.cli(sys.argv[2:])
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=5238)
    p.add_argument("--data", required=True, help="where the hub's own records live")
    p.add_argument("--users", required=True, help="htpasswd file with bcrypt hashes")
    p.add_argument("--home", default="~", help="the folder holding the other services' data")
    p.add_argument("--web", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "web"))
    args = p.parse_args()
    data = os.path.expanduser(args.data)
    os.makedirs(data, mode=0o700, exist_ok=True)
    hub = Hub(data, os.path.expanduser(args.users), os.path.expanduser(args.home), os.path.expanduser(args.web))
    Handler.hub = hub
    threading.Thread(target=background, args=(hub,), daemon=True).start()
    threading.Thread(target=hub.monitor.run, daemon=True).start()
    srv = HubServer((args.host, args.port), Handler)
    sys.stderr.write(f"xrnhub on http://{args.host}:{args.port}, data in {data}\n")
    srv.serve_forever()


if __name__ == "__main__":
    main()

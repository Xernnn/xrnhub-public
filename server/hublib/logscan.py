"""Reads the services' logs: runit's svlogd keeps them as `current` plus rotated `@...s` files.

    2026-11-06_01:09:14.23919 203.0.113.9 "GET /api/changes?since=4 HTTP/1.1" 200 -

svlogd stamps lines in UTC. Query strings never leave this module: they hold nothing that's needed
and could hold anything.
"""
import calendar
import os
import re

STAMP = re.compile(r"^(\d{4})-(\d\d)-(\d\d)_(\d\d):(\d\d):(\d\d)\.\d+ (.*)$")
REQUEST = re.compile(r'^(\S+) "([A-Z]+) ([^ "]+) HTTP/[\d.]+" (\d{3})')
QUERY = re.compile(r'\?[^ "]*')
# Sign-ins are read only from the service that writes them, and only where its own message starts:
# other lines carry text a caller chose (Radicale logs each User-Agent), and so can a user name, which
# is why it may hold spaces and the address is the last "from ... port" in the line.
SSHD = r"^(?:sshd(?:-session|-auth)?(?:\[\d+\])?: )?"  # with -e, sshd may name itself first
LOGIN_OK = re.compile(SSHD + r"Accepted \S+ for (.*) from (\S+) port \d+")
LOGIN_BAD = re.compile(SSHD + r"(?:Failed \S+ for (?:invalid user )?|Invalid user )(.*) from (\S+) port \d+")
CALENDAR_BAD = re.compile(  # after Radicale's own [time] [process/thread] fields, if it writes them
    r"^(?:\[[^\]]*\] )*\[WARNING\] Failed login attempt from (\S+)(?: \(forwarded for '([^']+)'\))?(?:: '([^']*)')?"
)
ERROR_WORDS = re.compile(r"Traceback|\bERROR\b|\bCRITICAL\b|Exception:|panic:")
WARN_WORDS = re.compile(r"\bWARNING\b|\bwarn\b|Failed ")
# A line that starts by saying what it is, in any case: "error: upstream timed out". A Python
# exception's own line names it: "BrokenPipeError: [Errno 32] Broken pipe".
ERROR_START = re.compile(r"^(?:error|fatal|critical)\b", re.I)
WARN_START = re.compile(r"^(?:warning|warn)\b", re.I)
EXCEPTION = re.compile(r"^[A-Za-z_][\w.]*(?:Error|Exception): ")
# Lines with an alarming word that say nothing's wrong: Radicale's startup line, and the services'
# own notes about callers that went away.
QUIET = re.compile(r"Logging level set to|connection dropped: |caller left a long-poll after ")
# A traceback, as Python and socketserver print one, is many lines that are one error.
TRACE_START = re.compile(r"^(?:Traceback \(most recent call last\):|Exception occurred during processing of request)")
TRACE_CHAIN = re.compile(r"^(?:During handling of the above exception|The above exception was the direct cause)")
SEPARATOR = re.compile(r"^-{10,}$")
UNAUTHORIZED = 401
CONFLICT = 412  # If-Match lost to another write: the clients fetch and try again themselves
UNSUPPORTED = 501  # http.server's answer to a method it has no handler for, HEAD among them
HOLD_MS = 2000  # how long a 401 waits for its answered retry, and a traceback for its last line
FIRST_READ_LIMIT = 4 * 1024 * 1024  # the most history to take from a log folder the first time
READ_LIMIT = 1024 * 1024
TAIL_BYTES = 192 * 1024
MAX_LINE = 400
MAX_PATH = 60
READ_METHODS = ("GET", "HEAD", "OPTIONS", "PROPFIND", "REPORT")
MONITOR_POLL = "/api/server/now"
# What changes from one line of a message to the next, so the same message groups as one: a server's
# own [time] [process/thread] fields first, then quoted words, addresses, ids and numbers.
OWN_FIELDS = re.compile(r"^(?:\[[^\]]*\d[^\]]*\] )+")
QUOTED = re.compile(r"'[^']*'")
ADDRESS = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b|(?<![\w:])[0-9a-f]{0,4}(?::[0-9a-f]{0,4}){2,7}(?![\w:])", re.I)
DECIMAL = re.compile(r"(?<![\w.])\d+\.\d+(?![\d.])")
NUMBER = re.compile(r"\b0x[0-9a-f]+\b|\b[0-9a-f]*\d[0-9a-f]*\b|(?<![a-z])\d+", re.I)
MAX_SIGNATURE = 160


def parse_line(line):
    """(ms since the epoch, the text), or None for a line with no stamp."""
    match = STAMP.match(line)
    if not match:
        return None
    y, mo, d, h, mi, s = (int(x) for x in match.groups()[:6])
    return calendar.timegm((y, mo, d, h, mi, s)) * 1000, match[7]


def parse_request(text):
    match = REQUEST.match(text)
    if not match:
        return None
    path, _, query = match[3].partition("?")
    status = int(match[4])
    return {
        "ip": match[1],
        "method": match[2],
        "path": path[:MAX_PATH],
        "status": status,
        # Apps and this page asking on a timer, as opposed to someone doing something. A `wait=`
        # request is held open until there's news.
        "poll": "wait=" in query or path.endswith(MONITOR_POLL),
        "write": match[2] not in READ_METHODS and status < 400,
    }


def parse_login(text, service):
    """(worked, who, from where) for a line of [service]'s log about signing in: sshd's in ssh's,
    Radicale's in calendar's."""
    if service == "ssh":
        for pattern, ok in ((LOGIN_OK, True), (LOGIN_BAD, False)):
            match = pattern.match(text)
            if match:
                return ok, match[1], match[2]
    if service == "calendar":
        match = CALENDAR_BAD.match(text)
        return (False, match[3] or "", match[2] or match[1]) if match else None
    return None


def level_of(text):
    request = parse_request(text)
    if request:
        status = request["status"]
        if status == UNSUPPORTED:
            return "warn"
        return "error" if status >= 500 else "warn" if status >= 400 and status != CONFLICT else "info"
    if QUIET.search(text):
        return "info"
    if ERROR_WORDS.search(text) or ERROR_START.match(text) or EXCEPTION.match(text):
        return "error"
    return "warn" if WARN_WORDS.search(text) or WARN_START.match(text) else "info"


class Lines:
    """One log's lines, oldest first, as what they say: `feed` and `flush` give (ms, text, level,
    routine) for each. A traceback comes out as one error, its last exception line, however many
    lines and chained exceptions it ran to. A 401 answered within a moment by a 2xx or 3xx for the
    same caller and path is a browser meeting the password challenge, so it's routine, as requests
    answered 2xx or 3xx and a 412 (If-Match lost, which the clients sort out) are."""

    def __init__(self):
        self.block = None  # [ms of its first line, ms of its latest] while in a traceback
        self.cause = None  # (ms, text): the traceback's exception line so far
        self.awaiting = False  # a traceback's frames are being listed: its exception line is next
        self.chained = False  # "During handling of the above exception": another traceback follows
        self.challenges = []  # [(ms, text, (ip, path))]: 401s waiting for their answered retry

    def feed(self, at, text):
        out = []
        if SEPARATOR.match(text.strip()):
            return out
        if self.block is not None:
            if TRACE_CHAIN.match(text):
                self.chained = True
            elif TRACE_START.match(text) and not (self.awaiting or self.chained):
                out.append(self.end_block())  # a traceback of its own straight after this one
            elif TRACE_START.match(text) or not text.strip() or text[:1].isspace():
                self.awaiting = self.awaiting or bool(TRACE_START.match(text))
                self.chained = self.chained and not TRACE_START.match(text)
            elif self.awaiting:
                self.cause, self.awaiting = (at, text), False  # the last exception is what's shown
            else:
                out.append(self.end_block())
            if self.block is not None:
                self.block[1] = at
                return out
        if TRACE_START.match(text):
            self.block, self.awaiting = [at, at], True
            return out
        request = parse_request(text)
        out += self.expired(at)
        if request:
            status = request["status"]
            key = (request["ip"], request["path"])
            if status == UNAUTHORIZED:
                self.challenges.append((at, text, key))
                return out
            if status < 400:
                match = next((c for c in self.challenges if c[2] == key), None)
                if match:
                    self.challenges.remove(match)
                    out.append((match[0], match[1], "info", True))
            out.append((at, text, level_of(text), status < 400 or status == CONFLICT))
            return out
        out.append((at, text, level_of(text), False))
        return out

    def end_block(self):
        first, _ = self.block
        at, text = self.cause or (first, "Traceback (most recent call last):")
        self.block = self.cause = None
        self.awaiting = self.chained = False
        return at, text, "error", False

    def expired(self, now_ms):
        """The 401s no retry answered in time: refused, so warnings."""
        late = [c for c in self.challenges if now_ms - c[0] > HOLD_MS]
        self.challenges = [c for c in self.challenges if now_ms - c[0] <= HOLD_MS]
        return [(at, text, "warn", False) for at, text, _ in late]

    def flush(self, now_ms=None):
        """What's settled: everything at the end of the lines (no `now_ms`), or, while the log is
        still being written, only what's waited out HOLD_MS, so a traceback or a retry that's
        half written when a pass ends is finished by the next."""
        out = []
        if self.block is not None and (now_ms is None or now_ms - self.block[1] > HOLD_MS):
            out.append(self.end_block())
        out += self.expired(float("inf") if now_ms is None else now_ms)
        return out


def clean(text):
    return QUERY.sub("", text)[:MAX_LINE]


def signature(text):
    """What a line says with the parts that change taken out, so a message logged a thousand times is
    one row: a request is its method, path and status; anything else its words."""
    request = parse_request(text)
    if request:
        return f'{request["method"]} {request["path"]} {request["status"]}'
    words = OWN_FIELDS.sub("", clean(text))
    words = NUMBER.sub("#", DECIMAL.sub("#", ADDRESS.sub("…", QUOTED.sub("'…'", words))))
    return words.strip()[:MAX_SIGNATURE]


def read_from(path, offset, limit):
    """(whole lines from offset, the offset after them)."""
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            data = f.read(limit)
    except OSError:
        return [], offset
    end = data.rfind(b"\n") + 1
    return data[:end].decode("utf-8", "replace").splitlines(), offset + end


class Follower:
    """Hands over each new line of one log folder once, across rotations and restarts: svlogd
    renames `current`, so a rotated file keeps its inode and can be finished from where we were."""

    def __init__(self, folder, inode=0, offset=0):
        self.folder = folder
        self.inode = inode
        self.offset = offset

    def rotated(self):
        try:
            names = [n for n in os.listdir(self.folder) if n.startswith("@") and n.endswith((".s", ".u"))]
        except OSError:
            return []
        return [os.path.join(self.folder, n) for n in sorted(names)]

    def read(self):
        current = os.path.join(self.folder, "current")
        try:
            inode = os.stat(current).st_ino
            size = os.stat(current).st_size
        except OSError:
            return []
        lines = []
        if not self.inode:
            budget = FIRST_READ_LIMIT
            for path in reversed(self.rotated()):
                taken, _ = read_from(path, 0, budget)
                lines = taken + lines
                budget -= sum(len(t) + 1 for t in taken)
                if budget <= 0:
                    break
        elif inode != self.inode:
            for path in self.rotated():
                try:
                    if os.stat(path).st_ino == self.inode:
                        lines += read_from(path, self.offset, FIRST_READ_LIMIT)[0]
                except OSError:
                    pass
            self.offset = 0
        elif size < self.offset:
            self.offset = 0  # cut short by hand
        self.inode = inode
        taken, self.offset = read_from(current, self.offset, READ_LIMIT)
        return lines + taken


def before(folder, inode, offset, budget):
    """Up to [budget] bytes of the newest whole lines a Follower at (inode, offset) has already read,
    oldest first: what was there before something started counting the lines it reads."""
    if not inode:
        return []
    follower = Follower(folder)
    current = os.path.join(folder, "current")
    try:
        files = [(current, os.stat(current).st_ino)]
    except OSError:
        files = []
    for path in reversed(follower.rotated()):
        try:
            files.append((path, os.stat(path).st_ino))
        except OSError:
            pass
    # Newest first: the file the follower is in, up to where it got, then every older one whole.
    at = next((i for i, (_, ino) in enumerate(files) if ino == inode), None)
    if at is None:
        return []
    lines = []
    for i, (path, _) in enumerate(files[at:]):
        try:
            end = offset if i == 0 else os.path.getsize(path)
            start = max(end - budget, 0)
            with open(path, "rb") as f:
                f.seek(start)
                data = f.read(end - start)
        except OSError:
            continue
        if start:
            data = data[data.find(b"\n") + 1:]  # the line cut in half
        data = data[:data.rfind(b"\n") + 1]
        lines = data.decode("utf-8", "replace").splitlines() + lines
        budget -= end - start
        if budget <= 0:
            break
    return lines


def tail(folder, keep=None):
    """The newest lines of a log folder as (ms, text), oldest first, without reading all of it."""
    path = os.path.join(folder, "current")
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(size - TAIL_BYTES, 0))
            data = f.read()
    except OSError:
        return []
    found = []
    for line in data.decode("utf-8", "replace").splitlines():
        parsed = parse_line(line)
        if parsed and (keep is None or keep(parsed[1])):
            found.append(parsed)
    return found

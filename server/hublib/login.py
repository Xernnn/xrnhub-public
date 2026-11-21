"""Who is asking: Basic auth for devices, the shared xrn_session cookie for browsers.

The users file and session.key are the ones the other xrn sync services read, so one sign-in covers
the hub, the habits web app and the blocks status page. A cookie counts only with an X-Xrn-Client
header, which another site's page can't send.

Signing out ends that one cookie for good, on every server that takes it: a SHA-256 of it goes on
signed-out.json beside the users file and session.key until it would have run out, and the hub,
xrnloops-sync, xrnrule-sync and xrninsta all refuse what that list holds; any of them signing out
adds to it. The cookie's format and session.key stay as they were. A new password ends every
session everywhere.
"""
import base64
import fcntl
import hashlib
import hmac
import json
import os
import re
import sys
import threading
import time

AUTH_CACHE_S = 600  # a bcrypt check costs ~0.3s on a phone; don't pay it per request
FAIL_DELAY_S = 2  # after a wrong one, as Radicale does
FAIL_LIMIT = 10  # wrong logins from one address within FAIL_WINDOW_S, then 429 until they age out
FAIL_WINDOW_S = 15 * 60
SESSION_COOKIE = "xrn_session"
SESSION_S = 30 * 86400
SIGNED_OUT = "signed-out.json"
USER_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


def load_session_key(path):
    """Made on first start by whichever server gets there first; the others read it."""
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, "wb") as f:
            f.write(os.urandom(32))
    for _ in range(50):  # another server may be writing it this very moment
        with open(path, "rb") as f:
            key = f.read()
        if len(key) >= 32:
            return key
        time.sleep(0.1)
    raise SystemExit(f"{path} is too short to sign with")


def read_names(users_path):
    """The user names an htpasswd file lists, first first: the first is the server's owner."""
    found = []
    try:
        with open(users_path, encoding="utf-8") as f:
            for line in f:
                name = line.strip().partition(":")[0]
                if USER_NAME.fullmatch(name):
                    found.append(name)
    except OSError:
        pass
    return found


class SignedOut:
    """The cookies signed out on any server that shares the users file: signed-out.json, beside it,
    {sha256 of the cookie: when it would have run out}. xrnloops-sync, xrnrule-sync and xrninsta
    read and write the same file with their own copy of this."""

    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.seen = None  # the file (inode, mtime, size) [listed] was read from
        self.listed = {}

    def read(self):
        """The file's list: {} with no file, None (and a line in the log) for one that can't be read."""
        try:
            with open(self.path, encoding="utf-8") as f:
                doc = json.load(f)
            if not isinstance(doc, dict):
                raise ValueError("not a JSON object")
        except FileNotFoundError:
            return {}
        except (OSError, ValueError, RecursionError) as e:  # ValueError: also bad UTF-8
            sys.stderr.write(f"{self.path} can't be read, so the sign-outs read before it hold: {e}\n")
            return None
        return {k: v for k, v in doc.items() if isinstance(k, str) and isinstance(v, (int, float))}

    def hashes(self):
        """The list as the file has it now, read again only when it has changed. Another server's
        sign-out shows up here on the next request; a file that can't be read keeps the last list."""
        try:
            st = os.stat(self.path)
            now = (st.st_ino, st.st_mtime_ns, st.st_size)
        except FileNotFoundError:
            now = None
        except OSError as e:
            now = ("unreadable", e.errno)
        with self.lock:
            if now != self.seen:
                self.seen = now
                listed = self.read() if now is not None else {}
                if listed is not None:
                    self.listed = listed
            return self.listed

    def add(self, entries):
        """Puts {sha256: when it runs out} on the list, dropping what has run out. Under a lock on
        signed-out.json.lock, and the file read again inside it, so two servers signing out at once
        both keep theirs; written whole to a new file that then takes its place."""
        lock = os.open(f"{self.path}.lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX)
            listed = self.read()
            if listed is None:
                listed = dict(self.listed)
            now = time.time()
            kept = {k: v for k, v in {**listed, **entries}.items() if v >= now}  # run out: refused anyway
            tmp = f"{self.path}.{os.getpid()}.{threading.get_ident()}.tmp"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(kept, f, separators=(",", ":"))
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
            st = os.stat(self.path)
            with self.lock:
                self.seen, self.listed = (st.st_ino, st.st_mtime_ns, st.st_size), kept
        finally:
            os.close(lock)  # and with it the flock


class Login:
    def __init__(self, users_path, data_dir=None):
        self.users_path = users_path
        self.session_key = load_session_key(os.path.join(os.path.dirname(users_path), "session.key"))
        self.signed_out = SignedOut(os.path.join(os.path.dirname(users_path), SIGNED_OUT))
        if data_dir:
            self.move_signed_out(os.path.join(data_dir, SIGNED_OUT))
        self.cache = {}  # sha256(user:secret) -> (user, expires, users-file mtime)
        self.fail_lock = threading.Lock()
        self.fail_settled = threading.Condition(self.fail_lock)  # a login being checked has been settled
        self.failures = {}  # client address -> (monotonic time, user name tried) of its recent wrong logins
        self.checking = {}  # client address -> how many of its logins are being checked right now

    def move_signed_out(self, old):
        """The hub kept its sign-outs in its own data folder, where the other servers never looked.
        They go onto the shared list once, and the old file becomes signed-out.json.migrated."""
        if not os.path.exists(old) or os.path.realpath(old) == os.path.realpath(self.signed_out.path):
            return
        try:
            self.signed_out.add(SignedOut(old).read() or {})
            os.replace(old, f"{old}.migrated")
        except OSError as e:  # the hub still starts; the next start tries again
            sys.stderr.write(f"{old} not moved to {self.signed_out.path}: {e}\n")

    def users_mtime(self):
        try:
            return os.stat(self.users_path).st_mtime
        except OSError:
            return 0

    def names(self):
        """Every user the file lists; the hub keeps a day log for each."""
        return read_names(self.users_path)

    def cached_basic(self, header):
        """The user name for a password already found right (the auth cache), else None: no check."""
        user, secret = basic_login(header)
        if user is None:
            return None
        hit = self.cache.get(hashlib.sha256(f"{user}:{secret}".encode()).hexdigest())
        return hit[0] if hit and hit[1] > time.time() and hit[2] == self.users_mtime() else None

    def check_basic(self, header):
        """The user name for a valid `Authorization: Basic ...`, else None."""
        user, secret = basic_login(header)
        if user is None or not USER_NAME.fullmatch(user):  # it names a folder here
            return None
        key = hashlib.sha256(f"{user}:{secret}".encode()).hexdigest()
        mtime = self.users_mtime()
        hit = self.cache.get(key)
        if hit and hit[1] > time.time() and hit[2] == mtime:
            return hit[0]
        import bcrypt  # only needed once someone logs in

        try:
            checked, spare = False, None
            with open(self.users_path, encoding="utf-8") as f:
                for line in f:
                    name, _, hashed = line.strip().partition(":")
                    spare = hashed or spare
                    if name == user and hashed:
                        checked = True
                        if bcrypt.checkpw(secret.encode(), hashed.encode()):
                            self.cache[key] = (user, time.time() + AUTH_CACHE_S, mtime)
                            return user
            if not checked and spare:
                # A name that isn't there takes as long as a wrong password for one that is, or
                # the time an answer takes says which names exist. The result is never used.
                bcrypt.checkpw(secret.encode(), spare.encode())
        except (OSError, ValueError):
            pass
        return None

    def password_tag(self, user):
        """A fingerprint of [user]'s hash, so a new password ends the old sessions."""
        try:
            with open(self.users_path, encoding="utf-8") as f:
                for line in f:
                    name, _, hashed = line.strip().partition(":")
                    if name == user and hashed:
                        return hashlib.sha256(hashed.encode()).hexdigest()
        except OSError:
            pass
        return None

    def session_mac(self, body, tag):
        return hmac.new(self.session_key, f"{body}|{tag}".encode(), hashlib.sha256).hexdigest()

    def make_session(self, user):
        who = base64.urlsafe_b64encode(user.encode()).decode().rstrip("=")
        tag = self.password_tag(user)
        expires = int(time.time()) + SESSION_S
        # A cookie is made from the name, the second it runs out and the password alone, so one made
        # in the second another was signed out would be that signed-out cookie. It runs out a second
        # later instead, which keeps the format the other services read.
        listed = self.signed_out.hashes()
        while True:
            body = f"{who}.{expires}"
            token = f"{body}.{self.session_mac(body, tag)}"
            if hashlib.sha256(token.encode()).hexdigest() not in listed:
                return token
            expires += 1

    def check_session(self, cookie_header):
        """The user a request's cookie names, if it's ours, unexpired, not signed out and the
        password unchanged."""
        token = session_token(cookie_header)
        try:
            who, expires, mac = token.split(".")
            user = base64.urlsafe_b64decode(who + "=" * (-len(who) % 4)).decode("utf-8")
            if int(expires) < time.time():
                return None
        except ValueError:  # also a bad base64 or UTF-8
            return None
        if hashlib.sha256(token.encode()).hexdigest() in self.signed_out.hashes():
            return None
        tag = self.password_tag(user)
        if tag is None:
            return None
        try:
            return user if hmac.compare_digest(self.session_mac(f"{who}.{expires}", tag), mac) else None
        except TypeError:  # a mac that isn't ASCII
            return None

    def end_session(self, cookie_header):
        """Signs out the session a request's cookie carries, everywhere and for good: the cookie is
        refused from then on by every server that shares it, wherever a copy of it turns up, until
        it would have run out anyway. The user it was, or None when it was no session of ours."""
        user = self.check_session(cookie_header)
        if not user:
            return None
        token = session_token(cookie_header)
        try:
            self.signed_out.add({hashlib.sha256(token.encode()).hexdigest(): int(token.split(".")[1])})
        except OSError as e:
            sys.stderr.write(f"sign-out not kept: {e}\n")
        return user

    def recent_failures(self, addr, now):
        """This address's wrong logins within FAIL_WINDOW_S; older ones are forgotten. Under fail_lock."""
        recent = [f for f in self.failures.get(addr, ()) if now - f[0] < FAIL_WINDOW_S]
        if recent:
            self.failures[addr] = recent
        else:
            self.failures.pop(addr, None)
        return recent

    def locked_out(self, addr):
        with self.fail_lock:
            return len(self.recent_failures(addr, time.monotonic())) >= FAIL_LIMIT

    def reserve_guess(self, addr):
        """Waits for a place to check a password from this address in; False once the address has
        used up its wrong logins. A login being checked holds a place until settle_guess: counted
        only afterwards, guesses sent at once all passed the limit while the first were still in
        bcrypt. It isn't a wrong login, though: one that finds no place waits for it to be settled
        rather than being turned away, so two devices signing in at once never lock each other out."""
        with self.fail_settled:
            now = time.monotonic()
            if len(self.failures) > 10000:  # a flood from many addresses: forget the stale ones
                for a in [a for a, fs in self.failures.items() if now - fs[-1][0] >= FAIL_WINDOW_S]:
                    del self.failures[a]
            while True:
                wrong = len(self.recent_failures(addr, time.monotonic()))
                if wrong >= FAIL_LIMIT:
                    return False
                if wrong + self.checking.get(addr, 0) < FAIL_LIMIT:
                    self.checking[addr] = self.checking.get(addr, 0) + 1
                    return True
                self.fail_settled.wait(1)

    def settle_guess(self, addr, name, right):
        """A login reserve_guess let through has been checked. A wrong one counts against the
        address, under the name it tried. A right one clears that name's mistakes from this
        address, and no one else's: a device signing in mustn't give a guesser behind the same
        address its guesses back. None (a password already found right, a token, a check that
        broke) settles nothing."""
        name = (name or "")[:64]
        with self.fail_settled:
            left = self.checking.get(addr, 1) - 1
            if left > 0:
                self.checking[addr] = left
            else:
                self.checking.pop(addr, None)
            if right is False:
                self.failures.setdefault(addr, []).append((time.monotonic(), name))
            elif right:
                kept = [f for f in self.failures.get(addr, ()) if f[1] != name]
                if kept:
                    self.failures[addr] = kept
                else:
                    self.failures.pop(addr, None)
            self.fail_settled.notify_all()


def session_token(cookie_header):
    """The xrn_session cookie's value in a Cookie header, or ""."""
    token = ""
    for part in (cookie_header or "").split(";"):
        name, _, value = part.strip().partition("=")
        if name == SESSION_COOKIE:
            token = value
    return token


def basic_login(header):
    """(user, secret) from `Authorization: Basic ...`, or (None, None)."""
    if not header or not header.startswith("Basic "):
        return None, None
    try:
        user, _, secret = base64.b64decode(header[6:]).decode("utf-8").partition(":")
    except Exception:
        return None, None
    return user, secret


def session_cookie(value, max_age):
    # Path=/: the habits web app and the blocks status page share this sign-in.
    return f"{SESSION_COOKIE}={value}; Path=/; Max-Age={max_age}; HttpOnly; Secure; SameSite=Strict"

"""Read-only API tokens: what a script or an assistant holds instead of the password.

The sync password signs in to every xrn service and can write to all of them: replace xrnrule's
blocks, empty a wallet. A token reads /api/v1 on the hub and nothing else, so handing one out risks
what it can see, never what it can change, and revoking it touches no device.

A token reads `xrnhub_<id>_<secret>`. Only a SHA-256 of the secret is kept, in
`<data>/users/<user>/tokens.json`, so a copy of that file signs no one in. The secret is 32 random
bytes, which no guessing reaches, so a fast hash is enough and no bcrypt is paid per request. A
user taken out of the users file loses their tokens with it.

Made and revoked on the server itself, where the data folder is:

    python3 xrnhub.py token add NAME [--user USER] --data D --users U
    python3 xrnhub.py token list [--user USER] --data D --users U
    python3 xrnhub.py token revoke ID [--user USER] --data D --users U

The running server reads the file again when it changes, so a new or revoked token counts at once.
"""
import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import threading
import time

from . import login

PREFIX = "xrnhub_"
TOKEN = re.compile(r"xrnhub_([0-9a-f]{8})_([A-Za-z0-9_-]{43})")
TOKEN_ID = re.compile(r"[0-9a-f]{8}")
NAME = re.compile(r"[^\x00-\x1f\x7f]{1,60}")
MAX_TOKENS = 50


def digest(secret):
    return hashlib.sha256(secret.encode()).hexdigest()


class Tokens:
    def __init__(self, data_dir, names):
        self.data_dir = data_dir
        self.names = names  # the users file's users, read fresh each time
        self.lock = threading.Lock()
        self.cache = {}  # user -> (mtime, tokens)

    def path(self, user):
        return os.path.join(self.data_dir, "users", user, "tokens.json")

    def read(self, user):
        path = self.path(user)
        try:
            stamp = os.stat(path).st_mtime_ns
        except OSError:
            return []
        with self.lock:
            hit = self.cache.get(user)
            if hit and hit[0] == stamp:
                return hit[1]
        try:
            with open(path, encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, ValueError):
            doc = None
        found = doc.get("tokens") if isinstance(doc, dict) else None
        found = [t for t in found if isinstance(t, dict)] if isinstance(found, list) else []
        with self.lock:
            self.cache[user] = (stamp, found)
        return found

    def write(self, user, found):
        path = self.path(user)
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"tokens": found}, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)

    def user_for(self, header):
        """The user a `Authorization: Bearer xrnhub_...` header belongs to, else None."""
        if not header or not header.startswith("Bearer "):
            return None
        m = TOKEN.fullmatch(header[7:].strip())
        if not m:
            return None
        token_id, secret = m.groups()
        want = digest(secret)
        for user in self.names():
            for t in self.read(user):
                if t.get("id") == token_id and hmac.compare_digest(str(t.get("sha256", "")), want):
                    return user
        return None

    def add(self, user, name):
        """A new token's full text, shown once; only its hash is kept."""
        if not NAME.fullmatch(name):
            raise ValueError("a name is 1 to 60 characters, with no control characters")
        found = self.read(user)
        if len(found) >= MAX_TOKENS:
            raise ValueError(f"{user} already has {MAX_TOKENS} tokens; revoke one first")
        taken = {t.get("id") for t in found}
        token_id = secrets.token_hex(4)
        while token_id in taken:
            token_id = secrets.token_hex(4)
        secret = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")
        entry = {"id": token_id, "name": name, "sha256": digest(secret), "created": int(time.time())}
        self.write(user, [*found, entry])
        return f"{PREFIX}{token_id}_{secret}"

    def revoke(self, user, token_id):
        found = self.read(user)
        kept = [t for t in found if t.get("id") != token_id]
        if len(kept) == len(found):
            return False
        self.write(user, kept)
        return True


def cli(argv):
    p = argparse.ArgumentParser(prog="xrnhub.py token", description="Read-only tokens for /api/v1.")
    p.add_argument("action", choices=("add", "list", "revoke"))
    p.add_argument("what", nargs="?", help="add: the token's name, for you to recognise it; revoke: its id")
    p.add_argument("--user", help="whose data it reads (default: the first user in the users file)")
    p.add_argument("--data", required=True, help="the hub's data folder")
    p.add_argument("--users", required=True, help="the htpasswd file the hub reads")
    args = p.parse_args(argv)
    names = login.read_names(os.path.expanduser(args.users))
    user = args.user or next(iter(names), None)
    if not user or user not in names:
        sys.exit(f"no such user in {args.users}")
    store = Tokens(os.path.expanduser(args.data), lambda: names)
    if args.action == "add":
        if not args.what:
            sys.exit("give the token a name, such as: token add laptop-scripts")
        try:
            text = store.add(user, args.what)
        except ValueError as e:
            sys.exit(str(e))
        print(text)
        print(f"Reads {user}'s data at /xrnhub/api/v1. It isn't shown again; revoke it with: token revoke {text[7:15]}",
              file=sys.stderr)
    elif args.action == "list":
        for t in store.read(user):
            made = time.strftime("%Y-%m-%d %H:%M", time.localtime(t.get("created", 0)))
            print(f"{t.get('id')}  {made}  {t.get('name')}")
    else:
        if not args.what or not TOKEN_ID.fullmatch(args.what):
            sys.exit("give the id that `token list` shows")
        if not store.revoke(user, args.what):
            sys.exit(f"{user} has no token {args.what}")
        print(f"Revoked {args.what}.", file=sys.stderr)

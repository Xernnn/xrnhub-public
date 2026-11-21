#!/usr/bin/env python3
"""Looks through a tree for what must not reach the public copy of this repo.

    python3 -I scripts/public-scan.py --deny <deny list> <folder>

Every file is read, text or not (a PNG can carry a path in its metadata), and so is every path's
name. It looks for:

- tailnet names: anything under .ts.net but the documentation's example.ts.net;
- tailnet addresses: anything in the range Tailscale hands out, 100.64/10 (the range itself,
  written out with its /10, is fine);
- home folders: any path under /home;
- Gmail addresses;
- keys and secrets: private key blocks, GitHub, AWS, Google, Slack and Telegram bot tokens, the
  hub's own read-only tokens, and key, keystore, password and netrc files by name;
- every line of the deny list: real values that must never be published, such as the owner's
  account name, address and server. It lives outside the repo (chmod 600), so the values aren't in
  this script either, and each is matched without regard to case.

Each finding prints as `path:line: what`, without the value itself, so the output can be pasted
anywhere. Exit 0 when the tree is clean, 1 when something was found, 2 when it couldn't look.
"""
import argparse
import os
import re
import stat
import sys

ALLOWED_TAILNET = "example"  # homeserver.example.ts.net, as the docs write a server's address
TAILNET_NAME = re.compile(rb"((?:[a-z0-9-]+\.)*([a-z0-9-]+))\.ts\.net\b", re.IGNORECASE)
TAILNET_ADDRESS = re.compile(rb"(?<![0-9.])100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.([0-9]{1,3})\.([0-9]{1,3})(?![0-9])(?!/10)")
PATTERNS = (
    ("a home folder (a path under /home)", re.compile(rb"/hom[e]/")),  # [e]: so this line isn't one
    ("a Gmail address", re.compile(rb"[A-Za-z0-9._%+-]+@gmail\.com", re.IGNORECASE)),
    ("a private key", re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("a GitHub token", re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})")),
    ("an AWS key", re.compile(rb"\bAKIA[0-9A-Z]{16}\b")),
    ("a Google API key", re.compile(rb"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("a Slack token", re.compile(rb"\bxox[abposr]-[A-Za-z0-9-]{10,}")),
    ("a Telegram bot token", re.compile(rb"\b[0-9]{8,10}:AA[A-Za-z0-9_-]{33}\b")),
    ("an xrnhub token", re.compile(rb"\bxrnhub_[0-9a-f]{8}_[A-Za-z0-9_-]{43}")),
)
SECRET_FILES = re.compile(
    r"(^|/)(\.env(\..*)?|\.netrc|netrc|id_(rsa|ed25519|ecdsa)|.*\.(pem|key|jks|keystore|p12|pfx|password|token))$",
    re.IGNORECASE,
)
MAX_BYTES = 64 * 1024 * 1024


def read_deny(path):
    """The deny list's entries as lower-case bytes, with their line numbers. Blank lines and lines
    starting with # are skipped. It must be readable by its owner alone."""
    try:
        st = os.stat(path)
    except OSError as e:
        sys.exit(f"public-scan: no deny list at {path} ({e.strerror}). Make one, chmod 600: one real value a line.")
    if st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        print(f"public-scan: {path} can be read by others; chmod 600 it first.", file=sys.stderr)
        sys.exit(2)
    entries = []
    with open(path, "rb") as f:
        for number, raw in enumerate(f, 1):
            line = raw.strip()
            if line and not line.startswith(b"#"):
                entries.append((number, line.lower()))
    if not entries:
        print(f"public-scan: {path} has no entries.", file=sys.stderr)
        sys.exit(2)
    return entries


def line_of(data, offset):
    return data.count(b"\n", 0, offset) + 1


def spans_in(data, deny):
    """(start, end, what) for everything found in [data], the bytes of a file or of a path."""
    found = []
    for match in TAILNET_NAME.finditer(data):
        if match[2].lower() != ALLOWED_TAILNET.encode():
            found.append((match.start(), match.end(), "a tailnet name (*.ts.net)"))
    for match in TAILNET_ADDRESS.finditer(data):
        if all(int(part) <= 255 for part in match.groups()[1:]):
            found.append((match.start(), match.end(), "a tailnet address (in 100.64/10)"))
    for what, pattern in PATTERNS:
        for match in pattern.finditer(data):
            found.append((match.start(), match.end(), what))
    lower = data.lower()
    for number, entry in deny:
        start = lower.find(entry)
        while start != -1:
            found.append((start, start + len(entry), f"deny list line {number}"))
            start = lower.find(entry, start + 1)
    return found


def findings_in(data, deny):
    """(line, what) for everything found in [data]."""
    return [(line_of(data, start), what) for start, _, what in spans_in(data, deny)]


def shown(rel, deny):
    """A path as it can be printed: whatever was found in it is left out, as "..."."""
    name = rel.encode()
    for start, end, _ in sorted(spans_in(name, deny), reverse=True):
        name = name[:start] + b"..." + name[end:]
    return name.decode("utf-8", "replace")


def scan(root, deny):
    """[(path, line or None, what)] for every finding under [root], and how many files were read."""
    found = []
    files = 0
    for folder, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d != ".git")
        names = [n for n in names if n != ".git"]  # a worktree's pointer to its repo; never published
        for name in sorted(dirs) + sorted(names):
            path = os.path.join(folder, name)
            rel = os.path.relpath(path, root)
            named = findings_in(rel.encode(), deny)
            if named:
                rel = shown(rel, deny)
                found += [(rel, None, f"{what}, in its name") for _, what in named]
            if name in dirs:
                continue
            if SECRET_FILES.search(rel):
                found.append((rel, None, "a key or password file, by its name"))
            if os.path.islink(path):
                found.append((rel, None, "a link, which could point anywhere"))
                continue
            size = os.path.getsize(path)
            if size > MAX_BYTES:
                found.append((rel, None, f"too big to read ({size} bytes)"))
                continue
            with open(path, "rb") as f:
                data = f.read()
            files += 1
            found += [(rel, line, what) for line, what in findings_in(data, deny)]
    return found, files


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--deny", required=True, help="the deny list: real values, one a line (chmod 600)")
    p.add_argument("root", help="the folder to look through")
    args = p.parse_args()
    deny = read_deny(args.deny)
    if not os.path.isdir(args.root):
        print(f"public-scan: {args.root} isn't a folder", file=sys.stderr)
        return 2
    found, files = scan(args.root, deny)
    for path, line, what in found:
        print(f"{path}:{line}: {what}" if line else f"{path}: {what}")
    print(f"public-scan: {files} files read, {len(deny)} deny-list entries, "
          + (f"{len(found)} found" if found else "nothing found"))
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())

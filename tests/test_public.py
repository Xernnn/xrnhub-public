"""scripts/public-scan.py and scripts/publish-public.sh on made-up trees: what the scan stops, that it
never prints what it found, and a snapshot landing in a public clone as one commit by Xernnn.

    cd tests && python3 -m unittest test_public

The private-looking values below are put together as the tests run, so this file passes the scan.
"""
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

import support

SCRIPTS = os.path.join(support.ROOT, "scripts")
SCAN = os.path.join(SCRIPTS, "public-scan.py")
PUBLISH = os.path.join(SCRIPTS, "publish-public.sh")
DENIED = "alex" + "-private-" + "value"
PLANTED = {
    "tailnet name": "box." + "tail0000" + ".ts.net",
    "tailnet address": ".".join(("100", "101", "7", "9")),
    "home folder": "/ho" + "me/alex/notes",
    "Gmail address": "alex.example" + "@" + "gmail.com",
    "private key": "-----BEGIN OPENSSH " + "PRIVATE KEY-----",
    "deny list": DENIED,
}


def write(path, text, mode="w"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, mode) as f:
        f.write(text)


class Scan(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="xrnhub-public-")
        self.addCleanup(shutil.rmtree, self.root)
        self.tree = os.path.join(self.root, "tree")
        self.deny = os.path.join(self.root, "deny.txt")
        write(self.deny, f"# a comment\n\n{DENIED.upper()}\n")
        os.chmod(self.deny, 0o600)
        tailnet_range = ".".join(("100", "64", "0", "0")) + "/10"
        write(os.path.join(self.tree, "README.md"),
              f"Type homeserver.example.ts.net. Tailscale hands out {tailnet_range}. Gmail and /home are words.\n")

    def scan(self):
        return subprocess.run([sys.executable, "-I", SCAN, "--deny", self.deny, self.tree],
                              capture_output=True, text=True, timeout=60)

    def test_a_clean_tree_passes(self):
        done = self.scan()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("nothing found", done.stdout)

    def test_each_private_thing_is_found_where_it_is_and_never_printed(self):
        for i, value in enumerate(PLANTED.values()):
            write(os.path.join(self.tree, "src", f"f{i}.txt"), f"fine\nfine\n{value}\n")
        write(os.path.join(self.tree, "docs", "shot.png"), b"\x89PNG\r\n\x00" + PLANTED["home folder"].encode() + b"\x00",
              mode="wb")
        write(os.path.join(self.tree, "release.jks"), "")
        write(os.path.join(self.tree, DENIED, "a.txt"), "")  # in a name, too
        done = self.scan()
        self.assertEqual(done.returncode, 1)
        lines = done.stdout.splitlines()
        for i, kind in enumerate(PLANTED):
            self.assertTrue(any(line.startswith(f"src/f{i}.txt:3: ") and kind in line for line in lines), (kind, lines))
        self.assertTrue(any(line.startswith("docs/shot.png:") and "a home folder" in line for line in lines), lines)
        self.assertIn("release.jks: a key or password file, by its name", lines)
        self.assertIn("deny list line 3", done.stdout)
        for value in PLANTED.values():
            self.assertNotIn(value.lower(), done.stdout.lower())

    def test_a_deny_list_others_can_read_is_refused(self):
        os.chmod(self.deny, 0o644)
        done = self.scan()
        self.assertEqual(done.returncode, 2)
        self.assertIn("chmod 600", done.stderr)

    def test_no_deny_list_is_refused(self):
        os.remove(self.deny)
        self.assertNotEqual(self.scan().returncode, 0)


@unittest.skipUnless(shutil.which("git") and shutil.which("sh") and shutil.which("tar"), "needs git, sh and tar")
class Publish(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="xrnhub-publish-")
        self.addCleanup(shutil.rmtree, self.root)
        home = os.path.join(self.root, "home")
        os.makedirs(home)
        self.env = {
            **os.environ, "HOME": home, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "Alex", "GIT_AUTHOR_EMAIL": "alex@example.com",
            "GIT_COMMITTER_NAME": "Alex", "GIT_COMMITTER_EMAIL": "alex@example.com",
            "PUBLIC_DENY": os.path.join(self.root, "deny.txt"),
        }
        write(self.env["PUBLIC_DENY"], DENIED + "\n")
        os.chmod(self.env["PUBLIC_DENY"], 0o600)
        # A private working repo, with a history of its own and a folder kept out of the copy.
        self.private = os.path.join(self.root, "private")
        os.makedirs(self.private)
        self.git(self.private, "init", "-q", "-b", "main")
        os.makedirs(os.path.join(self.private, "scripts"))
        for name in ("publish-public.sh", "public-scan.py"):
            shutil.copy2(os.path.join(SCRIPTS, name), os.path.join(self.private, "scripts", name))
        write(os.path.join(self.private, ".publicignore"), "# left out\n.publicignore\nnotes\n")
        write(os.path.join(self.private, "notes", "plan.md"), "kept private, though nothing in it is denied\n")
        write(os.path.join(self.private, "app.py"), "print('first')\n")
        self.git(self.private, "add", "-A")
        self.git(self.private, "commit", "-q", "-m", "First")
        write(os.path.join(self.private, "app.py"), "print('second')\n")
        self.git(self.private, "commit", "-q", "-am", "Second")
        self.public = os.path.join(self.root, "public")
        self.git(self.root, "init", "-q", "-b", "main", self.public)

    def git(self, where, *args):
        return subprocess.run(["git", "-C", where, *args], env=self.env, check=True, capture_output=True,
                              text=True).stdout

    def publish(self, ref="main"):
        return subprocess.run(["sh", os.path.join(self.private, "scripts", "publish-public.sh"), ref, self.public],
                              env=self.env, capture_output=True, text=True, timeout=120)

    def test_a_snapshot_is_one_commit_by_xernnn_without_the_history_or_what_is_left_out(self):
        done = self.publish()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        log = self.git(self.public, "log", "--format=%an <%ae>|%cn <%ce>|%s").splitlines()
        noreply = "Xernnn <117729152+Xernnn@users.noreply.github.com>"
        self.assertEqual(log, [f"{noreply}|{noreply}|Snapshot of the working repo as of "
                               + self.git(self.private, "show", "-s", "--format=%cs").strip()])
        files = set(self.git(self.public, "ls-files").split())
        self.assertEqual(files, {"app.py", "scripts/public-scan.py", "scripts/publish-public.sh"})
        with open(os.path.join(self.public, "app.py")) as f:
            self.assertEqual(f.read(), "print('second')\n")
        self.assertTrue(os.access(os.path.join(self.public, "scripts", "publish-public.sh"), os.X_OK))

    def test_publishing_again_adds_a_commit_only_when_something_changed(self):
        self.assertEqual(self.publish().returncode, 0)
        again = self.publish()
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn("Nothing changed", again.stdout)
        os.remove(os.path.join(self.private, "app.py"))
        write(os.path.join(self.private, "lib", "app.py"), "print('moved')\n")
        self.git(self.private, "add", "-A")
        self.git(self.private, "commit", "-q", "-m", "Moved")
        self.assertEqual(self.publish().returncode, 0)
        self.assertEqual(len(self.git(self.public, "log", "--format=%h").split()), 2)
        self.assertFalse(os.path.exists(os.path.join(self.public, "app.py")))
        self.assertTrue(os.path.exists(os.path.join(self.public, "lib", "app.py")))

    def test_a_snapshot_holding_a_denied_value_is_not_published(self):
        write(os.path.join(self.private, "app.py"), f"user = '{DENIED}'\n")
        self.git(self.private, "commit", "-q", "-am", "Oops")
        done = self.publish()
        self.assertEqual(done.returncode, 1)
        self.assertIn("app.py:1: deny list line 1", done.stdout)
        self.assertIn("Not published", done.stderr)
        self.assertEqual(os.listdir(self.public), [".git"])

    def test_only_committed_work_goes(self):
        write(os.path.join(self.private, "app.py"), f"user = '{DENIED}'  # not committed\n")
        done = self.publish()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        with open(os.path.join(self.public, "app.py")) as f:
            self.assertEqual(f.read(), "print('second')\n")


if __name__ == "__main__":
    unittest.main()

"""server/deploy.sh against a made-up server: ssh, sv, curl, tailscale and pip are stand-ins that run
here, and so is its first step, the test suite, which runs on its own.

    cd tests && python3 -m unittest test_deploy

It installs a fresh service and updates one, and what it writes on the "phone" is read back.
"""
import os
import shutil
import stat
import subprocess
import tempfile
import unittest

import support

DEPLOY = os.path.join(support.ROOT, "server", "deploy.sh")

FAKES = {
    # ssh HOST SCRIPT: the script runs here, in the made-up home, as it would on the phone.
    "ssh": '#!/bin/sh\nshift\nexec sh -c "$*"\n',
    "sv": '#!/bin/sh\necho "sv $*" >> "$HOME/calls"\n',
    "curl": "#!/bin/sh\nprintf ok\n",
    "tailscale": '#!/bin/sh\necho "tailscale $*" >> "$HOME/calls"\necho "Funnel on"\n',
    "pip": '#!/bin/sh\necho "pip $*" >> "$HOME/calls"\n',
    "python3": "#!/bin/sh\nexit 0\n",  # the suite, before anything is copied
}


@unittest.skipUnless(shutil.which("sh") and shutil.which("tar"), "needs sh and tar")
class Deploy(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="xrnhub-deploy-")
        self.addCleanup(shutil.rmtree, self.root)
        self.home = os.path.join(self.root, "home")
        self.prefix = os.path.join(self.root, "usr")
        self.bin = os.path.join(self.root, "bin")
        for folder in (self.home, os.path.join(self.prefix, "var", "service"), self.bin):
            os.makedirs(folder)
        for name, body in FAKES.items():
            path = os.path.join(self.bin, name)
            with open(path, "w") as f:
                f.write(body)
            os.chmod(path, stat.S_IRWXU)

    def deploy(self):
        env = {**os.environ, "HOME": self.home, "PREFIX": self.prefix, "PATH": f"{self.bin}:{os.environ['PATH']}"}
        return subprocess.run(["sh", DEPLOY, "phone"], env=env, capture_output=True, text=True, timeout=120)

    def calls(self):
        with open(os.path.join(self.home, "calls")) as f:
            return f.read().splitlines()

    def service_file(self, *parts):
        with open(os.path.join(self.prefix, "var", "service", "xrnhub", *parts)) as f:
            return f.read()

    def test_a_fresh_install_guards_its_port_before_starting(self):
        done = self.deploy()
        self.assertEqual(done.returncode, 0, done.stderr)
        app = os.path.join(self.home, "xrnhub")
        for part in ("xrnhub.py", os.path.join("hublib", "machine.py"), os.path.join("web", "index.html")):
            self.assertTrue(os.path.isfile(os.path.join(app, part)), part)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(app, "data")).st_mode), 0o700)
        run = self.service_file("run")
        self.assertTrue(run.startswith(f"#!{self.prefix}/bin/sh\n"))
        # sv-guard clears a copy left running by a dead supervisor, then the hub starts on its port.
        self.assertIn(f"\n{self.home}/.local/bin/sv-guard 5238 {self.home}/xrnhub/xrnhub.py || exit 1\n"
                      f"exec python {self.home}/xrnhub/xrnhub.py \\\n  --port 5238 --data {self.home}/xrnhub/data \\\n"
                      f"  --users {self.home}/.config/xrn-sync/users --home {self.home} 2>&1\n", run)
        self.assertEqual(self.service_file("log", "run"),
                         f'#!{self.prefix}/bin/sh\nmkdir -p "{self.prefix}/var/log/sv/xrnhub"\n'
                         f'exec svlogd -tt "{self.prefix}/var/log/sv/xrnhub"\n')
        self.assertFalse([n for n in os.listdir(os.path.join(self.prefix, "var", "service")) if n.startswith(".")],
                         "no half-built copy")
        self.assertIn("tailscale funnel --bg --set-path /xrnhub http://127.0.0.1:5238", self.calls())
        self.assertNotIn("serve", " ".join(self.calls()))

    def test_an_update_restarts_and_leaves_the_data_and_run_script_alone(self):
        self.assertEqual(self.deploy().returncode, 0)
        kept = os.path.join(self.home, "xrnhub", "data", "keep")
        with open(kept, "w") as f:
            f.write("the owner's")
        run = self.service_file("run")
        done = self.deploy()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("sv restart xrnhub", self.calls())
        with open(kept) as f:
            self.assertEqual(f.read(), "the owner's")
        self.assertEqual(self.service_file("run"), run)


if __name__ == "__main__":
    unittest.main()

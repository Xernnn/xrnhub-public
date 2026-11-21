"""The real server on a free port, driven over HTTP."""
import base64
import concurrent.futures
import hashlib
import http.client
import io
import json
import os
import re
import shutil
import socket
import sqlite3
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import date, datetime, time as dtime, timedelta
from unittest import mock

import bcrypt

import support
from support import USER, habit, make_loops, make_rule

import xrnhub
from hublib import log, login, logscan, machine, rules, sources

PW = "correct horse"


class Server(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.mkdtemp(prefix="xrnhub-server-")
        cls.home = os.path.join(cls.folder, "home")
        os.makedirs(cls.home)
        conf = os.path.join(cls.folder, "conf")
        os.makedirs(conf)
        cls.users = os.path.join(conf, "users")
        with open(cls.users, "w") as f:
            f.write(f"{USER}:{bcrypt.hashpw(PW.encode(), bcrypt.gensalt(4)).decode()}\n")
        make_loops(cls.home, [habit("a", "Gym")], [])
        make_rule(cls.home, {"blocks": {}, "settings": {"overridePhrase": "secret sentence"}})
        cls.hub = xrnhub.Hub(os.path.join(cls.folder, "data"), cls.users, cls.home, os.path.join(support.ROOT, "web"))
        cls.hub.source(USER).calendar.sweep()
        xrnhub.Handler.hub = cls.hub
        login.FAIL_DELAY_S = 0
        cls.srv = xrnhub.HubServer(("127.0.0.1", 0), xrnhub.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        shutil.rmtree(cls.folder)

    def setUp(self):
        self.hub.login.failures.clear()

    def call(self, method, path, body=None, headers=None, auth=True):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = dict(headers or {})
        if auth:
            h["Authorization"] = "Basic " + base64.b64encode(f"{USER}:{PW}".encode()).decode()
        data = json.dumps(body).encode() if body is not None else None
        c.request(method, path, body=data, headers=h)
        r = c.getresponse()
        out = r.read()
        got = {k.lower(): v for k, v in r.getheaders()}
        c.close()
        return r.status, out, got

    def test_health_needs_no_login(self):
        self.assertEqual(self.call("GET", "/health", auth=False)[:2], (200, b"ok"))
        self.assertEqual(self.call("GET", "/xrnhub/health", auth=False)[:2], (200, b"ok"))

    def test_api_needs_login(self):
        for path in ("/api/today", "/api/day?date=2026-01-01", "/api/review", "/api/services", "/api/rules"):
            self.assertEqual(self.call("GET", path, auth=False)[0], 401, path)

    def test_writes_need_login(self):
        self.assertEqual(self.call("POST", "/api/events", {"events": []}, auth=False)[0], 401)
        self.assertEqual(self.call("POST", "/api/nudges/dismiss", {"rule": "x"}, auth=False)[0], 401)
        self.assertEqual(self.call("POST", "/api/health", {"source": "gadgetbridge", "delete": ["x"]}, auth=False)[0], 401)
        self.assertEqual(self.call("PUT", "/api/rules", {"rules": []}, {"If-Match": '"0"'}, auth=False)[0], 401)

    def test_wrong_password_is_refused_and_counted(self):
        wrong = {"Authorization": "Basic " + base64.b64encode(f"{USER}:nope".encode()).decode()}
        for _ in range(10):
            self.assertEqual(self.call("GET", "/api/today", headers=wrong, auth=False)[0], 401)
        self.assertEqual(self.call("GET", "/api/today", headers=wrong, auth=False)[0], 429)

    def test_a_name_that_isnt_there_costs_a_check_like_one_that_is(self):
        def checks(name):
            header = "Basic " + base64.b64encode(f"{name}:nope".encode()).decode()
            with mock.patch.object(bcrypt, "checkpw", wraps=bcrypt.checkpw) as checkpw:
                self.assertIsNone(self.hub.login.check_basic(header))
            return checkpw.call_count
        self.assertEqual(checks(USER), 1)
        self.assertEqual(checks("nobody"), 1)  # none: the answer came sooner, and said so
        self.assertIsNone(self.hub.login.check_basic("Basic " + base64.b64encode(f"nobody:{PW}".encode()).decode()))

    def test_a_request_with_no_password_isnt_held(self):
        # The hold after a wrong password slows a guesser down. One that sent no password guessed
        # nothing, and was held all the same once its address was locked out.
        def timed(addr, auth):
            headers = {"X-Forwarded-For": addr}
            if auth:
                headers["Authorization"] = "Basic " + base64.b64encode(f"{USER}:nope".encode()).decode()
            start = time.monotonic()
            status = self.call("GET", "/api/today", headers=headers, auth=False)[0]
            return status, time.monotonic() - start
        self.hub.login.failures["203.0.113.61"] = [(time.monotonic(), USER)] * login.FAIL_LIMIT  # locked out
        with mock.patch.object(login, "FAIL_DELAY_S", 0.5):
            for addr, status in (("203.0.113.60", 401), ("203.0.113.61", 429)):
                got, took = timed(addr, False)
                self.assertEqual(got, status)
                self.assertLess(took, 0.4, addr)
                got, took = timed(addr, True)
                self.assertEqual(got, status)
                self.assertGreaterEqual(took, 0.5, addr)

    def test_guesses_sent_at_once_still_stop_at_the_limit(self):
        def slow_wrong(header):  # bcrypt takes a while: every guess is in flight at once
            time.sleep(0.3)
            return None

        wrong = {"Authorization": "Basic " + base64.b64encode(f"{USER}:nope".encode()).decode()}
        limit, n = 3, 12
        start = threading.Barrier(n)

        def guess(_):
            start.wait()
            return self.call("GET", "/api/today", headers=wrong, auth=False)[0]

        with mock.patch.object(self.hub.login, "check_basic", slow_wrong), mock.patch.object(login, "FAIL_LIMIT", limit):
            with concurrent.futures.ThreadPoolExecutor(n) as pool:
                statuses = list(pool.map(guess, range(n)))
        self.assertEqual(statuses.count(401), limit)
        self.assertEqual(statuses.count(429), n - limit)

    def guess_as(self, name, password, addr):
        headers = {"Authorization": "Basic " + base64.b64encode(f"{name}:{password}".encode()).decode(),
                   "X-Forwarded-For": addr}
        return self.call("GET", "/api/today", headers=headers, auth=False)[0]

    def test_a_right_login_clears_only_its_own_users_mistakes(self):
        # A login for one account wiped the whole address's count: guesses at another name from
        # the same address started over with every login of the first.
        addr = "203.0.113.80"
        for _ in range(5):
            self.assertEqual(self.guess_as("admin", "nope", addr), 401)
        for _ in range(4):
            self.assertEqual(self.guess_as(USER, "nope", addr), 401)
        self.hub.login.cache.clear()  # typed in, not the copy a device keeps sending
        self.assertEqual(self.guess_as(USER, PW, addr), 200)  # the tester's four go, admin's five stay
        for _ in range(login.FAIL_LIMIT - 5):
            self.assertEqual(self.guess_as("admin", "nope", addr), 401)
        self.assertEqual(self.guess_as("admin", "nope", addr), 429)

    def test_a_device_sending_the_password_it_had_clears_nothing(self):
        # A device at the same address as a guesser (home Wi-Fi, a carrier's shared address)
        # cleared the guesser's count with every request, so the guesses never ran out. A password
        # already found right (the auth cache) is a session, not a login, as a cookie is.
        addr = "203.0.113.81"
        self.assertEqual(self.guess_as(USER, PW, addr), 200)  # the device, signed in
        for _ in range(login.FAIL_LIMIT - 1):
            self.assertEqual(self.guess_as(USER, "nope", addr), 401)
            self.assertEqual(self.guess_as(USER, PW, addr), 200)  # it syncs on
        self.assertEqual(self.guess_as(USER, "nope", addr), 401)
        self.assertEqual(self.guess_as(USER, "nope", addr), 429)

    def test_a_login_in_flight_is_not_a_wrong_guess(self):
        # Counted as wrong before it was checked, a right login being checked took a place: two
        # devices signing in at once, at an address with a few mistakes behind it, got a 429.
        addr = "203.0.113.82"
        real = self.hub.login.check_basic

        def slow(header):
            time.sleep(0.3)
            return real(header)

        with mock.patch.object(login, "FAIL_LIMIT", 3):
            self.assertEqual(self.guess_as(USER, "nope", addr), 401)
            self.assertEqual(self.guess_as(USER, "nope", addr), 401)  # one place left
            self.hub.login.cache.clear()
            start = threading.Barrier(2)

            def sign_in(_):
                start.wait()
                return self.guess_as(USER, PW, addr)

            with mock.patch.object(self.hub.login, "check_basic", slow):
                with concurrent.futures.ThreadPoolExecutor(2) as pool:
                    self.assertEqual(list(pool.map(sign_in, range(2))), [200, 200])

    def guess(self, forwarded):
        wrong = {"Authorization": "Basic " + base64.b64encode(f"{USER}:nope".encode()).decode(),
                 "X-Forwarded-For": forwarded}
        return self.call("GET", "/api/today", headers=wrong, auth=False)[0]

    def test_a_made_up_forwarded_hop_is_not_a_new_address(self):
        for i in range(10):  # what a proxy that appends would pass on: the caller's own header first
            self.assertEqual(self.guess(f"198.51.100.{i}, 203.0.113.20"), 401)
        self.assertEqual(self.guess("198.51.100.200, 203.0.113.20"), 429)
        self.assertEqual(self.guess("203.0.113.20, 203.0.113.21"), 401)  # another caller isn't held

    def test_an_ipv6_guesser_is_its_64_not_each_address_in_it(self):
        for i in range(10):
            self.assertEqual(self.guess(f"2001:db8:1:2::{i + 1:x}"), 401)
        self.assertEqual(self.guess("2001:db8:1:2:aaaa::1"), 429)
        self.assertEqual(self.guess("2001:db8:1:3::1"), 401)  # the /64 next door isn't held
        self.assertEqual(self.guess("::ffff:203.0.113.40"), 401)  # an IPv4 caller written as IPv6
        self.hub.login.failures.pop("203.0.113.40")  # ...is counted as that IPv4 address

    def test_cookie_needs_the_client_header(self):
        cookie = self.call("POST", "/api/session")[2]["set-cookie"].split(";")[0]
        self.assertEqual(self.call("GET", "/api/today", headers={"Cookie": cookie}, auth=False)[0], 401)
        ok = self.call("GET", "/api/today", headers={"Cookie": cookie, "X-Xrn-Client": "t"}, auth=False)
        self.assertEqual(ok[0], 200)

    def test_cookie_settings(self):
        text = self.call("POST", "/api/session")[2]["set-cookie"]
        for part in ("HttpOnly", "Secure", "SameSite=Strict", "Path=/"):
            self.assertIn(part, text)

    def a_cookie(self, expires):
        """A session cookie as make_session mints it, running out at [expires]: two minted in the
        same second are the same cookie, and other tests mint theirs at now + SESSION_S."""
        who = base64.urlsafe_b64encode(USER.encode()).decode().rstrip("=")
        body = f"{who}.{expires}"
        return f"{login.SESSION_COOKIE}={body}.{self.hub.login.session_mac(body, self.hub.login.password_tag(USER))}"

    def test_signing_out_ends_the_session_on_the_server(self):
        # DELETE /api/session only told the browser to drop its cookie: a copy of it (another
        # browser, a stolen one) went on signing in for the rest of its 30 days.
        mine, other = self.a_cookie(int(time.time()) + 4000), self.a_cookie(int(time.time()) + 5000)
        page = {"X-Xrn-Client": "t"}
        self.assertEqual(self.call("GET", "/api/today", headers={"Cookie": mine, **page}, auth=False)[0], 200)
        status, _, got = self.call("DELETE", "/api/session", headers={"Cookie": mine, **page}, auth=False)
        self.assertEqual(status, 200)
        self.assertIn("Max-Age=0", got["set-cookie"])
        self.assertEqual(self.call("GET", "/api/today", headers={"Cookie": mine, **page}, auth=False)[0], 401)
        self.assertEqual(self.call("POST", "/api/session", headers={"Cookie": mine, **page}, auth=False)[0], 401)
        # Another device's session is its own.
        self.assertEqual(self.call("GET", "/api/today", headers={"Cookie": other, **page}, auth=False)[0], 200)
        # The hub restarting doesn't bring it back.
        again = login.Login(self.users, self.hub.data)
        self.assertIsNone(again.check_session(mine))
        self.assertEqual(again.check_session(other), USER)

    def test_signing_out_without_a_session_is_harmless(self):
        for headers in ({}, {"Cookie": "xrn_session=abc.1.def"}, {"Cookie": self.a_cookie(int(time.time()) - 5)}):
            status, _, got = self.call("DELETE", "/xrnhub/api/session", headers=headers, auth=False)
            self.assertEqual(status, 200, headers)
            self.assertIn("Max-Age=0", got["set-cookie"])
        self.assertTrue(all(until > time.time() for until in self.hub.login.signed_out.hashes().values()))

    def shared_list(self):
        """signed-out.json beside the users file: what every server that takes the cookie reads."""
        return os.path.join(os.path.dirname(self.users), "signed-out.json")

    def read_list(self):
        try:
            with open(self.shared_list(), encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return {}

    def write_list(self, doc):
        """The shared list as another server writes it: a new file, put in the old one's place."""
        with open(self.shared_list() + ".test", "w", encoding="utf-8") as f:
            f.write(doc if isinstance(doc, str) else json.dumps(doc))
        os.replace(self.shared_list() + ".test", self.shared_list())

    def test_a_signed_out_session_is_forgotten_once_it_would_have_run_out(self):
        gone = self.a_cookie(int(time.time()) + 6000)
        self.hub.login.end_session(gone)
        self.write_list({k: time.time() - 1 for k in self.read_list()})  # their time is up
        self.hub.login.end_session(self.a_cookie(int(time.time()) + 7000))
        self.assertEqual(len(self.read_list()), 1)

    def test_a_cookie_signed_out_on_another_server_is_refused_here(self):
        # The hub kept its sign-outs to itself and read no one else's, so a cookie signed out on
        # the habits or blocks page went on opening the hub for the rest of its 30 days.
        expires = int(time.time()) + 8000
        cookie = self.a_cookie(expires)
        page = {"Cookie": cookie, "X-Xrn-Client": "t"}
        self.assertEqual(self.call("GET", "/api/today", headers=page, auth=False)[0], 200)
        token = cookie.partition("=")[2]
        self.write_list({**self.read_list(), hashlib.sha256(token.encode()).hexdigest(): expires})
        self.assertEqual(self.call("GET", "/api/today", headers=page, auth=False)[0], 401)
        self.assertEqual(self.call("GET", "/api/today")[0], 200)  # a password still signs in

    def test_signing_out_here_ends_the_cookie_on_the_list_every_server_reads(self):
        expires = int(time.time()) + 8100
        mine = self.a_cookie(expires)
        status = self.call("DELETE", "/api/session", headers={"Cookie": mine, "X-Xrn-Client": "t"}, auth=False)[0]
        self.assertEqual(status, 200)
        self.assertEqual(self.read_list()[hashlib.sha256(mine.partition("=")[2].encode()).hexdigest()], expires)
        self.assertEqual(os.stat(self.shared_list()).st_mode & 0o777, 0o600)
        self.assertFalse(os.path.exists(os.path.join(self.hub.data, "signed-out.json")))

    def test_a_missing_or_broken_list_breaks_no_sign_in(self):
        gone, live = self.a_cookie(int(time.time()) + 8200), self.a_cookie(int(time.time()) + 8300)
        page = {"X-Xrn-Client": "t"}
        self.hub.login.end_session(gone)
        self.write_list("{half a file")
        with mock.patch.object(sys, "stderr", io.StringIO()) as said:
            for _ in range(3):
                self.assertEqual(self.call("GET", "/api/today", headers={"Cookie": live, **page}, auth=False)[0], 200)
                # The list read before the file broke still holds.
                self.assertEqual(self.call("GET", "/api/today", headers={"Cookie": gone, **page}, auth=False)[0], 401)
            self.assertEqual(self.call("GET", "/api/today")[0], 200)
            self.assertEqual(self.call("POST", "/api/session")[0], 200)
        self.assertEqual(said.getvalue().count("signed-out.json"), 1, said.getvalue())  # one line, not one a request
        os.remove(self.shared_list())
        self.assertEqual(self.call("GET", "/api/today", headers={"Cookie": live, **page}, auth=False)[0], 200)
        self.assertEqual(self.call("GET", "/api/today")[0], 200)
        self.assertEqual(self.call("POST", "/api/session")[0], 200)

    def test_a_new_session_is_never_one_another_server_signed_out(self):
        # The cookie made in the second another server signed the same one out was that cookie.
        clock = mock.Mock()
        clock.time.return_value = 1_900_000_100.5
        with mock.patch.object(login, "time", clock):
            first = self.hub.login.make_session(USER)
            self.write_list({**self.read_list(), hashlib.sha256(first.encode()).hexdigest(): 1_900_000_100 + login.SESSION_S})
            again = self.hub.login.make_session(USER)
            self.assertEqual(int(again.split(".")[1]), int(first.split(".")[1]) + 1)
            self.assertEqual(self.hub.login.check_session(f"{login.SESSION_COOKIE}={again}"), USER)

    def test_servers_signing_out_at_once_keep_every_sign_out(self):
        # Each server wrote its own whole list, so of two signing out together, one undid the other.
        go = os.path.join(self.folder, "go")
        script = ("import os, sys, time\n"
                  "sys.path.insert(0, sys.argv[1])\n"
                  "from hublib import login\n"
                  "out = login.SignedOut(sys.argv[2])\n"
                  "while not os.path.exists(sys.argv[4]): time.sleep(0.001)\n"
                  "for i in range(30): out.add({f'{sys.argv[3]}-{i}': 4_000_000_000})\n")
        servers = [subprocess.Popen([sys.executable, "-c", script, os.path.join(support.ROOT, "server"),
                                     self.shared_list(), f"server{n}", go]) for n in range(3)]
        time.sleep(0.3)
        open(go, "w").close()

        def hub_signs_out(n):
            for i in range(30):
                self.hub.login.signed_out.add({f"hub{n}-{i}": 4_000_000_000})

        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            list(pool.map(hub_signs_out, range(2)))
        for server in servers:
            self.assertEqual(server.wait(timeout=60), 0)
        os.remove(go)
        listed = self.read_list()
        for who in ("server0", "server1", "server2", "hub0", "hub1"):
            self.assertEqual(sum(k.startswith(who + "-") for k in listed), 30, who)
        self.write_list({k: v for k, v in listed.items() if v != 4_000_000_000})

    def test_the_hubs_own_list_from_before_moves_onto_the_shared_one(self):
        # Before, the hub kept its sign-outs in its own data folder, where no other server looked.
        folder = tempfile.mkdtemp(prefix="xrnhub-moving-")
        self.addCleanup(shutil.rmtree, folder)
        conf, data = os.path.join(folder, "conf"), os.path.join(folder, "data")
        os.makedirs(conf)
        os.makedirs(data)
        users = os.path.join(conf, "users")
        shutil.copy(self.users, users)
        with open(os.path.join(conf, "session.key"), "wb") as f:
            f.write(self.hub.login.session_key)
        later = int(time.time()) + 9000
        gone, live = self.a_cookie(later), self.a_cookie(later + 1)
        old = os.path.join(data, "signed-out.json")
        with open(old, "w", encoding="utf-8") as f:
            json.dump({hashlib.sha256(gone.partition("=")[2].encode()).hexdigest(): later, "a" * 64: int(time.time()) - 5}, f)
        shared = os.path.join(conf, "signed-out.json")
        with open(shared, "w", encoding="utf-8") as f:
            json.dump({"b" * 64: later}, f)
        moved = login.Login(users, data)
        with open(shared, encoding="utf-8") as f:  # what the other servers read; the run-out one is gone
            self.assertEqual(json.load(f), {hashlib.sha256(gone.partition("=")[2].encode()).hexdigest(): later, "b" * 64: later})
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(old + ".migrated"))
        self.assertIsNone(moved.check_session(gone))
        self.assertEqual(moved.check_session(live), USER)
        # Started again, there is nothing more to move, and the list stays as it was.
        self.assertIsNone(login.Login(users, data).check_session(gone))
        with open(shared, encoding="utf-8") as f:
            self.assertEqual(len(json.load(f)), 2)

    def test_signing_in_in_the_second_of_a_sign_out_gets_a_session_that_works(self):
        # A cookie is made from the name, the second it runs out and the password, so a sign-in in
        # the same second as a sign-out was handed the very cookie just signed out, and refused at
        # once. The laptop app signs in again that fast when a page signs itself out.
        clock = mock.Mock()
        clock.time.return_value = 1_900_000_000.25
        with mock.patch.object(login, "time", clock):
            first = self.hub.login.make_session(USER)
            self.assertEqual(self.hub.login.end_session(f"{login.SESSION_COOKIE}={first}"), USER)
            again = self.hub.login.make_session(USER)
            self.assertNotEqual(again, first)
            self.assertEqual(self.hub.login.check_session(f"{login.SESSION_COOKIE}={again}"), USER)
            # Signed out in that second as well, the next one is new again.
            self.hub.login.end_session(f"{login.SESSION_COOKIE}={again}")
            third = self.hub.login.make_session(USER)
            self.assertNotIn(third, (first, again))
            self.assertEqual(self.hub.login.check_session(f"{login.SESSION_COOKIE}={third}"), USER)

    def test_a_forged_cookie_fails(self):
        status = self.call("GET", "/api/today", headers={"Cookie": "xrn_session=abc.1.def", "X-Xrn-Client": "t"}, auth=False)[0]
        self.assertEqual(status, 401)

    def test_page_fetch_gets_no_password_box(self):
        got = self.call("GET", "/api/today", headers={"X-Xrn-Client": "t"}, auth=False)
        self.assertEqual(got[0], 401)
        self.assertNotIn("www-authenticate", got[2])

    def test_today_keeps_the_override_sentence_in(self):
        status, body, _ = self.call("GET", "/api/today")
        self.assertEqual(status, 200)
        self.assertNotIn(b"secret sentence", body)

    def test_rules_round_trip_with_if_match(self):
        status, body, headers = self.call("GET", "/api/rules")
        self.assertEqual(status, 200)
        doc = json.loads(body)
        self.assertEqual(len(doc["rules"]), 6)
        # The list of what fired lately was the Nudges page's, and went with it.
        self.assertEqual(set(doc), {"rules"})
        tag = headers["etag"]
        self.assertEqual(self.call("PUT", "/api/rules", {"rules": doc["rules"][:2]})[0], 428)
        status, _, headers = self.call("PUT", "/api/rules", {"rules": doc["rules"][:2]}, {"If-Match": tag})
        self.assertEqual(status, 200)
        self.assertEqual(self.call("PUT", "/api/rules", {"rules": []}, {"If-Match": tag})[0], 412)
        self.assertEqual(len(json.loads(self.call("GET", "/api/rules")[1])["rules"]), 2)
        self.call("PUT", "/api/rules", {"rules": doc["rules"]}, {"If-Match": headers["etag"]})

    def test_a_bad_rule_changes_nothing(self):
        _, body, headers = self.call("GET", "/api/rules")
        status = self.call("PUT", "/api/rules", {"rules": [{"id": "x"}]}, {"If-Match": headers["etag"]})[0]
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(self.call("GET", "/api/rules")[1])["rules"], json.loads(body)["rules"])

    def test_events_are_taken_and_checked(self):
        good = {"events": [{"app": "xrnmail", "kind": "batch", "at": 1793900000000, "detail": {"held": 4}}]}
        self.assertEqual(json.loads(self.call("POST", "/api/events", good)[1]), {"added": 1})
        self.assertEqual(self.call("POST", "/api/events", {"events": [{"app": "evil"}]})[0], 400)
        self.assertEqual(self.call("POST", "/api/events", {"events": []})[0], 400)
        self.assertEqual(self.call("POST", "/api/events", [1])[0], 400)

    def test_an_event_cannot_carry_a_number_no_page_can_read(self):
        event = {"app": "xrnmail", "kind": "state.queue", "at": 1793900000000}
        for bad in ({"value": float("inf")}, {"value": float("nan")}, {"value": 10**30}, {"at": float("inf")},
                    {"at": 10**30}, {"detail": {"held": float("inf")}}):
            self.assertEqual(self.call("POST", "/api/events", {"events": [{**event, **bad}]})[0], 400, bad)
        json.loads(self.call("GET", "/api/today")[1], parse_constant=self.fail)

    def test_health_records_are_taken_checked_and_replaced(self):
        at = int(time.time() * 1000) - 3_600_000
        record = {"id": "w1", "kind": "weight", "start": at, "value": 72.5, "impedance": 500}
        for path in ("/api/health", "/xrnhub/api/health"):
            status, out, _ = self.call("POST", path, {"source": "xrnfit-scale", "upsert": [record]})
            self.assertEqual((status, json.loads(out)), (200, {"upserted": 1, "deleted": 0}), path)
        weighings = json.loads(self.call("GET", "/api/v1/health/weight")[1])["weighings"]
        self.assertEqual([(w["kg"], w["source"]) for w in weighings], [(72.5, "xrnfit-scale")])  # one, not two
        status, out, _ = self.call("POST", "/api/health", {"source": "xrnfit-scale", "upsert": [{**record, "value": 900}]})
        self.assertEqual((status, out), (400, b"bad value for weight"))
        for bad in ([1], {"source": "xrnfit-scale"}, {"source": "Bad Source", "delete": ["w1"]}):
            self.assertEqual(self.call("POST", "/api/health", bad)[0], 400, bad)
        self.assertEqual(json.loads(self.call("POST", "/api/health", {"source": "xrnfit-scale", "delete": ["w1"]})[1]),
                         {"upserted": 0, "deleted": 1})

    def test_a_health_record_cannot_carry_a_number_no_page_can_read(self):
        at = int(time.time() * 1000) - 3_600_000
        steps = {"id": "s", "kind": "steps", "start": at, "end": at + 60_000, "value": 5}
        for bad in ({"value": float("inf")}, {"value": float("nan")}, {"value": 10**30}, {"start": float("inf")},
                    {"start": 10**30}, {"end": float("nan")}):
            self.assertEqual(self.call("POST", "/api/health", {"source": "gadgetbridge", "upsert": [{**steps, **bad}]})[0],
                             400, bad)
        beats = {"id": "h", "kind": "heart_rate", "samples": [[at, float("nan")]]}
        self.assertEqual(self.call("POST", "/api/health", {"source": "gadgetbridge", "upsert": [beats]})[0], 400)
        json.loads(self.call("GET", "/api/v1/health/days")[1], parse_constant=self.fail)

    def test_a_page_on_another_site_cant_write_with_the_browsers_password(self):
        # A browser sends the password it was once given with a form another site posts here, and
        # a text/plain form body can be JSON.
        forged = {"events": [{"app": "xrnforged", "kind": "batch", "at": 1793900000000}]}
        for elsewhere in ({"Sec-Fetch-Site": "cross-site", "Origin": "https://evil.example"},
                          {"Sec-Fetch-Site": "same-site", "Origin": "https://other.homeserver.example"},
                          {"Origin": "https://evil.example"}):  # a browser from before Sec-Fetch-Site
            sent = {"Content-Type": "text/plain", **elsewhere}
            self.assertEqual(self.call("POST", "/api/events", forged, sent)[0], 403, elsewhere)
            self.assertEqual(self.call("POST", "/api/nudges/dismiss", {"rule": "r1"}, sent)[0], 403, elsewhere)
            forged_weight = {"source": "xrnforged", "upsert": [
                {"id": "w", "kind": "weight", "start": int(time.time() * 1000) - 60_000, "value": 70}]}
            self.assertEqual(self.call("POST", "/api/health", forged_weight, sent)[0], 403, elsewhere)
            self.assertEqual(self.call("POST", "/api/session", None, sent)[0], 403, elsewhere)
            self.assertEqual(self.call("PUT", "/api/rules", {"rules": []}, {**sent, "If-Match": '"0"'})[0], 403)
        self.assertIsNone(self.hub.log.latest(USER, "xrnforged"))
        self.assertEqual(self.hub.log.db(USER).execute("SELECT COUNT(*) FROM health WHERE source = 'xrnforged'").fetchone(), (0,))
        # Reads, this page (behind the proxy too), xrnmail, the app and the bar widgets still do.
        self.assertEqual(self.call("GET", "/api/today", headers={"Sec-Fetch-Site": "cross-site"})[0], 200)
        good = {"events": [{"app": "xrnmail", "kind": "batch", "at": 1793900000000}]}
        for ours in ({"Sec-Fetch-Site": "same-origin", "Origin": "https://homeserver.example"},
                     {"Origin": f"http://127.0.0.1:{self.port}"},
                     {"Origin": "https://HomeServer.example", "X-Forwarded-Host": "homeserver.example:443"},
                     {}):
            self.assertEqual(self.call("POST", "/api/events", good, ours)[0], 200, ours)

    def test_an_unparseable_request_gets_an_answer(self):
        with socket.create_connection(("127.0.0.1", self.port), timeout=10) as s:
            s.sendall(b"GARBAGE\r\n\r\n")
            self.assertIn(b"400", s.recv(200))

    def test_the_log_shows_a_callers_control_characters_escaped(self):
        # The request line and X-Forwarded-For are the caller's. Written raw, an escape sequence
        # rewrote the terminal, and a \r started a line of its own where xrnhub reads the log.
        forged = b'2026-11-06_01:09:14.23919 198.51.100.66 "POST /api/changes HTTP/1.1" 200 -'
        with mock.patch("sys.stderr", new_callable=io.StringIO) as log:
            for raw in (b"GET /a\x1b[2Jb HTTP/1.1\r\nX-Forwarded-For: 203.0.113.70\x1b]0;hi\x07\r\nConnection: close\r\n\r\n",
                        b"GET /a\r" + forged + b" HTTP/1.1\r\n\r\n"):
                with socket.create_connection(("127.0.0.1", self.port), timeout=10) as s:
                    s.sendall(raw)
                    while s.recv(4096):
                        pass
        text = log.getvalue()
        self.assertIn("203.0.113.70\\x1b]0;hi\\x07", text)
        self.assertIn("/a\\x1b[2Jb", text)
        self.assertIn("/a\\x0d2026-11-06", text)
        self.assertEqual([], [c for c in text if c != "\n" and (ord(c) < 0x20 or 0x7F <= ord(c) < 0xA0)])

    def test_the_log_names_the_caller_the_lockout_counts(self):
        # The whole X-Forwarded-For went into the line, so a proxy that appends wrote
        # '198.51.100.1, 203.0.113.9 "GET ..."', which the monitor's reader doesn't take for a
        # request: it vanished from traffic, refused counts and callers.
        with mock.patch("sys.stderr", new_callable=io.StringIO) as log:
            self.call("GET", "/api/today", headers={"X-Forwarded-For": "198.51.100.1, 203.0.113.9"}, auth=False)
            self.call("GET", "/health", headers={"X-Forwarded-For": " 203.0.113.10 "}, auth=False)
        lines = log.getvalue().splitlines()
        found = [logscan.parse_request(line) for line in lines]
        self.assertEqual([(r["ip"], r["status"]) for r in found if r], [("203.0.113.9", 401), ("203.0.113.10", 200)],
                         lines)

    def logged(self, talk, settle=1.0):
        """What the server writes to its log while [talk] runs with a socket, and [settle] after.
        Connections time out after 0.3 s here, not a minute."""
        with mock.patch.object(xrnhub.Handler, "timeout", 0.3), \
                mock.patch("sys.stderr", new_callable=io.StringIO) as log:
            with socket.create_connection(("127.0.0.1", self.port), timeout=10) as s:
                talk(s)
                time.sleep(settle)
        return log.getvalue()

    @staticmethod
    def ask(s, path="/health"):
        s.sendall(f"GET {path} HTTP/1.1\r\nHost: hub\r\nX-Forwarded-For: 203.0.113.80\r\n\r\n".encode())
        got = b""
        while not got.endswith(b"ok"):
            got += s.recv(4096)

    def test_an_idle_kept_alive_connection_closes_without_a_word(self):
        # Funnel's proxy keeps a connection open after its request; when it's closed for idling,
        # that was a "Request timed out" line under the last caller's address, 30 times an hour.
        text = self.logged(lambda s: (self.ask(s), self.ask(s)))
        self.assertEqual(text.count('"GET /health HTTP/1.1" 200'), 2)
        self.assertNotIn("timed out", text)

    def test_a_connection_that_never_asks_still_times_out_in_the_log(self):
        self.assertIn("Request timed out", self.logged(lambda s: None))

    def test_a_request_cut_off_halfway_still_times_out_in_the_log(self):
        def talk(s):
            self.ask(s)
            s.sendall(b"GET /health HTTP/1.1\r\nHost: hu")  # and nothing more
        text = self.logged(talk)
        self.assertIn("203.0.113.80 Request timed out", text)

    def test_a_caller_that_resets_is_one_line_not_a_traceback(self):
        def talk(s):
            self.ask(s)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
            s.close()  # with a reset, while the server waits for the next request
        text = self.logged(talk, settle=0.5)
        self.assertIn("127.0.0.1 connection dropped: ConnectionResetError", text)
        self.assertNotIn("Traceback", text)
        self.assertEqual(len([line for line in text.splitlines() if "dropped" in line]), 1)

    def test_only_a_hang_up_is_cut_short(self):
        def fail(error):
            with mock.patch("sys.stderr", new_callable=io.StringIO) as log:
                try:
                    raise error
                except Exception:  # noqa: BLE001
                    self.srv.handle_error(None, ("127.0.0.1", 50000))
            return log.getvalue()

        for error in (ConnectionResetError(104, "Connection reset by peer"), BrokenPipeError(32, "Broken pipe"),
                      TimeoutError("timed out")):
            with self.subTest(error=error):
                text = fail(error)
                self.assertEqual(text, f"127.0.0.1 connection dropped: {error!r}\n")
        text = fail(KeyError("a real bug"))
        self.assertIn("Traceback", text)
        self.assertIn("KeyError: 'a real bug'", text)

    def test_no_answer_names_the_python_version(self):
        # The Server header said "<name>/1 Python/3.x.y" on every answer, error pages included.
        for method in ("GET", "PATCH"):  # PATCH: http.server's own 501 page
            conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
            conn.request(method, "/health")
            res = conn.getresponse()
            res.read()
            conn.close()
            self.assertNotIn("Python", res.getheader("Server"), method)

    def test_big_bodies_are_refused(self):
        auth = "Basic " + base64.b64encode(f"{USER}:{PW}".encode()).decode()
        for path in ("/api/events", "/api/health"):
            c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
            c.request("POST", path, headers={"Authorization": auth, "Content-Length": str(xrnhub.MAX_BODY + 1)})
            self.assertEqual(c.getresponse().status, 413, path)
            c.close()

    def post_raw(self, path, body):
        """A POST with [body] as it is, not made with json.dumps: (status, the answer's text)."""
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        auth = "Basic " + base64.b64encode(f"{USER}:{PW}".encode()).decode()
        c.request("POST", path, body=body, headers={"Authorization": auth})
        with c.getresponse() as res:  # no answer at all raises here
            answer = res.status, res.read().decode()
        c.close()
        return answer

    @staticmethod
    def deep_event(detail, app=b"xrndeep"):
        return b'{"events":[{"app":"' + app + b'","kind":"deep","at":1793900000000,"detail":' + detail + b"}]}"

    def test_a_body_nested_too_deep_gets_an_answer_and_adds_nothing(self):
        # Both fit in MAX_BODY. Past the depth it parses (about 10,000 levels on 3.12, 52,000 on
        # 3.14), json.loads raises RecursionError, which isn't the ValueError body() caught. On 3.14
        # a detail of objects 40,000 deep does parse, and then json.dumps, which stores it, gives up
        # (at about 29,000): no answer either way, and a traceback in the log.
        bodies = {
            "objects 40,000 deep": self.deep_event(b'{"a":' * 40_000 + b"0" + b"}" * 40_000),
            "lists 100,000 deep": self.deep_event(b"[" * 100_000 + b"]" * 100_000),
        }
        for what, body in bodies.items():
            self.assertLess(len(body), xrnhub.MAX_BODY, what)
            with mock.patch("sys.stderr", new_callable=io.StringIO) as logged:
                status, text = self.post_raw("/api/events", body)
            self.assertEqual((400, True), (status, "nested" in text), what)
            self.assertNotIn("Traceback", logged.getvalue(), what)
        self.assertEqual(self.hub.log.between(USER, 0, 2**53, app="xrndeep"), [])

    def test_a_body_nested_up_to_the_limit_is_taken(self):
        def detail(depth):  # {"events": [{"detail": {"a": ...}}]}: the outermost object is level 1
            return b'{"a":' * (depth - 3) + b"0" + b"}" * (depth - 3)
        status, text = self.post_raw("/api/events", self.deep_event(detail(xrnhub.MAX_DEPTH), b"xrnlimit"))
        self.assertEqual((200, {"added": 1}), (status, json.loads(text)))
        status, text = self.post_raw("/api/events", self.deep_event(detail(xrnhub.MAX_DEPTH + 1), b"xrnlimit"))
        self.assertEqual((400, True), (status, "nested" in text))

    def test_a_refused_body_cannot_pass_for_the_next_request(self):
        got = self.call("POST", "/api/events", {"events": []}, auth=False)
        self.assertEqual(got[0], 401)
        self.assertEqual(got[2].get("connection"), "close")
        good = {"events": [{"app": "xrnmail", "kind": "batch", "at": 1793900000000}]}
        self.assertNotIn("connection", self.call("POST", "/api/events", good)[2])

    def test_a_chunked_body_left_unread_closes_the_connection_too(self):
        # Only a Content-Length body counted: a chunked one was left in the pipe, and its chunk-size
        # line was then read as the next request, a stray 400 on the proxy's pooled connection.
        with socket.create_connection(("127.0.0.1", self.port), timeout=10) as s:
            s.sendall(b"POST /api/events HTTP/1.1\r\nHost: hub\r\nTransfer-Encoding: chunked\r\n\r\n"
                      b"2\r\n{}\r\n0\r\n\r\n")
            got = b""
            while chunk := s.recv(4096):
                got += chunk
        self.assertTrue(got.startswith(b"HTTP/1.1 401"), got)
        self.assertIn(b"\r\nConnection: close\r\n", got)
        self.assertEqual(got.count(b"HTTP/1.1 "), 1, got)

    def test_a_day_must_have_happened(self):
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        for query in ("", "?date=nope", f"?date={tomorrow}", "?date=1999-01-01"):
            self.assertEqual(self.call("GET", f"/api/day{query}")[0], 400, query)

    def test_a_dismissed_nudge_stays_gone_for_the_day(self):
        rule = {"id": "always", "name": "Always", "on": True, "when": [{"is": "after", "time": "00:00"}]}
        tag = self.call("GET", "/api/rules")[2]["etag"]
        saved = self.call("PUT", "/api/rules", {"rules": [rule]}, {"If-Match": tag})
        self.assertEqual(saved[0], 200)
        names = lambda: [n["id"] for n in json.loads(self.call("GET", "/api/today")[1])["nudges"]]  # noqa: E731
        self.assertEqual(names(), ["always"])
        self.assertEqual(self.call("POST", "/api/nudges/dismiss", {"rule": "always"})[0], 200)
        self.assertEqual(names(), [])
        self.assertEqual(self.call("POST", "/api/nudges/dismiss", {"rule": "../x"})[0], 400)
        self.call("PUT", "/api/rules", {"rules": rules.starter_rules()}, {"If-Match": saved[2]["etag"]})

    def test_a_rule_turned_off_from_its_card_takes_the_card_off_today(self):
        """What a card's Turn off does: read the rules, save them with that one off under If-Match.
        The card goes and Today counts it as off, so the page lists the rules to turn it back on.
        (Its own rule: one dismissed today, by the test above, shows no card.)"""
        every = {"id": "every-time", "name": "Every time", "on": True, "when": [{"is": "after", "time": "00:00"}]}
        tag = self.call("GET", "/api/rules")[2]["etag"]
        saved = self.call("PUT", "/api/rules", {"rules": [every, *rules.starter_rules()]}, {"If-Match": tag})
        self.assertEqual(saved[0], 200)
        today = lambda: json.loads(self.call("GET", "/api/today")[1])  # noqa: E731
        self.assertIn("every-time", [n["id"] for n in today()["nudges"]])
        self.assertEqual(today()["nudgesOff"], 0)

        _, body, headers = self.call("GET", "/api/rules")
        off = [{**r, "on": r["id"] != "every-time" and r["on"]} for r in json.loads(body)["rules"]]
        # Changed elsewhere since it was read: refused, and nothing changes.
        stale = self.call("PUT", "/api/rules", {"rules": off}, {"If-Match": tag})
        self.assertEqual(stale[0], 412)
        self.assertEqual(stale[2]["etag"], headers["etag"])
        self.assertEqual(today()["nudgesOff"], 0)
        turned = self.call("PUT", "/api/rules", {"rules": off}, {"If-Match": headers["etag"]})
        self.assertEqual(turned[0], 200)
        self.assertNotIn("every-time", [n["id"] for n in today()["nudges"]])
        self.assertEqual(today()["nudgesOff"], 1)
        kept = json.loads(self.call("GET", "/api/rules")[1])["rules"]
        self.assertEqual([r for r in kept if not r["on"]], [{**every, "on": False}])

        back = [{**r, "on": True} if r["id"] == "every-time" else r for r in kept]
        self.assertEqual(self.call("PUT", "/api/rules", {"rules": back}, {"If-Match": turned[2]["etag"]})[0], 200)
        self.assertIn("every-time", [n["id"] for n in today()["nudges"]])
        self.assertEqual(today()["nudgesOff"], 0)
        tag = self.call("GET", "/api/rules")[2]["etag"]
        self.call("PUT", "/api/rules", {"rules": rules.starter_rules()}, {"If-Match": tag})

    def test_review_keeps_the_override_sentence_in(self):
        status, text, _ = self.call("GET", "/api/review")
        self.assertEqual(status, 200)
        doc = json.loads(text)
        self.assertLessEqual({"day", "habits", "hours", "health"}, set(doc))
        self.assertNotIn(b"secret sentence", text)

    def test_page_and_headers(self):
        status, body, headers = self.call("GET", "/xrnhub/", auth=False)
        self.assertEqual(status, 200)
        self.assertIn(b"<title>xrnhub</title>", body)
        self.assertIn("script-src 'self'", headers["content-security-policy"])
        self.assertEqual(headers["x-frame-options"], "DENY")
        self.assertEqual(headers["x-content-type-options"], "nosniff")

    def test_page_revalidates(self):
        _, _, headers = self.call("GET", "/style.css", auth=False)
        status = self.call("GET", "/style.css", headers={"If-None-Match": headers["etag"]}, auth=False)[0]
        self.assertEqual(status, 304)

    def test_a_static_file_is_hashed_again_only_when_it_changes(self):
        # Every request read and hashed its file in full just to answer 304: some 20 files, five
        # fonts among them, per page load.
        with mock.patch.object(xrnhub.hashlib, "sha256", wraps=hashlib.sha256) as hashed:
            tag = self.call("GET", "/manifest.webmanifest", auth=False)[2]["etag"]
            for _ in range(3):
                status = self.call("GET", "/manifest.webmanifest", headers={"If-None-Match": tag}, auth=False)[0]
                self.assertEqual(status, 304)
        self.assertLessEqual(hashed.call_count, 1)
        folder = tempfile.mkdtemp(prefix="xrnhub-etag-")
        self.addCleanup(shutil.rmtree, folder)
        path = os.path.join(folder, "a.js")
        with open(path, "w") as f:
            f.write("one")
        first = xrnhub.etag_of(path)
        with open(path, "w") as f:
            f.write("two")
        os.utime(path, ns=(time.time_ns(), os.stat(path).st_mtime_ns + 10**9))
        self.assertNotEqual(xrnhub.etag_of(path), first)

    def test_no_way_out_of_web(self):
        for path in ("/../server/xrnhub.py", "/fonts/../../server/xrnhub.py", "/%2e%2e/secret", "/.git/config",
                     "/index.py", "/fonts/.hidden.woff2"):
            self.assertEqual(self.call("GET", path, auth=False)[0], 404, path)

    def test_the_page_builds_no_markup_from_strings(self):
        folder = os.path.join(support.ROOT, "web")
        scripts = [n for n in os.listdir(folder) if n.endswith(".js")]
        self.assertIn("app.js", scripts)
        for name in scripts:
            with open(os.path.join(folder, name), encoding="utf-8") as f:
                source = f.read()
            for risky in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
                self.assertNotIn(risky, source, name)

    def test_every_web_file_is_served(self):
        web = os.path.join(support.ROOT, "web")
        names = [n for n in os.listdir(web) if os.path.isfile(os.path.join(web, n))]
        # The fonts' licence note is for people reading the repo; the server serves only web files.
        names += [f"fonts/{n}" for n in os.listdir(os.path.join(web, "fonts")) if n != "README.md"]
        for name in names:
            self.assertEqual(self.call("GET", f"/{name}", auth=False)[0], 200, name)

    def test_rules_run_writes_once(self):
        self.assertIsInstance(self.hub.run_rules(USER), list)
        self.assertEqual(self.hub.run_rules(USER), [])


class Monitor(unittest.TestCase):
    """The monitor's routes, over HTTP, on a made-up phone."""

    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.mkdtemp(prefix="xrnhub-monitor-routes-")
        cls.phone = support.FakePhone(os.path.join(cls.folder, "phone"))
        cls.phone.commands["sv"] = (0, "ok: run\n")
        cls.users = os.path.join(cls.folder, "users")
        hashed = bcrypt.hashpw(PW.encode(), bcrypt.gensalt(4)).decode()
        with open(cls.users, "w") as f:
            f.write(f"{USER}:{hashed}\nguest:{hashed}\n")
        cls.hub = xrnhub.Hub(os.path.join(cls.folder, "data"), cls.users, cls.phone.home,
                             os.path.join(support.ROOT, "web"), machine=cls.phone.machine())
        xrnhub.Handler.hub = cls.hub
        cls.srv = xrnhub.HubServer(("127.0.0.1", 0), xrnhub.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.hub.monitor.minute_tick(cls.hub.monitor.clock())

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.hub.monitor.store.db.close()
        shutil.rmtree(cls.folder)

    def setUp(self):
        xrnhub.Handler.hub = self.hub

    def call(self, method, path, doc=None, user=USER):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {}
        if user:
            headers["Authorization"] = "Basic " + base64.b64encode(f"{user}:{PW}".encode()).decode()
        body = json.dumps(doc) if doc is not None else None
        conn.request(method, path, body, headers)
        res = conn.getresponse()
        data = res.read()
        conn.close()
        return res.status, data

    PAGES = ("now", "history", "services", "traffic", "data", "security", "logs", "logstats")

    def test_every_page_needs_a_login(self):
        for page in self.PAGES:
            self.assertEqual(self.call("GET", f"/api/server/{page}", user=None)[0], 401, page)

    def test_only_the_owner_sees_the_monitor(self):
        for page in self.PAGES:
            self.assertEqual(self.call("GET", f"/api/server/{page}", user="guest")[0], 403, page)

    def test_a_second_account_cannot_restart_anything(self):
        self.assertEqual(self.call("POST", "/api/server/restart", {"service": "xrnloops"}, user="guest")[0], 403)
        self.assertNotIn("sv", [args[0] for args in self.phone.ran])

    def test_the_other_pages_still_work_for_a_second_account(self):
        self.assertEqual(self.call("GET", "/api/today", user="guest")[0], 200)

    def test_every_page_answers_the_owner(self):
        for page in self.PAGES:
            self.assertEqual(self.call("GET", f"/api/server/{page}")[0], 200, page)

    def test_each_range_answers(self):
        for name in ("1h", "6h", "24h", "7d", "30d", "90d"):
            self.assertEqual(self.call("GET", f"/api/server/history?range={name}")[0], 200)
            self.assertEqual(self.call("GET", f"/api/server/traffic?range={name}")[0], 200)

    def test_xrnfit_can_be_asked_for_as_a_service_is(self):
        status, body = self.call("GET", "/api/server/history?range=24h&service=xrnfit")[:2]
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["services"], {})  # no process of its own to chart
        self.assertEqual(self.call("GET", "/api/server/logs?service=xrnfit")[0], 200)
        status, body = self.call("GET", "/api/server/services")[:2]
        self.assertEqual(len(json.loads(body)["fitDays"]), 30)

    def test_bad_filters_are_refused(self):
        for path in ("history?range=2y", "traffic?range=2y", "history?range=1h&service=nope", "logs?service=nope",
                     "logstats?range=2y", "logstats?service=nope",
                     "logs?level=nope", "logs?limit=lots", "now?since=soon"):
            self.assertEqual(self.call("GET", f"/api/server/{path}")[0], 400, path)

    def test_an_unknown_page_is_not_found(self):
        self.assertEqual(self.call("GET", "/api/server/secrets")[0], 404)

    def test_every_service_has_a_logo_that_is_served(self):
        with open(os.path.join(support.ROOT, "web", "serverkit.js"), encoding="utf-8") as f:
            logos = dict(re.findall(r'^  (\w+): "((?:apps/)?[\w.-]+\.(?:svg|png))",$', f.read(), re.M))
        # xrnfit has no service, but the Data page counts the health it sends here as its own.
        self.assertEqual(set(logos), {s.name for s in machine.SERVICES} | {"xrnfit"})
        for path in logos.values():
            self.assertEqual(self.call("GET", f"/{path}", user=None)[0], 200, path)

    def test_the_live_answer_carries_xrnfits_card_kept_until_xrnfit_sends_more(self):
        self.hub.fit_cache.clear()
        card = json.loads(self.call("GET", "/api/server/now?since=0")[1])["health"]
        self.assertEqual(set(card), {"lastIn", "late", "week", "day", "steps", "sleep", "resting", "weight", "spo2", "stress"})
        now_ms = int(time.time() * 1000)
        start = now_ms - 120_000
        if date.fromtimestamp(start / 1000).isoformat() != card["day"]:
            # Between midnight and the habit day's start hour the card still shows the day before,
            # and steps walked since midnight count the next morning: walk them before midnight.
            start = int(datetime.combine(date.fromisoformat(card["day"]), dtime(23, 57)).timestamp() * 1000)
        steps = {"id": "s1", "kind": "steps", "start": start, "end": start + 60_000, "value": 321}
        # Written behind the hub's back, the card a moment ago still stands: the Overview asks every
        # few seconds, and counting the health table each time would cost the phone.
        xrnhub.health.write(self.hub.log, USER, {"source": "xrnfit-watch", "upsert": [steps]})
        self.assertEqual(json.loads(self.call("GET", "/api/server/now?since=0")[1])["health"], card)
        self.assertEqual(self.call("POST", "/api/health", {"source": "xrnfit-watch", "upsert": [steps]})[0], 200)
        fresh = json.loads(self.call("GET", "/xrnhub/api/server/now?since=0")[1])["health"]
        self.assertAlmostEqual(fresh["lastIn"], time.time(), delta=5)
        self.assertFalse(fresh["late"])
        self.assertEqual(fresh["steps"], 321)

    def test_the_headers_down_count_is_the_monitors(self):
        # The header's "N down" probed the ports itself, 1.5 s each, one after another: beside the
        # Overview's "All good" the demo said "7 down", and in an outage a request waited up to
        # ten seconds while every other request arriving ran probes of its own.
        self.hub.service_cache = (0, [])
        self.phone.commands["answers:xrnwal"] = False
        self.addCleanup(self.phone.commands.pop, "answers:xrnwal")
        self.hub.monitor.minute_tick(self.hub.monitor.clock())
        with mock.patch.object(xrnhub, "answers", side_effect=AssertionError("probed a port")):
            services = json.loads(self.call("GET", "/api/services")[1])["services"]
        self.assertEqual([s["name"] for s in services if not s["up"]], ["xrnwal"])
        self.assertNotIn("xrnhub", [s["name"] for s in services])

    def test_a_search_may_hold_spaces(self):
        self.assertEqual(self.call("GET", "/api/server/logs?q=two%20words")[0], 200)

    def test_restarting_goes_through_the_whitelist(self):
        for name in ("xrnhub", "ssh", "tailscale", "nope"):
            self.assertEqual(self.call("POST", "/api/server/restart", {"service": name})[0], 400, name)
        status, data = self.call("POST", "/api/server/restart", {"service": "xrntube"})
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(data)["ok"])
        self.assertEqual(self.call("POST", "/api/server/restart", {"service": "xrntube"})[0], 429)

    def test_a_service_that_isnt_a_name_is_refused(self):
        for service in (["xrntube"], {"name": "xrntube"}, 5, None, True):
            status, _ = self.call("POST", "/api/server/restart", {"service": service})
            self.assertEqual(status, 400, service)
        self.assertNotIn(["sv", "restart"], [c[:2] for c in self.phone.ran])

    def test_a_page_on_another_site_cant_restart_anything(self):
        # A form another site posts here carries the owner's password if the browser has it.
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("POST", "/api/server/restart", json.dumps({"service": "xrnwal"}), {
            "Authorization": "Basic " + base64.b64encode(f"{USER}:{PW}".encode()).decode(),
            "Content-Type": "text/plain", "Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"})
        status = conn.getresponse().status
        conn.close()
        self.assertEqual(status, 403)
        self.assertNotIn(["sv", "restart"], [c[:2] for c in self.phone.ran])


class Connections(unittest.TestCase):
    def test_the_schema_runs_once_not_on_every_new_connection(self):
        # Each connection to the hub is a thread of its own, and each thread's first use of a
        # user's database ran the WAL pragma, every CREATE ... IF NOT EXISTS and a table_info:
        # xrnfit's posts and the widgets' polls each arrive on a fresh connection.
        folder = tempfile.mkdtemp(prefix="xrnhub-pool-")
        self.addCleanup(shutil.rmtree, folder)
        hub_log = log.HubLog(folder)
        ran, real = [], sqlite3.connect

        def connect(*args, **kwargs):
            conn = real(*args, **kwargs)
            conn.set_trace_callback(ran.append)
            return conn

        def first_use(_):
            db = hub_log.db(support.USER)
            db.execute("SELECT COUNT(*) FROM events").fetchone()
            db.close()

        with mock.patch.object(log.sqlite3, "connect", side_effect=connect):
            for i in range(3):  # a thread each, one after another, as connections come
                worker = threading.Thread(target=first_use, args=(i,))
                worker.start()
                worker.join()
        self.assertEqual(len([sql for sql in ran if "CREATE TABLE IF NOT EXISTS events" in sql]), 1)
        self.assertEqual(len([sql for sql in ran if sql == "SELECT COUNT(*) FROM events"]), 3)


class Tick(unittest.TestCase):
    def test_one_bad_user_folder_does_not_stop_the_loop_function(self):
        folder = tempfile.mkdtemp(prefix="xrnhub-tick-")
        self.addCleanup(shutil.rmtree, folder)
        users = os.path.join(folder, "users")
        with open(users, "w") as f:
            f.write(f"{USER}:x\n")
        hub = xrnhub.Hub(os.path.join(folder, "data"), users, folder, folder)
        hub.background_tick()
        self.assertTrue(hub.source(USER).calendar.ready)

    def test_the_calendar_is_swept_every_few_minutes_not_every_tick(self):
        # The tick comes every minute and swept every time, stat-ing some 8,000 files on the phone;
        # CAL_REFRESH_S was there and never used.
        folder = tempfile.mkdtemp(prefix="xrnhub-tick-")
        self.addCleanup(shutil.rmtree, folder)
        users = os.path.join(folder, "users")
        with open(users, "w") as f:
            f.write(f"{USER}:x\n")
        hub = xrnhub.Hub(os.path.join(folder, "data"), users, folder, folder)
        cal = hub.source(USER).calendar
        with mock.patch.object(cal, "sweep", wraps=cal.sweep) as swept:
            hub.background_tick()
            hub.background_tick()
            self.assertEqual(swept.call_count, 1)
            cal.checked -= sources.CAL_REFRESH_S
            hub.background_tick()
            self.assertEqual(swept.call_count, 2)


if __name__ == "__main__":
    unittest.main()

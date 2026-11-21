import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta
from unittest import mock

from support import USER, FakePhone, make_loops, habit, stamp_of, write_json
from hublib import health, logscan, machine, metrics
from hublib.inventory import Inventory
from hublib.log import HubLog
from hublib.sources import Sources
from hublib.watcher import BadRestart, TooSoon, Watcher, HOT_C, MAX_MESSAGES_A_DAY

NOW = int(datetime(2026, 11, 6, 12, 0, 30).timestamp())
METRICS = """# TYPE tailscaled_inbound_bytes_total counter
tailscaled_inbound_bytes_total{path="derp"} 100
tailscaled_inbound_bytes_total{path="direct_ipv4"} 900
tailscaled_outbound_bytes_total{path="derp"} 50
tailscaled_outbound_bytes_total{path="direct_ipv6"} 1950
tailscaled_outbound_packets_total{path="derp"} 7
"""
STATUS = {
    "BackendState": "Running", "Version": "1.2.3-abc", "Health": [],
    "Self": {"Online": True, "Relay": "syd", "PublicKey": "secret-looking"},
    "Peer": {
        "a": {"HostName": "funnel-ingress-node-1", "Online": True},
        "b": {"HostName": "funnel-ingress-node-2", "Online": False},
        "c": {"HostName": "laptop", "OS": "linux", "Online": True, "CurAddr": "198.51.100.4:41641", "Relay": "syd",
              "RxBytes": 5, "TxBytes": 6, "LastSeen": "2026-11-06T00:00:00Z", "PublicKey": "another"},
    },
}
BATTERY = {"percentage": 100, "plugged": "PLUGGED_AC", "status": "FULL", "health": "GOOD", "temperature": 24.0, "voltage": 4406}
WIFI = {"supplicant_state": "COMPLETED", "rssi": -52, "link_speed_mbps": 433, "frequency_mhz": 5220, "ssid": "home", "bssid": "aa:bb"}
FUNNEL = """https://example.ts.net (Funnel on)
|-- /          proxy http://127.0.0.1:5232
|-- /xrnhub    proxy http://127.0.0.1:5238
"""


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="xrnhub-monitor-")
        self.addCleanup(shutil.rmtree, self.root)
        self.phone = FakePhone(self.root)
        self.phone.commands.update({
            "termux-battery-status": (0, json.dumps(BATTERY)),
            "termux-wifi-connectioninfo": (0, json.dumps(WIFI)),
            "tailscale": (0, FUNNEL),
            "getprop": (0, "Phone\n"),
            "sv": (0, "ok: run\n"),
        })
        self.phone.api.update({"/localapi/v0/usermetrics": METRICS, "/localapi/v0/status": json.dumps(STATUS)})
        self.phone.zone(0, "cpu-0-0-usr", 41000)
        self.phone.zone(1, "cpu-1-0-usr", 44500)
        self.phone.zone(2, "battery", 24000)
        self.phone.zone(3, "conn_therm", 194700)
        self.phone.core(0, 900000, 1800000)
        self.phone.core(6, 1150000, 2300000)
        for i, service in enumerate(machine.SERVICES):
            self.phone.service(service.runit, 100 + i, since=NOW - 86400)
        self.phone.supervised(machine.SERVICES)
        self.clock = [NOW]
        self.store = metrics.Store(os.path.join(self.root, "data"))
        self.addCleanup(self.store.db.close)
        self.mac = self.phone.machine()
        self.inventory = Inventory(self.phone.home, lambda: [USER], self.source, self.phone.prefix)
        self.watcher = Watcher(self.mac, self.store, self.inventory, clock=lambda: self.clock[0])

    def source(self, user):
        return Sources(self.phone.home, user)

    def ago(self, **delta):
        return datetime.fromtimestamp(self.clock[0]) - timedelta(**delta)


class Reading(Base):
    def test_memory_comes_in_bytes(self):
        found = self.mac.memory()
        self.assertEqual((found["total"], found["available"]), (8000000 * 1024, 3000000 * 1024))

    def test_memory_is_none_when_android_hides_it(self):
        os.remove(os.path.join(self.phone.proc, "meminfo"))
        self.assertIsNone(self.mac.memory())

    def test_the_hottest_cpu_sensor_wins_and_nonsense_is_ignored(self):
        self.assertEqual(self.mac.temps(), {"cpu": 44.5, "battery": 24.0})

    def test_a_sensor_reading_nonsense_is_left_out(self):
        self.assertIsNone(machine.plausible_temp("194700"))
        self.assertIsNone(machine.plausible_temp("oops"))
        self.assertEqual(machine.plausible_temp("41000"), 41.0)

    def test_core_speed_is_a_share_of_the_top_speed(self):
        found = self.mac.cores()
        self.assertEqual(found["mhz"], [900, 1150])
        self.assertEqual(found["speed"], 0.5)

    def test_a_service_reads_from_runit(self):
        found = self.mac.service(machine.BY_NAME["xrnhub"])
        self.assertEqual((found["state"], found["pid"], int(found["since"])), ("run", 106, NOW - 86400))

    def test_runit_can_add_to_the_state_word(self):
        self.phone.service("xrnhub", 106, state="run, want down")
        self.assertEqual(self.mac.service(machine.BY_NAME["xrnhub"])["state"], "run")

    def test_a_service_that_was_never_set_up(self):
        shutil.rmtree(os.path.join(self.phone.prefix, "var", "service", "xrnhub"))
        self.assertEqual(self.mac.service(machine.BY_NAME["xrnhub"])["state"], "missing")

    def test_process_names_can_hold_spaces_and_brackets(self):
        self.phone.process(777, ticks=250, rss_pages=10, name="odd ) name")
        found = self.mac.process(777)
        self.assertEqual(found["cpuS"], 250 / self.mac.tick_hz)
        self.assertEqual(found["rss"], 10 * self.mac.page_size)
        self.assertEqual(found["threads"], 5)
        self.assertEqual(found["rchar"], 5000)

    def test_a_process_that_has_gone(self):
        self.assertIsNone(self.mac.process(99999))

    def test_network_adds_up_the_paths_and_notes_the_relay(self):
        self.assertEqual(self.mac.network(), {"in": 1000, "out": 2000, "relayed": 150})

    def test_network_without_tailscale(self):
        self.phone.api.clear()
        self.assertIsNone(self.mac.network())

    def test_devices_leave_out_ingress_nodes_and_keys(self):
        found = self.mac.tailnet()
        self.assertEqual(found["ingress"], 2)
        self.assertEqual([d["name"] for d in found["devices"]], ["laptop"])
        self.assertTrue(found["devices"][0]["direct"])
        self.assertEqual(found["version"], "1.2.3")
        self.assertNotIn("secret", json.dumps(found))
        self.assertNotIn("198.51.100.4", json.dumps(found))

    def test_public_paths(self):
        self.assertEqual(self.mac.funnel(), ["/", "/xrnhub"])

    def test_wifi_keeps_no_identifiers(self):
        found = self.mac.wifi()
        self.assertEqual(found, {"rssi": -52, "linkMbps": 433, "ghz": 5.2})

    def test_commands_that_are_missing_give_nothing(self):
        self.phone.commands.clear()
        for read in (self.mac.battery, self.mac.wifi, self.mac.funnel):
            self.assertIsNone(read())


class Answering(unittest.TestCase):
    """Machine.answers against real sockets on this machine."""

    def setUp(self):
        self.mac = machine.Machine(tempfile.gettempdir(), tempfile.gettempdir())
        timeout = machine.HEALTH_TIMEOUT_S
        machine.HEALTH_TIMEOUT_S = 0.5
        self.addCleanup(setattr, machine, "HEALTH_TIMEOUT_S", timeout)

    def serve(self, status):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(status)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, *a):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server.server_address[1]

    def service(self, port):
        return machine.Service("x", "x", port, "/health", "x", True, "x.py")

    def test_an_answer_is_up(self):
        self.assertTrue(self.mac.answers(self.service(self.serve(200))))

    def test_a_login_wall_is_still_an_answer(self):
        self.assertTrue(self.mac.answers(self.service(self.serve(401))))

    def test_a_server_error_is_down(self):
        self.assertFalse(self.mac.answers(self.service(self.serve(502))))

    def test_a_port_that_takes_connections_but_never_answers_is_down(self):
        # What a program left behind by a killed supervisor did on 2026-11-15.
        import socket

        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(5)
        self.addCleanup(listener.close)
        started = time.monotonic()
        self.assertFalse(self.mac.answers(self.service(listener.getsockname()[1])))
        self.assertLess(time.monotonic() - started, 3)

    def test_asking_leaves_the_service_nothing_to_trip_on(self):
        # The live log had a ConnectionResetError traceback right after many of the monitor's own
        # GET /health: it hung up with the answer unread, which resets the connection, and the
        # service, keeping it alive for a next request, read the reset instead.
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        tripped = []

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self):
                body = b"x" * 200_000
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        class Server(ThreadingHTTPServer):
            def handle_error(self, request, client_address):
                tripped.append(sys.exc_info()[0])

        server = Server(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        for _ in range(5):
            self.assertTrue(self.mac.answers(self.service(server.server_address[1])))
        time.sleep(0.3)
        self.assertEqual(tripped, [])

    def test_nothing_listening_is_down(self):
        import socket

        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        self.assertFalse(self.mac.answers(self.service(port)))


class Logs(Base):
    def test_a_request_loses_its_query(self):
        found = logscan.parse_request('203.0.113.9 "GET /api/changes?since=4&key=hunter2 HTTP/1.1" 200 -')
        self.assertEqual((found["path"], found["status"], found["ip"]), ("/api/changes", 200, "203.0.113.9"))
        self.assertNotIn("hunter2", json.dumps(found))

    def test_a_held_open_request_is_a_poll(self):
        self.assertTrue(logscan.parse_request('::1 "GET /api/changes?since=4&wait=50 HTTP/1.1" 200 -')["poll"])
        self.assertFalse(logscan.parse_request('::1 "GET /api/changes?since=4 HTTP/1.1" 200 -')["poll"])

    def test_this_pages_own_refresh_is_a_poll(self):
        self.assertTrue(logscan.parse_request('::1 "GET /api/server/now?since=4 HTTP/1.1" 200 -')["poll"])

    def test_only_a_successful_change_counts_as_a_write(self):
        self.assertTrue(logscan.parse_request('::1 "POST /api/changes HTTP/1.1" 200 -')["write"])
        self.assertFalse(logscan.parse_request('::1 "POST /api/changes HTTP/1.1" 401 -')["write"])
        self.assertFalse(logscan.parse_request('::1 "PROPFIND / HTTP/1.1" 207 -')["write"])

    def test_clean_strips_queries_from_any_line(self):
        self.assertEqual(logscan.clean('x "GET /a?token=1 HTTP/1.1" 200'), 'x "GET /a HTTP/1.1" 200')

    def test_stamps_are_utc(self):
        when, text = logscan.parse_line("2026-11-06_01:09:14.23919 hello")
        self.assertEqual((when, text), (int(datetime(2026, 11, 6, 1, 9, 14, tzinfo=_utc()).timestamp()) * 1000, "hello"))

    def test_levels(self):
        self.assertEqual(logscan.level_of('::1 "GET / HTTP/1.1" 500 -'), "error")
        self.assertEqual(logscan.level_of('::1 "GET / HTTP/1.1" 404 -'), "warn")
        self.assertEqual(logscan.level_of('::1 "GET / HTTP/1.1" 200 -'), "info")
        self.assertEqual(logscan.level_of("Traceback (most recent call last):"), "error")
        self.assertEqual(logscan.level_of("[WARNING] slow"), "warn")
        self.assertEqual(logscan.level_of("warning: slow query took 1.2s"), "warn")
        self.assertEqual(logscan.level_of("Error: upstream timed out"), "error")
        self.assertEqual(logscan.level_of("errors: none"), "info")

    def test_sign_ins(self):
        self.assertEqual(logscan.parse_login("Accepted publickey for me from 192.0.2.1 port 3 ssh2", "ssh"), (True, "me", "192.0.2.1"))
        self.assertEqual(logscan.parse_login("Failed password for invalid user admin from 192.0.2.1 port 3", "ssh"),
                         (False, "admin", "192.0.2.1"))
        line = "[WARNING] Failed login attempt from 127.0.0.1 (forwarded for '2001:db8::1'): 'me' (htpasswd)"
        self.assertEqual(logscan.parse_login(line, "calendar"), (False, "me", "2001:db8::1"))
        self.assertIsNone(logscan.parse_login("nothing to see", "ssh"))
        # Each service's own words, from that service only.
        self.assertIsNone(logscan.parse_login(line, "ssh"))
        self.assertIsNone(logscan.parse_login("Accepted publickey for me from 192.0.2.1 port 3 ssh2", "calendar"))

    def test_only_new_lines_come_back(self):
        path = self.phone.log("xrnloops-sync", "one", "two")
        follow = logscan.Follower(os.path.dirname(path))
        self.assertEqual(follow.read(), ["one", "two"])
        self.assertEqual(follow.read(), [])
        self.phone.log("xrnloops-sync", "three")
        self.assertEqual(follow.read(), ["three"])

    def test_a_half_written_line_waits(self):
        path = self.phone.log("xrnloops-sync", "one")
        follow = logscan.Follower(os.path.dirname(path))
        with open(path, "a") as f:
            f.write("par")
        self.assertEqual(follow.read(), ["one"])
        with open(path, "a") as f:
            f.write("tial\n")
        self.assertEqual(follow.read(), ["partial"])

    def test_a_rotation_loses_nothing(self):
        path = self.phone.log("xrnloops-sync", "one")
        follow = logscan.Follower(os.path.dirname(path))
        follow.read()
        self.phone.log("xrnloops-sync", "two")
        os.rename(path, os.path.join(os.path.dirname(path), "@400000006.s"))
        self.phone.log("xrnloops-sync", "three")
        self.assertEqual(follow.read(), ["two", "three"])

    def test_a_restart_carries_on_from_the_same_place(self):
        path = self.phone.log("xrnloops-sync", "one")
        first = logscan.Follower(os.path.dirname(path))
        first.read()
        self.phone.log("xrnloops-sync", "two")
        again = logscan.Follower(os.path.dirname(path), first.inode, first.offset)
        self.assertEqual(again.read(), ["two"])

    def test_the_first_read_starts_with_the_rotated_files(self):
        self.phone.log("xrnloops-sync", "old", name="@400000001.s")
        path = self.phone.log("xrnloops-sync", "new")
        self.assertEqual(logscan.Follower(os.path.dirname(path)).read(), ["old", "new"])


class Sampling(Base):
    def tick(self, seconds=60):
        self.clock[0] += seconds
        self.watcher.minute_tick(self.clock[0])

    def test_a_first_pass_has_no_rates_and_does_not_fall_over(self):
        self.watcher.minute_tick(self.clock[0])
        row = self.store.rows("SELECT cpu, net_in, disk_used FROM system")[0]
        self.assertIsNone(row[0])
        self.assertIsNone(row[1])
        self.assertIsNotNone(row[2])

    def test_a_phone_that_hides_everything_still_samples(self):
        shutil.rmtree(self.phone.proc)
        shutil.rmtree(self.phone.sys)
        self.phone.commands.clear()
        self.phone.api.clear()
        self.watcher.minute_tick(self.clock[0])
        self.tick()
        self.assertEqual(len(self.store.rows("SELECT at FROM system")), 2)

    def test_cpu_is_percent_of_one_core_over_the_time_between(self):
        self.watcher.minute_tick(self.clock[0])
        self.phone.process(106, ticks=100 + 60 * self.mac.tick_hz // 2)
        self.tick()
        cpu = self.store.rows("SELECT cpu FROM service WHERE name = 'xrnhub' ORDER BY at DESC LIMIT 1")[0][0]
        self.assertAlmostEqual(cpu, 50, delta=0.5)

    def test_a_new_process_starts_its_counters_again(self):
        self.watcher.minute_tick(self.clock[0])
        self.phone.service("xrnhub", 555, since=self.clock[0] + 30)
        self.phone.process(555, wchar=10)
        self.tick()
        row = self.store.rows("SELECT cpu, written_b FROM service WHERE name = 'xrnhub' ORDER BY at DESC LIMIT 1")[0]
        self.assertEqual(row, (None, None))

    def test_bytes_through_the_network_are_differences(self):
        self.watcher.minute_tick(self.clock[0])
        self.phone.api["/localapi/v0/usermetrics"] = METRICS.replace("} 900", "} 1900")
        self.tick()
        row = self.store.rows("SELECT net_in, net_out FROM system ORDER BY at DESC LIMIT 1")[0]
        self.assertEqual(row, (1000, 0))

    def test_tailscale_restarting_is_not_negative_traffic(self):
        self.watcher.minute_tick(self.clock[0])
        self.phone.api["/localapi/v0/usermetrics"] = METRICS.replace("} 900", "} 1").replace("} 100", "} 1")
        self.tick()
        self.assertEqual(self.store.rows("SELECT net_in FROM system ORDER BY at DESC LIMIT 1")[0][0], 2)

    def test_requests_are_counted_once_each_across_restarts(self):
        when = self.ago(seconds=20)
        self.phone.log("xrnloops-sync",
                       f'{stamp_of(when)} 203.0.113.9 "GET /api/changes?since=1 HTTP/1.1" 200 -',
                       f'{stamp_of(when)} 203.0.113.9 "POST /api/changes HTTP/1.1" 200 -',
                       f'{stamp_of(when)} 198.51.100.7 "GET /admin.php HTTP/1.1" 404 -',
                       f'{stamp_of(when)} 127.0.0.1 "GET /api/state HTTP/1.1" 200 -')
        self.watcher.minute_tick(self.clock[0])
        again = Watcher(self.mac, self.store, self.inventory, clock=lambda: self.clock[0])
        self.clock[0] += 60
        again.minute_tick(self.clock[0])
        total = self.store.totals(3600, self.clock[0])["xrnloops"]
        self.assertEqual((total["reqs"], total["writes"], total["refused"]), (4, 1, 1))

    def test_the_monitors_own_health_check_is_not_traffic(self):
        when = self.ago(seconds=20)
        self.phone.log("xrnloops-sync",
                       f'{stamp_of(when)} 127.0.0.1 "GET /health HTTP/1.1" 200 -',
                       f'{stamp_of(when)} 203.0.113.9 "GET /health HTTP/1.1" 200 -')
        self.watcher.minute_tick(self.clock[0])
        total = self.store.totals(3600, self.clock[0])["xrnloops"]
        self.assertEqual(total["reqs"], 1)  # the one through the public address still counts

    def test_health_requests_to_the_hub_count_as_xrnfits_traffic(self):
        # xrnfit has no service of its own: it sends and reads health through the hub, so without
        # this its uploads were the hub's traffic and the pages showed no trace of xrnfit.
        when = self.ago(seconds=20)
        self.phone.log("xrnhub",
                       f'{stamp_of(when)} 203.0.113.9 "POST /api/health HTTP/1.1" 200 -',
                       f'{stamp_of(when)} 203.0.113.9 "GET /api/v1/health/days?from=2026-03-01 HTTP/1.1" 200 -',
                       f'{stamp_of(when)} 203.0.113.9 "GET /xrnhub/api/v1/health HTTP/1.1" 200 -',
                       f'{stamp_of(when)} 203.0.113.9 "GET /api/v1/healthy HTTP/1.1" 404 -',
                       f'{stamp_of(when)} 203.0.113.9 "GET /api/today HTTP/1.1" 200 -')
        self.watcher.minute_tick(self.clock[0])
        totals = self.store.totals(3600, self.clock[0])
        self.assertEqual((totals["xrnfit"]["reqs"], totals["xrnfit"]["writes"]), (3, 1))
        self.assertEqual(totals["xrnhub"]["reqs"], 2)
        self.assertEqual({f["service"] for f in self.watcher.feed if "health" in f["path"]}, {"xrnfit", "xrnhub"})
        paths = {(p["service"], p["path"]) for p in self.store.top_paths("2000-01-01")}
        self.assertIn(("xrnfit", "/api/health"), paths)

    def test_loopback_callers_are_not_clients(self):
        self.phone.log("xrnloops-sync", f'{stamp_of(self.ago(seconds=20))} 127.0.0.1 "GET /health HTTP/1.1" 200 -')
        self.watcher.minute_tick(self.clock[0])
        self.assertEqual(self.store.top_clients("2000-01-01"), [])

    def test_a_page_asking_while_the_monitor_samples_gets_its_points(self):
        # The page read the live points in a loop over the ring while the monitor thread appended
        # to it, and a deque changed under a loop raises "deque mutated during iteration".
        ring = self.watcher.ring
        ring.extend({"at": i} for i in range(ring.maxlen))
        stop = threading.Event()
        def sample():
            at = ring.maxlen
            while not stop.is_set():
                ring.append({"at": at})
                at += 1
        old = sys.getswitchinterval()
        sys.setswitchinterval(1e-6)  # switch threads as often as can be, so the window is hit
        feeder = threading.Thread(target=sample)
        feeder.start()
        try:
            end = time.monotonic() + 0.5
            while time.monotonic() < end:
                self.assertTrue(self.watcher.points_since(1))
        finally:
            stop.set()
            feeder.join()
            sys.setswitchinterval(old)

    def test_a_scanner_cannot_fill_the_database(self):
        watcher = self.watcher
        lines = [f'{stamp_of(self.ago(seconds=20))} 203.0.113.9 "GET /p{i} HTTP/1.1" 404 -' for i in range(30)]
        self.phone.log("xrnloops-sync", *lines)
        import hublib.watcher as module
        keep, module.MAX_NAMES_A_DAY = module.MAX_NAMES_A_DAY, 5
        self.addCleanup(setattr, module, "MAX_NAMES_A_DAY", keep)
        watcher.minute_tick(self.clock[0])
        found = self.store.rows("SELECT path, sum(count) FROM paths GROUP BY path")
        self.assertEqual(len(found), 6)
        self.assertIn(("(other)", 25), found)

    def test_a_sign_in_is_kept(self):
        self.phone.log("sshd", f"{stamp_of(self.ago(seconds=20))} Accepted publickey for me from 192.0.2.1 port 3 ssh2")
        self.watcher.minute_tick(self.clock[0])
        rows = self.store.login_rows(0)
        self.assertEqual((rows[0]["ok"], rows[0]["who"], rows[0]["service"]), (True, "me", "ssh"))

    def test_a_sign_in_cant_be_written_into_another_line(self):
        # Every service's lines were searched for sshd's words anywhere in them. Radicale logs each
        # request's User-Agent, and it is public at /: a stranger could plant "signed in" rows, or
        # bury a real one under fakes. So could a bad request line sent straight to a service.
        stamp = stamp_of(self.ago(seconds=20))
        forged = "Accepted password for me from 198.51.100.66 port 1 ssh2"
        self.phone.log("radicale", f"{stamp} [2026-11-06 12:09:14 +1100] [7313/Thread-12 (process_request_thread)] "
                                   f"[INFO] GET request for '/' received from 127.0.0.1 using '{forged}'",
                       f"{stamp} [INFO] PROPFIND response status for '/' in 0.004 seconds: 207 "
                       f"'] [WARNING] Failed login attempt from 198.51.100.67: 'admin''")
        self.phone.log("xrnhub", f"{stamp} 127.0.0.1 code 400, message Bad request syntax ('{forged}')")
        self.phone.log("xrnloops-sync", f"{stamp} {forged}")
        # A user name is the caller's to choose, spaces and all.
        self.phone.log("sshd", f"{stamp} Invalid user {forged} from 203.0.113.7 port 5")
        self.watcher.minute_tick(self.clock[0])
        rows = self.store.login_rows(0)
        self.assertEqual([(r["service"], r["ok"], r["ip"]) for r in rows], [("ssh", False, "203.0.113.7")])

    def test_sign_ins_as_sshd_and_radicale_write_them(self):
        stamp = stamp_of(self.ago(seconds=20))
        self.phone.log("sshd", f"{stamp} sshd-session[812]: Accepted publickey for me from 192.0.2.1 port 3 ssh2: ED25519 SHA256:x",
                       f"{stamp} Failed password for invalid user admin from 192.0.2.9 port 4 ssh2")
        self.phone.log("radicale", f"{stamp} [2026-11-06 12:09:14 +1100] [7313/Thread-12 (process_request_thread)] "
                                   f"[WARNING] Failed login attempt from 127.0.0.1 (forwarded for '2001:db8::1'): 'me'")
        self.watcher.minute_tick(self.clock[0])
        rows = {(r["service"], r["ok"], r["who"], r["ip"]) for r in self.store.login_rows(0)}
        self.assertEqual(rows, {("ssh", True, "me", "192.0.2.1"), ("ssh", False, "admin", "192.0.2.9"),
                                ("calendar", False, "me", "2001:db8::1")})

    def test_the_same_sign_in_shows_once_and_failures_come_first(self):
        at = self.clock[0] * 1000
        self.store.add_logins([(at - i, "ssh", 1, "me", "192.0.2.1") for i in range(50)])
        self.store.add_logins([(at - 9000, "calendar", 0, "admin", "198.51.100.7")])
        rows = self.store.login_rows(0)
        self.assertEqual([(r["ok"], r["count"]) for r in rows], [(False, 1), (True, 50)])
        self.assertEqual(rows[1]["at"], at)

    def test_the_feed_carries_no_query_and_only_recent_requests(self):
        self.phone.log("xrnloops-sync",
                       f'{stamp_of(self.ago(hours=2))} 203.0.113.9 "GET /old HTTP/1.1" 200 -',
                       f'{stamp_of(self.ago(seconds=5))} 203.0.113.9 "GET /new?key=hunter2 HTTP/1.1" 200 -')
        self.watcher.sample(self.clock[0])
        self.assertEqual([f["path"] for f in self.watcher.feed], ["/new"])
        self.assertNotIn("hunter2", json.dumps(self.watcher.now_doc(0)))

    def test_nobody_watching_means_nothing_quick_runs(self):
        self.watcher.step()
        count = len(self.watcher.ring)
        self.clock[0] += 3
        self.watcher.step()
        self.assertEqual(len(self.watcher.ring), count)

    def test_a_viewer_makes_it_quick_and_leaving_slows_it_again(self):
        self.watcher.step()
        self.watcher.now_doc(0)
        count = len(self.watcher.ring)
        self.clock[0] += 3
        self.watcher.step()
        self.assertEqual(len(self.watcher.ring), count + 1)
        self.clock[0] += 21
        count = len(self.watcher.ring)
        self.watcher.step()
        self.assertLessEqual(len(self.watcher.ring), count + 1)  # at most the minute's own sample

    def test_every_wall_clock_minute_gets_one_sample(self):
        self.clock[0] = NOW // 60 * 60 + 59
        for _ in range(4):
            self.watcher.step()
            self.clock[0] += 60.2
        stamps = [r[0] for r in self.store.rows("SELECT at FROM system ORDER BY at")]
        self.assertEqual([b - a for a, b in zip(stamps, stamps[1:])], [60, 60, 60])

    def test_the_battery_and_wifi_are_sampled(self):
        self.watcher.minute_tick(self.clock[0])
        row = self.store.rows("SELECT battery, batt_temp, rssi FROM system")[0]
        self.assertEqual(row, (100, 24.0, -52))

    def test_a_first_look_is_filled_from_the_last_few_minutes(self):
        self.store.add_minute(self.clock[0] // 60 * 60 - 120, {"load1": 0.5, "net_in": 600}, {})
        self.watcher.minute_tick(self.clock[0])
        points = self.watcher.now_doc(0)["samples"]
        self.assertEqual(len(points), 2)
        self.assertEqual((points[0]["load"], points[0]["netIn"]), (0.5, 10))
        later = self.watcher.now_doc(points[-1]["at"])["samples"]
        self.assertEqual(later, [])

    def test_now_lists_every_service(self):
        self.watcher.minute_tick(self.clock[0])
        doc = self.watcher.now_doc(0)
        self.assertEqual(len(doc["services"]), len(machine.SERVICES))
        self.assertEqual(doc["about"]["model"], "Phone")
        self.assertTrue(doc["samples"])


class Problems(Base):
    def setUp(self):
        super().setUp()
        # Uptime that keeps pace with the fake clock. The real host's would count otherwise,
        # and on a machine up for under a few minutes the clock's 60s jumps read as a reboot.
        self.mac.system = lambda: {"uptime": 1000 + self.clock[0] - NOW, "load": [0, 0, 0], "procs": 1}

    def run_minutes(self, count=1):
        for _ in range(count):
            self.clock[0] += 60
            self.watcher.minute_tick(self.clock[0])

    def open_kinds(self):
        return sorted((i["kind"], i["target"]) for i in self.store.incidents() if i["ended"] is None)

    def test_a_healthy_phone_has_no_problems(self):
        self.run_minutes(2)
        self.assertEqual(self.open_kinds(), [])

    def test_a_service_that_stops_answering_is_down_after_a_while_and_comes_back(self):
        self.run_minutes()
        self.phone.commands["answers:xrnloops"] = False
        self.run_minutes()
        self.assertEqual(self.open_kinds(), [])  # one bad minute could be a restart
        self.run_minutes(2)
        self.assertEqual(self.open_kinds(), [("down", "xrnloops")])
        self.phone.commands["answers:xrnloops"] = True
        self.run_minutes()
        self.assertEqual(self.open_kinds(), [])
        incident = self.store.incidents()[0]
        self.assertEqual(incident["ended"] - incident["started"], 180)

    def test_an_open_problem_stays_listed_however_much_happens_after_it(self):
        self.phone.service("xrnrule-sync", 0, state="down")
        self.run_minutes()
        for i in range(250):  # a service in a restart loop notes one of these every minute
            self.store.note_incident("restart", "xrntube", self.clock[0] + i)
        problems = [(p["kind"], p["target"]) for p in self.watcher.now_doc(0)["problems"]]
        self.assertEqual(problems, [("down", "xrnrule")])

    def test_a_service_runit_has_stopped_is_down_at_once(self):
        self.phone.service("xrnrule-sync", 0, state="down")
        self.run_minutes()
        self.assertEqual(self.open_kinds(), [("down", "xrnrule")])

    def test_uptime_counts_a_stopped_service_as_down(self):
        self.phone.service("xrnrule-sync", 0, state="down")
        self.run_minutes(3)
        self.assertEqual(self.store.rows("SELECT sum(up) FROM service WHERE name = 'xrnrule'")[0][0], 0)
        self.assertEqual(self.store.rows("SELECT sum(up) FROM service WHERE name = 'xrnhub'")[0][0], 3)

    def runsv_of(self, name):
        return 9001 + [s.name for s in machine.SERVICES].index(name)

    def test_the_phone_as_it_runs_is_supervised(self):
        seen = self.mac.supervision()
        self.assertEqual(seen["runsvdir"], [9000])
        self.assertEqual(seen["runsv"]["xrninsta"], [9000])
        self.run_minutes()
        self.assertEqual(self.open_kinds(), [])

    def test_the_outage_of_2026_11_15(self):
        # Android's process trim killed runsvdir and xrnloops' runsv. The old xrnloops kept its port
        # and took connections but never answered; runit had nothing left to restart it with.
        loops = self.runsv_of("xrnloops")
        shutil.rmtree(os.path.join(self.phone.proc, "9000"))
        shutil.rmtree(os.path.join(self.phone.proc, str(loops)))
        self.phone.program(machine.BY_NAME["xrnloops"], 102, parent=1)
        for i, service in enumerate(machine.SERVICES):
            if service.name != "xrnloops":
                self.phone.process(9001 + i, parent=1, name="runsv", argv=["runsv", service.runit])
        self.phone.commands["answers:xrnloops"] = False
        self.run_minutes(3)  # "down" waits out a restart's worth of time; the others don't
        kinds = self.open_kinds()
        self.assertIn(("supervisor", "phone"), kinds)
        self.assertIn(("unsupervised", "xrnloops"), kinds)
        self.assertIn(("down", "xrnloops"), kinds)
        detail = {(i["kind"], i["target"]): i["detail"] for i in self.store.incidents() if i["ended"] is None}
        self.assertEqual(detail[("unsupervised", "xrnloops")], "pid 102")
        self.assertEqual(detail[("supervisor", "phone")], "")

    def test_a_runsv_left_from_an_earlier_runsvdir(self):
        # runsvdir came back, but the runsv the old one started still holds the service's lock.
        self.phone.process(self.runsv_of("xrntube"), parent=1, name="runsv", argv=["runsv", "xrntube-sync"])
        self.run_minutes()
        self.assertEqual(self.open_kinds(), [("unsupervised", "xrntube")])
        self.assertEqual(self.store.incidents()[0]["detail"], "runsv left over")

    def test_a_second_runsv_under_the_current_runsvdir_is_fine(self):
        self.phone.process(9500, parent=1, name="runsv", argv=["runsv", "xrntube-sync"])
        self.run_minutes()
        self.assertEqual(self.open_kinds(), [])

    def test_a_service_with_no_runsv(self):
        shutil.rmtree(os.path.join(self.phone.proc, str(self.runsv_of("xrninsta"))))
        self.run_minutes()
        self.assertEqual(self.open_kinds(), [("unsupervised", "xrninsta")])

    def test_two_runsvdirs(self):
        self.phone.process(9100, parent=1, name="runsvdir", argv=["runsvdir", "/x/var/service"])
        self.run_minutes()
        self.assertIn(("supervisor", "phone"), self.open_kinds())
        self.assertEqual(self.store.incidents()[0]["detail"], "2 running")

    def test_a_program_started_by_hand_from_a_shell_is_fine(self):
        self.phone.program(machine.BY_NAME["xrnhub"], 7777, parent=4242)  # e.g. `xrnhub.py token add`
        self.run_minutes()
        self.assertEqual(self.open_kinds(), [])

    def test_supervision_problems_clear_when_runit_is_back(self):
        self.phone.program(machine.BY_NAME["xrnrule"], 555, parent=1)
        self.run_minutes()
        self.assertEqual(self.open_kinds(), [("unsupervised", "xrnrule")])
        shutil.rmtree(os.path.join(self.phone.proc, "555"))
        self.run_minutes()
        self.assertEqual(self.open_kinds(), [])

    def test_an_unreadable_process_list_neither_opens_nor_closes_anything(self):
        self.phone.program(machine.BY_NAME["xrnrule"], 555, parent=1)
        self.run_minutes()
        self.mac.supervision = lambda: None
        self.run_minutes()
        self.assertEqual(self.open_kinds(), [("unsupervised", "xrnrule")])

    def test_the_laptops_limits_apply(self):
        low = {**BATTERY, "percentage": 20, "plugged": "UNPLUGGED", "temperature": HOT_C + 1}
        self.phone.commands["termux-battery-status"] = (0, json.dumps(low))
        self.run_minutes()
        self.assertEqual(self.open_kinds(), [("battery", "phone"), ("hot", "phone"), ("unplugged", "phone")])

    def test_losing_a_reading_does_not_close_a_problem(self):
        self.phone.commands["termux-battery-status"] = (0, json.dumps({**BATTERY, "plugged": "UNPLUGGED"}))
        self.run_minutes()
        self.watcher.battery = None
        self.phone.commands["termux-battery-status"] = (None, "")
        self.run_minutes()
        self.assertEqual(self.open_kinds(), [("unplugged", "phone")])

    def test_a_stale_backup_is_a_problem(self):
        folder = os.path.join(self.phone.home, "xrnhub", "data", "users", USER)
        self.phone.put(os.path.join(folder, "hub.backup.sqlite"), "x")
        old = self.clock[0] - 4 * 86400
        os.utime(os.path.join(folder, "hub.backup.sqlite"), (old, old))
        self.watcher.sweep(self.clock[0])
        self.run_minutes()
        self.assertEqual(self.open_kinds(), [("backup", "laptop")])

    def test_a_restart_and_a_reboot_are_noted(self):
        self.mac.system = lambda: {"uptime": 1000 + self.clock[0] - NOW, "load": [0, 0, 0], "procs": 1}
        self.run_minutes(2)
        self.phone.service("xrnhub", 106, since=self.clock[0])
        self.mac.system = lambda: {"uptime": 5, "load": [0, 0, 0], "procs": 1}
        self.run_minutes()
        kinds = {(i["kind"], i["target"]) for i in self.store.incidents()}
        self.assertEqual(kinds, {("restart", "xrnhub"), ("reboot", "phone")})

    def test_time_with_no_samples_is_shown_as_a_gap(self):
        self.run_minutes()
        self.clock[0] += 3600
        self.watcher.minute_tick(self.clock[0])
        found = [i for i in self.store.incidents() if i["kind"] == "gap"]
        self.assertEqual(len(found), 1)
        self.assertGreater(found[0]["ended"] - found[0]["started"], 3000)

    def test_only_services_that_can_be_restarted_can_be(self):
        for name in ("xrnhub", "ssh", "tailscale", "nonsense", None):
            with self.assertRaises(BadRestart):
                self.watcher.restart(name)
        self.assertEqual(self.phone.ran, [])

    def test_a_restart_goes_to_runit(self):
        worked, _ = self.watcher.restart("xrnloops")
        self.assertTrue(worked)
        self.assertEqual(self.phone.ran[-1][:2], ["sv", "restart"])
        self.assertTrue(self.phone.ran[-1][2].endswith("xrnloops-sync"))
        self.assertEqual(self.store.incidents()[0]["kind"], "restarted")

    def test_restarts_cannot_be_hammered(self):
        self.watcher.restart("xrnloops")
        with self.assertRaises(TooSoon):
            self.watcher.restart("xrnloops")
        self.clock[0] += 61
        self.watcher.restart("xrnloops")


class Keeping(Base):
    def sample(self, at, **over):
        self.store.add_minute(at, {"load1": 1.0, "net_in": 10, "net_out": 20, **over}, {"xrnhub": {"up": 1, "cpu": 2.0, "rss": 100}})

    def test_an_hour_rolls_up_with_its_averages_and_totals(self):
        start = NOW // 3600 * 3600 - 3600
        for i in range(60):
            self.sample(start + i * 60, load1=float(i % 2 * 2))
        self.store.roll_up(NOW)
        row = self.store.rows("SELECT n, load1, net_in FROM system WHERE span = 3600")[0]
        self.assertEqual(row, (60, 1.0, 600))
        self.assertEqual(self.store.rows("SELECT n, up, cpu FROM service WHERE span = 3600")[0], (60, 60, 2.0))

    def test_rolling_up_twice_changes_nothing(self):
        start = NOW // 3600 * 3600 - 3600
        for i in range(60):
            self.sample(start + i * 60)
        self.store.roll_up(NOW)
        self.store.roll_up(NOW + 60)
        self.assertEqual(self.store.rows("SELECT count(*), sum(n) FROM system WHERE span = 3600")[0], (1, 60))

    def test_old_minutes_go_and_their_hours_stay(self):
        old = NOW - 9 * 86400
        self.sample(old // 3600 * 3600)
        self.store.roll_up(NOW)
        self.assertEqual(self.store.rows("SELECT count(*) FROM system WHERE span = 60")[0][0], 0)
        self.assertEqual(self.store.rows("SELECT count(*) FROM system WHERE span = 3600")[0][0], 1)

    def test_old_traffic_read_from_a_log_keeps_its_hour(self):
        old = NOW - 9 * 86400
        self.store.add_traffic({(old // 60 * 60, "xrnhub"): {"reqs": 3}})
        self.store.roll_up(NOW)
        self.assertEqual(self.store.rows("SELECT sum(reqs) FROM traffic WHERE span = 60")[0][0], None)
        self.assertEqual(self.store.rows("SELECT sum(reqs) FROM traffic WHERE span = 3600")[0][0], 3)

    def test_traffic_adds_up_across_passes(self):
        at = NOW // 60 * 60
        self.store.add_traffic({(at, "xrnhub"): {"reqs": 3}})
        self.store.add_traffic({(at, "xrnhub"): {"reqs": 2, "writes": 1}})
        self.assertEqual(self.store.totals(3600, NOW)["xrnhub"]["reqs"], 5)
        self.assertEqual(self.store.rows("SELECT reqs, writes FROM traffic WHERE span = 3600")[0], (5, 1))

    def test_this_pages_own_polling_stays_off_the_busiest_list(self):
        self.store.add_paths({("2026-11-06", "xrnhub", "/api/server/now", 200): 900, ("2026-11-06", "xrnhub", "/api/today", 200): 3})
        self.assertEqual([p["path"] for p in self.store.top_paths("2026-11-01")], ["/api/today"])

    def test_refused_paths_are_listed_apart(self):
        self.store.add_paths({("2026-11-06", "xrnhub", "/.env", 404): 4, ("2026-11-06", "xrnhub", "/api/today", 200): 3})
        self.assertEqual([p["path"] for p in self.store.top_paths("2026-11-01", refused=True)], ["/.env"])

    def test_callers_rank_by_refusals_when_asked(self):
        self.store.add_clients({("2026-11-06", "203.0.113.9"): (500, 1), ("2026-11-06", "198.51.100.7"): (9, 9), ("2026-11-06", "192.0.2.5"): (50, 0)})
        self.assertEqual([c["ip"] for c in self.store.top_clients("2026-11-01")], ["203.0.113.9", "192.0.2.5", "198.51.100.7"])
        self.assertEqual([c["ip"] for c in self.store.top_clients("2026-11-01", refused=True)], ["198.51.100.7", "203.0.113.9"])

    def test_a_gap_is_left_empty_on_the_chart(self):
        at = NOW // 60 * 60
        self.sample(at - 300)
        self.sample(at)
        series = self.store.history("1h", NOW)["system"]["load1"]
        self.assertEqual(series[-1], 1.0)
        self.assertEqual(series[-6], 1.0)
        self.assertIsNone(series[-3])

    def test_a_chart_starts_where_watching_started(self):
        self.sample((NOW - 3 * 86400) // 60 * 60)
        doc = self.store.history("90d", NOW)
        self.assertEqual(doc["system"]["load1"][0], 1.0)
        self.assertLessEqual(len(doc["at"]), 5)

    def test_a_long_chart_includes_hours_not_rolled_up_yet(self):
        self.sample(NOW // 60 * 60)
        self.assertEqual(self.store.history("90d", NOW)["system"]["load1"][-1], 1.0)
        self.assertEqual(self.store.history("90d", NOW)["services"]["xrnhub"]["up"][-1], 1)

    def test_rolled_up_hours_are_not_counted_twice(self):
        start = NOW // 3600 * 3600 - 3600
        for i in range(60):
            self.sample(start + i * 60)
        self.store.roll_up(NOW)
        doc = self.store.history("30d", NOW)
        self.assertEqual(sum(v for v in doc["system"]["net_in"] if v), 600)

    def test_older_traffic_from_the_logs_widens_the_chart(self):
        self.sample(NOW // 60 * 60)
        self.store.add_traffic({((NOW - 5 * 86400) // 60 * 60, "xrnhub"): {"reqs": 3}})
        self.assertGreaterEqual(len(self.store.history("90d", NOW)["at"]), 6)

    def test_every_range_has_one_axis(self):
        self.sample(NOW // 60 * 60)
        for name in metrics.RANGES:
            doc = self.store.history(name, NOW)
            self.assertEqual(len(doc["at"]), len(doc["system"]["load1"]), name)
            self.assertEqual(doc["at"], sorted(doc["at"]))

    def test_one_service_can_be_asked_for(self):
        self.store.add_minute(NOW // 60 * 60, {}, {"xrnhub": {"up": 1}, "ssh": {"up": 1}})
        self.assertEqual(list(self.store.history("1h", NOW, "ssh")["services"]), ["ssh"])

    def test_uptime_days_use_hours_and_minutes(self):
        self.store.add_minute(NOW // 60 * 60, {}, {"xrnhub": {"up": 1}})
        self.store.write("INSERT INTO service(span, at, name, n, up) VALUES(3600, ?, 'xrnhub', 60, 30)", (NOW - 20 * 86400,))
        days = self.store.uptime_days(NOW)["xrnhub"]
        self.assertEqual(len(days), 30)
        self.assertEqual(days[-1], 1)
        self.assertEqual(days[9], 0.5)
        self.assertIsNone(days[0])


class Holdings(Base):
    def setUp(self):
        super().setUp()
        home = self.phone.home
        calendar = os.path.join(home, "radicale", "collections", "collection-root", USER, "work")
        self.phone.put(os.path.join(calendar, "a.ics"), "BEGIN:VCALENDAR\n")
        self.phone.put(os.path.join(calendar, "b.ics"), "BEGIN:VCALENDAR\n")
        self.phone.put(os.path.join(calendar, ".Radicale.cache", "item", "a.ics"), "x" * 5000)
        self.phone.put(os.path.join(home, "radicale", "radicale.log"), "x" * 9000)
        make_loops(home, [habit("a", "Walk")], [("a", "2026-11-05", 2), ("a", "2026-11-06", 2)])
        rule = os.path.join(home, "xrnrule-sync", "data", "users", USER, "events.jsonl")
        self.phone.put(rule, f'{{"at":{NOW * 1000},"kind":"a"}}\n{{"at":{(NOW - 86400) * 1000},"kind":"b"}}\n')
        self.rule = rule
        write_json(os.path.join(home, "xrnhome-sync", "data", "users", USER, "state.json"), {"data": {"items": [{"id": 1}]}})
        write_json(os.path.join(home, "xrntube-sync", "data", "users", USER, "state.json"), {"channels": [1, 2, 3]})
        write_json(os.path.join(home, "xrnwal-sync", "data", "users", USER, "state.json"),
                   {"data": {"bills": [{"atMillis": NOW * 1000}]}})

    def by_name(self, doc):
        return {a["name"]: a for a in doc["apps"]}

    def test_records_are_counted_per_app(self):
        apps = self.by_name(self.inventory.sweep(NOW))
        found = {name: apps[name]["records"] for name in ("calendar", "xrnloops", "xrnrule", "xrnhome", "xrntube", "xrnwal")}
        self.assertEqual(found, {"calendar": 2, "xrnloops": 2, "xrnrule": 2, "xrnhome": 1, "xrntube": 3, "xrnwal": 1})

    def test_xrnimg_counts_its_items_but_not_its_trash_and_when_each_came_in(self):
        folder = os.path.join(self.phone.home, "xrnimg-sync", "data", "users", USER)
        os.makedirs(os.path.join(folder, "blobs", "ab"))
        with open(os.path.join(folder, "blobs", "ab", "ab" * 32), "wb") as f:
            f.write(b"a made-up photo")
        with sqlite3.connect(os.path.join(folder, "photos.sqlite")) as db:
            db.execute("CREATE TABLE items(id TEXT, created INTEGER, trashed_at INTEGER)")
            db.executemany("INSERT INTO items VALUES(?, ?, ?)", [("a", NOW * 1000, None), ("b", NOW * 1000, None),
                                                                  ("c", (NOW - 86400) * 1000, NOW * 1000)])
        db.close()
        doc = self.inventory.sweep(NOW)
        app = self.by_name(doc)["xrnimg"]
        self.assertEqual((app["records"], app["unit"]), (2, "items"))
        self.assertGreater(app["bytes"], 0)
        self.assertEqual(doc["fed"]["apps"]["xrnimg"][-1], 2)

    def test_a_photo_library_that_cant_be_read_counts_nothing(self):
        folder = os.path.join(self.phone.home, "xrnimg-sync", "data", "users", USER)
        os.makedirs(folder)
        with open(os.path.join(folder, "photos.sqlite"), "w") as f:
            f.write("not a database")
        self.assertEqual(self.by_name(self.inventory.sweep(NOW))["xrnimg"]["records"], 0)

    def test_xrnnote_is_counted_by_its_files_and_never_opened(self):
        # Every note there is ciphertext: the hub has its files' number and size, and reads none.
        folder = os.path.join(self.phone.home, "xrnnote-sync", "data", "users", USER)
        self.phone.put(os.path.join(folder, "notes.sqlite"), "\x01" + "made-up ciphertext" * 10)
        self.phone.put(os.path.join(folder, "att", "Qm9vb2tzaGVsZjEyMzQ1Ng"), "\x01" + "an image" * 50)
        with mock.patch("sqlite3.connect", wraps=sqlite3.connect) as opened, \
                mock.patch("builtins.open", wraps=open) as read:
            doc = self.inventory.sweep(NOW)
        self.assertTrue(opened.called and read.called)  # the others are read, so these see every open
        self.assertFalse([c for c in opened.call_args_list + read.call_args_list if "xrnnote" in str(c)])
        app = self.by_name(doc)["xrnnote"]
        self.assertEqual((app["records"], app["unit"]), (2, "files"))
        self.assertGreater(app["bytes"], 0)
        self.assertNotIn("xrnnote", doc["fed"]["apps"])

    def test_xrnfits_health_counts_as_its_own_app(self):
        # The health records live in the hub's hub.sqlite, but they're xrnfit's: a row a reading,
        # in on the day they were written, and the bytes they take moved off the hub's share.
        log = HubLog(os.path.join(self.phone.home, "xrnhub", "data"))
        self.addCleanup(lambda: log.db(USER).close())
        today, yesterday = NOW * 1000, (NOW - 86400) * 1000
        health.write(log, USER, {"source": "xrnfit-watch", "upsert": [
            {"id": "h", "kind": "heart_rate", "samples": [[today - 60_000 * i, 70] for i in range(300)]},
            {"id": "s", "kind": "steps", "start": today - 3_600_000, "end": today - 60_000, "value": 400}]}, now_ms=today)
        health.write(log, USER, {"source": "xrnfit-scale", "upsert": [
            {"id": "w", "kind": "weight", "start": yesterday, "value": 70}]}, now_ms=yesterday)
        log.db(USER).commit()
        before = self.by_name(self.inventory.sweep(NOW))["xrnhub"]["bytes"]
        counted = Inventory(self.phone.home, lambda: [USER], self.source, self.phone.prefix, log)
        doc = counted.sweep(NOW)
        apps = self.by_name(doc)
        self.assertEqual((apps["xrnfit"]["records"], apps["xrnfit"]["unit"]), (302, "readings"))
        self.assertEqual(doc["fed"]["apps"]["xrnfit"][-2:], [1, 301])
        self.assertGreater(apps["xrnfit"]["bytes"], 0)
        self.assertEqual(apps["xrnhub"]["bytes"] + apps["xrnfit"]["bytes"], before)  # moved, not counted twice
        self.assertEqual(doc["total"], sum(a["bytes"] for a in doc["apps"]))

    def test_health_kept_outside_the_hubs_folder_is_xrnfits_alone(self):
        log = HubLog(os.path.join(self.root, "elsewhere"))
        self.addCleanup(lambda: log.db(USER).close())
        health.write(log, USER, {"source": "xrnfit-scale", "upsert": [
            {"id": "w", "kind": "weight", "start": NOW * 1000, "value": 70}]}, now_ms=NOW * 1000)
        hub = self.by_name(self.inventory.sweep(NOW))["xrnhub"]["bytes"]
        apps = self.by_name(Inventory(self.phone.home, lambda: [USER], self.source, self.phone.prefix, log).sweep(NOW))
        self.assertEqual(apps["xrnhub"]["bytes"], hub)
        self.assertGreater(apps["xrnfit"]["bytes"], 0)

    def test_without_the_hubs_records_there_is_no_xrnfit(self):
        self.assertNotIn("xrnfit", self.by_name(self.inventory.sweep(NOW)))

    def test_a_cache_is_not_your_data(self):
        doc = self.by_name(self.inventory.sweep(NOW))["calendar"]
        self.assertEqual(doc["files"], 2)
        self.assertGreaterEqual(doc["extra"], 14000)
        self.assertLess(doc["bytes"], 14000)

    def test_what_came_in_shows_by_day_with_no_history_needed(self):
        fed = self.inventory.sweep(NOW)["fed"]
        today, yesterday = fed["days"][-1], fed["days"][-2]
        self.assertEqual((today, yesterday), ("2026-11-06", "2026-11-05"))
        self.assertEqual(fed["apps"]["xrnrule"][-2:], [1, 1])
        self.assertEqual(fed["apps"]["xrnwal"][-1], 1)
        self.assertEqual(sum(fed["apps"]["xrnloops"]), 2)

    def test_a_stamp_no_calendar_reaches_is_counted_but_not_dated(self):
        home = self.phone.home
        with open(self.rule, "a") as f:
            f.write('{"at":%d}\n' % 10**20)
        write_json(os.path.join(home, "xrnwal-sync", "data", "users", USER, "state.json"),
                   {"data": {"bills": [{"atMillis": NOW * 1000}, {"atMillis": 1e300}]}})
        db = sqlite3.connect(os.path.join(home, "xrnloops-sync", "data", "users", USER, "loop.sqlite"))
        db.execute("UPDATE entries SET mtime = ? WHERE date = '2026-11-05'", (2**62,))  # as if in nanoseconds
        db.commit()
        db.close()
        doc = self.inventory.sweep(NOW)
        apps = self.by_name(doc)
        self.assertEqual([apps[name]["records"] for name in ("xrnrule", "xrnwal", "xrnloops")], [3, 2, 2])
        self.assertEqual([sum(doc["fed"]["apps"][name]) for name in ("xrnrule", "xrnwal", "xrnloops")], [2, 1, 1])

    def test_a_device_log_is_only_read_once(self):
        self.inventory.sweep(NOW)
        with open(self.rule, "a") as f:
            f.write(f'{{"at":{NOW * 1000}}}\n')
        apps = self.by_name(self.inventory.sweep(NOW))
        self.assertEqual(apps["xrnrule"]["records"], 3)

    def test_a_device_log_that_started_over_is_counted_again(self):
        self.inventory.sweep(NOW)
        self.phone.put(self.rule, f'{{"at":{NOW * 1000}}}\n')
        self.assertEqual(self.by_name(self.inventory.sweep(NOW))["xrnrule"]["records"], 1)

    def test_nothing_from_inside_the_records_leaves(self):
        with open(self.rule, "a") as f:
            f.write(f'{{"at":{NOW * 1000},"overridePhrase":"let me in please"}}\n')
        self.assertNotIn("let me in", json.dumps(self.inventory.sweep(NOW)))

    def test_missing_apps_read_as_empty(self):
        shutil.rmtree(self.phone.home)
        os.makedirs(self.phone.home)
        doc = self.inventory.sweep(NOW)
        self.assertEqual(doc["total"], 0)

    def test_the_data_page_needs_a_week_before_saying_what_grew(self):
        self.watcher.minute_tick(NOW)
        self.watcher.sweep(NOW)
        doc = self.watcher.data_doc()
        self.assertIsNone(doc["apps"][0]["grew"])
        self.assertIsNone(doc["runway"])

    def test_growth_and_how_long_the_disk_lasts(self):
        self.watcher.minute_tick(NOW)
        self.watcher.sweep(NOW)
        for days_ago in (8, 5, 2):
            day = (datetime.fromtimestamp(NOW) - timedelta(days=days_ago)).date().isoformat()
            self.store.set_data_day(day, {"xrnrule": {"bytes": 0, "records": 0}})
        doc = self.watcher.data_doc()
        rule = next(a for a in doc["apps"] if a["name"] == "xrnrule")
        self.assertEqual(rule["grewSince"], "2026-11-01")
        self.assertEqual(rule["grew"], rule["bytes"])
        self.assertEqual(rule["added"], 2)
        self.assertGreater(doc["runway"]["perDay"], 0)
        self.assertGreater(doc["runway"]["days"], 0)

    def test_the_days_writes_are_in_the_data_page(self):
        self.store.add_traffic({(NOW // 60 * 60, "xrnloops"): {"reqs": 5, "writes": 2}})
        self.watcher.minute_tick(NOW)
        self.watcher.sweep(NOW)
        daily = self.watcher.data_doc()["daily"]
        self.assertEqual(daily["writes"]["xrnloops"][-1], 2)


class Renamed(Base):
    """xrnwal was xrnwallet until 2026-11-21. Its own deploy moves its folder, runit service and log
    to the new name, and can land before the hub's or after it. Until it does, the hub reads them
    under the old name; once the new one is there, that wins."""

    def setUp(self):
        super().setUp()
        self.mac.system = lambda: {"uptime": 1000 + self.clock[0] - NOW, "load": [0, 0, 0], "procs": 1}
        self.wal = machine.BY_NAME["xrnwal"]
        at = machine.SERVICES.index(self.wal)
        # The phone before xrnwal's deploy: its service, program, data and log all under the old name.
        shutil.rmtree(os.path.join(self.phone.prefix, "var", "service", "xrnwal-sync"))
        self.phone.service("xrnwallet-sync", 100 + at, since=NOW - 86400)
        self.phone.process(9001 + at, parent=9000, name="runsv", argv=["runsv", "xrnwallet-sync"])
        self.old_home = os.path.join(self.phone.home, "xrnwallet-sync")
        self.phone.process(100 + at, parent=9001 + at, argv=["python", f"{self.old_home}/xrnwallet-sync.py", "--port", "5237"])
        write_json(os.path.join(self.old_home, "data", "users", USER, "state.json"),
                   {"data": {"bills": [{"id": 1, "atMillis": NOW * 1000}]}})

    def run_minutes(self, count=1):
        for _ in range(count):
            self.clock[0] += 60
            self.watcher.minute_tick(self.clock[0])

    def open_kinds(self):
        return sorted((i["kind"], i["target"]) for i in self.store.incidents() if i["ended"] is None)

    def test_the_wallet_is_read_from_the_old_folder_until_the_new_one_is_there(self):
        src = self.source(USER)
        self.assertEqual([b["id"] for b in src.wallet()["bills"]], [1])
        write_json(os.path.join(self.phone.home, "xrnwal-sync", "data", "users", USER, "state.json"),
                   {"data": {"bills": [{"id": 1}, {"id": 2}]}})
        self.assertEqual([b["id"] for b in src.wallet()["bills"]], [1, 2])  # no restart needed

    def test_its_records_are_counted_in_the_old_folder(self):
        apps = {a["name"]: a for a in self.inventory.sweep(NOW)["apps"]}
        self.assertEqual(apps["xrnwal"]["records"], 1)
        self.assertGreater(apps["xrnwal"]["bytes"], 0)

    def test_its_service_is_watched_under_the_old_name_and_restarts_there(self):
        self.assertEqual(self.mac.service(self.wal)["state"], "run")
        self.run_minutes()
        self.assertEqual(self.open_kinds(), [])
        self.watcher.restart("xrnwal")
        self.assertEqual(self.phone.ran[-1][2], os.path.join(self.phone.prefix, "var", "service", "xrnwallet-sync"))

    def test_a_copy_left_running_under_the_old_name_is_still_caught(self):
        self.phone.process(555, parent=1, argv=["python", f"{self.old_home}/xrnwallet-sync.py", "--port", "5237"])
        self.run_minutes()
        self.assertEqual(self.open_kinds(), [("unsupervised", "xrnwal")])
        self.assertEqual(self.store.incidents()[0]["detail"], "pid 555")

    def test_once_its_deploy_has_moved_it_the_new_names_win(self):
        self.phone.service("xrnwal-sync", 600, since=NOW)
        self.phone.log("xrnwal-sync", "")
        self.assertEqual(self.mac.service(self.wal)["pid"], 600)
        self.assertEqual(self.mac.log_dir(self.wal), os.path.join(self.phone.prefix, "var", "log", "sv", "xrnwal-sync"))
        self.watcher.restart("xrnwal")
        self.assertEqual(self.phone.ran[-1][2], os.path.join(self.phone.prefix, "var", "service", "xrnwal-sync"))

    def test_its_log_is_read_from_the_old_folder_and_then_from_the_new(self):
        self.phone.log("xrnwallet-sync", f'{stamp_of(self.ago(seconds=20))} 203.0.113.9 "GET /state HTTP/1.1" 200 -')
        self.watcher.minute_tick(self.clock[0])
        self.phone.log("xrnwal-sync", f'{stamp_of(self.ago(seconds=-30))} 203.0.113.9 "PUT /state HTTP/1.1" 200 -')
        self.run_minutes()
        total = self.store.totals(3600, self.clock[0])["xrnwal"]
        self.assertEqual((total["reqs"], total["writes"]), (2, 1))

    def test_its_history_under_the_old_name_goes_on_under_the_new(self):
        folder = os.path.join(self.root, "before")
        os.makedirs(folder)
        db = sqlite3.connect(os.path.join(folder, "monitor.sqlite"))
        db.executescript(metrics.SCHEMA)
        db.executemany("INSERT INTO service(span, at, name, up) VALUES(60, ?, ?, ?)",
                       [(NOW - 120, "xrnwallet", 1), (NOW - 60, "xrnwallet", 1), (NOW - 60, "xrnwal", 0)])
        db.execute("INSERT INTO traffic(span, at, name, reqs) VALUES(60, ?, 'xrnwallet', 5)", (NOW - 120,))
        db.execute("INSERT INTO incidents(kind, target, started) VALUES('down', 'xrnwallet', ?)", (NOW - 300,))
        db.execute("INSERT INTO log_state VALUES('xrnwallet', 42, 1000)")
        db.commit()
        db.close()
        store = metrics.Store(folder)
        self.addCleanup(store.db.close)
        self.assertEqual(store.rows("SELECT at, name, up FROM service ORDER BY at"),
                         [(NOW - 120, "xrnwal", 1), (NOW - 60, "xrnwal", 0)])  # a row under the new name stays
        self.assertEqual(store.rows("SELECT name, reqs FROM traffic"), [("xrnwal", 5)])
        self.assertEqual(list(store.open_incidents()), [("down", "xrnwal")])
        self.assertEqual(store.log_position("xrnwal"), (42, 1000))  # its log goes on from the same line


class RenamedNoteAndImg(Base):
    """xrnnote was xrnnotes and xrnimg was xrnphotos until 2026-11-21. As with xrnwal, each one's
    deploy moves its folder, runit service and log, before the hub's or after it."""

    OLD = {"xrnnote": "xrnnotes-sync", "xrnimg": "xrnphotos-sync"}

    def setUp(self):
        super().setUp()
        self.mac.system = lambda: {"uptime": 1000 + self.clock[0] - NOW, "load": [0, 0, 0], "procs": 1}
        # The phone before their deploys: each one's service, program and data under its old name.
        self.old_home = {}
        for name, old in self.OLD.items():
            service = machine.BY_NAME[name]
            at = machine.SERVICES.index(service)
            shutil.rmtree(os.path.join(self.phone.prefix, "var", "service", service.runit))
            self.phone.service(old, 100 + at, since=NOW - 86400)
            self.phone.process(9001 + at, parent=9000, name="runsv", argv=["runsv", old])
            self.old_home[name] = os.path.join(self.phone.home, old)
            self.phone.process(100 + at, parent=9001 + at,
                               argv=["python", f"{self.old_home[name]}/{old}.py", "--port", str(service.port)])
        notes = os.path.join(self.old_home["xrnnote"], "data", "users", USER)
        self.phone.put(os.path.join(notes, "notes.sqlite"), "\x01" + "made-up ciphertext" * 10)
        library = os.path.join(self.old_home["xrnimg"], "data", "users", USER)
        os.makedirs(library)
        with sqlite3.connect(os.path.join(library, "photos.sqlite")) as db:
            db.execute("CREATE TABLE items(id TEXT, created INTEGER, trashed_at INTEGER)")
            db.executemany("INSERT INTO items VALUES(?, ?, NULL)", [("a", NOW * 1000), ("b", NOW * 1000)])
        db.close()

    def open_kinds(self):
        return sorted((i["kind"], i["target"]) for i in self.store.incidents() if i["ended"] is None)

    def test_their_records_are_counted_in_the_old_folders_until_the_new_ones_are_there(self):
        doc = self.inventory.sweep(NOW)
        apps = {a["name"]: a for a in doc["apps"]}
        self.assertEqual((apps["xrnnote"]["records"], apps["xrnnote"]["unit"]), (1, "files"))
        self.assertEqual((apps["xrnimg"]["records"], apps["xrnimg"]["unit"]), (2, "items"))
        self.assertEqual(doc["fed"]["apps"]["xrnimg"][-1], 2)
        # Once a deploy has moved the library, the new folder is read, with no restart.
        os.rename(self.old_home["xrnimg"], os.path.join(self.phone.home, "xrnimg-sync"))
        self.assertEqual({a["name"]: a for a in self.inventory.sweep(NOW)["apps"]}["xrnimg"]["records"], 2)

    def test_notes_last_sync_is_read_from_the_old_folder(self):
        self.assertEqual(self.source(USER).path("xrnnote", "notes.sqlite"),
                         os.path.join(self.old_home["xrnnote"], "data", "users", USER, "notes.sqlite"))

    def test_their_services_are_watched_under_the_old_names_and_restart_there(self):
        self.clock[0] += 60
        self.watcher.minute_tick(self.clock[0])
        self.assertEqual(self.open_kinds(), [])
        for name, old in self.OLD.items():
            self.assertEqual(self.mac.service(machine.BY_NAME[name])["state"], "run", name)
            self.watcher.restart(name)
            self.assertEqual(self.phone.ran[-1][2], os.path.join(self.phone.prefix, "var", "service", old))

    def test_a_copy_left_running_under_an_old_name_is_still_caught(self):
        self.phone.process(555, parent=1, argv=["python", f"{self.old_home['xrnimg']}/xrnphotos-sync.py", "--port", "5242"])
        self.clock[0] += 60
        self.watcher.minute_tick(self.clock[0])
        self.assertEqual(self.open_kinds(), [("unsupervised", "xrnimg")])

    def test_their_history_under_the_old_names_goes_on_under_the_new(self):
        folder = os.path.join(self.root, "before")
        os.makedirs(folder)
        db = sqlite3.connect(os.path.join(folder, "monitor.sqlite"))
        db.executescript(metrics.SCHEMA)
        db.executemany("INSERT INTO service(span, at, name, up) VALUES(60, ?, ?, 1)",
                       [(NOW - 120, "xrnnotes"), (NOW - 120, "xrnphotos")])
        db.execute("INSERT INTO data_days(day, app, bytes, records) VALUES('2026-11-20', 'xrnphotos', 10, 3)")
        db.commit()
        db.close()
        store = metrics.Store(folder)
        self.addCleanup(store.db.close)
        self.assertEqual(sorted(store.rows("SELECT name FROM service")), [("xrnimg",), ("xrnnote",)])
        self.assertEqual(store.rows("SELECT app, records FROM data_days"), [("xrnimg", 3)])


class LogSearch(Base):
    def setUp(self):
        super().setUp()
        at = datetime(2026, 11, 6, 1, 0, 0)
        self.phone.log("xrnloops-sync", f'{stamp_of(at)} 203.0.113.9 "GET /a?key=hunter2 HTTP/1.1" 200 -')
        self.phone.log("xrnhub", f'{stamp_of(at + timedelta(seconds=5))} 203.0.113.9 "GET /boom HTTP/1.1" 500 -')
        self.phone.log("sshd", f"{stamp_of(at + timedelta(seconds=9))} [WARNING] odd thing")

    def test_newest_first_across_every_service(self):
        found = self.watcher.log_lines("", "info", "", 10)
        self.assertEqual([r["service"] for r in found], ["ssh", "xrnhub", "xrnloops"])

    def test_one_service(self):
        self.assertEqual(len(self.watcher.log_lines("xrnhub", "info", "", 10)), 1)

    def test_by_level(self):
        self.assertEqual([r["level"] for r in self.watcher.log_lines("", "warn", "", 10)], ["warn", "error"])
        self.assertEqual([r["service"] for r in self.watcher.log_lines("", "error", "", 10)], ["xrnhub"])

    def test_by_words_any_case(self):
        self.assertEqual(len(self.watcher.log_lines("", "info", "BOOM", 10)), 1)

    def test_a_query_string_is_neither_shown_nor_searched(self):
        self.assertEqual(self.watcher.log_lines("", "info", "hunter2", 10), [])
        self.assertNotIn("hunter2", json.dumps(self.watcher.log_lines("", "info", "", 10)))

    def test_xrnfits_requests_to_the_hub_are_its_lines(self):
        at = datetime(2026, 11, 6, 1, 0, 20)
        self.phone.log("xrnhub", f'{stamp_of(at)} 203.0.113.9 "POST /xrnhub/api/health HTTP/1.1" 200 -')
        self.phone.log("xrnhub", f'{stamp_of(at + timedelta(seconds=1))} 203.0.113.9 "GET /api/v1/health/steps?every=30 HTTP/1.1" 200 -')
        self.phone.log("xrnhub", f"{stamp_of(at + timedelta(seconds=2))} [INFO] health from xrnfit-watch")
        fit = self.watcher.log_lines("xrnfit", "info", "", 10)
        self.assertEqual([r["text"] for r in fit], [
            '203.0.113.9 "GET /api/v1/health/steps HTTP/1.1" 200 -',
            '203.0.113.9 "POST /xrnhub/api/health HTTP/1.1" 200 -',
        ])
        self.assertEqual({r["service"] for r in fit}, {"xrnfit"})
        # The hub keeps its own lines, the one that isn't a request among them.
        hub = self.watcher.log_lines("xrnhub", "info", "", 10)
        self.assertEqual(len(hub), 2)
        self.assertNotIn("xrnfit", [r["service"] for r in hub])
        # And every service's list names xrnfit's lines as its own.
        every = [r["service"] for r in self.watcher.log_lines("", "info", "", 10)]
        self.assertEqual(every.count("xrnfit"), 2)


class LogCounts(Base):
    """The Logs page's figures: lines by level, and warnings and errors grouped by what they say."""

    def write(self, runit, *texts, seconds=20):
        when = self.ago(seconds=seconds)
        self.phone.log(runit, *(f"{stamp_of(when)} {text}" for text in texts))

    def totals(self, only=None):
        levels = self.store.log_history("1h", self.clock[0], only)["levels"]
        return {level: sum(values) for level, values in levels.items()}

    def test_lines_count_by_level_and_routine_requests_are_left_to_traffic(self):
        self.write("xrnloops-sync",
                   '203.0.113.9 "GET /api/changes?since=1 HTTP/1.1" 200 -',
                   '203.0.113.9 "GET /api/changes HTTP/1.1" 304 -',
                   '198.51.100.7 "GET /admin.php HTTP/1.1" 404 -',
                   '203.0.113.9 "POST /api/changes HTTP/1.1" 500 -',
                   "sync from phone: 4 changes",
                   "[WARNING] slow query")
        self.watcher.minute_tick(self.clock[0])
        self.assertEqual(self.totals(), {"info": 1, "warn": 2, "error": 1})
        self.assertEqual(self.store.totals(3600, self.clock[0])["xrnloops"]["reqs"], 4)

    def test_a_message_logged_again_is_one_row_with_its_count_and_latest_line(self):
        self.write("xrnloops-sync", "warning: slow query took 1.2s", seconds=40)
        self.write("xrnloops-sync", "warning: slow query took 3.4s", "warning: slow query took 0.9s")
        self.watcher.minute_tick(self.clock[0])
        groups = self.store.log_groups(self.clock[0] - 3600)
        self.assertEqual(len(groups), 1)
        self.assertEqual((groups[0]["count"], groups[0]["level"], groups[0]["service"]), (3, "warn", "xrnloops"))
        self.assertEqual(groups[0]["text"], "warning: slow query took 0.9s")
        # The newest lines still come back one by one, and only that message's when asked.
        self.write("xrnloops-sync", "error: something else")
        lines = self.watcher.log_lines("xrnloops", "info", "", 10, said=groups[0]["sig"])
        self.assertEqual(len(lines), 3)

    def test_errors_come_before_warnings_however_many_there_are(self):
        self.write("xrnrule-sync", *["[WARNING] busy"] * 5, "Traceback (most recent call last):")
        self.watcher.minute_tick(self.clock[0])
        self.assertEqual([g["level"] for g in self.store.log_groups(self.clock[0] - 3600)], ["error", "warn"])

    def test_xrnfits_refused_requests_to_the_hub_are_its_own(self):
        self.write("xrnhub", '203.0.113.9 "POST /api/health HTTP/1.1" 401 -', "[ERROR] the hub's own")
        self.watcher.minute_tick(self.clock[0])
        self.assertEqual({(g["service"], g["level"]) for g in self.store.log_groups(self.clock[0] - 3600)},
                         {("xrnfit", "warn"), ("xrnhub", "error")})
        self.assertEqual(self.totals("xrnfit"), {"info": 0, "warn": 1, "error": 0})

    def test_a_scanner_cannot_fill_the_messages(self):
        self.write("xrnloops-sync", *[f'198.51.100.7 "GET /probe-{chr(97 + i % 26)}{i // 26} HTTP/1.1" 404 -'
                                      for i in range(MAX_MESSAGES_A_DAY + 30)])
        self.watcher.minute_tick(self.clock[0])
        groups = self.store.log_groups(self.clock[0] - 3600, limit=10000)
        self.assertEqual(len(groups), MAX_MESSAGES_A_DAY + 1)
        other = next(g for g in groups if g["sig"] == "(other)")
        self.assertEqual(other["count"], 30)

    def test_routine_requests_can_be_left_out_of_the_lines(self):
        self.write("xrnloops-sync", '203.0.113.9 "GET /api/changes HTTP/1.1" 200 -', "sync from phone: 4 changes",
                   '203.0.113.9 "GET /nope HTTP/1.1" 404 -')
        texts = {r["text"] for r in self.watcher.log_lines("xrnloops", "info", "", 10, routine=False)}
        self.assertEqual(texts, {'203.0.113.9 "GET /nope HTTP/1.1" 404 -', "sync from phone: 4 changes"})
        self.assertEqual(len(self.watcher.log_lines("xrnloops", "info", "", 10)), 3)

    def test_lines_read_before_counting_began_are_counted_once(self):
        # A monitor from before this read the log and saved how far it got, without counting.
        self.write("xrnloops-sync", "[WARNING] before", '203.0.113.9 "GET /a HTTP/1.1" 200 -', seconds=90)
        path = os.path.join(self.phone.prefix, "var", "log", "sv", "xrnloops-sync")
        earlier = logscan.Follower(path)
        earlier.read()
        self.store.set_log_positions([("xrnloops", earlier.inode, earlier.offset)])
        self.write("xrnloops-sync", "[WARNING] after")
        watcher = Watcher(self.mac, self.store, self.inventory, clock=lambda: self.clock[0])
        watcher.minute_tick(self.clock[0])
        self.clock[0] += 60
        watcher.minute_tick(self.clock[0])
        self.assertEqual(self.totals()["warn"], 2)
        # The old request was traffic already; only the new pass's lines are.
        self.assertNotIn("xrnloops", self.store.totals(3600, self.clock[0]))

    def test_before_reads_only_what_was_read_and_only_once(self):
        folder = os.path.dirname(self.phone.log("xrnloops-sync", "a1", name="@400000001.s"))
        self.phone.log("xrnloops-sync", "b1", "b2")
        follow = logscan.Follower(folder)
        follow.read()
        self.phone.log("xrnloops-sync", "c1")
        self.assertEqual(logscan.before(folder, follow.inode, follow.offset, 1000), ["a1", "b1", "b2"])
        self.assertEqual(logscan.before(folder, follow.inode, follow.offset, 4), ["b2"])
        self.assertEqual(logscan.before(folder, 0, 0, 1000), [])

    def test_old_messages_and_counts_go(self):
        self.write("xrnloops-sync", "[WARNING] long ago")
        self.watcher.minute_tick(self.clock[0])
        later = self.clock[0] + metrics.KEEP_DAYS * 86400 + 7200
        self.store.roll_up(later)
        self.assertEqual(self.store.log_groups(0), [])
        self.assertEqual(self.store.rows("SELECT count(*) FROM log_counts WHERE span = 60")[0][0], 0)

    def test_a_message_is_its_words_without_what_changes(self):
        said = logscan.signature
        self.assertEqual(said("warning: slow query took 1.2s"), said("warning: slow query took 13.75s"))
        self.assertEqual(said("Failed password for root from 192.0.2.1 port 3345 ssh2"),
                         said("Failed password for root from 2001:db8::9 port 41 ssh2"))
        self.assertEqual(said("[2026-11-06 01:09:14 +0000] [12/Thread-3] [WARNING] Failed login attempt from 127.0.0.1: 'me'"),
                         "[WARNING] Failed login attempt from …: '…'")
        self.assertEqual(said('203.0.113.9 "GET /wp-admin?x=1 HTTP/1.1" 404 -'), "GET /wp-admin 404")
        self.assertNotEqual(said("[WARNING] slow"), said("[ERROR] slow"))

    def test_lines_by_level_start_where_counting_began(self):
        self.assertEqual(self.store.log_history("24h", self.clock[0])["at"], [])
        self.write("xrnloops-sync", "[WARNING] first")
        self.watcher.minute_tick(self.clock[0])
        doc = self.store.log_history("24h", self.clock[0])
        self.assertLessEqual(len(doc["at"]), 2)
        self.assertEqual(sum(doc["services"]["xrnloops"]["warn"]), 1)


class LogRatings(Base):
    """What the services' real logs showed was rated wrong (the audit of 2026-11-19)."""

    TRACE = [
        "----------------------------------------",
        "Exception occurred during processing of request from ('127.0.0.1', 53412)",
        "Traceback (most recent call last):",
        '  File "/usr/lib/python3.12/socketserver.py", line 692, in process_request_thread',
        "    self.finish_request(request, client_address)",
        "BrokenPipeError: [Errno 32] Broken pipe",
        "----------------------------------------",
    ]
    CHAINED = [
        "Traceback (most recent call last):",
        '  File "sync.py", line 40, in load',
        "KeyError: 'seq'",
        "",
        "During handling of the above exception, another exception occurred:",
        "",
        "Traceback (most recent call last):",
        '  File "sync.py", line 900, in main',
        "OSError: [Errno 98] Address already in use",
    ]

    def write(self, runit, *texts, seconds=20):
        when = self.ago(seconds=seconds)
        self.phone.log(runit, *(f"{stamp_of(when)} {text}" for text in texts))

    def counted(self):
        self.watcher.minute_tick(self.clock[0])
        levels = self.store.log_history("1h", self.clock[0])["levels"]
        return {level: sum(values) for level, values in levels.items()}

    def test_a_traceback_is_one_error_that_says_its_cause(self):
        self.write("xrnloops-sync", *self.TRACE, "listening on 127.0.0.1")
        self.assertEqual(self.counted(), {"info": 1, "warn": 0, "error": 1})
        groups = self.store.log_groups(self.clock[0] - 3600)
        self.assertEqual([g["text"] for g in groups], ["BrokenPipeError: [Errno 32] Broken pipe"])

    def test_a_chained_traceback_is_one_error_that_says_its_last_exception(self):
        self.write("xrnloops-sync", *self.CHAINED, *self.CHAINED)
        self.assertEqual(self.counted()["error"], 2)
        groups = self.store.log_groups(self.clock[0] - 3600)
        self.assertEqual([(g["text"], g["count"]) for g in groups], [("OSError: [Errno 98] Address already in use", 2)])

    def test_a_traceback_cut_by_a_pass_is_still_one(self):
        now = self.ago(seconds=0)
        self.phone.log("xrnloops-sync", *(f"{stamp_of(now)} {t}" for t in self.CHAINED[:4]))
        self.watcher.minute_tick(self.clock[0])
        self.phone.log("xrnloops-sync", *(f"{stamp_of(now)} {t}" for t in self.CHAINED[4:]))
        self.clock[0] += 60
        self.assertEqual(self.counted()["error"], 1)

    def test_an_exceptions_own_line_is_an_error_anywhere(self):
        self.assertEqual(logscan.level_of("json.decoder.JSONDecodeError: Expecting value: line 1 column 1"), "error")
        self.assertEqual(logscan.level_of("BrokenPipeError: [Errno 32] Broken pipe"), "error")
        self.assertEqual(logscan.level_of("ValueErrors happen"), "info")

    def test_a_password_challenge_answered_at_once_is_routine(self):
        when = self.ago(seconds=20)
        later = when + timedelta(milliseconds=400)
        self.phone.log("xrnhome-sync",
                       f'{stamp_of(when)} 203.0.113.9 "GET /photos/a.jpg HTTP/1.1" 401 -',
                       f'{stamp_of(later)} 203.0.113.9 "GET /photos/a.jpg HTTP/1.1" 200 -',
                       f'{stamp_of(later)} 198.51.100.7 "GET /state HTTP/1.1" 401 -',
                       f'{stamp_of(later)} 203.0.113.9 "GET /state HTTP/1.1" 200 -')
        self.assertEqual(self.counted()["warn"], 1)  # only the 401 nobody answered
        lines = self.watcher.log_lines("xrnhome", "warn", "", 10)
        self.assertEqual([r["text"] for r in lines], ['198.51.100.7 "GET /state HTTP/1.1" 401 -'])
        self.assertEqual(len(self.watcher.log_lines("xrnhome", "info", "", 10, routine=False)), 1)

    def test_a_challenge_answered_in_the_next_pass_is_routine(self):
        now = self.ago(seconds=0)
        self.phone.log("xrnhome-sync", f'{stamp_of(now)} 203.0.113.9 "GET /state HTTP/1.1" 401 -')
        self.watcher.minute_tick(self.clock[0])
        self.phone.log("xrnhome-sync", f'{stamp_of(now + timedelta(milliseconds=600))} 203.0.113.9 "GET /state HTTP/1.1" 200 -')
        self.clock[0] += 60
        self.assertEqual(self.counted()["warn"], 0)

    def test_an_if_match_conflict_is_routine_and_a_missing_head_only_a_warning(self):
        self.write("xrnrule-sync", '203.0.113.9 "PUT /state HTTP/1.1" 412 -', '203.0.113.9 "HEAD /state HTTP/1.1" 501 -',
                   '203.0.113.9 "POST /state HTTP/1.1" 500 -')
        self.assertEqual(self.counted(), {"info": 0, "warn": 1, "error": 1})

    def test_a_bot_that_isnt_ok_is_a_warning_on_the_server_page(self):
        # xrninsta logs its bots' state when it changes. From 18 to 21 Nov 2026 the song bot answered
        # no one, and when it learned to say why ("bot: badchat: ..."), the hub counted that as info,
        # so the Server page never showed it.
        self.write("xrninsta",
                   'bot: badchat: the "chat" in bot.json is "<your chat id>", which isn\'t a chat id '
                   "(a number such as 123456789), so it answers no one",
                   'bot: update 9 from chat 1234 ignored: the "chat" in bot.json is "<your chat id>"',
                   'covers bot: otherchat: a message came from chat 4242, but the "chat" under "covers" in '
                   "bot.json is 1234",
                   "covers: missing: no day-12.jpg in /data/covers",
                   "covers: first start, after 2am: Day 12's cover is taken as sent by the Apps Script",
                   "bot: ok")
        self.assertEqual(self.counted(), {"info": 3, "warn": 3, "error": 0})
        groups = self.store.log_groups(self.clock[0] - 3600)
        self.assertEqual({(g["service"], g["level"]) for g in groups}, {("xrninsta", "warn")})
        self.assertEqual(sorted(": ".join(g["text"].split(": ")[:2]) for g in groups),
                         ["bot: badchat", "covers bot: otherchat", "covers: missing"])

    def test_a_bots_state_is_a_warning_unless_it_is_ok(self):
        for state in ("badchat", "nochat", "otherchat", "refused", "unreachable", "webhook", "twice", "error"):
            for name in ("bot", "covers bot"):
                self.assertEqual(logscan.level_of(f"{name}: {state}: why"), "warn", f"{name}: {state}")
        for state in ("missing", "nozone", "refused", "unreachable", "badchat"):
            self.assertEqual(logscan.level_of(f"covers: {state}: why"), "warn", state)
        self.assertEqual(logscan.level_of("bot: error: ConnectionResetError: [Errno 104] reset"), "warn")
        for line in ("bot: ok", "covers bot: ok", "covers: ok", "bot: update 9 from chat 4242 ignored: why",
                     "covers: first start, after 2am: why", "robot: badchat: not xrninsta's"):
            self.assertEqual(logscan.level_of(line), "info", line)

    def test_lines_that_only_sound_alarming_are_info(self):
        self.write("radicale", "[2026-11-06 01:09:14 +0000] [1/MainThread] [WARNING] Logging level set to: 'WARNING'",
                   "203.0.113.9 connection dropped: BrokenPipeError(32, 'Broken pipe')",
                   "203.0.113.9 caller left a long-poll after 25s")
        self.assertEqual(self.counted(), {"info": 3, "warn": 0, "error": 0})

    def test_the_newest_lines_show_a_traceback_as_its_cause(self):
        self.write("xrnloops-sync", *self.TRACE)
        lines = self.watcher.log_lines("xrnloops", "info", "", 10)
        self.assertEqual([(r["level"], r["text"]) for r in lines], [("error", "BrokenPipeError: [Errno 32] Broken pipe")])


class Timeline(Base):
    def test_every_incident_of_the_last_month_is_on_the_timeline(self):
        for i in range(70):
            self.store.note_incident("restart", "xrnloops", NOW - i * 3600)
        self.store.note_incident("down", "xrnrule", NOW - 40 * 86400, ended=NOW - 40 * 86400 + 600)
        self.store.open_incident("unplugged", "phone", NOW - 50 * 86400)
        doc = self.watcher.services_doc()
        self.assertEqual(len(doc["incidents"]), 60)  # as the phone app reads it
        kinds = [(i["kind"], i["target"]) for i in doc["timeline"]]
        self.assertEqual(kinds.count(("restart", "xrnloops")), 70)
        self.assertNotIn(("down", "xrnrule"), kinds)
        self.assertIn(("unplugged", "phone"), kinds)  # still going, however long ago it began


class FitDays(Base):
    """xrnfit's row on Services, in place of an uptime strip: readings that came in a day."""

    def days_back(self, n):
        today = datetime.fromtimestamp(NOW).date()
        return [(today - timedelta(days=i)).isoformat() for i in range(n - 1, -1, -1)]

    def test_each_day_is_its_count_from_the_first_day_anything_came(self):
        self.watcher.data = {"fed": {"days": self.days_back(30), "apps": {"xrnfit": [0] * 26 + [500, 0, 40, 7]}}}
        days = self.watcher.services_doc()["fitDays"]
        self.assertEqual(len(days), 30)
        self.assertEqual(days, [None] * 26 + [500, 0, 40, 7])

    def test_a_day_the_sweep_didnt_cover_is_unknown(self):
        # The sweep ran yesterday: today isn't counted yet, and isn't nothing.
        yesterday = self.days_back(31)[:-1]
        self.watcher.data = {"fed": {"days": yesterday, "apps": {"xrnfit": [3] * 30}}}
        days = self.watcher.fit_days(NOW)
        self.assertEqual(days[-1], None)
        self.assertEqual(days[:-1], [3] * 29)

    def test_before_the_first_sweep_or_with_nothing_sent_every_day_is_unknown(self):
        self.watcher.data = None
        self.assertEqual(self.watcher.fit_days(NOW), [None] * 30)
        self.watcher.data = {"fed": {"days": self.days_back(30), "apps": {"xrnloops": [1] * 30}}}
        self.assertEqual(self.watcher.fit_days(NOW), [None] * 30)


def _utc():
    from datetime import timezone

    return timezone.utc


if __name__ == "__main__":
    unittest.main()

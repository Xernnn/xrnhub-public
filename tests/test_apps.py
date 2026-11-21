"""The Apps page: the catalogue, what's joined into it, and its endpoint."""
import base64
import http.client
import json
import os
import re
import shutil
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from unittest import mock

import bcrypt

import support
from support import USER, habit, make_loops, make_rule, write_json

import xrnhub
from hublib import apps, health, login, machine
from hublib.log import HubLog
from hublib.sources import Sources

PW = "correct horse"
NOW = int(datetime(2026, 11, 17, 12, 0, 0).timestamp())
PUBLIC = {"xrncal-desktop", "xrnchips", "xrnhome", "xrnsoc", "xrntube"}
ANDROID = os.path.join(support.ROOT, "android", "app", "src", "main")
WEB = os.path.join(support.ROOT, "web")


def release_files(home, folder, phone=None, extension=None):
    top = os.path.join(home, folder, "data", "update")
    os.makedirs(top, exist_ok=True)
    if phone is not None:
        with open(os.path.join(top, "app.json"), "w", encoding="utf-8") as f:
            f.write(phone if isinstance(phone, str) else json.dumps(phone))
    if extension is not None:
        with open(os.path.join(top, "updates.xml"), "w", encoding="utf-8") as f:
            f.write("<?xml version='1.0' encoding='UTF-8'?>\n<gupdate xmlns='http://www.google.com/update2/response' "
                    "protocol='2.0'>\n  <app appid='abcdefghijklmnopabcdefghijklmnop'>\n    <updatecheck "
                    f"codebase='https://example.test/x.crx' version='{extension}' hash_sha256='00' />\n  </app>\n</gupdate>\n")


class Catalogue(unittest.TestCase):
    def test_there_are_fifteen_projects_each_once(self):
        self.assertEqual(len(apps.APPS), 15)
        self.assertEqual(len({a.name for a in apps.APPS}), 15)
        self.assertEqual(len({a.repo for a in apps.APPS}), 15)

    def test_every_project_sits_in_a_group_and_no_group_is_empty(self):
        groups = [g.id for g in apps.GROUPS]
        self.assertEqual(len(groups), len(set(groups)))
        self.assertEqual({a.group for a in apps.APPS}, set(groups))

    def test_each_runs_somewhere_it_can(self):
        for a in apps.APPS:
            self.assertTrue(a.runs, a.name)
            self.assertLessEqual(set(a.runs), set(apps.RUNS_ON), a.name)
            self.assertEqual(len(a.runs), len(set(a.runs)), a.name)

    def test_the_public_ones_are_the_five_public_repos(self):
        self.assertEqual({a.repo for a in apps.APPS if a.public}, PUBLIC)

    def test_an_app_with_a_server_has_a_service_the_monitor_watches(self):
        for a in apps.APPS:
            if a.service:
                self.assertIn(a.service, machine.BY_NAME, a.name)
                self.assertIn("Server", a.runs, a.name)
        watched = {s.name for s in machine.SERVICES if s.port}
        self.assertEqual({a.service for a in apps.APPS if a.service}, watched)

    def test_every_app_on_the_server_names_the_service_it_talks_to(self):
        for a in apps.APPS:
            if "Server" in a.runs:
                self.assertIn(apps.server_of(a), machine.BY_NAME, a.name)
            else:
                self.assertIsNone(apps.server_of(a), a.name)
            if a.kept_by:  # kept by another service only when it has none of its own
                self.assertIsNone(a.service, a.name)
        self.assertEqual(apps.server_of(apps.BY_NAME["xrnfit"]), "xrnhub")

    def test_every_logo_is_in_the_page(self):
        for a in apps.APPS:
            self.assertTrue(os.path.isfile(os.path.join(WEB, a.logo)), a.logo)
            self.assertTrue(xrnhub.WEB_NAME.fullmatch(a.logo), a.logo)

    def test_every_project_has_a_logo_in_the_phone_app(self):
        for a in apps.APPS:
            name = a.name.replace("-", "_")
            self.assertTrue(os.path.isfile(os.path.join(ANDROID, "res", "drawable-nodpi", f"logo_{name}.png")), a.name)

    def test_the_phone_app_may_look_up_every_other_package(self):
        # Android 11 on hides other apps' packages from an app that doesn't name them.
        with open(os.path.join(ANDROID, "AndroidManifest.xml"), encoding="utf-8") as f:
            manifest = f.read()
        queries = re.search(r"<queries>(.*?)</queries>", manifest, re.S)
        self.assertIsNotNone(queries)
        named = set(re.findall(r'<package android:name="([\w.]+)"', queries[1]))
        self.assertEqual(named, {a.package for a in apps.APPS if a.package} - {"com.xern.xrnhub"})

    def test_packages_are_the_familys_ids(self):
        for a in apps.APPS:
            if a.package:
                self.assertEqual(a.package, f"com.xern.{a.name}")
                self.assertIn("Phone", a.runs, a.name)

    def test_web_pages_are_on_this_host(self):
        for a in apps.APPS:
            if a.web:
                self.assertRegex(a.web, r"^/xrn[a-z]+/$")
                self.assertEqual(a.web, f"/{a.service}/")


class Releasing(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="xrnhub-releases-")
        self.addCleanup(shutil.rmtree, self.home)
        self.releases = apps.Releases(self.home)

    def test_a_phone_release_comes_from_app_json(self):
        release_files(self.home, "xrnrule-sync", phone={"versionCode": 16, "versionName": "0.2.16", "apk": "x.apk"})
        self.assertEqual(self.releases.of("xrnrule-sync"), [{"kind": "phone", "version": "0.2.16", "code": 16}])

    def test_an_extension_release_comes_from_updates_xml(self):
        release_files(self.home, "xrntube-sync", extension="2.1.10")
        self.assertEqual(self.releases.of("xrntube-sync"), [{"kind": "extension", "version": "2.1.10", "code": None}])

    def test_a_folder_can_hand_out_both(self):
        release_files(self.home, "xrnrule-sync", phone={"versionCode": 16, "versionName": "0.2.16"}, extension="2.3.6")
        self.assertEqual([r["kind"] for r in self.releases.of("xrnrule-sync")], ["phone", "extension"])

    def test_no_update_folder_is_no_release(self):
        self.assertEqual(self.releases.of("xrnwal-sync"), [])

    def test_a_renamed_service_hands_out_from_its_old_folder_until_its_deploy_moves_it(self):
        release_files(self.home, "xrnwallet-sync", phone={"versionCode": 30, "versionName": "0.5.3"})
        self.assertEqual(self.releases.of("xrnwal-sync"), [{"kind": "phone", "version": "0.5.3", "code": 30}])
        release_files(self.home, "xrnwal-sync", phone={"versionCode": 31, "versionName": "0.6.0"})
        self.assertEqual(self.releases.of("xrnwal-sync"), [{"kind": "phone", "version": "0.6.0", "code": 31}])

    def test_a_release_file_that_says_something_odd_is_left_out(self):
        for phone in ("not json", "[1, 2]", {"versionName": "<b>1</b>", "versionCode": 3}, {"versionName": 2},
                      {"versionName": "1.0-beta"}, {"versionName": "1." * 30}):
            with self.subTest(phone=phone):
                release_files(self.home, "a-sync", phone=phone)
                self.assertEqual(apps.Releases(self.home).of("a-sync"), [])
        for extension in ("2.1<script>", "", "1.2.3.4.5"):
            with self.subTest(extension=extension):
                release_files(self.home, "b-sync", extension=extension)
                self.assertEqual(apps.Releases(self.home).of("b-sync"), [])

    def test_a_code_that_isnt_a_whole_number_is_dropped_and_the_name_kept(self):
        for code in (True, "16", 1.5, -1, 2**40):
            release_files(self.home, "a-sync", phone={"versionName": "1.1.0", "versionCode": code})
            self.assertEqual(apps.Releases(self.home).of("a-sync"), [{"kind": "phone", "version": "1.1.0", "code": None}])

    def test_a_huge_file_isnt_read(self):
        release_files(self.home, "a-sync", phone=json.dumps({"versionName": "1.0", "pad": "x" * apps.MAX_RELEASE_BYTES}))
        self.assertEqual(self.releases.of("a-sync"), [])

    def test_a_new_release_is_read_again(self):
        release_files(self.home, "xrnhome-sync", phone={"versionCode": 7, "versionName": "1.1.0"})
        self.assertEqual(self.releases.of("xrnhome-sync")[0]["version"], "1.1.0")
        with mock.patch.object(apps, "phone_release", wraps=apps.phone_release) as parse:
            self.releases.of("xrnhome-sync")
            self.assertEqual(parse.call_count, 0)  # unchanged: not read again
        release_files(self.home, "xrnhome-sync", phone={"versionCode": 8, "versionName": "1.2.0"})
        later = time.time() + 5
        os.utime(os.path.join(self.home, "xrnhome-sync", "data", "update", "app.json"), (later, later))
        self.assertEqual(self.releases.of("xrnhome-sync")[0], {"kind": "phone", "version": "1.2.0", "code": 8})


def service_row(name, state="run", since=NOW - 86400, answers=True):
    return {"name": name, "state": state, "since": since, "answers": answers}


class Joining(unittest.TestCase):
    """The live part, from a watcher made up in place: what the monitor and inventory last saw."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="xrnhub-apps-")
        self.addCleanup(shutil.rmtree, self.root)
        self.home = os.path.join(self.root, "home")
        os.makedirs(self.home)
        self.log = HubLog(os.path.join(self.root, "data"))
        self.src = Sources(self.home, USER)
        self.page = apps.AppsPage(self.home, clock=lambda: NOW)
        days = [(datetime.fromtimestamp(NOW).date() - timedelta(days=i)).isoformat() for i in range(29, -1, -1)]
        self.watcher = SimpleNamespace(
            current={"services": [service_row(s.name) for s in machine.SERVICES]},
            data={
                "at": NOW - 120,
                "apps": [
                    {"name": "xrnloops", "records": 812, "unit": "check-ins"},
                    {"name": "xrnhome", "records": 30, "unit": "items"},
                    {"name": "calendar", "records": 70, "unit": "events"},
                ],
                "fed": {"days": days, "apps": {"xrnloops": [1] * 23 + [0, 2, 3, 0, 4, 5, 6]}},
                "backup": NOW - 5 * 3600,
            },
        )

    def build(self, owner=True):
        doc = self.page.build(USER, owner, self.watcher, self.log, self.src)
        return doc, {a["name"]: a for a in doc["apps"]}

    def test_the_answer_lists_every_project_in_its_groups(self):
        doc, _ = self.build()
        self.assertEqual([a["name"] for a in doc["apps"]], [a.name for a in apps.APPS])
        self.assertEqual([g["id"] for g in doc["groups"]], [g.id for g in apps.GROUPS])
        self.assertEqual(doc["counted"], NOW - 120)
        xrnhome = next(a for a in doc["apps"] if a["name"] == "xrnhome")
        self.assertEqual(xrnhome["repoUrl"], "https://github.com/Xernnn/xrnhome")
        self.assertTrue(xrnhome["public"])
        self.assertEqual(xrnhome["web"], "/xrnhome/")
        self.assertEqual(xrnhome["package"], "com.xern.xrnhome")

    def test_a_service_is_up_only_while_it_runs_and_answers(self):
        self.watcher.current["services"] = [
            service_row("xrnloops"), service_row("xrnrule", state="down", since=NOW - 60),
            service_row("xrnhome", answers=False),
        ]
        _, by = self.build()
        self.assertTrue(by["xrnloops"]["up"])
        self.assertEqual(by["xrnloops"]["since"], NOW - 86400)
        self.assertFalse(by["xrnrule"]["up"])
        self.assertIsNone(by["xrnrule"]["since"])
        self.assertFalse(by["xrnhome"]["up"])  # running, but holding its port without answering
        self.assertIsNone(by["xrnwal"]["up"])  # the monitor hasn't said
        self.assertIsNone(by["xrnmail"]["up"])  # nothing on the server to be up

    def test_xrnfit_is_up_and_down_with_the_hub_that_keeps_its_data(self):
        self.watcher.current["services"] = [service_row("xrnhub", since=NOW - 7200), service_row("xrnwal")]
        _, by = self.build()
        self.assertEqual((by["xrnfit"]["server"], by["xrnfit"]["service"]), ("xrnhub", None))
        self.assertEqual(by["xrnwal"]["server"], "xrnwal")
        self.assertIsNone(by["xrnmail"]["server"])
        self.assertTrue(by["xrnfit"]["up"])
        self.assertEqual(by["xrnfit"]["since"], NOW - 7200)
        self.watcher.current["services"] = [service_row("xrnhub", state="down")]
        _, by = self.build()
        self.assertFalse(by["xrnfit"]["up"])
        self.assertIsNone(by["xrnfit"]["since"])

    def test_what_each_holds_comes_from_the_inventory(self):
        _, by = self.build()
        self.assertEqual((by["xrnloops"]["records"], by["xrnloops"]["unit"]), (812, "check-ins"))
        self.assertEqual(by["xrnloops"]["week"], 20)  # the last seven days: 0 + 2 + 3 + 0 + 4 + 5 + 6
        self.assertEqual(by["xrncal"]["records"], 70)  # the monitor calls it calendar
        self.assertIsNone(by["xrnhome"]["week"])  # items carry no dates to count
        self.assertIsNone(by["xrnwal"]["records"])  # not in this sweep
        self.assertIsNone(by["xrnmail"]["records"])

    def test_xrnfits_records_show_once_the_inventory_counts_them(self):
        _, by = self.build()
        self.assertIsNone(by["xrnfit"]["records"])
        self.watcher.data["apps"].append({"name": "xrnfit", "records": 5120, "unit": "records"})
        self.watcher.data["fed"]["apps"]["xrnfit"] = [0] * 29 + [40]
        _, by = self.build()
        self.assertEqual((by["xrnfit"]["records"], by["xrnfit"]["unit"], by["xrnfit"]["week"]), (5120, "records", 40))

    def test_last_in_is_the_newest_thing_the_app_wrote_here(self):
        make_loops(self.home, [habit("a", "Gym")], [])
        loops = self.src.path("xrnloops", "loop.sqlite")
        os.utime(loops, (NOW - 3600, NOW - 3600))
        make_rule(self.home, {"blocks": {}})
        os.utime(self.src.path("xrnrule", "state.json"), (NOW - 7200, NOW - 7200))
        os.utime(self.src.path("xrnrule", "events.jsonl"), (NOW - 600, NOW - 600))
        support.make_calendar(self.home, "uni", [support.ics("UID:1", "DTSTART:20261117T090000", "SUMMARY:Lecture")])
        os.utime(os.path.join(self.src.calendar.root, "uni"), (NOW - 900, NOW - 900))
        _, by = self.build()
        self.assertEqual(by["xrnloops"]["lastIn"], NOW - 3600)
        self.assertEqual(by["xrnrule"]["lastIn"], NOW - 600)
        self.assertEqual(by["xrncal"]["lastIn"], NOW - 900)
        self.assertIsNone(by["xrnwal"]["lastIn"])

    def test_an_app_with_no_server_of_its_own_is_heard_from_through_the_hub(self):
        self.log.add(USER, [{"app": "xrnmail", "kind": "batch", "at": (NOW - 300) * 1000, "value": 4}])
        health.write(self.log, USER, {"source": "xrnfit-watch", "upsert": [
            {"id": "s1", "kind": "steps", "start": (NOW - 7200) * 1000, "end": (NOW - 3600) * 1000, "value": 900},
        ]}, (NOW - 60) * 1000)
        _, by = self.build()
        self.assertEqual(by["xrnmail"]["lastIn"], NOW - 300)
        self.assertEqual(by["xrnfit"]["lastIn"], NOW - 60)
        self.assertIsNone(by["xrnsoc"]["lastIn"])

    def test_the_home_server_card_shows_the_last_backup(self):
        _, by = self.build()
        self.assertEqual(by["home-server"]["backup"], NOW - 5 * 3600)
        self.assertIsNone(by["xrnhub"]["backup"])

    def test_releases_are_joined_by_service(self):
        release_files(self.home, "xrnrule-sync", phone={"versionCode": 16, "versionName": "0.2.16"}, extension="2.3.6")
        release_files(self.home, "xrntube-sync", extension="2.1.10")
        _, by = self.build()
        self.assertEqual([r["version"] for r in by["xrnrule"]["released"]], ["0.2.16", "2.3.6"])
        self.assertEqual(by["xrntube"]["released"], [{"kind": "extension", "version": "2.1.10", "code": None}])
        self.assertEqual(by["xrnloops"]["released"], [])
        self.assertEqual(by["xrnmail"]["released"], [])

    def test_another_account_sees_the_catalogue_and_no_more(self):
        release_files(self.home, "xrnhome-sync", phone={"versionCode": 7, "versionName": "1.1.0"})
        doc, by = self.build(owner=False)
        self.assertFalse(doc["owner"])
        for row in doc["apps"]:
            for key in ("records", "unit", "week", "lastIn", "since", "backup"):
                self.assertNotIn(key, row, row["name"])
        self.assertTrue(by["xrnloops"]["up"])
        self.assertEqual(by["xrnhome"]["released"][0]["version"], "1.1.0")

    def test_before_the_monitor_has_looked_everything_reads_as_unknown(self):
        self.watcher.current, self.watcher.data = {}, None
        doc, by = self.build()
        self.assertIsNone(doc["counted"])
        self.assertTrue(all(row["up"] is None for row in doc["apps"]))
        self.assertIsNone(by["xrnloops"]["records"])
        self.assertIsNone(by["home-server"]["backup"])

    def test_an_answer_is_kept_for_a_little_while(self):
        first = self.page.answer(USER, True, self.watcher, self.log, self.src)
        self.watcher.data["apps"][0]["records"] = 900
        self.assertIs(self.page.answer(USER, True, self.watcher, self.log, self.src), first)
        self.assertIsNot(self.page.answer(USER, False, self.watcher, self.log, self.src), first)
        with mock.patch.object(apps, "CACHE_S", 0):
            fresh = self.page.answer(USER, True, self.watcher, self.log, self.src)
        self.assertEqual(next(a for a in fresh["apps"] if a["name"] == "xrnloops")["records"], 900)


def at(hour, minute=0, day=17):
    """Epoch ms on a day around NOW, 2026-11-17 12:00."""
    return support.ms(2026, 11, day, hour, minute)


class FitCard(unittest.TestCase):
    """xrnfit's card on the Server page's Overview, which lists services and so had no place for it."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="xrnhub-fit-card-")
        self.addCleanup(shutil.rmtree, self.root)
        self.home = os.path.join(self.root, "home")
        os.makedirs(self.home)
        self.log = HubLog(os.path.join(self.root, "data"))
        self.src = Sources(self.home, USER)
        days = [(datetime.fromtimestamp(NOW).date() - timedelta(days=i)).isoformat() for i in range(29, -1, -1)]
        self.data = {"at": NOW - 120, "apps": [{"name": "xrnfit", "records": 9, "unit": "readings"}],
                     "fed": {"days": days, "apps": {"xrnfit": [5] * 23 + [0, 1, 2, 3, 4, 5, 40]}}}

    def send(self, source, records, received_s=NOW - 600):
        health.write(self.log, USER, {"source": source, "upsert": records}, received_s * 1000)

    def card(self, now=NOW, data=True):
        return apps.fit_card(self.log, USER, self.src, self.data if data else None, datetime.fromtimestamp(now))

    def stock(self):
        self.send("xrnfit-watch", [
            {"id": "s1", "kind": "steps", "start": at(9), "end": at(10), "value": 4200},
            {"id": "s2", "kind": "steps", "start": at(10), "end": at(11), "value": 1300},
            {"id": "n1", "kind": "sleep", "start": at(23, day=16), "end": at(6, 30)},
            {"id": "r1", "kind": "resting_heart_rate", "start": at(8), "value": 61},
            {"id": "o1", "kind": "spo2", "samples": [[at(4), 95], [at(5), 97]]},
            {"id": "t1", "kind": "stress", "samples": [[at(10, 30), 31]]},
        ])
        self.send("xrnfit-scale", [{"id": "w1", "kind": "weight", "start": at(7), "value": 78.4}], NOW - 300)

    def test_it_says_when_xrnfit_last_sent_as_its_apps_row_does(self):
        self.stock()
        card = self.card()
        page = apps.AppsPage(self.home, clock=lambda: NOW)
        watcher = SimpleNamespace(current={"services": []}, data=self.data)
        row = next(a for a in page.build(USER, True, watcher, self.log, self.src)["apps"] if a["name"] == "xrnfit")
        self.assertEqual(card["lastIn"], NOW - 300)
        self.assertEqual(card["lastIn"], row["lastIn"])
        self.assertEqual(card["week"], 55)  # the last seven days: 1 + 2 + 3 + 4 + 5 + 40 and a 0
        self.assertEqual(card["week"], row["week"])
        self.assertFalse(card["late"])

    def test_it_has_todays_figures_as_the_today_page_works_them_out(self):
        self.stock()
        card = self.card()
        self.assertEqual(card["day"], "2026-11-17")
        self.assertEqual(card["steps"], 5500)
        self.assertEqual(card["sleep"]["minutes"], 450)
        self.assertEqual(card["sleep"]["day"], "2026-11-17")
        self.assertEqual(card["resting"], {"day": "2026-11-17", "bpm": 61})
        self.assertEqual((card["weight"]["kg"], card["weight"]["at"]), (78.4, at(7)))
        self.assertEqual(card["spo2"], {"at": at(5), "percent": 97})
        self.assertEqual(card["stress"], {"at": at(10, 30), "level": 31})
        today = health.days(self.log.db(USER), "2026-11-17", "2026-11-17")["2026-11-17"]
        self.assertEqual(card["steps"], today["steps"])

    def test_before_the_day_starts_it_is_still_yesterdays_page(self):
        # 01:30 belongs to the day before, as on Today: the habit day starts at 03:00.
        self.send("xrnfit-watch", [{"id": "s1", "kind": "steps", "start": at(9, day=16), "end": at(10, day=16), "value": 800}],
                  NOW - 12 * 3600)
        card = self.card(now=NOW - 10 * 3600 - 1800)
        self.assertEqual((card["day"], card["steps"]), ("2026-11-16", 800))

    def test_it_is_late_once_nothing_has_come_in_for_six_hours(self):
        self.send("xrnfit-watch", [{"id": "s1", "kind": "steps", "start": at(5), "end": at(6), "value": 10}], NOW - 6 * 3600)
        self.assertFalse(self.card()["late"])
        self.assertTrue(self.card(now=NOW + 1)["late"])

    def test_an_event_from_xrnfit_counts_as_hearing_from_it(self):
        self.send("xrnfit-watch", [{"id": "s1", "kind": "steps", "start": at(1), "end": at(2), "value": 10}], NOW - 8 * 3600)
        self.log.add(USER, [{"app": "xrnfit", "kind": "state.watch", "at": (NOW - 120) * 1000}])
        card = self.card()
        self.assertEqual(card["lastIn"], NOW - 120)
        self.assertFalse(card["late"])

    def test_with_nothing_ever_sent_it_says_so_and_is_not_late(self):
        self.data["fed"]["apps"] = {}
        card = self.card()
        self.assertIsNone(card["lastIn"])
        self.assertFalse(card["late"])
        self.assertEqual(card["week"], 0)  # counted, and none came
        for key in ("steps", "sleep", "resting", "weight", "spo2", "stress"):
            self.assertIsNone(card[key], key)

    def test_before_the_inventory_has_counted_the_week_is_unknown(self):
        self.stock()
        card = self.card(data=False)
        self.assertIsNone(card["week"])
        self.assertEqual(card["lastIn"], NOW - 300)


class Endpoint(unittest.TestCase):
    """/api/apps over HTTP, on a made-up phone the monitor has looked at once."""

    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.mkdtemp(prefix="xrnhub-apps-routes-")
        cls.phone = support.FakePhone(os.path.join(cls.folder, "phone"))
        for i, service in enumerate(machine.SERVICES):
            cls.phone.service(service.runit, 100 + i, since=time.time() - 3600)
        cls.phone.supervised(machine.SERVICES)
        make_loops(cls.phone.home, [habit("a", "Gym")], [("a", datetime.now().date().isoformat(), 2)])
        make_rule(cls.phone.home, {"blocks": {}, "settings": {"overridePhrase": "secret sentence"}})
        write_json(os.path.join(cls.phone.home, "xrnwal-sync", "data", "users", USER, "state.json"),
                   {"version": 1, "data": {"bills": [{"atMillis": int(time.time() * 1000)}], "pinHash": "secret-hash"}})
        release_files(cls.phone.home, "xrnrule-sync", phone={"versionCode": 16, "versionName": "0.2.16"}, extension="2.3.6")
        cls.users = os.path.join(cls.folder, "users")
        hashed = bcrypt.hashpw(PW.encode(), bcrypt.gensalt(4)).decode()
        with open(cls.users, "w") as f:
            f.write(f"{USER}:{hashed}\nguest:{hashed}\n")
        cls.hub = xrnhub.Hub(os.path.join(cls.folder, "data"), cls.users, cls.phone.home, WEB, machine=cls.phone.machine())
        login.FAIL_DELAY_S = 0
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), xrnhub.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        now = cls.hub.monitor.clock()
        cls.hub.monitor.minute_tick(now)
        cls.hub.monitor.sweep(now)

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.hub.monitor.store.db.close()
        shutil.rmtree(cls.folder)

    def setUp(self):
        xrnhub.Handler.hub = self.hub

    def call(self, path, user=USER):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {}
        if user:
            headers["Authorization"] = "Basic " + base64.b64encode(f"{user}:{PW}".encode()).decode()
        conn.request("GET", path, headers=headers)
        res = conn.getresponse()
        data = res.read()
        conn.close()
        return res.status, data

    def test_it_needs_a_login(self):
        self.assertEqual(self.call("/api/apps", user=None)[0], 401)

    def test_it_answers_with_or_without_the_prefix(self):
        for path in ("/api/apps", "/xrnhub/api/apps"):
            status, body = self.call(path)
            self.assertEqual(status, 200, path)
            self.assertEqual(len(json.loads(body)["apps"]), 15, path)

    def test_the_owner_sees_the_live_state(self):
        by = {a["name"]: a for a in json.loads(self.call("/api/apps")[1])["apps"]}
        self.assertTrue(by["xrnloops"]["up"])
        self.assertIsNotNone(by["xrnloops"]["since"])
        self.assertEqual((by["xrnloops"]["records"], by["xrnloops"]["unit"]), (1, "check-ins"))
        self.assertEqual(by["xrnloops"]["week"], 1)
        self.assertEqual(by["xrnwal"]["records"], 1)
        self.assertIsNotNone(by["xrnwal"]["lastIn"])
        self.assertEqual([r["version"] for r in by["xrnrule"]["released"]], ["0.2.16", "2.3.6"])
        self.assertIsNone(by["xrnhub"]["records"])  # its own records belong to the apps that send them

    def test_a_second_account_sees_no_live_state(self):
        status, body = self.call("/api/apps", user="guest")
        self.assertEqual(status, 200)
        row = next(a for a in json.loads(body)["apps"] if a["name"] == "xrnloops")
        self.assertTrue(row["up"])
        self.assertNotIn("records", row)

    def test_nothing_private_leaves_with_it(self):
        body = self.call("/api/apps")[1]
        for secret in (b"secret sentence", b"secret-hash", self.phone.home.encode()):
            self.assertNotIn(secret, body)

    def test_every_logo_is_served(self):
        for a in apps.APPS:
            self.assertEqual(self.call(f"/{a.logo}", user=None)[0], 200, a.logo)


if __name__ == "__main__":
    unittest.main()

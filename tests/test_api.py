"""The personal API, /api/v1: each source's records, and the read-only tokens. All data is made up."""
import base64
import contextlib
import http.client
import io
import json
import os
import shutil
import tempfile
import threading
import unittest
from datetime import datetime

import bcrypt

import support
from support import USER, habit, ics, make_calendar, make_home, make_insta, make_loops, make_rule, make_wallet, ms

import xrnhub
from hublib import api, daybook, health, login, tokens
from hublib.log import HubLog
from hublib.sources import Sources

NOW = datetime(2026, 3, 10, 14, 0)
PW = "correct horse"


def ask(src, log=None, now=NOW, owner=True, **query):
    return api.Ask(src, log, USER, now, lambda name, fallback: query.get(name, fallback), owner=owner)


def get(path, src, log=None, now=NOW, owner=True, **query):
    return api.answer(path, ask(src, log, now, owner, **query))


class Folder(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="xrnhub-api-")
        self.addCleanup(shutil.rmtree, self.home)

    def src(self):
        s = Sources(self.home, USER)
        s.calendar.sweep()
        return s


class Habits(Folder):
    def setUp(self):
        super().setUp()
        make_loops(self.home, [
            habit("gym", "Gym", question="Did you go?", position=0),
            habit("water", "Water", type=1, targetValue=8, unit="glasses", position=1),
            habit("read", "Read", freqNum=3, freqDen=7, position=2),
        ], [
            ("gym", "2026-03-08", 2), ("gym", "2026-03-09", 2), ("gym", "2026-03-10", 3),
            ("water", "2026-03-09", 9000), ("water", "2026-03-10", 5000),
            ("read", "2026-02-01", 2), ("read", "2026-03-10", 1),
        ])

    def test_each_habit_says_what_kind_it_is_and_how_today_stands(self):
        doc = get("/api/v1/habits", self.src())
        self.assertEqual(doc["today"], "2026-03-10")
        gym, water, read = doc["habits"]
        self.assertEqual((gym["kind"], gym["question"], gym["today"]), ("yesno", "Did you go?", {"done": False, "mark": "skip"}))
        self.assertEqual(gym["streak"], 2)  # a skip neither breaks the run nor adds to it
        self.assertEqual(water["kind"], "measurable")
        self.assertEqual(water["target"], {"value": 8, "unit": "glasses", "atMost": False})
        self.assertEqual(water["today"], {"done": False, "amount": 5.0})
        self.assertEqual(read["frequency"], {"times": 3, "days": 7})
        self.assertEqual(read["today"], {"done": True, "mark": "auto"})

    def test_entries_come_day_by_day_within_the_range(self):
        doc = get("/api/v1/habits/entries", self.src(), **{"from": "2026-03-09", "to": "2026-03-10"})
        self.assertEqual([(e["habit"], e["date"]) for e in doc["entries"]],
                         [("gym", "2026-03-09"), ("water", "2026-03-09"), ("gym", "2026-03-10"),
                          ("water", "2026-03-10"), ("read", "2026-03-10")])
        self.assertEqual(doc["entries"][1], {"habit": "water", "name": "Water", "date": "2026-03-09", "done": True, "amount": 9.0})

    def test_entries_default_to_the_last_30_habit_days(self):
        # 01:00 belongs to the day before when days start at 03:00.
        doc = get("/api/v1/habits/entries", self.src(), now=datetime(2026, 3, 11, 1, 0))
        self.assertEqual((doc["from"], doc["to"]), ("2026-02-09", "2026-03-10"))
        self.assertNotIn("2026-02-01", [e["date"] for e in doc["entries"]])

    def test_entries_for_one_habit_and_its_notes(self):
        import sqlite3
        db = sqlite3.connect(os.path.join(self.home, "xrnloops-sync", "data", "users", USER, "loop.sqlite"))
        db.execute("UPDATE entries SET notes = 'legs' WHERE habit = 'gym' AND date = '2026-03-09'")
        db.commit()
        db.close()
        doc = get("/api/v1/habits/entries", self.src(), habit="gym")
        self.assertEqual([e["date"] for e in doc["entries"]], ["2026-03-08", "2026-03-09", "2026-03-10"])
        self.assertEqual(doc["entries"][1]["note"], "legs")
        self.assertNotIn("note", doc["entries"][0])

    def test_a_habit_that_isnt_there_is_a_bad_request(self):
        with self.assertRaises(api.BadRequest):
            get("/api/v1/habits/entries", self.src(), habit="nope")

    def test_ranges_are_checked(self):
        for bad in ({"from": "2026-3-1"}, {"from": "2026-02-30"}, {"from": "2026-03-10", "to": "2026-03-01"}):
            with self.assertRaises(api.BadRequest, msg=bad):
                get("/api/v1/habits/entries", self.src(), **bad)


class Calendar(Folder):
    def test_events_carry_where_and_what_for_with_their_offset(self):
        make_calendar(self.home, "work", [
            ics("UID:a", "SUMMARY:Standup", "LOCATION:Room 4\\, level 2", "DESCRIPTION:Bring notes\\nand coffee",
                "DTSTART:20260310T090000", "DTEND:20260310T091500"),
            ics("UID:b", "SUMMARY:Holiday", "DTSTART;VALUE=DATE:20260312", "DTEND;VALUE=DATE:20260314"),
        ], display="Work")
        doc = get("/api/v1/calendar", self.src())
        self.assertEqual((doc["from"], doc["to"], doc["ready"]), ("2026-03-10", "2026-04-09", True))
        self.assertEqual(doc["calendars"], [{"name": "Work", "color": "#1c6fd8"}])
        standup, holiday = doc["events"]
        self.assertEqual(standup["start"], datetime(2026, 3, 10, 9, 0).astimezone().isoformat())
        self.assertEqual((standup["location"], standup["description"]), ("Room 4, level 2", "Bring notes\nand coffee"))
        self.assertEqual((holiday["allDay"], holiday["start"], holiday["end"]), (True, "2026-03-12", "2026-03-14"))
        self.assertEqual(holiday["location"], "")

    def test_a_range_is_capped(self):
        with self.assertRaises(api.BadRequest):
            get("/api/v1/calendar", self.src(), **{"from": "2026-01-01", "to": "2027-03-01"})


class Money(Folder):
    def wallet(self, **over):
        doc = {
            "settings": json.dumps({"baseCurrency": "AUD", "startDay": 15, "pinHash": "never-shown", "lockSalt": "salt"}),
            "ledgers": [{"id": 1, "name": "Daily"}],
            "accounts": [
                {"id": 1, "name": "Everyday", "kind": 0, "currency": "AUD", "openingCents": 10000, "includeInTotal": True},
                {"id": 2, "name": "Card", "kind": 1, "currency": "AUD", "openingCents": 0, "includeInTotal": True},
                {"id": 3, "name": "Dong", "kind": 0, "currency": "VND", "openingCents": 0, "includeInTotal": True},
                {"id": 4, "name": "Old", "kind": 0, "currency": "AUD", "openingCents": 500, "archived": True},
            ],
            "categories": [
                {"id": 10, "type": 0, "name": "Food", "parentId": None},
                {"id": 11, "type": 0, "name": "Coffee", "parentId": 10},
                {"id": 20, "type": 1, "name": "Salary", "parentId": None},
            ],
            "tags": [{"id": 1, "name": "work"}],
            "billTags": [{"billId": 2, "tagId": 1}],
            "bills": [
                {"id": 1, "ledgerId": 1, "type": 1, "categoryId": 20, "accountId": 1, "amountCents": 50000,
                 "currency": "AUD", "rate": 1.0, "atMillis": ms(2026, 3, 1, 9)},
                {"id": 2, "ledgerId": 1, "type": 0, "categoryId": 11, "accountId": 2, "amountCents": 550,
                 "currency": "AUD", "rate": 1.0, "note": "flat white", "atMillis": ms(2026, 3, 9, 8)},
                {"id": 3, "ledgerId": 1, "type": 2, "accountId": 1, "toAccountId": 3, "amountCents": 10000,
                 "feeCents": 300, "toAmountCents": 170000000, "currency": "AUD", "rate": 1.0, "atMillis": ms(2026, 3, 9, 10)},
                {"id": 4, "ledgerId": 1, "type": 0, "categoryId": 10, "accountId": 1, "amountCents": 2000,
                 "currency": "AUD", "rate": 1.0, "atMillis": ms(2026, 1, 5, 12), "recurringId": 7, "photo": "/x/y.jpg"},
            ],
            "budgets": [{"id": 1, "categoryId": None, "amountCents": 100000}, {"id": 2, "categoryId": 11, "amountCents": 3000}],
            "recurring": [{"id": 7, "type": 0, "categoryId": 10, "accountId": 1, "amountCents": 2000, "currency": "AUD",
                           "startEpochDay": 20458, "repeatKind": "MONTHLY", "repeatValue": 5, "every": 1, "times": 0, "generated": 3}],
            "plans": [{"id": 1, "name": "Trip", "targetCents": 200000, "startCents": 0, "currency": "AUD", "accountId": 1}],
            "deposits": [{"id": 1, "planId": 1, "amountCents": 2500, "atMillis": ms(2026, 3, 2)}],
            "rates": [{"code": "VND", "rate": 0.00006}],
        }
        doc.update(over)
        make_wallet(self.home, doc)

    def test_balances_net_worth_and_names(self):
        self.wallet()
        doc = get("/api/v1/money", self.src())
        by = {a["name"]: a for a in doc["accounts"]}
        # 100.00 opening + 500.00 salary - 20.00 rent - (100.00 + 3.00 fee) sent + 25.00 saved for the trip
        self.assertEqual(by["Everyday"]["balanceCents"], 10000 + 50000 - 2000 - 10300 + 2500)
        self.assertEqual(by["Card"]["balanceCents"], -550)
        # What landed in VND was fixed when it was saved, so a new rate doesn't move it.
        self.assertEqual(by["Dong"]["balanceCents"], 170000000)
        self.assertEqual(by["Dong"]["balanceBaseCents"], 10200)
        self.assertEqual(doc["netWorth"], {"assetsCents": 50200 + 10200, "debtsCents": -550, "netCents": 50200 + 10200 - 550})
        self.assertEqual((doc["currency"], doc["monthStartsOn"]), ("AUD", 15))
        self.assertEqual([(b["category"], b["amountCents"]) for b in doc["budgets"]], [(None, 100000), ("Coffee", 3000)])
        coffee = next(c for c in doc["categories"] if c["name"] == "Coffee")
        self.assertEqual((coffee["parent"], coffee["type"]), ("Food", "expense"))
        self.assertEqual(doc["recurring"][0]["starts"], "2026-01-05")
        self.assertEqual(doc["savings"][0]["savedCents"], 2500)

    def test_the_wallets_lock_never_leaves(self):
        self.wallet()
        body = json.dumps([get("/api/v1/money", self.src()), get("/api/v1/money/transactions", self.src())])
        for secret in ("never-shown", "salt", "pinHash", "/x/y.jpg"):
            self.assertNotIn(secret, body)

    def test_transactions_in_a_range_with_totals(self):
        self.wallet()
        doc = get("/api/v1/money/transactions", self.src())
        self.assertEqual((doc["from"], doc["to"]), ("2026-02-09", "2026-03-10"))
        self.assertEqual([t["id"] for t in doc["transactions"]], [1, 2, 3])
        self.assertEqual(doc["totals"], {"expenseCents": 550, "incomeCents": 50000})
        coffee = doc["transactions"][1]
        self.assertEqual((coffee["category"], coffee["parentCategory"], coffee["account"], coffee["tags"], coffee["note"]),
                         ("Coffee", "Food", "Card", ["work"], "flat white"))
        sent = doc["transactions"][2]
        self.assertEqual((sent["type"], sent["toAccount"], sent["feeCents"], sent["toAmountCents"]),
                         ("transfer", "Dong", 300, 170000000))
        older = get("/api/v1/money/transactions", self.src(), **{"from": "2026-01-01", "to": "2026-01-31"})
        self.assertEqual([(t["id"], t["repeating"], t["hasPhoto"]) for t in older["transactions"]], [(4, True, True)])

    def test_a_half_cent_rounds_up_as_the_app_does(self):
        # 5 cents at 0.5 is 2.5: the app's roundToLong says 3, Python's round() said 2.
        self.wallet(bills=[{"id": 1, "ledgerId": 1, "type": 0, "accountId": 1, "amountCents": 5, "currency": "USD",
                            "rate": 0.5, "atMillis": ms(2026, 3, 9)}])
        doc = get("/api/v1/money/transactions", self.src())
        self.assertEqual(doc["transactions"][0]["baseCents"], 3)
        self.assertEqual(daybook.bill_base_cents({"amountCents": 5, "rate": 0.5}), 3)

    def test_a_bad_rate_counts_as_one(self):
        self.wallet(rates=[{"code": "VND", "rate": 0}], bills=[])
        by = {a["name"]: a for a in get("/api/v1/money", self.src())["accounts"]}
        self.assertEqual(by["Dong"]["balanceBaseCents"], 0)

    def test_no_wallet_is_empty_not_an_error(self):
        doc = get("/api/v1/money", self.src())
        self.assertEqual((doc["accounts"], doc["netWorth"]["netCents"]), ([], 0))
        self.assertEqual(get("/api/v1/money/transactions", self.src())["transactions"], [])


class Inventory(Folder):
    def test_items_and_the_shopping_list(self):
        make_home(self.home, [
            {"id": "m", "name": "milk", "room": "food", "category": "Fridge", "quantity": 0, "unit": "bottle",
             "wanted": True, "expiryDate": None, "photo": "/xrnhome/photos/abc", "createdAt": "2026-01-01T00:00:00.000Z",
             "history": [{"date": "2026-03-09T08:00:00.000Z", "action": "Used 1 bottle", "quantityBefore": 1, "quantityAfter": 0}]},
            {"id": "s", "name": "Soap", "room": "household", "category": "Bathroom", "quantity": 3, "wanted": True, "snoozed": True},
        ])
        items = get("/api/v1/inventory", self.src())["items"]
        self.assertEqual([i["name"] for i in items], ["milk", "Soap"])
        milk, soap = items
        self.assertEqual((milk["onShoppingList"], soap["onShoppingList"]), (True, False))
        self.assertEqual(milk["history"][0]["action"], "Used 1 bottle")
        self.assertTrue(milk["hasPhoto"])
        self.assertNotIn("/xrnhome/photos/abc", json.dumps(items))


class Songs(Folder):
    def setUp(self):
        super().setUp()
        make_insta(self.home, [
            ("Second Day", "Ca Sĩ Mẫu", 2, "VPOP", ms(2026, 3, 9, 20)),
            ("Before It Began", "Alex Example", -3, "INST", None),
            ("First Day", "Alex Example, Ca Sĩ Mẫu", 1, "VPOP", None),
        ], [("Later Maybe", "", "find the key")])

    def test_the_list_by_day_with_whats_on_hold(self):
        doc = get("/api/v1/songs", self.src())
        self.assertEqual([(s["day"], s["title"]) for s in doc["songs"]],
                         [(-3, "Before It Began"), (1, "First Day"), (2, "Second Day")])
        self.assertEqual(doc["songs"][2]["added"], datetime(2026, 3, 9, 20).astimezone().isoformat())
        self.assertIsNone(doc["songs"][0]["added"])  # imported from the sheet, which kept no times
        self.assertEqual((doc["latestDay"], doc["genres"]), (2, {"VPOP": 2, "INST": 1}))
        self.assertEqual(doc["onHold"], [{"id": 1, "title": "Later Maybe", "artists": "", "note": "find the key", "added": None}])
        self.assertEqual(get("/api/v1", self.src())["sources"]["songs"]["songs"], 3)

    def test_its_the_owners_alone(self):
        doc = get("/api/v1/songs", self.src(), owner=False)
        self.assertEqual((doc["songs"], doc["onHold"], doc["latestDay"]), ([], [], None))
        self.assertEqual(get("/api/v1", self.src(), owner=False)["sources"]["songs"], {"from": "xrninsta", "songs": 0, "updated": None})

    def test_no_bot_is_an_empty_list(self):
        os.remove(os.path.join(self.home, "xrninsta", "data", "xrninsta.sqlite"))
        self.assertEqual(get("/api/v1/songs", self.src())["songs"], [])


class Blocks(Folder):
    def setUp(self):
        super().setUp()
        make_loops(self.home, [habit("gym", "Gym")], [("gym", "2026-03-10", 2)])
        day_start = ms(2026, 3, 10)
        make_rule(self.home, {
            "blocks": {
                "b1": {"id": "b1", "name": "Social", "mode": "ALLOWANCE", "dailyAllowanceMs": 1800000,
                       "targetSites": ["example.com"], "targetPackages": ["com.example.feed"], "schedule": None},
                "b2": {"id": "b2", "name": "Games", "mode": "HABIT", "targetDesktopApps": ["game"]},
            },
            "items": {"i1": {"id": "i1", "blockId": "b2", "text": "Go to the gym", "habitId": "gym", "order": 0}},
            "allowance": {"b1": {"phone-1": {"ms": 600000, "dayStartWall": day_start}}},
            "settings": {"overridePhrase": "I really do need more minutes"},
        }, events=[
            {"t": "blocked", "at": ms(2026, 3, 9, 21), "d": "phone-1", "name": "Feed", "target": "com.example.feed"},
            {"t": "unlock", "at": ms(2026, 3, 10, 7), "d": "laptop-1", "block": "b2", "ms": 3600000, "start": ms(2026, 3, 10, 7)},
            {"t": "break", "at": ms(2026, 2, 1), "d": "phone-1", "ms": 60000},
        ])

    def test_each_block_with_its_state_use_and_needs(self):
        doc = get("/api/v1/blocks", self.src())
        games, social = doc["blocks"]
        self.assertEqual((social["state"], social["usedMs"], social["limitMs"]), ("open", 600000, 1800000))
        self.assertEqual((social["sites"], social["apps"]), (["example.com"], ["com.example.feed"]))
        self.assertEqual(games["needs"], [{"text": "Go to the gym", "habit": "gym", "done": True, "habitName": "Gym"}])
        self.assertEqual(games["desktopApps"], ["game"])

    def test_the_phrase_that_buys_minutes_never_leaves(self):
        body = json.dumps([get("/api/v1/blocks", self.src()), get("/api/v1/blocks/events", self.src()),
                           get("/api/v1", self.src())])
        self.assertNotIn("I really do need", body)
        self.assertNotIn("overridePhrase", body)

    def test_events_in_a_range_with_block_names(self):
        doc = get("/api/v1/blocks/events", self.src())
        self.assertEqual([e["type"] for e in doc["events"]], ["blocked", "unlock"])
        unlock = doc["events"][1]
        self.assertEqual((unlock["block"], unlock["device"], unlock["ms"]), ("Games", "laptop-1", 3600000))
        self.assertEqual(unlock["start"], datetime(2026, 3, 10, 7).astimezone().isoformat())


class Index(Folder):
    def test_it_lists_every_endpoint_and_what_each_source_holds(self):
        make_loops(self.home, [habit("gym", "Gym")], [])
        doc = get("/api/v1", self.src())
        self.assertEqual(doc["version"], 1)
        self.assertEqual(doc["sources"]["habits"]["habits"], 1)
        self.assertIsNotNone(doc["sources"]["habits"]["updated"])
        self.assertIsNone(doc["sources"]["money"]["updated"])  # no wallet here
        paths = [e["path"] for e in doc["endpoints"]]
        self.assertEqual(paths, list(api.ROUTES))
        self.assertNotIn("xrntube", json.dumps(doc))
        self.assertEqual(doc["sources"]["health"], {"from": "xrnfit", "records": 0, "updated": None})

    def test_an_unknown_path_has_no_answer(self):
        self.assertIsNone(get("/api/v1/tube", self.src()))
        self.assertIsNotNone(get("/api/v1/", self.src()))


class HubEvents(Folder):
    def test_events_in_a_range_with_their_detail(self):
        data = os.path.join(self.home, "hubdata")
        log = HubLog(data)
        log.add(USER, [
            {"app": "xrnmail", "kind": "state.held", "at": ms(2026, 3, 9, 12), "value": 4, "detail": {"held": 4}},
            {"app": "xrnsheet", "kind": "ping", "at": ms(2026, 3, 9, 13)},
            {"app": "xrnmail", "kind": "batch", "at": ms(2026, 2, 1), "value": 1},
        ])
        doc = get("/api/v1/events", self.src(), log, app="xrnmail")
        self.assertEqual(len(doc["events"]), 1)
        self.assertEqual(doc["events"][0]["detail"], {"held": 4})
        self.assertEqual(doc["events"][0]["at"], datetime(2026, 3, 9, 12).astimezone().isoformat())
        self.assertEqual(len(get("/api/v1/events", self.src(), log)["events"]), 2)


class Health(Folder):
    def setUp(self):
        super().setUp()
        self.log = HubLog(os.path.join(self.home, "hubdata"))
        t = ms(2026, 3, 9, 23)
        put = lambda source, *records: health.write(self.log, USER, {"source": source, "upsert": list(records)})
        put("gadgetbridge",
            {"id": "s1", "kind": "steps", "start": ms(2026, 3, 10, 9), "end": ms(2026, 3, 10, 10), "value": 4321},
            {"id": "s0", "kind": "steps", "start": ms(2026, 3, 8, 9), "end": ms(2026, 3, 8, 10), "value": 9000},
            {"id": "h1", "kind": "heart_rate", "samples": [[ms(2026, 3, 10, 9), 64], [ms(2026, 3, 10, 13), 80]]},
            {"id": "h0", "kind": "heart_rate", "samples": [[ms(2026, 3, 2, 9), 70]]},
            {"id": "r1", "kind": "resting_heart_rate", "start": ms(2026, 3, 10, 8), "value": 56},
            {"id": "z1", "kind": "sleep", "start": t, "end": t + 7 * 3_600_000,
             "stages": [[t, t + 3 * 3_600_000, "light"], [t + 3 * 3_600_000, t + 7 * 3_600_000, "deep"]]})
        put("import-mifitness",
            {"id": "s9", "kind": "steps", "start": ms(2026, 3, 10, 0), "end": ms(2026, 3, 10, 23), "value": 99})
        put("import-renpho", {"id": "w0", "kind": "weight", "start": ms(2025, 3, 1, 7), "value": 75})
        put("xrnfit-scale", {"id": "w1", "kind": "weight", "start": ms(2026, 3, 10, 7), "value": 72.35, "impedance": 512})

    def test_now_has_today_last_night_heart_rate_and_the_latest_weight(self):
        doc = get("/api/v1/health", self.src(), self.log)
        self.assertEqual((doc["today"], doc["steps"]), ("2026-03-10", 4321))
        self.assertEqual((doc["sleep"]["day"], doc["sleep"]["minutes"], doc["sleep"]["source"]), ("2026-03-10", 420, "gadgetbridge"))
        self.assertEqual(doc["sleep"]["stages"][1], {"start": datetime(2026, 3, 10, 2).astimezone().isoformat(),
                                                     "end": datetime(2026, 3, 10, 6).astimezone().isoformat(), "stage": "deep"})
        self.assertEqual(doc["heartRate"], {"latest": {"at": datetime(2026, 3, 10, 13).astimezone().isoformat(), "bpm": 80},
                                            "resting": 56})
        self.assertEqual(doc["weight"], {"at": datetime(2026, 3, 10, 7).astimezone().isoformat(), "kg": 72.35, "impedance": 512})
        self.assertEqual(doc["held"], {"steps": 3, "heartRate": 3, "sleep": 1, "weight": 2, "spo2": 0, "stress": 0,
                                       "distance": 0, "activeEnergy": 0, "first": "2025-03-01", "last": "2026-03-10"})
        self.assertEqual((doc["spo2"], doc["stress"]), (None, None))

    def test_last_night_is_yesterdays_before_tonights_lands(self):
        doc = get("/api/v1/health", self.src(), self.log, now=datetime(2026, 3, 11, 9))
        self.assertEqual((doc["sleep"]["day"], doc["steps"]), ("2026-03-10", None))

    def test_nothing_kept_reads_as_empty(self):
        doc = get("/api/v1/health", self.src(), HubLog(os.path.join(self.home, "empty")))
        self.assertEqual({k: doc[k] for k in ("steps", "sleep", "heartRate", "weight")},
                         {"steps": None, "sleep": None, "heartRate": None, "weight": None})

    def test_days_list_every_day_in_the_range(self):
        doc = get("/api/v1/health/days", self.src(), self.log, **{"from": "2026-03-08", "to": "2026-03-10"})
        self.assertEqual([d["date"] for d in doc["days"]], ["2026-03-08", "2026-03-09", "2026-03-10"])
        empty, today = doc["days"][1], doc["days"][2]
        self.assertEqual(empty, {"date": "2026-03-09", "steps": None, "sleep": None, "heartRate": None, "weightKg": None,
                                 "spo2": None, "stress": None, "distance": None, "activeEnergy": None})
        self.assertEqual(today["steps"], 4321)  # the watch's day, not the export's too
        self.assertEqual(today["heartRate"], {"min": 64, "avg": 72, "max": 80, "resting": 56})
        self.assertEqual({k: today["sleep"][k] for k in ("minutes", "light", "deep", "awake")},
                         {"minutes": 420, "light": 180, "deep": 240, "awake": 0})
        self.assertEqual(today["sleep"]["start"], datetime(2026, 3, 9, 23).astimezone().isoformat())
        self.assertEqual(today["weightKg"], 72.35)

    def test_spo2_and_stress_come_by_day_and_as_the_latest_reading(self):
        health.write(self.log, USER, {"source": "xrnfit-watch", "upsert": [
            {"id": "o1", "kind": "spo2", "samples": [[ms(2026, 3, 10, 2), 95], [ms(2026, 3, 10, 4), 97]]},
            {"id": "t1", "kind": "stress", "samples": [[ms(2026, 3, 10, 9), 22], [ms(2026, 3, 10, 12), 48]]}]})
        day = get("/api/v1/health/days", self.src(), self.log, **{"from": "2026-03-10", "to": "2026-03-10"})["days"][0]
        self.assertEqual((day["spo2"], day["stress"]), ({"min": 95, "avg": 96, "max": 97}, {"min": 22, "avg": 35, "max": 48}))
        doc = get("/api/v1/health", self.src(), self.log)
        self.assertEqual(doc["spo2"], {"at": datetime(2026, 3, 10, 4).astimezone().isoformat(), "percent": 97})
        self.assertEqual(doc["stress"], {"at": datetime(2026, 3, 10, 12).astimezone().isoformat(), "level": 48})
        self.assertEqual((doc["held"]["spo2"], doc["held"]["stress"]), (2, 2))

    def test_distance_and_active_energy_come_by_day_and_as_todays_totals(self):
        health.write(self.log, USER, {"source": "xrnfit-watch", "upsert": [
            {"id": "d1", "kind": "distance", "start": ms(2026, 3, 10, 9), "end": ms(2026, 3, 10, 10), "value": 3200},
            {"id": "d2", "kind": "distance", "start": ms(2026, 3, 9, 9), "end": ms(2026, 3, 9, 10), "value": 1500},
            {"id": "e1", "kind": "active_energy", "start": ms(2026, 3, 10, 9), "end": ms(2026, 3, 10, 10), "value": 210.4}]})
        found = get("/api/v1/health/days", self.src(), self.log, **{"from": "2026-03-09", "to": "2026-03-10"})["days"]
        self.assertEqual([(d["distance"], d["activeEnergy"]) for d in found], [(1500, None), (3200, 210)])
        doc = get("/api/v1/health", self.src(), self.log)
        self.assertEqual((doc["distance"], doc["activeEnergy"]), (3200, 210))
        self.assertEqual((doc["held"]["distance"], doc["held"]["activeEnergy"]), (2, 1))

    def test_days_default_to_the_last_30_and_stop_at_400(self):
        doc = get("/api/v1/health/days", self.src(), self.log)
        self.assertEqual((doc["from"], doc["to"], len(doc["days"])), ("2026-02-09", "2026-03-10", 30))
        with self.assertRaises(api.BadRequest):
            get("/api/v1/health/days", self.src(), self.log, **{"from": "2025-01-01", "to": "2026-03-10"})

    def test_heart_rate_samples_default_to_today_and_stop_at_a_week(self):
        doc = get("/api/v1/health/heart-rate", self.src(), self.log)
        self.assertEqual([s["bpm"] for s in doc["samples"]], [64, 80])
        doc = get("/api/v1/health/heart-rate", self.src(), self.log, **{"from": "2026-03-04"})
        self.assertEqual(len(doc["samples"]), 2)
        with self.assertRaises(api.BadRequest) as caught:
            get("/api/v1/health/heart-rate", self.src(), self.log, **{"from": "2026-03-03"})
        self.assertEqual(str(caught.exception), "at most 7 days at once")

    def test_steps_come_in_buckets_of_every_minutes(self):
        doc = get("/api/v1/health/steps", self.src(), self.log, every="30")
        self.assertEqual(doc, {"from": "2026-03-10", "to": "2026-03-10", "every": 30,
                               "buckets": [[ms(2026, 3, 10, 9), 2160], [ms(2026, 3, 10, 9, 30), 2161]]})
        self.assertEqual(get("/api/v1/health/steps", self.src(), self.log)["every"], 60)
        # 56 days by the half hour, xrnfit's "usual by this time", is allowed.
        get("/api/v1/health/steps", self.src(), self.log, every="30", **{"from": "2026-01-14"})
        for every in ("0", "1441", "abc", "-5", "1.5"):
            with self.subTest(every=every), self.assertRaises(api.BadRequest):
                get("/api/v1/health/steps", self.src(), self.log, every=every)
        with self.assertRaises(api.BadRequest) as caught:  # 8 days by the minute
            get("/api/v1/health/steps", self.src(), self.log, every="1", **{"from": "2026-03-03"})
        self.assertIn("at most 10000 buckets", str(caught.exception))

    def test_spo2_and_stress_samples_default_to_today_and_stop_at_a_week(self):
        health.write(self.log, USER, {"source": "xrnfit-watch", "upsert": [
            {"id": "o1", "kind": "spo2", "samples": [[ms(2026, 3, 10, 2), 95], [ms(2026, 3, 9, 4), 97]]},
            {"id": "t1", "kind": "stress", "samples": [[ms(2026, 3, 10, 9), 22]]}]})
        doc = get("/api/v1/health/spo2", self.src(), self.log)
        self.assertEqual(doc, {"from": "2026-03-10", "to": "2026-03-10",
                               "samples": [{"at": datetime(2026, 3, 10, 2).astimezone().isoformat(), "percent": 95}]})
        doc = get("/api/v1/health/spo2", self.src(), self.log, **{"from": "2026-03-09"})
        self.assertEqual([s["percent"] for s in doc["samples"]], [97, 95])
        doc = get("/api/v1/health/stress", self.src(), self.log)
        self.assertEqual(doc["samples"], [{"at": datetime(2026, 3, 10, 9).astimezone().isoformat(), "level": 22}])
        for path in ("/api/v1/health/spo2", "/api/v1/health/stress"):
            with self.subTest(path=path), self.assertRaises(api.BadRequest):
                get(path, self.src(), self.log, **{"from": "2026-03-03"})

    def test_sleep_sessions_with_their_stages(self):
        doc = get("/api/v1/health/sleep", self.src(), self.log)
        self.assertEqual(len(doc["sessions"]), 1)
        self.assertEqual([s["stage"] for s in doc["sessions"][0]["stages"]], ["light", "deep"])

    def test_weighings_from_every_source_for_a_year(self):
        doc = get("/api/v1/health/weight", self.src(), self.log)
        self.assertEqual(doc["from"], "2025-03-11")
        self.assertEqual(doc["weighings"], [{"at": datetime(2026, 3, 10, 7).astimezone().isoformat(), "kg": 72.35,
                                             "impedance": 512, "source": "xrnfit-scale"}])
        doc = get("/api/v1/health/weight", self.src(), self.log, **{"from": "2000-01-01"})
        self.assertEqual([w["source"] for w in doc["weighings"]], ["import-renpho", "xrnfit-scale"])
        self.assertNotIn("impedance", doc["weighings"][0])
        self.assertNotIn("body", doc["weighings"][1])

    def test_a_weighing_brings_the_scale_apps_own_figures(self):
        body = {"fat": 21.4, "bmr": 1712}
        rec = {"id": "rb", "kind": "weight", "start": ms(2026, 3, 9, 7), "value": 79.15, "body": body}
        health.write(self.log, USER, {"source": "import-renpho", "upsert": [rec]}, now_ms=ms(2026, 3, 10, 14))
        doc = get("/api/v1/health/weight", self.src(), self.log, **{"from": "2026-03-09", "to": "2026-03-09"})
        self.assertEqual(doc["weighings"][0]["body"], body)

    def test_the_index_counts_what_is_kept(self):
        doc = get("/api/v1", self.src(), self.log)
        self.assertEqual(doc["sources"]["health"]["records"], 10)  # a heart-rate sample is a row
        self.assertIsNotNone(doc["sources"]["health"]["updated"])


class Tokens(unittest.TestCase):
    def setUp(self):
        self.data = tempfile.mkdtemp(prefix="xrnhub-tokens-")
        self.addCleanup(shutil.rmtree, self.data)
        self.names = [USER]
        self.store = tokens.Tokens(self.data, lambda: self.names)

    def test_a_token_reads_as_its_user_and_only_its_hash_is_kept(self):
        token = self.store.add(USER, "scripts")
        self.assertRegex(token, tokens.TOKEN)
        self.assertEqual(self.store.user_for(f"Bearer {token}"), USER)
        with open(self.store.path(USER)) as f:
            kept = f.read()
        self.assertNotIn(token.split("_")[2], kept)
        self.assertEqual(os.stat(self.store.path(USER)).st_mode & 0o777, 0o600)

    def test_a_wrong_revoked_or_orphaned_token_reads_as_no_one(self):
        token = self.store.add(USER, "scripts")
        token_id = token.split("_")[1]
        self.assertIsNone(self.store.user_for(f"Bearer {token[:-1]}{'A' if token[-1] != 'A' else 'B'}"))
        self.assertIsNone(self.store.user_for(f"Basic {token}"))
        self.assertIsNone(self.store.user_for("Bearer xrnhub_zz_short"))
        self.names = []  # taken out of the users file
        self.assertIsNone(self.store.user_for(f"Bearer {token}"))
        self.names = [USER]
        self.assertTrue(self.store.revoke(USER, token_id))
        self.assertIsNone(self.store.user_for(f"Bearer {token}"))
        self.assertFalse(self.store.revoke(USER, token_id))

    def test_names_are_checked(self):
        for bad in ("", "x" * 61, "line\nbreak"):
            with self.assertRaises(ValueError):
                self.store.add(USER, bad)

    def test_the_command_line_adds_lists_and_revokes(self):
        users = os.path.join(self.data, "users.htpasswd")
        with open(users, "w") as f:
            f.write(f"{USER}:x\nsomeone:y\n")
        args = ["--data", self.data, "--users", users]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            tokens.cli(["add", "laptop", *args])
        token = out.getvalue().strip()
        self.assertEqual(self.store.user_for(f"Bearer {token}"), USER)  # the first user, by default
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            tokens.cli(["list", *args])
        self.assertIn("laptop", out.getvalue())
        self.assertNotIn(token, out.getvalue())
        with contextlib.redirect_stderr(io.StringIO()):
            tokens.cli(["revoke", token.split("_")[1], *args])
        self.assertIsNone(self.store.user_for(f"Bearer {token}"))
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            tokens.cli(["add", "x", "--user", "nobody", *args])


class OverHttp(unittest.TestCase):
    """Who may read /api/v1, through the real server."""

    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.mkdtemp(prefix="xrnhub-api-http-")
        cls.home = os.path.join(cls.folder, "home")
        os.makedirs(cls.home)
        cls.users = os.path.join(cls.folder, "users")
        with open(cls.users, "w") as f:
            f.write(f"{USER}:{bcrypt.hashpw(PW.encode(), bcrypt.gensalt(4)).decode()}\n")
        make_loops(cls.home, [habit("gym", "Gym")], [])
        cls.hub = xrnhub.Hub(os.path.join(cls.folder, "data"), cls.users, cls.home, os.path.join(support.ROOT, "web"))
        xrnhub.Handler.hub = cls.hub
        login.FAIL_DELAY_S = 0
        cls.srv = xrnhub.HubServer(("127.0.0.1", 0), xrnhub.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.token = cls.hub.tokens.add(USER, "tests")

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        shutil.rmtree(cls.folder)

    def setUp(self):
        self.hub.login.failures.clear()

    def call(self, method, path, auth=None, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Authorization": auth} if auth else {}
        data = json.dumps(body).encode() if body is not None else None
        if data:
            headers["Content-Type"] = "application/json"
        c.request(method, path, body=data, headers=headers)
        r = c.getresponse()
        out = r.read()
        c.close()
        return r.status, out

    def basic(self):
        return "Basic " + base64.b64encode(f"{USER}:{PW}".encode()).decode()

    def test_a_token_reads_the_api_under_either_path(self):
        for path in ("/api/v1", "/xrnhub/api/v1/habits", "/api/v1/habits/entries?from=2026-01-01&to=2026-01-02"):
            status, out = self.call("GET", path, f"Bearer {self.token}")
            self.assertEqual(status, 200, path)
            self.assertIsInstance(json.loads(out), dict)

    def test_the_owner_is_the_first_user(self):
        make_insta(self.home, [("Only Song", "Someone", 1, "MISC", None)])
        status, out = self.call("GET", "/api/v1/songs", f"Bearer {self.token}")
        self.assertEqual((status, [s["title"] for s in json.loads(out)["songs"]]), (200, ["Only Song"]))

    def test_the_password_reads_it_too(self):
        self.assertEqual(self.call("GET", "/api/v1/habits", self.basic())[0], 200)

    def test_a_token_reads_nothing_else_and_writes_nothing(self):
        bearer = f"Bearer {self.token}"
        for path in ("/api/today", "/api/review", "/api/rules", "/api/server/now"):
            self.assertEqual(self.call("GET", path, bearer)[0], 401, path)
        self.assertEqual(self.call("POST", "/api/events", bearer, {"events": [{"app": "x", "kind": "y", "at": 1}]})[0], 401)
        heavier = {"source": "gadgetbridge", "upsert": [{"id": "w", "kind": "weight", "start": ms(2026, 3, 10), "value": 200}]}
        self.assertEqual(self.call("POST", "/api/health", bearer, heavier)[0], 401)
        self.assertEqual(self.call("GET", "/api/v1/health", bearer)[0], 200)
        self.assertEqual(self.call("POST", "/api/session", bearer)[0], 401)

    def test_no_login_or_a_wrong_token_is_refused_and_counted(self):
        self.assertEqual(self.call("GET", "/api/v1")[0], 401)
        wrong = "Bearer xrnhub_00000000_" + "A" * 43
        for _ in range(10):
            self.assertEqual(self.call("GET", "/api/v1", wrong)[0], 401)
        self.assertEqual(self.call("GET", "/api/v1", wrong)[0], 429)

    def test_a_token_in_use_gives_no_guesses_back(self):
        # Every right login cleared its address's whole count, a token's too: a token holder (or
        # anyone behind the same address as a script using one) could guess the password nine
        # times, use the token once, and go on for ever. docs/api.md: a token risks what it can
        # see, never what it can change.
        wrong = "Basic " + base64.b64encode(f"{USER}:nope".encode()).decode()
        for _ in range(login.FAIL_LIMIT - 1):
            self.assertEqual(self.call("GET", "/api/v1", wrong)[0], 401)
            self.assertEqual(self.call("GET", "/api/v1", f"Bearer {self.token}")[0], 200)
        self.assertEqual(self.call("GET", "/api/v1", wrong)[0], 401)
        self.assertEqual(self.call("GET", "/api/v1", wrong)[0], 429)

    def test_bad_requests_and_unknown_paths(self):
        bearer = f"Bearer {self.token}"
        status, out = self.call("GET", "/api/v1/calendar?from=yesterday", bearer)
        self.assertEqual((status, out), (400, b"from must be a day, as 2026-11-15"))
        self.assertEqual(self.call("GET", "/api/v1/tube", bearer)[0], 404)


if __name__ == "__main__":
    unittest.main()

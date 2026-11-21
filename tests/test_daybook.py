import json
import os
import shutil
import sqlite3
import tempfile
import time
import unittest
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

import support
from support import USER, habit, ics, make_calendar, make_home, make_loops, make_rule, make_wallet, ms
from hublib import daybook, health, rules, sources
from hublib.log import HubLog
from hublib.sources import Sources

NOW = datetime(2026, 11, 6, 15, 0)
WEEK_AGO = date(2026, 10, 31)
BARE = SimpleNamespace(entry_times={}, entry_notes={})


class Base(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="xrnhub-test-")
        self.addCleanup(shutil.rmtree, self.home)
        self.log = HubLog(self.home + "/hub")

    def src(self):
        s = Sources(self.home, USER)
        s.calendar.sweep()
        return s


class HabitLogic(unittest.TestCase):
    def test_habit_day_starts_late(self):
        self.assertEqual(daybook.habit_day(datetime(2026, 11, 6, 1, 30), 3), date(2026, 11, 5))
        self.assertEqual(daybook.habit_day(datetime(2026, 11, 6, 3, 0), 3), date(2026, 11, 6))

    def test_ticks(self):
        h = {"type": 0, "targetType": 0, "targetValue": 0}
        self.assertEqual([daybook.habit_done(h, v) for v in (None, -1, 0, 1, 2, 3)],
                         [False, False, False, True, True, False])

    def test_at_least_is_in_thousandths(self):
        h = {"type": 1, "targetType": 0, "targetValue": 2.0}
        self.assertTrue(daybook.habit_done(h, 2000))
        self.assertFalse(daybook.habit_done(h, 1999))

    def test_at_most(self):
        h = {"type": 1, "targetType": 1, "targetValue": 2.0}
        self.assertTrue(daybook.habit_done(h, 2000))
        self.assertFalse(daybook.habit_done(h, 2001))

    def test_a_weekly_habit_only_counts_when_done(self):
        weekly = {"id": "w", "name": "W", "archived": False, "type": 0, "targetType": 0, "targetValue": 0,
                  "freqNum": 3, "freqDen": 7, "unit": "", "color": 1}
        rows = daybook.habit_rows(BARE, [weekly], {}, date(2026, 11, 6))
        self.assertEqual(daybook.habit_score(rows), {"done": 0, "total": 0})
        rows = daybook.habit_rows(BARE, [weekly], {"w": {"2026-11-06": 2}}, date(2026, 11, 6))
        self.assertEqual(daybook.habit_score(rows), {"done": 1, "total": 1})

    def test_a_skipped_day_leaves_the_score_alone(self):
        h = {"id": "a", "name": "A", "archived": False, "type": 0, "targetType": 0, "targetValue": 0,
             "freqNum": 1, "freqDen": 1, "unit": "", "color": 1}
        rows = daybook.habit_rows(BARE, [h], {"a": {"2026-11-06": 3}}, date(2026, 11, 6))
        self.assertEqual(daybook.habit_score(rows)["total"], 0)
        self.assertTrue(rows[0]["skipped"])


class Blocks(unittest.TestCase):
    def view(self, block, doc=None):
        return daybook.block_view({"id": "b", "name": "YouTube", **block}, doc or {}, NOW)

    def test_off(self):
        self.assertEqual(self.view({"enabled": False})["state"], "off")

    def test_habit_block_blocks_until_opened(self):
        self.assertEqual(self.view({"mode": "HABIT"})["state"], "blocked")

    def test_session_opens_it(self):
        doc = {"sessions": {"b": {"startedAtWall": daybook.ms_of(NOW) - 1000, "durationMs": 60000}}}
        view = self.view({"mode": "HABIT"}, doc)
        self.assertEqual(view["state"], "open")
        self.assertEqual(view["until"], daybook.ms_of(NOW) + 59000)

    def test_limit_counts_todays_use_across_devices(self):
        today = daybook.day_start_ms(NOW)
        doc = {"allowance": {"b": {
            "phone": {"dayStartWall": today, "ms": 600000},
            "laptop": {"dayStartWall": today, "ms": 300000},
            "stale": {"dayStartWall": today - 86400000, "ms": 9999999},
        }}}
        view = self.view({"mode": "ALLOWANCE", "dailyAllowanceMs": 1800000}, doc)
        self.assertEqual((view["state"], view["usedMs"]), ("open", 900000))
        view = self.view({"mode": "ALLOWANCE", "dailyAllowanceMs": 900000}, doc)
        self.assertEqual(view["state"], "blocked")

    def test_outside_its_hours(self):
        schedule = {"activeDays": [1, 2, 3, 4, 5, 6, 7],
                    "activeWindow": {"startMinuteOfDay": 18 * 60, "endMinuteOfDay": 22 * 60}, "moreWindows": []}
        self.assertEqual(self.view({"mode": "SCHEDULE", "schedule": schedule})["state"], "off")

    def test_overnight_window_runs_past_midnight(self):
        window = {"startMinuteOfDay": 22 * 60, "endMinuteOfDay": 6 * 60}
        self.assertTrue(daybook.window_active([5], window, datetime(2026, 11, 7, 1, 0)))  # Sat 01:00, Friday's window
        self.assertFalse(daybook.window_active([6], window, datetime(2026, 11, 7, 1, 0)))

    def test_lockdown(self):
        wall = daybook.ms_of(NOW)
        doc = {"lockdowns": {"b": {"startedAtWall": wall - 1000, "durationMs": 60000}}}
        self.assertEqual(self.view({"mode": "LOCKDOWN"}, doc)["state"], "blocked")
        self.assertEqual(self.view({"mode": "LOCKDOWN"})["state"], "off")


class Money(unittest.TestCase):
    def test_spending_in_base_currency(self):
        wallet = {
            "settings": json.dumps({"baseCurrency": "AUD"}),
            "accounts": [{}],
            "categories": [{"id": 1, "name": "Food"}],
            "budgets": [{"categoryId": None, "amountCents": 100000}],
            "bills": [
                {"type": 0, "amountCents": 1000, "rate": 1.0, "atMillis": ms(2026, 11, 6, 9), "categoryId": 1},
                {"type": 0, "amountCents": 1000000, "rate": 0.0001, "atMillis": ms(2026, 11, 2, 9), "categoryId": 1},
                {"type": 1, "amountCents": 5000, "rate": 1.0, "atMillis": ms(2026, 11, 2, 9)},
                {"type": 2, "amountCents": 7777, "rate": 1.0, "atMillis": ms(2026, 11, 6, 9)},
                {"type": 0, "amountCents": 9999, "rate": 1.0, "atMillis": ms(2026, 10, 30, 9)},
            ],
        }
        view = daybook.money_view(wallet, NOW)
        self.assertEqual(view["todayCents"], 1000)
        self.assertEqual(view["monthCents"], 1100)
        self.assertEqual(view["earnedCents"], 5000)
        self.assertEqual(view["budgetCents"], 100000)
        self.assertEqual(view["top"], [{"name": "Food", "cents": 1100}])

    def test_a_bill_with_an_impossible_time_or_amount_is_left_out(self):
        wallet = {"accounts": [{}], "bills": [
            {"type": 0, "amountCents": 1000, "rate": 1.0, "atMillis": ms(2026, 11, 6, 9)},
            {"type": 0, "amountCents": 500, "rate": 1.0, "atMillis": 1e300},
            {"type": 0, "amountCents": 500, "rate": 1.0, "atMillis": 10**20},
            {"type": 0, "amountCents": 1e308, "rate": 1e308, "atMillis": ms(2026, 11, 6, 10)},
        ]}
        self.assertEqual(daybook.money_view(wallet, NOW)["todayCents"], 1000)

    def test_no_wallet_shows_nothing(self):
        self.assertIsNone(daybook.money_view({}, NOW))


class Budget(unittest.TestCase):
    """The budget as xrnwal's Budget card works it out (Stats.budget), so both show one number."""

    def wallet(self, bills, **settings):
        return {
            "settings": json.dumps({"baseCurrency": "AUD", **settings}),
            "accounts": [{}],
            "budgets": [{"categoryId": None, "amountCents": 100000}, {"categoryId": 1, "amountCents": 100}],
            "bills": [{"type": 0, "rate": 1.0, **b} for b in bills],
        }

    def test_a_bill_left_out_of_the_budget_is_spent_but_not_against_it(self):
        # Rent is marked in xrnwal as outside the budget (inBudget false) and the wallet leaves it
        # out of its budget, but the hub put it against the budget all the same.
        view = daybook.money_view(self.wallet([
            {"amountCents": 150000, "atMillis": ms(2026, 11, 1, 9), "inBudget": False},
            {"amountCents": 2000, "atMillis": ms(2026, 11, 6, 9), "inBudget": True},
            {"amountCents": 500, "atMillis": ms(2026, 11, 6, 10)},
            {"amountCents": 9000, "atMillis": ms(2026, 11, 6, 11), "inBudget": False},
        ]), NOW)
        budget = view["budget"]
        self.assertEqual(view["monthCents"], 161500)
        self.assertEqual((budget["spentCents"], budget["todayCents"], budget["outsideCents"]), (2500, 2500, 159000))
        self.assertEqual((budget["cents"], budget["leftCents"]), (100000, 97500))
        # 25 days are left, today among them, as the app counts them.
        self.assertEqual((budget["daysLeft"], budget["perDayCents"]), (25, 3900))
        self.assertEqual(budget["days"][5], 2500)

    def test_the_nudges_see_what_counts_against_the_budget(self):
        view = daybook.money_view(self.wallet([
            {"amountCents": 150000, "atMillis": ms(2026, 11, 6, 9), "inBudget": False},
            {"amountCents": 2000, "atMillis": ms(2026, 11, 6, 9)},
        ]), NOW)
        f = rules.facts({"habits": {"done": 0, "total": 0}, "money": view, "blocks": {"hits": 0}}, [], NOW)
        self.assertEqual((f["spent_day"], f["spent_month"]), (20, 20))

    def test_the_month_starts_on_the_wallets_start_of_month(self):
        view = daybook.money_view(self.wallet([
            {"amountCents": 100, "atMillis": ms(2026, 10, 14, 9)},
            {"amountCents": 200, "atMillis": ms(2026, 10, 15, 9)},
            {"amountCents": 400, "atMillis": ms(2026, 11, 6, 9)},
        ], startDay=15), NOW)
        self.assertEqual((view["budget"]["first"], view["budget"]["last"]), ("2026-10-15", "2026-11-14"))
        self.assertEqual((view["monthCents"], view["budget"]["spentCents"]), (600, 600))

    def test_a_start_on_a_weekend_moves_as_the_wallet_says(self):
        # 1 November 2026 is a Sunday.
        settings = {"startDay": 1}
        self.assertEqual(daybook.accounting_month(settings, date(2026, 11, 6)), (date(2026, 11, 1), date(2026, 11, 30)))
        earlier = daybook.accounting_month({**settings, "weekendRule": daybook.EARLIER}, date(2026, 10, 31))
        self.assertEqual(earlier, (date(2026, 10, 30), date(2026, 11, 30)))
        later = daybook.accounting_month({**settings, "weekendRule": daybook.LATER}, date(2026, 11, 1))
        self.assertEqual(later, (date(2026, 10, 1), date(2026, 11, 1)))
        self.assertEqual(daybook.accounting_month({"startDay": 31}, date(2026, 11, 6)), (date(2026, 10, 31), date(2026, 11, 29)))
        self.assertEqual(daybook.accounting_month(settings, date(2026, 1, 6), back=2), (date(2025, 11, 1), date(2025, 11, 30)))

    def test_a_bill_later_this_month_counts_only_when_the_wallet_includes_the_future(self):
        bills = [{"amountCents": 100, "atMillis": ms(2026, 11, 6, 9)}, {"amountCents": 900, "atMillis": ms(2026, 11, 20, 9)}]
        self.assertEqual(daybook.money_view(self.wallet(bills), NOW)["budget"]["spentCents"], 1000)
        self.assertEqual(daybook.money_view(self.wallet(bills, includeFuture=False), NOW)["budget"]["spentCents"], 100)
        # Later today is today, either way.
        later = [{"amountCents": 300, "atMillis": ms(2026, 11, 6, 22)}]
        self.assertEqual(daybook.money_view(self.wallet(later, includeFuture=False), NOW)["budget"]["todayCents"], 300)

    def test_a_budget_switched_off_in_the_wallet_is_none(self):
        view = daybook.money_view(self.wallet([], budgetEnabled=False), NOW)
        self.assertIsNone(view["budgetCents"])
        self.assertIsNone(view["budget"]["leftCents"])

    def test_a_bill_keeps_the_day_it_was_entered_on(self):
        # 23:30 in Sydney on the 5th is 12:30 UTC; read where it was entered, it is still the 5th.
        bill = {"atMillis": int(datetime(2026, 11, 5, 12, 30, tzinfo=timezone.utc).timestamp() * 1000), "offsetSeconds": 11 * 3600}
        self.assertEqual(daybook.bill_date(bill), date(2026, 11, 5))
        self.assertEqual(daybook.bill_date({**bill, "offsetSeconds": -4 * 3600}), date(2026, 11, 5))
        self.assertEqual(daybook.bill_date({**bill, "offsetSeconds": 13 * 3600}), date(2026, 11, 6))

    def test_home_shows_the_ledger_it_was_left_on_and_the_budget_spans_every_ledger(self):
        wallet = self.wallet([
            {"amountCents": 100, "atMillis": ms(2026, 11, 6, 9), "ledgerId": 1},
            {"amountCents": 300, "atMillis": ms(2026, 11, 6, 9), "ledgerId": 2},
        ], activeLedger=2)
        wallet["ledgers"] = [{"id": 1}, {"id": 2}]
        view = daybook.money_view(wallet, NOW)
        self.assertEqual((view["monthCents"], view["budget"]["spentCents"]), (300, 400))
        wallet["ledgers"] = [{"id": 1}]  # a ledger since deleted: Home shows them all
        self.assertEqual(daybook.money_view(wallet, NOW)["monthCents"], 400)


class Home(unittest.TestCase):
    def test_shopping_and_expiring(self):
        items = [
            {"name": "Milk", "wanted": True, "quantity": 0},
            {"name": "Snoozed", "wanted": True, "snoozed": True, "quantity": 0},
            {"name": "Eggs", "quantity": 4, "expiryDate": "2026-11-08T00:00:00.000Z"},
            {"name": "Rice", "quantity": 1, "expiryDate": "2027-11-08"},
            {"name": "Gone", "quantity": 0, "expiryDate": "2026-11-07"},
        ]
        view = daybook.home_view(items, NOW)
        self.assertEqual(view["shopping"], ["Milk"])
        self.assertEqual(view["expiring"], [{"name": "Eggs", "days": 2}])


class Whole(Base):
    def fill(self):
        make_loops(self.home, [habit("a", "Gym", position=1), habit("b", "Read", position=2)],
                   [("a", "2026-11-06", 2), ("a", "2026-11-05", 2), ("b", "2026-11-05", 0)])
        make_rule(self.home, {
            "blocks": {"b1": {"id": "b1", "name": "YouTube", "mode": "ALLOWANCE", "dailyAllowanceMs": 1800000}},
            "settings": {"overridePhrase": "never show this"},
        }, [{"d": "phone", "t": "blocked", "at": ms(2026, 11, 6, 10), "target": "com.x", "name": "X"}])
        make_wallet(self.home, {"accounts": [{}], "bills": [
            {"type": 0, "amountCents": 1234, "rate": 1.0, "atMillis": ms(2026, 11, 6, 9), "note": "Lunch"},
        ]})
        make_home(self.home, [{"name": "Milk", "wanted": True, "quantity": 0}])
        make_calendar(self.home, "personal", [
            ics("UID:1", "DTSTART:20261106T170000", "DTEND:20261106T180000", "SUMMARY:Dinner"),
            ics("UID:2", "DTSTART:20261106T090000", "DTEND:20261106T100000", "SUMMARY:Over already"),
        ], display="Personal")

    def test_today_across_apps(self):
        self.fill()
        view = daybook.today_view(self.src(), self.log, NOW)
        self.assertEqual(view["day"], "2026-11-06")
        self.assertEqual((view["habits"]["done"], view["habits"]["total"]), (1, 2))
        self.assertEqual(view["blocks"]["hits"], 1)
        self.assertEqual([e["title"] for e in view["calendar"]["items"]], ["Dinner"])
        self.assertEqual(view["calendar"]["items"][0]["calendar"], "Personal")
        self.assertEqual(view["money"]["todayCents"], 1234)
        self.assertEqual(view["home"]["shopping"], ["Milk"])

    def test_the_whole_day_has_the_events_already_over(self):
        self.fill()
        view = daybook.today_view(self.src(), self.log, NOW)
        self.assertEqual([e["title"] for e in view["calendar"]["day"]], ["Over already", "Dinner"])
        self.assertEqual(view["dayStartHour"], 3)

    def test_a_number_written_as_text_by_another_app_shows_less_but_never_fails_today(self):
        # A string where a number belongs, from a bad import or a field changed in xrnhome, xrnrule
        # or xrnloops, raised TypeError, and the whole Today page said "Can't reach the server".
        make_loops(self.home, [habit("a", "Water", type=1, targetValue="2000")], [("a", "2026-11-06", 2500000)])
        make_rule(self.home, {
            "blocks": {"b1": {"id": "b1", "name": "YouTube", "mode": "ALLOWANCE", "dailyAllowanceMs": "1800000"}},
            "allowance": {"b1": {"phone": {"ms": "600000", "dayStartWall": daybook.day_start_ms(NOW)}}},
            "sessions": {"b1": {"startedAtWall": "1", "durationMs": None}},
        })
        make_home(self.home, [{"name": "Eggs", "quantity": "6", "expiryDate": "2026-11-07"}])
        view = daybook.today_view(self.src(), self.log, NOW)
        self.assertEqual(view["home"]["expiring"], [])  # a quantity that isn't a number counts as none
        self.assertEqual([b["state"] for b in view["blocks"]["items"]], ["open"])

    def test_a_blocked_open_stamped_after_now_is_not_one_of_today(self):
        # xrnrule took any time, so one stamped years ahead was counted every day.
        make_rule(self.home, {"blocks": {}}, [
            {"d": "phone", "t": "blocked", "at": ms(2026, 11, 6, 10), "target": "com.x"},
            {"d": "phone", "t": "blocked", "at": ms(2026, 11, 6, 16), "target": "com.x"},  # an hour on
            {"d": "phone", "t": "blocked", "at": ms(2030, 1, 1, 0), "target": "com.x"},
        ])
        self.assertEqual(daybook.today_view(self.src(), self.log, NOW)["blocks"]["hits"], 1)

    def test_the_override_sentence_never_leaves(self):
        self.fill()
        text = json.dumps(daybook.today_view(self.src(), self.log, NOW))
        self.assertNotIn("never show this", text)

    def test_state_reports_feed_the_card_but_are_not_on_the_timeline(self):
        at = int(NOW.timestamp() * 1000) - 3600000
        self.log.add(USER, [
            {"app": "xrnmail", "kind": "batch", "at": at - 1000, "value": 3},
            {"app": "xrnmail", "kind": "state.held", "at": at, "value": 2, "detail": {"held": 2}},
        ])
        items = daybook.happenings(self.src(), self.log, NOW, WEEK_AGO, NOW.date())["items"]
        self.assertEqual([e["kind"] for e in items if e["app"] == "xrnmail"], ["batch"])
        self.assertEqual(daybook.today_view(self.src(), self.log, NOW)["mail"]["detail"], {"held": 2})

    def test_timeline_is_past_only_and_newest_first(self):
        self.fill()
        items = daybook.happenings(self.src(), self.log, NOW, WEEK_AGO, NOW.date())["items"]
        titles = [e["title"] for e in items]
        self.assertIn("Over already", titles)
        self.assertNotIn("Dinner", titles)
        self.assertEqual(sorted((e["at"] for e in items), reverse=True), [e["at"] for e in items])
        self.assertEqual({"xrnloops", "xrnrule", "xrnwal", "xrncal"}, {e["app"] for e in items})

    def test_a_row_carries_what_a_tick_needs_to_win(self):
        self.fill()
        src = self.src()
        rows = daybook.today_view(src, self.log, NOW)["habits"]["items"]
        gym = next(r for r in rows if r["name"] == "Gym")
        self.assertEqual((gym["id"], gym["value"], gym["type"]), ("a", 2, 0))
        self.assertEqual(gym["mtime"], ms(2026, 11, 6, 12))
        self.assertEqual(gym["notes"], "")

    def test_streaks_run_through_skips_and_an_open_today(self):
        h = {"type": 0, "targetType": 0, "targetValue": 0, "freqNum": 1, "freqDen": 1}
        per_day = {"2026-11-05": 2, "2026-11-04": 3, "2026-11-03": 2, "2026-11-01": 2}
        self.assertEqual(daybook.habit_streak(h, per_day, date(2026, 11, 6)), 2)
        self.assertEqual(daybook.habit_streak(h, {**per_day, "2026-11-06": 2}, date(2026, 11, 6)), 3)
        self.assertEqual(daybook.habit_streak(h, per_day, date(2026, 11, 7)), 0)

    def test_an_at_most_habit_counts_a_low_number_and_not_a_blank(self):
        h = {"type": 1, "targetType": 1, "targetValue": 2, "freqNum": 1, "freqDen": 1}
        self.assertTrue(daybook.habit_done(h, 0))
        self.assertTrue(daybook.habit_done(h, 2000))
        self.assertFalse(daybook.habit_done(h, 3000))
        self.assertFalse(daybook.habit_done(h, None))
        self.assertFalse(daybook.habit_done(h, -1))

    def test_the_strip_shows_fourteen_days_ending_today(self):
        self.fill()
        strip = daybook.today_view(self.src(), self.log, NOW)["recent"]
        self.assertEqual(len(strip), 14)
        self.assertEqual(strip[-1]["day"], "2026-11-06")
        self.assertEqual((strip[-1]["done"], strip[-1]["logged"]), (1, 1))
        self.assertEqual((strip[-2]["done"], strip[-2]["logged"]), (1, 2))
        self.assertEqual(strip[0]["logged"], 0)

    def test_a_block_says_what_it_waits_for(self):
        make_loops(self.home, [habit("a", "Gym"), habit("b", "Read")],
                   [("a", "2026-11-06", 2), ("b", "2026-11-05", 2)])
        make_rule(self.home, {
            "blocks": {"b1": {"id": "b1", "name": "Games", "mode": "HABIT"}},
            "items": {
                "2": {"id": "2", "blockId": "b1", "text": "Read", "order": 1, "habitId": "b", "linkedDay": "YESTERDAY"},
                "1": {"id": "1", "blockId": "b1", "text": "Gym", "order": 0, "habitId": "a", "linkedDay": "TODAY"},
                "3": {"id": "3", "blockId": "b1", "text": "Tidy", "order": 2, "habitId": None, "done": False},
                "4": {"id": "4", "blockId": "other", "text": "Not here", "order": 0},
            },
        })
        needs = daybook.today_view(self.src(), self.log, NOW)["blocks"]["items"][0]["needs"]
        self.assertEqual([(n["text"], n["done"]) for n in needs], [("Gym", True), ("Read", True), ("Tidy", False)])

    def test_countdowns_take_named_calendars_only(self):
        make_calendar(self.home, "dl", [
            ics("UID:1", "DTSTART;VALUE=DATE:20261120", "DTEND;VALUE=DATE:20261121", "SUMMARY:Essay"),
            ics("UID:2", "DTSTART;VALUE=DATE:20261001", "DTEND;VALUE=DATE:20261002", "SUMMARY:Gone"),
        ], display="Deadlines")
        make_calendar(self.home, "other", [
            ics("UID:3", "DTSTART;VALUE=DATE:20261110", "DTEND;VALUE=DATE:20261111", "SUMMARY:Holiday"),
        ], display="Event")
        found = daybook.today_view(self.src(), self.log, NOW)["countdowns"]
        self.assertEqual([(c["title"], c["days"]) for c in found], [("Essay", 14)])

    def test_a_day_in_full(self):
        self.fill()
        view = daybook.day_view(self.src(), self.log, NOW, date(2026, 11, 6))
        self.assertEqual({e["title"] for e in view["calendar"]["items"]}, {"Dinner", "Over already"})
        self.assertEqual({e["kind"] for e in view["log"]}, {"blocked", "spent"})

    def test_a_day_in_full_has_the_bodys_day(self):
        night = ms(2026, 11, 5, 23)
        health.write(self.log, USER, {"source": "xrnfit-watch", "upsert": [
            {"id": "s", "kind": "steps", "start": ms(2026, 11, 6, 9), "end": ms(2026, 11, 6, 10), "value": 4321},
            {"id": "z", "kind": "sleep", "start": night, "end": night + 7 * 3_600_000,
             "stages": [[night, night + 2 * 3_600_000, "light"], [night + 2 * 3_600_000, night + 7 * 3_600_000, "rem"]]},
            {"id": "t", "kind": "stress", "samples": [[ms(2026, 11, 6, 9), 20], [ms(2026, 11, 6, 10), 40]]},
            {"id": "x", "kind": "steps", "start": ms(2026, 11, 5, 9), "end": ms(2026, 11, 5, 10), "value": 99},
        ]}, now_ms=ms(2026, 11, 6, 12))
        found = daybook.day_view(self.src(), self.log, NOW, date(2026, 11, 6))["health"]
        self.assertEqual(found["steps"], 4321)
        self.assertEqual((found["sleep"]["minutes"], found["sleep"]["light"], found["sleep"]["rem"]), (420, 120, 300))
        self.assertEqual(found["stress"], {"min": 20, "avg": 30, "max": 40})
        self.assertIsNone(daybook.day_view(self.src(), self.log, NOW, date(2026, 11, 4))["health"])

    def test_a_day_in_full_leaves_last_night_on_last_night(self):
        make_calendar(self.home, "c", [
            ics("UID:1", "DTSTART:20261105T220000", "DTEND:20261106T010000", "SUMMARY:Late one"),
            ics("UID:2", "DTSTART:20261106T090000", "DTEND:20261106T100000", "SUMMARY:Morning"),
        ])
        view = daybook.day_view(self.src(), self.log, NOW, date(2026, 11, 6))
        self.assertEqual([e["title"] for e in view["calendar"]["items"]], ["Morning"])

    def test_a_day_in_full_stops_where_the_habit_day_stops(self):
        make_loops(self.home, [habit("a", "Gym")], [])
        make_rule(self.home, {}, [
            {"t": "blocked", "at": ms(2026, 11, 5, 4), "name": "In"},
            {"t": "blocked", "at": ms(2026, 11, 6, 1), "name": "Still in"},
            {"t": "blocked", "at": ms(2026, 11, 6, 4), "name": "Out"},
        ])
        view = daybook.day_view(self.src(), self.log, NOW, date(2026, 11, 5))
        self.assertEqual({e["title"] for e in view["log"]}, {"In", "Still in"})

    def test_day_table_leaves_a_habit_out_before_it_began(self):
        make_loops(self.home, [habit("a", "Old"), habit("b", "New")],
                   [("a", "2026-10-01", 2), ("a", "2026-10-15", 0), ("a", "2026-11-04", 2),
                    ("b", "2026-11-03", 2), ("b", "2026-11-04", 2)])
        rows = daybook.day_table(self.src(), NOW)
        by_day = {r["day"].isoformat(): r for r in rows}
        self.assertNotIn("b", by_day["2026-10-15"]["habits"])
        self.assertEqual(by_day["2026-10-15"]["habits"], {"a": False})
        self.assertEqual(by_day["2026-11-04"]["habits"], {"a": True, "b": True})
        self.assertEqual(rows[-1]["day"], date(2026, 11, 5))

    def test_a_day_nobody_logged_is_not_a_bad_day(self):
        habits = [habit(f"h{i}", f"H{i}") for i in range(9)]
        full = [(f"h{i}", "2026-11-01", 2) for i in range(9)] + [(f"h{i}", "2026-11-05", 2) for i in range(9)]
        make_loops(self.home, habits, full + [("h0", "2026-11-03", 2)])
        rows = daybook.day_table(self.src(), NOW)
        by_day = {r["day"].isoformat(): r for r in rows}
        self.assertEqual(len(by_day["2026-11-01"]["habits"]), 9)
        self.assertEqual(by_day["2026-11-03"]["habits"], {})  # one tick of nine
        self.assertEqual(by_day["2026-11-04"]["habits"], {})  # nothing at all

    def test_quiet_habits_still_count_toward_whether_a_day_was_logged(self):
        habits = [habit(f"h{i}", f"H{i}") for i in range(9)]
        early = [(f"h{i}", "2026-10-20", 2) for i in range(9)]
        make_loops(self.home, habits, early + [("h0", "2026-11-03", 2), ("h0", "2026-11-04", 2)])
        rows = daybook.day_table(self.src(), NOW)
        self.assertEqual({r["day"].isoformat(): r for r in rows}["2026-11-03"]["habits"], {})

    def test_an_archived_habit_does_stop(self):
        habits = [habit("a", "Old", archived=True), habit("b", "B"), habit("c", "C")]
        entries = [("a", "2026-10-20", 2), ("b", "2026-10-20", 2), ("c", "2026-10-20", 2),
                   ("b", "2026-11-03", 2), ("c", "2026-11-03", 2)]
        make_loops(self.home, habits, entries)
        rows = daybook.day_table(self.src(), NOW)
        self.assertNotIn("a", {r["day"].isoformat(): r for r in rows}["2026-11-03"]["habits"])

    def test_a_day_of_real_noes_still_counts(self):
        habits = [habit(f"h{i}", f"H{i}") for i in range(6)]
        make_loops(self.home, habits, [(f"h{i}", "2026-11-03", 0) for i in range(6)] + [("h0", "2026-11-04", 2)])
        rows = daybook.day_table(self.src(), NOW)
        by_day = {r["day"].isoformat(): r for r in rows}
        self.assertEqual(sum(by_day["2026-11-03"]["habits"].values()), 0)
        self.assertEqual(len(by_day["2026-11-03"]["habits"]), 6)

    def test_missing_services_read_as_empty(self):
        view = daybook.today_view(self.src(), self.log, NOW)
        self.assertEqual(view["habits"]["items"], [])
        self.assertIsNone(view["money"])
        self.assertIsNone(view["home"])
        self.assertIsNone(view["health"])

    def test_broken_files_read_as_empty(self):
        make_wallet(self.home, {"bills": []})
        with open(f"{self.home}/xrnwal-sync/data/users/{USER}/state.json", "w") as f:
            f.write("{ not json")
        self.assertIsNone(daybook.today_view(self.src(), self.log, NOW)["money"])

    def test_nothing_opens_a_source_for_writing(self):
        self.fill()
        before = {}
        import os

        for folder, _, files in os.walk(self.home):
            if "/hub" in folder:
                continue
            for name in files:
                if not name.endswith(("-wal", "-shm")):
                    path = os.path.join(folder, name)
                    with open(path, "rb") as f:
                        before[path] = (f.read(), os.stat(path).st_mtime_ns)
        src = self.src()
        daybook.today_view(src, self.log, NOW)
        daybook.happenings(src, self.log, NOW, WEEK_AGO, NOW.date())
        daybook.day_table(src, NOW)
        for path, (body, stamp) in before.items():
            with open(path, "rb") as f:
                self.assertEqual(f.read(), body, path)
            self.assertEqual(os.stat(path).st_mtime_ns, stamp, path)



class SourceCaches(Base):
    """Today, Review and the nudges ask every minute for every habit entry ever made, the devices'
    whole log and the calendar; each is worked out again only when its files change."""

    def settle(self, path, mtime_ns=None):
        """As if [path] last changed a minute ago (or at [mtime_ns]): past the racy window."""
        stamp = mtime_ns or time.time_ns() - 60 * 10**9
        os.utime(path, ns=(stamp, stamp))
        return stamp

    def test_habits_are_read_again_only_when_the_database_changes(self):
        make_loops(self.home, [habit("a", "Gym")], [("a", "2026-11-05", 2)])
        src = Sources(self.home, USER)
        path = src.path("xrnloops", "loop.sqlite")
        writer = sqlite3.connect(path)  # as xrnloops' server holds it: WAL, commits not yet checkpointed
        self.addCleanup(writer.close)
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("UPDATE meta SET value = 4 WHERE key = 'day_start_hour'")
        writer.commit()
        main = self.settle(path)
        self.settle(path + "-wal")
        with mock.patch.object(sources.sqlite3, "connect", wraps=sqlite3.connect) as opened:
            for _ in range(3):
                self.assertEqual(src.habits()[1:], ({"a": {"2026-11-05": 2}}, 4))
        self.assertEqual(opened.call_count, 1)
        # A tick lands in the write-ahead log alone; the database file itself is untouched.
        writer.execute("INSERT INTO entries(habit, date, value, mtime) VALUES('a', '2026-11-06', 2, 5)")
        writer.commit()
        self.settle(path, main)
        self.settle(path + "-wal")
        self.assertEqual(src.habits()[1], {"a": {"2026-11-05": 2, "2026-11-06": 2}})
        self.assertEqual(src.entry_times[("a", "2026-11-06")], 5)

    def test_a_database_that_just_changed_is_not_trusted_to_its_mtime(self):
        # A phone's clock ticks file times in steps of a few ms: two commits inside one step leave
        # the same mtime, so a read between them can't be kept on the mtime alone.
        make_loops(self.home, [habit("a", "Gym")], [])
        src = Sources(self.home, USER)
        os.utime(src.path("xrnloops", "loop.sqlite"))  # changed just now
        with mock.patch.object(sources.sqlite3, "connect", wraps=sqlite3.connect) as opened:
            src.habits()
            src.habits()
        self.assertEqual(opened.call_count, 2)

    def test_the_devices_log_is_parsed_once_then_only_what_is_added(self):
        make_rule(self.home, {"blocks": {}}, [{"t": "blocked", "at": 1000}, {"t": "unlock", "at": 2000}])
        src = Sources(self.home, USER)
        path = src.path("xrnrule", "events.jsonl")
        with mock.patch.object(sources.json, "loads", wraps=json.loads) as parsed:
            self.assertEqual([e["at"] for e in src.rule_events()], [1000, 2000])
            self.assertEqual([e["at"] for e in src.rule_events(1500)], [2000])
        self.assertEqual(parsed.call_count, 2)
        with open(path, "a", encoding="utf-8") as f:
            f.write('{"t":"blocked","at":3000}\nnot json\n{"t":"blocked","at":40')  # the last still being written
        with mock.patch.object(sources.json, "loads", wraps=json.loads) as parsed:
            self.assertEqual([e["at"] for e in src.rule_events()], [1000, 2000, 3000])
        self.assertEqual(parsed.call_count, 2)
        with open(path, "a", encoding="utf-8") as f:
            f.write('00}\n')
        self.assertEqual([e["at"] for e in src.rule_events(2500)], [3000, 4000])
        # xrnrule drops old events by writing a new file in its place.
        make_rule(self.home, {"blocks": {}}, [{"t": "unlock", "at": 3000}, {"t": "blocked", "at": 5000}])
        self.assertEqual([e["at"] for e in src.rule_events()], [3000, 5000])
        os.remove(path)
        self.assertEqual(src.rule_events(), [])

    def test_a_log_rewritten_in_place_is_read_again(self):
        make_rule(self.home, {"blocks": {}}, [{"t": "blocked", "at": 1000}])
        src = Sources(self.home, USER)
        path = src.path("xrnrule", "events.jsonl")
        self.assertEqual([e["at"] for e in src.rule_events()], [1000])
        with open(path, "w", encoding="utf-8") as f:  # same file, same length up to here, other lines
            f.write('{"t":"blocked","at":7000}\n{"t":"blocked","at":8000}\n')
        self.assertEqual([e["at"] for e in src.rule_events()], [7000, 8000])

    def write_event(self, name, title, start):
        make_calendar(self.home, "personal", [
            ics("UID:1", f"DTSTART:{start}T090000", f"DTEND:{start}T100000", "RRULE:FREQ=DAILY", f"SUMMARY:{title}"),
        ], display=name)

    def test_a_sweep_that_finds_nothing_changed_keeps_what_was_worked_out(self):
        # Each sweep threw the memo away, so the next Today and Review worked every repeating
        # event out again from its first date, every minute, with nothing changed.
        self.write_event("Personal", "Standup", "20200106")
        cal = self.src().calendar
        week = cal.between(date(2026, 11, 6), date(2026, 11, 13))
        self.assertEqual(len(week), 8)
        cal.sweep()
        self.assertIs(cal.between(date(2026, 11, 6), date(2026, 11, 13)), week)
        # A changed file, a new name for the calendar, a file gone: each is seen at the next sweep.
        path = os.path.join(cal.root, "personal", "0.ics")
        self.write_event("Personal", "Retro", "20200106")
        self.settle(path, os.stat(path).st_mtime_ns + 10**9)
        cal.sweep()
        self.assertEqual({o["title"] for o in cal.between(date(2026, 11, 6), date(2026, 11, 13))}, {"Retro"})
        self.write_event("Work", "Retro", "20200106")
        cal.sweep()
        self.assertEqual({o["calendar"] for o in cal.between(date(2026, 11, 6), date(2026, 11, 13))}, {"Work"})
        os.remove(path)
        cal.sweep()
        self.assertEqual(cal.between(date(2026, 11, 6), date(2026, 11, 13)), [])


class BaseCents(unittest.TestCase):
    def test_a_half_cent_rounds_up_as_the_wallet_does(self):
        # 5 cents at 0.5 is 2.5. The app's roundToLong makes it 3, and -2.5 makes -2: a half goes up.
        # Python's round() sent 2.5 to the even side, 2, so a day's spending could be a cent off the app's.
        self.assertEqual(daybook.bill_base_cents({"amountCents": 5, "rate": 0.5}), 3)
        self.assertEqual(daybook.bill_base_cents({"amountCents": -5, "rate": 0.5}), -2)
        self.assertEqual(daybook.bill_base_cents({"amountCents": 1234, "rate": 1.0}), 1234)


class Body(Base):
    def put(self, source, *records):
        health.write(self.log, USER, {"source": source, "upsert": list(records)}, now_ms=ms(2026, 11, 6, 14))

    def body(self, now=NOW):
        return daybook.today_view(self.src(), self.log, now)["health"]

    def test_today_has_the_days_steps_last_night_resting_and_weight(self):
        night = ms(2026, 11, 5, 23)
        self.put("xrnfit-watch",
                 {"id": "s1", "kind": "steps", "start": ms(2026, 11, 6, 9), "end": ms(2026, 11, 6, 10), "value": 4000},
                 {"id": "s2", "kind": "steps", "start": ms(2026, 11, 6, 12), "end": ms(2026, 11, 6, 13), "value": 321},
                 {"id": "z", "kind": "sleep", "start": night, "end": night + 7 * 3_600_000},
                 {"id": "r", "kind": "resting_heart_rate", "start": ms(2026, 11, 6, 6), "value": 54})
        self.put("xrnfit-scale",
                 {"id": "w0", "kind": "weight", "start": ms(2026, 10, 30, 7), "value": 73.1},
                 {"id": "w1", "kind": "weight", "start": ms(2026, 11, 6, 7), "value": 72.6})
        body = self.body()
        self.assertEqual((body["day"], body["steps"]), ("2026-11-06", 4321))
        self.assertEqual(body["sleep"], {"day": "2026-11-06", "minutes": 420, "start": night, "end": night + 7 * 3_600_000})
        self.assertEqual(body["resting"], {"day": "2026-11-06", "bpm": 54})
        self.assertEqual(body["weight"], {"at": ms(2026, 11, 6, 7), "kg": 72.6, "change": -0.5, "since": ms(2026, 10, 30, 7)})
        self.assertEqual(body["updated"], ms(2026, 11, 6, 14))
        recent = body["recent"]
        self.assertEqual(recent["first"], "2026-10-24")
        self.assertEqual((len(recent["steps"]), recent["steps"][-1], recent["steps"][-2]), (14, 4321, None))
        self.assertEqual((recent["asleep"][-1], recent["resting"][-1]), (420, 54))
        self.assertEqual((recent["weightKg"][-8], recent["weightKg"][-1]), (73.1, 72.6))

    def test_before_tonights_sleep_lands_the_night_before_shows(self):
        self.put("xrnfit-watch", {"id": "z", "kind": "sleep", "start": ms(2026, 11, 4, 23), "end": ms(2026, 11, 5, 6)},
                 {"id": "r", "kind": "resting_heart_rate", "start": ms(2026, 11, 2, 6), "value": 57})
        body = self.body()
        self.assertEqual((body["sleep"]["day"], body["steps"]), ("2026-11-05", None))
        self.assertEqual(body["resting"], {"day": "2026-11-02", "bpm": 57})  # the newest, however old

    def test_after_midnight_the_card_is_still_the_pages_day(self):
        # At 01:30 the page is still the 6th, so the card is the 6th's calendar day; the 7th's first
        # steps wait for the morning, when the page turns over.
        self.put("xrnfit-watch",
                 {"id": "a", "kind": "steps", "start": ms(2026, 11, 6, 20), "end": ms(2026, 11, 6, 21), "value": 9000},
                 {"id": "b", "kind": "steps", "start": ms(2026, 11, 7, 0, 30), "end": ms(2026, 11, 7, 1), "value": 40})
        make_loops(self.home, [habit("a", "Gym")], [])
        self.assertEqual(self.body(datetime(2026, 11, 7, 1, 30))["steps"], 9000)
        self.assertEqual(self.body(datetime(2026, 11, 7, 8))["steps"], 40)

    def test_a_weight_change_is_against_the_weighing_nearest_a_week_back(self):
        self.put("xrnfit-scale",
                 {"id": "a", "kind": "weight", "start": ms(2026, 10, 20, 7), "value": 75},  # 17 days: too far
                 {"id": "b", "kind": "weight", "start": ms(2026, 10, 28, 7), "value": 73.4},  # 9 days
                 {"id": "c", "kind": "weight", "start": ms(2026, 10, 31, 7), "value": 73.2},  # 6 days: nearest 7
                 {"id": "d", "kind": "weight", "start": ms(2026, 11, 4, 7), "value": 72.9},  # 2 days: too near
                 {"id": "e", "kind": "weight", "start": ms(2026, 11, 6, 7), "value": 72.95})
        weight = self.body()["weight"]
        self.assertEqual((weight["change"], weight["since"]), (-0.2, ms(2026, 10, 31, 7)))

    def test_no_weighing_a_week_back_is_no_change(self):
        self.put("xrnfit-scale", {"id": "a", "kind": "weight", "start": ms(2026, 10, 1, 7), "value": 75},
                 {"id": "b", "kind": "weight", "start": ms(2026, 11, 6, 7), "value": 72})
        self.assertEqual(self.body()["weight"], {"at": ms(2026, 11, 6, 7), "kg": 72, "change": None, "since": None})


if __name__ == "__main__":
    unittest.main()

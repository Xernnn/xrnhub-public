import json
import shutil
import tempfile
import unittest
from datetime import date, datetime

import support  # noqa: F401 - puts server/ on the path
from support import USER, habit, ics, make_calendar, make_loops, make_rule, make_wallet, ms
from hublib import daybook, health, review
from hublib.log import HubLog
from hublib.sources import Sources

NOW = datetime(2026, 11, 6, 15, 0)


class Review(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.home)

    def src(self):
        src = Sources(self.home, USER)
        src.calendar.sweep()
        return src

    def build(self, now=NOW, log=None):
        src = self.src()
        return review.build(src, now, daybook.day_table(src, now), log.db(USER) if log else None)

    def health(self, source, *records):
        log = HubLog(self.home + "/hub")
        health.write(log, USER, {"source": source, "upsert": list(records)}, now_ms=ms(2026, 11, 7, 12))
        return log

    def test_the_year_keeps_logged_days_and_today(self):
        make_loops(self.home, [habit("a", "A"), habit("b", "B")],
                   [("a", "2026-11-04", 2), ("b", "2026-11-04", 2), ("a", "2026-11-06", 2)])
        year = {d["day"]: d for d in self.build()["year"]}
        self.assertEqual(year["2026-11-04"]["share"], 1)
        self.assertNotIn("2026-11-05", year)
        self.assertEqual(year["2026-11-06"]["share"], 0.5)

    def test_a_thin_day_still_shows_though_it_does_not_count(self):
        habits = [habit(f"h{i}", f"H{i}") for i in range(9)]
        full = [(f"h{i}", "2026-11-01", 2) for i in range(9)]
        make_loops(self.home, habits, full + [("h0", "2026-11-03", 2), ("h1", "2026-11-03", 2)])
        doc = self.build()
        year = {d["day"]: d for d in doc["year"]}
        self.assertEqual(year["2026-11-03"]["share"], 0.222)
        self.assertEqual(doc["loggedDays"], 2)
        rows = daybook.day_table(self.src(), NOW)
        self.assertEqual({r["day"].isoformat(): r for r in rows}["2026-11-03"]["habits"], {})

    def test_habits_are_weakest_first_over_logged_days(self):
        make_loops(self.home, [habit("a", "Strong"), habit("b", "Weak")],
                   [("a", "2026-11-04", 2), ("a", "2026-11-05", 2), ("b", "2026-11-05", 0),
                    ("b", "2026-11-04", 3)])
        items = self.build()["habits"]["items"]
        self.assertEqual([(r["name"], r["pct"]) for r in items], [("Weak", 0), ("Strong", 100)])
        self.assertEqual(items[1]["streak"], 2)
        self.assertEqual(items[0]["strip"][-3:], [2, 0, None])

    def test_the_longest_run_passes_through_a_skip_and_breaks_on_a_gap(self):
        h = {"type": 0, "targetType": 0, "targetValue": 0}
        per_day = {"2026-11-01": 2, "2026-11-02": 3, "2026-11-03": 2, "2026-11-05": 2}
        self.assertEqual(review.best_run(h, per_day), 2)

    def test_hours_add_up_by_week_and_leave_all_day_out(self):
        make_calendar(self.home, "w", [
            ics("UID:1", "DTSTART:20261102T090000", "DTEND:20261102T113000", "SUMMARY:A"),
            ics("UID:2", "DTSTART:20261103T090000", "DTEND:20261103T100000", "SUMMARY:B"),
            ics("UID:3", "DTSTART:20261026T090000", "DTEND:20261026T100000", "SUMMARY:C"),
            ics("UID:4", "DTSTART:20261106T200000", "DTEND:20261106T210000", "SUMMARY:Not yet"),
            ics("UID:5", "DTSTART;VALUE=DATE:20261104", "DTEND;VALUE=DATE:20261105", "SUMMARY:All day"),
        ], display="Work")
        hours = self.build()["hours"]
        self.assertEqual(hours["weeks"][-1], "2026-11-02")
        self.assertEqual(hours["series"][0]["name"], "Work")
        self.assertEqual(hours["series"][0]["hours"][-2:], [1.0, 3.5])

    def test_a_seventh_calendar_folds_into_other(self):
        for i in range(8):
            make_calendar(self.home, f"c{i}", [
                ics(f"UID:{i}", "DTSTART:20261102T090000", f"DTEND:20261102T1{i}0000", "SUMMARY:X"),
            ], display=f"C{i}")
        series = self.build()["hours"]["series"]
        self.assertEqual(len(series), 7)
        self.assertEqual(series[-1]["name"], "Other")
        self.assertIsNone(series[-1]["color"])

    def test_temptations_rank_the_apps_and_fold_by_hour(self):
        make_rule(self.home, {}, [
            {"t": "blocked", "at": ms(2026, 11, 5, 22), "name": "Game"},
            {"t": "blocked", "at": ms(2026, 11, 5, 22, 30), "name": "Game"},
            {"t": "blocked", "at": ms(2026, 10, 25, 9), "name": "Video"},
            {"t": "habit", "at": ms(2026, 11, 5, 22)},
        ])
        found = self.build()["temptations"]
        self.assertEqual(found["apps"][0], {"name": "Game", "count": 2})
        self.assertEqual((found["total"], found["thisWeek"], found["lastWeek"]), (3, 2, 1))
        self.assertEqual((found["hours"][22], found["hours"][9]), (2, 1))
        self.assertEqual(found["weeks"][-2:], [1, 2])
        self.assertEqual(len(found["weeks"]), review.TREND_WEEKS)

    def test_habits_a_week_for_the_trend(self):
        days = [date(2026, 10, 1 + i).isoformat() for i in range(31)] + [date(2026, 11, 1 + i).isoformat() for i in range(5)]
        make_loops(self.home, [habit("a", "A"), habit("b", "B")],
                   [("a", d, 2) for d in days] + [("b", d, 2 if d >= "2026-10-30" else 0) for d in days])
        weeks = self.build()["habitWeeks"]
        self.assertEqual(len(weeks), review.TREND_WEEKS)
        self.assertEqual(weeks[-2:], [50, 100])
        self.assertIsNone(weeks[0])

    def test_money_a_month_leaves_out_what_the_wallet_leaves_out_of_the_budget(self):
        categories = [{"id": 1, "name": "Food"}, {"id": 2, "name": "Cafe", "parentId": 1}, {"id": 3, "name": "Rent"}]
        bill = {"type": 0, "rate": 1.0, "categoryId": 1}
        make_wallet(self.home, {
            "settings": json.dumps({"startDay": 1}),
            "categories": categories,
            "budgets": [{"categoryId": None, "amountCents": 50000}],
            "bills": [
                {**bill, "amountCents": 120000, "atMillis": ms(2026, 11, 1, 9), "categoryId": 3, "inBudget": False},
                {**bill, "amountCents": 1500, "atMillis": ms(2026, 11, 2, 9)},
                {**bill, "amountCents": 500, "atMillis": ms(2026, 11, 3, 9), "categoryId": 2},
                {**bill, "amountCents": 7000, "atMillis": ms(2026, 10, 9, 9)},
                {"type": 1, "rate": 1.0, "amountCents": 300000, "atMillis": ms(2026, 10, 15, 9)},
                {"type": 2, "rate": 1.0, "amountCents": 9999, "atMillis": ms(2026, 11, 2, 9)},
                {**bill, "amountCents": 9999, "atMillis": ms(2025, 10, 9, 9)},
            ],
        })
        found = self.build()["money"]
        months = found["months"]
        self.assertEqual(len(months), review.MONEY_MONTHS)
        self.assertEqual((months[-1]["first"], months[-1]["last"]), ("2026-11-01", "2026-11-30"))
        self.assertEqual((months[0]["first"], months[0]["spentCents"]), ("2025-12-01", 0))
        self.assertEqual((months[-1]["spentCents"], months[-1]["outsideCents"]), (2000, 120000))
        self.assertEqual((months[-2]["spentCents"], months[-2]["earnedCents"]), (7000, 300000))
        self.assertEqual(found["budgetCents"], 50000)
        self.assertEqual(found["categories"], [{"name": "Food", "cents": 2000}])

    def test_a_blocked_open_with_an_impossible_time_is_left_out(self):
        make_rule(self.home, {}, [
            {"t": "blocked", "at": ms(2026, 11, 5, 22), "name": "Game"},
            {"t": "blocked", "at": 10**20, "name": "Game"},
        ])
        self.assertEqual(self.build()["temptations"]["total"], 1)

    def test_nothing_set_up_still_builds(self):
        doc = self.build()
        self.assertEqual(doc["year"], [])
        self.assertEqual(doc["habits"]["items"], [])
        self.assertEqual(doc["day"], date(2026, 11, 6).isoformat())
        self.assertIsNone(doc["health"])
        self.assertIsNone(doc["money"])
        self.assertEqual(doc["habitWeeks"], [None] * review.TREND_WEEKS)

    def test_the_body_runs_over_the_habit_strips_days(self):
        night = ms(2026, 11, 5, 23)
        log = self.health(
            "xrnfit-watch",
            {"id": "s1", "kind": "steps", "start": ms(2026, 11, 4, 9), "end": ms(2026, 11, 4, 10), "value": 9000},
            {"id": "s2", "kind": "steps", "start": ms(2026, 11, 5, 9), "end": ms(2026, 11, 5, 10), "value": 7000},
            {"id": "s3", "kind": "steps", "start": ms(2026, 11, 6, 9), "end": ms(2026, 11, 6, 10), "value": 1200},
            {"id": "z", "kind": "sleep", "start": night, "end": night + 7 * 3_600_000,
             "stages": [[night, night + 3_600_000, "awake"], [night + 3_600_000, night + 7 * 3_600_000, "deep"]]},
            {"id": "r", "kind": "resting_heart_rate", "start": ms(2026, 11, 6, 6), "value": 54},
            {"id": "o", "kind": "spo2", "samples": [[ms(2026, 11, 6, 3), 95], [ms(2026, 11, 6, 4), 97]]},
            {"id": "w", "kind": "weight", "start": ms(2026, 10, 20, 7), "value": 72.25},
        )
        doc = self.build(log=log)
        habits, body = doc["habits"], doc["health"]
        self.assertEqual(len(body["days"]), 30)
        self.assertEqual(body["first"], habits["first"])  # each column the same day as the strips above
        today = body["days"][-1]
        self.assertEqual(today, {"date": "2026-11-06", "steps": 1200, "asleep": 360, "resting": 54, "weightKg": None,
                                 "spo2": 96, "stress": None})
        summary = body["summary"]
        self.assertEqual(summary["steps"], {"avg": 8000, "latest": 1200, "day": "2026-11-06"})  # today's isn't in yet
        self.assertEqual(summary["weightKg"], {"avg": 72.3, "latest": 72.25, "day": "2026-10-20"})
        self.assertEqual(summary["asleep"]["avg"], 360)
        self.assertNotIn("stress", summary)  # a measure nothing came for gets no row

    def test_after_midnight_the_body_still_ends_on_the_pages_day(self):
        # At 01:30 it's still the 6th's habit day, and the 6th's health is the 6th's calendar day:
        # the steps just after midnight belong to the 7th, which the page hasn't reached.
        log = self.health(
            "xrnfit-watch",
            {"id": "a", "kind": "steps", "start": ms(2026, 11, 6, 22), "end": ms(2026, 11, 6, 23), "value": 500},
            {"id": "b", "kind": "steps", "start": ms(2026, 11, 7, 0, 30), "end": ms(2026, 11, 7, 1), "value": 80},
        )
        make_loops(self.home, [habit("a", "A")], [])
        body = self.build(now=datetime(2026, 11, 7, 1, 30), log=log)["health"]
        self.assertEqual((body["days"][-1]["date"], body["days"][-1]["steps"]), ("2026-11-06", 500))

    def test_nothing_measured_this_month_is_no_body(self):
        log = self.health("xrnfit-scale", {"id": "w", "kind": "weight", "start": ms(2026, 6, 1, 7), "value": 70})
        self.assertIsNone(self.build(log=log)["health"])


if __name__ == "__main__":
    unittest.main()

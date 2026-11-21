import shutil
import tempfile
import unittest
from datetime import datetime, timedelta

import support  # noqa: F401 - puts server/ on the path
from hublib import rules
from hublib.log import BadEvent, HubLog, clean_event

NOW = datetime(2026, 11, 6, 21, 0)


def facts(**over):
    base = {
        "now": NOW, "habits_done": 2, "habits_total": 5, "events": [], "spent_day": 0, "spent_month": 0,
        "shopping": 0, "expiring": [], "hits": 0,
    }
    return {**base, **over}


def rule(*when, on=True):
    return {"id": "r", "name": "R", "on": on, "when": list(when)}


def event(title, start, end=None, all_day=False):
    return {"title": title, "start": start, "end": end or start + timedelta(hours=1), "allDay": all_day}


class Evaluating(unittest.TestCase):
    def fires(self, r, f):
        return rules.evaluate(r, f)[0]

    def test_event_soon(self):
        r = rule({"is": "event_soon", "match": "exam", "hours": 48})
        self.assertTrue(self.fires(r, facts(events=[event("MLA Exam", NOW + timedelta(hours=20))])))
        self.assertFalse(self.fires(r, facts(events=[event("MLA Exam", NOW + timedelta(hours=60))])))
        self.assertFalse(self.fires(r, facts(events=[event("Dinner", NOW + timedelta(hours=2))])))

    def test_an_all_day_exam_today_counts_as_soon(self):
        day = datetime(2026, 11, 6)
        r = rule({"is": "event_soon", "match": "exam", "hours": 1})
        self.assertTrue(self.fires(r, facts(events=[event("Exam", day, day + timedelta(days=1), True)])))

    def test_a_finished_event_is_not_soon(self):
        r = rule({"is": "event_soon", "match": "exam", "hours": 48})
        self.assertFalse(self.fires(r, facts(events=[event("Exam", NOW - timedelta(hours=3))])))

    def test_event_now(self):
        r = rule({"is": "event_now", "match": "class"})
        self.assertTrue(self.fires(r, facts(events=[event("Class", NOW - timedelta(minutes=5))])))
        self.assertFalse(self.fires(r, facts(events=[event("Class", NOW + timedelta(minutes=5))])))

    def test_habits_open_waits_for_its_time(self):
        r = rule({"is": "habits_open", "after": "20:00", "atLeast": 3})
        self.assertTrue(self.fires(r, facts()))
        self.assertFalse(self.fires(r, facts(now=NOW.replace(hour=19))))
        self.assertFalse(self.fires(r, facts(habits_done=4)))

    def test_habits_done_needs_some_habits(self):
        r = rule({"is": "habits_done", "atLeast": 100})
        self.assertTrue(self.fires(r, facts(habits_done=5)))
        self.assertFalse(self.fires(r, facts(habits_done=0, habits_total=0)))

    def test_spent_over(self):
        r = rule({"is": "spent_over", "amount": 100, "per": "day"})
        self.assertFalse(self.fires(r, facts(spent_day=100)))
        self.assertTrue(self.fires(r, facts(spent_day=100.5)))

    def test_all_conditions_must_hold(self):
        r = rule({"is": "after", "time": "20:00"}, {"is": "shopping_over", "count": 5})
        self.assertFalse(self.fires(r, facts()))
        self.assertTrue(self.fires(r, facts(shopping=5)))

    def test_a_rule_turned_off_never_fires(self):
        self.assertFalse(self.fires(rule({"is": "after", "time": "00:00"}, on=False), facts()))


class Nudges(unittest.TestCase):
    def test_a_nudge_stays_while_it_holds_and_can_be_dismissed(self):
        r = rule({"is": "shopping_over", "count": 5})
        self.assertEqual(rules.live([r], facts(shopping=6), set()), [{"id": "r", "name": "R", "why": ["6 to buy"]}])
        self.assertEqual(rules.live([r], facts(shopping=2), set()), [])
        self.assertEqual(rules.live([r], facts(shopping=6), {"r"}), [])

    def test_a_calendar_name_counts_as_a_match(self):
        r = rule({"is": "event_soon", "match": "deadline", "hours": 48})
        when = facts()["now"] + timedelta(hours=5)
        ev = {"title": "Essay", "calendar": "Deadlines", "allDay": False, "start": when, "end": when}
        self.assertTrue(self.fires(r, facts(events=[ev])))

    def fires(self, r, f):
        return rules.evaluate(r, f)[0]


class OldDatabase(unittest.TestCase):
    def test_a_database_from_before_dismissing_still_opens(self):
        import os
        import sqlite3

        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder)
        path = os.path.join(folder, "users", "u")
        os.makedirs(path)
        db = sqlite3.connect(os.path.join(path, "hub.sqlite"))
        db.execute("""CREATE TABLE fired(rule TEXT NOT NULL, day TEXT NOT NULL, name TEXT NOT NULL, at INTEGER NOT NULL,
                      why TEXT NOT NULL, action TEXT NOT NULL, PRIMARY KEY(rule, day))""")
        db.execute("INSERT INTO fired VALUES('r', '2026-11-06', 'R', 1, '[]', 'strict_blocks')")
        db.commit()
        db.close()
        log = HubLog(folder)
        self.assertEqual(log.dismissed("u", "2026-11-06"), set())
        log.dismiss("u", "r", "2026-11-06")
        self.assertEqual(log.dismissed("u", "2026-11-06"), {"r"})
        self.assertEqual(log.db("u").execute("SELECT name FROM fired").fetchall(), [("R",)])


class Saving(unittest.TestCase):
    def test_starters_are_valid(self):
        self.assertEqual(len(rules.clean_rules(rules.starter_rules())), 6)

    def test_a_saved_rule_from_the_watching_days_still_loads(self):
        old = {**rule({"is": "after", "time": "20:00"}), "then": "strict_blocks"}
        self.assertNotIn("then", rules.clean_rules([old])[0])

    def test_unknown_fields_drop(self):
        out = rules.clean_rules([{**rule({"is": "after", "time": "20:00", "extra": 1}), "script": "x"}])
        self.assertEqual(out[0]["when"], [{"is": "after", "time": "20:00"}])
        self.assertNotIn("script", out[0])

    def bad(self, *conds, **over):
        with self.assertRaises(rules.BadRule):
            rules.clean_rules([{**rule(*conds), **over}])

    def test_refused(self):
        ok = {"is": "after", "time": "20:00"}
        self.bad({"is": "nonsense"})
        self.bad({"is": "after", "time": "25:00"})
        self.bad({"is": "event_soon", "match": "", "hours": 1})
        self.bad({"is": "event_soon", "match": "x", "hours": True})
        self.bad({"is": "spent_over", "amount": 1, "per": "year"})
        self.bad(ok, id="Bad Id!")
        self.bad(ok, name="")
        self.bad()

    def test_duplicate_ids_refused(self):
        with self.assertRaises(rules.BadRule):
            rules.clean_rules([rule({"is": "after", "time": "20:00"})] * 2)


class Log(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix="xrnhub-log-")
        self.addCleanup(shutil.rmtree, self.folder)
        self.log = HubLog(self.folder)

    def test_fires_once_a_day(self):
        r = rule({"is": "after", "time": "20:00"})
        self.assertEqual(rules.run_due([r], facts(), "2026-11-06", self.log, "u"), ["r"])
        self.assertEqual(rules.run_due([r], facts(), "2026-11-06", self.log, "u"), [])
        self.assertEqual(rules.run_due([r], facts(), "2026-11-07", self.log, "u"), ["r"])
        self.assertEqual(self.log.db("u").execute("SELECT COUNT(*) FROM fired").fetchone()[0], 2)

    def test_events_round_trip(self):
        self.log.add("u", [{"app": "xrnmail", "kind": "batch", "at": 1000, "value": 3, "detail": {"held": 2}}])
        self.assertEqual(self.log.latest("u", "xrnmail")["detail"], {"held": 2})
        self.assertEqual(self.log.events("u", 0)[0]["app"], "xrnmail")

    def test_bad_events_refused(self):
        good = {"app": "xrnmail", "kind": "batch", "at": 1000}
        clean_event(good)
        for over in ({"app": "other"}, {"kind": "Has Space"}, {"at": True}, {"at": -1}, {"title": "x" * 500},
                     {"value": "many"}, {"detail": ["list"]}, {"detail": {"x": "y" * 3000}}):
            with self.assertRaises(BadEvent):
                clean_event({**good, **over})


if __name__ == "__main__":
    unittest.main()

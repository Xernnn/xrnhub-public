"""Health records: what's taken, how a resend and a delete land, and how days add up. All data is made up."""
import os
import shutil
import sqlite3
import tempfile
import time
import unittest
from datetime import date

from support import USER, ms

from hublib import daybook, health
from hublib.health import BadHealth
from hublib.log import HubLog

NOW = ms(2026, 3, 10, 14)


def steps(rid, start, end, value, kind="steps"):
    """A steps record, or with [kind] another amount over a stretch: distance or active energy."""
    return {"id": rid, "kind": kind, "start": start, "end": end, "value": value}


def beats(rid, *samples):
    return {"id": rid, "kind": "heart_rate", "samples": [list(s) for s in samples]}


def sleep(rid, start, end, *stages):
    rec = {"id": rid, "kind": "sleep", "start": start, "end": end}
    if stages:
        rec["stages"] = [list(s) for s in stages]
    return rec


def run(rid, kind, *samples):
    """An SpO2 or stress record: a run of samples, sent as heart rate is."""
    return {"id": rid, "kind": kind, "samples": [list(s) for s in samples]}


def weight(rid, at, kg, impedance=None):
    rec = {"id": rid, "kind": "weight", "start": at, "value": kg}
    if impedance is not None:
        rec["impedance"] = impedance
    return rec


class Store(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix="xrnhub-health-")
        self.addCleanup(shutil.rmtree, self.folder)
        self.log = HubLog(self.folder)

    def put(self, source="gadgetbridge", upsert=None, delete=None):
        doc = {"source": source}
        if upsert is not None:
            doc["upsert"] = upsert
        if delete is not None:
            doc["delete"] = delete
        return health.write(self.log, USER, doc, now_ms=NOW)

    @property
    def db(self):
        return self.log.db(USER)

    def rows(self, kind=None):
        sql = "SELECT source, rid, kind, start, value FROM health"
        found = self.db.execute(sql + (" WHERE kind = ?" if kind else "") + " ORDER BY start", (kind,) if kind else ())
        return found.fetchall()

    def day(self, date):
        return health.days(self.db, date, date).get(date)


class Taking(Store):
    def test_every_kind_is_kept_and_counted_as_records(self):
        got = self.put(upsert=[
            steps("s1", ms(2026, 3, 10, 9), ms(2026, 3, 10, 10), 1200),
            beats("h1", (ms(2026, 3, 10, 9), 70), (ms(2026, 3, 10, 9, 1), 72), (ms(2026, 3, 10, 9, 2), 75)),
            {"id": "r1", "kind": "resting_heart_rate", "start": ms(2026, 3, 10, 8), "value": 56},
            sleep("z1", ms(2026, 3, 9, 23), ms(2026, 3, 10, 7)),
            weight("w1", ms(2026, 3, 10, 7, 5), 72.35, impedance=512),
        ])
        self.assertEqual(got, (5, 0))
        self.assertEqual(len(self.rows("heart_rate")), 3)  # a sample a row
        self.assertEqual(health.held(self.db), {"steps": 1, "heartRate": 3, "sleep": 1, "weight": 1, "spo2": 0,
                                                "stress": 0, "distance": 0, "activeEnergy": 0,
                                                "first": "2026-03-10", "last": "2026-03-10"})

    def test_distance_and_active_energy_are_amounts_over_a_stretch_like_steps(self):
        # The shape xrnfit sends from the watch's minutes: metres walked and kcal burnt moving.
        self.put("xrnfit-watch", upsert=[
            steps("d1", ms(2026, 3, 10, 9), ms(2026, 3, 10, 9, 1), 81.5, kind="distance"),
            steps("d2", ms(2026, 3, 10, 9, 1), ms(2026, 3, 10, 9, 2), 76, kind="distance"),
            steps("e1", ms(2026, 3, 10, 9), ms(2026, 3, 10, 9, 1), 4.6, kind="active_energy"),
        ])
        self.assertEqual([r[3:] for r in self.rows("distance")],
                         [(ms(2026, 3, 10, 9), 81.5), (ms(2026, 3, 10, 9, 1), 76.0)])
        self.put("xrnfit-watch", upsert=[steps("d1", ms(2026, 3, 10, 9), ms(2026, 3, 10, 9, 1), 90, kind="distance")])
        day = health.days(self.db, "2026-03-10", "2026-03-10")["2026-03-10"]
        self.assertEqual((day["distance"], day["activeEnergy"], day["steps"]), (166, 5, None))
        held = health.held(self.db)
        self.assertEqual((held["distance"], held["activeEnergy"]), (2, 1))

    def test_steps_come_in_buckets_counted_from_midnight(self):
        # What xrnfit's Day bars and active minutes ask for: the watch's minutes, by the minute or
        # the half hour.
        self.put("xrnfit-watch", upsert=[
            steps("m1", ms(2026, 3, 10, 9), ms(2026, 3, 10, 9, 1), 40),
            steps("m2", ms(2026, 3, 10, 9, 1), ms(2026, 3, 10, 9, 2), 60),
            steps("m3", ms(2026, 3, 10, 9, 31), ms(2026, 3, 10, 9, 32), 10),
        ])
        self.assertEqual(health.step_buckets(self.db, "2026-03-10", "2026-03-10", 30),
                         [(ms(2026, 3, 10, 9), 100), (ms(2026, 3, 10, 9, 30), 10)])
        self.assertEqual(health.step_buckets(self.db, "2026-03-10", "2026-03-10", 1),
                         [(ms(2026, 3, 10, 9), 40), (ms(2026, 3, 10, 9, 1), 60), (ms(2026, 3, 10, 9, 31), 10)])
        self.assertEqual(health.step_buckets(self.db, "2026-03-10", "2026-03-10", 1440), [(ms(2026, 3, 10), 110)])
        self.assertEqual(health.step_buckets(self.db, "2026-03-11", "2026-03-11", 30), [])

    def test_a_long_steps_record_is_shared_over_the_buckets_it_covers(self):
        # Google Fit's export has hours: all 600 in the hour's first minute would be a sprint.
        self.put("import-googlefit", upsert=[steps("h", ms(2026, 3, 10, 10), ms(2026, 3, 10, 11), 600)])
        self.assertEqual(health.step_buckets(self.db, "2026-03-10", "2026-03-10", 30),
                         [(ms(2026, 3, 10, 10), 300), (ms(2026, 3, 10, 10, 30), 300)])
        self.assertEqual(len(health.step_buckets(self.db, "2026-03-10", "2026-03-10", 1)), 60)

    def test_a_days_buckets_add_up_to_its_total(self):
        self.put("import-googlefit", upsert=[steps("h", ms(2026, 3, 10, 10), ms(2026, 3, 10, 11), 100)])
        thirds = health.step_buckets(self.db, "2026-03-10", "2026-03-10", 20)
        self.assertEqual([n for _, n in thirds], [33, 34, 33])
        self.assertEqual(sum(n for _, n in thirds), health.days(self.db, "2026-03-10", "2026-03-10")["2026-03-10"]["steps"])

    def test_buckets_take_the_source_that_counts_each_day(self):
        self.put("com.android.healthconnect.phone", upsert=[
            steps("p1", ms(2026, 3, 10, 9), ms(2026, 3, 10, 9, 1), 70),
            steps("p2", ms(2026, 3, 11, 9), ms(2026, 3, 11, 9, 1), 80),
        ])
        self.put("xrnfit-watch", upsert=[steps("w1", ms(2026, 3, 10, 9), ms(2026, 3, 10, 9, 1), 50)])
        self.assertEqual(health.step_buckets(self.db, "2026-03-10", "2026-03-11", 60),
                         [(ms(2026, 3, 10, 9), 50), (ms(2026, 3, 11, 9), 80)])

    def test_a_days_distance_comes_from_one_source_as_its_steps_do(self):
        # The watch and the phone both count metres; adding them would walk the day twice.
        at = ms(2026, 3, 10, 9)
        self.put("com.android.healthconnect.phone", upsert=[steps("p", at, at + 3_600_000, 4000, kind="distance")])
        self.put("xrnfit-watch", upsert=[steps("w", at, at + 3_600_000, 3500, kind="distance")])
        self.assertEqual(health.days(self.db, "2026-03-10", "2026-03-10")["2026-03-10"]["distance"], 3500)

    def test_spo2_and_stress_are_runs_of_samples_kept_a_row_each(self):
        # The shape xrnfit sends: heart rate's, with a percentage or the watch's stress score.
        got = self.put("xrnfit-watch", upsert=[
            run("o1", "spo2", (ms(2026, 3, 10, 3), 96), (ms(2026, 3, 10, 4), 94.5)),
            run("t1", "stress", (ms(2026, 3, 10, 9), 31), (ms(2026, 3, 10, 9, 5), 44), (ms(2026, 3, 10, 9, 10), 12)),
        ])
        self.assertEqual(got, (2, 0))
        self.assertEqual([r[3:] for r in self.rows("spo2")], [(ms(2026, 3, 10, 3), 96.0), (ms(2026, 3, 10, 4), 94.5)])
        self.assertEqual(len(self.rows("stress")), 3)
        held = health.held(self.db)
        self.assertEqual((held["spo2"], held["stress"]), (2, 3))
        self.put("xrnfit-watch", upsert=[run("t1", "stress", (ms(2026, 3, 10, 9), 20))])  # a resend replaces
        self.assertEqual([r[3:] for r in self.rows("stress")], [(ms(2026, 3, 10, 9), 20.0)])
        self.assertEqual(self.put("xrnfit-watch", delete=["o1"]), (0, 1))
        self.assertEqual(self.rows("spo2"), [])

    def test_a_resend_replaces_the_record_whole(self):
        self.put(upsert=[beats("h1", (ms(2026, 3, 10, 9), 70), (ms(2026, 3, 10, 9, 1), 72))])
        self.put(upsert=[beats("h1", (ms(2026, 3, 10, 9, 5), 80))])
        self.assertEqual([r[3:] for r in self.rows()], [(ms(2026, 3, 10, 9, 5), 80.0)])
        self.put(upsert=[steps("s1", ms(2026, 3, 10, 9), ms(2026, 3, 10, 10), 10)] * 2)
        self.assertEqual(len(self.rows("steps")), 1)

    def test_the_same_id_from_another_source_is_another_record(self):
        self.put(upsert=[steps("s1", ms(2026, 3, 10, 9), ms(2026, 3, 10, 10), 10)])
        self.put("import-mifitness", upsert=[steps("s1", ms(2026, 3, 10, 9), ms(2026, 3, 10, 10), 20)])
        self.assertEqual(len(self.rows("steps")), 2)

    def test_a_delete_takes_every_row_of_its_record(self):
        self.put(upsert=[beats("h1", (ms(2026, 3, 10, 9), 70), (ms(2026, 3, 10, 9, 1), 72)),
                         weight("w1", ms(2026, 3, 10, 7), 70)])
        self.assertEqual(self.put(delete=["h1", "nothing-here"]), (0, 1))
        self.assertEqual([r[2] for r in self.rows()], ["weight"])
        self.assertEqual(self.put("someone-else", delete=["w1"]), (0, 0))  # only its own source's
        self.assertEqual(len(self.rows()), 1)

    def test_an_id_both_sent_and_deleted_ends_up_gone(self):
        self.assertEqual(self.put(upsert=[weight("w1", ms(2026, 3, 10, 7), 70)], delete=["w1"]), (1, 1))
        self.assertEqual(self.rows(), [])

    def test_the_store_survives_pruning(self):
        self.put(upsert=[weight("w1", ms(2001, 1, 1, 7), 70)])
        self.log.prune(USER)
        self.assertEqual(len(self.rows()), 1)

    def test_each_reading_says_when_it_came(self):
        self.put(upsert=[weight("w1", ms(2026, 3, 10, 7), 70)])
        self.assertEqual(health.size(self.db), (1, NOW))
        self.assertEqual(health.received_by_day(self.db, NOW - 1), {NOW: 1})
        self.assertEqual(health.received_by_day(self.db, NOW + 1), {})

    def test_what_the_records_take_on_disk(self):
        empty = health.stored_bytes(self.db)  # a page for the table and each index
        self.put(upsert=[beats("h", *[(ms(2026, 3, 10, 9) + i * 60_000, 70) for i in range(500)])])
        self.assertGreater(health.stored_bytes(self.db), empty + 8192)

        class NoDbstat:  # SQLite built without it
            def execute(self, *_):
                raise sqlite3.OperationalError("no such table: dbstat")

        self.assertIsNone(health.stored_bytes(NoDbstat()))


class Checking(Store):
    def assertRefused(self, doc, message=None):
        with self.assertRaises(BadHealth) as caught:
            health.write(self.log, USER, doc, now_ms=NOW)
        if message:
            self.assertIn(message, str(caught.exception))

    def test_bad_records_are_refused(self):
        at = ms(2026, 3, 10, 9)
        good = {"steps": steps("s", at, at + 60_000, 5), "beats": beats("h", (at, 70)),
                "sleep": sleep("z", at, at + 3_600_000), "weight": weight("w", at, 70),
                "spo2": run("o", "spo2", (at, 50), (at + 1, 100)), "stress": run("t", "stress", (at, 1), (at + 1, 100)),
                "distance": steps("d", at, at + 60_000, 0, kind="distance"),
                "energy": steps("e", at, at + health.DAY_MS, 10_000, kind="active_energy")}
        for name, bad in [
            ("steps", {"value": 100_001}), ("steps", {"value": -1}), ("steps", {"value": True}),
            ("steps", {"value": float("inf")}), ("steps", {"value": float("nan")}), ("steps", {"value": "5"}),
            ("steps", {"end": at}), ("steps", {"end": at + health.DAY_MS + 1}), ("steps", {"start": 1.5e12}),
            ("steps", {"start": health.EARLIEST_MS - 1}), ("steps", {"end": NOW + health.DAY_MS + 1}),
            ("steps", {"id": ""}), ("steps", {"id": "a/b"}), ("steps", {"id": "x" * 101}), ("steps", {"kind": "mood"}),
            ("beats", {"samples": []}), ("beats", {"samples": [[at, 300]]}), ("beats", {"samples": [[at, 19]]}),
            ("beats", {"samples": [[at]]}), ("beats", {"samples": [[True, 70]]}),
            ("beats", {"samples": [[at, 70]] * (health.MAX_SAMPLES + 1)}),
            ("sleep", {"end": at + health.DAY_MS + 1}), ("sleep", {"stages": [[at, at + 60_000, "dreaming"]]}),
            ("sleep", {"stages": [[at - 1, at + 60_000, "deep"]]}), ("sleep", {"stages": [[at + 60_000, at + 60_000, "deep"]]}),
            ("sleep", {"stages": [[at, at + 1, "deep"]] * (health.MAX_STAGES + 1)}), ("sleep", {"stages": "deep"}),
            ("weight", {"value": 1.9}), ("weight", {"value": 301}), ("weight", {"impedance": 5001}),
            ("weight", {"impedance": float("nan")}), ("weight", {"impedance": -1}),
            ("weight", {"body": {}}), ("weight", {"body": [13.6]}), ("weight", {"body": {"fat": 76}}),
            ("weight", {"body": {"fat": True}}), ("weight", {"body": {"mood": 3}}), ("weight", {"body": {"bmr": float("inf")}}),
            ("spo2", {"samples": [[at, 49]]}), ("spo2", {"samples": [[at, 101]]}), ("spo2", {"samples": [[at, "96"]]}),
            ("spo2", {"samples": []}), ("spo2", {"samples": [[at, 96]] * (health.MAX_SAMPLES + 1)}),
            ("spo2", {"samples": [[1.5e12, 96]]}), ("stress", {"samples": [[at, 0]]}),
            ("stress", {"samples": [[at, 101]]}), ("stress", {"samples": [[at, True]]}), ("stress", {"samples": [[at]]}),
            ("distance", {"value": 200_001}), ("distance", {"value": -1}), ("distance", {"value": "80"}),
            ("distance", {"end": at}), ("distance", {"end": at + health.DAY_MS + 1}),
            ("energy", {"value": 10_001}), ("energy", {"value": float("nan")}), ("energy", {"start": None}),
        ]:
            with self.subTest(name=name, bad=bad):
                self.assertRefused({"source": "gadgetbridge", "upsert": [{**good[name], **bad}]})
        for name in good:  # and each is fine as it was
            health.write(self.log, USER, {"source": "gadgetbridge", "upsert": [good[name]]}, now_ms=NOW)

    def test_a_weighing_keeps_the_scale_apps_own_figures(self):
        body = {"fat": 21.4, "musclePercent": 74.5, "waterPercent": 55.1, "visceral": 9, "bmr": 1712, "metabolicAge": 34}
        rec = {**weight("r1", ms(2026, 3, 10, 7, 5), 79.15), "body": body}
        health.write(self.log, USER, {"source": "import-renpho", "upsert": [rec]}, now_ms=NOW)
        found = health.weighings(self.log.db(USER), "2026-03-10", "2026-03-10")
        self.assertEqual(found, [(ms(2026, 3, 10, 7, 5), 79.15, None, "import-renpho", body)])

    def test_bad_batches_are_refused(self):
        one = [weight("w", ms(2026, 3, 10, 7), 70)]
        for doc in ([1], "x", {"upsert": one}, {"source": "Gadgetbridge", "upsert": one},
                    {"source": "a" * 61, "upsert": one}, {"source": "gadgetbridge"},
                    {"source": "gadgetbridge", "upsert": [], "delete": []},
                    {"source": "gadgetbridge", "upsert": {"w": one[0]}},
                    {"source": "gadgetbridge", "delete": [5]},
                    {"source": "gadgetbridge", "upsert": one * (health.MAX_RECORDS + 1)},
                    {"source": "gadgetbridge", "delete": ["w"] * (health.MAX_RECORDS + 1)}):
            with self.subTest(doc=str(doc)[:80]):
                self.assertRefused(doc)

    def test_a_bad_sample_says_what_it_is(self):
        at = ms(2026, 3, 10, 9)
        self.assertRefused({"source": "xrnfit-watch", "upsert": [run("o", "spo2", (at, 40))]}, "bad percent for spo2")
        self.assertRefused({"source": "xrnfit-watch", "upsert": [run("t", "stress", (at, 0))]}, "bad level for stress")
        self.assertRefused({"source": "xrnfit-watch", "upsert": [beats("h", (at, 300))]}, "bad bpm for heart_rate")

    def test_one_bad_record_refuses_the_whole_batch(self):
        at = ms(2026, 3, 10, 9)
        self.assertRefused({"source": "gadgetbridge",
                            "upsert": [weight("w", at, 70), weight("w2", at, 999)]}, "bad value for weight")
        self.assertEqual(self.rows(), [])

    def test_a_reading_a_day_ahead_is_taken_but_not_further(self):
        # A phone whose clock runs a little fast still lands; a year 2100 reading doesn't.
        self.put(upsert=[weight("w", NOW + health.DAY_MS - 1, 70)])
        self.assertRefused({"source": "gadgetbridge", "upsert": [weight("w", NOW + health.DAY_MS + 1, 70)]})

    def test_the_real_clock_is_used_when_none_is_given(self):
        recent = int(time.time() * 1000) - 60_000
        self.assertEqual(health.write(self.log, USER, {"source": "gadgetbridge", "upsert": [weight("w", recent, 70)]}),
                         (1, 0))


class Days(Store):
    def test_a_sleep_is_the_day_it_ends_on(self):
        self.put(upsert=[sleep("z", ms(2026, 3, 9, 23), ms(2026, 3, 10, 7))])
        self.assertIsNone(self.day("2026-03-09"))
        night = self.day("2026-03-10")["sleep"]
        self.assertEqual((night["minutes"], night["asleep"], night["start"], night["end"]),
                         (480, 480, ms(2026, 3, 9, 23), ms(2026, 3, 10, 7)))

    def test_a_sleeps_stages_add_up_and_awake_isnt_asleep(self):
        t = ms(2026, 3, 9, 23)
        m = 60_000
        self.put(upsert=[sleep("z", t, t + 300 * m, (t, t + 100 * m, "light"), (t + 100 * m, t + 160 * m, "deep"),
                               (t + 160 * m, t + 180 * m, "awake"), (t + 180 * m, t + 270 * m, "rem"),
                               (t + 270 * m, t + 300 * m, "asleep"))])
        night = self.day("2026-03-10")["sleep"]
        self.assertEqual({k: night[k] for k in ("minutes", "light", "deep", "awake", "rem", "asleep")},
                         {"minutes": 280, "light": 100, "deep": 60, "awake": 20, "rem": 90, "asleep": 30})

    def test_a_nap_and_the_night_make_the_days_sleep(self):
        self.put(upsert=[sleep("night", ms(2026, 3, 9, 23), ms(2026, 3, 10, 6)),
                         sleep("nap", ms(2026, 3, 10, 14), ms(2026, 3, 10, 14, 30))])
        day = self.day("2026-03-10")["sleep"]
        self.assertEqual((day["minutes"], day["start"], day["end"]), (450, ms(2026, 3, 9, 23), ms(2026, 3, 10, 14, 30)))

    def test_steps_count_on_the_day_they_start_on_this_machines_clock(self):
        self.put(upsert=[steps("late", ms(2026, 3, 9, 23, 30), ms(2026, 3, 9, 23, 59), 300),
                         steps("early", ms(2026, 3, 10, 0, 30), ms(2026, 3, 10, 1), 40),
                         steps("more", ms(2026, 3, 10, 9), ms(2026, 3, 10, 10), 2.5)])
        self.assertEqual(self.day("2026-03-09")["steps"], 300)
        self.assertEqual(self.day("2026-03-10")["steps"], 42)

    def test_one_source_counts_each_day(self):
        # The watch and the Mi Fitness export both have the 10th: the watch's counts, not both.
        self.put("import-mifitness", upsert=[steps("a", ms(2026, 3, 9, 9), ms(2026, 3, 9, 10), 5000),
                                             steps("b", ms(2026, 3, 10, 9), ms(2026, 3, 10, 10), 7000)])
        self.put(upsert=[steps("c", ms(2026, 3, 10, 9), ms(2026, 3, 10, 10), 6000)])
        self.put("some-app", upsert=[steps("d", ms(2026, 3, 10, 9), ms(2026, 3, 10, 10), 9000)])
        days = health.days(self.db, "2026-03-09", "2026-03-10")
        self.assertEqual((days["2026-03-09"]["steps"], days["2026-03-10"]["steps"]), (5000, 6000))

    def test_the_priority_runs_the_listed_sources_then_the_rest_by_name(self):
        order = sorted(["zeta", "import-googlefit", "alpha", "gadgetbridge", "import-renpho", "xrnfit-scale",
                        "import-mifitness", "xrnfit-watch"], key=health.rank)
        self.assertEqual(order, ["xrnfit-watch", "gadgetbridge", "xrnfit-scale", "import-mifitness", "import-renpho",
                                 "import-googlefit", "alpha", "zeta"])

    def test_on_the_day_xrnfit_took_over_from_gadgetbridge_its_watch_data_counts(self):
        # Both sent the watch's file for that day: xrnfit's has the whole day, Gadgetbridge's stops early.
        self.put(upsert=[steps("gb", ms(2026, 3, 10, 9), ms(2026, 3, 10, 10), 4000)])
        self.put("xrnfit-watch", upsert=[steps("xw", ms(2026, 3, 10, 9), ms(2026, 3, 10, 10), 4000),
                                         steps("xw2", ms(2026, 3, 10, 18), ms(2026, 3, 10, 19), 2500)])
        self.assertEqual(health.days(self.db, "2026-03-10", "2026-03-10")["2026-03-10"]["steps"], 6500)

    def test_heart_rate_by_day_from_one_source(self):
        self.put(upsert=[beats("h", (ms(2026, 3, 10, 9), 60), (ms(2026, 3, 10, 10), 71), (ms(2026, 3, 10, 11), 150)),
                         {"id": "r", "kind": "resting_heart_rate", "start": ms(2026, 3, 10, 8), "value": 57},
                         {"id": "r2", "kind": "resting_heart_rate", "start": ms(2026, 3, 10, 20), "value": 55}])
        self.put("import-mifitness", upsert=[beats("x", (ms(2026, 3, 10, 9), 200))])
        self.assertEqual(self.day("2026-03-10")["heartRate"], {"min": 60, "avg": 94, "max": 150, "resting": 55})
        self.assertEqual(health.heart_rate(self.db, "2026-03-10", "2026-03-10"),
                         [(ms(2026, 3, 10, 9), 60), (ms(2026, 3, 10, 10), 71), (ms(2026, 3, 10, 11), 150)])
        self.assertEqual(health.latest_resting(self.db), 55)

    def test_spo2_and_stress_by_day_from_one_source(self):
        self.put("xrnfit-watch", upsert=[run("o", "spo2", (ms(2026, 3, 10, 2), 93), (ms(2026, 3, 10, 3), 98)),
                                         run("t", "stress", (ms(2026, 3, 10, 9), 20), (ms(2026, 3, 10, 13), 61),
                                             (ms(2026, 3, 9, 13), 5))])
        self.put("some-app", upsert=[run("o", "spo2", (ms(2026, 3, 10, 2), 50)), run("t", "stress", (ms(2026, 3, 11, 9), 80))])
        days = health.days(self.db, "2026-03-09", "2026-03-11")
        self.assertEqual(days["2026-03-10"]["spo2"], {"min": 93, "avg": 96, "max": 98})  # the watch's, not the app's too
        self.assertEqual(days["2026-03-10"]["stress"], {"min": 20, "avg": 40, "max": 61})
        self.assertEqual(days["2026-03-09"]["stress"], {"min": 5, "avg": 5, "max": 5})
        self.assertEqual((days["2026-03-09"]["spo2"], days["2026-03-11"]["stress"]["avg"]), (None, 80))

    def test_a_resting_rate_alone_still_makes_a_heart_rate(self):
        self.put(upsert=[{"id": "r", "kind": "resting_heart_rate", "start": ms(2026, 3, 10, 8), "value": 57}])
        self.assertEqual(self.day("2026-03-10")["heartRate"], {"min": None, "avg": None, "max": None, "resting": 57})

    def test_every_weighing_is_listed_and_a_days_weight_is_its_last(self):
        self.put("import-renpho", upsert=[weight("a", ms(2026, 3, 10, 7), 72.4)])
        self.put("xrnfit-scale", upsert=[weight("b", ms(2026, 3, 10, 21), 72.9, impedance=480)])
        self.assertEqual(self.day("2026-03-10")["weightKg"], 72.9)
        self.assertEqual(health.weighings(self.db, "2026-03-10", "2026-03-10"),
                         [(ms(2026, 3, 10, 7), 72.4, None, "import-renpho", None),
                          (ms(2026, 3, 10, 21), 72.9, 480, "xrnfit-scale", None)])

    def test_nothing_held_reads_as_nothing(self):
        self.assertEqual(health.days(self.db, "2026-03-01", "2026-03-10"), {})
        self.assertIsNone(health.latest(self.db, "weight"))
        self.assertIsNone(health.latest_resting(self.db))
        self.assertEqual(health.held(self.db)["first"], None)


class Indexes(Store):
    """What Today's health card and the Overview read every minute or so goes through an index:
    the table gains a row for every minute of watch data, and the server is an old phone."""

    def test_todays_card_and_the_newest_readings_read_no_whole_table(self):
        # A whole-table count and a sort of every heart-rate sample ran on every /api/today: 43 and
        # 114 ms at a million rows on a laptop, several times that on the phone.
        self.put(upsert=[steps("s", ms(2026, 3, 10, 9), ms(2026, 3, 10, 10), 900),
                         beats("h", (ms(2026, 3, 10, 9), 70), (ms(2026, 3, 10, 9, 1), 72)),
                         sleep("n", ms(2026, 3, 9, 23), ms(2026, 3, 10, 7)),
                         {"id": "r", "kind": "resting_heart_rate", "start": ms(2026, 3, 10, 8), "value": 57},
                         weight("w", ms(2026, 3, 3, 7), 72.4), weight("v", ms(2026, 3, 10, 7), 72.1),
                         run("o", "spo2", (ms(2026, 3, 10, 2), 96)), run("t", "stress", (ms(2026, 3, 10, 9), 20))])
        ran = []
        self.db.set_trace_callback(ran.append)
        try:
            self.assertIsNotNone(daybook.body_view(self.db, date(2026, 3, 10)))
            for kind in ("heart_rate", "spo2", "stress", "weight"):
                self.assertIsNotNone(health.latest(self.db, kind))
            self.assertTrue(health.received_by_day(self.db, NOW - 1))
        finally:
            self.db.set_trace_callback(None)
        reads = [sql for sql in ran if sql.lstrip().upper().startswith("SELECT")]
        self.assertGreater(len(reads), 6)
        for sql in reads:
            plan = [row[3] for row in self.db.execute("EXPLAIN QUERY PLAN " + sql)]
            self.assertFalse([step for step in plan if step.startswith("SCAN")], (sql, plan))
            # A sort is fine over a few days' rows, not over every reading of a kind.
            bounded = any(">" in step for step in plan)
            self.assertFalse([step for step in plan if "TEMP B-TREE" in step and not bounded], (sql, plan))

    def test_a_database_made_before_the_indexes_gets_them(self):
        folder = os.path.join(self.folder, "older")
        os.makedirs(os.path.join(folder, "users", USER))
        old = sqlite3.connect(os.path.join(folder, "users", USER, "hub.sqlite"))
        old.executescript("""
            CREATE TABLE health(
                source TEXT NOT NULL, rid TEXT NOT NULL, kind TEXT NOT NULL, start INTEGER NOT NULL, end INTEGER,
                day TEXT NOT NULL, value REAL NOT NULL, detail TEXT NOT NULL DEFAULT '{}', received INTEGER NOT NULL,
                PRIMARY KEY(source, rid, start));
            CREATE INDEX health_day ON health(kind, day, source);
            INSERT INTO health VALUES('gadgetbridge', 'w', 'weight', 1773086400000, NULL, '2026-03-10', 72.4, '{}', 1);
        """)
        old.close()
        db = HubLog(folder).db(USER)
        names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'health'")}
        self.assertLessEqual({"health_kind_start", "health_received"}, names)
        self.assertEqual(health.latest(db, "weight")[1], 72.4)
        db.close()


if __name__ == "__main__":
    unittest.main()

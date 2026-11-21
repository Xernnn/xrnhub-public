import unittest
from datetime import date, datetime, timedelta

from support import ics
from hublib import ical


def starts(text, first, last):
    found = []
    for ev in ical.parse_ics(text):
        found += [lo for lo, _ in ical.occurrences(ev, first, last)]
    return sorted(found)


def days(text, first, last):
    return [d.date() for d in starts(text, first, last)]


class Parsing(unittest.TestCase):
    def test_folded_and_escaped_title(self):
        text = ics("UID:a", "DTSTART:20261106T090000", "SUMMARY:Lunch\\, then\r\n  a walk\\; home")
        self.assertEqual(ical.parse_ics(text)[0]["title"], "Lunch, then a walk; home")

    def test_alarm_inside_event_leaves_it_alone(self):
        text = ics("UID:a", "DTSTART:20261106T090000", "SUMMARY:Real",
                   "BEGIN:VALARM", "TRIGGER:-PT10M", "DESCRIPTION:x", "END:VALARM", "DTEND:20261106T100000")
        ev = ical.parse_ics(text)[0]
        self.assertEqual(ev["title"], "Real")
        self.assertEqual(ev["span"], timedelta(hours=1))

    def test_where_and_what_for_are_kept_short(self):
        text = ics("UID:a", "DTSTART:20261106T090000", "LOCATION:" + "x" * 300, "DESCRIPTION:" + "y" * 1200)
        ev = ical.parse_ics(text)[0]
        self.assertEqual((len(ev["location"]), len(ev["description"])), (200, 1000))

    def test_an_alarms_description_isnt_the_events(self):
        text = ics("UID:a", "DTSTART:20261106T090000", "BEGIN:VALARM", "DESCRIPTION:Reminder", "END:VALARM")
        self.assertNotIn("description", ical.parse_ics(text)[0])

    def test_cancelled_is_dropped(self):
        self.assertEqual(ical.parse_ics(ics("UID:a", "DTSTART:20261106T090000", "STATUS:CANCELLED")), [])

    def test_a_bad_date_loses_only_that_event(self):
        text = ics("UID:a", "DTSTART:garbage") + ics("UID:b", "DTSTART:20261106T090000")
        self.assertEqual([e["uid"] for e in ical.parse_ics(text)], ["b"])

    def test_a_number_too_big_loses_only_that_line(self):
        text = ics("UID:a", "DTSTART:20261106T090000", "DURATION:P99999999999W") + ics("UID:b", "DTSTART:20261106T090000")
        events = ical.parse_ics(text)
        self.assertEqual([e["uid"] for e in events], ["a", "b"])
        self.assertEqual(events[0]["span"], timedelta(0))

    def test_an_exception_before_the_first_year_loses_only_that_line(self):
        text = ics("UID:a", "DTSTART;TZID=Asia/Bangkok:20261106T090000", "RRULE:FREQ=DAILY;COUNT=2",
                   "EXDATE;TZID=Asia/Bangkok:00010101T000000")
        self.assertEqual(len(starts(text, date(2026, 11, 1), date(2026, 11, 30))), 2)

    def test_quoted_parameter_may_hold_a_colon(self):
        name, params, value = ical.split_property('DTSTART;TZID="UTC:odd":20261106T090000')
        self.assertEqual((name, params["TZID"], value), ("DTSTART", "UTC:odd", "20261106T090000"))

    def test_duration_stands_in_for_an_end(self):
        ev = ical.parse_ics(ics("UID:a", "DTSTART:20261106T090000", "DURATION:PT1H30M"))[0]
        self.assertEqual(ev["span"], timedelta(minutes=90))


class Times(unittest.TestCase):
    def test_all_day_end_is_exclusive(self):
        text = ics("UID:a", "DTSTART;VALUE=DATE:20261106", "DTEND;VALUE=DATE:20261107")
        self.assertEqual(days(text, date(2026, 11, 6), date(2026, 11, 6)), [date(2026, 11, 6)])
        self.assertEqual(days(text, date(2026, 11, 7), date(2026, 11, 7)), [])

    def test_several_days_touch_each_day_they_cover(self):
        text = ics("UID:a", "DTSTART;VALUE=DATE:20261106", "DTEND;VALUE=DATE:20261109")
        self.assertEqual(len(days(text, date(2026, 11, 8), date(2026, 11, 8))), 1)

    def test_fixed_zone_needs_no_zone_database(self):
        ical._zones.clear()
        moment = ical.parse_time("20261106T090000", {"TZID": "Asia/Bangkok"})
        self.assertEqual(moment.utcoffset(), timedelta(hours=7))

    def test_unknown_zone_reads_as_local_time(self):
        moment = ical.parse_time("20261106T090000", {"TZID": "Nowhere/Land"})
        self.assertIsNone(moment.tzinfo)


class Repeats(unittest.TestCase):
    def test_daily_count(self):
        text = ics("UID:a", "DTSTART:20261101T090000", "RRULE:FREQ=DAILY;COUNT=3")
        self.assertEqual(len(starts(text, date(2026, 11, 1), date(2026, 11, 30))), 3)

    def test_count_counts_from_the_start_not_the_window(self):
        text = ics("UID:a", "DTSTART:20261101T090000", "RRULE:FREQ=DAILY;COUNT=3")
        self.assertEqual(starts(text, date(2026, 11, 10), date(2026, 11, 20)), [])

    def test_weekly_days(self):
        text = ics("UID:a", "DTSTART:20261102T090000", "RRULE:FREQ=WEEKLY;BYDAY=MO,WE")
        self.assertEqual(days(text, date(2026, 11, 2), date(2026, 11, 8)), [date(2026, 11, 2), date(2026, 11, 4)])

    def test_weekly_interval(self):
        text = ics("UID:a", "DTSTART:20261102T090000", "RRULE:FREQ=WEEKLY;INTERVAL=2")
        self.assertEqual(days(text, date(2026, 11, 2), date(2026, 11, 22)), [date(2026, 11, 2), date(2026, 11, 16)])

    def test_until(self):
        text = ics("UID:a", "DTSTART:20261102T090000", "RRULE:FREQ=WEEKLY;UNTIL=20261110T000000Z")
        self.assertEqual(days(text, date(2026, 11, 1), date(2026, 12, 1)), [date(2026, 11, 2), date(2026, 11, 9)])

    def test_until_is_a_moment_not_a_date(self):
        # 15:00 in Bangkok is 08:00 UTC. xrncal ends a split series a second before the occurrence
        # it was split at, which is the same date in UTC: that occurrence isn't part of it.
        split = ics("UID:a", "DTSTART;TZID=Asia/Bangkok:20261102T150000", "RRULE:FREQ=DAILY;UNTIL=20261104T075959Z")
        self.assertEqual(len(starts(split, date(2026, 10, 25), date(2026, 11, 20))), 2)
        # 06:00 in Bangkok is 23:00 UTC the day before. A series ended at its last start keeps it.
        last = ics("UID:a", "DTSTART;TZID=Asia/Bangkok:20261102T060000", "RRULE:FREQ=DAILY;UNTIL=20261103T230000Z")
        self.assertEqual(len(starts(last, date(2026, 10, 25), date(2026, 11, 20))), 3)

    def test_until_on_an_all_day_series_is_its_last_day(self):
        text = ics("UID:a", "DTSTART;VALUE=DATE:20261102", "RRULE:FREQ=DAILY;UNTIL=20261104T235959Z")
        self.assertEqual(days(text, date(2026, 10, 25), date(2026, 11, 20)),
                         [date(2026, 11, 2), date(2026, 11, 3), date(2026, 11, 4)])

    def test_monthly_nth_weekday(self):
        text = ics("UID:a", "DTSTART:20261108T090000", "RRULE:FREQ=MONTHLY;BYDAY=2SU")
        self.assertEqual(days(text, date(2026, 12, 1), date(2026, 12, 31)), [date(2026, 12, 13)])

    def test_monthly_last_weekday(self):
        text = ics("UID:a", "DTSTART:20261130T090000", "RRULE:FREQ=MONTHLY;BYDAY=-1MO")
        self.assertEqual(days(text, date(2026, 12, 1), date(2026, 12, 31)), [date(2026, 12, 28)])

    def test_monthly_skips_short_months(self):
        text = ics("UID:a", "DTSTART:20270131T090000", "RRULE:FREQ=MONTHLY")
        self.assertEqual(days(text, date(2027, 2, 1), date(2027, 3, 31)), [date(2027, 3, 31)])

    def test_yearly_birthday(self):
        text = ics("UID:a", "DTSTART;VALUE=DATE:20100704", "DTEND;VALUE=DATE:20100705", "RRULE:FREQ=YEARLY")
        self.assertEqual(days(text, date(2026, 7, 4), date(2026, 7, 4)), [date(2026, 7, 4)])

    def test_yearly_on_a_weekday_of_a_month(self):
        text = ics("UID:a", "DTSTART;VALUE=DATE:20200104", "RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=2SA")
        self.assertEqual(days(text, date(2026, 10, 1), date(2026, 10, 31)), [date(2026, 10, 10)])

    def test_bysetpos_last_friday(self):
        text = ics("UID:a", "DTSTART:20261127T090000", "RRULE:FREQ=MONTHLY;BYDAY=FR;BYSETPOS=-1")
        self.assertEqual(days(text, date(2026, 12, 1), date(2026, 12, 31)), [date(2026, 12, 25)])

    def test_exdate_drops_one(self):
        text = ics("UID:a", "DTSTART:20261102T090000", "RRULE:FREQ=DAILY;COUNT=3", "EXDATE:20261103T090000")
        self.assertEqual(days(text, date(2026, 11, 1), date(2026, 11, 30)), [date(2026, 11, 2), date(2026, 11, 4)])

    def test_moved_occurrence_replaces_its_slot(self):
        series = ("BEGIN:VEVENT\r\nUID:a\r\nDTSTART:20261102T090000\r\nRRULE:FREQ=DAILY;COUNT=3\r\nSUMMARY:Series\r\nEND:VEVENT\r\n"
                  "BEGIN:VEVENT\r\nUID:a\r\nRECURRENCE-ID:20261103T090000\r\nDTSTART:20261103T150000\r\nSUMMARY:Moved\r\nEND:VEVENT\r\n")
        found = starts("BEGIN:VCALENDAR\r\n" + series + "END:VCALENDAR\r\n", date(2026, 11, 3), date(2026, 11, 3))
        self.assertEqual(found, [datetime(2026, 11, 3, 15)])

    def test_a_cancelled_occurrence_takes_its_slot_away(self):
        # Dropped before the series was told, the cancelled one left the series its slot.
        series = "BEGIN:VEVENT\r\nUID:a\r\nDTSTART:20261102T090000\r\nRRULE:FREQ=DAILY;COUNT=3\r\nSUMMARY:Series\r\nEND:VEVENT\r\n"
        for written in ("RECURRENCE-ID:20261103T090000\r\nDTSTART:20261103T090000\r\n",
                        "RECURRENCE-ID:20261103T090000\r\n"):  # with no start of its own
            off = f"BEGIN:VEVENT\r\nUID:a\r\n{written}STATUS:CANCELLED\r\nSUMMARY:Series\r\nEND:VEVENT\r\n"
            text = "BEGIN:VCALENDAR\r\n" + off + series + "END:VCALENDAR\r\n"  # either order in the file
            self.assertEqual(days(text, date(2026, 11, 1), date(2026, 11, 30)), [date(2026, 11, 2), date(2026, 11, 4)])

    def test_a_rule_that_never_lands_ends(self):
        text = ics("UID:a", "DTSTART:20261102T090000", "RRULE:FREQ=MONTHLY;BYMONTHDAY=31;BYMONTH=2")
        self.assertEqual(starts(text, date(2026, 1, 1), date(2030, 1, 1)), [])

    def test_a_rule_stepping_past_the_last_year_ends(self):
        for rule in ("FREQ=DAILY;INTERVAL=4000000", "FREQ=WEEKLY;INTERVAL=4000000", "FREQ=MONTHLY;INTERVAL=400000"):
            text = ics("UID:a", "DTSTART:20261102T090000", f"RRULE:{rule}")
            self.assertEqual(days(text, date(2026, 11, 1), date(2026, 11, 30)), [date(2026, 11, 2)], rule)

    def test_an_event_at_the_end_of_time_is_left_out(self):
        for start in ("DTSTART;VALUE=DATE:99991231", "DTSTART:99991231T235900Z"):
            self.assertEqual(starts(ics("UID:a", start), date(2026, 11, 1), date(2026, 11, 30)), [], start)

    def test_unknown_freq_is_a_single_event(self):
        text = ics("UID:a", "DTSTART:20261102T090000", "RRULE:FREQ=HOURLY")
        self.assertEqual(len(starts(text, date(2026, 11, 2), date(2026, 11, 9))), 1)


if __name__ == "__main__":
    unittest.main()

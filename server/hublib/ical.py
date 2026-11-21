"""Just enough iCalendar to say what's on: VEVENTs, their times and their repeats.

The calendar is Radicale's folders of .ics files, around 8,000 events. Nothing here writes to them.
Times are worked out in the event's own zone, so a weekly 09:00 stays 09:00 across a clock change,
then moved to this machine's local time. All of it is plain data so the tests need no calendar.
"""
import calendar
import re
from datetime import date, datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # pragma: no cover
    ZoneInfo = None
    ZoneInfoNotFoundError = Exception

# Termux's Python finds no zone database of its own (Android keeps it in another format). These
# hold no daylight saving, so they're right without one; any other zone falls back to local time.
FIXED_ZONES = {
    "Asia/Bangkok": 7,
    "Asia/Ho_Chi_Minh": 7,
    "Asia/Saigon": 7,
    "UTC": 0,
    "Etc/UTC": 0,
    "GMT": 0,
}
WEEKDAYS = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}
MAX_PERIODS = 20000  # a rule that never lands gives up here, rather than spinning
# Where and what for, kept for the API. Some 8,000 events sit in memory on the phone, and an invite
# can carry pages of joining instructions, so each is cut short.
MAX_LOCATION = 200
MAX_DESCRIPTION = 1000
DURATION = re.compile(r"^([+-])?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$")
_zones = {}


def zone(name):
    """The tzinfo for a TZID, or None when this machine can't place it (read as local time)."""
    if name in _zones:
        return _zones[name]
    found = None
    if ZoneInfo is not None:
        try:
            found = ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError, OSError):
            found = None
    if found is None and name in FIXED_ZONES:
        found = timezone(timedelta(hours=FIXED_ZONES[name]))
    _zones[name] = found
    return found


def unfold(text):
    return re.sub(r"\r?\n[ \t]", "", text).splitlines()


def split_property(line):
    """NAME;PARAM=VALUE:rest into (name, params, rest); a quoted parameter may hold a colon."""
    quoted = False
    for i, ch in enumerate(line):
        if ch == '"':
            quoted = not quoted
        elif ch == ":" and not quoted:
            head, value = line[:i], line[i + 1:]
            break
    else:
        return None
    name, *rest = head.split(";")
    params = {}
    for p in rest:
        k, _, v = p.partition("=")
        params[k.upper()] = v.strip('"')
    return name.upper(), params, value


def unescape(value):
    return re.sub(r"\\([\\,;nN])", lambda m: "\n" if m[1] in "nN" else m[1], value)


def parse_time(value, params):
    """A date, an aware datetime, or a naive one meaning local time. Raises ValueError if bad."""
    value = value.strip()
    if params.get("VALUE") == "DATE" or len(value) == 8:
        return datetime.strptime(value[:8], "%Y%m%d").date()
    if value.endswith("Z"):
        return datetime.strptime(value[:-1], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
    moment = datetime.strptime(value, "%Y%m%dT%H%M%S")
    place = zone(params["TZID"]) if "TZID" in params else None
    return moment.replace(tzinfo=place) if place else moment


def parse_duration(value):
    m = DURATION.match(value.strip())
    if not m:
        return None
    sign, weeks, days, hours, minutes, seconds = m.groups()
    span = timedelta(
        weeks=int(weeks or 0), days=int(days or 0),
        hours=int(hours or 0), minutes=int(minutes or 0), seconds=int(seconds or 0),
    )
    return -span if sign == "-" else span


def parse_rrule(value):
    rule = {}
    for part in value.split(";"):
        k, _, v = part.partition("=")
        if k:
            rule[k.upper()] = v
    return rule if rule.get("FREQ") in ("DAILY", "WEEKLY", "MONTHLY", "YEARLY") else None


def parse_ics(text):
    """Every VEVENT in a file, as dicts. A broken event is skipped; the rest of the file still reads."""
    events = []
    cancelled = []  # occurrences of a series called off one by one
    cur = None
    depth = 0  # VALARMs sit inside a VEVENT
    for line in unfold(text):
        upper = line.upper()
        if upper == "BEGIN:VEVENT":
            cur = {"exdates": set()}
            depth = 0
            continue
        if cur is None:
            continue
        if upper.startswith("BEGIN:"):
            depth += 1
            continue
        if upper.startswith("END:") and upper != "END:VEVENT":
            depth = max(0, depth - 1)
            continue
        if upper == "END:VEVENT":
            if cur.get("status") == "CANCELLED":
                if "recurrence_id" in cur:
                    cancelled.append(cur)
            elif "start" in cur:
                events.append(finish_event(cur))
            cur = None
            continue
        if depth:
            continue
        parsed = split_property(line)
        if not parsed:
            continue
        name, params, value = parsed
        try:
            if name == "UID":
                cur["uid"] = value
            elif name == "SUMMARY":
                cur["title"] = unescape(value)
            elif name == "LOCATION":
                cur["location"] = unescape(value).strip()[:MAX_LOCATION]
            elif name == "DESCRIPTION":
                cur["description"] = unescape(value).strip()[:MAX_DESCRIPTION]
            elif name == "STATUS":
                cur["status"] = value.strip().upper()
            elif name == "DTSTART":
                cur["start"] = parse_time(value, params)
            elif name == "DTEND":
                cur["end"] = parse_time(value, params)
            elif name == "DURATION":
                cur["duration"] = parse_duration(value)
            elif name == "RRULE":
                cur["rrule"] = parse_rrule(value)
            elif name == "EXDATE":
                cur["exdates"] |= {key_of(parse_time(v, params)) for v in value.split(",")}
            elif name == "RECURRENCE-ID":
                cur["recurrence_id"] = key_of(parse_time(value, params))
        except (ValueError, OverflowError):  # a number no date or length can hold is as bad as a typo
            continue
    return link_overrides(events, cancelled)


def finish_event(ev):
    start = ev["start"]
    all_day = not isinstance(start, datetime)
    span = ev.pop("duration", None)
    end = ev.pop("end", None)
    if end is not None:
        try:
            span = end - start
        except TypeError:  # one with a zone and one without: give up on the length
            span = None
    if span is None or span < timedelta(0):
        span = timedelta(days=1) if all_day else timedelta(0)
    ev["span"] = span
    ev["all_day"] = all_day
    ev.setdefault("title", "")
    return ev


def link_overrides(events, cancelled=()):
    """An occurrence moved or renamed on its own stands alone, and the series skips that slot. One
    called off on its own (a RECURRENCE-ID with STATUS:CANCELLED) only takes its slot away."""
    masters = {e.get("uid"): e for e in events if e.get("rrule") and "recurrence_id" not in e}
    for e in [*events, *cancelled]:
        master = masters.get(e.get("uid"))
        if master is not None and "recurrence_id" in e:
            master["exdates"].add(e["recurrence_id"])
    return events


def key_of(moment):
    """What identifies an occurrence, however its zone was written."""
    if isinstance(moment, datetime):
        if moment.tzinfo is not None:
            return moment.astimezone(timezone.utc).replace(tzinfo=None)
        return moment
    return moment


def month_days(year, month):
    return calendar.monthrange(year, month)[1]


def parse_byday(value):
    found = []
    for part in (value or "").split(","):
        m = re.fullmatch(r"([+-]?\d+)?(MO|TU|WE|TH|FR|SA|SU)", part.strip())
        if m:
            found.append((int(m[1]) if m[1] else None, WEEKDAYS[m[2]]))
    return found


def ints(value):
    out = []
    for part in (value or "").split(","):
        try:
            out.append(int(part))
        except ValueError:
            pass
    return out


def nth_weekdays(year, month, ordinal, weekday):
    """The days of a month falling on [weekday]; just the nth (from the end if negative) if given."""
    days = [d for d in range(1, month_days(year, month) + 1) if date(year, month, d).weekday() == weekday]
    if ordinal is None:
        return days
    index = ordinal - 1 if ordinal > 0 else ordinal
    return [days[index]] if -len(days) <= index < len(days) else []


def month_candidates(year, month, start, byday, bymonthday):
    length = month_days(year, month)
    if byday:
        days = sorted({d for o, w in byday for d in nth_weekdays(year, month, o, w)})
        if bymonthday:
            allowed = {d if d > 0 else length + d + 1 for d in bymonthday}
            days = [d for d in days if d in allowed]
        return [date(year, month, d) for d in days]
    if bymonthday:
        days = sorted({d if d > 0 else length + d + 1 for d in bymonthday})
        return [date(year, month, d) for d in days if 1 <= d <= length]
    return [date(year, month, start.day)] if start.day <= length else []


def rule_dates(start, rule, last):
    """The dates [rule] lands on from [start], in order, up to [last]."""
    freq = rule["FREQ"]
    try:
        interval = max(1, int(rule.get("INTERVAL") or 1))
        count = int(rule["COUNT"]) if "COUNT" in rule else None
    except ValueError:
        return
    byday = parse_byday(rule.get("BYDAY"))
    bymonth = ints(rule.get("BYMONTH"))
    bymonthday = ints(rule.get("BYMONTHDAY"))
    bysetpos = ints(rule.get("BYSETPOS"))
    until = None
    if rule.get("UNTIL"):
        try:
            moment = parse_time(rule["UNTIL"], {})
            until = moment.date() if isinstance(moment, datetime) else moment
        except ValueError:
            pass
    hard_end = min(last, until) if until else last
    emitted = 0
    for period in range(MAX_PERIODS):
        if freq == "DAILY":
            day = start + timedelta(days=period * interval)
            first, candidates = day, [day]
            if byday:
                allowed = {w for _, w in byday}
                candidates = [d for d in candidates if d.weekday() in allowed]
        elif freq == "WEEKLY":
            monday = start - timedelta(days=start.weekday()) + timedelta(weeks=period * interval)
            first = monday
            days = sorted({w for _, w in byday}) or [start.weekday()]
            candidates = [monday + timedelta(days=w) for w in days]
        elif freq == "MONTHLY":
            total = start.year * 12 + start.month - 1 + period * interval
            year, month = total // 12, total % 12 + 1
            first = date(year, month, 1)
            candidates = month_candidates(year, month, start, byday, bymonthday)
        else:
            year = start.year + period * interval
            if year > 9000:
                return
            first = date(year, 1, 1)
            candidates = []
            for month in bymonth or [start.month]:
                if 1 <= month <= 12:
                    candidates += month_candidates(year, month, start, byday, bymonthday)
        if first > hard_end:
            return
        if bymonth and freq != "YEARLY":
            candidates = [d for d in candidates if d.month in bymonth]
        if bysetpos:
            candidates = sorted(candidates)
            size = len(candidates)
            candidates = [candidates[p - 1 if p > 0 else p] for p in bysetpos if -size <= p <= size and p != 0]
        for day in sorted(candidates):
            if day < start:
                continue
            if day > hard_end:
                return
            if count is not None and emitted >= count:
                return
            emitted += 1
            yield day


def until_moment(rule):
    """A rule's UNTIL when it's written as an instant (...Z), else None. A plain date is the
    series' last day and rule_dates judges it."""
    try:
        moment = parse_time(rule.get("UNTIL") or "", {})
    except ValueError:
        return None
    return moment if isinstance(moment, datetime) and moment.tzinfo else None


def to_local(moment):
    """A naive datetime on this machine's clock, for comparing with its days."""
    if isinstance(moment, datetime):
        if moment.tzinfo is not None:
            return moment.astimezone().replace(tzinfo=None)
        return moment
    return datetime(moment.year, moment.month, moment.day)


def occurrences(event, first, last):
    """(start, end) in local time for each time [event] touches the days [first]..[last].

    An all-day end is exclusive, as written in the file, and comes back that way."""
    start = event["start"]
    span = event["span"]
    margin = timedelta(days=1)  # a zone can put an occurrence on the next day here
    window_lo = first - margin
    window_hi = last + margin
    if not event.get("rrule"):
        slots = [start]
    else:
        day_of = start.date() if isinstance(start, datetime) else start
        rule = event["rrule"]
        # An instant can't be judged by its UTC date, which east of Greenwich is often the day
        # before the occurrence it names: each start is held against it instead.
        cut = until_moment(rule) if isinstance(start, datetime) else None
        if cut:
            rule = {k: v for k, v in rule.items() if k != "UNTIL"}
        slots = []
        try:
            for day in rule_dates(day_of, rule, window_hi):
                slot = start.replace(year=day.year, month=day.month, day=day.day) if isinstance(start, datetime) else day
                if cut and (slot if slot.tzinfo else slot.astimezone()) > cut:
                    break
                if key_of(slot) not in event["exdates"]:
                    slots.append(slot)
        except (ValueError, OverflowError):  # the rule stepped past the last year there is
            pass
    found = []
    for slot in slots:
        try:
            lo = to_local(slot)
            hi = to_local(slot + span) if isinstance(slot, datetime) else lo + span
        except (ValueError, OverflowError):  # too near year 1 or 9999 to place on this clock
            continue
        last_touch = hi - timedelta(microseconds=1) if hi > lo else hi
        if last_touch.date() < first or lo.date() > last:
            continue
        found.append((lo, hi))
    return found

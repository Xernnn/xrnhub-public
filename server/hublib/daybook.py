"""What a day looks like across every app: today's page, one day in full, and the table the long
view reads.

Days are habit days, as in xrnloops and xrnrule: 01:30 still belongs to the day before when the
day starts at 03:00. Everything takes `now` as an argument (a naive local datetime), so the tests
can stand on any day.
"""
import calendar
import math
from datetime import date, datetime, timedelta

from . import health
from .sources import days_between, parse_day

# xrnloops' entry values, the same as xrnrule's HabitEntry.
NO, YES_AUTO, YES_MANUAL, SKIP = 0, 1, 2, 3
EXPENSE, INCOME = 0, 1
EXPIRY_DAYS = 7
HISTORY_DAYS = 400
RECENT_DAYS = 14  # the strip above the habits: far enough back to catch up on a lapse
COUNTDOWN_WORDS = ("deadline", "birthday", "payment", "exam")  # calendars worth counting down to
COUNTDOWN_DAYS = 90
COUNTDOWN_SHOW = 5


def number(value, fallback=0):
    """[value] if it is a number, else [fallback]. The other apps' files are read as they come: a
    string where a number belongs (a bad import, a changed field) shows less, it never fails a page."""
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else fallback


def habit_day(now, start_hour):
    return (now - timedelta(hours=start_hour)).date()


def ms_of(moment):
    return int(moment.timestamp() * 1000)


def habit_done(habit, value):
    """Whether an entry meets the habit: a tick, or an amount (thousandths) past its target."""
    if value is None or value < 0:
        return False
    if habit["type"] == 1:
        amount, target = value / 1000, number(habit["targetValue"])
        return amount <= target if habit["targetType"] == 1 else amount >= target
    return value in (YES_AUTO, YES_MANUAL)


def is_daily(habit):
    return habit["freqNum"] >= habit["freqDen"]


def habit_state(habit, value):
    """A day's mark on a habit. Not daily, it counts only when done; skipped, it doesn't count at
    all: neither lowers the score."""
    done = habit_done(habit, value)
    skipped = habit["type"] == 0 and value == SKIP
    return {"done": done, "skipped": skipped, "counts": (done or is_daily(habit)) and not skipped}


def habit_streak(habit, per_day, day):
    """Days in a row up to [day]. A skip neither breaks nor adds, and a day not done yet is still
    open, so it doesn't end the run behind it."""
    if not is_daily(habit):
        return 0
    run = 0
    when = day
    if not habit_done(habit, per_day.get(when.isoformat())):
        when -= timedelta(days=1)
    while True:
        value = per_day.get(when.isoformat())
        if habit_done(habit, value):
            run += 1
        elif not (habit["type"] == 0 and value == SKIP):
            return run
        when -= timedelta(days=1)


def habit_rows(src, habits, entries, day):
    """The list for a day, with what the page needs to write a tick back through xrnloops."""
    key = day.isoformat()
    rows = []
    for h in habits:
        if h["archived"]:
            continue
        per_day = entries.get(h["id"], {})
        value = per_day.get(key)
        rows.append({
            **habit_state(h, value),
            "id": h["id"],
            "name": h["name"],
            "color": h["color"],
            "type": h["type"],
            "targetType": h["targetType"],
            "targetValue": h["targetValue"],
            "unit": h["unit"],
            "daily": is_daily(h),
            "value": value,
            "notes": src.entry_notes.get((h["id"], key), ""),
            "mtime": src.entry_times.get((h["id"], key), 0),
            "streak": habit_streak(h, per_day, day),
        })
    return rows


def recent_days(habits, entries, today, count=RECENT_DAYS):
    """One small summary a day, oldest first, for the strip."""
    active = [h for h in habits if not h["archived"]]
    found = []
    for back in range(count - 1, -1, -1):
        day = today - timedelta(days=back)
        key = day.isoformat()
        rows = []
        logged = 0
        for h in active:
            value = entries.get(h["id"], {}).get(key)
            rows.append(habit_state(h, value))
            logged += value is not None and value >= 0
        found.append({**habit_score(rows), "day": key, "logged": logged})
    return found


def habit_score(rows):
    counted = [r for r in rows if r["counts"]]
    return {
        "done": sum(1 for r in counted if r["done"]),
        "total": len(counted),
    }


def day_start_ms(now):
    return ms_of(now.replace(hour=0, minute=0, second=0, microsecond=0))


def window_active(days, window, now):
    iso = now.isoweekday()
    if not window:
        return iso in days
    minute = now.hour * 60 + now.minute
    lo, hi = window.get("startMinuteOfDay", 0), window.get("endMinuteOfDay", 0)
    if lo <= hi:
        return iso in days and lo <= minute < hi
    if minute >= lo:
        return iso in days
    if minute < hi:
        return (7 if iso == 1 else iso - 1) in days
    return False


def schedule_active(schedule, now):
    schedule = schedule or {}
    if window_active(schedule.get("activeDays") or [1, 2, 3, 4, 5, 6, 7], schedule.get("activeWindow"), now):
        return True
    return any(window_active(w.get("days") or [], w.get("window"), now) for w in schedule.get("moreWindows") or [])


def block_view(block, doc, now):
    """One block for the page: its state, and its use if it has a limit. A short read of what
    xrnrule's own status page works out; xrnrule's engine stays the judge of what's blocked."""
    wall = ms_of(now)
    today = day_start_ms(now)
    bid = block.get("id")
    uses = [u for u in ((doc.get("allowance") or {}).get(bid) or {}).values() if isinstance(u, dict)]
    used = sum(number(u.get("ms")) for u in uses if u.get("dayStartWall") == today)
    limit = number(block.get("dailyAllowanceMs"))
    view = {"name": str(block.get("name") or ""), "usedMs": used, "limitMs": limit, "until": None}
    mode = block.get("mode") or "HABIT"
    pause = doc.get("pause")
    session = (doc.get("sessions") or {}).get(bid)
    override = (doc.get("overrides") or {}).get(bid)
    lockdown = (doc.get("lockdowns") or {}).get(bid)

    def until(item):
        item = item if isinstance(item, dict) else {}
        end = number(item.get("startedAtWall")) + number(item.get("durationMs"))
        return end if end > wall else 0

    if block.get("enabled") is False:
        return {**view, "state": "off"}
    if mode == "LOCKDOWN":
        end = until(lockdown)
        return {**view, "state": "blocked" if end else "off", "until": end or None}
    soft = (block.get("enforcementTier") or "SOFT") == "SOFT"
    if pause and until(pause) and soft:
        return {**view, "state": "open", "until": until(pause)}
    if until(session):
        return {**view, "state": "open", "until": until(session)}
    if until(override):
        return {**view, "state": "open", "until": until(override)}
    if not schedule_active(block.get("schedule"), now):
        return {**view, "state": "off"}
    if mode == "SCHEDULE":
        return {**view, "state": "blocked"}
    if mode == "ALLOWANCE" and limit and used < limit:
        return {**view, "state": "open"}
    if mode == "ALLOWANCE" and not limit:
        return {**view, "state": "open"}
    return {**view, "state": "blocked"}


def round_half_up(x):
    """Kotlin's roundToLong, which the wallet rounds with: a half goes up, so 2.5 is 3 and -2.5 is -2.
    Python's round() sends a half to the even side, 2.5 to 2, a cent away from what the app shows."""
    return math.floor(x + 0.5)


def bill_base_cents(bill):
    """A bill's worth in the wallet's base currency, as the app works it out (Money.convert)."""
    try:
        return round_half_up(bill.get("amountCents", 0) * float(bill.get("rate", 1.0)))
    except (TypeError, ValueError, OverflowError):
        return 0


def wallet_settings(wallet):
    """xrnwal's settings, which it keeps as a JSON string inside its state; {} when unreadable."""
    settings = wallet.get("settings")
    if isinstance(settings, str):
        import json

        try:
            settings = json.loads(settings)
        except ValueError:
            settings = None
    return settings if isinstance(settings, dict) else {}


def base_currency(wallet):
    return str(wallet_settings(wallet).get("baseCurrency") or "AUD")[:3]


def setting(settings, key, fallback, lo=None, hi=None):
    """One setting as the app would read it: the default when missing, mistyped or out of range."""
    value = settings.get(key, fallback)
    if isinstance(fallback, bool):
        return value if isinstance(value, bool) else fallback
    if not isinstance(value, int) or isinstance(value, bool) or (lo is not None and not lo <= value <= hi):
        return fallback
    return value


# PeriodRules' WeekendRule: a start that lands on a weekend stays, moves back to Friday, or on to Monday.
NO_CHANGE, EARLIER, LATER = 0, 1, 2


def month_anchor(year, month, start_day, weekend):
    """The first day of the accounting month that starts in [year]-[month] (PeriodRules.anchor)."""
    day = date(year, month, min(start_day, calendar.monthrange(year, month)[1]))
    if day.weekday() >= 5 and weekend == EARLIER:
        return day - timedelta(days=day.weekday() - 4)
    if day.weekday() >= 5 and weekend == LATER:
        return day + timedelta(days=7 - day.weekday())
    return day


def shift_month(year, month, steps):
    index = year * 12 + month - 1 + steps
    return index // 12, index % 12 + 1


def accounting_month(settings, today, back=0):
    """(first, last) of the accounting month holding [today], or [back] months before it, as xrnwal
    works it out (PeriodRules.monthOf and monthRange): it starts on the Start of Month day, moved off a
    weekend if asked, and ends the day before the next one starts. Which month it is labelled with
    (Belonging) doesn't move its days."""
    start_day = setting(settings, "startDay", 1, 1, 31)
    weekend = setting(settings, "weekendRule", NO_CHANGE, 0, 2)
    year, month = today.year, today.month
    while month_anchor(year, month, start_day, weekend) > today:
        year, month = shift_month(year, month, -1)
    while month_anchor(*shift_month(year, month, 1), start_day, weekend) <= today:
        year, month = shift_month(year, month, 1)
    year, month = shift_month(year, month, -back)
    following = month_anchor(*shift_month(year, month, 1), start_day, weekend)
    return month_anchor(year, month, start_day, weekend), following - timedelta(days=1)


def bill_date(bill):
    """The day a bill is on, where it was entered (Bill.date): at its offset from UTC when it has one,
    else in this machine's zone, as the app reads a bill from before it kept the offset. None for a
    time no calendar reaches."""
    at = bill.get("atMillis")
    if not isinstance(at, (int, float)) or isinstance(at, bool):
        return None
    offset = bill.get("offsetSeconds")
    try:
        if isinstance(offset, int) and not isinstance(offset, bool) and abs(offset) <= 18 * 3600:
            return (datetime(1970, 1, 1) + timedelta(milliseconds=at, seconds=offset)).date()
        return datetime.fromtimestamp(at / 1000).date()
    except (OverflowError, ValueError, OSError):
        return None


def in_budget(bill):
    """Whether a bill counts against the budget. xrnwal's bills carry `inBudget`, true unless it
    was switched off on the bill (or on the repeating bill that made it), as for rent."""
    return bill.get("inBudget") is not False


def wallet_bills(wallet):
    """(bill, its day, its worth in the base currency) for every expense and income that has a day."""
    found = []
    for b in wallet.get("bills") or []:
        if not isinstance(b, dict) or b.get("type") not in (EXPENSE, INCOME):
            continue
        day = bill_date(b)
        if day is not None:
            found.append((b, day, bill_base_cents(b)))
    return found


def overall_budget(wallet, settings):
    """The monthly budget's amount, or None when there's none or Budget is off in the app's settings."""
    if not setting(settings, "budgetEnabled", True):
        return None
    amount = next(
        (b.get("amountCents") for b in wallet.get("budgets") or [] if isinstance(b, dict) and b.get("categoryId") is None),
        None,
    )
    amount = number(amount, None)
    return amount if amount and amount > 0 else None


def active_ledger(wallet, settings):
    """The ledger the app's Home shows, or None for all of them (Snapshot.activeLedger)."""
    chosen = setting(settings, "activeLedger", 0)
    ids = {x.get("id") for x in wallet.get("ledgers") or [] if isinstance(x, dict)}
    return chosen if chosen and chosen in ids else None


def money_view(wallet, now):
    """The month so far as xrnwal's Home shows it, and the budget as its Budget card works it out
    (Stats.budget): the accounting month, every ledger, expenses only, and only bills counted in the
    budget. Rent marked as outside the budget is spent, but not against the budget. Future-dated bills
    count when the app's Include future setting is on, as they do there."""
    bills = wallet_bills(wallet)
    if not bills and not wallet.get("accounts"):
        return None
    settings = wallet_settings(wallet)
    today = now.date()
    first, last = accounting_month(settings, today)
    future = setting(settings, "includeFuture", True)
    ledger = active_ledger(wallet, settings)
    names = {c.get("id"): str(c.get("name") or "") for c in wallet.get("categories") or [] if isinstance(c, dict)}
    spent_today = spent_month = earned_month = 0
    counted_today = counted_month = outside = 0
    per_day = [0] * ((last - first).days + 1)
    by_category = {}
    for b, day, cents in bills:
        if not first <= day <= last or (day > today and not future):
            continue
        if b.get("type") == INCOME:
            earned_month += cents if ledger is None or b.get("ledgerId") == ledger else 0
            continue
        if ledger is None or b.get("ledgerId") == ledger:
            spent_month += cents
            spent_today += cents if day == today else 0
            label = names.get(b.get("categoryId")) or ""
            by_category[label] = by_category.get(label, 0) + cents
        if in_budget(b):
            counted_month += cents
            counted_today += cents if day == today else 0
            per_day[(day - first).days] += cents
        else:
            outside += cents
    amount = overall_budget(wallet, settings)
    days_left = (last - today).days + 1 if first <= today <= last else 0
    left = amount - counted_month if amount is not None else None
    top = sorted(by_category.items(), key=lambda kv: -kv[1])[:3]
    return {
        "currency": base_currency(wallet),
        "todayCents": spent_today,
        "monthCents": spent_month,
        "earnedCents": earned_month,
        "budgetCents": amount,
        "top": [{"name": k, "cents": v} for k, v in top if k],
        "budget": {
            "first": first.isoformat(),
            "last": last.isoformat(),
            "cents": amount,
            "spentCents": counted_month,
            "todayCents": counted_today,
            "outsideCents": outside,
            "leftCents": left,
            "daysLeft": days_left,
            # What's left to spend each day to the month's end (BudgetStatus.dailyAverageCents).
            "perDayCents": max(left, 0) // days_left if left is not None and days_left else None,
            "days": per_day,
        },
    }


def home_view(items, now):
    if not items:
        return None
    today = now.date()
    shopping = []
    expiring = []
    for item in items:
        name = str(item.get("name") or "")
        if item.get("wanted") and not item.get("snoozed"):
            shopping.append(name)
        if number(item.get("quantity")) > 0 and item.get("expiryDate"):
            expiry = parse_day(item["expiryDate"])
            if expiry and (expiry - today).days <= EXPIRY_DAYS:
                expiring.append({"name": name, "days": (expiry - today).days})
    expiring.sort(key=lambda e: e["days"])
    return {"shopping": shopping, "expiring": expiring, "items": len(items)}


def occurrence_view(o):
    return {
        "start": o["start"].isoformat(timespec="minutes"),
        "end": o["end"].isoformat(timespec="minutes"),
        "allDay": o["allDay"],
        "title": o["title"],
        "calendar": o["calendar"],
        "color": o["color"],
    }


def block_needs(block, doc, habits, entries, day):
    """What a habit-gated block is waiting for, read the way xrnrule's contract says: a habit item
    looks at the habit's own day, anything else at its tick."""
    by_id = {h["id"]: h for h in habits}
    found = []
    items = [i for i in (doc.get("items") or {}).values() if isinstance(i, dict) and i.get("blockId") == block.get("id")]
    for item in sorted(items, key=lambda i: i.get("order") or 0):
        habit = by_id.get(item.get("habitId"))
        if habit:
            when = day - timedelta(days=1) if item.get("linkedDay") == "YESTERDAY" else day
            done = habit_done(habit, entries.get(habit["id"], {}).get(when.isoformat()))
        else:
            done = bool(item.get("done"))
        found.append({"text": str(item.get("text") or ""), "habit": habit["id"] if habit else None, "done": done})
    return found


def countdowns(src, now):
    if not src.calendar.ready:
        return []
    today = now.date()
    found = []
    for o in src.calendar.between(today, today + timedelta(days=COUNTDOWN_DAYS)):
        when = o["start"].date()
        if when < today or not any(word in o["calendar"].lower() for word in COUNTDOWN_WORDS):
            continue
        found.append({
            "title": o["title"], "calendar": o["calendar"], "color": o["color"],
            "date": when.isoformat(), "days": (when - today).days,
        })
    return found[:COUNTDOWN_SHOW]


def body_view(db, day):
    """The health card on Today: [day]'s steps so far, the night that ended that morning (or the one
    before, until it comes in), the newest resting heart rate and the newest weighing, with its
    change over about a week. [day] is the page's habit day, and health is read under the same date
    in the health table, which keeps calendar days: so from midnight to the day's start hour the
    card still shows the day the page shows, and the steps walked in those hours count the next
    morning, when the page turns over. None when no health has ever come in."""
    received = health.last_received(db)
    if received is None:
        return None
    first = day - timedelta(days=RECENT_DAYS - 1)
    found = health.days(db, first.isoformat(), day.isoformat())
    recent = [found.get((first + timedelta(days=i)).isoformat()) or health.blank_day() for i in range(RECENT_DAYS)]
    key, before = day.isoformat(), (day - timedelta(days=1)).isoformat()
    today = recent[-1]
    night, night_day = today["sleep"], key
    if night is None:
        night, night_day = recent[-2]["sleep"], before
    resting = health.resting_day(db)
    return {
        "day": key,
        "steps": today["steps"],
        "sleep": {"day": night_day, "minutes": night["minutes"], "start": night["start"], "end": night["end"]}
        if night else None,
        "resting": {"day": resting[0], "bpm": resting[1]} if resting else None,
        "weight": health.weight_trend(db),
        "updated": received,
        # The fortnight up to [day], oldest first, a number or None a day: steps, minutes asleep (the
        # night that ended that morning), resting heart rate and weight.
        "recent": {
            "first": first.isoformat(),
            "steps": [r["steps"] for r in recent],
            "asleep": [r["sleep"]["minutes"] if r["sleep"] else None for r in recent],
            "resting": [(r["heartRate"] or {}).get("resting") for r in recent],
            "weightKg": [r["weightKg"] for r in recent],
        },
    }


def today_view(src, log, now):
    habits, entries, start_hour = src.habits()
    day = habit_day(now, start_hour)
    rows = habit_rows(src, habits, entries, day)
    doc = src.rule_state()
    blocks = []
    for b in (doc.get("blocks") or {}).values():
        if isinstance(b, dict):
            blocks.append({**block_view(b, doc, now), "needs": block_needs(b, doc, habits, entries, day)})
    day_ms = ms_of(datetime(day.year, day.month, day.day, start_hour))
    # One stamped after now hasn't happened, and was counted every day until its own came.
    now_ms = ms_of(now)
    hits = [e for e in src.rule_events(day_ms) if e.get("t") == "blocked" and e["at"] <= now_ms]
    agenda = []
    whole_day = []
    if src.calendar.ready:
        lo = datetime(day.year, day.month, day.day)
        hi = lo + timedelta(days=1, hours=start_hour)
        for o in src.calendar.between(day, day + timedelta(days=1)):
            if o["allDay"] or o["end"] >= now:
                agenda.append(occurrence_view(o))
            if not o["allDay"] and o["start"] < hi and o["end"] > lo:
                whole_day.append(occurrence_view(o))
    return {
        "day": day.isoformat(),
        "dayStartHour": start_hour,
        "now": now.isoformat(timespec="minutes"),
        "habits": {**habit_score(rows), "items": rows},
        "recent": recent_days(habits, entries, day),
        "blocks": {"items": blocks, "hits": len(hits)},
        # items: what's still to come, with the all-day ones; day: every timed event of the day,
        # those over too, from its midnight to the next day's start.
        "calendar": {"ready": src.calendar.ready, "items": agenda[:12], "day": whole_day[:40]},
        "countdowns": countdowns(src, now),
        "money": money_view(src.wallet(), now),
        "home": home_view(src.inventory(), now),
        "mail": log.latest(src.user, "xrnmail"),
        "health": body_view(log.db(src.user), day),
    }


def day_view(src, log, now, day):
    """One day in full: its habits, what was on, what else happened, and the body. Health is the
    health table's own day of the same date (the calendar day; a sleep counts on the day it ended),
    as everywhere the pages show it."""
    habits, entries, _ = src.habits()
    rows = habit_rows(src, habits, entries, day)
    agenda = []
    if src.calendar.ready:
        # Something that ran on from the night before belongs to that night's page.
        agenda = [occurrence_view(o) for o in src.calendar.between(day, day) if o["allDay"] or o["start"].date() == day]
    return {
        "day": day.isoformat(),
        "habits": {**habit_score(rows), "items": rows},
        "calendar": {"ready": src.calendar.ready, "items": agenda},
        "log": [e for e in happenings(src, log, now, day, day)["items"] if e["kind"] not in ("habit", "event")],
        "health": health.days(log.db(src.user), day.isoformat(), day.isoformat()).get(day.isoformat()),
    }


def happenings(src, log, now, first, last):
    """What happened across days first..last, newest first. Only finished things: no future
    calendar events."""
    habits, entries, start_hour = src.habits()
    times = src.entry_times
    lo_ms = ms_of(datetime(first.year, first.month, first.day, start_hour))
    after = last + timedelta(days=1)
    hi_ms = min(ms_of(now), ms_of(datetime(after.year, after.month, after.day, start_hour)))
    found = []
    for h in habits:
        for key, value in entries.get(h["id"], {}).items():
            day = parse_day(key)
            if day is None or not first <= day <= last or not habit_done(h, value):
                continue
            noon = ms_of(datetime(day.year, day.month, day.day, 12))
            at = times.get((h["id"], key), noon)
            if abs(at - noon) > 26 * 3600 * 1000:  # ticked long after the day it's for
                at = noon
            found.append({"at": at, "app": "xrnloops", "kind": "habit", "title": h["name"]})
    block_names = {b.get("id"): b.get("name") for b in (src.rule_state().get("blocks") or {}).values() if isinstance(b, dict)}
    for e in src.rule_events(lo_ms):
        t = e.get("t")
        if t == "blocked":
            found.append({"at": e["at"], "app": "xrnrule", "kind": "blocked", "title": str(e.get("name") or e.get("target") or "")})
        elif t == "unlock":
            found.append({
                "at": e["at"], "app": "xrnrule", "kind": "unlock",
                "title": str(block_names.get(e.get("block")) or ""),
                "value": round((e.get("ms") or 0) / 60000),
            })
    wallet = src.wallet()
    names = {c.get("id"): str(c.get("name") or "") for c in wallet.get("categories") or [] if isinstance(c, dict)}
    for b in wallet.get("bills") or []:
        if not isinstance(b, dict):
            continue
        at = b.get("atMillis")
        if isinstance(at, (int, float)) and lo_ms <= at <= hi_ms and b.get("type") in (EXPENSE, INCOME):
            found.append({
                "at": int(at), "app": "xrnwal", "kind": "spent" if b["type"] == EXPENSE else "earned",
                "title": str(b.get("note") or names.get(b.get("categoryId")) or ""),
                "cents": bill_base_cents(b),
            })
    if src.calendar.ready:
        for o in src.calendar.between(first, last):
            if o["allDay"] or o["end"] > now:
                continue
            found.append({"at": ms_of(o["start"]), "app": "xrncal", "kind": "event", "title": o["title"]})
    # state.* is an app saying where it stands, for its card; it isn't something that happened.
    found += [e for e in log.events(src.user, lo_ms) if not e["kind"].startswith("state.")]
    found = [e for e in found if lo_ms <= e["at"] <= hi_ms]
    found.sort(key=lambda e: -e["at"])
    return {"currency": base_currency(wallet), "items": found[:600]}


def day_table(src, now, days=HISTORY_DAYS):
    """One row per finished day, for the long view: which habits were done, and what to show."""
    habits, entries, start_hour = src.habits()
    today = habit_day(now, start_hour)
    stamps = sorted(k for per in entries.values() for k in per)
    if not stamps:
        return []
    first = max(parse_day(stamps[0]) or today, today - timedelta(days=days))
    last = today - timedelta(days=1)
    if first > last:
        return []
    spans = {}
    for h in habits:
        keys = sorted(entries.get(h["id"], {}))
        if keys:
            # One that's still in the list hasn't stopped, however long since its last entry; its
            # quiet days should count against "was this day logged", not shrink the question.
            spans[h["id"]] = (parse_day(keys[0]), parse_day(keys[-1]) if h["archived"] else last)
    rows = []
    for day in days_between(first, last):
        key = day.isoformat()
        done = {}
        logged = 0
        for h in habits:
            span = spans.get(h["id"])
            if not span or not span[0] <= day <= span[1]:
                continue  # before it existed or after it stopped: it says nothing about this day
            value = entries.get(h["id"], {}).get(key)
            if value is not None and value >= 0:
                logged += 1
            if h["type"] == 0 and value == SKIP:
                continue
            ok = habit_done(h, value)
            if ok or is_daily(h):
                done[h["id"]] = ok
        # What a page shows for the day, however little was logged. A tick on an old day has to
        # show up, even when the day is too thin to count below.
        shown = sum(done.values()) / len(done) if logged and done else None
        if logged < math.ceil(len(done) / 3):
            done = {}  # hardly touched: nobody logged this day, which isn't the same as doing nothing
        rows.append({"day": day, "shown": shown, "habits": done})
    return rows

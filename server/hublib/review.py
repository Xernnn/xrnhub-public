"""The long view: a year of days, how strong each habit is, where the hours went, what tempted, and
the body over the same month.

Takes the day table and the same sources as the rest. `now` is an argument, as everywhere.
"""
from collections import Counter
from datetime import datetime, timedelta
from statistics import mean

from . import health
from .daybook import (
    EXPENSE, INCOME, SKIP, accounting_month, base_currency, habit_day, habit_done, habit_state, habit_streak, in_budget,
    is_daily, ms_of, overall_budget, round_half_up, setting, wallet_bills, wallet_settings,
)
from .sources import parse_day

YEAR_DAYS = 371  # 53 whole weeks, so a year's grid always has its first column full
HABIT_DAYS = 30
HOUR_WEEKS = 12
SERIES = 6  # calendars with a colour of their own; the rest are folded together
TEMPTATION_DAYS = 90
TOP_APPS = 6
MAX_EVENT_HOURS = 24  # a timed event left running for days isn't a week of work
DONE, MISSED, SKIPPED = 1, 0, 2
HEALTH_ROWS = ("steps", "asleep", "resting", "weightKg", "spo2", "stress")
TREND_WEEKS = 12
MONEY_MONTHS = 12
MONEY_CATEGORIES = 6


def year(rows, today_entry):
    """Every day that says anything, oldest first."""
    found = [
        {"day": row["day"].isoformat(), "share": round(row["shown"], 3)}
        for row in rows[-(YEAR_DAYS - 1):]
        if row["shown"] is not None
    ]
    if today_entry:
        found.append(today_entry)
    return found


def today_row(habits, entries, today):
    key = today.isoformat()
    states = [habit_state(h, entries.get(h["id"], {}).get(key)) for h in habits if not h["archived"]]
    counted = [s for s in states if s["counts"]]
    done = sum(1 for s in counted if s["done"])
    if not done or not counted:
        return None
    return {"day": key, "share": round(done / len(counted), 3)}


def recent_trend(rows):
    """Habits done this week against last, over days that were logged."""
    def share(chunk):
        scores = [sum(r["habits"].values()) / len(r["habits"]) for r in chunk if len(r["habits"]) >= 2]
        return mean(scores) if scores else None

    this, last = share(rows[-7:]), share(rows[-14:-7])
    if this is None or last is None:
        return None
    return {"thisWeek": round(this * 100), "lastWeek": round(last * 100)}


def weekly_habits(rows):
    """The share of habits done each of the last TREND_WEEKS weeks, as a whole percent, oldest first;
    None for a week with no logged day. The same reading as recent_trend, a week at a time."""
    found = []
    for week in range(TREND_WEEKS, 0, -1):
        chunk = rows[max(0, len(rows) - week * 7):max(0, len(rows) - (week - 1) * 7)]
        scores = [sum(r["habits"].values()) / len(r["habits"]) for r in chunk if len(r["habits"]) >= 2]
        found.append(round(mean(scores) * 100) if scores else None)
    return found


def best_run(habit, per_day):
    """The longest run ever, by the same reading as the streak: a skip passes through."""
    best = run = 0
    last = None
    for key in sorted(per_day):
        day = parse_day(key)
        if day is None:
            continue
        value = per_day[key]
        if last is not None and (day - last).days > 1:
            run = 0
        last = day
        if habit_done(habit, value):
            run += 1
            best = max(best, run)
        elif not (habit["type"] == 0 and value == SKIP):
            run = 0
    return best


def strengths(habits, entries, today):
    """Each habit over the last month, weakest first, since that's the one to look at. The share
    is of the days anything was logged at all: a quiet day says nothing about one habit."""
    days = [today - timedelta(days=back) for back in range(HABIT_DAYS - 1, -1, -1)]
    keys = [d.isoformat() for d in days]
    logged = {
        k for k in keys
        if any(entries.get(h["id"], {}).get(k, -1) >= 0 for h in habits if not h["archived"])
    }
    found = []
    for h in habits:
        if h["archived"]:
            continue
        per_day = entries.get(h["id"], {})
        strip = []
        done = 0
        for key in keys:
            value = per_day.get(key)
            if habit_done(h, value):
                strip.append(DONE)
                done += 1
            elif h["type"] == 0 and value == SKIP:
                strip.append(SKIPPED)
            elif value is not None and value >= 0:
                strip.append(MISSED)
            else:
                strip.append(None)
        found.append({
            "id": h["id"],
            "name": h["name"],
            "color": h["color"],
            "daily": is_daily(h),
            "pct": round(100 * done / len(logged)) if logged else None,
            "streak": habit_streak(h, per_day, today),
            "best": best_run(h, per_day),
            "strip": strip,
        })
    found.sort(key=lambda r: (r["pct"] is None, r["pct"] or 0))
    return {"first": keys[0], "items": found}


def week_hours(src, now):
    """Timed calendar hours per week, by calendar. All-day entries aren't time spent."""
    if not src.calendar.ready:
        return None
    monday = now.date() - timedelta(days=now.weekday())
    first = monday - timedelta(weeks=HOUR_WEEKS - 1)
    per_calendar = {}
    colors = {}
    for o in src.calendar.between(first, now.date()):
        if o["allDay"] or o["end"] > now or o["start"].date() < first:
            continue
        hours = min((o["end"] - o["start"]).total_seconds() / 3600, MAX_EVENT_HOURS)
        week = (o["start"].date() - first).days // 7
        per_calendar.setdefault(o["calendar"], [0.0] * HOUR_WEEKS)[week] += hours
        colors[o["calendar"]] = o["color"]
    ranked = sorted(per_calendar.items(), key=lambda kv: -sum(kv[1]))
    series = [{"name": n, "color": colors[n], "hours": [round(h, 1) for h in w]} for n, w in ranked[:SERIES]]
    if len(ranked) > SERIES:
        rest = [round(sum(w[i] for _, w in ranked[SERIES:]), 1) for i in range(HOUR_WEEKS)]
        series.append({"name": "Other", "color": None, "hours": rest})
    return {
        "weeks": [(first + timedelta(weeks=i)).isoformat() for i in range(HOUR_WEEKS)],
        "series": series,
    }


def temptations(src, now):
    since = now - timedelta(days=TEMPTATION_DAYS)
    week_ago = ms_of(now - timedelta(days=7))
    two_weeks_ago = ms_of(now - timedelta(days=14))
    apps = Counter()
    hours = [0] * 24
    weeks = [0] * TREND_WEEKS  # seven days at a time back from now, oldest first
    total = this_week = last_week = 0
    now_ms = ms_of(now)
    for e in src.rule_events(ms_of(since)):
        # One stamped after now hasn't happened, and may be no time at all: xrnrule takes any number.
        if e.get("t") != "blocked" or e["at"] > now_ms:
            continue
        total += 1
        apps[str(e.get("name") or e.get("target") or "?")] += 1
        hours[datetime.fromtimestamp(e["at"] / 1000).hour] += 1
        back = int((now_ms - e["at"]) // (7 * 86400 * 1000))
        if back < TREND_WEEKS:
            weeks[TREND_WEEKS - 1 - back] += 1
        if e["at"] >= week_ago:
            this_week += 1
        elif e["at"] >= two_weeks_ago:
            last_week += 1
    return {
        "days": TEMPTATION_DAYS,
        "total": total,
        "thisWeek": this_week,
        "lastWeek": last_week,
        "weeks": weeks,
        "apps": [{"name": n, "count": c} for n, c in apps.most_common(TOP_APPS)],
        "hours": hours,
    }


def body(db, today):
    """The health card: the same days as the habit strips, a number or None for each measure on each,
    and each measure's average and latest. A date's health is what the health table files under that
    date, a calendar day (a sleep under the day it ended), though the page's days are habit days: so
    Monday is Monday's steps and the night that ended Monday morning, whatever hour a habit day
    starts. None when nothing in the month was measured."""
    if db is None:
        return None
    first = today - timedelta(days=HABIT_DAYS - 1)
    found = health.days(db, first.isoformat(), today.isoformat())
    if not found:
        return None
    days = []
    for back in range(HABIT_DAYS - 1, -1, -1):
        key = (today - timedelta(days=back)).isoformat()
        rec = found.get(key) or health.blank_day()
        sleep = rec["sleep"]
        days.append({
            "date": key,
            "steps": rec["steps"],
            "asleep": sleep["minutes"] if sleep else None,
            "resting": (rec["heartRate"] or {}).get("resting"),
            "weightKg": rec["weightKg"],
            "spo2": (rec["spo2"] or {}).get("avg"),
            "stress": (rec["stress"] or {}).get("avg"),
        })
    summary = {}
    for name in HEALTH_ROWS:
        have = [(d["date"], d[name]) for d in days if d[name] is not None]
        if not have:
            continue
        # Today's steps are a day still going: counted in, they'd pull the average down all morning.
        settled = [v for day, v in have if not (name == "steps" and day == days[-1]["date"])]
        avg = mean(settled) if settled else None
        if avg is not None:
            # Halves up, as the page writes the latest weighing: one weighing of 72.25 reads the same twice.
            avg = round_half_up(avg * 10) / 10 if name == "weightKg" else round_half_up(avg)
        summary[name] = {"avg": avg, "latest": have[-1][1], "day": have[-1][0]}
    return {"first": days[0]["date"], "days": days, "summary": summary}


def money(wallet, now):
    """Spending a month against the budget, over the last MONEY_MONTHS accounting months, as xrnwal
    counts it (see daybook.money_view): what counted against the budget, what was left out of it (rent
    and the like), and what came in; and this month's counted spending by category, a sub-category
    under its parent as the wallet's category budgets count it. The budget is today's: the wallet
    keeps no history of it. None without a wallet."""
    bills = wallet_bills(wallet)
    if not bills:
        return None
    settings = wallet_settings(wallet)
    today = now.date()
    future = setting(settings, "includeFuture", True)
    months = [accounting_month(settings, today, back) for back in range(MONEY_MONTHS - 1, -1, -1)]
    totals = [{"first": f.isoformat(), "last": l.isoformat(), "spentCents": 0, "outsideCents": 0, "earnedCents": 0}
              for f, l in months]
    categories = {c.get("id"): c for c in wallet.get("categories") or [] if isinstance(c, dict)}

    def top_name(cid):
        c = categories.get(cid) or {}
        parent = categories.get(c.get("parentId"))
        return str((parent or c).get("name") or "")

    this_month = Counter()
    first, last = months[0][0], months[-1][1]
    for b, day, cents in bills:
        if not first <= day <= last or (day > today and not future):
            continue
        i = next(i for i, (f, l) in enumerate(months) if f <= day <= l)
        if b.get("type") == INCOME:
            totals[i]["earnedCents"] += cents
        elif b.get("type") == EXPENSE and in_budget(b):
            totals[i]["spentCents"] += cents
            if i == len(months) - 1:
                this_month[top_name(b.get("categoryId"))] += cents
        elif b.get("type") == EXPENSE:
            totals[i]["outsideCents"] += cents
    ranked = this_month.most_common()
    shown = [{"name": n or "Other", "cents": c} for n, c in ranked[:MONEY_CATEGORIES]]
    rest = sum(c for _, c in ranked[MONEY_CATEGORIES:])
    if rest:
        shown.append({"name": "Other", "cents": rest})
    return {
        "currency": base_currency(wallet),
        "budgetCents": overall_budget(wallet, settings),
        "months": totals,
        "categories": shown,
    }


def build(src, now, rows, db=None):
    """The page. [db] is the hub's own database, for the health card."""
    habits, entries, start_hour = src.habits()
    today = habit_day(now, start_hour)
    logged = [r for r in rows[-HABIT_DAYS:] if r["shown"] is not None]
    return {
        "day": today.isoformat(),
        "year": year(rows, today_row(habits, entries, today)),
        "habits": strengths(habits, entries, today),
        "hours": week_hours(src, now),
        "temptations": temptations(src, now),
        "health": body(db, today),
        "trend": recent_trend(rows),
        "habitWeeks": weekly_habits(rows),
        "money": money(src.wallet(), now),
        "loggedDays": len(logged),
        "windowDays": HABIT_DAYS,
    }

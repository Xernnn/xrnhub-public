"""Nudges: when something's true across the apps, say so on the Today page.

A rule only ever shows a card. It can't change a block (xrnrule's engine is the judge of those and
shares its state with nothing) and it can't touch another app, so a badly written one costs a card
you dismiss. A nudge stays while its conditions hold and goes by itself when they stop; the first
time each day it's also written down in the hub's log (hublib/log.py), which is where hiding it for
the rest of the day is kept. A card's Turn off saves the rule with "on": false.

    {"id": "exam", "name": "Exam soon", "on": true,
     "when": [{"is": "event_soon", "match": "exam", "hours": 48}]}

All of a rule's conditions must hold. `evaluate` is pure: it takes the facts of the moment.
"""
import re
from datetime import timedelta

CONDITIONS = {
    "event_soon": {"match": str, "hours": int},
    "event_now": {"match": str},
    "habits_open": {"after": str, "atLeast": int},
    "habits_done": {"atLeast": int},
    "spent_over": {"amount": int, "per": str},
    "shopping_over": {"count": int},
    "expiring": {"days": int},
    "blocked_over": {"count": int},
    "after": {"time": str},
}
PERIODS = ("day", "month")
RULE_ID = re.compile(r"[a-z0-9-]{1,40}")
TIME = re.compile(r"([01]\d|2[0-3]):[0-5]\d")
MAX_RULES = 40
MAX_TEXT = 80


class BadRule(ValueError):
    pass


def clean_condition(raw):
    if not isinstance(raw, dict) or raw.get("is") not in CONDITIONS:
        raise BadRule("unknown condition")
    kind = raw["is"]
    out = {"is": kind}
    for field, want in CONDITIONS[kind].items():
        value = raw.get(field)
        if want is int:
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 1_000_000:
                raise BadRule(f"{kind}: {field} must be a whole number")
        else:
            if not isinstance(value, str) or not value.strip() or len(value) > MAX_TEXT:
                raise BadRule(f"{kind}: {field} is missing")
            value = value.strip()
        if field in ("after", "time") and not TIME.fullmatch(value):
            raise BadRule(f"{kind}: {field} must look like 20:00")
        if field == "per" and value not in PERIODS:
            raise BadRule(f"{kind}: per must be day or month")
        out[field] = value
    return out


def clean_rules(raw):
    """A rule list as it may be saved. Raises BadRule; unknown fields are dropped."""
    if not isinstance(raw, list) or len(raw) > MAX_RULES:
        raise BadRule("rules must be a short list")
    seen = set()
    out = []
    for rule in raw:
        if not isinstance(rule, dict):
            raise BadRule("a rule must be an object")
        rid = rule.get("id")
        if not isinstance(rid, str) or not RULE_ID.fullmatch(rid) or rid in seen:
            raise BadRule("each rule needs its own id")
        seen.add(rid)
        name = rule.get("name")
        if not isinstance(name, str) or not name.strip() or len(name) > MAX_TEXT:
            raise BadRule("each rule needs a name")
        when = rule.get("when")
        if not isinstance(when, list) or not 1 <= len(when) <= 6:
            raise BadRule("a rule needs at least one condition")
        out.append({
            "id": rid,
            "name": name.strip(),
            "on": bool(rule.get("on", True)),
            "when": [clean_condition(c) for c in when],
        })
    return out


def starter_rules():
    return [
        {"id": "deadline-soon", "name": "Deadline close", "on": True,
         "when": [{"is": "event_soon", "match": "deadline", "hours": 72}]},
        {"id": "exam-soon", "name": "Exam soon", "on": True,
         "when": [{"is": "event_soon", "match": "exam", "hours": 48}]},
        {"id": "late-habits", "name": "Evening, habits open", "on": True,
         "when": [{"is": "habits_open", "after": "20:00", "atLeast": 3}]},
        {"id": "tempted", "name": "Lots of blocked opens", "on": True,
         "when": [{"is": "blocked_over", "count": 5}]},
        {"id": "big-spend", "name": "Big spending day", "on": True,
         "when": [{"is": "spent_over", "amount": 100, "per": "day"}]},
        {"id": "shopping-pile", "name": "Shopping piling up", "on": True,
         "when": [{"is": "shopping_over", "count": 5}]},
    ]


def facts(today, soon, now):
    """What rules look at, from a today page and the calendar's next days."""
    habits = today["habits"]
    # Spending as the budget counts it: rent and the like, left out of the budget in xrnwal, is
    # no big spending day.
    budget = (today.get("money") or {}).get("budget") or {}
    home = today.get("home") or {}
    return {
        "now": now,
        "habits_done": habits["done"],
        "habits_total": habits["total"],
        "events": soon,
        "spent_day": (budget.get("todayCents") or 0) / 100,
        "spent_month": (budget.get("spentCents") or 0) / 100,
        "shopping": len(home.get("shopping") or []),
        "expiring": home.get("expiring") or [],
        "hits": today["blocks"]["hits"],
    }


def past(now, hhmm):
    hour, minute = map(int, hhmm.split(":"))
    return now.hour * 60 + now.minute >= hour * 60 + minute


def check(cond, f):
    """(holds, what about it) for one condition."""
    kind = cond["is"]
    now = f["now"]
    if kind in ("event_soon", "event_now"):
        needle = cond["match"].lower()
        horizon = now + timedelta(hours=cond.get("hours", 0))
        for e in f["events"]:
            if needle not in e["title"].lower() and needle not in e.get("calendar", "").lower():
                continue
            if e["allDay"] and kind == "event_now":
                if e["start"].date() <= now.date() < e["end"].date():
                    return True, e["title"]
            elif kind == "event_now":
                if e["start"] <= now < e["end"]:
                    return True, e["title"]
            elif e["start"] <= horizon and e["end"] > now:  # on now counts as soon
                return True, e["title"]
        return False, ""
    if kind == "habits_open":
        left = f["habits_total"] - f["habits_done"]
        return past(now, cond["after"]) and left >= cond["atLeast"], f"{left} open"
    if kind == "habits_done":
        total = f["habits_total"]
        share = 100 * f["habits_done"] / total if total else 0
        return bool(total) and share >= cond["atLeast"], f"{f['habits_done']} of {total}"
    if kind == "spent_over":
        spent = f[f"spent_{cond['per']}"]
        return spent > cond["amount"], f"{spent:.0f} spent"
    if kind == "shopping_over":
        return f["shopping"] >= cond["count"], f"{f['shopping']} to buy"
    if kind == "expiring":
        close = [e for e in f["expiring"] if e["days"] <= cond["days"]]
        return bool(close), close[0]["name"] if close else ""
    if kind == "blocked_over":
        return f["hits"] >= cond["count"], f"{f['hits']} blocked"
    if kind == "after":
        return past(now, cond["time"]), cond["time"]
    return False, ""


def evaluate(rule, f):
    """(fires, the reasons), for one rule at one moment."""
    if not rule.get("on", True):
        return False, []
    reasons = []
    for cond in rule["when"]:
        holds, why = check(cond, f)
        if not holds:
            return False, []
        if why:
            reasons.append(why)
    return True, reasons


def run_due(rules, f, day, log, user):
    """Writes down each rule that fires, once a day per rule. Returns those new today."""
    fired = []
    for rule in rules:
        hit, reasons = evaluate(rule, f)
        if hit and log.note_fired(user, rule["id"], rule["name"], day, reasons, "remind"):
            fired.append(rule["id"])
    return fired


def live(rules, f, dismissed):
    """The nudges to show this moment."""
    found = []
    for rule in rules:
        hit, reasons = evaluate(rule, f)
        if hit and rule["id"] not in dismissed:
            found.append({"id": rule["id"], "name": rule["name"], "why": reasons})
    return found


def soon_list(src, now, days=8):
    """The calendar's next days as rules read them, naive local datetimes kept."""
    if not src.calendar.ready:
        return []
    first = now.date()
    return src.calendar.between(first, first + timedelta(days=days))


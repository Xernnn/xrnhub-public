"""The personal API: each xrn app's records as plain JSON, read-only, under /api/v1.

The pages' own endpoints (/api/today, /api/review) answer with what a page shows. These answer with
the records themselves, so a script or an assistant can ask its own questions: every transaction
since March, each habit entry this month, what's on next week. Like the rest of the hub they read
the services' files and change nothing; health is the one source the hub keeps itself, from what
xrnfit posts. xrntube's blocklist isn't here, and mail stays on the laptop.

The same rules hold everywhere (docs/api.md has the whole list):

- A moment is ISO 8601 with this machine's offset, "2026-11-15T16:04:00+11:00". A whole day is
  "2026-11-15".
- `from` and `to` are days, both included. Habits count habit days, which start at xrnloops' day
  start hour; everything else counts days on this machine's clock.
- Money is whole cents beside its currency code.
- Every answer is an object, so a field can be added without breaking a caller.

What leaves here is chosen field by field, never passed through: xrnrule's settings hold the
sentence that buys extra minutes, and xrnwal's hold its PIN's hash.
"""
import math
import os
import re
from datetime import date, datetime, time, timedelta

from . import daybook, health
from .daybook import (
    base_currency, bill_base_cents, block_needs, block_view, habit_day, habit_done, habit_streak, number, round_half_up,
)
from .sources import insta_songs

VERSION = 1
MAX_CALENDAR_DAYS = 400  # repeating events are worked out for every day asked for
MAX_HEALTH_DAYS = 400
MAX_HEART_DAYS = 7  # a sample a minute is 10,000 a week
MAX_STEP_BUCKETS = 10_000  # a week by the minute, or 56 days by the half hour with room to spare
DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
MARKS = {daybook.NO: "no", daybook.YES_AUTO: "auto", daybook.YES_MANUAL: "yes", daybook.SKIP: "skip"}
BILL_TYPES = {0: "expense", 1: "income", 2: "transfer"}
CATEGORY_TYPES = {0: "expense", 1: "income"}
ACCOUNT_KINDS = {0: "savings", 1: "credit"}
CREDIT = 1
RULE_EVENT_FIELDS = ("name", "target", "ms", "start")


class BadRequest(ValueError):
    pass


class Ask:
    """One request: who, when, and its query string. [owner] is whether it's the server's owner, the
    first user in the users file: some sources are one person's and aren't kept per user."""

    def __init__(self, src, log, user, now, query, owner=True):
        self.src = src
        self.log = log
        self.user = user
        self.now = now
        self.query = query  # (name, fallback) -> the raw value
        self.owner = owner


ROUTES = {}


def route(path, about, params=""):
    def wrap(fn):
        ROUTES[path] = (fn, about, params)
        return fn

    return wrap


def answer(path, ask):
    """The answer for [path], or None when there's no such endpoint. Raises BadRequest."""
    hit = ROUTES.get(path.rstrip("/") or "/")
    return hit[0](ask) if hit else None


# ---- the shared rules --------------------------------------------------------------------------


def moment(ms):
    """Epoch milliseconds as ISO 8601 with the offset; None for a number no clock reaches."""
    if isinstance(ms, bool) or not isinstance(ms, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(ms / 1000).astimezone().isoformat(timespec="seconds")
    except (OverflowError, ValueError, OSError):
        return None


def local(when):
    """A naive local datetime as ISO 8601 with the offset it had then."""
    return when.astimezone().isoformat(timespec="seconds")


def ms_at(day):
    return int(datetime.combine(day, time()).timestamp() * 1000)


def day_param(ask, name, fallback):
    text = ask.query(name, "")
    if not text:
        return fallback
    if not DAY.fullmatch(text):
        raise BadRequest(f"{name} must be a day, as 2026-11-15")
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise BadRequest(f"{name} isn't a day there is") from None


def day_range(ask, today, back, ahead=0, most=None):
    first = day_param(ask, "from", today - timedelta(days=back))
    last = day_param(ask, "to", today + timedelta(days=ahead))
    if first > last:
        raise BadRequest("from is after to")
    if most and (last - first).days + 1 > most:
        raise BadRequest(f"at most {most} days at once")
    return first, last


def text(value, fallback=""):
    return value if isinstance(value, str) else fallback


def updated(*paths):
    """When the newest of a service's files last changed, or None if it has none."""
    stamps = []
    for path in paths:
        try:
            stamps.append(os.stat(path).st_mtime)
        except OSError:
            pass
    return moment(max(stamps) * 1000) if stamps else None


# ---- the index ---------------------------------------------------------------------------------


@route("/api/v1", "what's here: each endpoint, and what each source holds")
def index(ask):
    src = ask.src
    habits, _, start_hour = src.habits()
    wallet = src.wallet()
    rule = src.rule_state()
    loops = src.path("xrnloops", "loop.sqlite")
    return {
        "version": VERSION,
        "user": ask.user,
        "now": local(ask.now),
        "dayStartHour": start_hour,
        "sources": {
            "habits": {"from": "xrnloops", "habits": len(habits), "updated": updated(loops, loops + "-wal")},
            "calendar": {
                "from": "Radicale", "ready": src.calendar.ready, "calendars": len(src.calendar.calendars),
                "updated": moment(src.calendar.swept_at * 1000) if src.calendar.ready else None,
            },
            "money": {
                "from": "xrnwal", "transactions": len(listed(wallet, "bills")),
                "updated": updated(src.path("xrnwal", "state.json")),
            },
            "inventory": {
                "from": "xrnhome", "items": len(src.inventory()), "updated": updated(src.path("xrnhome", "state.json")),
            },
            "songs": {
                "from": "xrninsta", "songs": len(insta_songs(src.home)[0]) if ask.owner else 0,
                "updated": updated(*insta_files(src.home)) if ask.owner else None,
            },
            "blocks": {
                "from": "xrnrule", "blocks": len(dicts(rule.get("blocks"))),
                "updated": updated(src.path("xrnrule", "state.json"), src.path("xrnrule", "events.jsonl")),
            },
            "health": health_source(ask),
        },
        "endpoints": [{"path": p, "about": about, "params": params} for p, (_, about, params) in ROUTES.items()],
    }


def listed(doc, key):
    found = doc.get(key) if isinstance(doc, dict) else None
    return [x for x in found if isinstance(x, dict)] if isinstance(found, list) else []


def dicts(value):
    """A dict of records keyed by id, as xrnrule keeps them, as a list."""
    return [x for x in value.values() if isinstance(x, dict)] if isinstance(value, dict) else []


# ---- habits ------------------------------------------------------------------------------------


def habit_record(h):
    measurable = h["type"] == 1
    rec = {
        "id": h["id"],
        "name": h["name"],
        "question": h["question"],
        "description": h["description"],
        "kind": "measurable" if measurable else "yesno",
        "frequency": {"times": h["freqNum"], "days": h["freqDen"]},
        "archived": h["archived"],
    }
    if measurable:
        rec["target"] = {"value": h["targetValue"], "unit": h["unit"], "atMost": h["targetType"] == 1}
    return rec


@route("/api/v1/habits", "every habit, with today's mark and the current streak")
def habits(ask):
    found, entries, start_hour = ask.src.habits()
    today = habit_day(ask.now, start_hour)
    out = []
    for h in found:
        per_day = entries.get(h["id"], {})
        value = per_day.get(today.isoformat())
        out.append({
            **habit_record(h),
            "today": entry_mark(h, value) if value is not None and value >= 0 else None,
            "streak": habit_streak(h, per_day, today),
        })
    return {"today": today.isoformat(), "dayStartHour": start_hour, "habits": out}


def entry_mark(h, value):
    rec = {"done": habit_done(h, value)}
    if h["type"] == 1:
        rec["amount"] = value / 1000
    else:
        rec["mark"] = MARKS.get(value, "unknown")
    return rec


@route("/api/v1/habits/entries", "each habit's entries, day by day", "from, to (default: the last 30 days), habit")
def habit_entries(ask):
    src = ask.src
    found, entries, start_hour = src.habits()
    notes = src.entry_notes
    first, last = day_range(ask, habit_day(ask.now, start_hour), back=29)
    only = ask.query("habit", "")
    if only and only not in {h["id"] for h in found}:
        raise BadRequest("no such habit")
    lo, hi = first.isoformat(), last.isoformat()
    out = []
    for order, h in enumerate(found):
        if only and h["id"] != only:
            continue
        for day, value in entries.get(h["id"], {}).items():
            if not (lo <= day <= hi) or not DAY.fullmatch(day) or value < 0:
                continue
            rec = {"habit": h["id"], "name": h["name"], "date": day, **entry_mark(h, value)}
            note = notes.get((h["id"], day))
            if note:
                rec["note"] = note
            out.append((day, order, rec))
    out.sort(key=lambda r: r[:2])
    return {"from": lo, "to": hi, "entries": [r for _, _, r in out]}


# ---- calendar ----------------------------------------------------------------------------------


@route("/api/v1/calendar", "events in a range, repeats worked out",
       f"from, to (default: today and the next 30 days; at most {MAX_CALENDAR_DAYS} days)")
def calendar(ask):
    cal = ask.src.calendar
    first, last = day_range(ask, ask.now.date(), back=0, ahead=30, most=MAX_CALENDAR_DAYS)
    events = []
    for o in cal.between(first, last) if cal.ready else []:
        if o["allDay"]:
            start, end = o["start"].date().isoformat(), o["end"].date().isoformat()
        else:
            start, end = local(o["start"]), local(o["end"])
        events.append({
            "title": o["title"], "calendar": o["calendar"], "allDay": o["allDay"], "start": start, "end": end,
            "location": o.get("location", ""), "description": o.get("description", ""),
        })
    calendars = sorted(({"name": c["name"], "color": c["color"]} for c in cal.calendars.values()), key=lambda c: c["name"])
    return {"from": first.isoformat(), "to": last.isoformat(), "ready": cal.ready, "calendars": calendars,
            "events": events}


# ---- money -------------------------------------------------------------------------------------


def good_rate(value):
    """A rate the sums can use: a stored rate that's zero, negative or not finite counts as 1, as a
    missing one does in the app."""
    rate = number(value, 1.0)
    return rate if math.isfinite(rate) and rate > 0 else 1.0


class Wallet:
    """The wallet's records with names put in, and balances worked out as the app does
    (Snapshot.kt's balance(), effectOn() and depositsInto()). If one changes there, change it here."""

    def __init__(self, doc):
        self.doc = doc
        self.base = base_currency(doc)
        self.rates = {r.get("code"): good_rate(r.get("rate")) for r in listed(doc, "rates")}
        self.accounts = listed(doc, "accounts")
        self.bills = [b for b in listed(doc, "bills") if isinstance(b.get("atMillis"), (int, float))]
        self.categories = {c.get("id"): c for c in listed(doc, "categories")}
        self.account_names = {a.get("id"): text(a.get("name")) for a in self.accounts}
        self.ledger_names = {x.get("id"): text(x.get("name")) for x in listed(doc, "ledgers")}
        tag_names = {t.get("id"): text(t.get("name")) for t in listed(doc, "tags")}
        self.tags = {}
        for bt in listed(doc, "billTags"):
            if bt.get("tagId") in tag_names:
                self.tags.setdefault(bt.get("billId"), []).append(tag_names[bt["tagId"]])

    def rate(self, code):
        return 1.0 if code == self.base else self.rates.get(code, 1.0)

    def category(self, cid):
        """(name, its parent's name) for a category id."""
        c = self.categories.get(cid)
        if not c:
            return None, None
        parent = self.categories.get(c.get("parentId"))
        return text(c.get("name")), text(parent.get("name")) if parent else None

    def in_account_currency(self, bill, account, cents):
        if bill.get("currency") == account.get("currency"):
            return cents
        return round_half_up(round_half_up(cents * good_rate(bill.get("rate"))) * (1.0 / self.rate(account.get("currency"))))

    def landed(self, bill, account):
        if bill.get("currency") == account.get("currency"):
            return number(bill.get("amountCents"))
        fixed = bill.get("toAmountCents")
        return fixed if isinstance(fixed, int) else self.in_account_currency(bill, account, number(bill.get("amountCents")))

    def effect_on(self, bill, account):
        aid, kind, total = account.get("id"), bill.get("type"), 0
        if kind == 0 and bill.get("accountId") == aid:
            total -= self.landed(bill, account)
        elif kind == 1 and bill.get("accountId") == aid:
            total += self.landed(bill, account)
        elif kind == 2:
            if bill.get("accountId") == aid:
                cents = number(bill.get("amountCents")) + number(bill.get("feeCents"))
                total -= self.in_account_currency(bill, account, cents)
            if bill.get("toAccountId") == aid:
                total += self.landed(bill, account)
        return total

    def deposits_into(self, account):
        total = 0
        deposits = listed(self.doc, "deposits")
        for plan in listed(self.doc, "plans"):
            if plan.get("accountId") != account.get("id"):
                continue
            cents = sum(number(d.get("amountCents")) for d in deposits if d.get("planId") == plan.get("id"))
            if plan.get("currency") == account.get("currency"):
                total += cents
            else:
                total += round_half_up(round_half_up(cents * self.rate(plan.get("currency"))) * (1.0 / self.rate(account.get("currency"))))
        return total

    def balance(self, account):
        return number(account.get("openingCents")) + sum(self.effect_on(b, account) for b in self.bills) + self.deposits_into(account)


def month_start_day(doc):
    settings = read_settings(doc)
    day = settings.get("startDay")
    return day if isinstance(day, int) and 1 <= day <= 31 else 1


def read_settings(doc):
    settings = doc.get("settings")
    if isinstance(settings, str):
        import json

        try:
            settings = json.loads(settings)
        except ValueError:
            settings = None
    return settings if isinstance(settings, dict) else {}


def epoch_day(value):
    """The wallet's days since 1970 as a day, or None."""
    if not isinstance(value, int) or isinstance(value, bool):
        return None
    try:
        return (date(1970, 1, 1) + timedelta(days=value)).isoformat()
    except OverflowError:
        return None


@route("/api/v1/money", "accounts with balances, net worth, categories, budgets, repeating bills, savings plans")
def money(ask):
    w = Wallet(ask.src.wallet())
    accounts, assets, debts = [], 0, 0
    for a in w.accounts:
        bal = w.balance(a)
        in_base = round_half_up(bal * w.rate(a.get("currency")))
        if a.get("includeInTotal", True) and not a.get("archived"):
            if a.get("kind") == CREDIT or in_base < 0:
                debts += in_base
            else:
                assets += in_base
        accounts.append({
            "id": a.get("id"), "name": text(a.get("name")), "kind": ACCOUNT_KINDS.get(a.get("kind"), "savings"),
            "currency": text(a.get("currency"), w.base), "balanceCents": bal, "balanceBaseCents": in_base,
            "inNetWorth": bool(a.get("includeInTotal", True)), "archived": bool(a.get("archived")),
            "note": text(a.get("note")),
        })
    categories = []
    for c in w.categories.values():
        name, parent = w.category(c.get("id"))
        categories.append({"id": c.get("id"), "name": name, "type": CATEGORY_TYPES.get(c.get("type"), "expense"),
                           "parent": parent, "active": bool(c.get("active", True))})
    budgets = [{"category": w.category(b.get("categoryId"))[0] if b.get("categoryId") is not None else None,
                "amountCents": number(b.get("amountCents"))} for b in listed(w.doc, "budgets")]
    recurring = []
    for r in listed(w.doc, "recurring"):
        start = r.get("startEpochDay")
        recurring.append({
            "type": BILL_TYPES.get(r.get("type"), "expense"), "amountCents": number(r.get("amountCents")),
            "currency": text(r.get("currency"), w.base), "category": w.category(r.get("categoryId"))[0],
            "account": w.account_names.get(r.get("accountId")), "note": text(r.get("note")),
            "starts": epoch_day(start),
            "repeats": {"kind": text(r.get("repeatKind")), "value": number(r.get("repeatValue"), 1), "every": number(r.get("every"), 1)},
            "times": number(r.get("times")) or None, "made": number(r.get("generated")),
        })
    deposits = listed(w.doc, "deposits")
    savings = [{
        "name": text(p.get("name")), "targetCents": number(p.get("targetCents")), "currency": text(p.get("currency"), w.base),
        "savedCents": number(p.get("startCents")) + sum(number(d.get("amountCents")) for d in deposits if d.get("planId") == p.get("id")),
        "account": w.account_names.get(p.get("accountId")), "note": text(p.get("note")),
    } for p in listed(w.doc, "plans")]
    return {
        "currency": w.base,
        "monthStartsOn": month_start_day(w.doc),
        "netWorth": {"assetsCents": assets, "debtsCents": debts, "netCents": assets + debts},
        "accounts": accounts,
        "categories": sorted(categories, key=lambda c: (c["type"], c["parent"] or c["name"], c["parent"] is not None, c["name"])),
        "budgets": budgets,
        "recurring": recurring,
        "savings": savings,
        "ledgers": [{"id": k, "name": v} for k, v in w.ledger_names.items()],
        "rates": {code: rate for code, rate in w.rates.items() if isinstance(code, str)},
    }


@route("/api/v1/money/transactions", "expenses, income and transfers in a range, with totals",
       "from, to (default: the last 30 days)")
def transactions(ask):
    w = Wallet(ask.src.wallet())
    first, last = day_range(ask, ask.now.date(), back=29)
    lo, hi = ms_at(first), ms_at(last + timedelta(days=1))
    out, spent, earned = [], 0, 0
    for b in sorted(w.bills, key=lambda b: b["atMillis"]):
        if not lo <= b["atMillis"] < hi:
            continue
        kind = BILL_TYPES.get(b.get("type"), "unknown")
        category, parent = w.category(b.get("categoryId"))
        in_base = bill_base_cents(b)
        rec = {
            "id": b.get("id"), "at": moment(b["atMillis"]), "type": kind,
            "amountCents": number(b.get("amountCents")), "currency": text(b.get("currency"), w.base), "baseCents": in_base,
            "category": category, "parentCategory": parent, "account": w.account_names.get(b.get("accountId")),
            "ledger": w.ledger_names.get(b.get("ledgerId")), "note": text(b.get("note")), "tags": w.tags.get(b.get("id"), []),
            "inBudget": bool(b.get("inBudget", True)), "repeating": b.get("recurringId") is not None,
            "hasPhoto": bool(b.get("photo")),
        }
        if kind == "transfer":
            rec["toAccount"] = w.account_names.get(b.get("toAccountId"))
            rec["feeCents"] = number(b.get("feeCents"))
        if isinstance(b.get("toAmountCents"), int):
            rec["toAmountCents"] = b["toAmountCents"]
        if kind == "expense":
            spent += in_base
        elif kind == "income":
            earned += in_base
        out.append(rec)
    return {"from": first.isoformat(), "to": last.isoformat(), "currency": w.base,
            "totals": {"expenseCents": spent, "incomeCents": earned}, "transactions": out}


# ---- inventory ---------------------------------------------------------------------------------


@route("/api/v1/inventory", "everything in the home inventory, with what's on the shopping list and each item's history")
def inventory(ask):
    out = []
    for item in ask.src.inventory():
        out.append({
            "id": text(item.get("id")), "name": text(item.get("name")), "room": text(item.get("room")),
            "category": text(item.get("category")), "quantity": number(item.get("quantity")), "unit": text(item.get("unit")),
            "openedPercent": number(item.get("openedPercent"), None), "expires": text(item.get("expiryDate"), None),
            "onShoppingList": bool(item.get("wanted")) and not item.get("snoozed"),
            "restocksItself": bool(item.get("recurring")), "note": text(item.get("note")),
            "created": text(item.get("createdAt"), None), "updated": text(item.get("updatedAt"), None),
            "hasPhoto": bool(item.get("photo")),
            "history": [{
                "date": text(e.get("date")), "action": text(e.get("action")),
                "quantityBefore": number(e.get("quantityBefore"), None), "quantityAfter": number(e.get("quantityAfter"), None),
            } for e in listed(item, "history")],
        })
    out.sort(key=lambda i: (i["room"], i["category"], i["name"].lower()))
    return {"items": out}


# ---- songs ---------------------------------------------------------------------------------------


def insta_files(home):
    path = os.path.join(home, "xrninsta", "data", "xrninsta.sqlite")
    return path, path + "-wal"


@route("/api/v1/songs", "the song-of-the-day list (xrninsta): each day's song, and what's on hold")
def songs(ask):
    found, held = insta_songs(ask.src.home) if ask.owner else ([], [])
    out = [{
        "id": s["id"], "day": s["day"], "title": text(s["title"]), "artists": text(s["artists"]), "genre": text(s["genre"]),
        "added": moment(s["added"]) if s["added"] is not None else None,
    } for s in found]
    genres = {}
    for s in out:
        genres[s["genre"]] = genres.get(s["genre"], 0) + 1
    return {
        "latestDay": max((s["day"] for s in out), default=None),
        "genres": dict(sorted(genres.items(), key=lambda kv: -kv[1])),
        "songs": out,
        "onHold": [{"id": h["id"], "title": text(h["title"]), "artists": text(h["artists"]), "note": text(h["note"]),
                    "added": moment(h["added"]) if h["added"] is not None else None} for h in held],
    }


# ---- blocks ------------------------------------------------------------------------------------


@route("/api/v1/blocks", "xrnrule's blocks: what each covers, whether it's open, today's use, and the habits it waits for")
def blocks(ask):
    src = ask.src
    doc = src.rule_state()
    found, entries, start_hour = src.habits()
    day = habit_day(ask.now, start_hour)
    names = {h["id"]: h["name"] for h in found}
    out = []
    for b in dicts(doc.get("blocks")):
        view = block_view(b, doc, ask.now)
        out.append({
            "id": text(b.get("id")), "name": view["name"], "enabled": b.get("enabled") is not False,
            "mode": text(b.get("mode"), "HABIT").lower(), "state": view["state"],
            "usedMs": view["usedMs"], "limitMs": view["limitMs"] or None, "until": moment(view["until"]) if view["until"] else None,
            "apps": strings(b.get("targetPackages")), "sites": strings(b.get("targetSites")),
            "exceptSites": strings(b.get("exceptSites")), "desktopApps": strings(b.get("targetDesktopApps")),
            "needs": [{**n, "habitName": names.get(n["habit"])} for n in block_needs(b, doc, found, entries, day)],
        })
    out.sort(key=lambda b: b["name"].lower())
    return {"day": day.isoformat(), "blocks": out}


def strings(value):
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


@route("/api/v1/blocks/events", "what the devices logged: blocked opens, unlocks, habits done, breaks",
       "from, to (default: the last 7 days; xrnrule keeps 120)")
def block_events(ask):
    src = ask.src
    first, last = day_range(ask, ask.now.date(), back=6)
    lo, hi = ms_at(first), ms_at(last + timedelta(days=1))
    names = {b.get("id"): text(b.get("name")) for b in dicts(src.rule_state().get("blocks"))}
    out = []
    for e in src.rule_events(lo):
        if e["at"] >= hi:
            continue
        rec = {"at": moment(e["at"]), "type": text(e.get("t"), "unknown"), "device": text(e.get("d"), None)}
        if isinstance(e.get("block"), str):
            rec["blockId"] = e["block"]
            rec["block"] = names.get(e["block"])
        for key in RULE_EVENT_FIELDS:
            value = e.get(key)
            if isinstance(value, str) or (isinstance(value, (int, float)) and not isinstance(value, bool)):
                rec[key] = moment(value) if key == "start" else value
        out.append((e["at"], rec))
    out.sort(key=lambda r: r[0])
    return {"from": first.isoformat(), "to": last.isoformat(), "events": [r for _, r in out]}


# ---- the hub's own log -------------------------------------------------------------------------


@route("/api/v1/events", "what apps reported to the hub, such as xrnmail's waiting counts",
       "from, to (default: the last 7 days), app")
def hub_events(ask):
    first, last = day_range(ask, ask.now.date(), back=6)
    app = ask.query("app", "")
    rows = ask.log.between(ask.user, ms_at(first), ms_at(last + timedelta(days=1)), app or None)
    for r in rows:
        r["at"] = moment(r["at"])
    return {"from": first.isoformat(), "to": last.isoformat(), "events": rows}



# ---- health ------------------------------------------------------------------------------------


def health_source(ask):
    rows, received = health.size(ask.log.db(ask.user)) if ask.log else (0, None)
    return {"from": "xrnfit", "records": rows, "updated": moment(received) if received else None}


def sleep_record(day, source, start, end, stages):
    return {
        "day": day, "start": moment(start), "end": moment(end), "minutes": health.stage_minutes(start, end, stages)["minutes"],
        "source": source, "stages": [{"start": moment(lo), "end": moment(hi), "stage": name} for lo, hi, name in stages],
    }


def weighing(at, kg, impedance, source=None, body=None):
    rec = {"at": moment(at), "kg": kg}
    if impedance is not None:
        rec["impedance"] = impedance
    if source is not None:
        rec["source"] = source
    if body:
        rec["body"] = body
    return rec


@route("/api/v1/health", "today's steps, distance and active energy, last night's sleep, heart rate, the latest "
       "weight, SpO2 and stress, "
       "and how much is kept")
def health_now(ask):
    db = ask.log.db(ask.user)
    today = ask.now.date()
    day = today.isoformat()
    yesterday = (today - timedelta(days=1)).isoformat()
    sessions = health.sleep_sessions(db, yesterday, day)
    nights = [s for s in sessions if s[0] == day] or [s for s in sessions if s[0] == yesterday]
    night = max(nights, key=lambda s: health.stage_minutes(s[2], s[3], s[4])["minutes"], default=None)
    last_beat = health.latest(db, "heart_rate")
    resting = health.latest_resting(db)
    heart = None
    if last_beat or resting is not None:
        heart = {"latest": {"at": moment(last_beat[0]), "bpm": health.whole(last_beat[1])} if last_beat else None,
                 "resting": resting}
    weight = health.latest(db, "weight")
    if weight:
        weight = weighing(weight[0], health.whole(weight[1]), health.impedance_of(weight[2]), body=health.body_of(weight[2]))
    spo2, stress = health.latest(db, "spo2"), health.latest(db, "stress")
    today_sums = health.days(db, day, day).get(day, {})
    return {
        "today": day,
        **{key: today_sums.get(key) for key in ("steps", "distance", "activeEnergy")},
        "sleep": sleep_record(*night) if night else None,
        "heartRate": heart,
        "weight": weight,
        "spo2": {"at": moment(spo2[0]), "percent": health.whole(spo2[1])} if spo2 else None,
        "stress": {"at": moment(stress[0]), "level": health.whole(stress[1])} if stress else None,
        "held": health.held(db),
    }


@route("/api/v1/health/days", "steps, distance, active energy, sleep, heart rate, weight, SpO2 and stress, day by day",
       f"from, to (default: the last 30 days; at most {MAX_HEALTH_DAYS} days)")
def health_days(ask):
    first, last = day_range(ask, ask.now.date(), back=29, most=MAX_HEALTH_DAYS)
    found = health.days(ask.log.db(ask.user), first.isoformat(), last.isoformat())
    out = []
    day = first
    while day <= last:
        rec = found.get(day.isoformat()) or health.blank_day()
        sleep = rec["sleep"]
        if sleep:
            sleep = {**sleep, "start": moment(sleep["start"]), "end": moment(sleep["end"])}
        out.append({"date": day.isoformat(), "steps": rec["steps"], "sleep": sleep, "heartRate": rec["heartRate"],
                    "weightKg": rec["weightKg"], "spo2": rec["spo2"], "stress": rec["stress"],
                    "distance": rec["distance"], "activeEnergy": rec["activeEnergy"]})
        day += timedelta(days=1)
    return {"from": first.isoformat(), "to": last.isoformat(), "days": out}


@route("/api/v1/health/heart-rate", "every heart-rate sample",
       f"from, to (default: today; at most {MAX_HEART_DAYS} days)")
def health_heart_rate(ask):
    first, last = day_range(ask, ask.now.date(), back=0, most=MAX_HEART_DAYS)
    samples = health.heart_rate(ask.log.db(ask.user), first.isoformat(), last.isoformat())
    return {"from": first.isoformat(), "to": last.isoformat(),
            "samples": [{"at": moment(at), "bpm": bpm} for at, bpm in samples]}


@route("/api/v1/health/spo2", "every SpO2 sample", f"from, to (default: today; at most {MAX_HEART_DAYS} days)")
def health_spo2(ask):
    first, last = day_range(ask, ask.now.date(), back=0, most=MAX_HEART_DAYS)
    found = health.samples(ask.log.db(ask.user), "spo2", first.isoformat(), last.isoformat())
    return {"from": first.isoformat(), "to": last.isoformat(),
            "samples": [{"at": moment(at), "percent": value} for at, value in found]}


@route("/api/v1/health/stress", "every stress sample", f"from, to (default: today; at most {MAX_HEART_DAYS} days)")
def health_stress(ask):
    first, last = day_range(ask, ask.now.date(), back=0, most=MAX_HEART_DAYS)
    found = health.samples(ask.log.db(ask.user), "stress", first.isoformat(), last.isoformat())
    return {"from": first.isoformat(), "to": last.isoformat(),
            "samples": [{"at": moment(at), "level": value} for at, value in found]}


@route("/api/v1/health/steps", "steps summed in buckets of a few minutes, from each day's midnight",
       f"from, to (default: today), every (minutes, 1-1440, default 60; at most {MAX_STEP_BUCKETS} buckets)")
def health_steps(ask):
    first, last = day_range(ask, ask.now.date(), back=0, most=MAX_HEALTH_DAYS)
    asked = ask.query("every", "60")
    if not asked.isdigit() or not 1 <= int(asked) <= 1440:
        raise BadRequest("every must be whole minutes from 1 to 1440")
    every = int(asked)
    days = (last - first).days + 1
    if days * math.ceil(1440 / every) > MAX_STEP_BUCKETS:
        raise BadRequest(f"at most {MAX_STEP_BUCKETS} buckets at once: fewer days, or a bigger every")
    found = health.step_buckets(ask.log.db(ask.user), first.isoformat(), last.isoformat(), every)
    return {"from": first.isoformat(), "to": last.isoformat(), "every": every,
            "buckets": [[at, steps] for at, steps in found]}


@route("/api/v1/health/sleep", "each sleep with its stages", "from, to (default: the last 30 days)")
def health_sleep(ask):
    first, last = day_range(ask, ask.now.date(), back=29)
    sessions = health.sleep_sessions(ask.log.db(ask.user), first.isoformat(), last.isoformat())
    return {"from": first.isoformat(), "to": last.isoformat(), "sessions": [sleep_record(*s) for s in sessions]}


@route("/api/v1/health/weight", "every weighing", "from, to (default: the last 365 days)")
def health_weight(ask):
    first, last = day_range(ask, ask.now.date(), back=364)
    found = health.weighings(ask.log.db(ask.user), first.isoformat(), last.isoformat())
    return {"from": first.isoformat(), "to": last.isoformat(), "weighings": [weighing(*w) for w in found]}

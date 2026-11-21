// The Today page: the day at a glance. A row of tiles (the budget, the body, mail), the habits to
// tick beside the day's calendar, then screen time and home. Each part is a slot that only redraws
// when its own data changed, so a refresh never moves focus or scroll.

import { SignedOut, addDays, api, card, clock, clockOf, dateOf, dayPlain, dayShort, fill, getJson, h, isoOf, money, postJson, span, svg, toast } from "./dom.js";
import { average, bars, burnUp, figure, readings, ribbon, tile } from "./days-kit.js";
import { habitGrid, score, scoreRing, writesPending } from "./habits.js";

const RELOAD_AFTER_TAP_MS = 1200;
const NEXT_ROWS = 5;
const CHIPS_ALL_DAY = 3;
const STALE_MS = 6 * 3600000; // health that hasn't come in for this long says when it last did

const state = {
  today: null,
  picked: null,
  day: null, // the picked day, when it isn't today
  grid: null,
  rows: null, // the rows the grid on screen is holding
  syncHabits: null,
  slots: {},
  drawn: {},
  view: null,
  target: null,
  reloadTimer: 0,
  asked: 0,
  rulesOpen: false, // the list of nudge rules at the foot is open
  rulesCount: null, // its "2 off"
};

const isToday = () => state.picked === state.today.day;
const pickedDoc = () => (isToday() ? state.today : state.day);

function slot(name, signature, build) {
  const el = state.slots[name];
  const text = JSON.stringify(signature);
  if (state.drawn[name] === text) return;
  state.drawn[name] = text;
  const built = build();
  fill(el, built);
  el.hidden = !built;
}

// ---- habits ------------------------------------------------------------------------------------

function level(entry) {
  if (!entry.logged) return "open";
  const share = entry.total ? entry.done / entry.total : 0;
  return `level-${1 + Math.min(3, Math.floor(share * 4))}`;
}

function stripEl() {
  const { recent, day } = state.today;
  return h("div", { class: "strip", role: "group", "aria-label": "Last two weeks" }, recent.map((entry) => {
    const date = dateOf(entry.day);
    const letter = date.toLocaleDateString(undefined, { weekday: "narrow" });
    return h("button", {
      type: "button",
      class: `strip-day ${level(entry)}${entry.day === state.picked ? " picked" : ""}${entry.day === day ? " now" : ""}`,
      "aria-label": `${dayShort(entry.day)}, ${entry.logged ? `${entry.done} of ${entry.total}` : "not logged"}`,
      "aria-pressed": String(entry.day === state.picked),
      onclick: () => pick(entry.day),
    }, h("span", { class: "letter" }, letter), h("span", { class: "date" }, date.getDate()));
  }));
}

const openDays = () => state.today.recent.filter((d) => !d.logged && d.day !== state.today.day);

function locks() {
  const found = {};
  if (!isToday()) return found;
  for (const block of state.today.blocks.items) {
    for (const need of block.needs) if (need.habit) found[need.habit] = block.name;
  }
  return found;
}

function habitActions(rows) {
  if (isToday()) {
    const open = openDays();
    return open.length > 0 && h("button", {
      type: "button", class: "chip warn", onclick: () => pick(open[open.length - 1].day),
    }, `${open.length} days open`);
  }
  const untouched = !rows.some((r) => r.value != null && r.value >= 0);
  const next = openDays().filter((d) => d.day !== state.picked).pop();
  return [
    untouched && h("button", { type: "button", class: "chip", onclick: () => state.grid.tickAll() }, "Tick all"),
    next && !untouched && h("button", { type: "button", class: "chip warn", onclick: () => pick(next.day) }, "Next open"),
    h("button", { type: "button", class: "chip", onclick: () => pick(state.today.day) }, "Today"),
  ];
}

function habitsCard() {
  const rows = pickedDoc().habits.items;
  if (!rows.length) {
    return card("Habits", null, h("p", { class: "empty" }, "No habits yet"));
  }
  const counts = score(rows);
  const scoreBox = h("div", { class: "score-slot" }, scoreRing(counts.done, counts.total));
  const strip = h("div", null, stripEl());
  const actions = h("div", { class: "habit-actions" }, habitActions(rows));
  const chrome = () => {
    const now = score(rows);
    fill(scoreBox, scoreRing(now.done, now.total));
    fill(strip, stripEl());
    fill(actions, habitActions(rows));
    return now;
  };
  state.rows = rows;
  state.syncHabits = () => {
    state.grid.sync();
    chrome();
  };
  state.grid = habitGrid(rows, state.picked, {
    locks: locks(),
    keys: matchMedia("(hover: hover)").matches,
    onChange() {
      noteTap(rows, score(rows));
      chrome();
      reloadSoon();
    },
    onStale: () => load(),
  });
  return h("section", { class: "card habits" },
    h("div", { class: "habit-head" },
      h("h2", { class: isToday() ? "habit-day" : "habit-day picked" }, isToday() ? "Habits" : dayShort(state.picked)),
      actions, scoreBox),
    strip,
    state.grid.el);
}

/** Carries a tap into what the other cards read, before the server has been asked again. */
function noteTap(rows, counts) {
  const entry = state.today.recent.find((d) => d.day === state.picked);
  if (entry) {
    entry.done = counts.done;
    entry.total = counts.total;
    entry.logged = rows.filter((r) => r.value != null && r.value >= 0).length;
  }
  if (!isToday()) return;
  const done = new Map(rows.map((r) => [r.id, r.done]));
  for (const block of state.today.blocks.items) {
    for (const need of block.needs) if (done.has(need.habit)) need.done = done.get(need.habit);
  }
  drawScreen();
}

/** The grid keeps the old day until the new one is here, so a tap can't land on the wrong date. */
async function pick(day) {
  if (day === state.picked) return;
  const asked = ++state.asked;
  let doc = null;
  if (day !== state.today.day) {
    try {
      doc = await getJson(`api/day?date=${day}`);
    } catch {
      toast("Can't reach the server");
      return;
    }
  }
  if (asked !== state.asked) return;
  state.picked = day;
  state.day = doc;
  draw();
}

// ---- tiles -------------------------------------------------------------------------------------

const count = (v) => Math.round(v).toLocaleString();

/** A day of the fortnight the tiles chart, for a bar's tip. */
const fortnightDay = (body, i) => dayShort(addDays(body.recent.first, i));

function budgetTile(m) {
  const b = m.budget;
  if (!b) return null;
  const cur = m.currency;
  const days = b.days ?? [];
  const today = Math.max(0, Math.min(days.length - 1, Math.round((dateOf(state.today.now.slice(0, 10)) - dateOf(b.first)) / 86400000)));
  const foot = [
    figure(money(b.todayCents, cur), "today"),
    b.perDayCents != null && b.leftCents > 0 && figure(money(b.perDayCents, cur), "a day"),
    b.outsideCents > 0 && figure(money(b.outsideCents, cur), "outside"),
  ];
  if (b.cents == null) {
    return tile({
      label: "Spent", value: money(b.spentCents, cur), wide: true, color: "var(--svc-xrnwal)", foot,
      viz: bars(days.slice(0, today + 1), { label: "Spending a day this month" }),
    });
  }
  const over = b.leftCents < 0;
  return tile({
    label: "Budget",
    value: money(Math.abs(b.leftCents), cur),
    unit: over ? "over" : `left of ${money(b.cents, cur)}`,
    tone: over ? "warn" : "",
    wide: true,
    color: "var(--svc-xrnwal)",
    viz: burnUp({ days, budget: b.cents, today, first: b.first, currency: cur }),
    foot,
  });
}

function stepsTile(body) {
  const values = body.recent.steps;
  const settled = average(values.slice(0, -1));
  const updated = new Date(body.updated);
  const stale = Date.parse(state.today.now) - updated > STALE_MS;
  return tile({
    label: "Steps",
    value: body.steps == null ? "–" : body.steps.toLocaleString(),
    color: "var(--svc-xrnfit)",
    viz: bars(values, {
      line: settled,
      label: "Steps, the last two weeks",
      tip: (i) => [fortnightDay(body, i), [[values[i] == null ? "–" : count(values[i]), "steps"]]],
    }),
    foot: stale
      ? h("span", { class: "dt-fig warn" }, `${dayPlain(isoOf(updated))} ${clock(updated)}`)
      : settled != null && figure(count(settled), "avg"),
  });
}

function sleepTile(body) {
  const { sleep } = body;
  const values = body.recent.asleep;
  return tile({
    label: "Sleep",
    value: sleep ? span(sleep.minutes) : "–",
    color: "var(--svc-xrnfit)",
    viz: bars(values, {
      mark: sleep && sleep.day !== body.day ? values.length - 2 : values.length - 1,
      line: average(values.slice(0, -1)),
      label: "Sleep, the last two weeks",
      tip: (i) => [fortnightDay(body, i), [[values[i] == null ? "–" : span(values[i]), "asleep"]]],
    }),
    foot: sleep && figure(`${clock(new Date(sleep.start))}–${clock(new Date(sleep.end))}`),
  });
}

function restingTile(body) {
  const { resting } = body;
  if (!resting) return null;
  const values = body.recent.resting;
  const avg = average(values);
  return tile({
    label: "Resting",
    value: resting.bpm,
    unit: "bpm",
    color: "var(--svc-xrnfit)",
    viz: readings(values, {
      label: "Resting heart rate, the last two weeks",
      tip: (i) => [fortnightDay(body, i), [[`${values[i]} bpm`, "resting"]]],
    }),
    foot: avg != null && figure(Math.round(avg), "avg"),
  });
}

function weightTile(body) {
  const w = body.weight;
  if (!w) return null;
  const values = body.recent.weightKg;
  const days = w.change == null ? null : Math.round((w.at - w.since) / 86400000);
  const sign = w.change > 0 ? "+" : w.change < 0 ? "−" : "±";
  return tile({
    label: "Weight",
    value: w.kg.toFixed(1),
    unit: "kg",
    color: "var(--svc-xrnfit)",
    viz: readings(values, {
      label: "Weight, the last two weeks",
      tip: (i) => [fortnightDay(body, i), [[`${values[i]?.toFixed(1)} kg`, ""]]],
    }),
    foot: w.change != null && figure(`${sign}${Math.abs(w.change).toFixed(1)} kg`, `${days}d`),
  });
}

function mailTile(mail) {
  const detail = mail?.detail;
  if (!detail || detail.held == null) return null;
  return tile({
    label: "Mail",
    value: detail.held,
    unit: "waiting",
    foot: detail.nextBatch && figure(clock(new Date(detail.nextBatch)), "next"),
  });
}

function tilesRow() {
  const t = state.today;
  const body = t.health;
  const found = [
    t.money && budgetTile(t.money),
    body && stepsTile(body),
    body && sleepTile(body),
    body && restingTile(body),
    body && weightTile(body),
    mailTile(t.mail),
  ].filter(Boolean);
  return found.length > 0 && h("div", { class: "dt-tiles" }, found);
}

// ---- the day's calendar ------------------------------------------------------------------------

function dot(color) {
  return h("i", { class: "dot", style: { "background-color": color || "var(--muted)" } });
}

function until(minutes) {
  return minutes < 60 ? `${Math.max(1, Math.round(minutes))}m` : span(Math.round(minutes / 5) * 5);
}

function nextRows(items, now, dayIso) {
  const rows = [];
  let lastDay = dayIso;
  for (const e of items.filter((x) => !x.allDay).slice(0, NEXT_ROWS)) {
    const start = new Date(e.start);
    const end = new Date(e.end);
    const key = e.start.slice(0, 10);
    if (key !== lastDay) {
      rows.push(h("div", { class: "next-day" }, dayShort(key)));
      lastDay = key;
    }
    const running = start <= now && now < end;
    const side = running ? `${until((end - now) / 60000)} left` : start - now < 86400000 ? `in ${until((start - now) / 60000)}` : "";
    rows.push(h("div", { class: `row${running ? " running" : ""}` },
      h("span", { class: "time" }, running ? "Now" : clockOf(e.start)),
      dot(e.color),
      h("span", { class: "grow clip" }, e.title),
      h("span", { class: "side" }, side)));
  }
  return rows;
}

function countdownChips(found) {
  return found.length > 0 && h("div", { class: "chips day-soon" }, found.map((c) =>
    h("span", { class: `chip plain${c.days <= 3 ? " near" : ""}`, title: dayShort(c.date) },
      dot(c.color), h("span", { class: "clip" }, c.title),
      h("b", null, c.days === 0 ? "today" : `${c.days}d`))));
}

function dayCard() {
  const { calendar, now, day, countdowns } = state.today;
  if (!calendar.ready) return card("Calendar", null, h("div", { class: "skeleton line" }), h("div", { class: "skeleton line short" }));
  const moment = new Date(now);
  const allDay = calendar.items.filter((e) => e.allDay && e.start.slice(0, 10) <= day);
  const rows = nextRows(calendar.items, moment, day);
  return h("section", { class: "card day-card" },
    h("div", { class: "card-head" }, h("h2", null, "Calendar")),
    ribbon(calendar.day ?? [], { day, now: moment }),
    allDay.length > 0 && h("div", { class: "chips" },
      allDay.slice(0, CHIPS_ALL_DAY).map((e) => h("span", { class: "chip plain" }, dot(e.color), h("span", { class: "clip" }, e.title))),
      allDay.length > CHIPS_ALL_DAY && h("span", { class: "chip plain" }, `+${allDay.length - CHIPS_ALL_DAY}`)),
    rows.length ? h("div", { class: "day-next" }, rows) : h("p", { class: "empty" }, "Nothing booked"),
    countdownChips(countdowns));
}

// ---- screen ------------------------------------------------------------------------------------

function limitRow(block) {
  const left = Math.max(block.limitMs - block.usedMs, 0);
  const used = block.limitMs ? Math.min(block.usedMs / block.limitMs, 1) : 0;
  const tone = left === 0 ? "out" : used > 0.75 ? "low" : "";
  return h("div", { class: `limit ${tone}` },
    h("span", { class: "grow clip" }, block.name),
    h("b", null, left === 0 ? "Out" : span(left / 60000)),
    h("span", { class: "sub" }, `/ ${span(block.limitMs / 60000)}`),
    h("div", { class: "meter" }, h("i", { style: { width: `${used * 100}%` } })));
}

function gateRow(block) {
  const ready = block.needs.every((n) => n.done);
  return h("div", { class: "gate" },
    h("span", { class: "gate-name clip" }, block.name),
    h("span", { class: "needs" }, block.needs.map((n) => h("span", { class: `need${n.done ? " met" : ""}` }, n.text))),
    block.state === "open"
      ? h("span", { class: "pill open" }, "Open")
      : ready && h("span", { class: "pill open" }, "Ready"));
}

function screenCard() {
  const { items, hits } = state.today.blocks;
  const limits = items.filter((b) => b.limitMs > 0 && b.state !== "off");
  const gates = items.filter((b) => b.needs.length > 0 && b.state !== "off");
  if (!limits.length && !gates.length) return null;
  return card("Screen", hits > 0 && h("span", { class: "side" }, `${hits} stopped`),
    limits.length > 0 && h("div", { class: "limits" }, limits.map(limitRow)),
    gates.map(gateRow));
}

function drawScreen() {
  slot("screen", state.today.blocks, screenCard);
}

// ---- home --------------------------------------------------------------------------------------

function homeCard() {
  const home = state.today.home;
  if (!home || (!home.shopping.length && !home.expiring.length)) return null;
  const expiring = home.expiring.slice(0, 4);
  return card("Home", home.shopping.length > 0 && h("span", { class: "side" }, `${home.shopping.length} to buy`),
    home.shopping.length > 0 && h("div", { class: "chips" }, home.shopping.slice(0, 12).map((n) => h("span", { class: "chip plain" }, n))),
    expiring.map((e) => h("div", { class: "row" },
      h("span", { class: "grow clip" }, e.name),
      h("span", { class: `pill ${e.days <= 1 ? "blocked" : "warn"}` }, e.days < 0 ? "Expired" : e.days === 0 ? "Today" : `${e.days}d`))));
}

// ---- nudges ------------------------------------------------------------------------------------

// Saves to the rules go one after another, so two quick switches land in the order they were made.
let ruleWrites = Promise.resolve();

// Goes up as each change to the nudges (a card hidden, a rule switched) starts and again as it
// lands. Today's answer from the server can be on its way across one: asked for before the change
// reached the server, it still holds the card just hidden or turned off, so load() asks again
// rather than draw that card back.
let nudgeEdits = 0;

async function editNudges(work) {
  nudgeEdits += 1;
  try {
    return await work();
  } finally {
    nudgeEdits += 1;
  }
}

/**
 * Turns one rule on or off, against the rules as the server has them now: they carry a version
 * (ETag), and a save names it (If-Match). If they changed in between, the switch goes onto the new
 * ones, once. Resolves to the rules as saved.
 */
function setRule(id, on) {
  const run = editNudges(() => ruleWrites.then(async () => {
    for (let tries = 0; ; tries++) {
      const res = await api("api/rules");
      const etag = res.headers.get("ETag");
      const { rules } = await res.json();
      if (!rules.some((r) => r.id === id)) return rules;
      try {
        const saved = await api("api/rules", {
          method: "PUT",
          headers: { "Content-Type": "application/json", "If-Match": etag },
          body: JSON.stringify({ rules: rules.map((r) => (r.id === id ? { ...r, on } : r)) }),
        });
        return (await saved.json()).rules;
      } catch (e) {
        if (e.status !== 412 || tries > 0) throw e;
      }
    }
  }));
  ruleWrites = run.catch(() => {});
  return run;
}

/** Saves a switch, then counts what's off for the list at the foot. */
async function saveRule(id, on) {
  const rules = await setRule(id, on);
  state.today.nudgesOff = rules.filter((r) => !r.on).length;
  drawRules();
}

/** The server said no (it says why), or it couldn't be reached. */
const notSaved = (e) => (e.status ? `Not saved: ${e.message}` : "Not saved. Check the connection.");

function dropNudge(id) {
  state.today.nudges = state.today.nudges.filter((x) => x.id !== id);
  draw();
}

async function hideToday(n) {
  await editNudges(async () => {
    dropNudge(n.id);
    await postJson("api/nudges/dismiss", { rule: n.id }).catch(() => toast("Not saved. Check the connection."));
  });
}

async function turnOff(n) {
  try {
    await editNudges(async () => {
      dropNudge(n.id);
      await saveRule(n.id, false);
    });
  } catch (e) {
    if (e instanceof SignedOut) return;
    toast(notSaved(e));
    load().catch(() => {});
    return;
  }
  toast(`${n.name} off`, () => switchBack(n.id));
}

async function switchBack(id) {
  try {
    await saveRule(id, true);
  } catch (e) {
    if (!(e instanceof SignedOut)) toast(notSaved(e));
    return;
  }
  load().catch(() => {});
}

/** The card's menu sits under its button, or over it near the bottom of the screen. */
function placeMenu(menu, button) {
  const box = button.getBoundingClientRect();
  const width = document.documentElement.clientWidth;
  menu.style.setProperty("right", `${Math.max(8, width - box.right)}px`);
  menu.style.setProperty("top", `${box.bottom + 4}px`);
  menu.style.removeProperty("bottom");
  requestAnimationFrame(() => {
    if (menu.getBoundingClientRect().bottom <= innerHeight - 8) return;
    menu.style.removeProperty("top");
    menu.style.setProperty("bottom", `${innerHeight - box.top + 4}px`);
  });
}

function moreIcon() {
  return svg("svg", { viewBox: "0 0 24 24", "aria-hidden": "true" }, svg("path", { d: "M5.5 12h.01M12 12h.01M18.5 12h.01" }));
}

function nudgeCard(n) {
  const id = `nudge-menu-${n.id}`;
  const close = () => menu.hidePopover();
  const button = h("button", {
    type: "button", class: "more", popovertarget: id, "aria-haspopup": "menu", "aria-label": `${n.name}: more`,
  }, moreIcon());
  const menu = h("div", {
    id, class: "menu", popover: "auto", role: "menu",
    onbeforetoggle: (e) => {
      if (e.newState !== "open") return;
      placeMenu(menu, button);
      addEventListener("scroll", () => menu.isConnected && menu.matches(":popover-open") && close(), { once: true, passive: true });
    },
  },
  h("button", { type: "button", role: "menuitem", onclick: () => { close(); hideToday(n); } }, "Hide today"),
  h("button", { type: "button", role: "menuitem", onclick: () => { close(); turnOff(n); } }, "Turn off"));
  return h("div", { class: "nudge" },
    h("span", { class: "grow" }, h("b", null, n.name), n.why.length > 0 && h("span", { class: "sub" }, n.why.join(", "))),
    button, menu);
}

function nudgeBar() {
  const list = state.today.nudges;
  if (!list.length) return null;
  return h("div", { class: "nudges" }, list.map(nudgeCard));
}

/** A rule's switch in the list at the foot: it flips at once, and back if the save fails. */
function ruleRow(rule) {
  const flip = async (button) => {
    const on = !rule.on;
    rule.on = on;
    button.setAttribute("aria-checked", String(on));
    try {
      await saveRule(rule.id, on);
    } catch (e) {
      rule.on = !on;
      button.setAttribute("aria-checked", String(!on));
      if (!(e instanceof SignedOut)) toast(notSaved(e));
      return;
    }
    load().catch(() => {});
  };
  return h("div", { class: "rule" },
    h("span", { class: "grow clip" }, rule.name),
    h("button", {
      type: "button", class: "switch", role: "switch", "aria-checked": String(rule.on), "aria-label": rule.name,
      onclick: (e) => flip(e.currentTarget),
    }));
}

async function listRules(list) {
  fill(list, h("div", { class: "skeleton line" }));
  try {
    const { rules } = await getJson("api/rules");
    fill(list, rules.map(ruleRow));
  } catch (e) {
    if (e instanceof SignedOut) return;
    fill(list, h("p", { class: "error" }, "Can't reach the server"));
  }
}

/**
 * The way back for a rule turned off on its card: every rule with its switch, folded away at the
 * foot of the page, and there only while one is off (or the list is open).
 */
function rulesCard() {
  const count = h("span", { class: "side" });
  const list = h("div", { class: "rule-list" });
  const box = h("details", {
    class: "card rules",
    ontoggle: () => {
      if (box.open === state.rulesOpen) return;
      state.rulesOpen = box.open;
      if (box.open) listRules(list);
      else drawRules();
    },
  }, h("summary", { class: "card-head" }, h("h2", null, "Nudges"), count), list);
  if (state.rulesOpen) {
    box.open = true;
    listRules(list);
  }
  state.rulesCount = count;
  return box;
}

function drawRules() {
  const off = state.today.nudgesOff ?? 0;
  const shown = off > 0 || state.rulesOpen;
  slot("rules", shown, () => shown && rulesCard());
  if (shown) fill(state.rulesCount, off > 0 && `${off} off`);
}

// ---- the page ----------------------------------------------------------------------------------

/** What a tile can't change in place; anything else is the same grid with new values. */
const tileShape = (r) => [r.id, r.name, r.type, r.color, r.targetValue, r.targetType];

function drawHabits() {
  const doc = pickedDoc();
  const rows = doc.habits.items;
  slot("habits", [state.picked, rows.map(tileShape), locks()], habitsCard);
  if (state.rows === rows || state.rows?.length !== rows.length) return;
  rows.forEach((row, i) => Object.assign(state.rows[i], row));
  doc.habits.items = state.rows;
  state.syncHabits();
}

function draw() {
  const t = state.today;
  fill(state.title, dateOf(t.day).toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" }));
  slot("nudges", t.nudges, nudgeBar);
  slot("tiles", [t.money, t.health, t.mail?.detail, t.now.slice(0, 10)], tilesRow);
  drawHabits();
  slot("day", [t.calendar, t.countdowns, t.now], dayCard);
  drawScreen();
  slot("home", t.home, homeCard);
  drawRules();
}

function layout(view) {
  const slots = {};
  const make = (name) => {
    slots[name] = h("div", { class: `slot slot-${name}` });
    return slots[name];
  };
  state.title = h("h1", { class: "page-title" });
  fill(view,
    state.title,
    make("nudges"),
    h("div", { class: "today" },
      make("tiles"),
      h("div", { class: "dt-row main" }, make("habits"), make("day")),
      h("div", { class: "dt-row" }, make("screen"), make("home")),
      make("rules")));
  state.slots = slots;
  state.drawn = {};
  state.rows = null;
  state.view = view;
}

function skeleton(view) {
  fill(view, h("div", { class: "skeleton title" }), h("div", { class: "today" },
    h("div", { class: "dt-tiles" }, [0, 1, 2].map(() => h("div", { class: "skeleton block short dt-tile" }))),
    h("div", { class: "dt-row main" }, h("div", { class: "skeleton block tall" }), h("div", { class: "skeleton block tall" }))));
}

/**
 * Reads the server and redraws what changed, unless a tap is still on its way there or a nudge
 * changed while the answer was on its way here.
 */
async function load() {
  const edits = nudgeEdits;
  const stale = () => writesPending() || nudgeEdits !== edits;
  const today = await getJson("api/today");
  if (stale()) return reloadSoon();
  const rolled = state.today && state.today.day !== today.day && state.picked === state.today.day;
  state.today = today;
  if (!state.picked || rolled) state.picked = today.day;
  if (!isToday()) state.day = await getJson(`api/day?date=${state.picked}`);
  if (stale()) return reloadSoon();
  if (state.view !== state.target) layout(state.target);
  return draw();
}

function reloadSoon() {
  clearTimeout(state.reloadTimer);
  state.reloadTimer = setTimeout(() => load().catch(() => {}), RELOAD_AFTER_TAP_MS);
}

export async function showToday(view) {
  state.target = view;
  if (state.view !== view) {
    if (state.today) {
      layout(view);
      draw();
    } else {
      skeleton(view);
    }
  }
  await load();
}

export function todayKey(event) {
  if (!state.today) return;
  if (/^[1-9]$/.test(event.key)) state.grid?.press(Number(event.key) - 1);
  const days = state.today.recent.map((d) => d.day);
  const at = days.indexOf(state.picked);
  if (event.key === "ArrowLeft" && at > 0) pick(days[at - 1]);
  if (event.key === "ArrowRight" && at >= 0 && at < days.length - 1) pick(days[at + 1]);
  if (event.key === "t") pick(state.today.day);
}

// The long view. Each chart answers one question and carries its own hover layer; anything only a
// hover shows is also in a table a tap away. Calendar colours stay the ones set in the calendar, so
// a colour means the same thing here as there; every other chart takes its app's colour.

import { addDays, card, dateOf, dayPlain, dayShort, fill, getJson, glyphClass, h, hideTip, isWordName, money, showTip, span, withTip } from "./dom.js";
import { openDay } from "./day.js";
import { average, bars, delta, figure, monthColumns, tile } from "./days-kit.js";
import { habitColors, takeChanged } from "./habits.js";

const CELL_MIN = 13; // px; what a thumb can still hit
const CELL_GAP = 3;
const YEAR_WEEKS = 53;
const WEEKDAY_COLUMN = 70; // px: the weekday bars at the end of the year's rows, and their gap
const WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const STRIP_CLASS = { 0: "miss", 1: "hit", 2: "rest" };
const WEEK = 7;

let target = null;

/** A card whose numbers are one tap away: the head's button swaps the chart for its table. */
function tableCard(title, side, chart, table) {
  const box = h("div", { class: "table-scroll", hidden: true });
  const toggle = h("button", {
    type: "button",
    class: "link",
    "aria-pressed": "false",
    onclick: () => {
      const on = box.hidden;
      box.hidden = !on;
      chart.hidden = on;
      toggle.setAttribute("aria-pressed", String(on));
      toggle.textContent = on ? "Chart" : "Table";
      if (on) fill(box, table());
    },
  }, "Table");
  return h("section", { class: "card" },
    h("div", { class: "card-head" }, h("h2", null, title), side, toggle),
    chart, box);
}

function table(head, rows) {
  return h("table", null,
    h("thead", null, h("tr", null, head.map((name) => h("th", null, name)))),
    h("tbody", null, rows.map((row) => h("tr", null, row.map((cell) => h("td", null, cell))))));
}

// ---- the week in tiles -------------------------------------------------------------------------

/** The last finished week's first and last day, [back] weeks before; Review's weeks end yesterday. */
function weekSpan(today, back) {
  const last = addDays(today, -1 - WEEK * back);
  return `${dayPlain(addDays(last, -(WEEK - 1)))} – ${dayPlain(last)}`;
}

/** This week's average of a health measure against the week before, from the month's days. */
function weekAverages(days, key, settledToday) {
  const values = days.map((d) => d[key]);
  const end = settledToday ? values.length : values.length - 1;
  return [average(values.slice(end - WEEK, end)), average(values.slice(end - 2 * WEEK, end - WEEK))];
}

function tiles(doc) {
  const { trend, temptations: t, health: body } = doc;
  const weeks = doc.habitWeeks ?? [];
  const found = [
    tile({
      label: "Habits",
      value: trend ? `${trend.thisWeek}%` : "–",
      delta: trend && delta(trend.thisWeek, trend.lastWeek, { title: "Against the week before" }),
      color: "var(--svc-xrnloops)",
      viz: bars(weeks, {
        label: "Habits done a week",
        tip: (i) => [weekSpan(doc.day, weeks.length - 1 - i), [[weeks[i] == null ? "–" : `${weeks[i]}%`, "done"]]],
      }),
    }),
  ];
  if (body?.summary.steps) {
    const [now, before] = weekAverages(body.days, "steps", false);
    const values = body.days.map((d) => d.steps);
    found.push(tile({
      label: "Steps",
      value: now == null ? "–" : Math.round(now).toLocaleString(),
      unit: "a day",
      delta: delta(now && Math.round(now), before && Math.round(before), { format: (v) => v.toLocaleString(), title: "Against the week before" }),
      color: "var(--svc-xrnfit)",
      viz: bars(values, { label: "Steps a day", tip: (i) => [dayShort(body.days[i].date), [[values[i] == null ? "–" : values[i].toLocaleString(), "steps"]]] }),
    }));
  }
  if (body?.summary.asleep) {
    const [now, before] = weekAverages(body.days, "asleep", true);
    const values = body.days.map((d) => d.asleep);
    found.push(tile({
      label: "Sleep",
      value: now == null ? "–" : span(now),
      unit: "a night",
      delta: delta(now && Math.round(now), before && Math.round(before), { format: span, title: "Against the week before" }),
      color: "var(--svc-xrnfit)",
      viz: bars(values, { label: "Sleep a night", tip: (i) => [dayShort(body.days[i].date), [[values[i] == null ? "–" : span(values[i]), "asleep"]]] }),
    }));
  }
  const stopped = t.weeks ?? [];
  found.push(tile({
    label: "Stopped",
    value: t.thisWeek,
    unit: "this week",
    delta: delta(t.thisWeek, t.lastWeek, { upIsGood: false, title: "Against the week before" }),
    color: "var(--svc-xrnrule)",
    viz: bars(stopped, {
      label: "Blocked opens a week",
      tip: (i) => [weekSpan(addDays(doc.day, 1), stopped.length - 1 - i), [[stopped[i], "stopped"]]],
    }),
  }));
  return h("div", { class: "dt-tiles" }, found);
}

// ---- the year ----------------------------------------------------------------------------------

function cellLevel(entry) {
  if (entry?.share == null) return "level-0";
  return `level-${1 + Math.min(3, Math.floor(entry.share * 4))}`;
}

function moveFocus(event, grid) {
  const steps = { ArrowLeft: -7, ArrowRight: 7, ArrowUp: -1, ArrowDown: 1 };
  const step = steps[event.key];
  if (!step) return;
  event.preventDefault();
  const cells = [...grid.querySelectorAll(".cell[data-day]")];
  const at = cells.indexOf(event.target);
  const next = cells[Math.min(cells.length - 1, Math.max(0, at + step))];
  if (next) {
    event.target.tabIndex = -1;
    next.tabIndex = 0;
    next.focus();
  }
}

/** Each weekday's average share over the year, Monday first, at the end of its row. */
function weekdayBars(year) {
  const sums = Array.from({ length: 7 }, () => [0, 0]);
  for (const d of year) {
    const weekday = (dateOf(d.day).getDay() + 6) % 7;
    sums[weekday][0] += d.share;
    sums[weekday][1] += 1;
  }
  return h("div", { class: "weekday-bars", role: "img", "aria-label": "Habits done by weekday" }, sums.map(([sum, n], i) => {
    const share = n ? sum / n : null;
    const row = h("span", null,
      share != null && h("i", { style: { width: `${Math.max(share * 70, 2)}%` } }),
      share != null && h("b", null, `${Math.round(share * 100)}%`));
    return share == null ? row : withTip(row, () => showTip(row, WEEKDAY_NAMES[i], [[`${Math.round(share * 100)}%`, `of ${n} days`]]));
  }));
}

function drawYear(box, doc) {
  const room = box.clientWidth - 22 - WEEKDAY_COLUMN;
  const weeks = Math.max(8, Math.min(YEAR_WEEKS, Math.floor((room + CELL_GAP) / (CELL_MIN + CELL_GAP))));
  const byDay = new Map(doc.year.map((d) => [d.day, d]));
  const today = doc.day;
  const shift = (dateOf(today).getDay() + 6) % 7;
  const lastMonday = addDays(today, -shift);
  const firstMonday = addDays(lastMonday, -7 * (weeks - 1));
  const grid = h("div", { class: "year-grid", style: { "--weeks": weeks } });
  const months = h("div", { class: "year-months", style: { "--weeks": weeks } });
  let lastMonth = -1;
  let lastLabel = -9;
  const earliest = addDays(today, -398);
  for (let w = 0; w < weeks; w += 1) {
    const monday = addDays(firstMonday, 7 * w);
    const month = dateOf(monday).getMonth();
    if (month !== lastMonth && w - lastLabel >= 3) {
      months.append(h("span", { style: { "grid-column": `${w + 1} / span 3` } },
        dateOf(monday).toLocaleDateString(undefined, { month: "short" })));
      lastLabel = w;
    }
    lastMonth = month;
    for (let d = 0; d < 7; d += 1) {
      const day = addDays(monday, d);
      if (day > today) {
        grid.append(h("span", { class: "cell future" }));
        continue;
      }
      const entry = byDay.get(day);
      const words = entry?.share != null ? `${Math.round(entry.share * 100)}%` : "–";
      const cell = h("button", {
        type: "button",
        class: `cell ${cellLevel(entry)}${day === today ? " now" : ""}`,
        "data-day": day,
        tabindex: day === today ? 0 : -1,
        "aria-label": `${dayShort(day)}: ${words}`,
        onclick: () => {
          hideTip();
          openDay(day, { first: earliest, last: today, onClose: refresh });
        },
      });
      grid.append(withTip(cell, () => showTip(cell, dayShort(day), [[words, entry?.share != null ? "habits" : ""]])));
    }
  }
  grid.addEventListener("keydown", (e) => moveFocus(e, grid));
  const firstShown = firstMonday;
  fill(box,
    h("div", { class: "year-head" }, months),
    h("div", { class: "year-body with-days" },
      h("div", { class: "year-days" }, WEEKDAY_NAMES.map((n, i) => h("span", null, i % 2 ? "" : n[0]))),
      grid,
      weekdayBars(doc.year.filter((d) => d.day >= firstShown))));
}

function yearCard(doc) {
  const box = h("div", { class: "year" });
  let width = 0;
  const watch = new ResizeObserver(() => {
    if (box.clientWidth !== width) {
      width = box.clientWidth;
      drawYear(box, doc);
    }
  });
  watch.observe(box);
  return card("Every day", h("span", { class: "side" }, `${doc.loggedDays}/${doc.windowDays} logged`), box);
}

// ---- habits ------------------------------------------------------------------------------------

function habitRow(row, first, words) {
  return h("div", { class: words ? "strength words" : "strength", style: habitColors(row.color) },
    h("span", { class: glyphClass(row.name) }, row.name),
    h("div", { class: "cells", role: "img", "aria-label": `${row.name}: ${row.strip.filter((s) => s === 1).length} of 30 days done` },
      row.strip.map((value, i) => {
        const day = addDays(first, i);
        return h("i", { class: STRIP_CLASS[value] ?? "none", title: dayPlain(day) });
      })),
    h("span", { class: "pct" }, row.pct == null ? "–" : `${row.pct}%`),
    h("span", { class: "run" }, row.streak > 0 ? row.streak : "–"),
    h("span", { class: "run faint" }, row.best));
}

function habitsCard(doc) {
  const { items, first } = doc.habits;
  if (!items.length) return null;
  return card("Habits", h("div", { class: "strength-head", "aria-hidden": "true" },
    h("span", { class: "pct" }, "30d"), h("span", { class: "run" }, "Now"), h("span", { class: "run" }, "Best")),
  h("div", { class: "fills" }, items.map((row) => habitRow(row, first, items.some((r) => isWordName(r.name))))));
}

// ---- the body ----------------------------------------------------------------------------------

// A row per measure, over the same 30 days as the habit strips. Steps and sleep are amounts, so
// they're bars from zero; the rest are levels, so they're dots between the month's lowest and
// highest. Each row is its own scale: no two measures share an axis.
const count = (v) => Math.round(v).toLocaleString();
const BODY_ROWS = [
  { key: "steps", label: "Steps", form: "amount", text: count, tip: (v, today) => `${count(v)} steps${today ? " so far" : ""}` },
  { key: "asleep", label: "Asleep", form: "amount", text: span, tip: (v) => `${span(v)} asleep` },
  { key: "resting", label: "Resting HR", form: "level", text: String, tip: (v) => `${v} bpm resting` },
  { key: "weightKg", label: "Weight", unit: "kg", form: "level", text: (v) => v.toFixed(1), tip: (v) => `${v.toFixed(1)} kg` },
  { key: "spo2", label: "SpO2", unit: "%", form: "level", text: String, tip: (v) => `${v}% SpO2` },
  { key: "stress", label: "Stress", form: "level", text: String, tip: (v) => `Stress ${v}` },
];

function bodyStrip(row, days, today, earliest) {
  const values = days.map((d) => d[row.key]).filter((v) => v != null);
  const top = Math.max(...values);
  const low = Math.min(...values);
  return h("div", { class: `body-strip ${row.form}`, role: "img", "aria-label": `${row.label}, the last 30 days` },
    days.map((d) => {
      const value = d[row.key];
      const col = h("i", { class: "body-col", onclick: () => { hideTip(); openDay(d.date, { first: earliest, last: today, onClose: refresh }); } });
      if (value == null) return col;
      const share = row.form === "amount" ? value / (top || 1) : top === low ? 0.5 : (value - low) / (top - low);
      col.append(h("b", { style: row.form === "amount" ? { height: `${Math.max(share * 100, 4)}%` } : { bottom: `${8 + share * 84}%` } }));
      return withTip(col, () => showTip(col, dayShort(d.date), [[row.tip(value, d.date === today), ""]]));
    }));
}

function bodyCard(doc) {
  const body = doc.health;
  if (!body) return null;
  const rows = BODY_ROWS.filter((r) => body.summary[r.key]);
  const earliest = addDays(doc.day, -398);
  const cell = (row, v) => (v == null ? "–" : row.text(v));
  const chart = h("div", { class: "fills" },
    h("div", { class: "body-head", "aria-hidden": "true" }, h("span", null, "30d avg"), h("span", null, "Latest")),
    rows.map((row) => {
      const { avg, latest } = body.summary[row.key];
      return h("div", { class: "body-row" },
        h("span", { class: "body-name" }, row.label, row.unit && h("small", null, ` ${row.unit}`)),
        bodyStrip(row, body.days, doc.day, earliest),
        h("span", { class: "body-num" }, cell(row, avg)),
        h("span", { class: "body-num strong" }, cell(row, latest)));
    }));
  const box = tableCard("Health", null, chart, () => table(
    ["Day", ...rows.map((r) => (r.unit ? `${r.label} (${r.unit})` : r.label))],
    [...body.days].reverse().map((d) => [dayPlain(d.date), ...rows.map((r) => cell(r, d[r.key]))])));
  box.id = "review-health"; // #review/health, where the Overview's xrnfit card goes
  return box;
}

// ---- hours -------------------------------------------------------------------------------------

function niceTop(value) {
  if (value <= 0) return 10;
  const base = 10 ** Math.floor(Math.log10(value));
  for (const step of [1, 2, 2.5, 5, 10]) if (value <= step * base) return step * base;
  return 10 * base;
}

const seriesColor = (s) => s.color || "var(--outline)";

function hoursCard(doc) {
  const hours = doc.hours;
  if (!hours?.series.length) return null;
  const { weeks, series } = hours;
  const totals = weeks.map((_, i) => series.reduce((sum, s) => sum + s.hours[i], 0));
  const top = niceTop(Math.max(...totals));
  const lines = [0, 0.5, 1].map((share) => h("div", { class: "gridline", style: { bottom: `${share * 100}%` } },
    h("span", null, Math.round(top * share))));
  const columns = weeks.map((week, i) => {
    const col = h("button", { type: "button", class: "col", "aria-label": `Week of ${dayPlain(week)}: ${Math.round(totals[i])} hours` },
      series.filter((s) => s.hours[i] > 0).map((s) =>
        h("i", { style: { height: `${(s.hours[i] / top) * 100}%`, "background-color": seriesColor(s) } })));
    return withTip(col, () => showTip(col, `Week of ${dayPlain(week)}`, [
      [`${Math.round(totals[i])} h`, "Total"],
      ...series.filter((s) => s.hours[i] > 0).map((s) => [`${Math.round(s.hours[i])} h`, s.name, seriesColor(s)]),
    ]));
  });
  const weekly = average(totals.slice(0, -1));
  const chart = h("div", null,
    h("div", { class: "plot" }, lines, h("div", { class: "cols" }, columns)),
    h("div", { class: "x-axis" }, weeks.map((week, i) => h("span", null, i % 3 === 0 ? dayPlain(week) : ""))),
    h("div", { class: "legend" }, series.map((s) =>
      h("span", null, h("i", { class: "key", style: { "background-color": seriesColor(s) } }), s.name,
        h("b", null, `${Math.round(s.hours.reduce((a, b) => a + b, 0))}h`)))));
  return tableCard("Hours", weekly != null && h("span", { class: "side" }, `${Math.round(weekly)}h a week`), chart,
    () => table(["Week", ...series.map((s) => s.name)], weeks.map((week, i) => [dayPlain(week), ...series.map((s) => s.hours[i])])));
}

// ---- temptations -------------------------------------------------------------------------------

function temptationsCard(doc) {
  const t = doc.temptations;
  if (!t.total) return null;
  const most = Math.max(...t.apps.map((a) => a.count));
  const peak = Math.max(...t.hours, 1);
  return card("Stopped", h("span", { class: "side" }, `${t.total} in ${t.days}d`),
    h("div", { class: "ranked" }, t.apps.map((a) => h("div", { class: "rank" },
      h("span", { class: "clip" }, a.name),
      h("div", { class: "rank-bar" }, h("i", { style: { width: `${(a.count / most) * 100}%` } })),
      h("b", null, a.count)))),
    h("h3", null, "By hour"),
    h("div", { class: "hours-of-day" }, t.hours.map((n, hour) => {
      const bar = h("span", { class: "hour", tabindex: 0, "aria-label": `${hour}:00, ${n}` },
        h("i", { style: { height: `${(n / peak) * 100}%` } }));
      return withTip(bar, () => showTip(bar, `${hour}:00`, [[n, "stopped"]]));
    })),
    h("div", { class: "x-axis four" }, [0, 6, 12, 18].map((n) => h("span", null, `${n}:00`))));
}

// ---- money -------------------------------------------------------------------------------------

function spendingCard(doc) {
  const m = doc.money;
  if (!m) return null;
  const done = m.months.slice(0, -1).filter((x) => x.spentCents || x.outsideCents);
  const avg = average(done.map((x) => x.spentCents));
  const chart = h("div", null,
    monthColumns(m.months, { budget: m.budgetCents, currency: m.currency }),
    h("div", { class: "legend" },
      h("span", null, h("i", { class: "key money-in" }), "Budget"),
      m.months.some((x) => x.outsideCents) && h("span", null, h("i", { class: "key money-out" }), "Outside"),
      m.budgetCents != null && h("span", null, h("i", { class: "key money-line" }), money(m.budgetCents, m.currency))));
  return tableCard("Spending", avg != null && h("span", { class: "side" }, `${money(Math.round(avg), m.currency)} a month`), chart,
    () => table(["Month", "Budget", "Outside", "In"], [...m.months].reverse().map((x) => [
      new Date(`${x.first}T12:00`).toLocaleDateString(undefined, { month: "short", year: "numeric" }),
      money(x.spentCents, m.currency), money(x.outsideCents, m.currency), money(x.earnedCents, m.currency),
    ])));
}

function monthCard(doc) {
  const m = doc.money;
  if (!m) return null;
  const now = m.months[m.months.length - 1];
  const left = m.budgetCents != null ? m.budgetCents - now.spentCents : null;
  const used = m.budgetCents ? now.spentCents / m.budgetCents : null;
  const most = Math.max(...m.categories.map((c) => c.cents), 1);
  return card("This month", h("span", { class: "side" }, `${dayPlain(now.first)} – ${dayPlain(now.last)}`),
    h("div", { class: "money-figures" },
      h("div", null, h("div", { class: "dt-label" }, "Spent"), h("div", { class: "dt-value" }, money(now.spentCents, m.currency))),
      left != null && h("div", null, h("div", { class: "dt-label" }, left < 0 ? "Over" : "Left"),
        h("div", { class: `dt-value${left < 0 ? " warn" : ""}` }, money(Math.abs(left), m.currency))),
      now.outsideCents > 0 && h("div", null, h("div", { class: "dt-label" }, "Outside"), h("div", { class: "dt-value" }, money(now.outsideCents, m.currency))),
      h("div", null, h("div", { class: "dt-label" }, "In"), h("div", { class: "dt-value" }, money(now.earnedCents, m.currency)))),
    used != null && h("div", { class: "spend-bar", role: "img", "aria-label": `${Math.round(used * 100)}% of the budget` },
      h("i", { class: used > 1 ? "over" : "", style: { width: `${Math.min(used, 1) * 100}%` } })),
    m.categories.length > 0 && h("div", { class: "ranked money-rank" }, m.categories.map((c) => h("div", { class: "rank" },
      h("span", { class: "clip" }, c.name),
      h("div", { class: "rank-bar" }, h("i", { style: { width: `${(c.cents / most) * 100}%` } })),
      h("b", null, money(c.cents, m.currency))))));
}

// ---- the page ----------------------------------------------------------------------------------

function draw(view, doc) {
  fill(view,
    h("h1", { class: "page-title" }, "Review"),
    h("div", { class: "review" },
      tiles(doc),
      yearCard(doc),
      h("div", { class: "rv-row" }, habitsCard(doc), bodyCard(doc)),
      h("div", { class: "rv-row" }, hoursCard(doc), temptationsCard(doc)),
      h("div", { class: "rv-row" }, spendingCard(doc), monthCard(doc))));
}

function skeleton(view) {
  fill(view, h("div", { class: "skeleton title" }), h("div", { class: "review" },
    h("div", { class: "dt-tiles" }, [1, 2, 3, 4].map(() => h("div", { class: "skeleton block short dt-tile" }))),
    h("div", { class: "skeleton block" }), h("div", { class: "skeleton block tall" })));
}

let shown = null;

async function refresh() {
  if (!target?.isConnected) return;
  const doc = await getJson(`api/review${takeChanged() ? "?fresh=1" : ""}`);
  const y = scrollY;
  shown = doc;
  draw(target, doc);
  scrollTo(0, y);
}

export async function showReview(view) {
  target = view;
  // Opened from the Overview's xrnfit card: straight to the health card, once there is one to show.
  const toHealth = location.hash === "#review/health";
  const jump = () => toHealth && document.getElementById("review-health")?.scrollIntoView({ block: "start" });
  const drawn = shown != null;
  if (drawn) {
    draw(view, shown);
    jump();
  } else skeleton(view);
  await refresh();
  if (!drawn) jump();
}

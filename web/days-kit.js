// What Today and Review share: a tile with a figure and a small chart, and the charts themselves (a
// fortnight of bars, a line of readings, a month's spending against its budget, the day as a ribbon).
// Built from numbers only; sizes and colours go through CSSOM, since the CSP forbids inline styles.

import { addDays, clockOf, dayShort, h, money, showTip, svg, withTip } from "./dom.js";

/** A tile: a label, a figure, an optional change, a chart and a line of figures under it. */
export function tile({ label, value, unit, delta, viz, foot, wide = false, tone = "", color, id }) {
  return h("section", { class: `dt-tile${wide ? " wide" : ""}`, id, style: color ? { "--viz": color } : {} },
    h("div", { class: "dt-label" }, label),
    h("div", { class: `dt-value ${tone}` }, value, unit && h("small", null, ` ${unit}`),
      delta && h("span", { class: `dt-delta ${delta.tone}`, title: delta.title }, delta.text)),
    viz && h("div", { class: "dt-viz" }, viz),
    foot && h("div", { class: "dt-foot" }, foot));
}

/** "▲ 12" against the period before, toned by whether up is good here; null when either is missing. */
export function delta(now, before, { upIsGood = true, format = String, title } = {}) {
  if (now == null || before == null) return null;
  const gap = now - before;
  if (gap === 0) return { tone: "", text: "=", title };
  return { tone: gap > 0 === upIsGood ? "good" : "bad", text: `${gap > 0 ? "▲" : "▼"} ${format(Math.abs(gap))}`, title };
}

/** A figure and its short label, for a tile's foot. */
export const figure = (value, label) => h("span", { class: "dt-fig" }, h("b", null, value), label && ` ${label}`);

const average = (values) => {
  const seen = values.filter((v) => v != null);
  return seen.length ? seen.reduce((a, b) => a + b, 0) / seen.length : null;
};
export { average };

/**
 * Bars from zero, one per value, oldest first; the one at `mark` (the last, by default) in full colour.
 * `line` draws a level across (an average, a budget), `tip(i)` gives [title, rows] for a bar's tip.
 */
export function bars(values, { mark = values.length - 1, line = null, tip, label = "" } = {}) {
  const seen = values.filter((v) => v != null);
  const top = Math.max(...seen, line ?? 0, 0) || 1;
  const cols = values.map((value, i) => {
    const col = h("span", { class: `mb-col${i === mark ? " mark" : ""}` },
      value > 0 && h("i", { style: { height: `${Math.max((value / top) * 100, 3)}%` } }));
    return tip ? withTip(col, () => showTip(col, ...tip(i))) : col;
  });
  return h("div", { class: "mini-bars", role: "img", "aria-label": label }, cols,
    line != null && line > 0 && h("b", { class: "mb-line", style: { bottom: `${(line / top) * 100}%` } }));
}

/** A line through readings that come now and then (weight, heart rate), each one a dot; gaps are skipped. */
export function readings(values, { tip, label = "" } = {}) {
  const seen = values.map((v, i) => [i, v]).filter(([, v]) => v != null);
  const box = h("div", { class: "mini-line", role: "img", "aria-label": label });
  if (!seen.length) return box;
  const low = Math.min(...seen.map(([, v]) => v));
  const high = Math.max(...seen.map(([, v]) => v));
  const xOf = (i) => ((i + 0.5) / values.length) * 100;
  const yOf = (v) => (high === low ? 50 : 12 + ((v - low) / (high - low)) * 76);
  const path = svg("path", { d: seen.map(([i, v], n) => `${n ? "L" : "M"}${xOf(i).toFixed(2)} ${(100 - yOf(v)).toFixed(2)}`).join(" ") });
  box.append(svg("svg", { viewBox: "0 0 100 100", preserveAspectRatio: "none", "aria-hidden": "true" }, path));
  for (const [i, v] of seen) {
    const dot = h("i", { class: i === values.length - 1 ? "mark" : "", style: { left: `${xOf(i)}%`, bottom: `${yOf(v)}%` } });
    box.append(tip ? withTip(dot, () => showTip(dot, ...tip(i))) : dot);
  }
  return box;
}

/**
 * This month's counted spending as it adds up, day by day, against the straight line that would end
 * on the budget: under the line is on pace. `days` is cents a day from the month's first day, `today`
 * the index of today in it.
 */
export function burnUp({ days, budget, today, first, currency }) {
  const n = days.length;
  const sums = [];
  let run = 0;
  for (const cents of days) sums.push((run += cents));
  const shown = Math.max(today, days.findLastIndex((c) => c > 0));
  const top = Math.max(budget ?? 0, sums[shown] ?? 0) * 1.08 || 1;
  const x = (i) => ((i + 1) / n) * 100;
  const y = (cents) => 100 - (cents / top) * 100;
  const points = sums.slice(0, shown + 1).map((s, i) => `${x(i).toFixed(2)} ${y(s).toFixed(2)}`);
  const over = budget != null && sums[today] > (budget * (today + 1)) / n;
  const plot = svg("svg", { viewBox: "0 0 100 100", preserveAspectRatio: "none", "aria-hidden": "true" },
    ...(budget != null ? [
      svg("line", { class: "bu-budget", x1: 0, x2: 100, y1: y(budget), y2: y(budget) }),
      svg("line", { class: "bu-pace", x1: 0, y1: 100, x2: 100, y2: y(budget) }),
    ] : []),
    svg("path", { class: "bu-area", d: `M0 100 L${points.join(" L")} L${x(shown).toFixed(2)} 100 Z` }),
    svg("path", { class: `bu-line${over ? " over" : ""}`, d: `M0 100 L${points.join(" L")}` }));
  const marker = h("b", { class: "bu-today", style: { left: `${x(today)}%` } });
  const hits = h("div", { class: "bu-hits" }, days.map((cents, i) => {
    const col = h("span");
    return withTip(col, () => showTip(col, dayShort(addDays(first, i)), [
      [money(cents, currency), "that day"],
      i <= shown ? [money(sums[i], currency), "so far"] : null,
      budget != null ? [money(Math.round((budget * (i + 1)) / n), currency), "on pace"] : null,
    ].filter(Boolean)));
  }));
  return h("div", { class: "burn-up", role: "img", "aria-label": "Spending this month against the budget" }, plot, marker, hits);
}

/** Months as columns: what counted against the budget, with what was left out of it stacked above. */
export function monthColumns(months, { budget, currency, height = 150 }) {
  const top = niceTop(Math.max(...months.map((m) => m.spentCents + m.outsideCents), budget ?? 0));
  const label = (m) => new Date(`${m.first}T12:00`).toLocaleDateString(undefined, { month: "short" });
  const cols = months.map((m, i) => {
    const col = h("span", { class: `mc-col${i === months.length - 1 ? " now" : ""}` },
      m.spentCents > 0 && h("i", { class: "in", style: { height: `${(m.spentCents / top) * 100}%` } }),
      m.outsideCents > 0 && h("i", { class: "out", style: { height: `${(m.outsideCents / top) * 100}%` } }));
    return withTip(col, () => showTip(col, new Date(`${m.first}T12:00`).toLocaleDateString(undefined, { month: "long", year: "numeric" }), [
      [money(m.spentCents, currency), "budget", "var(--svc-xrnwal)"],
      m.outsideCents > 0 ? [money(m.outsideCents, currency), "outside", "var(--outline)"] : null,
      [money(m.earnedCents, currency), "in"],
    ].filter(Boolean)));
  });
  return h("div", { class: "month-cols" },
    h("div", { class: "mc-plot", style: { height: `${height}px` } },
      h("div", { class: "mc-grid" }, [1, 0.5].map((share) => h("span", { style: { bottom: `${share * 100}%` } }, short(top * share, currency)))),
      h("div", { class: "mc-cols" }, cols),
      budget != null && h("b", { class: "mc-budget", style: { bottom: `${(budget / top) * 100}%` } })),
    h("div", { class: "mc-x" }, months.map((m, i) => h("span", null, i % 2 === (months.length - 1) % 2 ? label(m) : ""))));
}

function niceTop(value) {
  if (!(value > 0)) return 1;
  const base = 10 ** Math.floor(Math.log10(value));
  const step = [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10].find((s) => s * base >= value) ?? 10;
  return step * base;
}

/** Money with no cents, for an axis. */
function short(cents, currency) {
  return new Intl.NumberFormat(undefined, { style: "currency", currency: currency || "AUD", notation: "compact", maximumFractionDigits: 1 })
    .format(cents / 100);
}

/**
 * The day as a ribbon of hours: each timed event a block in its calendar's colour, laid in lanes where
 * two overlap, the part already gone shaded, and a line at now. Runs at least from 06:00 to midnight,
 * further when an event or now is outside that.
 */
export function ribbon(items, { day, now }) {
  const midnight = new Date(`${day}T00:00`);
  const hourOf = (date) => (date - midnight) / 3600000;
  const at = (iso) => hourOf(new Date(iso));
  // Something running on from the night before is cut at the ribbon's start; it doesn't stretch it.
  let lo = 6;
  let hi = 24;
  for (const e of items) {
    if (at(e.start) >= 0) lo = Math.min(lo, Math.floor(at(e.start)));
    hi = Math.max(hi, Math.ceil(Math.min(at(e.end), 30)));
  }
  const nowHour = hourOf(now);
  lo = Math.max(0, Math.min(lo, Math.floor(nowHour)));
  hi = Math.max(hi, Math.ceil(nowHour));
  items = items.filter((e) => at(e.end) > lo);
  const pos = (hour) => ((Math.min(Math.max(hour, lo), hi) - lo) / (hi - lo)) * 100;
  const lanes = [];
  const blocks = items.map((e) => {
    const start = at(e.start);
    const end = at(e.end);
    let lane = lanes.findIndex((last) => last <= start);
    if (lane < 0) lane = lanes.push(end) - 1;
    else lanes[lane] = end;
    return { e, start, end, lane };
  });
  const count = Math.min(Math.max(lanes.length, 1), 3);
  const ticks = [];
  for (let hour = Math.ceil(lo / 3) * 3; hour <= hi; hour += 3) ticks.push(hour);
  return h("div", { class: "ribbon", style: { "--lanes": count } },
    h("div", { class: "rb-track" },
      ticks.map((hour) => h("i", { class: "rb-tick", style: { left: `${pos(hour)}%` } })),
      h("div", { class: "rb-past", style: { width: `${pos(nowHour)}%` } }),
      blocks.filter((b) => b.lane < 3).map(({ e, start, end, lane }) => {
        const block = h("span", {
          class: `rb-event${end <= nowHour ? " over" : ""}`,
          tabindex: 0,
          "aria-label": `${clockOf(e.start)} ${e.title}`,
          style: {
            left: `${pos(start)}%`, width: `${Math.max(pos(end) - pos(start), 0.8)}%`,
            top: `${(lane / count) * 100}%`, height: `${100 / count}%`, "background-color": e.color || "var(--series)",
          },
        });
        return withTip(block, () => showTip(block, e.title, [[`${clockOf(e.start)}–${clockOf(e.end)}`, e.calendar]]));
      }),
      h("b", { class: "rb-now", style: { left: `${pos(nowHour)}%` } })),
    h("div", { class: "rb-hours" }, ticks.map((hour) => h("span", { style: { left: `${pos(hour)}%` } }, String(hour % 24).padStart(2, "0")))));
}

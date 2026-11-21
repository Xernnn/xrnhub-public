// The monitor's charts, tables and timelines. Everything is built from numbers: no text from a server
// becomes markup, and sizes go through CSSOM because the CSP forbids inline styles. A chart keeps its
// element across redraws, so a live one doesn't lose your pointer or focus every few seconds.
//
// Every chart card has the same parts at the same heights: a title row, one line of key, the plot
// and its time axis. The key holds the series' names and figures, so a chart with a legend is no
// taller than one without, and its table takes exactly the chart's place.

import { clock, dayPlain, fill, h, hideTip, isoOf, showTip, svg, withTip } from "./dom.js";
import { serviceLogo, serviceName } from "./serverkit.js";

// Each service keeps its logo's colour wherever it appears, so a filter never repaints one. The
// tokens in style.css carry a lighter step of the same hue where dark mode needs one.
const COLORS = {
  xrnloops: "var(--svc-xrnloops)",
  xrnrule: "var(--svc-xrnrule)",
  xrnhome: "var(--svc-xrnhome)",
  xrntube: "var(--svc-xrntube)",
  xrnwal: "var(--svc-xrnwal)",
  xrninsta: "var(--svc-xrninsta)",
  xrnfit: "var(--svc-xrnfit)",
  xrnhub: "var(--svc-xrnhub)",
  calendar: "var(--svc-calendar)",
  tailscale: "var(--svc-system)",
  ssh: "var(--svc-system)",
};
// xrnloops and xrnwal both have green logos, and xrninsta and xrnfit violet ones; among several
// lines xrnwal's and xrnfit's are dashed so each pair still tells apart. Stacks keep them apart by
// order: other services sit between them.
const DASHED = new Set(["xrnwal", "xrnfit"]);
export const SERVICE_ORDER = ["xrnloops", "xrnrule", "xrnhome", "xrntube", "xrnwal", "xrninsta", "xrnhub", "calendar", "xrnfit"];
export const colorOf = (name) => COLORS[name] ?? "var(--series)";
export const dashedOf = (name) => DASHED.has(name);

/** A series for one service: its name, colour and logo in the key. */
export const serviceSeries = (name, values) => ({ name: serviceName(name), values, color: colorOf(name), service: name, dash: dashedOf(name) });

/** Status classes and log levels, each its own colour wherever it's shown. */
export const STATUS_COLORS = { 2: "var(--open)", 3: "var(--series)", 4: "var(--warn)", 5: "var(--blocked)" };
export const LEVEL_COLORS = { info: "var(--faint)", warn: "var(--warn)", error: "var(--blocked)" };

const GAP_STEPS = 3.5; // this many usual steps with no sample is a hole, not a slope

function niceTop(value) {
  if (!(value > 0)) return 1;
  const base = 10 ** Math.floor(Math.log10(value));
  const step = [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10].find((s) => s * base >= value * 1.02) ?? 10;
  return step * base;
}

function timeLabel(ms, span) {
  const date = new Date(ms);
  return span <= 2 * 86400000 ? clock(date) : date.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

function tipTime(ms, span) {
  const date = new Date(ms);
  const day = date.toLocaleDateString(undefined, { day: "numeric", month: "short" });
  if (span <= 86400000) return clock(date);
  return span <= 8 * 86400000 ? `${day} ${clock(date)}` : day;
}

/** One series in a key: its logo, or a swatch and its name when there are several, then its figure. */
export function keyItem(series, value, many = true) {
  const logo = series.service && serviceLogo(series.service);
  return h("span", { class: "key-item", title: series.name || null },
    logo || (many && h("i", { class: "key", style: { "background-color": series.color } })),
    !logo && many && series.name && h("span", null, series.name),
    value != null && h("b", null, value));
}

function tableOf(head, rows) {
  return h("table", null,
    h("thead", null, h("tr", null, head.map((name) => h("th", null, name)))),
    h("tbody", null, rows.map((row) => h("tr", null, row.map((cell) => h("td", null, cell))))));
}

/** Pointer and arrow keys move one crosshair along a plot; `show(i)` does the rest. */
function follow(plot, cross, count, nearest, show, leave) {
  let active = -1;
  const go = (i) => {
    active = i;
    show(i);
  };
  const away = () => {
    active = -1;
    cross.hidden = true;
    hideTip();
    leave?.();
  };
  plot.addEventListener("pointermove", (e) => {
    const box = plot.getBoundingClientRect();
    const i = nearest((e.clientX - box.left) / box.width);
    if (i >= 0) go(i);
  });
  plot.addEventListener("pointerleave", away);
  plot.addEventListener("blur", away);
  plot.addEventListener("keydown", (e) => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    e.preventDefault();
    const n = count();
    if (!n) return;
    const from = active < 0 ? n - 1 : active;
    go(Math.max(0, Math.min(n - 1, from + (e.key === "ArrowRight" ? 1 : -1))));
  });
  return () => active;
}

function frame(label, height, ...layers) {
  const plot = h("div", { class: "chart-plot", tabindex: 0, role: "img", "aria-label": label, style: { height: `${height}px` } }, ...layers);
  const key = h("div", { class: "chart-key" });
  const xAxis = h("div", { class: "chart-x" });
  const el = h("div", { class: "chart" }, key, plot, xAxis);
  return { plot, key, xAxis, el };
}

/**
 * Lines over time. update({ at: [ms], from, to, gap, series: [{ name, values, color, dash, service }] }).
 * A missing value or a pause longer than `gap` ms leaves a hole: nothing was measured there. The key
 * reads the latest figures, or those under the pointer, so a line chart needs no tooltip.
 */
export function lineChart({ label, format, height = 150, floor = 0, zero = true }) {
  const grid = svg("g");
  const lines = svg("g");
  const box = svg("svg", { viewBox: "0 0 1000 100", preserveAspectRatio: "none", "aria-hidden": "true" }, grid, lines);
  const yLabels = h("div", { class: "y-labels" });
  const cross = h("div", { class: "crosshair", hidden: true });
  const dots = h("div", { class: "dots" });
  const { plot, key, xAxis, el } = frame(label, height, box, yLabels, cross, dots);
  let data = null;
  let low = 0;
  let top = 1;

  const xOf = (i) => ((data.at[i] - data.from) / (data.to - data.from)) * 1000;
  const yOf = (value) => 100 - ((value - low) / (top - low)) * 100;

  function segments(values) {
    const steps = data.at.slice(1).map((t, i) => t - data.at[i]).sort((a, b) => a - b);
    const usual = data.gap ?? (steps[steps.length >> 1] || 0) * GAP_STEPS;
    const found = [];
    let run = null;
    values.forEach((value, i) => {
      if (value == null) {
        run = null;
        return;
      }
      if (run && usual && data.at[i] - data.at[run.at(-1)] > usual) run = null;
      if (!run) {
        run = [];
        found.push(run);
      }
      run.push(i);
    });
    return found;
  }

  function drawn(series) {
    const parts = [];
    for (const run of segments(series.values)) {
      const line = run.map((i) => `${xOf(i).toFixed(1)} ${yOf(series.values[i]).toFixed(2)}`);
      if (data.series.length === 1 && run.length > 1) {
        const area = svg("path", {
          d: `M${line[0]} L${line.join(" L")} L${xOf(run.at(-1)).toFixed(1)} 100 L${xOf(run[0]).toFixed(1)} 100 Z`,
          class: "area",
        });
        area.style.fill = series.color;
        parts.push(area);
      }
      const dashed = series.dash && data.series.length > 1;
      const path = svg("path", { d: run.length > 1 ? `M${line.join(" L")}` : `M${line[0]} L${line[0]}`, class: dashed ? "line dashed" : "line" });
      path.style.stroke = series.color;
      parts.push(path);
    }
    return parts;
  }

  const nearestTo = (share) => {
    if (!data?.at.length) return -1;
    const wanted = data.from + share * (data.to - data.from);
    let best = -1;
    let gap = Infinity;
    data.at.forEach((t, i) => {
      if (Math.abs(t - wanted) < gap && data.series.some((s) => s.values[i] != null)) {
        gap = Math.abs(t - wanted);
        best = i;
      }
    });
    return best;
  };

  function readout(i) {
    const many = data.series.length > 1;
    const value = (s) => {
      const v = i == null ? s.values.findLast((x) => x != null) : s.values[i];
      return v == null ? "–" : format(v);
    };
    fill(key, i != null && h("time", null, tipTime(data.at[i], data.to - data.from)),
      data.series.map((s) => keyItem(s, value(s), many)));
  }

  function show(i) {
    cross.hidden = false;
    cross.style.left = `${xOf(i) / 10}%`;
    fill(dots, data.series.filter((s) => s.values[i] != null).map((s) =>
      h("i", { style: { left: `${xOf(i) / 10}%`, top: `${yOf(s.values[i])}%`, "background-color": s.color } })));
    readout(i);
  }

  const active = follow(plot, cross, () => data?.at.length ?? 0, nearestTo, show, () => {
    fill(dots);
    if (data) readout(null);
  });

  el.update = (next) => {
    data = next;
    const seen = next.series.flatMap((s) => s.values).filter((v) => v != null);
    const highest = seen.length ? Math.max(...seen) : 0;
    low = zero ? 0 : Math.floor(Math.min(floor, ...seen) / 5) * 5;
    top = low + niceTop(highest - low);
    if (!(top > low)) top = low + 1;
    grid.replaceChildren(...[0, 50].map((y) => svg("line", { x1: 0, x2: 1000, y1: y, y2: y, class: "grid-line" })),
      svg("line", { x1: 0, x2: 1000, y1: 100, y2: 100, class: "base-line" }));
    fill(yLabels, [top, (top + low) / 2].map((value, i) => h("span", { style: { top: `${i * 50}%` } }, format(value))));
    lines.replaceChildren(...next.series.flatMap(drawn));
    const span = next.to - next.from;
    fill(xAxis, h("span", null, timeLabel(next.from, span)), h("span", null, timeLabel((next.from + next.to) / 2, span)), h("span", null, timeLabel(next.to, span)));
    if (active() >= 0 && active() < next.at.length) show(active());
    else readout(null);
    el.drawn?.();
  };

  el.table = () => {
    if (!data) return null;
    const span = data.to - data.from;
    const rows = data.at.map((t, i) => [tipTime(t, span), ...data.series.map((s) => (s.values[i] == null ? "–" : format(s.values[i])))]);
    return tableOf(["When", ...data.series.map((s) => s.name || label)], rows.reverse());
  };
  return el;
}

/**
 * Stacked columns. update({ labels: [text], tips: [text], series: [{ name, values, color, service }] }).
 * The key gives each series' total over the chart; with several, a column's breakdown is in its tip.
 */
export function stackChart({ label, format, height = 150 }) {
  const cols = h("div", { class: "stack-cols" });
  const yLabels = h("div", { class: "y-labels" });
  const cross = h("div", { class: "crosshair", hidden: true });
  const { plot, key, xAxis, el } = frame(label, height, h("div", { class: "grid-mid" }), cols, yLabels, cross);
  let data = null;

  const count = () => data?.labels.length ?? 0;
  const many = () => data.series.length > 1;
  const sum = (values) => values.reduce((a, v) => a + (v || 0), 0);

  function readout(i) {
    const at = i == null ? null : i;
    fill(key, at != null && h("time", null, data.tips[at]),
      at != null && many() && h("span", { class: "key-item" }, h("b", null, format(sum(data.series.map((s) => s.values[at]))))),
      (at == null || !many()) && data.series.map((s) => keyItem(s, format(at == null ? sum(s.values) : s.values[at] || 0), many())));
  }

  const show = (i) => {
    cross.hidden = false;
    cross.style.left = `${((i + 0.5) / count()) * 100}%`;
    readout(i);
    if (!many()) return;
    const rows = data.series.filter((s) => s.values[i]).map((s) => [format(s.values[i]), s.name, s.color]);
    showTip(cross, data.tips[i], rows.length ? rows : [["0", ""]]);
  };
  const active = follow(plot, cross, count, (share) => (count() ? Math.min(count() - 1, Math.floor(share * count())) : -1), show,
    () => data && readout(null));

  el.update = (next) => {
    data = next;
    const n = next.labels.length;
    const totals = next.labels.map((_, i) => next.series.reduce((total, s) => total + (s.values[i] || 0), 0));
    // Counts read as whole numbers on the axis too: 0, 1, 2 rather than 0, 1, 1, and 30 over 15
    // rather than 25 over 13.
    const whole = totals.every(Number.isInteger);
    let top = Math.max(niceTop(Math.max(0, ...totals)), whole ? 2 : 0);
    while (whole && !Number.isInteger(top / 2)) top = niceTop(top);
    cols.classList.toggle("dense", n > 60);
    fill(cols, next.labels.map((_, i) => h("div", { class: "stack-col" },
      next.series.map((s) => s.values[i] > 0 && h("i", {
        style: { height: `${(s.values[i] / top) * 100}%`, "background-color": s.color },
      })))));
    fill(yLabels, [top, top / 2].map((value, i) => h("span", { style: { top: `${i * 50}%` } }, format(value))));
    fill(xAxis, n > 0 && [next.labels[0], next.labels[n >> 1], next.labels[n - 1]].map((text) => h("span", null, text)));
    if (active() >= 0 && active() < n) show(active());
    else readout(null);
    el.drawn?.();
  };

  el.table = () => {
    if (!data) return null;
    const rows = data.labels.map((_, i) => [data.tips[i], ...data.series.map((s) => format(s.values[i] || 0))]);
    return tableOf(["When", ...data.series.map((s) => s.name || label)], rows.reverse());
  };
  return el;
}

/** The labels for a run of time buckets: `at` in seconds, as the server sends them. */
export function bucketLabels(at, bucket) {
  const span = at.length * bucket * 1000;
  return {
    labels: at.map((s) => timeLabel(s * 1000, span)),
    tips: at.map((s) => tipTime(s * 1000, span)),
  };
}

export function dayLabels(days) {
  const names = days.map(dayPlain);
  return { labels: names, tips: names };
}

/** A card holding a chart, with its numbers one tap away in exactly the chart's space. */
export function chartCard(title, chart, side = null) {
  const table = h("div", { class: "chart-table", hidden: true });
  const toggle = h("button", {
    type: "button",
    class: "link",
    "aria-pressed": "false",
    onclick: () => {
      const on = table.hidden;
      if (on) table.style.height = `${chart.offsetHeight}px`;
      table.hidden = !on;
      chart.hidden = on;
      toggle.setAttribute("aria-pressed", String(on));
      toggle.textContent = on ? "Chart" : "Table";
      if (on) fill(table, chart.table());
    },
  }, "Table");
  chart.drawn = () => {
    if (!table.hidden) fill(table, chart.table());
  };
  return h("section", { class: "card chart-card" },
    h("div", { class: "card-head" }, h("h2", null, title), side, toggle),
    chart, table);
}

/** A small line for a tile. `series` is values, or [{ values, color }] for several on one scale. */
export function spark(series, color = "var(--series)") {
  const lines = Array.isArray(series[0]?.values) ? series : [{ values: series, color }];
  const seen = lines.flatMap((l) => l.values).filter((v) => v != null);
  const box = svg("svg", { viewBox: "0 0 100 30", preserveAspectRatio: "none", class: "spark", "aria-hidden": "true" });
  if (seen.length < 2) return box;
  const low = Math.min(...seen);
  const high = Math.max(...seen);
  const range = high - low || 1;
  for (const { values, color: stroke } of lines) {
    const step = 100 / Math.max(values.length - 1, 1);
    let d = "";
    let open = false;
    values.forEach((value, i) => {
      if (value == null) {
        open = false;
        return;
      }
      const y = 27 - ((value - low) / range) * 24;
      d += `${open ? "L" : "M"}${(i * step).toFixed(1)} ${y.toFixed(1)} `;
      open = true;
    });
    const path = svg("path", { d, class: "line" });
    path.style.stroke = stroke;
    box.append(path);
  }
  return box;
}

/** Four bars, as a phone shows its signal. */
export function signal(rssi) {
  const lit = rssi == null ? 0 : rssi >= -55 ? 4 : rssi >= -65 ? 3 : rssi >= -75 ? 2 : 1;
  return h("div", { class: "signal", "aria-hidden": "true" }, [1, 2, 3, 4].map((n) => h("i", { class: n <= lit ? "on" : null })));
}

/** A row of marks, one each, in a tone: services up or down, days since a reboot. */
export function marks(tones) {
  return h("div", { class: "marks", "aria-hidden": "true" }, tones.map((tone) => h("i", { class: tone || null })));
}

/** Thirty days, each a cell: all up, some down, down, or not watched yet. */
const UPTIME = (share) => [
  share == null ? "none" : share >= 0.999 ? "up" : share > 0 ? "some" : "out",
  share == null ? "Not watched" : share >= 0.999 ? "Up all day" : `Up ${(share * 100).toFixed(1)}%`,
];

/** A cell a day, oldest first. [read] turns a day's value into its tone and words; by default the
 * value is the share of the day a service was up. */
export function uptimeStrip(days, name, read = UPTIME) {
  const now = new Date();
  return h("div", { class: "uptime", role: "img", "aria-label": `${name}: last ${days.length} days` },
    days.map((value, i) => {
      const date = new Date(now.getFullYear(), now.getMonth(), now.getDate() - (days.length - 1 - i));
      const [tone, words] = read(value);
      const cell = h("i", { class: tone });
      return withTip(cell, () => showTip(cell, date.toLocaleDateString(undefined, { day: "numeric", month: "short" }), [[words, ""]]));
    }));
}

export function gauge(share, tone = "") {
  const fillBar = h("i", { class: tone || null, style: { width: `${Math.min(Math.max(share, 0), 1) * 100}%` } });
  return h("div", { class: "gauge", "aria-hidden": "true" }, fillBar);
}

/** A bar for a table cell: `share` of the longest, in `color`. */
export function bar(share, color = "var(--series)") {
  return h("span", { class: "bar", "aria-hidden": "true" },
    share > 0 && h("i", { style: { width: `${Math.max(Math.min(share, 1) * 100, 1.5)}%`, "background-color": color } }));
}

/** A bar made of parts side by side: [{ share, color }], each a share of the whole bar. */
export function splitBar(parts, length = 1) {
  return h("span", { class: "bar split", "aria-hidden": "true" },
    h("span", { style: { width: `${Math.max(Math.min(length, 1) * 100, 1.5)}%` } }, parts.filter((p) => p.share > 0).map((p) =>
      h("i", { style: { width: `${p.share * 100}%`, "background-color": p.color } }))));
}

/**
 * Rows of figures that line up. columns: [{ label, width, narrow, end, cell(row), className }]:
 * `width` is the column's grid track, `narrow` its track on a phone or null to leave it out there,
 * `end` sets figures to the right. A row may have `href` or `onclick`, `tone`, and `after` (an
 * element shown under it, across every column).
 */
export function dataRows(columns, rows, empty = "None") {
  if (!rows.length) return h("p", { class: "empty" }, empty);
  const wide = columns.map((c) => c.width).join(" ");
  const narrow = columns.filter((c) => c.narrow !== null).map((c) => c.narrow ?? c.width).join(" ");
  const classOf = (c, extra) => [c.end ? "end" : "", c.narrow === null ? "wide-only" : "", extra ?? ""].filter(Boolean).join(" ") || null;
  return h("div", { class: "data-rows", style: { "--cols": wide, "--cols-narrow": narrow } },
    columns.some((c) => c.label) && h("div", { class: "data-head", "aria-hidden": "true" },
      columns.map((c) => h("span", { class: classOf(c) }, c.label ?? ""))),
    rows.map((row) => [
      h(row.href ? "a" : row.onclick ? "button" : "div", {
        class: `data-row${row.tone ? ` ${row.tone}` : ""}`,
        href: row.href ?? null,
        type: row.onclick ? "button" : null,
        onclick: row.onclick ?? null,
        "aria-expanded": row.expanded == null ? null : String(row.expanded),
      }, columns.map((c) => h("span", { class: classOf(c, c.className) }, c.cell(row)))),
      row.after,
    ]));
}

/** The kinds of thing that happen, as the timeline colours them. */
export const KINDS = [
  ["down", "Down", "var(--blocked)"],
  ["restart", "Restart", "var(--warn)"],
  ["problem", "Problem", "var(--pair-2)"],
  ["reboot", "Reboot", "var(--pair-1)"],
  ["gap", "No data", "var(--outline)"],
];
const KIND_OF = { down: "down", restart: "restart", restarted: "restart", reboot: "reboot", gap: "gap" };
export const kindOf = (incident) => KIND_OF[incident.kind] ?? "problem";

const dayOfSeconds = (seconds) => dayPlain(isoOf(new Date(seconds * 1000)));

/**
 * Marks along a stretch of time, a lane each. rows: [{ label, figure, marks: [{ from, to, kind, title,
 * words }] }], in seconds; `to` null is still going, and a mark too short to see is a tick. Its
 * details are in its tip. `key` is [[label, color]] (the incident kinds by default) or false, and
 * `when` labels the axis (days by default).
 */
export function timeline(rows, from, to, { key = KINDS.map(([, label, color]) => [label, color]), axis = true, when = dayOfSeconds, weeks = true } = {}) {
  const span = to - from;
  const days = Math.round(span / 86400);
  const named = rows.some((r) => r.label != null);
  const figures = rows.some((r) => r.figure != null);
  const now = Date.now() / 1000;
  const lane = (label, track, figure) => h("div", { class: "lane" },
    named && h("div", { class: "lane-name" }, label),
    track,
    figures && h("div", { class: "lane-figure" }, figure));
  const markOf = (m) => {
    const end = Math.min(m.to ?? now, to);
    const start = Math.max(m.from, from);
    const wide = (end - start) / span > 0.004;
    const mark = h("b", {
      class: `mark ${m.kind}${wide ? "" : " tick"}${m.to == null ? " open" : ""}`,
      tabindex: 0,
      style: { left: `${((start - from) / span) * 100}%`, width: wide ? `${((end - start) / span) * 100}%` : null },
    });
    return withTip(mark, () => showTip(mark, m.title, m.words.map((w) => [w, ""])));
  };
  return h("div", { class: `timeline${named ? " named" : ""}${figures ? " counted" : ""}` },
    key && h("div", { class: "chart-key" }, key.map(([label, color]) => keyItem({ name: label, color }, null))),
    rows.map((row) => lane(row.label, h("div", { class: "lane-track" },
      // A line each week, so a mark can be placed by eye.
      weeks && Array.from({ length: Math.floor((days - 1) / 7) }, (_, w) =>
        h("i", { class: "week", style: { left: `${(1 - ((w + 1) * 7) / days) * 100}%` } })),
      row.marks.map(markOf)), row.figure)),
    axis && lane(null, h("div", { class: "chart-x" }, [from, (from + to) / 2, to - 1].map((t) => h("span", null, when(t)))), null));
}

/** The start of the day [count - 1] days ago and the end of today, in seconds: the strips' days. */
export function stripDays(count = 30) {
  const now = new Date();
  const first = new Date(now.getFullYear(), now.getMonth(), now.getDate() - (count - 1));
  const end = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1);
  return [first.getTime() / 1000, end.getTime() / 1000];
}

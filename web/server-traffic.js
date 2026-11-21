// Traffic: what's calling the server, for what, and how it was answered. The last fifteen minutes
// are a lane per service with a mark per request, coloured by its answer, not a list of lines.

import {
  SERVICE_ORDER, STATUS_COLORS, bar, bucketLabels, chartCard, colorOf, dataRows, keyItem, serviceSeries, splitBar, stackChart, timeline,
} from "./charts.js";
import { card, clock, fill, getJson, h } from "./dom.js";
import { RANGES, count, fillTiles, percent, rangeRow, serviceLabel, serviceLogo, tile } from "./serverkit.js";

const CLASSES = ["2", "3", "4", "5"];
const FEED_S = 15 * 60;

/** The share of answers in each status class: { "2": n, ... } and the total. */
function classesOf(mix) {
  const groups = Object.fromEntries(CLASSES.map((c) => [c, 0]));
  for (const [code, total] of Object.entries(mix)) groups[code[0]] = (groups[code[0]] ?? 0) + total;
  return { groups, all: Object.values(groups).reduce((a, b) => a + b, 0) };
}

/** One bar split by status class, with each class's share under it. */
function answers(mix) {
  const { groups, all } = classesOf(mix);
  if (!all) return h("p", { class: "empty" }, "None");
  return [
    h("div", { class: "share-bar", role: "img", "aria-label": "Answers by status" }, CLASSES.filter((c) => groups[c]).map((c) =>
      h("i", { style: { width: `${(groups[c] / all) * 100}%`, "background-color": STATUS_COLORS[c] } }))),
    h("div", { class: "share-key" }, CLASSES.filter((c) => groups[c]).map((c) => keyItem({ name: `${c}xx`, color: STATUS_COLORS[c] },
      `${percent((groups[c] / all) * 100)} · ${count(groups[c])}`))),
  ];
}

const pct = (part, whole) => (whole ? percent((part / whole) * 100) : "–");

export function trafficPage(box, shared) {
  const ranges = h("div");
  const tiles = h("div", { class: "live-tiles" });
  const chart = stackChart({ label: "Requests by service", format: (v) => Math.round(v).toLocaleString(), height: 180 });
  let hideTimers = true;
  let feedTimers = false;
  const timers = h("button", {
    type: "button", class: "toggle", "aria-pressed": "false",
    onclick: () => {
      hideTimers = !hideTimers;
      timers.setAttribute("aria-pressed", String(!hideTimers));
      drawChart();
    },
  }, "Timers");
  const feedToggle = h("button", {
    type: "button", class: "toggle", "aria-pressed": "false",
    onclick: () => {
      feedTimers = !feedTimers;
      feedToggle.setAttribute("aria-pressed", String(feedTimers));
      drawFeed();
    },
  }, "Timers");
  const status = h("div");
  const services = h("div");
  const paths = h("div");
  const clients = h("div");
  const feed = h("div");
  fill(box, ranges, tiles,
    chartCard("Requests", chart, timers),
    card("Answers", null, status),
    card("By service", null, services),
    h("div", { class: "grid2" }, card("Busiest paths", null, paths), card("Who's calling", null, clients)),
    h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", null, "Last 15 minutes"), feedToggle), feed));
  let range = shared.range === "live" ? "1h" : shared.range;
  let history = null;

  function drawChart() {
    if (!history) return;
    const names = SERVICE_ORDER.filter((n) => n in history.traffic);
    chart.update({
      ...bucketLabels(history.at, history.bucket),
      series: names.map((name) => {
        const { reqs, polls } = history.traffic[name];
        return serviceSeries(name, reqs.map((r, i) => (r == null ? 0 : hideTimers ? r - (polls[i] || 0) : r)));
      }),
    });
  }

  /** A lane per service, a mark per request in its status's colour; method, path and caller in its tip. */
  function drawFeed() {
    const to = Date.now() / 1000;
    const from = to - FEED_S;
    const rows = (shared.now?.feed ?? []).filter((f) => (feedTimers || !f.poll) && f.at / 1000 >= from);
    const by = new Map();
    for (const f of rows) {
      if (!by.has(f.service)) by.set(f.service, []);
      by.get(f.service).push(f);
    }
    const names = [...SERVICE_ORDER.filter((n) => by.has(n)), ...[...by.keys()].filter((n) => !SERVICE_ORDER.includes(n))];
    fill(feed, names.length ? timeline(names.map((name) => ({
      label: serviceLabel(name),
      figure: count(by.get(name).length),
      marks: by.get(name).map((f) => ({
        from: f.at / 1000, to: f.at / 1000, kind: `s${String(f.status)[0]}`,
        title: `${f.method} ${f.path}`, words: [`${f.status} · ${clock(new Date(f.at))}`, f.ip],
      })),
    })), from, to, {
      key: CLASSES.map((c) => [`${c}xx`, STATUS_COLORS[c]]),
      when: (t) => clock(new Date(t * 1000)),
      weeks: false,
    }) : h("p", { class: "empty" }, "None"));
  }

  function drawRanges() {
    fill(ranges, rangeRow(RANGES, range, (id) => {
      range = id;
      drawRanges();
      page.refresh().catch(() => {});
    }));
  }

  const page = {
    live: true,
    feed: true,
    onNow: drawFeed,
    async refresh() {
      const [doc, past] = await Promise.all([
        getJson(`api/server/traffic?range=${range}`), getJson(`api/server/history?range=${range}`),
      ]);
      history = past;
      drawChart();
      const all = Object.values(doc.totals);
      const sum = (key) => all.reduce((total, t) => total + t[key], 0);
      const reqs = sum("reqs");
      fillTiles(tiles,
        tile({ label: "Requests", value: count(reqs), sub: `${count(sum("polls"))} on a timer` }),
        tile({ label: "Saved", value: count(sum("writes")), sub: "changes" }),
        tile({ label: "Refused", value: count(sum("refused")), sub: pct(sum("refused"), reqs), href: "#server/security", tone: sum("refused") ? "warn" : "" }),
        tile({ label: "Failed", value: count(sum("errors")), sub: pct(sum("errors"), reqs), tone: sum("errors") ? "bad" : "" }));
      fill(status, answers(doc.status));

      const named = Object.entries(doc.totals).sort((a, b) => b[1].reqs - a[1].reqs);
      const most = Math.max(1, ...named.map(([, t]) => t.reqs));
      fill(services, dataRows([
        { label: "Service", width: "minmax(0, 150px)", narrow: "minmax(0, 1fr)", cell: ([name]) => h("span", { class: "name" }, serviceLabel(name)) },
        { width: "minmax(0, 1fr)", narrow: null, cell: ([name, t]) => bar(t.reqs / most, colorOf(name)) },
        { label: "Requests", width: "76px", narrow: "68px", end: true, cell: ([, t]) => h("b", null, count(t.reqs)) },
        { label: "Timers", width: "64px", narrow: null, end: true, cell: ([, t]) => h("span", { class: "muted" }, count(t.polls)) },
        { label: "Saved", width: "60px", narrow: null, end: true, cell: ([, t]) => count(t.writes) },
        { label: "Refused", width: "64px", narrow: "56px", end: true, className: "", cell: ([, t]) => h("span", { class: t.refused ? "code warn" : "muted" }, count(t.refused)) },
        { label: "Failed", width: "56px", narrow: "48px", end: true, cell: ([, t]) => h("span", { class: t.errors ? "code error" : "muted" }, count(t.errors)) },
      ], named.map(([name, t]) => Object.assign([name, t], { href: `#server/services/${name}` })), "None"));

      const top = doc.paths[0]?.count || 1;
      fill(paths, dataRows([
        { label: "Path", width: "minmax(0, 1fr)", cell: (p) => h("span", { class: "name mono", title: p.path }, serviceLogo(p.service), h("span", null, p.path)) },
        { width: "minmax(40px, 30%)", narrow: "48px", cell: (p) => bar(p.count / top, colorOf(p.service)) },
        { label: "Requests", width: "64px", end: true, cell: (p) => h("b", null, count(p.count)) },
      ], doc.paths, "None"));

      const busiest = doc.clients[0]?.reqs || 1;
      fill(clients, dataRows([
        { label: "Caller", width: "minmax(0, 1fr)", cell: (c) => h("span", { class: "mono", title: c.ip }, c.ip) },
        { width: "minmax(40px, 30%)", narrow: "48px", cell: (c) => splitBar([
          { share: (c.reqs - c.refused) / c.reqs, color: "var(--series)" }, { share: c.refused / c.reqs, color: STATUS_COLORS[4] }], c.reqs / busiest) },
        { label: "Requests", width: "64px", end: true, cell: (c) => h("b", null, count(c.reqs)) },
        { label: "Refused", width: "60px", narrow: "56px", end: true, cell: (c) => h("span", { class: c.refused ? "code warn" : "muted" }, count(c.refused)) },
      ], doc.clients, "None"));
    },
  };
  drawRanges();
  return page;
}

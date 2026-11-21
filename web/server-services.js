// Services: each one's state, then one in full, with the only button here that changes anything.
// xrnfit is listed too, after the hub that keeps what it sends: it has no process here, so its row
// says when it last sent and its strip how many readings came in a day.
//
// What went wrong is a timeline, a lane per service over the strips' thirty days, rather than a
// list: a restart is a tick, an outage a bar, and the words are in each mark's tip.

import {
  LEVEL_COLORS, bar, bucketLabels, chartCard, colorOf, dataRows, kindOf, lineChart, stackChart, stripDays, timeline, uptimeStrip,
} from "./charts.js";
import { fill, getJson, h, postJson, toast } from "./dom.js";
import {
  FIT, FIT_KEPT_BY, RANGES, besideHub, bytes, count, duration, fillTiles, fitAgo, fitTone, incidentWords, moment, percent, rangeRow,
  sentDay, serviceLabel, serviceLogo, serviceName, sinceNow, tile,
} from "./serverkit.js";

const STRIP_DAYS = 30;
// Lanes for what isn't a service, after the services.
const PLACES = { phone: "Phone", monitor: "Monitor", laptop: "Laptop", network: "Network" };

const isUp = (s) => s.state === "run" && s.answers !== false;

/** A mark for each incident: its words, when, and how long, in its tip. */
function markOf(i) {
  const lasted = i.ended != null && i.ended > i.started ? duration(i.ended - i.started) : null;
  return {
    from: i.started,
    to: i.ended,
    kind: kindOf(i),
    title: incidentWords(i),
    words: [[moment(i.started), i.ended == null ? "Ongoing" : lasted].filter(Boolean).join(" · "), i.detail].filter(Boolean),
  };
}

/** A lane for each service or place anything happened to: services in [order], then the phone,
 * the monitor and the rest. */
export function incidentLanes(incidents, order) {
  const by = new Map();
  for (const i of incidents) {
    const target = i.target || (i.kind === "gap" ? "monitor" : "phone");
    if (!by.has(target)) by.set(target, []);
    by.get(target).push(markOf(i));
  }
  const names = [...order.filter((n) => by.has(n)), ...Object.keys(PLACES).filter((n) => by.has(n)),
    ...[...by.keys()].filter((n) => !order.includes(n) && !(n in PLACES))];
  return names.map((name) => ({
    label: PLACES[name] ?? serviceLabel(name),
    figure: count(by.get(name).length),
    marks: by.get(name),
  }));
}

/** A figure with a thin bar under it: its share of the column's largest. */
function figureBar(text, share, color) {
  return h("span", { class: "fig-bar" }, h("span", null, text), bar(share, color));
}

const rowOf = (s, hour, strip, most) => {
  const requests = hour?.[s.name] ? hour[s.name].reqs - hour[s.name].polls : null;
  const color = colorOf(s.name);
  return h("a", { class: "service-row", href: `#server/services/${s.name}` },
    h("i", { class: `dot ${isUp(s) ? "up" : "out"}` }),
    h("b", null, serviceLabel(s.name)),
    h("span", { class: `cell-state${isUp(s) ? "" : " out"}` }, isUp(s) ? (s.since ? sinceNow(s.since) : "Up") : "Down"),
    h("span", { class: "num wide-only" }, figureBar(s.cpu == null ? "–" : percent(s.cpu), (s.cpu ?? 0) / most.cpu, color)),
    h("span", { class: "num" }, figureBar(bytes(s.rss), (s.rss ?? 0) / most.rss, color)),
    h("span", { class: "num" }, figureBar(requests == null ? "–" : count(requests), (requests ?? 0) / most.reqs, color)),
    strip ?? h("span"));
};

const fitRow = (card, hour, strip, most) => {
  const requests = hour?.[FIT] ? hour[FIT].reqs - hour[FIT].polls : null;
  return h("a", { class: "service-row", href: `#server/services/${FIT}` },
    h("i", { class: `dot ${fitTone(card)}` }),
    h("b", null, serviceLabel(FIT)),
    h("span", { class: `cell-state${card.late ? " late" : ""}` }, fitAgo(card)),
    h("span", { class: "num wide-only" }, "–"),
    h("span", { class: "num" }, "–"),
    h("span", { class: "num" }, figureBar(requests == null ? "–" : count(requests), (requests ?? 0) / most.reqs, colorOf(FIT))),
    strip ?? h("span"));
};

export function servicesPage(box, shared) {
  const list = h("div", { class: "service-rows" });
  const happened = h("div");
  fill(box, h("section", { class: "card" },
    h("div", { class: "service-head", "aria-hidden": "true" },
      h("span"), h("span", null, "Service"), h("span", null, "Up for"), h("span", { class: "num wide-only" }, "CPU"),
      h("span", { class: "num" }, "Memory"), h("span", { class: "num" }, "Req/h"), h("span", { class: "wide-only" }, "30 days")),
    list),
  h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", null, "What happened")), happened));
  let uptime = {};
  let fitDays = null;

  const draw = (doc) => {
    const services = doc.services ?? [];
    const hour = doc.hour ?? {};
    const most = {
      cpu: Math.max(1, ...services.map((s) => s.cpu ?? 0)),
      rss: Math.max(1, ...services.map((s) => s.rss ?? 0)),
      reqs: Math.max(1, ...Object.values(hour).map((t) => t.reqs - t.polls)),
    };
    const rows = services.map((s) => rowOf(s, hour, uptime[s.name] ? uptimeStrip(uptime[s.name], serviceName(s.name)) : null, most));
    const fit = doc.health && fitRow(doc.health, hour, fitDays ? uptimeStrip(fitDays, serviceName(FIT), sentDay) : null, most);
    fill(list, besideHub(services, rows, fit));
  };

  return {
    live: true,
    onNow: draw,
    async refresh() {
      const doc = await getJson("api/server/services");
      uptime = doc.uptime;
      fitDays = doc.fitDays ?? null;
      const services = shared.now?.services ?? Object.keys(uptime).map((name) => ({ name }));
      const lanes = incidentLanes(doc.timeline ?? doc.incidents, besideHub(services, services.map((s) => s.name), FIT));
      const [from, to] = stripDays(STRIP_DAYS);
      fill(happened, lanes.length ? timeline(lanes, from, to) : h("p", { class: "empty" }, "Nothing in 30 days"));
      if (shared.now) draw(shared.now);
    },
  };
}

/** The thirty days card: a cell a day, as tall as the card beside it, then the service's lane. */
function stripCard(title) {
  const figures = h("div", { class: "head-figures" });
  const strip = h("div", { class: "strip-body" });
  const lane = h("div");
  const el = h("section", { class: "card strip-card" },
    h("div", { class: "card-head" }, h("h2", null, title), figures), strip, lane);
  el.draw = ({ cells, marks: own, sums }) => {
    fill(strip, cells ?? h("p", { class: "empty" }, "Nothing yet"));
    const [from, to] = stripDays(STRIP_DAYS);
    fill(lane, own && timeline([{ marks: own }], from, to, { axis: false }));
    fill(figures, sums.map(([value, label]) => h("span", null, h("b", null, value), ` ${label}`)));
  };
  return el;
}

const SHOWN_MESSAGES = 8;
// A refused request reads best as its method, path and status; anything else as its latest words.
const REQUEST_LINE = /^\S+ "[A-Z]+ /;

/** Warnings and errors by what they say, the most pressing few, each one's newest lines a tap away. */
export function messageRows(groups, { withService = true, empty = "None" } = {}) {
  const most = Math.max(1, ...groups.map((g) => g.count));
  const open = new Set();
  let all = false;
  const box = h("div");
  const more = () => groups.length > SHOWN_MESSAGES && h("button", {
    type: "button", class: "link more", "aria-expanded": String(all),
    onclick: () => {
      all = !all;
      draw();
    },
  }, all ? "Fewer" : `All ${groups.length}`);
  const draw = () => fill(box, dataRows([
    { width: "48px", cell: (g) => h("span", { class: `code ${g.level}` }, g.level === "error" ? "Error" : "Warn") },
    ...(withService ? [{
      width: "minmax(0, 140px)", narrow: "20px",
      cell: (g) => h("span", { class: "name" }, serviceLogo(g.service, true), h("span", { class: "wide-only" }, serviceName(g.service))),
    }] : []),
    { width: "minmax(0, 1fr)", cell: (g) => {
      const words = REQUEST_LINE.test(g.text) ? g.sig : g.text;
      return h("span", { class: "mono", title: words }, words);
    } },
    { width: "minmax(40px, 110px)", narrow: null, cell: (g) => bar(g.count / most, LEVEL_COLORS[g.level]) },
    { label: "Count", width: "56px", narrow: "44px", end: true, cell: (g) => h("b", null, count(g.count)) },
    { label: "Last", width: "96px", narrow: null, end: true, cell: (g) => h("span", { class: "muted" }, moment(g.last / 1000)) },
  ], (all ? groups : groups.slice(0, SHOWN_MESSAGES)).map((g) => {
    const key = `${g.service}\n${g.level}\n${g.sig}`;
    const shown = open.has(key);
    return {
      ...g,
      expanded: shown,
      onclick: () => {
        if (shown) open.delete(key);
        else open.add(key);
        draw();
      },
      after: shown && linesOf(g),
    };
  }), empty), more());
  draw();
  return box;
}

/** The newest lines of one message, fetched when it's opened. */
function linesOf(group) {
  const box = h("div", { class: "group-lines log-lines" });
  const query = `service=${encodeURIComponent(group.service)}&sig=${encodeURIComponent(group.sig)}&limit=12`;
  getJson(`api/server/logs?${query}`).then((doc) => fill(box, doc.lines.length ? doc.lines.map((row) => logLine(row))
    : h("p", { class: "empty" }, "Gone from the log")))
    .catch(() => fill(box, h("p", { class: "empty" }, "Can't reach the server")));
  return box;
}

/** The share of watched days' time a service was up. */
function uptimeShare(days) {
  const seen = (days ?? []).filter((d) => d != null);
  if (!seen.length) return null;
  const share = (seen.reduce((a, b) => a + b, 0) / seen.length) * 100;
  return share >= 99.95 ? "100" : share.toFixed(1);
}

/** Errors and warnings over time, from the log's counts. */
export function levelSeries(stats, levels) {
  const values = (level) => stats.at.map((_, i) => levels?.[level]?.[i] ?? 0);
  return {
    ...bucketLabels(stats.at, stats.bucket),
    series: [
      { name: "Errors", values: values("error"), color: LEVEL_COLORS.error },
      { name: "Warnings", values: values("warn"), color: LEVEL_COLORS.warn },
    ],
  };
}

function warningsCard(name, messages) {
  return h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", null, "Warnings and errors"),
    h("a", { class: "link", href: `#server/logs/${name}` }, "Logs")), messages);
}

export function serviceDetail(box, shared, name) {
  if (name === FIT) return fitDetail(box);
  const head = h("div", { class: "detail-head" });
  const tiles = h("div", { class: "live-tiles" });
  const ranges = h("div");
  const cpu = lineChart({ label: `${name} CPU`, format: percent });
  const memory = lineChart({ label: `${name} memory`, format: bytes });
  const requests = stackChart({ label: `${name} requests`, format: (v) => Math.round(v).toLocaleString() });
  const lines = stackChart({ label: `${name} warnings and errors`, format: (v) => Math.round(v).toLocaleString() });
  const days = stripCard("Last 30 days");
  const messages = h("div");
  fill(box, head, ranges, tiles,
    h("div", { class: "grid2" }, chartCard("CPU", cpu), chartCard("Memory", memory)),
    h("div", { class: "grid2" }, chartCard("Requests", requests), chartCard("Warnings", lines)),
    days, warningsCard(name, messages));
  let range = "24h";

  async function restart(button) {
    if (!confirm(`Restart ${serviceName(name)}?`)) return;
    button.disabled = true;
    try {
      const res = await (await postJson("api/server/restart", { service: name })).json();
      toast(res.ok ? `${serviceName(name)} restarted` : `Restart failed: ${res.said}`);
    } catch (e) {
      toast(e.message);
    }
    button.disabled = false;
  }

  function drawHead(s) {
    const up = isUp(s);
    fill(head,
      h("a", { class: "link", href: "#server/services" }, "Services"),
      h("h1", null, h("i", { class: `dot ${up ? "up" : "out"}` }), serviceLabel(name)),
      s.restartable && h("button", { type: "button", class: "quiet", onclick: (e) => restart(e.currentTarget) }, "Restart"));
  }

  function draw(doc) {
    const s = doc.services?.find((row) => row.name === name);
    if (!s) return;
    const hour = doc.hour?.[name];
    if (!head.childElementCount) drawHead(s);
    fillTiles(tiles,
      tile({ label: "Up for", value: isUp(s) ? (s.since ? sinceNow(s.since) : "Up") : "Down", sub: s.since ? `Since ${moment(s.since)}` : "", tone: isUp(s) ? "" : "bad" }),
      tile({ label: "CPU", value: percent(s.cpu), sub: "of one core" }),
      tile({ label: "Memory", value: bytes(s.rss), sub: s.threads ? `${s.threads} threads` : "" }),
      tile({ label: "Requests", value: hour ? count(hour.reqs) : "–", sub: "last hour" }),
      tile({ label: "CPU time", value: duration(s.cpuTotal), sub: "since start" }),
      tile({ label: "Read", value: bytes(s.read), sub: "since start" }),
      tile({ label: "Written", value: bytes(s.written), sub: "since start" }),
      tile({ label: "To storage", value: bytes(s.diskWritten), sub: "since start" }));
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
    onNow: draw,
    async refresh() {
      const [history, doc, stats] = await Promise.all([
        getJson(`api/server/history?range=${range}&service=${name}`),
        getJson("api/server/services"),
        getJson(`api/server/logstats?range=${range}&service=${name}`),
      ]);
      const at = history.at.map((s) => s * 1000);
      const from = at[0];
      const to = at.at(-1) + history.bucket * 1000;
      const series = history.services[name];
      const color = colorOf(name);
      cpu.update({ at, from, to, series: [{ name: "", values: series?.cpu ?? at.map(() => null), color }] });
      memory.update({ at, from, to, series: [{ name: "", values: series?.rss ?? at.map(() => null), color }] });
      const traffic = history.traffic[name];
      requests.update({ ...bucketLabels(history.at, history.bucket), series: [{ name: "", values: traffic?.reqs ?? [], color }] });
      lines.update(levelSeries(stats, stats.services[name]));
      const own = (doc.timeline ?? doc.incidents).filter((i) => i.target === name);
      const share = uptimeShare(doc.uptime[name]);
      days.draw({
        cells: doc.uptime[name] && uptimeStrip(doc.uptime[name], serviceName(name)),
        marks: own.map(markOf),
        sums: [[share == null ? "–" : `${share}%`, "up"], [count(own.filter((i) => kindOf(i) === "restart").length), "restarts"],
          [count(own.filter((i) => kindOf(i) === "down").length), "down"]],
      });
      fill(messages, messageRows(stats.groups, { withService: false }));
    },
  };
  drawRanges();
  return page;
}

/** xrnfit in full: when it last sent, what it sent and read, and its lines in the hub's log. */
function fitDetail(box) {
  const head = h("div", { class: "detail-head" });
  const tiles = h("div", { class: "live-tiles" });
  const ranges = h("div");
  const requests = stackChart({ label: `${FIT} requests`, format: (v) => Math.round(v).toLocaleString() });
  const days = stripCard("Last 30 days");
  const messages = h("div");
  fill(box, head, ranges, tiles, h("div", { class: "grid2" }, chartCard("Requests", requests), days), warningsCard(FIT, messages));
  let range = "24h";

  function draw(doc) {
    const fit = doc.health;
    if (!fit) return;
    const hub = doc.services?.find((s) => s.name === FIT_KEPT_BY);
    const hour = doc.hour?.[FIT];
    fill(head,
      h("a", { class: "link", href: "#server/services" }, "Services"),
      h("h1", null, h("i", { class: `dot ${fitTone(fit)}` }), serviceLabel(FIT)));
    fillTiles(tiles,
      tile({
        label: "Last sent", value: fit.lastIn == null ? "–" : sinceNow(fit.lastIn), sub: fit.lastIn == null ? "" : moment(fit.lastIn),
        tone: fit.late ? "warn" : "",
      }),
      tile({ label: "Readings", value: fit.week == null ? "–" : count(fit.week), sub: "this week" }),
      tile({ label: "Requests", value: hour ? count(hour.reqs) : "–", sub: "last hour" }),
      tile({
        label: "Server", value: serviceLabel(FIT_KEPT_BY), href: `#server/services/${FIT_KEPT_BY}`,
        sub: hub ? (isUp(hub) ? (hub.since ? `Up · ${sinceNow(hub.since)}` : "Up") : "Down") : "",
      }));
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
    onNow: draw,
    async refresh() {
      const [history, doc, stats] = await Promise.all([
        getJson(`api/server/history?range=${range}&service=${FIT}`),
        getJson("api/server/services"),
        getJson(`api/server/logstats?range=${range}&service=${FIT}`),
      ]);
      const traffic = history.traffic[FIT];
      const writes = traffic?.writes ?? [];
      const reads = (traffic?.reqs ?? []).map((n, i) => (n == null ? null : n - (writes[i] ?? 0)));
      requests.update({ ...bucketLabels(history.at, history.bucket), series: [
        { name: "Sent", values: writes, color: "var(--pair-1)" }, { name: "Read", values: reads, color: "var(--pair-2)" }] });
      const sent = (doc.fitDays ?? []).filter((n) => n != null);
      days.draw({
        cells: doc.fitDays && uptimeStrip(doc.fitDays, serviceName(FIT), sentDay),
        marks: null,
        sums: [[count(sent.filter((n) => n > 0).length), "days sent"], [count(sent.reduce((a, b) => a + b, 0)), "readings"]],
      });
      fill(messages, messageRows(stats.groups, { withService: false }));
    },
  };
  drawRanges();
  return page;
}

export function logLine(row, withService = false) {
  const date = new Date(row.at);
  return h("div", { class: `log-line ${row.level}` },
    h("time", null, moment(date.getTime() / 1000)),
    withService === true && h("b", null, serviceLogo(row.service), serviceName(row.service)),
    h("code", null, row.text));
}

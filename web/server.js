// The Server page: the home server, watched. This file is the shell and the Overview; each other
// section has a file of its own. While a live section is open the page asks every few seconds, and
// the phone only samples that quickly because it's being asked.

import { SERVICE_ORDER, bucketLabels, chartCard, gauge, lineChart, marks, serviceSeries, signal, spark, stackChart } from "./charts.js";
import { SignedOut, fill, getJson, h } from "./dom.js";
import { dataPage } from "./server-data.js";
import { logsPage } from "./server-logs.js";
import { securityPage } from "./server-security.js";
import { serviceDetail, servicesPage } from "./server-services.js";
import { trafficPage } from "./server-traffic.js";
import {
  FIT, RANGES, besideHub, bytes, degrees, duration, fillTiles, fitAgo, fitTone, moment, percent, perSecond, problemWords, rangeRow,
  serviceLabel, serviceLogo, sinceNow, tile, wifiWord,
} from "./serverkit.js";

const POLL_MS = 3000;
const REFRESH_MS = 60000;
const LIVE_WINDOW_MS = 10 * 60000;
const LIVE_GAP_MS = 150000; // points may be a minute apart until someone's watching
const STALE_MS = 150000;
const RING_SIZE = 240;
const STRIP_DAYS = 30;
const SECTIONS = [
  ["overview", "Overview"], ["services", "Services"], ["traffic", "Traffic"],
  ["data", "Data"], ["security", "Security"], ["logs", "Logs"],
];
const OVERVIEW_RANGES = [["live", "Live"], ...RANGES];
const HISTORY = {
  load: { column: "load1" },
  speed: { column: "speed" },
  cpu: { column: "cpu" },
  memUsed: { column: "mem_used" },
  cpuTemp: { column: "cpu_temp" },
  battTemp: { column: "batt_temp" },
  netIn: { column: "net_in", perBucket: true },
  netOut: { column: "net_out", perBucket: true },
};

const shared = { now: null, ring: [], misses: 0, range: "live" };
let session = null;

export function stopServer() {
  if (session) {
    clearInterval(session.pollTimer);
    clearInterval(session.refreshTimer);
    session.page.stop?.();
  }
  session = null;
}

async function poll() {
  const mine = session;
  if (!mine?.page.live || document.visibilityState !== "visible") return;
  const since = shared.ring.at(-1)?.at ?? 0;
  try {
    const doc = await getJson(`api/server/now?since=${since}${mine.page.feed ? "&feed=1" : ""}`);
    if (mine !== session) return;
    shared.now = doc;
    shared.misses = 0;
    shared.ring.push(...doc.samples);
    shared.ring.splice(0, Math.max(0, shared.ring.length - RING_SIZE));
    mine.page.onNow?.(doc);
  } catch (e) {
    if (e instanceof SignedOut || mine !== session) return;
    shared.misses += 1;
    mine.page.onMiss?.();
  }
}

function refresh() {
  if (document.visibilityState === "visible") session?.page.refresh?.().catch(() => {});
}

export async function showServer(view) {
  const [, section = "overview", detail] = location.hash.slice(1).split("/");
  const known = SECTIONS.some(([id]) => id === section);
  const route = `${known ? section : "overview"}/${detail ?? ""}`;
  if (session?.route === route && view.contains(session.box)) {
    await poll();
    return;
  }
  stopServer();
  const box = h("div", { class: "server-body" });
  fill(view, h("nav", { class: "subnav", "aria-label": "Server sections" }, SECTIONS.map(([id, label]) =>
    h("a", { href: `#server/${id}`, "aria-current": id === section || (!known && id === "overview") ? "page" : null }, label))), box);
  const page = !known || section === "overview" ? overviewPage(box)
    : section === "services" ? (detail ? serviceDetail(box, shared, detail) : servicesPage(box, shared))
      : section === "traffic" ? trafficPage(box, shared)
        : section === "data" ? dataPage(box, shared)
          : section === "security" ? securityPage(box, shared)
            : logsPage(box, shared, detail);
  session = { route, box, page };
  centreSection(view.querySelector(".subnav"));
  await Promise.all([poll(), page.refresh?.()]);
  if (session?.box !== box) return;
  session.pollTimer = setInterval(poll, POLL_MS);
  session.refreshTimer = setInterval(refresh, REFRESH_MS);
}

/** The open section in the middle of the row of sections, on a phone where they don't all fit. Only the
 * row scrolls: scrollIntoView also scrolled the page whenever the window wasn't laid out yet, as the
 * laptop app's isn't when the page first draws, so it opened with the sections out of sight above. */
function centreSection(nav) {
  const current = nav?.querySelector('[aria-current="page"]');
  if (current) nav.scrollLeft = current.offsetLeft - (nav.clientWidth - current.offsetWidth) / 2;
}

// ---- overview ----------------------------------------------------------------------------------

/** What's wrong right now, a chip each: nothing at all when all is well. */
function alerts(doc) {
  const problems = doc?.problems ?? [];
  if (shared.misses >= 2) return [h("div", { class: "alert", role: "status" }, "Can't reach the phone")];
  const chips = problems.map((p) => h("div", { class: "alert", role: "status" }, serviceLogo(p.target), problemWords(p),
    h("span", null, [p.detail, sinceNow(p.started)].filter(Boolean).join(" · "))));
  if (!chips.length && doc?.at && Date.now() - doc.at > STALE_MS) {
    chips.push(h("div", { class: "alert warn", role: "status" }, "Monitor quiet", h("span", null, sinceNow(doc.at / 1000))));
  }
  return chips;
}

/** The phone's days up, out of the last thirty: one mark a day. */
const upDays = (uptime) => {
  const days = Math.min(Math.ceil(uptime / 86400), STRIP_DAYS);
  return Array.from({ length: STRIP_DAYS }, (_, i) => (i >= STRIP_DAYS - days ? "up" : ""));
};

function overviewPage(box) {
  const status = h("div", { class: "alerts" });
  const tiles = h("div", { class: "live-tiles" });
  const ranges = h("div");
  const load = lineChart({ label: "Load average over time", format: (v) => v.toFixed(2) });
  const cpu = lineChart({ label: "CPU used by the services", format: percent });
  const memory = lineChart({ label: "Memory used", format: bytes });
  const heat = lineChart({ label: "Temperatures", format: degrees, zero: false, floor: 20 });
  const network = lineChart({ label: "Network speed", format: perSecond });
  const requests = stackChart({ label: "Requests by service", format: (v) => Math.round(v).toLocaleString() });
  const services = h("div", { class: "service-chips" });
  fill(box, status, ranges, tiles,
    h("div", { class: "grid2" }, chartCard("Load", load), chartCard("Services' CPU", cpu)),
    h("div", { class: "grid2" }, chartCard("Memory", memory), chartCard("Temperature", heat)),
    h("div", { class: "grid2" }, chartCard("Network", network), chartCard("Requests", requests)),
    h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", null, "Services")), services));
  let history = null;

  function seriesOf(key) {
    if (shared.range === "live") {
      const to = shared.ring.at(-1)?.at ?? Date.now();
      const points = shared.ring.filter((p) => p.at >= to - LIVE_WINDOW_MS);
      return { at: points.map((p) => p.at), values: points.map((p) => p[key]), from: to - LIVE_WINDOW_MS, to, gap: LIVE_GAP_MS };
    }
    if (!history) return { at: [], values: [], from: 0, to: 1 };
    const { column, perBucket } = HISTORY[key];
    const raw = history.system[column];
    const at = history.at.map((s) => s * 1000);
    // The newest bucket is still filling: over its whole length it would read as a sudden drop.
    const seconds = (i) => (i === at.length - 1 ? Math.max((Date.now() - at[i]) / 1000, 1) : history.bucket);
    const values = perBucket ? raw.map((v, i) => (v == null ? null : v / seconds(i))) : raw;
    return { at, values, from: at[0], to: at.at(-1) + history.bucket * 1000 };
  }

  function requestChart() {
    if (!history) return;
    const names = [...SERVICE_ORDER.filter((n) => n in history.traffic), ...Object.keys(history.traffic).filter((n) => !SERVICE_ORDER.includes(n))];
    requests.update({
      ...bucketLabels(history.at, history.bucket),
      series: names.map((name) => serviceSeries(name, history.traffic[name].reqs)),
    });
  }

  function draw(key, chart, name = "", color = "var(--series)") {
    const s = seriesOf(key);
    chart.update({ ...s, series: [{ name, values: s.values, color }] });
  }

  function drawCharts() {
    draw("load", load);
    draw("cpu", cpu);
    draw("memUsed", memory);
    const a = seriesOf("cpuTemp");
    const b = seriesOf("battTemp");
    heat.update({ ...a, series: [
      { name: "CPU", values: a.values, color: "var(--pair-2)" }, { name: "Battery", values: b.values, color: "var(--pair-1)" }] });
    const down = seriesOf("netIn");
    const up = seriesOf("netOut");
    network.update({ ...down, series: [
      { name: "In", values: down.values, color: "var(--pair-1)" }, { name: "Out", values: up.values, color: "var(--pair-2)" }] });
    requestChart();
  }

  /** Ten tiles, each a figure with a small picture of it. */
  function drawTiles(doc) {
    const { system, memory: mem, temps, cores, disk, battery, wifi, speed } = doc;
    const rows = doc.services ?? [];
    const running = rows.filter((s) => s.state === "run").length;
    const line = (key) => spark(seriesOf(key).values);
    const used = mem ? mem.total - mem.available : null;
    const serviceMarks = besideHub(rows, rows.map((s) => (s.state === "run" && s.answers !== false ? "up" : "out")),
      doc.health && (fitTone(doc.health) || "none"));
    fillTiles(tiles,
      tile({ label: "Load", value: system ? system.load[0].toFixed(2) : "–", sub: doc.about?.cores ? `${doc.about.cores} cores` : "", extra: line("load") }),
      tile({ label: "CPU speed", value: cores ? percent(cores.speed * 100) : "–", sub: "of top speed", extra: line("speed") }),
      tile({ label: "Memory", value: bytes(used), sub: mem ? `of ${bytes(mem.total)}` : "", extra: mem ? gauge(used / mem.total) : null }),
      tile({ label: "Temperature", value: degrees(temps?.cpu), sub: temps?.battery != null ? `Battery ${degrees(temps.battery)}` : "", extra: line("cpuTemp") }),
      tile({
        label: "Battery",
        value: battery ? `${battery.percent}%` : "–",
        sub: battery ? (battery.plugged ? (battery.status === "FULL" ? "Full" : "Charging") : "On battery") : "",
        extra: battery ? gauge(battery.percent / 100, battery.percent < 30 ? "bad" : "") : null,
      }),
      tile({ label: "Storage", value: bytes(disk?.used), sub: disk ? `of ${bytes(disk.total)}` : "", extra: disk ? gauge(disk.used / disk.total, disk.used / disk.total > 0.9 ? "bad" : "") : null, href: "#server/data" }),
      tile({
        label: "Network", value: `↓ ${perSecond(speed?.in)}`, sub: `↑ ${perSecond(speed?.out)}`, href: "#server/traffic",
        extra: spark([{ values: seriesOf("netIn").values, color: "var(--pair-1)" }, { values: seriesOf("netOut").values, color: "var(--pair-2)" }]),
      }),
      tile({ label: "Wi-Fi", value: wifi ? wifiWord(wifi.rssi) : "–", sub: wifi ? `${wifi.linkMbps} Mbps · ${wifi.ghz} GHz` : "", extra: signal(wifi?.rssi) }),
      tile({
        label: "Up for", value: system ? duration(system.uptime) : "–", sub: system ? `Since ${moment(Date.now() / 1000 - system.uptime)}` : "",
        extra: marks(system ? upDays(system.uptime) : []),
      }),
      tile({ label: "Services", value: `${running}/${rows.length}`, sub: "running", href: "#server/services", extra: marks(serviceMarks) }));
  }

  function drawServices(doc) {
    const rows = doc.services ?? [];
    const chips = rows.map((s) => {
      const ok = s.state === "run" && s.answers !== false;
      return h("a", { class: "service-chip", href: `#server/services/${s.name}` },
        h("i", { class: `dot ${ok ? "up" : "out"}` }),
        h("span", null, serviceLabel(s.name)),
        h("em", { class: ok ? null : "out" }, ok ? (s.since ? sinceNow(s.since) : "") : "Down"));
    });
    const fit = doc.health && h("a", { class: "service-chip", href: `#server/services/${FIT}` },
      h("i", { class: `dot ${fitTone(doc.health)}` }),
      h("span", null, serviceLabel(FIT)),
      h("em", { class: doc.health.late ? "late" : "" }, fitAgo(doc.health)));
    fill(services, besideHub(rows, chips, fit));
  }

  function drawRanges() {
    fill(ranges, rangeRow(OVERVIEW_RANGES, shared.range, async (id) => {
      shared.range = id;
      drawRanges();
      await page.refresh();
      if (shared.now) drawTiles(shared.now);
    }));
  }

  const page = {
    live: true,
    onNow(doc) {
      fill(status, alerts(doc));
      drawTiles(doc);
      drawServices(doc);
      if (shared.range === "live") drawCharts();
    },
    onMiss() {
      fill(status, alerts(shared.now));
    },
    async refresh() {
      history = await getJson(`api/server/history?range=${shared.range === "live" ? "1h" : shared.range}`);
      drawCharts();
    },
  };
  drawRanges();
  return page;
}

// Logs: how much each service logs and at what level, its warnings and errors by what they say, and
// the newest lines. Requests answered 2xx or 3xx stay out unless asked for: Traffic counts those.
// Query strings never reach this page.

import { LEVEL_COLORS, SERVICE_ORDER, bucketLabels, chartCard, serviceSeries, stackChart } from "./charts.js";
import { fill, getJson, h } from "./dom.js";
import { rangeRow, serviceLogo } from "./serverkit.js";
import { logLine, messageRows } from "./server-services.js";

const SERVICES = ["calendar", "xrnhub", "xrnfit", "xrnloops", "xrnrule", "xrntube", "xrnhome", "xrnwal", "xrninsta", "ssh"];
const RANGES = [["24h", "24h"], ["7d", "7d"], ["30d", "30d"]];
const LEVELS = [["info", "All"], ["warn", "Warnings"], ["error", "Errors"]];
const TYPE_PAUSE_MS = 300;
const FOLLOW_MS = 5000;
const LINES = 100;

export function logsPage(box, _shared, picked) {
  let range = "24h";
  let level = "info";
  let routine = false;
  let typing = 0;
  let asked = 0;
  const ranges = h("div");
  // Which service, by its tile; the tile carries the name for screen readers.
  const service = { value: SERVICES.includes(picked) ? picked : "" };
  const services = h("div", { class: "ranges tiles-row", role: "group", "aria-label": "Service" });
  const byLevel = stackChart({ label: "Lines logged by level", format: (v) => Math.round(v).toLocaleString() });
  const byService = stackChart({ label: "Warnings and errors by service", format: (v) => Math.round(v).toLocaleString() });
  const groups = h("div");
  const lines = h("div", { class: "log-lines" });
  const search = h("input", {
    type: "search", placeholder: "Search", "aria-label": "Search the logs",
    oninput: () => {
      clearTimeout(typing);
      typing = setTimeout(() => load().catch(() => {}), TYPE_PAUSE_MS);
    },
  });
  const levels = h("div", { class: "ranges" });
  const requests = h("button", {
    type: "button", class: "toggle", "aria-pressed": "false",
    onclick: () => {
      routine = !routine;
      requests.setAttribute("aria-pressed", String(routine));
      load().catch(() => {});
    },
  }, "Requests");

  function drawLevels() {
    fill(levels, LEVELS.map(([id, label]) => h("button", {
      type: "button",
      "aria-pressed": String(id === level),
      onclick: () => {
        level = id;
        drawLevels();
        load().catch(() => {});
      },
    }, label)));
  }

  function drawRanges() {
    fill(ranges, h("div", { class: "log-filters" }, rangeRow(RANGES, range, (id) => {
      range = id;
      drawRanges();
      refresh().catch(() => {});
    }), services));
    fill(services, [["", "All"], ...SERVICES.map((name) => [name, serviceLogo(name, true)])].map(([name, face]) => h("button", {
      type: "button",
      "aria-pressed": String(service.value === name),
      onclick: () => {
        service.value = name;
        drawRanges();
        refresh().catch(() => {});
      },
    }, face)));
  }

  /** The newest lines that match, kept where they're scrolled to. */
  async function load() {
    const mine = ++asked;
    const query = [["service", service.value], ["level", level], ["q", search.value.trim()], ["limit", String(LINES)],
      ["routine", routine ? "1" : "0"]].map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join("&");
    const doc = await getJson(`api/server/logs?${query}`);
    if (mine !== asked) return;
    const keep = lines.scrollTop;
    fill(lines, doc.lines.length ? doc.lines.map((row) => logLine(row, !service.value)) : h("p", { class: "empty" }, "None"));
    lines.scrollTop = keep;
  }

  async function refresh() {
    const only = service.value;
    const stats = await getJson(`api/server/logstats?range=${range}${only ? `&service=${only}` : ""}`);
    const labels = bucketLabels(stats.at, stats.bucket);
    byLevel.update({ ...labels, series: [
      { name: "Errors", values: stats.levels.error, color: LEVEL_COLORS.error },
      { name: "Warnings", values: stats.levels.warn, color: LEVEL_COLORS.warn },
      { name: "Other", values: stats.levels.info, color: LEVEL_COLORS.info },
    ] });
    const named = [...SERVICE_ORDER, "ssh"].filter((n) => n in stats.services);
    byService.update({ ...labels, series: named.map((name) => serviceSeries(name,
      stats.at.map((_, i) => stats.services[name].warn[i] + stats.services[name].error[i]))) });
    fill(groups, messageRows(stats.groups, { withService: !only }));
    await load();
  }

  drawRanges();
  drawLevels();
  fill(box, ranges,
    h("div", { class: "grid2" }, chartCard("By level", byLevel), chartCard("Warnings by service", byService)),
    h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", null, "Warnings and errors")), groups),
    h("section", { class: "card" },
      h("div", { class: "card-head" }, h("h2", null, "Latest")),
      h("div", { class: "log-filters" }, levels, requests, search),
      lines));
  const timer = setInterval(() => {
    if (document.visibilityState === "visible") load().catch(() => {});
  }, FOLLOW_MS);
  return { live: false, refresh, stop: () => clearInterval(timer) };
}

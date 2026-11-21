// Data: how much the server holds for each app, how much came in, and how long the disk will last.

import {
  SERVICE_ORDER, bar, chartCard, colorOf, dataRows, dayLabels, keyItem, lineChart, serviceSeries, stackChart,
} from "./charts.js";
import { card, fill, getJson, h } from "./dom.js";
import { bytes, count, duration, fillTiles, moment, serviceLabel, serviceName, tile } from "./serverkit.js";

const BACKUP_OK_S = 3 * 86400;
// What the server keeps besides the apps' data, in greys after the apps' colours.
const OTHERS = [["extra", "App files", "var(--outline)"], ["logs", "Logs", "var(--faint)"], ["monitorBytes", "Monitor", "var(--muted)"]];

function runwayWords(doc) {
  if (!doc.runway) return "–";
  if (!doc.runway.days) return "Not growing";
  const years = doc.runway.days / 365;
  return years >= 1 ? `${years >= 10 ? Math.round(years) : years.toFixed(1)} years` : `${Math.round(doc.runway.days)} days`;
}

const signed = (n) => `${n > 0 ? "+" : n < 0 ? "−" : ""}${bytes(Math.abs(n))}`;

/** A figure with a thin bar under it: its share of the column's largest. */
const figureBar = (text, share, color) => h("span", { class: "fig-bar" }, h("span", null, text), bar(share, color));

export function dataPage(box) {
  const hero = h("div", { class: "hero" });
  const tiles = h("div", { class: "live-tiles" });
  const apps = h("div");
  const fed = stackChart({ label: "Records that came in each day", format: (v) => Math.round(v).toLocaleString() });
  const writes = stackChart({ label: "Changes saved each day", format: (v) => Math.round(v).toLocaleString() });
  const network = stackChart({ label: "Bytes through the network each day", format: bytes });
  const growth = lineChart({ label: "Size of each app's data", format: bytes });
  const processes = h("div");
  fill(box, hero, tiles,
    card("What each app holds", null, apps),
    h("div", { class: "grid2" }, chartCard("Came in, per day", fed), chartCard("Saved, per day", writes)),
    h("div", { class: "grid2" }, chartCard("Network, per day", network), chartCard("Size over time", growth)),
    card("Since each service started", null, processes));

  async function refresh() {
    const doc = await getJson("api/server/data");
    if (doc.pending) {
      fill(hero, h("p", { class: "empty" }, "Counting…"));
      setTimeout(() => refresh().catch(() => {}), 4000);
      return;
    }
    const grew = doc.apps.reduce((sum, a) => sum + (a.grew ?? 0), 0);
    const knownGrowth = doc.apps.some((a) => a.grew != null);
    const added = doc.apps.reduce((sum, a) => sum + (a.added ?? 0), 0);
    const records = doc.apps.reduce((sum, a) => sum + a.records, 0);
    const parts = [
      ...doc.apps.filter((a) => a.bytes > 0).map((a) => ({ name: serviceName(a.name), service: a.name, color: colorOf(a.name), bytes: a.bytes })),
      ...OTHERS.filter(([key]) => doc[key] > 0).map(([key, name, color]) => ({ name, color, bytes: doc[key] })),
    ];
    const whole = parts.reduce((sum, p) => sum + p.bytes, 0) || 1;
    fill(hero,
      h("div", { class: "hero-top" },
        h("div", { class: "hero-value" }, bytes(doc.total)),
        h("div", { class: "hero-sub" }, knownGrowth ? `${signed(grew)} · ${count(added)} records this week` : `${count(records)} records`)),
      h("div", { class: "share-bar", role: "img", "aria-label": "What the server holds" }, parts.map((p) =>
        h("i", { style: { width: `${(p.bytes / whole) * 100}%`, "background-color": p.color } }))),
      h("div", { class: "share-key" }, parts.map((p) => keyItem(p, bytes(p.bytes)))));

    const { disk, network: net } = doc;
    const backupAge = doc.backup ? doc.at - doc.backup : null;
    fillTiles(tiles,
      tile({ label: "Received", value: bytes(net?.in), sub: "since Tailscale started" }),
      tile({ label: "Sent", value: bytes(net?.out), sub: "since Tailscale started" }),
      tile({ label: "Disk free", value: bytes(disk?.free), sub: disk ? `of ${bytes(disk.total)}` : "" }),
      tile({ label: "Lasts", value: runwayWords(doc), sub: doc.runway?.perDay > 0 ? `at ${bytes(doc.runway.perDay)} a day` : "" }),
      tile({
        label: "Last backup", value: doc.backup ? `${duration(backupAge)} ago` : "None",
        sub: doc.backup ? moment(doc.backup) : "", tone: !doc.backup || backupAge > BACKUP_OK_S ? "bad" : "",
      }));

    const top = Math.max(1, ...doc.apps.map((a) => a.bytes));
    fill(apps, dataRows([
      { label: "App", width: "minmax(0, 150px)", narrow: "minmax(0, 1fr)", cell: (a) => h("span", { class: "name" }, serviceLabel(a.name)) },
      { width: "minmax(0, 1fr)", narrow: null, cell: (a) => bar(a.bytes / top, colorOf(a.name)) },
      { label: "Size", width: "72px", end: true, cell: (a) => h("b", null, bytes(a.bytes)) },
      { label: "Holds", width: "minmax(0, 150px)", narrow: "minmax(0, 120px)", end: true, cell: (a) => `${count(a.records)} ${a.unit}` },
      { label: "Week", width: "80px", narrow: null, end: true, cell: (a) => h("span", { class: a.grew ? "" : "muted" }, a.grew == null ? "–" : signed(a.grew)) },
      { label: "New", width: "64px", narrow: null, end: true, cell: (a) => h("span", { class: a.added ? "" : "muted" }, a.added == null ? "–" : count(a.added)) },
    ], doc.apps.map((a) => ({ ...a, href: SERVICE_ORDER.includes(a.name) ? `#server/services/${a.name}` : null })), "None"));

    const appsIn = SERVICE_ORDER.filter((n) => n in doc.fed.apps);
    fed.update({ ...dayLabels(doc.fed.days), series: appsIn.map((name) => serviceSeries(name, doc.fed.apps[name])) });
    const writers = SERVICE_ORDER.filter((n) => n in doc.daily.writes);
    writes.update({ ...dayLabels(doc.daily.days), series: writers.map((name) => serviceSeries(name, doc.daily.writes[name])) });
    network.update({ ...dayLabels(doc.daily.days), series: [
      { name: "In", values: doc.daily.in, color: "var(--pair-1)" }, { name: "Out", values: doc.daily.out, color: "var(--pair-2)" }] });
    const at = doc.growth.days.map((day) => new Date(`${day}T12:00`).getTime());
    const named = SERVICE_ORDER.filter((n) => n in doc.growth.apps);
    growth.update({
      at, from: at[0] ?? 0, to: Math.max(at.at(-1) ?? 1, (at[0] ?? 0) + 86400000),
      series: named.map((name) => serviceSeries(name, doc.growth.apps[name])),
    });

    const most = (key) => Math.max(1, ...doc.services.map((s) => s[key] ?? 0));
    const tops = { read: most("read"), written: most("written"), diskWritten: most("diskWritten") };
    fill(processes, dataRows([
      { label: "Service", width: "minmax(0, 1.2fr)", cell: (s) => h("span", { class: "name" }, serviceLabel(s.name)) },
      ...[["Read", "read"], ["Written", "written"], ["To storage", "diskWritten"]].map(([label, key]) => ({
        label, width: "minmax(0, 1fr)", narrow: key === "read" ? null : "minmax(0, 1fr)", end: true,
        cell: (s) => figureBar(bytes(s[key]), (s[key] ?? 0) / tops[key], colorOf(s.name)),
      })),
      { label: "Started", width: "80px", narrow: null, end: true, cell: (s) => h("span", { class: "muted" }, s.since ? moment(s.since) : "–") },
    ], doc.services.map((s) => ({ ...s, href: `#server/services/${s.name}` })), "None"));
  }

  return { live: false, refresh };
}

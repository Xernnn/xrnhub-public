// Security: who tried to get in, what's being probed, and what's open to the internet.

import { STATUS_COLORS, bar, colorOf, dataRows } from "./charts.js";
import { card, fill, getJson, h } from "./dom.js";
import { count, fillTiles, moment, serviceLogo, serviceName, tile } from "./serverkit.js";

const lockouts = (status) => status["429"] ?? 0;

export function securityPage(box) {
  const tiles = h("div", { class: "live-tiles" });
  const logins = h("div");
  const refused = h("div");
  const clients = h("div");
  const devices = h("div");
  const funnel = h("div");
  fill(box, tiles,
    card("Sign-ins, 30 days", null, logins),
    h("div", { class: "grid2" }, card("Refused paths, 7 days", null, refused), card("Refused callers, 7 days", null, clients)),
    h("div", { class: "grid2" }, card("Your devices", null, devices), card("Open to the internet", null, funnel)));

  async function refresh() {
    const doc = await getJson("api/server/security");
    const refusals = Object.entries(doc.status).filter(([code]) => code[0] === "4").reduce((sum, [, n]) => sum + n, 0);
    fillTiles(tiles,
      tile({ label: "Failed sign-ins", value: count(doc.day.refused), sub: `${count(doc.week.refused)} this week`, tone: doc.day.refused ? "warn" : "" }),
      tile({ label: "Refused requests", value: count(refusals), sub: "this week" }),
      tile({ label: "Locked out", value: count(lockouts(doc.status)), sub: "this week" }),
      tile({ label: "Sign-ins", value: count(doc.week.ok), sub: "this week" }));

    fill(logins, dataRows([
      { label: "Last", width: "100px", narrow: "76px", cell: (r) => h("span", { class: "muted" }, moment(r.at / 1000)) },
      { width: "60px", cell: (r) => h("span", { class: `code ${r.ok ? "s2" : "s5"}` }, r.ok ? "In" : "Failed") },
      { label: "Who", width: "minmax(0, 1fr)", cell: (r) => h("span", { class: "strong" }, r.who || "–") },
      { label: "Service", width: "minmax(0, 140px)", narrow: "24px", cell: (r) => h("span", { class: "name" }, serviceLogo(r.service), h("span", { class: "wide-only" }, serviceName(r.service))) },
      { label: "Times", width: "56px", narrow: "40px", end: true, cell: (r) => h("b", null, count(r.count)) },
      { label: "From", width: "minmax(0, 200px)", narrow: null, end: true, cell: (r) => h("span", { class: "mono", title: r.ip }, r.ip) },
    ], doc.logins.map((r) => ({ ...r, tone: r.ok ? "" : "failed" })), "None in 30 days"));

    const most = doc.refused[0]?.count || 1;
    fill(refused, dataRows([
      { label: "Path", width: "minmax(0, 1fr)", cell: (r) => h("span", { class: "name mono", title: r.path }, serviceLogo(r.service), h("span", null, r.path)) },
      { width: "48px", cell: (r) => h("span", { class: `code s${String(r.status)[0]}` }, r.status) },
      { width: "minmax(32px, 22%)", narrow: "40px", cell: (r) => bar(r.count / most, colorOf(r.service)) },
      { label: "Times", width: "56px", end: true, cell: (r) => h("b", null, count(r.count)) },
    ], doc.refused, "None this week"));

    const worst = Math.max(1, ...doc.clients.map((c) => c.refused));
    fill(clients, dataRows([
      { label: "Caller", width: "minmax(0, 1fr)", cell: (c) => h("span", { class: "mono", title: c.ip }, c.ip) },
      { width: "minmax(32px, 22%)", narrow: "40px", cell: (c) => bar(c.refused / worst, STATUS_COLORS[4]) },
      { label: "Refused", width: "60px", end: true, cell: (c) => h("b", null, count(c.refused)) },
      { label: "Of", width: "64px", narrow: null, end: true, cell: (c) => h("span", { class: "muted" }, count(c.reqs)) },
      { label: "Days", width: "44px", narrow: null, end: true, cell: (c) => h("span", { class: "muted" }, count(c.days)) },
    ], doc.clients, "None this week"));

    const net = doc.tailnet;
    fill(devices, net ? [
      dataRows([
        { width: "minmax(0, 1fr)", cell: (d) => h("span", { class: "name" }, h("i", { class: `dot ${d.online ? "up" : "idle"}` }), h("span", null, d.name)) },
        { width: "minmax(0, 90px)", cell: (d) => h("span", { class: "muted" }, d.os || "") },
        { width: "minmax(0, 90px)", end: true, cell: (d) => h("span", { class: d.online ? "strong" : "muted" }, d.online ? (d.direct ? "Direct" : `Relay ${d.relay}`) : "Offline") },
      ], net.devices, "None"),
      h("div", { class: "chips-row top-gap" },
        h("span", { class: "path" }, `Tailscale ${net.version}`), h("span", { class: "path" }, net.state),
        h("span", { class: "path" }, `${net.ingress} relays`)),
    ] : h("p", { class: "empty" }, "Tailscale isn't answering"));
    fill(funnel, doc.funnel ? h("div", { class: "chips-row" }, doc.funnel.map((path) => h("code", { class: "path" }, path)))
      : h("p", { class: "empty" }, "–"));
  }

  return { live: false, refresh };
}

// The Apps page: every project and where it runs, with what the hub can see of each right now. The words for those facts are written again in android/.../data/Apps.kt, which has to
// stay in step with this file, as Words.kt does with serverkit.js.

import { fill, getJson, h, svg } from "./dom.js";
import { count, serviceLogo, serviceName, sinceNow } from "./serverkit.js";

const REFRESH_MS = 60000;
const BACKUP_OK_S = 3 * 86400; // the monitor's own limit (watcher.BACKUP_STALE_S)

// Line icons in the tabs' style, one for each place an app runs.
const PLACES = {
  Phone: "M8 3h8a1 1 0 0 1 1 1v16a1 1 0 0 1-1 1H8a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1zM11 18h2",
  Laptop: "M5 6a1 1 0 0 1 1-1h12a1 1 0 0 1 1 1v9H5zM3 18h18",
  Browser: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM3 12h18M12 3c-3 3.5-3 14.5 0 18M12 3c3 3.5 3 14.5 0 18",
  Server: "M4 5h16v6H4zM4 13h16v6H4zM8 8h.01M8 16h.01",
  Telegram: "M21 4 3 11l6 2.5L11 20l3.5-4.5L19 19zM9 13.5 21 4",
};
const RELEASE_WORDS = { phone: "Phone app", extension: "Extension" };

let timer = 0;

const line = (d) => svg("svg", { viewBox: "0 0 24 24", "aria-hidden": "true" }, svg("path", { d }));

/** Up or down, with how long for, as the Server page says it. */
export function serviceWords(app) {
  if (app.up == null) return "Not heard from yet";
  if (!app.up) return "Down";
  return app.since ? `Up · ${sinceNow(app.since)}` : "Up";
}

export function holdsWords(app) {
  if (app.records == null) return null;
  const held = `${count(app.records)} ${app.unit}`;
  return app.week ? `${held} · ${count(app.week)} this week` : held;
}

export const agoWords = (seconds) => `${sinceNow(seconds)} ago`;

export const releaseWords = (released) =>
  released.map((r) => `${RELEASE_WORDS[r.kind] ?? r.kind} ${r.version}`).join(" · ");

function fact(label, value, tone) {
  return h("div", { class: "app-fact" }, h("span", null, label), h("b", tone ? { class: tone } : null, value));
}

/** The service an app talks to on the home server: its own, or the hub, which keeps xrnfit's data. */
export const serverOf = (app) => app.server ?? app.service ?? null;

function facts(app) {
  const rows = [];
  const server = serverOf(app);
  if (server) {
    // The server's logo stands for its name, which is the app's own but for xrnfit (the hub's).
    const logo = serviceLogo(server, true);
    rows.push(h("div", { class: "app-fact" }, h("span", null, "Server"),
      h("b", null, app.up != null && h("i", { class: `dot ${app.up ? "up" : "out"}` }), logo,
        logo ? serviceWords(app) : `${serviceName(server)} · ${serviceWords(app)}`)));
  }
  const holds = holdsWords(app);
  if (holds) rows.push(fact("Holds", holds));
  if (app.lastIn) rows.push(fact("Last sync", agoWords(app.lastIn)));
  if (app.backup) {
    rows.push(fact("Last backup", agoWords(app.backup), Date.now() / 1000 - app.backup > BACKUP_OK_S ? "bad" : null));
  }
  if (app.released?.length) rows.push(fact("Released", releaseWords(app.released)));
  return rows.length ? h("div", { class: "app-facts" }, rows) : null;
}

function appCard(app) {
  return h("article", { class: "app-card", id: `app-${app.name}` },
    h("div", { class: "app-head" },
      h("img", { class: "app-logo", src: app.logo, alt: "" }),
      h("div", { class: "app-id" },
        h("h3", null, app.name),
        h("ul", { class: "app-runs", "aria-label": "Runs on" },
          app.runs.map((place) => h("li", null, PLACES[place] && line(PLACES[place]), place))))),
    facts(app),
    h("div", { class: "app-foot" },
      h("span", { class: `vis${app.public ? " public" : ""}` }, app.public ? "Public repo" : "Private repo"),
      h("div", { class: "app-links" },
        app.web && h("a", { href: app.web }, "Open"),
        h("a", { href: app.repoUrl, target: "_blank", rel: "noopener noreferrer" }, "GitHub"))));
}

function jump(name) {
  const card = document.getElementById(`app-${name}`);
  if (!card) return;
  for (const other of document.querySelectorAll(".app-card.picked")) other.classList.remove("picked");
  card.classList.add("picked");
  card.scrollIntoView({ block: "start", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
}

/** How many facts a card shows. */
const factCount = (app) =>
  [serverOf(app), holdsWords(app), app.lastIn, app.backup, app.released?.length].filter(Boolean).length;

/** The apps in grids by how many facts each shows, most first: a row holds cards of one height. */
function grids(apps) {
  const tiers = new Map();
  for (const app of apps) {
    const n = factCount(app);
    if (!tiers.has(n)) tiers.set(n, []);
    tiers.get(n).push(app);
  }
  return [...tiers.entries()].sort((a, b) => b[0] - a[0]).map(([, members]) => h("div", { class: "apps-grid" }, members.map(appCard)));
}

function draw(view, doc) {
  const { apps } = doc;
  fill(view,
    h("nav", { class: "apps-strip", "aria-label": "Jump to an app" }, apps.map((app) =>
      h("a", {
        href: `#apps/${app.name}`,
        title: app.name,
        onclick: (event) => {
          event.preventDefault();
          history.replaceState(null, "", `#apps/${app.name}`);
          jump(app.name);
        },
      }, h("img", { src: app.logo, alt: app.name })))),
    h("div", { class: "apps-grids" }, grids(apps)));
}

export async function showApps(view) {
  const first = !view.querySelector(".apps-grid");
  draw(view, await getJson("api/apps"));
  const picked = location.hash.slice(1).split("/")[1];
  if (first && picked) jump(picked);
  clearInterval(timer);
  timer = setInterval(() => {
    if (!view.isConnected) {
      clearInterval(timer);
      return;
    }
    if (document.visibilityState !== "visible") return;
    // Redrawn in place: where the page is scrolled to stays put.
    getJson("api/apps").then((doc) => view.isConnected && draw(view, doc)).catch(() => {});
  }, REFRESH_MS);
}

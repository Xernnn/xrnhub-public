// xrnhub's page: the frame, the sign-in, and which view is showing.

import { showApps } from "./apps.js";
import { SignedOut, fill, getJson, h, hideTip, toast, whenSignedOut } from "./dom.js";
import { showReview } from "./review.js";
import { showServer, stopServer } from "./server.js";
import { serviceLogo, serviceName } from "./serverkit.js";
import { showToday, todayKey } from "./today.js";

const REFRESH_MS = 60000;
const HEALTH_MS = 300000;
const VIEWS = { server: showServer, today: showToday, review: showReview, apps: showApps };
// Pages that went, and where an old address of theirs lands: the nudge rules are switched on the
// cards on Today now.
const MOVED = { nudges: "today" };
const TABS = [["server", "Server"], ["today", "Today"], ["review", "Review"], ["apps", "Apps"]];
const APPS = [["xrnloops", "Habits"], ["xrnrule", "Blocks"]];

const root = document.getElementById("app");
let shownTab = "";
let refreshTimer = 0;
let healthAt = 0;

function icon(path) {
  const ns = "http://www.w3.org/2000/svg";
  const box = document.createElementNS(ns, "svg");
  box.setAttribute("viewBox", "0 0 24 24");
  box.setAttribute("aria-hidden", "true");
  const line = document.createElementNS(ns, "path");
  line.setAttribute("d", path);
  box.append(line);
  return box;
}

const ICONS = {
  server: "M4 5h16v6H4zM4 13h16v6H4zM8 8h.01M8 16h.01",
  today: "M5 12.5 9.5 17 19 7",
  review: "M5 19V11M12 19V5M19 19v-5",
  apps: "M5 4h4a1 1 0 0 1 1 1v4a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1zM15 4h4a1 1 0 0 1 1 1v4a1 1 0 0 1-1 1h-4a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1zM5 14h4a1 1 0 0 1 1 1v4a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1v-4a1 1 0 0 1 1-1zM15 14h4a1 1 0 0 1 1 1v4a1 1 0 0 1-1 1h-4a1 1 0 0 1-1-1v-4a1 1 0 0 1 1-1z",
  signOut: "M9 20H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h3M15 16l4-4-4-4M19 12H9",
};

/** Ends the session on the server too, so a copy of the cookie stops working; the page can't drop
 * the HttpOnly cookie itself, so a failed request leaves it signed in and says so. */
async function signOut() {
  const res = await fetch("api/session", {
    method: "DELETE",
    credentials: "same-origin",
    headers: { "X-Xrn-Client": "xrnhub" },
  }).catch(() => null);
  if (res?.ok) showSignIn();
  else toast("Can't reach the server");
}

function frame(tab, view) {
  const health = h("span", { class: "health", id: "health" });
  const tabs = TABS.map(([id, label]) =>
    h("a", { href: `#${id}`, "aria-current": id === tab ? "page" : null }, icon(ICONS[id]), h("span", null, label)));
  return h("div", { class: "frame" },
    h("header", { class: "top" },
      // The family's tile stands for the name.
      h("a", { class: "brand", href: "#server", "aria-label": "xrnhub", title: "xrnhub" }, h("img", { src: "icon.svg", alt: "" })),
      h("nav", { class: "tabs", "aria-label": "Pages" }, tabs),
      h("div", { class: "top-right" },
        health,
        APPS.map(([name, label]) => h("a", { class: "app-link", href: `/${name}/`, title: label, "aria-label": label },
          h("img", { src: `apps/${name}.svg`, alt: "" }))),
        h("button", { type: "button", class: "icon-link", title: "Sign out", "aria-label": "Sign out", onclick: signOut },
          icon(ICONS.signOut)))),
    view);
}

async function checkHealth() {
  if (Date.now() - healthAt < HEALTH_MS) return;
  healthAt = Date.now();
  const { services } = await getJson("api/services");
  const down = services.filter((s) => !s.up).map((s) => s.name);
  const slot = document.getElementById("health");
  // Each one down by its logo, which stands for its name; past three, the count is what matters.
  const shown = down.length <= 3 ? down.map((name) => serviceLogo(name, true) ?? serviceName(name)) : String(down.length);
  if (slot) fill(slot, down.length > 0 && h("span", { class: "pill blocked down-pill", title: down.map(serviceName).join(", ") }, shown, " down"));
}

function showSignIn() {
  clearInterval(refreshTimer);
  stopServer();
  hideTip();
  shownTab = "";
  const error = h("p", { class: "error", role: "alert" });
  const user = h("input", { autocomplete: "username", autocapitalize: "none", required: true });
  const secret = h("input", { type: "password", autocomplete: "current-password", required: true });
  const submit = async (e) => {
    e.preventDefault();
    error.textContent = "";
    const bytes = new TextEncoder().encode(`${user.value}:${secret.value}`);
    const res = await fetch("api/session", {
      method: "POST",
      credentials: "same-origin",
      headers: { "X-Xrn-Client": "xrnhub", Authorization: `Basic ${btoa(String.fromCharCode(...bytes))}` },
    }).catch(() => null);
    if (res?.ok) render();
    else error.textContent = res?.status === 429 ? "Too many tries. Wait a while." : res ? "Wrong name or password" : "Can't reach the server";
  };
  root.replaceChildren(h("form", { class: "signin", onsubmit: submit },
    h("img", { src: "icon.svg", alt: "" }),
    h("label", { class: "field" }, h("span", null, "Name"), user),
    h("label", { class: "field" }, h("span", null, "Password"), secret),
    error,
    h("button", { class: "primary", type: "submit" }, "Sign in")));
}

function currentTab() {
  const tab = location.hash.slice(1).split("/")[0];
  if (tab in MOVED) {
    history.replaceState(null, "", `#${MOVED[tab]}`);
    return MOVED[tab];
  }
  return tab in VIEWS ? tab : "server";
}

async function render() {
  const tab = currentTab();
  let view = document.getElementById("view");
  if (!view || shownTab !== tab) {
    stopServer();
    // A chart's tip lives on the body and goes on pointerleave, which a mark removed with its page
    // never sends: it stayed over the next page.
    hideTip();
    view = h("main", { id: "view" });
    root.replaceChildren(frame(tab, view));
    shownTab = tab;
    healthAt = 0;
    scrollTo(0, 0);
  }
  clearInterval(refreshTimer);
  try {
    await VIEWS[tab](view);
    await checkHealth().catch(() => {});
  } catch (e) {
    if (e instanceof SignedOut) return;
    fill(view, h("p", { class: "error" }, "Can't reach the server"),
      h("button", { type: "button", class: "quiet", onclick: render }, "Try again"));
    return;
  }
  if (tab === "today") refreshTimer = setInterval(refreshToday, REFRESH_MS);
}

/** A failed refresh changes nothing: the page keeps what it has until the next one works. */
function refreshToday() {
  const view = document.getElementById("view");
  if (view && document.visibilityState === "visible" && shownTab === "today") showToday(view).catch(() => {});
}

addEventListener("hashchange", render);

document.addEventListener("visibilitychange", refreshToday);
whenSignedOut(showSignIn);

/** A nudge card's menu is open: its keys are its own. (A browser without popovers has no menus.) */
function menuOpen() {
  try {
    return !!document.querySelector(".menu:popover-open");
  } catch {
    return false;
  }
}

document.addEventListener("keydown", (event) => {
  const tag = event.target.tagName;
  if (["INPUT", "TEXTAREA", "SELECT"].includes(tag) || document.querySelector("dialog[open]") || menuOpen()) return;
  if (event.ctrlKey || event.metaKey || event.altKey) return;
  if (shownTab === "today") todayKey(event);
  if (event.key === "r") location.hash = "#review";
  if (event.key === "a") location.hash = "#apps";
  if (event.key === "h" || event.key === "s") location.hash = "#server";
  if (event.key === "t" && shownTab !== "today") location.hash = "#today";
});

render();

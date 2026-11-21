// What the Server pages share: how numbers read, tiles, and the words for what went wrong.

import { clock, h } from "./dom.js";

const UNITS = ["B", "KB", "MB", "GB", "TB"];
const WEEK_DAYS = 7;

export function bytes(n) {
  if (n == null) return "–";
  let value = Math.abs(n);
  let unit = 0;
  while (value >= 1024 && unit < UNITS.length - 1) {
    value /= 1024;
    unit += 1;
  }
  const digits = value >= 100 || unit === 0 ? 0 : value >= 10 ? 1 : 2;
  return `${n < 0 ? "−" : ""}${Number(value.toFixed(digits))} ${UNITS[unit]}`;
}

export const perSecond = (n) => (n == null ? "–" : `${bytes(n)}/s`);
export const count = (n) => (n == null ? "–" : Math.round(n).toLocaleString());
export const percent = (n) => (n == null ? "–" : `${n < 10 ? n.toFixed(1) : Math.round(n)}%`);
export const degrees = (n) => (n == null ? "–" : `${Math.round(n)}°`);

export function duration(seconds) {
  if (seconds == null) return "–";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  if (seconds < 86400) {
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.round((seconds % 3600) / 60);
    return minutes ? `${hours}h ${minutes}m` : `${hours}h`;
  }
  const days = Math.floor(seconds / 86400);
  return `${days} ${days === 1 ? "day" : "days"}`;
}

/** Seconds since the epoch as the shortest thing that's clear. */
export function moment(seconds) {
  const date = new Date(seconds * 1000);
  const now = new Date();
  if (date.toDateString() === now.toDateString()) return clock(date);
  const day = date.toLocaleDateString(undefined, { day: "numeric", month: "short" });
  return now - date < WEEK_DAYS * 86400000 ? `${day} ${clock(date)}` : day;
}

export const sinceNow = (seconds) => duration(Date.now() / 1000 - seconds);

export const wifiWord = (rssi) => (rssi >= -60 ? "Strong" : rssi >= -72 ? "Good" : "Weak");

// The calendar service is Radicale, the server xrncal syncs with. It goes by the app's name here;
// the API and the stored history keep calling it `calendar`.
const DISPLAY_NAMES = { calendar: "xrncal", tailscale: "Tailscale" };
const titled = (name) => DISPLAY_NAMES[name] ?? name;
export const serviceName = titled;

const LOGOS = {
  calendar: "apps/xrncal.svg",
  xrnloops: "apps/xrnloops.svg",
  xrnrule: "apps/xrnrule.svg",
  xrntube: "apps/xrntube.svg",
  xrnhome: "apps/xrnhome.svg",
  xrnwal: "apps/xrnwal.svg",
  xrninsta: "apps/xrninsta.svg",
  xrnimg: "apps/xrnimg.svg",
  xrnnote: "apps/xrnnote.svg",
  xrnfit: "apps/xrnfit.svg",
  xrnhub: "icon.svg",
  tailscale: "apps/tailscale.svg",
  ssh: "apps/ssh.svg",
};

/** The service's logo. Beside its name, screen readers skip it; `alone`, in place of the name, it
 * carries the name for them. */
export const serviceLogo = (name, alone = false) =>
  (LOGOS[name] ? h("img", { class: "logo", src: LOGOS[name], alt: alone ? titled(name) : "" }) : null);

/** A logo and name, for wherever a service is named on its own. */
export const serviceLabel = (name) => [serviceLogo(name), serviceName(name)];

const WORDS = {
  down: (i) => `${titled(i.target)} is down`,
  unplugged: () => "Phone unplugged",
  battery: () => "Battery low",
  hot: () => "Phone running hot",
  disk: () => "Disk nearly full",
  backup: () => "Backup is old",
  tailscale: () => "Tailscale not running",
  supervisor: (i) => (i.detail ? "More than one runsvdir" : "No runsvdir: stopped services stay stopped"),
  unsupervised: (i) => `${titled(i.target)} is running unsupervised`,
};
const PAST = {
  ...WORDS,
  down: (i) => `${titled(i.target)} down`,
  restart: (i) => `${titled(i.target)} restarted`,
  restarted: (i) => (i.detail === "failed" ? `Restart of ${titled(i.target)} failed` : `${titled(i.target)} restarted by you`),
  reboot: () => "Phone rebooted",
  gap: () => "Monitor wasn't running",
  supervisor: (i) => (i.detail ? "More than one runsvdir" : "runsvdir missing"),
  unsupervised: (i) => `${titled(i.target)} unsupervised`,
};
export const problemWords = (i) => (WORDS[i.kind] ?? (() => i.kind))(i);
export const incidentWords = (i) => (PAST[i.kind] ?? (() => i.kind))(i);

// ---- xrnfit among the services, as data/Words.kt says it ---------------------------------------

/** xrnfit has no service of its own: the hub keeps what it sends, so it's listed right after it. */
export const FIT = "xrnfit";
export const FIT_KEPT_BY = "xrnhub";

/** Where a service's figure says how long it's been up, xrnfit's says when it last sent. */
export const fitAgo = (card) => (card.lastIn == null ? "–" : `${sinceNow(card.lastIn)} ago`);

/** Green while it sends, amber once six hours pass with nothing, grey before it ever has. */
export const fitTone = (card) => (card.lastIn == null ? "" : card.late ? "late" : "up");

export const readingsWords = (n) => `${count(n)} ${n === 1 ? "reading" : "readings"}`;

/** A day on xrnfit's strip: green with readings, red with none, grey before the first. */
export const sentDay = (n) => (n == null ? ["none", "Nothing yet"] : n > 0 ? ["up", readingsWords(n)] : ["out", "Nothing sent"]);

/** [items], one a service in [services]' order, with [extra] put in after the hub's. */
export function besideHub(services, items, extra) {
  if (!extra) return items;
  const at = services.findIndex((s) => s.name === FIT_KEPT_BY);
  const out = [...items];
  out.splice(at < 0 ? out.length : at + 1, 0, extra);
  return out;
}

/** One number with a word under it, and in a set that has them, a small picture of it at the foot.
 * Every tile in a set has the same parts, so a row of them is one height with nothing left empty.
 * `href` makes it a way into the page behind it. */
export function tile({ label, value, sub, extra, href, tone }) {
  const body = [
    h("div", { class: "stat-label" }, label),
    h("div", { class: `stat-value${tone ? ` ${tone}` : ""}` }, value),
    h("div", { class: "stat-delta" }, sub ?? ""),
    extra && h("div", { class: "tile-extra" }, extra),
  ];
  return href ? h("a", { class: "stat live-tile", href }, body) : h("div", { class: "stat live-tile" }, body);
}

/** A set of tiles: as many across as fit them evenly, two on a phone. */
export function fillTiles(box, ...tiles) {
  const list = tiles.flat().filter(Boolean);
  box.dataset.n = String(list.length);
  box.replaceChildren(...list);
}

/** The pieces of a range row; `current` is shown pressed. */
export function rangeRow(names, current, pick) {
  return h("div", { class: "ranges", role: "group", "aria-label": "Time range" }, names.map(([id, label]) =>
    h("button", { type: "button", "aria-pressed": String(id === current), onclick: () => pick(id) }, label)));
}

export const RANGES = [["1h", "1h"], ["6h", "6h"], ["24h", "24h"], ["7d", "7d"], ["30d", "30d"], ["90d", "90d"]];

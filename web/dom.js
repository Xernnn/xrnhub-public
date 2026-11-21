// What every part of the page shares. Everything from a server goes in through textContent, never
// as markup: this origin is shared with the other xrn services. Sizes and colours go through CSSOM
// because the CSP forbids inline styles.

export function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props ?? {})) {
    if (k === "class") el.className = v;
    else if (k === "style") for (const [name, value] of Object.entries(v)) el.style.setProperty(name, value);
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (v === true) el.setAttribute(k, "");
    else if (v !== false && v != null) el.setAttribute(k, v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false) continue;
    el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

/** replaceChildren that takes what h() takes; alone it would print `false` and flatten nothing. */
export function fill(el, ...kids) {
  el.replaceChildren(...kids.flat(Infinity).filter((kid) => kid != null && kid !== false && kid !== ""));
}

export function svg(tag, attrs, ...kids) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs ?? {})) el.setAttribute(k, v);
  el.append(...kids);
  return el;
}

export class SignedOut extends Error {}

let onSignedOut = () => {};

/** Any request can find the session gone; the page goes to sign-in from wherever it was. */
export function whenSignedOut(run) {
  onSignedOut = run;
}

export async function api(path, options = {}) {
  const res = await fetch(path, {
    ...options,
    credentials: "same-origin",
    headers: { "X-Xrn-Client": "xrnhub", ...(options.headers ?? {}) },
  });
  if (res.status === 401) {
    onSignedOut();
    throw new SignedOut();
  }
  if (!res.ok) {
    const error = new Error((await res.text()) || `Error ${res.status}`);
    error.status = res.status;
    throw error;
  }
  return res;
}

export const getJson = async (path) => (await api(path)).json();

export const postJson = (path, doc) =>
  api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(doc) });

// ---- days and times ----------------------------------------------------------------------------

/** "2026-11-06" as a local date. */
export function dateOf(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d);
}

export function isoOf(date) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

export function addDays(iso, count) {
  const date = dateOf(iso);
  date.setDate(date.getDate() + count);
  return isoOf(date);
}

export const dayLong = (iso) =>
  dateOf(iso).toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" });
export const dayShort = (iso) =>
  dateOf(iso).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
export const dayPlain = (iso) => dateOf(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" });
export const clock = (date) => date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
export const clockOf = (text) => clock(new Date(text));

export function span(minutes) {
  const hours = Math.floor(minutes / 60);
  const rest = Math.round(minutes % 60);
  if (!hours) return `${rest}m`;
  return rest ? `${hours}h ${rest}m` : `${hours}h`;
}

export function money(cents, currency) {
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency: currency || "AUD",
    maximumFractionDigits: Math.abs(cents) >= 100000 ? 0 : 2,
  }).format(cents / 100);
}

// ---- small shared pieces -----------------------------------------------------------------------

export function ring(done, total, size = 46) {
  const r = size / 2 - 4;
  const length = 2 * Math.PI * r;
  const box = svg("svg", { viewBox: `0 0 ${size} ${size}`, class: "ring", "aria-hidden": "true" });
  box.append(svg("circle", { class: "ring-track", cx: size / 2, cy: size / 2, r }));
  if (done > 0 && total > 0) {
    const value = svg("circle", { class: "ring-value", cx: size / 2, cy: size / 2, r });
    value.style.strokeDasharray = `${length * Math.min(done / total, 1)} ${length}`;
    box.append(value);
  }
  return box;
}

export function card(title, side, ...kids) {
  return h("section", { class: "card" },
    title && h("div", { class: "card-head" }, h("h2", null, title), side),
    kids);
}

let toastTimer = 0;

/** One line at the bottom of the screen; with `undo`, a way back. */
export function toast(text, undo) {
  clearTimeout(toastTimer);
  document.querySelector(".toast")?.remove();
  const box = h("div", { class: "toast", role: "status" },
    h("span", null, text),
    undo && h("button", {
      type: "button",
      onclick: () => {
        box.remove();
        undo();
      },
    }, "Undo"));
  document.body.append(box);
  toastTimer = setTimeout(() => box.remove(), undo ? 5000 : 3500);
}

/** A tooltip above a mark. Rows are [value, label]; values lead. */
export function showTip(target, title, rows = []) {
  let tip = document.querySelector(".tip");
  if (!tip) {
    tip = h("div", { class: "tip", role: "status" });
    document.body.append(tip);
  }
  fill(tip, h("div", { class: "tip-title" }, title),
    rows.map(([value, label, color]) => h("div", { class: "tip-row" },
      color !== undefined && h("i", { style: { "background-color": color || "var(--muted)" } }),
      h("b", null, value), h("span", null, label))));
  const box = target.getBoundingClientRect();
  const wide = tip.offsetWidth;
  tip.style.left = `${Math.max(8, Math.min(box.left + box.width / 2 - wide / 2, innerWidth - wide - 8))}px`;
  tip.style.top = `${Math.max(8, box.top - tip.offsetHeight - 8)}px`;
}

export const hideTip = () => document.querySelector(".tip")?.remove();

/** Hover and keyboard focus both open the same tip. */
export function withTip(el, show) {
  el.addEventListener("pointerenter", show);
  el.addEventListener("pointerleave", hideTip);
  el.addEventListener("focus", show);
  el.addEventListener("blur", hideTip);
  return el;
}

// Habits are usually named with an emoji, which shows big. A name longer than two characters is
// set as words instead, so "Read 20 pages" wraps inside its tile rather than spilling out of it.
const graphemes = typeof Intl !== "undefined" && Intl.Segmenter ? new Intl.Segmenter() : null;
export const isWordName = (name) =>
  (graphemes ? [...graphemes.segment(name ?? "")].length : [...(name ?? "")].length) > 2;
export const glyphClass = (name) => (isWordName(name) ? "glyph word" : "glyph");


// The tap grid. A tick goes straight to xrnloops' own API on this origin, under
// the shared sign-in, so it lands exactly as if the xrnloops app had made it: its phone app and
// xrnrule see it on their next sync, and the hub's server stays read-only on xrnloops' data.

import { api, fill, glyphClass, h, ring, svg, toast } from "./dom.js";

const NOT_LOGGED = -1;
const YES_MANUAL = 2;
const SKIP = 3;
const LONG_PRESS_MS = 450;
const NUMERIC = 1;
const AT_MOST = 1;

// xrnloops' habit colours, so a habit looks the same in both apps.
const COLORS_LIGHT = [
  "#D32F2F", "#E64A19", "#F57C00", "#FF8F00", "#F9A825", "#AFB42B", "#7CB342", "#388E3C",
  "#00897B", "#00ACC1", "#039BE5", "#1976D2", "#303F9F", "#5E35B1", "#8E24AA", "#D81B60",
  "#5D4037", "#27272A", "#757575", "#9E9E9E",
];
const COLORS_DARK = [
  "#EF9A9A", "#FFAB91", "#FFCC80", "#FFECB3", "#FFF59D", "#E6EE9C", "#C5E1A5", "#69F0AE",
  "#80CBC4", "#80DEEA", "#81D4FA", "#64B5F6", "#9FA8DA", "#B39DDB", "#CE93D8", "#F48FB1",
  "#BCAAA4", "#F5F5F5", "#E0E0E0", "#9E9E9E",
];

export const habitColors = (index) => ({
  "--c-light": COLORS_LIGHT[index] ?? COLORS_LIGHT[18],
  "--c-dark": COLORS_DARK[index] ?? COLORS_DARK[18],
});

const logged = (value) => value != null && value >= 0;

function isDone(row, value) {
  if (!logged(value)) return false;
  if (row.type !== NUMERIC) return value === 1 || value === YES_MANUAL;
  const amount = value / 1000;
  return row.targetType === AT_MOST ? amount <= row.targetValue : amount >= row.targetValue;
}

/** The same reading the server gives a row, for a value that hasn't reached it yet. */
function setValue(row, value) {
  row.value = value;
  row.done = isDone(row, value);
  row.skipped = row.type !== NUMERIC && value === SKIP;
  row.counts = (row.done || row.daily) && !row.skipped;
}

export function score(rows) {
  const counted = rows.filter((r) => r.counts);
  return { done: counted.filter((r) => r.done).length, total: counted.length };
}

let line = Promise.resolve();
let writing = 0;
let changed = false;

export const writesPending = () => writing > 0;

/** Whether anything was ticked since the last ask; the long view's cache doesn't hear of ticks. */
export function takeChanged() {
  const was = changed;
  changed = false;
  return was;
}

/** Sends writes one at a time, in order, so two quick taps can't arrive backwards. */
function send(entries) {
  writing += 1;
  changed = true;
  const run = async () => {
    const res = await api("/xrnloops/api/changes", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ habits: [], entries }),
    });
    return res.json();
  };
  const result = line.then(run, run);
  line = result.catch(() => {}).finally(() => {
    writing -= 1;
  });
  return result;
}

/**
 * Changes rows on screen now and sends them. `onChange` redraws; on a failed write the old values
 * come back and it redraws again. A record that lost to a newer one is a sign another device
 * wrote: `onStale` fetches the truth.
 */
export async function writeRows(changes, day, { onChange, onStale, onError }) {
  const before = changes.map(({ row }) => [row, row.value, row.mtime]);
  const entries = changes.map(({ row, value }) => {
    setValue(row, value);
    row.mtime = Math.max(Date.now(), (row.mtime ?? 0) + 1);
    return { habit: row.id, date: day, value, notes: row.notes ?? "", mtime: row.mtime };
  });
  onChange();
  try {
    const done = await send(entries);
    if (done.rejected?.entries?.length) onStale();
  } catch (e) {
    for (const [row, value, mtime] of before) {
      setValue(row, value);
      row.mtime = mtime;
    }
    onChange();
    onError(e);
  }
}

function tick() {
  return svg("svg", { viewBox: "0 0 16 16", class: "tick", "aria-hidden": "true" },
    svg("path", { d: "M3.4 8.4 6.6 11.4 12.6 4.8" }));
}

function padlock() {
  return svg("svg", { viewBox: "0 0 16 16", class: "lock", "aria-hidden": "true" },
    svg("path", { d: "M5 7V5a3 3 0 0 1 6 0v2" }), svg("rect", { x: 3.5, y: 7, width: 9, height: 6.5, rx: 2 }));
}

function checkTile(row, { key, write, unlocks }) {
  const button = h("button", {
    type: "button",
    style: habitColors(row.color),
    title: unlocks ? `Unlocks ${unlocks}` : null,
  });
  let pressTimer = 0;
  let pressed = false;
  const draw = () => {
    button.className = `tile ${row.done ? "done" : ""} ${row.skipped ? "skipped" : ""}`;
    const state = row.done ? "done" : row.skipped ? "skipped" : "not done";
    button.setAttribute("aria-label", `${row.name} ${state}${row.streak > 1 ? `, ${row.streak} days running` : ""}`);
    button.setAttribute("aria-pressed", String(row.done));
    fill(button,
      h("span", { class: glyphClass(row.name) }, row.name),
      row.done && h("span", { class: "badge" }, tick()),
      row.skipped && h("span", { class: "badge rest" }, "–"),
      row.streak > 1 && h("span", { class: "streak" }, row.streak),
      unlocks && h("span", { class: "unlocks" }, padlock()),
      key && h("kbd", null, key));
  };
  const flip = () => {
    write([{ row, value: row.done ? NOT_LOGGED : YES_MANUAL }]);
    button.classList.remove("pop");
    void button.offsetWidth;
    button.classList.add("pop");
  };
  const skip = () => {
    const was = row.value ?? NOT_LOGGED;
    write([{ row, value: row.skipped ? NOT_LOGGED : SKIP }]);
    if (row.skipped) toast(`${row.name} skipped`, () => write([{ row, value: was }]));
  };
  button.addEventListener("pointerdown", (e) => {
    if (e.button) return;
    pressed = false;
    pressTimer = setTimeout(() => {
      pressed = true;
      skip();
    }, LONG_PRESS_MS);
  });
  for (const name of ["pointerup", "pointerleave", "pointercancel"]) {
    button.addEventListener(name, () => clearTimeout(pressTimer));
  }
  button.addEventListener("contextmenu", (e) => {
    e.preventDefault();
    clearTimeout(pressTimer);
    if (!pressed) skip();
  });
  button.addEventListener("click", () => {
    if (pressed) pressed = false;
    else flip();
  });
  draw();
  return { el: button, draw, press: flip };
}

function numberTile(row, { key, write, unlocks }) {
  const amount = h("b");
  const box = h("div", { class: "tile number", style: habitColors(row.color), title: unlocks ? `Unlocks ${unlocks}` : null });
  const set = (value) => write([{ row, value }]);
  const more = () => set((logged(row.value) ? row.value : 0) + 1000);
  const less = () => {
    if (!logged(row.value)) return set(0);
    return set(row.value === 0 ? NOT_LOGGED : Math.max(row.value - 1000, 0));
  };
  const draw = () => {
    box.classList.toggle("done", row.done);
    box.classList.toggle("over", logged(row.value) && !row.done);
    const shown = logged(row.value) ? row.value / 1000 : "–";
    amount.textContent = `${shown}/${row.targetValue}`;
  };
  fill(box,
    h("button", { type: "button", class: "step", "aria-label": `${row.name} less`, onclick: less }, "−"),
    h("span", { class: "middle" }, h("span", { class: glyphClass(row.name) }, row.name), amount),
    h("button", { type: "button", class: "step", "aria-label": `${row.name} more`, onclick: more }, "+"),
    key && h("kbd", null, key),
    unlocks && h("span", { class: "unlocks" }, padlock()));
  draw();
  return { el: box, draw, press: more };
}

/** The grid for one day. `locks` maps a habit id to the block it opens; `onChange` follows every tap. */
export function habitGrid(rows, day, { locks = {}, onChange, onStale, keys = false }) {
  const tiles = [];
  const redraw = () => {
    for (const tile of tiles) tile.draw();
    onChange();
  };
  const write = (changes) => writeRows(changes, day, {
    onChange: redraw,
    onStale,
    onError: () => toast("Not saved. Check the connection."),
  });
  rows.forEach((row, i) => {
    const make = row.type === NUMERIC ? numberTile : checkTile;
    const key = keys && i < 9 ? String(i + 1) : null;
    tiles.push(make(row, { key, write, unlocks: locks[row.id] }));
  });
  return {
    el: h("div", { class: "tiles" }, tiles.map((t) => t.el)),
    press: (index) => tiles[index]?.press(),
    /** Redraws in place, so a refresh leaves focus and a finger mid-tap where they were. */
    sync: () => tiles.forEach((t) => t.draw()),
    tickAll() {
      const open = rows.filter((r) => r.type !== NUMERIC && !r.done && !r.skipped);
      if (!open.length) return;
      const was = open.map((row) => ({ row, value: row.value ?? NOT_LOGGED }));
      write(open.map((row) => ({ row, value: YES_MANUAL })));
      toast(`${open.length} ticked`, () => write(was));
    },
  };
}

export function scoreRing(done, total) {
  return h("div", { class: "score" }, ring(done, total), h("span", null, h("b", null, done), `/${total}`));
}

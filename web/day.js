// One day in full, over whatever page opened it: fix its habits, see what was on.

import { addDays, clock, clockOf, dayShort, fill, getJson, h, money, span } from "./dom.js";
import { habitGrid, score, scoreRing } from "./habits.js";

function agendaRows(items) {
  return items.map((e) => h("div", { class: "row" },
    h("span", { class: "time" }, e.allDay ? "" : clockOf(e.start)),
    h("i", { class: "dot", style: { "background-color": e.color || "var(--muted)" } }),
    h("span", { class: "grow clip" }, e.title)));
}

function logText(e) {
  switch (e.kind) {
    case "blocked": return `Stopped ${e.title}`;
    case "unlock": return `Unlocked ${e.title}${e.value ? ` for ${e.value}m` : ""}`;
    case "spent": return e.title || "Spent";
    case "earned": return e.title || "Earned";
    case "batch": return `${e.value ?? 0} mail delivered`;
    case "early": return "Mail delivered early";
    default: return e.title || e.kind;
  }
}

function logRows(items) {
  return items.map((e) => h("div", { class: "row" },
    h("span", { class: "time" }, clock(new Date(e.at))),
    h("span", { class: "grow clip" }, logText(e)),
    e.cents != null && h("span", { class: "side" }, money(e.cents))));
}

const STAGES = [["deep", "Deep"], ["light", "Light"], ["rem", "REM"], ["asleep", "Asleep"], ["awake", "Awake"]];

function healthRow(label, value, sub) {
  return h("div", { class: "row" }, h("span", { class: "grow" }, label, sub && h("span", { class: "sub" }, ` ${sub}`)), h("b", null, value));
}

/** The day's steps, sleep and readings; the sleep's stages where the source said them. */
function healthRows(day) {
  const { steps, sleep, heartRate: heart, weightKg, spo2, stress } = day;
  const stages = sleep && STAGES.filter(([key]) => sleep[key] > 0 && !(key === "asleep" && sleep.asleep === sleep.minutes));
  const range = (r) => (r.min === r.max ? "" : `${r.min}–${r.max}`);
  return [
    steps != null && healthRow("Steps", steps.toLocaleString()),
    sleep && healthRow("Asleep", span(sleep.minutes), `${clock(new Date(sleep.start))}–${clock(new Date(sleep.end))}`),
    stages?.length > 0 && h("div", { class: "stages" }, stages.map(([key, name]) => h("span", null, `${name} ${span(sleep[key])}`))),
    heart?.resting != null && healthRow("Resting heart rate", `${heart.resting} bpm`),
    heart?.avg != null && healthRow("Heart rate", `${heart.avg} bpm`, `${range(heart)} through the day`),
    weightKg != null && healthRow("Weight", `${weightKg.toFixed(1)} kg`),
    spo2 && healthRow("SpO2", `${spo2.avg}%`, range(spo2)),
    stress && healthRow("Stress", String(stress.avg), range(stress)),
  ];
}

export function openDay(iso, { first, last, onClose }) {
  const body = h("div", { class: "sheet-body" });
  const title = h("h2");
  const scoreBox = h("div", { class: "score-slot" });
  let day = iso;
  let asked = 0;
  const earlier = h("button", { type: "button", class: "round", "aria-label": "Day before", onclick: () => go(-1) }, "‹");
  const later = h("button", { type: "button", class: "round", "aria-label": "Day after", onclick: () => go(1) }, "›");
  const draw = (doc) => {
    const rows = doc.habits.items;
    const tally = () => {
      const now = score(rows);
      fill(scoreBox, scoreRing(now.done, now.total));
    };
    tally();
    const grid = habitGrid(rows, doc.day, { onChange: tally, onStale: () => load() });
    fill(body,
      rows.length > 0 && grid.el,
      doc.calendar.items.length > 0 && h("section", null, h("h3", null, "On the calendar"), agendaRows(doc.calendar.items)),
      doc.health && h("section", null, h("h3", null, "Health"), healthRows(doc.health)),
      doc.log.length > 0 && h("section", null, h("h3", null, "Also"), logRows(doc.log)));
  };
  const load = async () => {
    const mine = ++asked;
    title.textContent = dayShort(day);
    earlier.disabled = day <= first;
    later.disabled = day >= last;
    const doc = await getJson(`api/day?date=${day}`);
    if (mine === asked) draw(doc);
  };
  const go = (step) => {
    const next = addDays(day, step);
    if (next < first || next > last) return;
    day = next;
    load();
  };
  const dialog = h("dialog", {
    class: "sheet",
    onclose: () => {
      dialog.remove();
      onClose?.();
    },
    onclick: (e) => {
      if (e.target === dialog) dialog.close();
    },
    onkeydown: (e) => {
      if (e.target.tagName === "INPUT") return;
      if (e.key === "ArrowLeft") go(-1);
      if (e.key === "ArrowRight") go(1);
    },
  },
  h("div", { class: "sheet-frame" },
    h("div", { class: "sheet-head" },
      earlier, later, title, scoreBox,
      h("button", { type: "button", class: "round", "aria-label": "Close", onclick: () => dialog.close() }, "×")),
    body));
  document.body.append(dialog);
  dialog.showModal();
  return load();
}

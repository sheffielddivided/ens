"use strict";

/* ENS production explorer.
 * Loads data/combined.json (built by scripts/update.py); falls back to a
 * labelled synthetic sample. Colours come from CSS custom properties (the
 * validated dataviz reference palette) so light/dark and the theme toggle
 * restyle the charts in one place. Every field keeps ONE colour across all
 * charts (identity, not rank); the 8 biggest fields take the categorical
 * slots, the rest fold into "Andre felt". */

const MEASURES = ["oil", "gas", "water"];
const MEASURE_LABEL = { oil: "Olje", gas: "Gass", water: "Vann" };
const SLOTS = ["--c1", "--c2", "--c3", "--c4", "--c5", "--c6", "--c7", "--c8"];
const MONTHS_NB = ["jan", "feb", "mar", "apr", "mai", "jun", "jul", "aug", "sep", "okt", "nov", "des"];

let DATA = null, timeChart = null, rankChart = null;
let TOP = [];                 // 8 biggest fields (slugs), fixed colour order
let fieldSlot = {};           // slug -> CSS var name (or null => "other")
let displayName = {};         // slug -> label
const state = { measure: "oil", res: "yearly", view: "total", unit: "si", annualize: true, selected: new Set(), year: null };

const $ = (id) => document.getElementById(id);
const css = (v) => getComputedStyle(document.body).getPropertyValue(v).trim();
const toMap = (arr) => { const m = {}; (arr || []).forEach((p) => { m[p.t] = p.v; }); return m; };
const prettyUnit = (u) => (u || "").replace(/Nm3/g, "Nm³").replace(/m3/g, "m³");

// Oil-equivalent conversion: 1 m³ oil ≈ 6.29 barrels. Used for the per-day unit
// and for stacking oil/gas/water in a common oil-equivalent unit.
const BOE = 6.29;

function daysInPeriod(t) {
  const mm = /^(\d{4})-(\d{2})$/.exec(t);        // month: real days in that month
  if (mm) return new Date(+mm[1], +mm[2], 0).getDate();
  const y = +t;                                   // year: 365 or 366
  if (!isNaN(y)) return ((y % 4 === 0 && y % 100 !== 0) || y % 400 === 0) ? 366 : 365;
  return 365;
}

// Months of the year that actually have monthly data (report coverage).
function monthsPresent(year) {
  return (DATA.series._total?.monthly?.oil || []).filter((p) => p.t.startsWith(year + "-"));
}
// Gross-up factor for an incomplete year: 365 / days-with-data (1 otherwise).
// Only applies to preliminary years (aggregated from monthly) in year view.
function annualFactor(t) {
  if (!state.annualize || state.res !== "yearly" || !/^\d{4}$/.test(t)) return 1;
  const yp = (DATA.series._total?.yearly?.oil || []).find((p) => p.t === t);
  if (!yp || !yp.p) return 1;                     // final year from the Excel
  const months = monthsPresent(t);
  if (months.length === 0 || months.length >= 12) return 1;   // complete year
  const days = months.reduce((a, p) => a + new Date(+t, +p.t.slice(5, 7), 0).getDate(), 0);
  return days > 0 ? 365 / days : 1;
}
function isAnnualized(t) { return annualFactor(t) !== 1; }

// Single-measure display value + unit (tiles, rank, total/field/compare views).
function mVal(v, t) {
  if (v == null) return v;
  const b = v * annualFactor(t);
  return state.unit === "boed" ? b * BOE / daysInPeriod(t) : b;
}
const mUnit = (m) => state.unit === "boed" ? "1000 fat o.e./dag" : prettyUnit((DATA.unit_definitions || {})[m] || "");

// Oil-equivalent value + unit (for stacking oil/gas/water together).
function oeVal(v, t) {
  if (v == null) return v;
  const b = v * annualFactor(t) * BOE;
  return state.unit === "boed" ? b / daysInPeriod(t) : b;
}
const oeUnit = () => state.unit === "boed" ? "1000 fat o.e./dag" : "1000 fat o.e.";

// Value formatter: more decimals for the smaller per-day numbers.
function fmtVal(v) {
  const a = Math.abs(v), d = a >= 100 ? 0 : a >= 10 ? 1 : 2;
  return new Intl.NumberFormat("nb-NO", { maximumFractionDigits: d }).format(v);
}
const colorOf = (slug) => (fieldSlot[slug] ? css(fieldSlot[slug]) : css("--c-other"));
const measureColor = () => css("--" + state.measure);

// --------------------------------------------------------------------------- boot
async function boot() {
  DATA = await load();
  if (!DATA) return;
  try {
    prepare();
    buildControls();
    applyChartDefaults();
    renderAll();
  } catch (e) {
    const eb = $("error-banner");
    if (eb) { eb.textContent = "Kunne ikke bygge visningen: " + e.message; eb.classList.remove("hidden"); }
    throw e;
  }
}

async function load() {
  const real = await tryFetch("data/combined.json");
  if (real && real.series && Object.keys(real.series).length) return real;
  const sample = await tryFetch("data/combined.sample.json");
  if (sample && sample.series && Object.keys(sample.series).length) {
    $("sample-banner").classList.remove("hidden");
    return sample;
  }
  const eb = $("error-banner");
  eb.textContent = "Fant ingen data. Kjør scripts/update.py for å bygge data/combined.json.";
  eb.classList.remove("hidden");
  return null;
}
async function tryFetch(u) {
  try { const r = await fetch(u, { cache: "no-store" }); return r.ok ? await r.json() : null; }
  catch (e) { return null; }
}

function prepare() {
  displayName = {};
  DATA.fields.forEach((f) => { displayName[f.slug] = f.display_name; });

  // Rank fields by all-time production (sum of oil+gas+water yearly).
  const fields = DATA.fields.map((f) => f.slug).filter((s) => s !== "_total");
  const allTime = (slug) => MEASURES.reduce((a, m) =>
    a + (DATA.series[slug]?.yearly?.[m] || []).reduce((b, p) => b + p.v, 0), 0);
  const ranked = fields.slice().sort((a, b) => allTime(b) - allTime(a));
  TOP = ranked.slice(0, 8);
  fieldSlot = {};
  TOP.forEach((slug, i) => { fieldSlot[slug] = SLOTS[i]; });
  state.selected = new Set(ranked.slice(0, 4));

  // Last complete (non-preliminary) year, and the master year list.
  const years = (DATA.series._total?.yearly?.oil || []).map((p) => p.t);
  const finals = (DATA.series._total?.yearly?.oil || []).filter((p) => !p.p).map((p) => p.t);
  state.year = finals.length ? finals[finals.length - 1] : (years[years.length - 1] || null);

  // updated stamp
  const el = $("updated");
  if (DATA.last_updated) {
    const d = new Date(DATA.last_updated);
    el.textContent = isNaN(d) ? "Sist oppdatert: " + DATA.last_updated :
      `Sist oppdatert ${d.getUTCDate()}. ${["jan","feb","mar","apr","mai","jun","jul","aug","sep","okt","nov","des"][d.getUTCMonth()]}. ${d.getUTCFullYear()}.`;
  }
}

// --------------------------------------------------------------------------- controls
function buildControls() {
  segGroup("measure-seg", "measure", (v) => { state.measure = v; renderAll(); });
  segGroup("res-seg", "res", (v) => { state.res = v; renderTime(); });
  segGroup("view-seg", "view", (v) => {
    state.view = v;
    $("field-picker").classList.toggle("hidden", v !== "compare");
    renderTime();
  });
  segGroup("unit-seg", "unit", (v) => { state.unit = v; renderAll(); });

  // annualize toggle (gross the current partial year up to a full year)
  const ann = $("annualize");
  if (ann) {
    ann.checked = state.annualize;
    ann.addEventListener("change", () => { state.annualize = ann.checked; renderAll(); });
  }

  // field picker (compare mode)
  const pick = $("field-picker");
  DATA.fields.filter((f) => f.slug !== "_total").forEach((f) => {
    const lab = document.createElement("label");
    const cb = document.createElement("input");
    cb.type = "checkbox"; cb.value = f.slug; cb.checked = state.selected.has(f.slug);
    cb.addEventListener("change", () => {
      cb.checked ? state.selected.add(f.slug) : state.selected.delete(f.slug);
      if (state.view === "compare") renderTime();
    });
    const sw = document.createElement("span");
    sw.className = "sw"; sw.style.background = colorOf(f.slug);
    lab.append(cb, sw, document.createTextNode(f.display_name));
    pick.appendChild(lab);
  });

  // year slider
  const yrs = (DATA.series._total?.yearly?.oil || []).map((p) => p.t);
  const sl = $("year-slider");
  sl.min = 0; sl.max = Math.max(0, yrs.length - 1);
  sl.value = Math.max(0, yrs.indexOf(state.year));
  sl.addEventListener("input", () => { state.year = yrs[+sl.value]; renderRank(); });

  // theme toggle
  const saved = localStorage.getItem("ens-theme");
  if (saved) document.documentElement.setAttribute("data-theme", saved);
  $("theme-toggle").addEventListener("click", () => {
    const cur = document.documentElement.getAttribute("data-theme");
    const next = cur === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    localStorage.setItem("ens-theme", next);
    applyChartDefaults(); renderAll();
  });
}

function segGroup(id, key, onPick) {
  $(id).querySelectorAll("button").forEach((btn) => {
    btn.addEventListener("click", () => {
      $(id).querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", "false"));
      btn.setAttribute("aria-pressed", "true");
      onPick(btn.dataset[key]);
    });
  });
}

// --------------------------------------------------------------------------- rendering
function applyChartDefaults() {
  if (!window.Chart) return;
  Chart.defaults.font.family = "system-ui, -apple-system, 'Segoe UI', sans-serif";
  Chart.defaults.color = css("--ink-2");
  Chart.defaults.borderColor = css("--grid");
}

function renderAll() { renderTiles(); renderTime(); renderRank(); }

function renderTiles() {
  const tot = DATA.series._total.yearly;
  // Tiles always show the latest COMPLETE (final) year, independent of the
  // ranking-chart year slider.
  const finals = (tot.oil || []).filter((p) => !p.p);
  const yr = finals.length ? finals[finals.length - 1].t : ((tot.oil || []).slice(-1)[0]?.t || "–");
  const tiles = [];
  for (const m of MEASURES) {
    const pt = (tot[m] || []).find((p) => p.t === yr);
    tiles.push(`<div class="tile"><div class="label"><span class="dot" style="background:${css("--" + m)}"></span>${MEASURE_LABEL[m]} ${yr}</div>
      <div class="value">${pt ? fmtVal(mVal(pt.v, yr)) : "–"} <span class="unit">${mUnit(m)}</span></div>
      <div class="foot">${pt && pt.p ? "foreløpig" : "endelige tall"}</div></div>`);
  }
  const producing = DATA.fields.filter((f) => f.slug !== "_total")
    .filter((f) => (DATA.series[f.slug]?.yearly?.oil || []).some((p) => p.t === yr && p.v > 0)).length;
  tiles.push(`<div class="tile"><div class="label">Felt i produksjon ${yr}</div>
    <div class="value">${producing}</div><div class="foot">med oljeproduksjon</div></div>`);
  // peak oil year (in the displayed per-measure unit; not annualized)
  const peak = (tot.oil || []).map((p) => ({ t: p.t, v: mVal(p.v, p.t) }))
    .reduce((a, p) => (p.v > a.v ? p : a), { v: -1, t: "–" });
  tiles.push(`<div class="tile"><div class="label">Toppår olje</div>
    <div class="value">${peak.t}</div><div class="foot">${fmtVal(peak.v)} ${mUnit("oil")}</div></div>`);
  $("tiles").innerHTML = tiles.join("");
}

function labelFmt(t) {
  const m = /^(\d{4})-(\d{2})$/.exec(t);
  return m ? `${MONTHS_NB[+m[2] - 1]} ${m[1]}` : t;
}

function renderTime() {
  const isSum = state.view === "sum";
  // All measures share the same period axis; use oil's total series as the master.
  const base = DATA.series._total[state.res][isSum ? "oil" : state.measure] || [];
  const labels = base.map((p) => p.t);
  const prelimIdx = base.findIndex((p) => p.p);
  const surface = css("--surface");
  const curUnit = isSum ? oeUnit() : mUnit(state.measure);
  let datasets = [], stacked = false;

  if (isSum) {                                   // stack oil + gas + water in o.e.
    stacked = true;
    ["oil", "gas", "water"].forEach((m, i) => {
      const mp = toMap(DATA.series._total[state.res][m]);
      datasets.push(areaDS(MEASURE_LABEL[m], labels.map((t) => oeVal(mp[t] ?? 0, t)), css("--" + m), i === 0, surface));
    });
  } else if (state.view === "total") {
    datasets = [lineDS("Alle felt", base.map((p) => mVal(p.v, p.t)), measureColor(), prelimIdx)];
  } else if (state.view === "compare") {
    const chosen = DATA.fields.map((f) => f.slug).filter((s) => state.selected.has(s));
    datasets = chosen.map((slug) => {
      const mp = toMap(DATA.series[slug]?.[state.res]?.[state.measure]);
      return lineDS(displayName[slug], labels.map((t) => (mp[t] == null ? null : mVal(mp[t], t))), colorOf(slug), prelimIdx);
    });
  } else {                                       // stacked area by field
    stacked = true;
    const maps = TOP.map((slug) => toMap(DATA.series[slug]?.[state.res]?.[state.measure]));
    const totMap = toMap(base);
    datasets = TOP.map((slug, i) =>
      areaDS(displayName[slug], labels.map((t) => mVal(maps[i][t] ?? 0, t)), colorOf(slug), i === 0, surface));
    const other = labels.map((t) => {
      const s = maps.reduce((a, mp) => a + (mp[t] || 0), 0);        // native sum
      return mVal(Math.max(0, +((totMap[t] || 0) - s).toFixed(3)), t);
    });
    datasets.push(areaDS("Andre felt", other, css("--c-other"), false, surface));
  }

  const cfg = {
    type: "line",
    data: { labels, datasets },
    options: {
      responsive: true, maintainAspectRatio: false, animation: { duration: 250 },
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { display: datasets.length > 1, position: "bottom",
          labels: { boxWidth: 10, boxHeight: 10, usePointStyle: true, pointStyle: "rectRounded", padding: 12 } },
        tooltip: {
          callbacks: {
            title: (it) => labelFmt(it[0].label),
            label: (it) => `${it.dataset.label}: ${fmtVal(it.parsed.y)} ${curUnit}`,
            footer: (it) => {
              const t = labels[it[0].dataIndex], parts = [];
              if (prelimIdx >= 0 && it[0].dataIndex >= prelimIdx) parts.push("foreløpige tall");
              if (isAnnualized(t)) parts.push("estimert helår");
              return parts.join(" · ");
            },
          },
        },
        prelim: { index: prelimIdx, fill: css("--grid") + "66", text: css("--muted") },
      },
      scales: {
        x: { stacked, grid: { display: false }, ticks: { maxTicksLimit: 13, autoSkip: true, callback(v) { const l = this.getLabelForValue(v); return /^\d{4}-\d{2}$/.test(l) ? l.slice(0, 4) : l; } } },
        y: { stacked, beginAtZero: true, border: { display: false },
          title: { display: true, text: curUnit, color: css("--muted") },
          ticks: { callback: (v) => fmtVal(v) } },
      },
    },
  };
  if (timeChart) timeChart.destroy();
  timeChart = new Chart($("timeChart"), cfg);

  // The measure switch is irrelevant when stacking all three measures.
  $("measure-seg").style.opacity = isSum ? ".4" : "1";
  $("measure-seg").style.pointerEvents = isSum ? "none" : "auto";

  const capParts = {
    total: "Sum for alle felt.",
    stacked: `Bidrag fra de ${TOP.length} største feltene; resten er samlet i «Andre felt».`,
    compare: "Velg felt over grafen for å sammenligne.",
    sum: "Olje, gass og vann stablet i oljeekvivalenter (× 6,29).",
  };
  let cap = capParts[state.view];
  if (prelimIdx >= 0) cap += ` <span class="prelim">Skravert område</span> er foreløpige år (overstyres av endelige årstall).`;
  if (state.res === "yearly" && state.annualize && labels.some(isAnnualized))
    cap += " Siste år er oppjustert til helårsestimat (× 365 ÷ dager med data).";
  if (state.unit === "boed" && !isSum) cap += " Verdier i oljeekvivalenter per dag (× 6,29 ÷ dager).";
  $("time-cap").innerHTML = cap;
  $("time-sub").textContent = isSum
    ? `– olje + gass + vann (${curUnit})`
    : `– ${MEASURE_LABEL[state.measure].toLowerCase()} (${curUnit})`;
}

function lineDS(label, data, color, prelimIdx) {
  return {
    label, data, borderColor: color, backgroundColor: color + "22",
    borderWidth: 2, tension: 0.15, spanGaps: true,
    pointRadius: 0, pointHoverRadius: 4, pointBackgroundColor: color,
    segment: { borderDash: (ctx) => (prelimIdx >= 0 && ctx.p1DataIndex >= prelimIdx ? [6, 4] : undefined) },
  };
}
function areaDS(label, data, color, isBottom, surface) {
  return {
    label, data, backgroundColor: color, borderColor: surface, borderWidth: 1.2,
    fill: isBottom ? "origin" : "-1", tension: 0.15, pointRadius: 0, pointHoverRadius: 0,
  };
}

function renderRank() {
  const yr = state.year, m = state.measure;
  const rows = DATA.fields.map((f) => f.slug).filter((s) => s !== "_total")
    .map((slug) => ({ slug, v: mVal(toMap(DATA.series[slug]?.yearly?.[m])[yr] || 0, yr) }))
    .filter((r) => r.v > 0).sort((a, b) => b.v - a.v);
  const prelim = (DATA.series._total.yearly[m] || []).find((p) => p.t === yr)?.p;

  const cfg = {
    type: "bar",
    data: {
      labels: rows.map((r) => displayName[r.slug]),
      datasets: [{
        label: MEASURE_LABEL[m], data: rows.map((r) => r.v),
        backgroundColor: rows.map((r) => colorOf(r.slug)),
        borderRadius: 4, borderSkipped: false, barThickness: "flex", maxBarThickness: 26,
      }],
    },
    options: {
      indexAxis: "y", responsive: true, maintainAspectRatio: false, animation: { duration: 250 },
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: (it) => `${fmtVal(it.parsed.x)} ${mUnit(m)}${prelim ? " (foreløpig)" : ""}` } },
        prelim: { index: -1 },
      },
      scales: {
        x: { beginAtZero: true, border: { display: false }, grid: { color: css("--grid") },
          title: { display: true, text: mUnit(m), color: css("--muted") }, ticks: { callback: (v) => fmtVal(v) } },
        y: { grid: { display: false }, border: { display: false }, ticks: { autoSkip: false, font: { size: 12 } } },
      },
    },
  };
  if (rankChart) rankChart.destroy();
  rankChart = new Chart($("rankChart"), cfg);
  $("year-out").textContent = yr;
  $("rank-year-label").textContent = `– ${MEASURE_LABEL[m].toLowerCase()}, ${yr}`;
  $("rank-cap").innerHTML = `${rows.length} felt i produksjon (${MEASURE_LABEL[m].toLowerCase()}) i ${yr}` +
    (prelim ? ' — <span class="prelim">foreløpige tall</span>' : "") +
    (isAnnualized(yr) ? " — oppjustert til helårsestimat" : "") + ".";
}

// Preliminary-region shading plugin (shared by the time chart).
const prelimPlugin = {
  id: "prelim",
  beforeDatasetsDraw(chart, args, opts) {
    if (!opts || opts.index == null || opts.index < 0) return;
    const { ctx, chartArea, scales: { x } } = chart;
    const step = x.getPixelForValue(1) - x.getPixelForValue(0);
    const x0 = Math.max(chartArea.left, x.getPixelForValue(opts.index) - step / 2);
    ctx.save();
    ctx.fillStyle = opts.fill || "rgba(137,135,129,0.12)";
    ctx.fillRect(x0, chartArea.top, chartArea.right - x0, chartArea.bottom - chartArea.top);
    ctx.fillStyle = opts.text || "#898781";
    ctx.font = "11px system-ui, sans-serif";
    ctx.fillText("Foreløpig", x0 + 6, chartArea.top + 13);
    ctx.restore();
  },
};
if (window.Chart) Chart.register(prelimPlugin);

boot();

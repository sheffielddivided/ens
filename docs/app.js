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
const state = { measure: "oil", res: "yearly", view: "total", selected: new Set(), year: null };

const $ = (id) => document.getElementById(id);
const css = (v) => getComputedStyle(document.body).getPropertyValue(v).trim();
const toMap = (arr) => { const m = {}; (arr || []).forEach((p) => { m[p.t] = p.v; }); return m; };
const fmt = (v, d = 0) => new Intl.NumberFormat("nb-NO", { maximumFractionDigits: d }).format(v);
const prettyUnit = (u) => (u || "").replace(/Nm3/g, "Nm³").replace(/m3/g, "m³");
const unitOf = (m) => prettyUnit((DATA.unit_definitions || {})[m] || "");
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
  const yr = state.year;
  const tiles = [];
  for (const m of MEASURES) {
    const pt = (tot[m] || []).find((p) => p.t === yr);
    tiles.push(`<div class="tile"><div class="label"><span class="dot" style="background:${css("--" + m)}"></span>${MEASURE_LABEL[m]} ${yr}</div>
      <div class="value">${pt ? fmt(pt.v, 0) : "–"} <span class="unit">${unitOf(m)}</span></div>
      <div class="foot">${pt && pt.p ? "foreløpig" : "endelige tall"}</div></div>`);
  }
  // producing fields that year
  const producing = DATA.fields.filter((f) => f.slug !== "_total")
    .filter((f) => (DATA.series[f.slug]?.yearly?.oil || []).some((p) => p.t === yr && p.v > 0)).length;
  tiles.push(`<div class="tile"><div class="label">Felt i produksjon ${yr}</div>
    <div class="value">${producing}</div><div class="foot">med oljeproduksjon</div></div>`);
  // peak oil year
  const oilSeries = tot.oil || [];
  const peak = oilSeries.reduce((a, p) => (p.v > a.v ? p : a), { v: -1, t: "–" });
  tiles.push(`<div class="tile"><div class="label">Toppår olje</div>
    <div class="value">${peak.t}</div><div class="foot">${fmt(peak.v, 0)} ${unitOf("oil")}</div></div>`);
  $("tiles").innerHTML = tiles.join("");
}

function masterTimeline() {
  const master = DATA.series._total[state.res][state.measure] || [];
  return { labels: master.map((p) => p.t), master, prelimIdx: master.findIndex((p) => p.p) };
}

function labelFmt(t) {
  const m = /^(\d{4})-(\d{2})$/.exec(t);
  return m ? `${MONTHS_NB[+m[2] - 1]} ${m[1]}` : t;
}

function renderTime() {
  const { labels, master, prelimIdx } = masterTimeline();
  const mc = measureColor();
  const surface = css("--surface");
  let datasets = [];
  let stacked = false;

  if (state.view === "total") {
    datasets = [lineDS("Alle felt", master.map((p) => p.v), mc, prelimIdx)];
  } else if (state.view === "compare") {
    const chosen = DATA.fields.map((f) => f.slug).filter((s) => state.selected.has(s));
    datasets = chosen.map((slug) => {
      const mp = toMap(DATA.series[slug]?.[state.res]?.[state.measure]);
      return lineDS(displayName[slug], labels.map((t) => mp[t] ?? null), colorOf(slug), prelimIdx);
    });
  } else { // stacked area by field
    stacked = true;
    const maps = TOP.map((slug) => toMap(DATA.series[slug]?.[state.res]?.[state.measure]));
    datasets = TOP.map((slug, i) => areaDS(displayName[slug], labels.map((t) => maps[i][t] ?? 0), colorOf(slug), i === 0, surface));
    const other = labels.map((t, li) => {
      const s = maps.reduce((a, mp) => a + (mp[t] || 0), 0);
      return Math.max(0, +((master[li]?.v || 0) - s).toFixed(3));
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
            label: (it) => `${it.dataset.label}: ${fmt(it.parsed.y, 1)} ${unitOf(state.measure)}`,
            footer: (it) => (prelimIdx >= 0 && it[0].dataIndex >= prelimIdx) ? "foreløpige tall" : "",
          },
        },
        prelim: { index: prelimIdx, fill: css("--grid") + "66", text: css("--muted") },
      },
      scales: {
        x: { stacked, grid: { display: false }, ticks: { maxTicksLimit: 13, autoSkip: true, callback(v) { const l = this.getLabelForValue(v); return /^\d{4}-\d{2}$/.test(l) ? l.slice(0, 4) : l; } } },
        y: { stacked, beginAtZero: true, border: { display: false },
          title: { display: true, text: unitOf(state.measure), color: css("--muted") },
          ticks: { callback: (v) => fmt(v, 0) } },
      },
    },
  };
  if (timeChart) timeChart.destroy();
  timeChart = new Chart($("timeChart"), cfg);

  const capParts = {
    total: "Sum for alle felt.",
    stacked: `Bidrag fra de ${TOP.length} største feltene; resten er samlet i «Andre felt».`,
    compare: "Velg felt over grafen for å sammenligne.",
  };
  $("time-cap").innerHTML = capParts[state.view] +
    (prelimIdx >= 0 ? ` <span class="prelim">Skravert område</span> er foreløpige år (overstyres av endelige årstall).` : "");
  $("time-sub").textContent = `– ${MEASURE_LABEL[state.measure].toLowerCase()} (${unitOf(state.measure)})`;
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
    .map((slug) => ({ slug, v: (toMap(DATA.series[slug]?.yearly?.[m])[yr] || 0) }))
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
        tooltip: { callbacks: { label: (it) => `${fmt(it.parsed.x, 1)} ${unitOf(m)}${prelim ? " (foreløpig)" : ""}` } },
        prelim: { index: -1 },
      },
      scales: {
        x: { beginAtZero: true, border: { display: false }, grid: { color: css("--grid") },
          title: { display: true, text: unitOf(m), color: css("--muted") }, ticks: { callback: (v) => fmt(v, 0) } },
        y: { grid: { display: false }, border: { display: false }, ticks: { autoSkip: false, font: { size: 12 } } },
      },
    },
  };
  if (rankChart) rankChart.destroy();
  rankChart = new Chart($("rankChart"), cfg);
  $("year-out").textContent = yr;
  $("rank-year-label").textContent = `– ${MEASURE_LABEL[m].toLowerCase()}, ${yr}`;
  $("rank-cap").innerHTML = `${rows.length} felt i produksjon (${MEASURE_LABEL[m].toLowerCase()}) i ${yr}` +
    (prelim ? ' — <span class="prelim">foreløpige tall</span>.' : ".");
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

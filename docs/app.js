"use strict";

/* ENS production explorer.
 * Loads data/combined.json (built by scripts/update.py) and data/ownership.json
 * (built by scripts/ingest_ownership.py); falls back to labelled synthetic
 * samples. Colours come from CSS custom properties (the validated dataviz
 * reference palette) for the 8 biggest fields/companies; the remaining ones
 * get stable generated hues so any selected subset is distinguishable.
 * Field selection is global and scopes every chart and the tiles.
 *
 * Everything is always expressed as oil+gas combined, in oil-equivalent
 * barrels per day (1000 fat o.e./dag) -- there is no SI-units mode and no
 * single-measure toggle. Water, when shown at all, is an optional extra line
 * in its own native unit (1000 m³/dag) on a secondary axis, since converting
 * produced water to an oil-equivalent has no physical meaning. */

const MEASURE_LABEL = { oil: "Olje", gas: "Gass" };
const OE_UNIT = "1000 fat o.e./dag";
const WATER_UNIT = "1000 m³/dag";
const SLOTS = ["--c1", "--c2", "--c3", "--c4", "--c5", "--c6", "--c7", "--c8"];
const MONTHS_NB = ["jan", "feb", "mar", "apr", "mai", "jun", "jul", "aug", "sep", "okt", "nov", "des"];
const STACK_CAP = 8;             // max individual bands before folding to "Andre valgte"
const UNKNOWN_COMPANY = "Ukjent";

let DATA = null, OWN = null, timeChart = null, rankChart = null;
let RANKED = [];                 // all field slugs, biggest first
let COMPANIES = [];               // all company names, biggest first
let fieldColor = {};              // slug -> { v: cssVarName } or { h: hex/hsl }
let companyColor = {};
let displayName = {};
const state = {
  res: "yearly", view: "total", annualize: true, showWater: false,
  selected: new Set(), year: null,
};

const $ = (id) => document.getElementById(id);
const css = (v) => getComputedStyle(document.body).getPropertyValue(v).trim();
const toMap = (arr) => { const m = {}; (arr || []).forEach((p) => { m[p.t] = p.v; }); return m; };

// -- oil-equivalent + annualization -----------------------------------------
const BOE = 6.29;                                  // barrels per m³ of oil
function daysInPeriod(t) {
  const mm = /^(\d{4})-(\d{2})$/.exec(t);
  if (mm) return new Date(+mm[1], +mm[2], 0).getDate();
  const y = +t;
  if (!isNaN(y)) return ((y % 4 === 0 && y % 100 !== 0) || y % 400 === 0) ? 366 : 365;
  return 365;
}
function monthsPresent(year) {
  return (DATA.series._total?.monthly?.oil || []).filter((p) => p.t.startsWith(year + "-"));
}
function annualFactor(t) {                          // gross an incomplete year up to a full year
  if (!state.annualize || state.res !== "yearly" || !/^\d{4}$/.test(t)) return 1;
  const yp = (DATA.series._total?.yearly?.oil || []).find((p) => p.t === t);
  if (!yp || !yp.p) return 1;
  const months = monthsPresent(t);
  if (months.length === 0 || months.length >= 12) return 1;
  const days = months.reduce((a, p) => a + new Date(+t, +p.t.slice(5, 7), 0).getDate(), 0);
  return days > 0 ? 365 / days : 1;
}
const isAnnualized = (t) => annualFactor(t) !== 1;

// Oil/gas volume (in its native SI unit) -> oil-equivalent barrels per day.
function oeRate(v, t) {
  if (v == null) return null;
  return (v * annualFactor(t) * BOE) / daysInPeriod(t);
}
// Water volume (1000 m³) -> 1000 m³ per day. Not oil-equivalent: water has no
// meaningful barrel-of-oil conversion, so it keeps its native unit.
function waterRate(v, t) {
  if (v == null) return null;
  return (v * annualFactor(t)) / daysInPeriod(t);
}

function fmtVal(v) {
  const a = Math.abs(v), d = a >= 100 ? 0 : a >= 10 ? 1 : 2;
  return new Intl.NumberFormat("nb-NO", { maximumFractionDigits: d }).format(v);
}

// -- selection helpers -------------------------------------------------------
const selectedSlugs = () => RANKED.filter((s) => state.selected.has(s));
const allSelected = () => state.selected.size >= RANKED.length;
const fieldMap = (slug, res, m) => toMap(DATA.series[slug]?.[res]?.[m]);
// Total over the selected fields (falls back to the precomputed _total when all
// are selected). Keeps the master timeline and preliminary flags.
function selTotal(res, m) {
  const base = DATA.series._total?.[res]?.[m] || [];
  if (allSelected()) return base;
  const maps = selectedSlugs().map((s) => fieldMap(s, res, m));
  return base.map((pt) => ({ t: pt.t, p: pt.p, v: maps.reduce((a, mp) => a + (mp[pt.t] || 0), 0) }));
}
// Combined oil+gas oe-rate for one field at one period; null only if both
// measures are missing (no data), never if only one of them is.
function fieldOeAt(t, mpOil, mpGas) {
  const o = mpOil[t], g = mpGas[t];
  if (o == null && g == null) return null;
  return oeRate(o || 0, t) + oeRate(g || 0, t);
}

// --------------------------------------------------------------------------- boot
async function boot() {
  DATA = await load();
  if (!DATA) return;
  try {
    prepare(); buildControls(); applyChartDefaults(); renderAll();
  } catch (e) {
    const eb = $("error-banner");
    if (eb) { eb.textContent = "Kunne ikke bygge visningen: " + e.message; eb.classList.remove("hidden"); }
    throw e;
  }
}

async function load() {
  const real = await tryFetch("data/combined.json");
  const own = await tryFetch("data/ownership.json");
  if (real && real.series && Object.keys(real.series).length) {
    OWN = (own && own.fields) ? own : await tryFetch("data/ownership.sample.json");
    return real;
  }
  const sample = await tryFetch("data/combined.sample.json");
  if (sample && sample.series && Object.keys(sample.series).length) {
    $("sample-banner").classList.remove("hidden");
    OWN = await tryFetch("data/ownership.sample.json");
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

  const fields = DATA.fields.map((f) => f.slug).filter((s) => s !== "_total");
  const allTime = (slug) => ["oil", "gas"].reduce((a, m) =>
    a + (DATA.series[slug]?.yearly?.[m] || []).reduce((b, p) => b + p.v, 0), 0);
  RANKED = fields.slice().sort((a, b) => allTime(b) - allTime(a));
  // Colour: 8 biggest use the validated palette slots; the rest get stable hues.
  fieldColor = {};
  const extra = Math.max(1, RANKED.length - SLOTS.length);
  RANKED.forEach((slug, i) => {
    fieldColor[slug] = i < SLOTS.length
      ? { v: SLOTS[i] }
      : { h: `hsl(${Math.round((360 / extra) * (i - SLOTS.length) + 20) % 360} 55% 52%)` };
  });
  state.selected = new Set(RANKED);                 // all fields selected by default

  // Company ownership matrix (scripts/ingest_ownership.py); missing entries
  // (should not happen for real data) fall back to a single "Ukjent" bucket.
  const own = (OWN && OWN.fields) || {};
  const companyTotal = {};
  fields.forEach((slug) => {
    const shares = own[slug] || { [UNKNOWN_COMPANY]: 1 };
    const at = allTime(slug);
    Object.entries(shares).forEach(([c, s]) => { companyTotal[c] = (companyTotal[c] || 0) + at * s; });
  });
  COMPANIES = Object.keys(companyTotal).sort((a, b) => companyTotal[b] - companyTotal[a]);
  companyColor = {};
  const extraC = Math.max(1, COMPANIES.length - SLOTS.length);
  COMPANIES.forEach((c, i) => {
    companyColor[c] = i < SLOTS.length
      ? { v: SLOTS[i] }
      : { h: `hsl(${Math.round((360 / extraC) * (i - SLOTS.length) + 20) % 360} 55% 52%)` };
  });

  const yearsOil = DATA.series._total?.yearly?.oil || [];
  const finals = yearsOil.filter((p) => !p.p).map((p) => p.t);
  state.year = yearsOil.slice(-1)[0]?.t || (finals.length ? finals[finals.length - 1] : null);

  const el = $("updated");
  if (DATA.last_updated) {
    const d = new Date(DATA.last_updated);
    el.textContent = isNaN(d) ? "Sist oppdatert: " + DATA.last_updated :
      `Sist oppdatert ${d.getUTCDate()}. ${MONTHS_NB[d.getUTCMonth()]}. ${d.getUTCFullYear()}.`;
  }
}

function shareOf(slug, company) {
  const shares = (OWN && OWN.fields && OWN.fields[slug]) || { [UNKNOWN_COMPANY]: 1 };
  return shares[company] || 0;
}
function ownerOf(slug) {
  return (OWN && OWN.fields && OWN.fields[slug]) || { [UNKNOWN_COMPANY]: 1 };
}

// --------------------------------------------------------------------------- controls
function buildControls() {
  segGroup("res-seg", "res", (v) => { state.res = v; renderTime(); });
  segGroup("view-seg", "view", (v) => { state.view = v; renderTime(); });

  const ann = $("annualize");
  if (ann) { ann.checked = state.annualize; ann.addEventListener("change", () => { state.annualize = ann.checked; renderAll(); }); }
  const water = $("show-water");
  if (water) { water.checked = state.showWater; water.addEventListener("change", () => { state.showWater = water.checked; renderTime(); }); }

  buildFieldPicker();

  // year slider (ranking chart)
  const yrs = (DATA.series._total?.yearly?.oil || []).map((p) => p.t);
  const sl = $("year-slider");
  sl.min = 0; sl.max = Math.max(0, yrs.length - 1);
  sl.value = Math.max(0, yrs.indexOf(state.year));
  sl.addEventListener("input", () => { state.year = yrs[+sl.value]; renderRank(); });

  const saved = localStorage.getItem("ens-theme");
  if (saved) document.documentElement.setAttribute("data-theme", saved);
  $("theme-toggle").addEventListener("click", () => {
    const next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
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

function buildFieldPicker() {
  const checks = $("field-checks");
  DATA.fields.filter((f) => f.slug !== "_total").forEach((f) => {
    const lab = document.createElement("label");
    const cb = document.createElement("input");
    cb.type = "checkbox"; cb.value = f.slug; cb.checked = state.selected.has(f.slug);
    cb.addEventListener("change", () => {
      cb.checked ? state.selected.add(f.slug) : state.selected.delete(f.slug);
      if (state.selected.size === 0) { state.selected.add(f.slug); cb.checked = true; }  // keep ≥1
      renderAll(); updateFieldUI();
    });
    const sw = document.createElement("span");
    sw.className = "sw"; sw.style.background = colorOf(f.slug);
    lab.append(cb, sw, document.createTextNode(f.display_name));
    checks.appendChild(lab);
  });
  $("fields-toggle").addEventListener("click", () => {
    const pk = $("field-picker"), open = pk.classList.toggle("hidden");
    $("fields-toggle").setAttribute("aria-expanded", open ? "false" : "true");
  });
  $("fields-all").addEventListener("click", () => setAllFields(true));
  $("fields-none").addEventListener("click", () => setAllFields(false));
  updateFieldUI();
}
function setAllFields(on) {
  state.selected = new Set(on ? RANKED : [RANKED[0]]);   // never empty
  $("field-checks").querySelectorAll("input").forEach((cb) => { cb.checked = state.selected.has(cb.value); });
  renderAll(); updateFieldUI();
}
function updateFieldUI() {
  $("fields-toggle").textContent = "Felt: " + (allSelected() ? `alle (${RANKED.length})` : `${state.selected.size} valgt`);
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
  const yearsOil = DATA.series._total.yearly.oil || [];
  const finals = yearsOil.filter((p) => !p.p);
  const yr = finals.length ? finals[finals.length - 1].t : (yearsOil.slice(-1)[0]?.t || "–");
  const tiles = [];
  for (const m of ["oil", "gas"]) {
    const pt = selTotal("yearly", m).find((p) => p.t === yr);
    tiles.push(`<div class="tile"><div class="label"><span class="dot" style="background:${css("--" + m)}"></span>${MEASURE_LABEL[m]} ${yr}</div>
      <div class="value">${pt ? fmtVal(oeRate(pt.v, yr)) : "–"} <span class="unit">${OE_UNIT}</span></div>
      <div class="foot">${pt && pt.p ? "foreløpig" : "endelige tall"}</div></div>`);
  }
  const producing = selectedSlugs()
    .filter((s) => (DATA.series[s]?.yearly?.oil || []).some((p) => p.t === yr && p.v > 0)).length;
  tiles.push(`<div class="tile"><div class="label">Felt i produksjon ${yr}</div>
    <div class="value">${producing}</div><div class="foot">${allSelected() ? "med oljeproduksjon" : "av valgte felt"}</div></div>`);
  const peak = selTotal("yearly", "oil").map((p) => ({ t: p.t, v: oeRate(p.v, p.t) }))
    .reduce((a, p) => (p.v > a.v ? p : a), { v: -1, t: "–" });
  tiles.push(`<div class="tile"><div class="label">Toppår olje</div>
    <div class="value">${peak.t}</div><div class="foot">${fmtVal(peak.v)} ${OE_UNIT}</div></div>`);
  $("tiles").innerHTML = tiles.join("");
}

const labelFmt = (t) => { const m = /^(\d{4})-(\d{2})$/.exec(t); return m ? `${MONTHS_NB[+m[2] - 1]} ${m[1]}` : t; };

function waterDataset(labels) {
  const tot = selTotal(state.res, "water");
  const map = toMap(tot);
  return {
    label: "Vann", data: labels.map((t) => waterRate(map[t], t)),
    borderColor: css("--water"), backgroundColor: css("--water") + "22", borderDash: [4, 3],
    borderWidth: 2, tension: 0.15, spanGaps: true, pointRadius: 0, pointHoverRadius: 3,
    yAxisID: "y1",
  };
}

function renderTime() {
  const base = DATA.series._total[state.res].oil || [];
  const labels = base.map((p) => p.t);
  const prelimIdx = base.findIndex((p) => p.p);
  const surface = css("--surface");
  const sel = selectedSlugs();
  let datasets = [], stacked = true;

  if (state.view === "total") {                       // oil vs gas, oe/day
    datasets = ["oil", "gas"].map((m, i) => {
      const tot = selTotal(state.res, m);
      return areaDS(MEASURE_LABEL[m], tot.map((p) => oeRate(p.v, p.t)), css("--" + m), i === 0, surface);
    });
  } else if (state.view === "field") {                 // per-field oe/day, stacked
    const shown = sel.length <= STACK_CAP ? sel : sel.slice(0, STACK_CAP);
    const maps = shown.map((s) => [fieldMap(s, state.res, "oil"), fieldMap(s, state.res, "gas")]);
    datasets = shown.map((slug, i) => areaDS(
      displayName[slug],
      labels.map((t) => fieldOeAt(t, maps[i][0], maps[i][1]) ?? 0),
      colorOf(slug), i === 0, surface,
    ));
    if (sel.length > STACK_CAP) {
      const totMap = toMap(selTotal(state.res, "oil"));
      const totGasMap = toMap(selTotal(state.res, "gas"));
      const shownTotals = labels.map((t) =>
        maps.reduce((a, [mo, mg]) => a + (fieldOeAt(t, mo, mg) ?? 0), 0));
      const other = labels.map((t, i2) => {
        const all = oeRate(totMap[t] || 0, t) + oeRate(totGasMap[t] || 0, t);
        return Math.max(0, all - shownTotals[i2]);
      });
      datasets.push(areaDS("Andre valgte", other, css("--c-other"), false, surface));
    }
  } else {                                             // per-company oe/day, stacked
    const companyOf = {};
    sel.forEach((slug) => {
      const mo = fieldMap(slug, state.res, "oil"), mg = fieldMap(slug, state.res, "gas");
      const shares = ownerOf(slug);
      labels.forEach((t) => {
        const v = fieldOeAt(t, mo, mg);
        if (v == null) return;
        Object.entries(shares).forEach(([c, s]) => {
          companyOf[c] = companyOf[c] || {};
          companyOf[c][t] = (companyOf[c][t] || 0) + v * s;
        });
      });
    });
    const shown = COMPANIES.filter((c) => companyOf[c]);
    datasets = shown.map((c, i) => areaDS(c, labels.map((t) => companyOf[c][t] || 0), colorOfCompany(c), i === 0, surface));
  }

  if (state.showWater) datasets.push(waterDataset(labels));

  const cfg = {
    type: "line", data: { labels, datasets },
    options: {
      responsive: true, maintainAspectRatio: false, animation: { duration: 250 },
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { display: true, position: "bottom",
          labels: { boxWidth: 10, boxHeight: 10, usePointStyle: true, pointStyle: "rectRounded", padding: 10 } },
        tooltip: {
          callbacks: {
            title: (it) => labelFmt(it[0].label),
            label: (it) => `${it.dataset.label}: ${fmtVal(it.parsed.y)} ${it.dataset.yAxisID === "y1" ? WATER_UNIT : OE_UNIT}`,
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
          title: { display: true, text: OE_UNIT, color: css("--muted") }, ticks: { callback: (v) => fmtVal(v) } },
        y1: { display: state.showWater, position: "right", beginAtZero: true, grid: { display: false }, border: { display: false },
          title: { display: true, text: WATER_UNIT, color: css("--muted") }, ticks: { callback: (v) => fmtVal(v) } },
      },
    },
  };
  if (timeChart) timeChart.destroy();
  timeChart = new Chart($("timeChart"), cfg);

  const capParts = {
    total: "Totalproduksjon for valgte felt, splittet i olje og gass.",
    field: sel.length > STACK_CAP ? `De ${STACK_CAP} største av ${sel.length} valgte felt; resten er «Andre valgte».` : `Olje + gass per felt (${sel.length}).`,
    company: "Olje + gass fordelt på eierselskap etter lisensandel (scripts/ingest_ownership.py).",
  };
  let cap = capParts[state.view];
  if (prelimIdx >= 0) cap += ` <span class="prelim">Skravert område</span> er foreløpige år (overstyres av endelige årstall).`;
  if (state.res === "yearly" && state.annualize && labels.some(isAnnualized))
    cap += " Siste år er oppjustert til helårsestimat (× 365 ÷ dager med data).";
  $("time-cap").innerHTML = cap;
  const viewLabel = { total: "totalt", field: "per felt", company: "per selskap" }[state.view];
  $("time-sub").textContent = `– ${viewLabel} (${OE_UNIT})`;
}

function colorOf(slug) { const c = fieldColor[slug]; return c ? (c.v ? css(c.v) : c.h) : css("--c-other"); }
function colorOfCompany(c) { const k = companyColor[c]; return k ? (k.v ? css(k.v) : k.h) : css("--c-other"); }

function areaDS(label, data, color, isBottom, surface) {
  return {
    label, data, backgroundColor: color, borderColor: surface, borderWidth: 1.2,
    fill: isBottom ? "origin" : "-1", tension: 0.15, pointRadius: 0, pointHoverRadius: 0,
  };
}

function renderRank() {
  const yr = state.year;
  const rows = selectedSlugs()
    .map((slug) => ({
      slug,
      v: oeRate(fieldMap(slug, "yearly", "oil")[yr] || 0, yr) + oeRate(fieldMap(slug, "yearly", "gas")[yr] || 0, yr),
    }))
    .filter((r) => r.v > 0).sort((a, b) => b.v - a.v);
  const prelim = (DATA.series._total.yearly.oil || []).find((p) => p.t === yr)?.p;

  const cfg = {
    type: "bar",
    data: {
      labels: rows.map((r) => displayName[r.slug]),
      datasets: [{
        label: "Olje + gass", data: rows.map((r) => r.v),
        backgroundColor: rows.map((r) => colorOf(r.slug)),
        borderRadius: 4, borderSkipped: false, barThickness: "flex", maxBarThickness: 26,
      }],
    },
    options: {
      indexAxis: "y", responsive: true, maintainAspectRatio: false, animation: { duration: 250 },
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: (it) => `${fmtVal(it.parsed.x)} ${OE_UNIT}${prelim ? " (foreløpig)" : ""}` } },
        prelim: { index: -1 },
      },
      scales: {
        x: { beginAtZero: true, border: { display: false }, grid: { color: css("--grid") },
          title: { display: true, text: OE_UNIT, color: css("--muted") }, ticks: { callback: (v) => fmtVal(v) } },
        y: { grid: { display: false }, border: { display: false }, ticks: { autoSkip: false, font: { size: 12 } } },
      },
    },
  };
  if (rankChart) rankChart.destroy();
  rankChart = new Chart($("rankChart"), cfg);
  $("year-out").textContent = yr;
  $("rank-year-label").textContent = `– olje + gass, ${yr}`;
  $("rank-cap").innerHTML = `${rows.length} felt i produksjon i ${yr}` +
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

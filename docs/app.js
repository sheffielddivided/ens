"use strict";

/* ENS production explorer.
 * Loads data/combined.json (built by scripts/update.py) and data/ownership.json
 * (built by scripts/ingest_ownership.py); falls back to labelled synthetic
 * samples. Colours come from CSS custom properties (the validated dataviz
 * reference palette) for the 8 biggest fields/companies; the remaining ones
 * get stable generated hues so any selected subset is distinguishable.
 * Field selection is global and scopes every chart and the tiles.
 *
 * Everything is always expressed as oil+gas combined, in mboepd (thousand
 * barrels of oil equivalent per day) -- there is no SI-units mode and no
 * single-measure toggle. Water, when shown at all, is an optional extra line
 * in its own native unit (1000 m³/dag) on a secondary axis, since converting
 * produced water to an oil-equivalent has no physical meaning.
 *
 * A still-open year (fewer than 12 months of monthly data) is never grossed
 * up to a full-year estimate -- since every value is a per-day rate already,
 * an incomplete year just averages over the months actually reported
 * (see daysForRate). */

const MEASURE_LABEL = { oil: "Olje", gas: "Gass" };
const OE_UNIT = "mboepd";
const WATER_UNIT = "1000 m³/dag";
const SLOTS = ["--c1", "--c2", "--c3", "--c4", "--c5", "--c6", "--c7", "--c8"];
const MONTHS_NB = ["jan", "feb", "mar", "apr", "mai", "jun", "jul", "aug", "sep", "okt", "nov", "des"];
const STACK_CAP = 8;             // max individual bands before folding to "Andre valgte"
const UNKNOWN_COMPANY = "Ukjent";

let DATA = null, OWN = null, timeChart = null, lastExport = null;
let RANKED = [];                 // all field slugs, biggest first
let COMPANIES = [];               // all company names, biggest first
let fieldColor = {};              // slug -> { v: cssVarName } or { h: hex/hsl }
let companyColor = {};
let displayName = {};
const state = {
  res: "yearly", view: "total", showWater: false,
  field: null, company: null,        // null = "Alle", else one slug/name
  // First year shown in the chart, kept separately per resolution: yearly
  // defaults to the earliest year with data, monthly to last year (a full
  // multi-decade run of monthly bars is unreadable). Set in prepare().
  startYearByRes: { yearly: null, monthly: null },
};
let YEARS = [];                      // every year with data, ascending

const $ = (id) => document.getElementById(id);
const css = (v) => getComputedStyle(document.body).getPropertyValue(v).trim();
const toMap = (arr) => { const m = {}; (arr || []).forEach((p) => { m[p.t] = p.v; }); return m; };
const periodYear = (t) => +String(t).slice(0, 4);   // "2024" or "2024-07" -> 2024

// -- oil-equivalent, expressed as a per-day rate -----------------------------
const BOE = 6.29;                                  // barrels per m³ of oil
function monthsPresent(year) {
  return (DATA.series._total?.monthly?.oil || []).filter((p) => p.t.startsWith(year + "-"));
}
// Days to divide a period's total by. For a still-open year (preliminary, with
// fewer than 12 months of monthly data yet) this is the days actually covered
// by those months, not the full calendar year -- so the rate shown is the
// average over the months we have data for, never a full-year projection.
function daysForRate(t) {
  const mm = /^(\d{4})-(\d{2})$/.exec(t);
  if (mm) return new Date(+mm[1], +mm[2], 0).getDate();
  const y = +t;
  if (isNaN(y)) return 365;
  if (state.res === "yearly") {
    const yp = (DATA.series._total?.yearly?.oil || []).find((p) => p.t === t);
    if (yp && yp.p) {
      const months = monthsPresent(t);
      if (months.length > 0 && months.length < 12) {
        return months.reduce((a, p) => a + new Date(y, +p.t.slice(5, 7), 0).getDate(), 0);
      }
    }
  }
  return ((y % 4 === 0 && y % 100 !== 0) || y % 400 === 0) ? 366 : 365;
}

// Oil/gas volume (in its native SI unit) -> oil-equivalent barrels per day.
function oeRate(v, t) {
  if (v == null) return null;
  return (v * BOE) / daysForRate(t);
}
// Water volume (1000 m³) -> 1000 m³ per day. Not oil-equivalent: water has no
// meaningful barrel-of-oil conversion, so it keeps its native unit.
function waterRate(v, t) {
  if (v == null) return null;
  return v / daysForRate(t);
}

function fmtVal(v) {
  const a = Math.abs(v), d = a >= 100 ? 0 : a >= 10 ? 1 : 2;
  return new Intl.NumberFormat("nb-NO", { maximumFractionDigits: d }).format(v);
}

// -- selection helpers ------------------------------------------------------
// Field/company selection is single-choice: either "Alle" (null) or one slug.
const selectedSlugs = () => (state.field ? [state.field] : RANKED);
const allSelected = () => state.field === null;
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
  state.field = null;                               // "Alle" by default

  const yearsOil = DATA.series._total.yearly.oil || [];
  YEARS = yearsOil.map((p) => +p.t).sort((a, b) => a - b);
  const firstYear = YEARS[0], lastYear = YEARS[YEARS.length - 1];
  if (firstYear != null) {
    state.startYearByRes.yearly = firstYear;
    // "Last year" relative to the dataset's own timeline (not the wall clock),
    // so it stays sensible for the sample/demo data too. Clamped in case the
    // data ever spans less than two years.
    state.startYearByRes.monthly = Math.max(firstYear, Math.min(lastYear, lastYear - 1));
  }

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
  state.company = null;                              // "Alle" by default
  companyColor = {};
  const extraC = Math.max(1, COMPANIES.length - SLOTS.length);
  COMPANIES.forEach((c, i) => {
    companyColor[c] = i < SLOTS.length
      ? { v: SLOTS[i] }
      : { h: `hsl(${Math.round((360 / extraC) * (i - SLOTS.length) + 20) % 360} 55% 52%)` };
  });

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
  const startYearSel = $("start-year");
  segGroup("res-seg", "res", (v) => {
    state.res = v;
    if (startYearSel) startYearSel.value = String(state.startYearByRes[v]);
    renderTime();
  });
  segGroup("view-seg", "view", (v) => { state.view = v; updateViewControls(); renderTime(); });

  if (startYearSel) {
    startYearSel.innerHTML = YEARS.map((y) => `<option value="${y}">${y}</option>`).join("");
    startYearSel.value = String(state.startYearByRes[state.res]);
    startYearSel.addEventListener("change", () => {
      state.startYearByRes[state.res] = +startYearSel.value;
      renderTime();
    });
  }

  const water = $("show-water");
  if (water) { water.checked = state.showWater; water.addEventListener("change", () => { state.showWater = water.checked; renderTime(); }); }

  const exportBtn = $("export-btn");
  if (exportBtn) exportBtn.addEventListener("click", exportExcel);

  buildFieldPicker();
  buildCompanySelect();
  updateViewControls();

  const saved = localStorage.getItem("ens-theme");
  if (saved) document.documentElement.setAttribute("data-theme", saved);
  $("theme-toggle").addEventListener("click", () => {
    const next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    localStorage.setItem("ens-theme", next);
    applyChartDefaults(); renderAll();
    if (window.refreshMapTheme) window.refreshMapTheme();
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

// Single-choice radio list: "Alle" plus one row per option. `onPick` gets the
// chosen value, or null for "Alle".
function buildRadioPicker(checksEl, radioName, current, options, colorFn, onPick) {
  const addOption = (value, label, color) => {
    const lab = document.createElement("label");
    const rb = document.createElement("input");
    rb.type = "radio"; rb.name = radioName; rb.value = value ?? "";
    rb.checked = current === value;
    rb.addEventListener("change", () => onPick(value));
    lab.appendChild(rb);
    if (color) { const sw = document.createElement("span"); sw.className = "sw"; sw.style.background = color; lab.appendChild(sw); }
    lab.appendChild(document.createTextNode(label));
    checksEl.appendChild(lab);
  };
  addOption(null, "Alle", null);
  options.forEach(([value, label]) => addOption(value, label, colorFn(value)));
}

function buildFieldPicker() {
  const options = DATA.fields.filter((f) => f.slug !== "_total").map((f) => [f.slug, f.display_name]);
  buildRadioPicker($("field-checks"), "field-radio", state.field, options, colorOf, (value) => {
    state.field = value;
    renderAll(); updateFieldUI(); updateWaterVisibility(); updateMapHighlight();
  });
  $("fields-toggle").addEventListener("click", () => {
    const pk = $("field-picker"), open = pk.classList.toggle("hidden");
    $("fields-toggle").setAttribute("aria-expanded", open ? "false" : "true");
  });
  updateFieldUI();
}
function updateFieldUI() {
  $("fields-toggle").textContent = "Felt: " + (state.field ? displayName[state.field] : "alle");
}

function buildCompanySelect() {
  const sel = $("company-select");
  sel.innerHTML = "";
  const addOpt = (value, label) => {
    const opt = document.createElement("option");
    opt.value = value; opt.textContent = label;
    sel.appendChild(opt);
  };
  addOpt("", "Alle");
  COMPANIES.forEach((c) => addOpt(c, c));
  sel.value = state.company || "";
  sel.addEventListener("change", () => {
    state.company = sel.value || null;
    renderTime(); updateMapHighlight();
  });
}

// The field picker only makes sense for "Per felt"; "Per selskap" gets its own
// company dropdown instead, and "Totalt" shows neither (it is always the sum
// over every field). The water toggle is only meaningful when looking at one
// specific field.
function updateViewControls() {
  const isField = state.view === "field", isCompany = state.view === "company";
  $("fields-toggle").classList.toggle("hidden", !isField);
  $("company-label").classList.toggle("hidden", !isCompany);
  if (!isField) { $("field-picker").classList.add("hidden"); $("fields-toggle").setAttribute("aria-expanded", "false"); }
  updateWaterVisibility();
  updateMapHighlight();
}
function waterRelevant() { return state.view === "field" && state.field !== null; }
function updateWaterVisibility() {
  $("water-label").classList.toggle("hidden", !waterRelevant());
}

// Tell the map (docs/map.js) which fields to highlight: the one field being
// looked at in "Per felt", or the field portfolio of the one company being
// looked at in "Per selskap". Null (both "Alle" and "Totalt") clears the
// highlight back to the default production choropleth.
function updateMapHighlight() {
  if (!window.refreshMapHighlight) return;
  let slugs = null;
  if (state.view === "field" && state.field) slugs = [state.field];
  else if (state.view === "company" && state.company) slugs = RANKED.filter((s) => shareOf(s, state.company) > 0);
  window.refreshMapHighlight(slugs);
}

// --------------------------------------------------------------------------- rendering
function applyChartDefaults() {
  if (!window.Chart) return;
  Chart.defaults.font.family = "system-ui, -apple-system, 'Segoe UI', sans-serif";
  Chart.defaults.color = css("--ink-2");
  Chart.defaults.borderColor = css("--grid");
}

function renderAll() { renderTiles(); renderTime(); }

function renderTiles() {
  const yearsOil = DATA.series._total.yearly.oil || [];
  const finals = yearsOil.filter((p) => !p.p);
  const yrFinal = finals.length ? finals[finals.length - 1].t : (yearsOil.slice(-1)[0]?.t || "–");
  const yrCurrent = yearsOil.slice(-1)[0]?.t || yrFinal;    // Olje/Gass-tiles: snitt for inneværende år
  const tiles = [];
  for (const m of ["oil", "gas"]) {
    const pt = selTotal("yearly", m).find((p) => p.t === yrCurrent);
    tiles.push(`<div class="tile"><div class="label"><span class="dot" style="background:${css("--" + m)}"></span>${MEASURE_LABEL[m]} ${yrCurrent}</div>
      <div class="value">${pt ? fmtVal(oeRate(pt.v, yrCurrent)) : "–"} <span class="unit">${OE_UNIT}</span></div>
      <div class="foot">${pt && pt.p ? "foreløpig snitt" : "endelige tall"}</div></div>`);
  }
  const producing = selectedSlugs()
    .filter((s) => (DATA.series[s]?.yearly?.oil || []).some((p) => p.t === yrFinal && p.v > 0)).length;
  tiles.push(`<div class="tile"><div class="label">Felt i produksjon ${yrFinal}</div>
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
    type: "line",   // overlaid on the bar chart (Chart.js mixed chart types)
    label: "Vann", data: labels.map((t) => waterRate(map[t], t)),
    borderColor: css("--water"), backgroundColor: css("--water") + "22", borderDash: [4, 3],
    borderWidth: 2, tension: 0.15, spanGaps: true, pointRadius: 0, pointHoverRadius: 3,
    yAxisID: "y1",
  };
}

function renderTime() {
  const allBase = DATA.series._total[state.res].oil || [];
  const base = allBase.filter((p) => periodYear(p.t) >= state.startYearByRes[state.res]);
  const labels = base.map((p) => p.t);
  const prelimIdx = base.findIndex((p) => p.p);
  const surface = css("--surface");
  const sel = selectedSlugs();
  let datasets = [], stacked = true;

  if (state.view === "total") {                       // oil vs gas, oe/day, always all fields
    datasets = ["oil", "gas"].map((m, i) => {
      const map = toMap(DATA.series._total[state.res][m]);
      return barDS(MEASURE_LABEL[m], labels.map((t) => oeRate(map[t], t)), css("--" + m), i === 0, surface);
    });
  } else if (state.view === "field") {
    if (state.field) {                                 // one field: oil vs gas, like Totalt
      const slug = state.field;
      datasets = ["oil", "gas"].map((m, i) => {
        const map = fieldMap(slug, state.res, m);
        return barDS(MEASURE_LABEL[m], labels.map((t) => oeRate(map[t], t)), css("--" + m), i === 0, surface);
      });
    } else {                                           // Alle: per-field oe/day, stacked
      const shown = sel.length <= STACK_CAP ? sel : sel.slice(0, STACK_CAP);
      const maps = shown.map((s) => [fieldMap(s, state.res, "oil"), fieldMap(s, state.res, "gas")]);
      datasets = shown.map((slug, i) => barDS(
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
        datasets.push(barDS("Andre valgte", other, css("--c-other"), false, surface));
      }
    }
  } else {                                             // per company, or (single company picked) per field
    // Always all fields: the field picker is hidden in this view (see updateViewControls).
    if (state.company) {
      const company = state.company;
      const fieldsWithShare = RANKED.filter((slug) => shareOf(slug, company) > 0);
      const maps = fieldsWithShare.map((s) => [fieldMap(s, state.res, "oil"), fieldMap(s, state.res, "gas")]);
      const valueOf = (i, t) => (fieldOeAt(t, maps[i][0], maps[i][1]) ?? 0) * shareOf(fieldsWithShare[i], company);
      const shown = fieldsWithShare.slice(0, STACK_CAP);
      datasets = shown.map((slug, i) => barDS(
        displayName[slug], labels.map((t) => valueOf(i, t)), colorOf(slug), i === 0, surface,
      ));
      if (fieldsWithShare.length > STACK_CAP) {
        const other = labels.map((t) => {
          const all = fieldsWithShare.reduce((a, _s, i) => a + valueOf(i, t), 0);
          const shownSum = shown.reduce((a, _s, i) => a + valueOf(i, t), 0);
          return Math.max(0, all - shownSum);
        });
        datasets.push(barDS("Andre valgte", other, css("--c-other"), false, surface));
      }
    } else {
      const companyOf = {};
      RANKED.forEach((slug) => {
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
      datasets = shown.map((c, i) => barDS(c, labels.map((t) => companyOf[c][t] || 0), colorOfCompany(c), i === 0, surface));
    }
  }

  const showWaterNow = state.showWater && waterRelevant();
  if (showWaterNow) datasets.push(waterDataset(labels));

  // Snapshot of exactly what is charted, for the Excel export -- kept in sync
  // with the chart on every render rather than recomputed independently.
  lastExport = { labels: labels.slice(), datasets: datasets.map((d) => ({ label: d.label, data: d.data.slice(), yAxisID: d.yAxisID })) };

  const cfg = {
    type: "bar", data: { labels, datasets },
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
              const sum = it.filter((i2) => i2.dataset.yAxisID !== "y1").reduce((a, i2) => a + i2.parsed.y, 0);
              parts.push(`Sum: ${fmtVal(sum)} ${OE_UNIT}`);
              if (prelimIdx >= 0 && it[0].dataIndex >= prelimIdx) parts.push("foreløpige tall");
              return parts.join(" · ");
            },
          },
        },
        prelim: { index: prelimIdx, fill: css("--grid") + "66", text: css("--muted") },
      },
      scales: {
        x: {
          stacked, grid: { display: false },
          // Monthly resolution: keep exactly one tick per calendar year, at
          // that year's first reported month, instead of letting Chart.js
          // autoSkip land on arbitrary months (which repeats the same year
          // label on neighbouring ticks). Yearly resolution needs no such
          // filtering -- every label is already a distinct year.
          afterBuildTicks(axis) {
            if (state.res !== "monthly") return;
            const seen = new Set();
            axis.ticks = axis.ticks.filter((tk) => {
              const y = labels[tk.value]?.slice(0, 4);
              if (!y || seen.has(y)) return false;
              seen.add(y);
              return true;
            });
          },
          ticks: {
            maxTicksLimit: 13, autoSkip: state.res !== "monthly",
            callback(v) { const l = this.getLabelForValue(v); return /^\d{4}-\d{2}$/.test(l) ? l.slice(0, 4) : l; },
          },
        },
        y: { stacked, beginAtZero: true, border: { display: false },
          title: { display: true, text: OE_UNIT, color: css("--muted") }, ticks: { callback: (v) => fmtVal(v) } },
        y1: { display: showWaterNow, position: "right", beginAtZero: true, grid: { display: false }, border: { display: false },
          title: { display: true, text: WATER_UNIT, color: css("--muted") }, ticks: { callback: (v) => fmtVal(v) } },
      },
    },
  };
  if (timeChart) timeChart.destroy();
  timeChart = new Chart($("timeChart"), cfg);

  const capParts = {
    total: "Totalproduksjon for alle felt, splittet i olje og gass.",
    field: sel.length > STACK_CAP ? `De ${STACK_CAP} største av ${sel.length} valgte felt; resten er «Andre valgte».` : `Olje + gass per felt (${sel.length}).`,
    company: state.company
      ? `${state.company}s andel av produksjonen (olje + gass), per felt.`
      : "Olje + gass fordelt på eierselskap etter lisensandel.",
  };
  let cap = capParts[state.view];
  if (prelimIdx >= 0) cap += ` <span class="prelim">Skravert område</span> er foreløpige år (overstyres av endelige årstall); et ufullstendig år vises som snittproduksjon for månedene med data.`;
  $("time-cap").innerHTML = cap;
  const viewLabel = state.view === "company" && state.company
    ? `${state.company}, per felt`
    : { total: "totalt", field: "per felt", company: "per selskap" }[state.view];
  $("time-sub").textContent = `– ${viewLabel} (${OE_UNIT})`;
}

function colorOf(slug) { const c = fieldColor[slug]; return c ? (c.v ? css(c.v) : c.h) : css("--c-other"); }
function colorOfCompany(c) { const k = companyColor[c]; return k ? (k.v ? css(k.v) : k.h) : css("--c-other"); }

function barDS(label, data, color, isBottom, surface) {
  return { label, data, backgroundColor: color, borderColor: surface, borderWidth: 1 };
}

// Exports exactly what renderTime() last drew on the chart (see lastExport),
// one row per period, one column per series, plus a Sum column when there is
// more than one oil/gas series (water excluded from the sum: different unit).
function exportExcel() {
  if (!lastExport || !window.XLSX) return;
  const { labels, datasets } = lastExport;
  const oeCols = datasets.filter((d) => d.yAxisID !== "y1");
  const header = ["Periode", ...datasets.map((d) => `${d.label} (${d.yAxisID === "y1" ? WATER_UNIT : OE_UNIT})`)];
  if (oeCols.length > 1) header.push(`Sum (${OE_UNIT})`);
  const rows = labels.map((t, i) => {
    const row = [labelFmt(t), ...datasets.map((d) => (d.data[i] == null ? null : Math.round(d.data[i] * 1000) / 1000))];
    if (oeCols.length > 1) row.push(Math.round(oeCols.reduce((a, d) => a + (d.data[i] || 0), 0) * 1000) / 1000);
    return row;
  });
  const ws = XLSX.utils.aoa_to_sheet([header, ...rows]);
  const wb = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(wb, ws, "Produksjon");
  XLSX.writeFile(wb, `produksjon_${state.view}_${state.res}.xlsx`);
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

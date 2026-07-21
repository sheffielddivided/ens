"use strict";

// Frontend for the ENS production dataset. Loads data/combined.json (produced
// by scripts/update.py); if that is absent it falls back to a clearly-labelled
// synthetic sample so the page is demonstrable before the pipeline has run.

const SERIES_LABELS = { oil: "Olje", gas: "Gass", water: "Vann" };
const COLORS = { oil: "#0b6e4f", gas: "#b5651d", water: "#1f6feb" };
const MONTHS_NB = ["januar", "februar", "mars", "april", "mai", "juni", "juli",
  "august", "september", "oktober", "november", "desember"];

let DATA = null;
let chart = null;

const $ = (id) => document.getElementById(id);

async function boot() {
  DATA = await loadData();
  if (!DATA) return;
  populateControls();
  attachHandlers();
  render();
  showLastUpdated();
}

async function loadData() {
  // Prefer the real dataset; fall back to the synthetic sample.
  const real = await tryFetch("data/combined.json");
  if (real && real.series && Object.keys(real.series).length) return real;

  const sample = await tryFetch("data/combined.sample.json");
  if (sample && sample.series && Object.keys(sample.series).length) {
    $("sample-banner").classList.remove("hidden");
    return sample;
  }
  const eb = $("error-banner");
  eb.textContent = "Fant ingen data (verken data/combined.json eller " +
    "data/combined.sample.json). Kjør scripts/update.py.";
  eb.classList.remove("hidden");
  return null;
}

async function tryFetch(url) {
  try {
    const r = await fetch(url, { cache: "no-store" });
    if (!r.ok) return null;
    return await r.json();
  } catch (e) {
    return null;
  }
}

function populateControls() {
  const fieldSel = $("field-select");
  DATA.fields.forEach((f) => {
    const o = document.createElement("option");
    o.value = f.slug;
    o.textContent = f.display_name;
    fieldSel.appendChild(o);
  });

  const seriesSel = $("series-select");
  (DATA.measures || ["oil", "gas", "water"]).forEach((m) => {
    const o = document.createElement("option");
    o.value = m;
    const unit = (DATA.unit_definitions || {})[m];
    o.textContent = SERIES_LABELS[m] + (unit ? ` (${unit})` : "");
    seriesSel.appendChild(o);
  });
}

function attachHandlers() {
  $("field-select").addEventListener("change", render);
  $("series-select").addEventListener("change", render);
  document.querySelectorAll('input[name="res"]').forEach((r) =>
    r.addEventListener("change", render));
}

function currentSelection() {
  return {
    field: $("field-select").value,
    measure: $("series-select").value,
    res: document.querySelector('input[name="res"]:checked').value,
  };
}

function seriesPoints(field, res, measure) {
  const f = DATA.series[field];
  if (!f || !f[res]) return [];
  return f[res][measure] || [];
}

function render() {
  const { field, measure, res } = currentSelection();
  const points = seriesPoints(field, res, measure);

  const labels = points.map((p) => p.t);
  const values = points.map((p) => p.v);
  // A segment is dashed when it leads into a preliminary point.
  const prelim = points.map((p) => !!p.p);

  const color = COLORS[measure] || "#0b6e4f";
  const cfg = {
    type: "line",
    data: {
      labels,
      datasets: [{
        label: SERIES_LABELS[measure],
        data: values,
        borderColor: color,
        backgroundColor: color + "22",
        pointRadius: res === "monthly" ? 0 : 3,
        pointHoverRadius: 4,
        borderWidth: 2,
        tension: 0.15,
        spanGaps: true,
        segment: {
          borderDash: (ctx) => (prelim[ctx.p1DataIndex] ? [6, 4] : undefined),
        },
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "nearest", intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            title: (items) => formatT(items[0].label, res),
            label: (item) => {
              const unit = (DATA.unit_definitions || {})[measure] || "";
              const p = prelim[item.dataIndex] ? " (foreløpig)" : "";
              return `${SERIES_LABELS[measure]}: ${fmt(item.parsed.y)} ${unit}${p}`;
            },
          },
        },
      },
      scales: {
        x: { ticks: { maxTicksLimit: 14, autoSkip: true }, grid: { display: false } },
        y: { beginAtZero: true, title: { display: true,
             text: (DATA.unit_definitions || {})[measure] || "" } },
      },
    },
  };

  if (chart) chart.destroy();
  chart = new Chart($("chart"), cfg);
  showSummary(field, measure, res, points);
}

function showSummary(field, measure, res, points) {
  const el = $("summary");
  if (!points.length) {
    el.innerHTML = "Ingen data for dette valget.";
    return;
  }
  const fieldName = DATA.fields.find((f) => f.slug === field)?.display_name || field;
  const unit = (DATA.unit_definitions || {})[measure] || "";
  const last = points[points.length - 1];
  const nPrelim = points.filter((p) => p.p).length;
  el.innerHTML =
    `<strong>${fieldName}</strong> – ${SERIES_LABELS[measure]}: ` +
    `${points.length} datapunkter, siste ${formatT(last.t, res)} = ` +
    `<strong>${fmt(last.v)}</strong> ${unit}.` +
    (nPrelim ? ` ${nPrelim} foreløpige (stiplet).` : "");
}

function showLastUpdated() {
  const el = $("last-updated");
  if (!DATA.last_updated) { el.textContent = ""; return; }
  const d = new Date(DATA.last_updated);
  if (isNaN(d)) { el.textContent = "Sist oppdatert: " + DATA.last_updated; return; }
  el.textContent = "Sist oppdatert: " +
    `${d.getUTCDate()}. ${MONTHS_NB[d.getUTCMonth()]} ${d.getUTCFullYear()} ` +
    `kl. ${String(d.getUTCHours()).padStart(2, "0")}:` +
    `${String(d.getUTCMinutes()).padStart(2, "0")} UTC`;
}

function formatT(t, res) {
  if (res === "monthly" && /^\d{4}-\d{2}$/.test(t)) {
    const [y, m] = t.split("-");
    return `${MONTHS_NB[parseInt(m, 10) - 1]} ${y}`;
  }
  return t;
}

function fmt(v) {
  return new Intl.NumberFormat("nb-NO", { maximumFractionDigits: 1 }).format(v);
}

boot();

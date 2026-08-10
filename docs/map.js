"use strict";

/* ENS field map.
 * Draws the Danish oil & gas field outlines (docs/data/gis/fields.geojson,
 * built by scripts/build_gis.py) on a Leaflet map and colours each field by its
 * cumulative production, read from the same data/combined.json the explorer
 * uses. Falls back to the checked-in *.sample.* files when the real data has
 * not been built yet, and mirrors the explorer's light/dark theme. */

const MEASURES = ["oil", "gas", "water"];
const MEASURE_LABEL = { oil: "Olje", gas: "Gass", water: "Vann" };
const MONTHS_NB = ["jan", "feb", "mar", "apr", "mai", "jun", "jul", "aug", "sep", "okt", "nov", "des"];

const $ = (id) => document.getElementById(id);
const css = (v) => getComputedStyle(document.body).getPropertyValue(v).trim();
const prettyUnit = (u) => (u || "").replace(/Nm3/g, "Nm³").replace(/m3/g, "m³");
const fmt = (v) => {
  if (v == null) return "–";
  const a = Math.abs(v), d = a >= 100 ? 0 : a >= 10 ? 1 : 2;
  return new Intl.NumberFormat("nb-NO", { maximumFractionDigits: d }).format(v);
};

// Danish North Sea; a sensible view before the layer's bounds are known.
const HOME = { center: [55.75, 4.9], zoom: 7 };

const CARTO = {
  light: "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
  dark: "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
};
const TILE_ATTR =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> ' +
  '&copy; <a href="https://carto.com/attributions">CARTO</a>';

const state = { measure: "oil" };
let map = null, tileLayer = null, fieldLayer = null;
let PROD = {};          // slug -> { cum:{oil,gas,water}, latest:{oil,gas,water}, name, operator }
let UNITS = {};         // measure -> unit string
let maxCum = { oil: 0, gas: 0, water: 0 };

// --------------------------------------------------------------------------- //
// Theme (shared with the explorer via the same localStorage key)
// --------------------------------------------------------------------------- //
function currentTheme() {
  const attr = document.documentElement.getAttribute("data-theme");
  if (attr) return attr;
  return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark" : "light";
}
function applyTiles() {
  const url = currentTheme() === "dark" ? CARTO.dark : CARTO.light;
  if (tileLayer) map.removeLayer(tileLayer);
  tileLayer = L.tileLayer(url, { attribution: TILE_ATTR, subdomains: "abcd", maxZoom: 12 });
  tileLayer.addTo(map);
  tileLayer.bringToBack();
}
function initTheme() {
  const saved = localStorage.getItem("ens-theme");
  if (saved) document.documentElement.setAttribute("data-theme", saved);
  $("theme-toggle").addEventListener("click", () => {
    const next = currentTheme() === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    localStorage.setItem("ens-theme", next);
    applyTiles();
    restyle();
    renderLegend();
  });
}

// --------------------------------------------------------------------------- //
// Data loading (real -> sample fallback)
// --------------------------------------------------------------------------- //
async function loadFirst(urls) {
  for (const url of urls) {
    try {
      const r = await fetch(url, { cache: "no-cache" });
      if (r.ok) return { data: await r.json(), url };
    } catch (e) { /* try next */ }
  }
  return null;
}

function indexProduction(combined) {
  UNITS = combined.unit_definitions || {};
  const names = {};
  (combined.fields || []).forEach((f) => { names[f.slug] = f.display_name; });
  const series = combined.series || {};
  maxCum = { oil: 0, gas: 0, water: 0 };
  const out = {};
  for (const slug of Object.keys(series)) {
    if (slug === "_total") continue;
    const rec = { cum: {}, latest: {}, name: names[slug] || slug };
    for (const m of MEASURES) {
      const arr = (series[slug].yearly && series[slug].yearly[m]) || [];
      let cum = 0, last = null;
      for (const p of arr) {
        if (p.v != null) { cum += p.v; last = p; }
      }
      rec.cum[m] = cum;
      rec.latest[m] = last;                 // {t, v} or null
      if (cum > maxCum[m]) maxCum[m] = cum;
    }
    out[slug] = rec;
  }
  return out;
}

// --------------------------------------------------------------------------- //
// Choropleth styling
// --------------------------------------------------------------------------- //
function intensity(slug) {                  // 0..1 for the active measure
  const rec = PROD[slug];
  const max = maxCum[state.measure];
  if (!rec || !max) return null;
  const v = rec.cum[state.measure] || 0;
  return v > 0 ? Math.sqrt(v / max) : 0;    // sqrt: perceptually fairer spread
}
function styleFor(feature) {
  const t = intensity(feature.properties.slug);
  const hasData = t != null && t > 0;
  return {
    color: css("--baseline"),
    weight: 1,
    fillColor: hasData ? css("--seq") : css("--c-other"),
    fillOpacity: hasData ? 0.2 + 0.65 * t : 0.12,
  };
}
function restyle() { if (fieldLayer) fieldLayer.setStyle(styleFor); }

function popupHtml(p) {
  const rec = PROD[p.slug] || {};
  const name = rec.name || p.name || p.slug;
  const rows = MEASURES.map((m) => {
    const last = rec.latest && rec.latest[m];
    const cum = rec.cum && rec.cum[m];
    const u = prettyUnit(UNITS[m] || "");
    const lv = last ? `${fmt(last.v)} <span class="pu">${u}</span> <span class="py">(${last.t})</span>` : "–";
    const cv = cum ? `${fmt(cum)} <span class="pu">${u}</span>` : "–";
    return `<tr><th style="color:var(--${m})">${MEASURE_LABEL[m]}</th><td>${lv}</td><td>${cv}</td></tr>`;
  }).join("");
  const op = p.OPERATOR || rec.operator;
  return (
    `<div class="mp"><h3>${name}</h3>` +
    (op ? `<p class="mp-op">Operatør: ${op}</p>` : "") +
    `<table><thead><tr><th></th><th>Siste år</th><th>Akkumulert</th></tr></thead>` +
    `<tbody>${rows}</tbody></table>` +
    `<p class="mp-foot"><a href="index.html">Åpne i datautforskeren →</a></p></div>`
  );
}

function onEachFeature(feature, layer) {
  layer.bindPopup(popupHtml(feature.properties), { maxWidth: 300 });
  layer.on({
    mouseover: (e) => e.target.setStyle({ weight: 2.5, color: css("--ink-2") }),
    mouseout: (e) => fieldLayer.resetStyle(e.target),
  });
}

// --------------------------------------------------------------------------- //
// Legend + measure toggle
// --------------------------------------------------------------------------- //
function renderLegend() {
  const seq = css("--seq"), none = css("--c-other");
  const max = maxCum[state.measure];
  const u = prettyUnit(UNITS[state.measure] || "");
  $("legend").innerHTML =
    `<div class="lg-title">Akkumulert ${MEASURE_LABEL[state.measure].toLowerCase()} ` +
    `<span class="lg-u">${u}</span></div>` +
    `<div class="lg-gradient">` +
    `<div class="lg-bar" style="background:linear-gradient(90deg, ${withAlpha(seq, 0.2)}, ${seq})"></div>` +
    `<div class="lg-scale"><span>0</span><span>${fmt(max)}</span></div></div>` +
    `<div class="lg-none"><span class="lg-sw" style="background:${none};opacity:.4"></span>Ingen data</div>`;
}
function withAlpha(color, a) {
  // color is a resolved CSS colour (hex or rgb); wrap into rgba via a canvas-free hack.
  if (color.startsWith("#")) {
    const n = color.slice(1);
    const h = n.length === 3 ? n.split("").map((c) => c + c).join("") : n;
    const r = parseInt(h.slice(0, 2), 16), g = parseInt(h.slice(2, 4), 16), b = parseInt(h.slice(4, 6), 16);
    return `rgba(${r},${g},${b},${a})`;
  }
  return color;
}
function initMeasureToggle() {
  $("measure-seg").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-measure]");
    if (!btn) return;
    state.measure = btn.dataset.measure;
    [...e.currentTarget.querySelectorAll("button")].forEach((b) =>
      b.setAttribute("aria-pressed", String(b === btn)));
    restyle();
    renderLegend();
  });
}

// --------------------------------------------------------------------------- //
// Boot
// --------------------------------------------------------------------------- //
async function main() {
  initTheme();
  initMeasureToggle();

  map = L.map("map", { scrollWheelZoom: true, minZoom: 5 }).setView(HOME.center, HOME.zoom);
  applyTiles();

  const combined = await loadFirst(["data/combined.json", "data/combined.sample.json"]);
  if (combined) {
    PROD = indexProduction(combined.data);
    if (/sample/.test(combined.url)) showBanner("combined");
  }

  const geo = await loadFirst(["data/gis/fields.geojson", "data/gis/fields.sample.geojson"]);
  if (!geo) {
    $("map-note").textContent =
      "Fant ingen feltgeometri. Kjør scripts/build_gis.py for å bygge kartlagene.";
    return;
  }
  if (/sample/.test(geo.url)) showBanner("geo");

  fieldLayer = L.geoJSON(geo.data, { style: styleFor, onEachFeature }).addTo(map);
  const b = fieldLayer.getBounds();
  if (b.isValid()) map.fitBounds(b, { padding: [30, 30], maxZoom: 9 });

  const n = geo.data.features.length;
  const updated = geo.data.generated_at ? ` · geometri oppdatert ${geo.data.generated_at.slice(0, 10)}` : "";
  $("map-note").textContent = `${n} felt vist${updated}.`;
  renderLegend();
}

let bannerReasons = new Set();
function showBanner(reason) {
  bannerReasons.add(reason);
  const el = $("sample-banner");
  el.classList.remove("hidden");
  el.innerHTML = "Viser <strong>syntetiske demodata</strong> – reelle kartlag/produksjonstall bygges av oppdateringsjobbene.";
}

main();

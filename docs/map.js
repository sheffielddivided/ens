"use strict";

/* ENS field map.
 * Draws the Danish oil & gas layers built by scripts/build_gis.py on a Leaflet
 * map: field outlines (docs/data/gis/fields.geojson) coloured by cumulative
 * production from the same data/combined.json the explorer uses, an always-on
 * block grid beneath, and toggleable overlays (installations, exploration
 * wells). Falls back to the checked-in *.sample.* files when the real data has
 * not been built yet, and mirrors the explorer's light/dark theme. */

const MEASURES = ["oil", "gas", "water"];
const MEASURE_LABEL = { oil: "Olje", gas: "Gass", water: "Vann" };

const $ = (id) => document.getElementById(id);
const css = (v) => getComputedStyle(document.body).getPropertyValue(v).trim();
const prettyUnit = (u) => (u || "").replace(/Nm3/g, "Nm³").replace(/m3/g, "m³");
const esc = (s) => String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
const fmt = (v) => {
  if (v == null) return "–";
  const a = Math.abs(v), d = a >= 100 ? 0 : a >= 10 ? 1 : 2;
  return new Intl.NumberFormat("nb-NO", { maximumFractionDigits: d }).format(v);
};

const HOME = { center: [55.9, 4.9], zoom: 7 };
const CARTO = {
  light: "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
  dark: "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
};
const TILE_ATTR =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> ' +
  '&copy; <a href="https://carto.com/attributions">CARTO</a>';

const state = { measure: "oil" };
let map = null, tileLayer = null, fieldLayer = null;
let PROD = {}, UNITS = {}, maxCum = { oil: 0, gas: 0, water: 0 };
const restylers = [];   // [() => void] re-applied on theme change

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
    restylers.forEach((fn) => fn());
    renderLegend();
  });
}

// --------------------------------------------------------------------------- //
// Data loading (real -> sample fallback)
// --------------------------------------------------------------------------- //
async function loadJson(url) {
  try {
    const r = await fetch(url, { cache: "no-cache" });
    if (r.ok) return await r.json();
  } catch (e) { /* ignore */ }
  return null;
}
async function loadFirst(urls) {
  for (const url of urls) {
    const data = await loadJson(url);
    if (data) return { data, url };
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
      for (const p of arr) if (p.v != null) { cum += p.v; last = p; }
      rec.cum[m] = cum;
      rec.latest[m] = last;
      if (cum > maxCum[m]) maxCum[m] = cum;
    }
    out[slug] = rec;
  }
  return out;
}

// --------------------------------------------------------------------------- //
// Fields choropleth
// --------------------------------------------------------------------------- //
function intensity(slug) {
  const rec = PROD[slug], max = maxCum[state.measure];
  if (!rec || !max) return null;
  const v = rec.cum[state.measure] || 0;
  return v > 0 ? Math.sqrt(v / max) : 0;
}
function fieldStyle(feature) {
  const t = intensity(feature.properties.slug);
  const hasData = t != null && t > 0;
  return {
    color: css("--baseline"), weight: 1,
    fillColor: hasData ? css("--seq") : css("--c-other"),
    fillOpacity: hasData ? 0.2 + 0.65 * t : 0.12,
  };
}
function fieldPopup(p) {
  const rec = PROD[p.slug] || {};
  const name = rec.name || p.name || p.slug;
  const rows = MEASURES.map((m) => {
    const last = rec.latest && rec.latest[m], cum = rec.cum && rec.cum[m];
    const u = prettyUnit(UNITS[m] || "");
    const lv = last ? `${fmt(last.v)} <span class="pu">${u}</span> <span class="py">(${last.t})</span>` : "–";
    const cv = cum ? `${fmt(cum)} <span class="pu">${u}</span>` : "–";
    return `<tr><th style="color:var(--${m})">${MEASURE_LABEL[m]}</th><td>${lv}</td><td>${cv}</td></tr>`;
  }).join("");
  const op = p.OPERATOR || rec.operator;
  const prod = PROD[p.slug];
  return (
    `<div class="mp"><h3>${esc(name)}</h3>` +
    (op ? `<p class="mp-op">Operatør: ${esc(op)}</p>` : "") +
    (prod
      ? `<table><thead><tr><th></th><th>Siste år</th><th>Akkumulert</th></tr></thead>` +
        `<tbody>${rows}</tbody></table>` +
        `<p class="mp-foot"><a href="index.html">Åpne i datautforskeren →</a></p>`
      : `<p class="mp-op">Ingen produksjonsdata (funn / ikke i produksjon).</p>`) +
    `</div>`
  );
}

function renderLegend() {
  const seq = css("--seq"), none = css("--c-other");
  const max = maxCum[state.measure], u = prettyUnit(UNITS[state.measure] || "");
  $("legend").innerHTML =
    `<div class="lg-title">Akkumulert ${MEASURE_LABEL[state.measure].toLowerCase()} ` +
    `<span class="lg-u">${u}</span></div>` +
    `<div class="lg-gradient">` +
    `<div class="lg-bar" style="background:linear-gradient(90deg, ${withAlpha(seq, 0.2)}, ${seq})"></div>` +
    `<div class="lg-scale"><span>0</span><span>${fmt(max)}</span></div></div>` +
    `<div class="lg-none"><span class="lg-sw" style="background:${none};opacity:.4"></span>Ingen data</div>`;
}
function withAlpha(color, a) {
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
    if (fieldLayer) fieldLayer.setStyle(fieldStyle);
    renderLegend();
  });
}

// --------------------------------------------------------------------------- //
// Overlays (installations, wells) + the always-on block grid
// --------------------------------------------------------------------------- //
function kv(title, sub, rows) {
  const body = rows
    .filter(([, v]) => v != null && v !== "")
    .map(([k, v]) => `<tr><th>${k}</th><td>${esc(v)}</td></tr>`).join("");
  return `<div class="mp"><h3>${esc(title)}</h3>` +
    (sub ? `<p class="mp-op">${esc(sub)}</p>` : "") +
    (body ? `<table class="kv">${body}</table>` : "") + `</div>`;
}

const PROD_COLOR = { Oil: "--oil", Gas: "--gas", Condensate: "--oil", Water: "--water" };

function installationsLayer(data) {
  const style = (f) => {
    const p = f.properties;
    const hue = PROD_COLOR[p.Primary_pr] || "--c3";
    const closed = /clos|abandon|removed/i.test(p.Current_St || "");
    return {
      radius: 5, weight: 1.2, color: css("--page"),
      fillColor: css(hue), fillOpacity: closed ? 0.4 : 0.95,
    };
  };
  const layer = L.geoJSON(data, {
    pointToLayer: (f, ll) => L.circleMarker(ll, style(f)),
    onEachFeature: (f, l) => {
      const p = f.properties;
      l.bindPopup(kv(p.Name || p.ID, [p.Function, p.Category].filter(Boolean).join(" · "), [
        ["Operatør", p.Operator], ["Status", p.Current_St],
        ["Primærprodukt", p.Primary_pr], ["I drift fra", p.Production],
        ["Vanndybde", p.Water_dept != null ? `${p.Water_dept} m` : ""],
        ["Merknad", p.Remarks],
      ]), { maxWidth: 300 });
    },
  });
  restylers.push(() => layer.setStyle(style));
  return layer;
}

function wellsLayer(data) {
  const style = () => ({
    radius: 3, weight: 0.6, color: css("--muted"),
    fillColor: css("--c-other"), fillOpacity: 0.75,
  });
  const layer = L.geoJSON(data, {
    pointToLayer: (f, ll) => L.circleMarker(ll, style()),
    onEachFeature: (f, l) => {
      const p = f.properties;
      const title = [p.Well_Name, p.Well_Numb].filter(Boolean).join(" · ") || "Brønn";
      l.bindPopup(kv(title, p.Classifica, [
        ["Operatør", p.Operator], ["Lisens", p.Licence],
        ["Boret", p.Spud_Date], ["Fullført", p.Comp_Date],
        ["Plassering", p.Location], ["Frigitt", p.Released],
      ]), { maxWidth: 300 });
    },
  });
  restylers.push(() => layer.setStyle(style()));
  return layer;
}

function blocksLayer(data) {
  const style = () => ({ color: css("--baseline"), weight: 0.6, opacity: 0.6, fill: false });
  const layer = L.geoJSON(data, {
    style,
    onEachFeature: (f, l) => l.bindPopup(kv(`Blokk ${f.properties.BlockNo || ""}`, "", []), { maxWidth: 200 }),
  });
  restylers.push(() => layer.setStyle(style()));
  return layer;
}

// Toggleable overlays; "on" = shown by default. Blocks are drawn always (as a
// base grid beneath the fields, see main); licences are omitted because they
// coincide with the field delineations.
const OVERLAYS = [
  ["⬤&nbsp; Installasjoner", "data/gis/installations.geojson", installationsLayer, true],
  ["•&nbsp; Letebrønner", "data/gis/wells.geojson", wellsLayer, false],
];

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
    if (/sample/.test(combined.url)) showBanner();
  }

  const geo = await loadFirst(["data/gis/fields.geojson", "data/gis/fields.sample.geojson"]);
  if (!geo) {
    $("map-note").textContent =
      "Fant ingen feltgeometri. Kjør scripts/build_gis.py for å bygge kartlagene.";
    return;
  }
  if (/sample/.test(geo.url)) showBanner();
  fieldLayer = L.geoJSON(geo.data, {
    style: fieldStyle,
    onEachFeature: (f, l) => {
      l.bindPopup(fieldPopup(f.properties), { maxWidth: 300 });
      l.on({
        mouseover: (e) => e.target.setStyle({ weight: 2.5, color: css("--ink-2") }),
        mouseout: (e) => fieldLayer.resetStyle(e.target),
      });
    },
  }).addTo(map);
  restylers.push(() => fieldLayer.setStyle(fieldStyle));

  const b = fieldLayer.getBounds();
  if (b.isValid()) map.fitBounds(b, { padding: [30, 30], maxZoom: 9 });

  // Block grid: always shown, drawn beneath the fields as a base reference.
  const blocksData = await loadJson("data/gis/blocks.geojson");
  if (blocksData && (blocksData.features || []).length) {
    blocksLayer(blocksData).addTo(map).bringToBack();
  }

  // Optional overlays, each loaded only if its file exists.
  const control = {};
  for (const [name, file, build, on] of OVERLAYS) {
    const data = await loadJson(file);
    if (!data || !(data.features || []).length) continue;
    const layer = build(data);
    control[name] = layer;
    if (on) layer.addTo(map);
  }
  if (Object.keys(control).length) {
    L.control.layers(null, control, { collapsed: false, position: "topright" }).addTo(map);
  }

  const n = geo.data.features.length;
  const upd = geo.data.generated_at ? ` · geometri oppdatert ${geo.data.generated_at.slice(0, 10)}` : "";
  $("map-note").textContent = `${n} felt vist${upd}. Slå lag av/på øverst til høyre.`;
  renderLegend();
}

function showBanner() {
  const el = $("sample-banner");
  el.classList.remove("hidden");
  el.innerHTML = "Viser <strong>syntetiske demodata</strong> – reelle kartlag/produksjonstall bygges av oppdateringsjobbene.";
}

main();

"use strict";

/* ENS field map.
 * Embedded in docs/index.html (next to the production chart): draws the
 * Danish oil & gas field layer built by scripts/build_gis.py on a Leaflet
 * map, one fixed colour for every producing field (from the same
 * data/combined.json app.js uses), with an always-on block grid beneath.
 * Falls back to the checked-in *.sample.* files when the real data has not
 * been built yet.
 *
 * The field-name-label toggle lives in the "Kart" panel head (outside the
 * map itself, see index.html's #show-labels checkbox) rather than as an
 * overlaid Leaflet control. So do the two well toggles: exploration/appraisal
 * wells (#show-exp-wells, on by default) and production/injection/storage
 * wells (#show-dev-wells, off by default -- ~500 points packed around the
 * platforms would bury the field outlines on the overview map). Only offshore
 * wells are drawn; onshore wells stay in the data file but are not shown.
 *
 * Theme (light/dark) is a single shared toggle owned by app.js: this module
 * never binds its own click handler, it just exposes refreshMapTheme() on
 * `window` for app.js to call after it flips the data-theme attribute.
 *
 * Wrapped in an IIFE because it now shares a page (and global script scope)
 * with app.js, which declares its own top-level BOE/$/css/fmt-style helpers. */
(function () {

// Barrels per m³ oil-equivalent. Per the ENS SI-unit convention, gas expressed
// in mio. Nm³ is numerically on the same oil-equivalent (1000 Sm³) scale as
// oil in 1000 m³, so both are summed directly before this factor is applied --
// the same convention docs/app.js uses for its "o.e." series.
const BOE = 6.29;

const $ = (id) => document.getElementById(id);
const css = (v) => getComputedStyle(document.body).getPropertyValue(v).trim();
const esc = (s) => String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
const fmt = (v) => {
  if (v == null) return "–";
  const a = Math.abs(v), d = a >= 100 ? 0 : a >= 10 ? 1 : 2;
  return new Intl.NumberFormat("nb-NO", { maximumFractionDigits: d }).format(v);
};
function daysInMonth(t) {
  const [y, m] = t.split("-").map(Number);
  return new Date(y, m, 0).getDate();
}
function monthLabel(t) {
  const [y, m] = t.split("-").map(Number);
  return new Intl.DateTimeFormat("nb-NO", { month: "short", year: "numeric" }).format(new Date(y, m - 1, 1));
}

const HOME = { center: [55.9, 4.9], zoom: 7 };
// Plain OpenStreetMap tiles: free forever, no API key or account required
// (unlike CARTO's hosted basemaps, which started demanding a key). There is
// no separate free dark tile set, so dark mode reuses these tiles with a CSS
// filter on the tile pane instead (see .map-tiles-dark in style.css).
const OSM_TILE_URL = "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png";
const TILE_ATTR = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';

let map = null, tileLayer = null, fieldLayer = null, labelsLayer = null, fieldsGeoData = null;
let wellGroups = null;   // { exploration: L.LayerGroup, development: L.LayerGroup } or null
let PROD = {}, OWN = null;
let highlightSet = null;   // Set of slugs to emphasise, or null for the default choropleth
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
  if (tileLayer) map.removeLayer(tileLayer);
  tileLayer = L.tileLayer(OSM_TILE_URL, { attribution: TILE_ATTR, subdomains: "abc", maxZoom: 19 });
  tileLayer.addTo(map);
  tileLayer.bringToBack();
  const pane = map.getPane("tilePane");
  if (pane) pane.classList.toggle("map-tiles-dark", currentTheme() === "dark");
}
// Called by app.js's own theme-toggle handler after it flips data-theme.
window.refreshMapTheme = () => {
  if (!map) return;
  applyTiles();
  restylers.forEach((fn) => fn());
  renderLegend();
};

// Called by app.js whenever its field/company selection changes. `slugs` is
// the list of fields to emphasise (the one field picked, or the field
// portfolio of the one company picked), or null/empty to go back to the
// default production choropleth over every field.
window.refreshMapHighlight = (slugs) => {
  highlightSet = slugs && slugs.length ? new Set(slugs) : null;
  if (fieldLayer) fieldLayer.setStyle(fieldStyle);
  if (labelsLayer) rebuildFieldLabels();
  if (Object.keys(PROD).length) renderLegend();
};

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
  const names = {};
  (combined.fields || []).forEach((f) => { names[f.slug] = f.display_name; });
  const series = combined.series || {};
  const out = {};
  for (const slug of Object.keys(series)) {
    if (slug === "_total") continue;
    const oilArr = (series[slug].yearly && series[slug].yearly.oil) || [];
    const gasArr = (series[slug].yearly && series[slug].yearly.gas) || [];
    const byYear = {};   // t -> { oil, gas }
    oilArr.forEach((p) => { if (p.v != null) (byYear[p.t] || (byYear[p.t] = {})).oil = p.v; });
    gasArr.forEach((p) => { if (p.v != null) (byYear[p.t] || (byYear[p.t] = {})).gas = p.v; });
    const years = Object.keys(byYear).sort();

    // Oil and gas are already on the same oil-equivalent scale before the BOE
    // factor is applied (see the module comment), so their raw sums give the
    // mix directly -- no need to convert to barrels just for a ratio.
    let cumOe = 0, cumOilRaw = 0;
    for (const y of years) {
      const o = byYear[y].oil || 0, g = byYear[y].gas || 0;
      cumOilRaw += o;
      cumOe += o + g;
    }
    const oilShare = cumOe > 0 ? cumOilRaw / cumOe : null;   // 1 = all oil, 0 = all gas

    const monthlyOilArr = (series[slug].monthly && series[slug].monthly.oil) || [];
    const monthlyGasArr = (series[slug].monthly && series[slug].monthly.gas) || [];
    const byMonth = {};   // t -> { oil, gas }
    monthlyOilArr.forEach((p) => { if (p.v != null) (byMonth[p.t] || (byMonth[p.t] = {})).oil = p.v; });
    monthlyGasArr.forEach((p) => { if (p.v != null) (byMonth[p.t] || (byMonth[p.t] = {})).gas = p.v; });
    const months = Object.keys(byMonth).sort();

    let lastMonth = null, oeRateMboepd = null, oeAvg12Mboepd = null;
    if (months.length) {
      lastMonth = months[months.length - 1];
      const lastOe = (byMonth[lastMonth].oil || 0) + (byMonth[lastMonth].gas || 0);
      oeRateMboepd = (lastOe * BOE) / daysInMonth(lastMonth);

      const last12 = months.slice(-12);
      let sumOe = 0, sumDays = 0;
      last12.forEach((t) => {
        sumOe += (byMonth[t].oil || 0) + (byMonth[t].gas || 0);
        sumDays += daysInMonth(t);
      });
      oeAvg12Mboepd = (sumOe * BOE) / sumDays;
    }

    out[slug] = {
      name: names[slug] || slug,
      oeCumMBbl: cumOe > 0 ? (cumOe * BOE) / 1000 : 0,          // million barrels
      oeRateMboepd, oeRateMonth: lastMonth, oeAvg12Mboepd,
      oilShare,
    };
  }
  return out;
}

// --------------------------------------------------------------------------- //
// Fields choropleth (one fixed colour for every producing field -- high
// contrast against the basemap's blue water; borders always pure white)
// --------------------------------------------------------------------------- //
const FIELD_BORDER = "#fff";
function fieldStyle(feature) {
  const slug = feature.properties.slug;
  if (highlightSet) {
    const on = highlightSet.has(slug);
    return {
      color: FIELD_BORDER, weight: on ? 2 : 1,
      fillColor: on ? css("--seq") : css("--c-other"),
      fillOpacity: on ? 0.75 : 0.22,
    };
  }
  const rec = PROD[slug];
  const hasData = rec && rec.oeCumMBbl > 0;
  return {
    color: FIELD_BORDER, weight: 1,
    fillColor: hasData ? css("--oil") : css("--c-other"),
    fillOpacity: hasData ? 0.68 : 0.12,
  };
}
// Licence ownership only covers producing fields (see scripts/ingest_ownership.py),
// so a field absent from OWN.fields genuinely has no known split -- the row is
// omitted rather than guessed at.
function ownershipRow(slug) {
  const shares = OWN && OWN.fields && OWN.fields[slug];
  if (!shares) return "";
  const parts = Object.entries(shares)
    .sort((a, b) => b[1] - a[1])
    .map(([company, share]) => `${esc(company)} ${fmt(share * 100)} %`);
  return `<tr><th>Eierskap</th><td>${parts.join(", ")}</td></tr>`;
}

function fieldPopup(p) {
  const rec = PROD[p.slug];
  const name = (rec && rec.name) || p.name || p.slug;
  const op = p.OPERATOR || (rec && rec.operator);
  const opLine = op ? `<p class="mp-op">Operatør: ${esc(op)}</p>` : "";
  const ownRow = ownershipRow(p.slug);
  if (!rec || !rec.oeCumMBbl) {
    return `<div class="mp"><h3>${esc(name)}</h3>${opLine}` +
      `<p class="mp-op">Ingen produksjonsdata (funn / ikke i produksjon).</p>` +
      (ownRow ? `<table class="kv">${ownRow}</table>` : "") +
      `</div>`;
  }
  const lastStr = rec.oeRateMboepd != null
    ? `${fmt(rec.oeRateMboepd)} mboepd (${monthLabel(rec.oeRateMonth)})`
    : "–";
  const avgStr = rec.oeAvg12Mboepd != null ? `${fmt(rec.oeAvg12Mboepd)} mboepd` : "–";
  const cumStr = `${fmt(rec.oeCumMBbl)} mill. fat`;
  const mixStr = rec.oilShare != null ? `${fmt(rec.oilShare * 100)} % olje / ${fmt((1 - rec.oilShare) * 100)} % gass` : "–";
  return (
    `<div class="mp"><h3>${esc(name)}</h3>${opLine}` +
    `<table class="kv">` +
    `<tr><th>Siste måned (o.e.)</th><td>${esc(lastStr)}</td></tr>` +
    `<tr><th>Snitt siste 12 mnd (o.e.)</th><td>${esc(avgStr)}</td></tr>` +
    `<tr><th>Akkumulert (o.e.)</th><td>${esc(cumStr)}</td></tr>` +
    `<tr><th>Olje/gass-miks (akk.)</th><td>${esc(mixStr)}</td></tr>` +
    ownRow +
    `</table></div>`
  );
}

function renderLegend() {
  const seq = css("--seq"), none = css("--c-other");
  if (highlightSet) {
    $("legend").innerHTML =
      `<div class="lg-title">Utvalgte felt</div>` +
      `<div class="lg-none"><span class="lg-sw" style="background:${seq}"></span>Uthevet</div>` +
      `<div class="lg-none"><span class="lg-sw" style="background:${none};opacity:.4"></span>Andre felt</div>` +
      wellLegend();
    return;
  }
  $("legend").innerHTML =
    `<div class="lg-none"><span class="lg-sw" style="background:${css("--oil")}"></span>Felt med produksjon</div>` +
    `<div class="lg-none"><span class="lg-sw" style="background:${none};opacity:.4"></span>Ingen data</div>` +
    wellLegend();
}

// --------------------------------------------------------------------------- //
// Wells (scripts/build_gis.py: ENS "All oil and gas wells" + legacy shapefile)
// --------------------------------------------------------------------------- //
// category -> [toggle group, colour token, legend label]. "other" (geothermal,
// salt research) rides along with the exploration toggle but keeps its own,
// neutral colour.
const WELL_CATS = {
  exploration: ["exploration", "--c7", "Lete-/avgrensningsbrønn"],
  other:       ["exploration", "--c-other", "Annen brønn (geotermi o.l.)"],
  development: ["development", "--c3", "Produksjons-/injeksjonsbrønn"],
};
const wellCat = (p) => WELL_CATS[p.category] || WELL_CATS.other;
const isoToNb = (d) => (d ? d.split("-").reverse().join(".") : null);

function wellStyle(feature) {
  return {
    radius: 3.5, weight: 1, color: css("--surface"), opacity: 0.9,
    fillColor: css(wellCat(feature.properties)[1]), fillOpacity: 0.95,
  };
}

function wellPopup(p) {
  const place = { OFFSHORE: "Til havs", ONSHORE: "På land" }[p.location] || p.location;
  const released = p.released == null ? null : p.released ? "Ja" : "Nei";
  const src = p.source === "shapefile"
    ? "ENS shapefil (2019) – mangler i ENS' brønnoversikt" : null;
  return kv(p.name, p.classification, [
    ["Brønnnummer", p.number], ["Type", p.well_type], ["Lisens", p.licence],
    ["Operatør", p.operator], ["Rigg", p.rig],
    ["Påbegynt", isoToNb(p.spud_date)], ["Avsluttet", isoToNb(p.comp_date)],
    ["Plassering", place], ["Frigitt", released], ["Kilde", src],
  ]);
}

function wellLegend() {
  if (!wellGroups) return "";
  const shown = new Set(Object.keys(wellGroups).filter((g) => map.hasLayer(wellGroups[g])));
  return Object.entries(WELL_CATS)
    .filter(([cat, [group]]) => shown.has(group) && wellCounts[cat])
    .map(([, [, token, label]]) =>
      `<div class="lg-none"><span class="lg-sw lg-dot" style="background:${css(token)}"></span>${label}</div>`)
    .join("");
}

const wellCounts = {};
function buildWells(data) {
  wellGroups = { exploration: L.layerGroup(), development: L.layerGroup() };
  const markers = [];
  (data.features || []).forEach((f) => {
    if (!f.geometry || f.geometry.type !== "Point") return;
    if (f.properties.location !== "OFFSHORE") return;   // offshore wells only
    const [lon, lat] = f.geometry.coordinates;
    const m = L.circleMarker([lat, lon], wellStyle(f));
    m.feature = f;
    m.bindPopup(wellPopup(f.properties), { maxWidth: 280 });
    wellGroups[wellCat(f.properties)[0]].addLayer(m);
    const cat = WELL_CATS[f.properties.category] ? f.properties.category : "other";
    wellCounts[cat] = (wellCounts[cat] || 0) + 1;
    markers.push(m);
  });
  restylers.push(() => markers.forEach((m) => m.setStyle(wellStyle(m.feature))));

  [["exploration", "show-exp-wells", true], ["development", "show-dev-wells", false]]
    .forEach(([group, id, dflt]) => {
      const box = $(id);
      const on = box ? box.checked : dflt;
      if (on) wellGroups[group].addTo(map);
      if (box) {
        box.addEventListener("change", () => {
          if (box.checked) wellGroups[group].addTo(map); else map.removeLayer(wellGroups[group]);
          renderLegend();
        });
      }
    });
}

// --------------------------------------------------------------------------- //
// Block grid (always on) + field-name labels
// --------------------------------------------------------------------------- //
function kv(title, sub, rows) {
  const body = rows
    .filter(([, v]) => v != null && v !== "")
    .map(([k, v]) => `<tr><th>${k}</th><td>${esc(v)}</td></tr>`).join("");
  return `<div class="mp"><h3>${esc(title)}</h3>` +
    (sub ? `<p class="mp-op">${esc(sub)}</p>` : "") +
    (body ? `<table class="kv">${body}</table>` : "") + `</div>`;
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

// Rebuilt (not just re-styled) on every highlight change, since a highlight
// also changes *which* fields get a label, not just their look. A field can
// be split across several polygon parts (e.g. straddling more than one
// licence) that all share the same slug -- group by slug first so each field
// gets exactly one label, placed at the centre of its combined extent.
function rebuildFieldLabels() {
  labelsLayer.clearLayers();
  const bySlug = new Map();
  (fieldsGeoData.features || []).forEach((f) => {
    const slug = f.properties.slug;
    if (highlightSet && !highlightSet.has(slug)) return;
    if (!bySlug.has(slug)) bySlug.set(slug, []);
    bySlug.get(slug).push(f);
  });
  bySlug.forEach((feats, slug) => {
    const name = (PROD[slug] && PROD[slug].name) || feats[0].properties.name || slug;
    let bounds = null;
    feats.forEach((f) => {
      const b = L.geoJSON(f).getBounds();
      bounds = bounds ? bounds.extend(b) : b;
    });
    labelsLayer.addLayer(L.marker(bounds.getCenter(), {
      icon: L.divIcon({ className: "field-label", html: esc(name) }),
      interactive: false, keyboard: false,
    }));
  });
}

// --------------------------------------------------------------------------- //
// Boot
// --------------------------------------------------------------------------- //
async function main() {
  if (!$("map")) return;   // map section not present on this page

  map = L.map("map", { scrollWheelZoom: true, minZoom: 5 }).setView(HOME.center, HOME.zoom);
  applyTiles();
  // The map's height now comes from flexbox (matching the chart panel's
  // height, see .chart-map-row), not a fixed aspect-ratio, so its container
  // can resize after Leaflet has already measured it (e.g. the chart panel's
  // caption changing line count). Keep Leaflet's own size in sync.
  if (window.ResizeObserver) new ResizeObserver(() => map.invalidateSize()).observe($("map"));

  const combined = await loadFirst(["data/combined.json", "data/combined.sample.json"]);
  if (combined) {
    PROD = indexProduction(combined.data);
    if (/sample/.test(combined.url)) showBanner();
  }

  // Fetched before the field layer is built (bindPopup below renders each
  // popup's HTML once, up front, not lazily on click), so ownership must be
  // in hand by then.
  const ownership = await loadFirst(["data/ownership.json", "data/ownership.sample.json"]);
  if (ownership) OWN = ownership.data;

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

  // Field name labels: toggled by the "Feltnavn" checkbox in the panel head
  // (outside the map itself), on by default.
  fieldsGeoData = geo.data;
  labelsLayer = L.layerGroup();
  rebuildFieldLabels();
  const labelsToggle = $("show-labels");
  if (!labelsToggle || labelsToggle.checked) labelsLayer.addTo(map);
  if (labelsToggle) {
    labelsToggle.addEventListener("change", () => {
      if (labelsToggle.checked) labelsLayer.addTo(map); else map.removeLayer(labelsLayer);
    });
  }

  // Wells: drawn last so they sit above the field polygons. Optional layer --
  // the map works without it (e.g. before build_gis.py has been run).
  const wellsData = await loadJson("data/gis/wells.geojson");
  if (wellsData && (wellsData.features || []).length) buildWells(wellsData);

  const n = geo.data.features.length;
  const nWells = Object.values(wellCounts).reduce((a, b) => a + b, 0);
  const wellsStr = nWells ? ` · ${nWells} brønner` : "";
  const upd = geo.data.generated_at ? ` · geometri oppdatert ${geo.data.generated_at.slice(0, 10)}` : "";
  const ownUpd = OWN && OWN.generated_at ? ` · eierskap oppdatert ${OWN.generated_at.slice(0, 10)}` : "";
  $("map-note").textContent = `${n} felt${wellsStr}${upd}${ownUpd}.`;
  renderLegend();
}

function showBanner() {
  const el = $("sample-banner");
  el.classList.remove("hidden");
  el.innerHTML = "Viser <strong>syntetiske demodata</strong> – reelle kartlag/produksjonstall bygges av oppdateringsjobbene.";
}

main();

})();

#!/usr/bin/env python3
"""Convert ENS oil & gas shapefiles into web-ready GeoJSON.

Energistyrelsen publishes map data ("Shape Files on Oil and Gas for Maps",
<https://ens.dk/en/energy-sources/oil-and-gas-related-data/shape-files-oil-and-gas-maps>)
as ESRI shapefiles in **UTM zone 32 N on the ED50 datum** (EPSG:23032). A web
map needs lon/lat on WGS84 (EPSG:4326, the only CRS GeoJSON allows per
RFC 7946), so every layer is reprojected -- a real datum shift, not just a unit
change -- simplified, coordinate-rounded and written as GeoJSON under
``docs/data/gis/`` where the static site can fetch it.

Design notes
------------
* This is deliberately NOT part of the monthly update. Map geometry changes
  rarely, and the dependencies are heavier, so it runs on its own (a manual
  ``build-gis`` workflow, or locally). Hence a separate ``requirements-gis.txt``.
* No network is required to *build*: point ``--raw-dir`` at a directory holding
  the ENS ``.zip``/``.shp`` files (commit them, or let CI download them). The
  shapefile URLs are not hard-coded; pass them explicitly with ``--url``.
* Wells come primarily from ENS's "All oil and gas wells" spreadsheet
  (``*well*.xlsx`` in ``--raw-dir``; ``--fetch-wells`` downloads it), which is
  maintained, unlike the 2019 well shapefile. Wells only the shapefile lists
  are merged in, and every well carries ``category`` and ``source``. Only
  offshore wells are published (``offshore_only``); onshore ones are dropped.
  Each well is tagged with the field whose delineation contains it
  (``assign_well_fields``), so the site can count wells per field.
* Idempotent, like the rest of the pipeline: unchanged geometry produces a
  byte-identical file (the only volatile field, ``generated_at``, is ignored
  when deciding whether anything changed), so re-runs make no git noise.

Run ``python scripts/build_gis.py --help`` for options.
"""

from __future__ import annotations

import argparse
import io
import re
import sys
import zipfile
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent))

import common as C  # noqa: E402

# The datum/projection ENS documents for its map shapefiles. Used only as a
# fallback when a layer ships no ``.prj`` -- a present ``.prj`` is authoritative.
ENS_SOURCE_EPSG = "EPSG:23032"  # ED50 / UTM zone 32 N
WGS84 = "EPSG:4326"

GIS_RAW_DIR = C.SOURCES_DIR / "gis" / "raw"
GIS_OUT_DIR = C.DOCS_DATA_DIR / "gis"

# Default Douglas-Peucker tolerance, in metres of the source projection. Fields
# and licence blocks are tens of kilometres across, so 50 m is invisible on an
# overview map yet shrinks the files a lot.
DEFAULT_SIMPLIFY_M = 50.0
# Coordinate decimal places in the output. 5 dp ~= 1.1 m at this latitude.
DEFAULT_PRECISION = 5

# Attribute names that, across ENS layers, tend to hold the human label. Tried
# in order, case-insensitively; the first non-empty one wins.
NAME_CANDIDATES = (
    "field_name", "fieldname", "field", "felt", "feltnavn",
    "name", "navn", "label", "titel", "title",
    "lic_name", "licence", "license", "licens", "licensname", "block",
    "well_name", "wellname", "brond", "boring",
    "facility", "installati", "installation", "platform", "platform_n",
)

# Attribute columns dropped from every layer: geometry-derived measures ESRI
# writes back into the table, internal ids, and coordinate columns that merely
# duplicate the geometry. Removing them shrinks the point layers and keeps the
# popups readable. Matched case-insensitively.
DROP_FIELDS = {
    "shape_leng", "shape_le_1", "shape_len", "shape_area",
    "fid", "fid_", "objectid", "oid", "f15", "f16",
    "n_degrees", "e_degrees", "n_latitude", "e_longitud", "latitude", "longitude",
}

# Reconciling the field-delineation labels with the production data slugs.
# The delineation layer uses English names and splits some fields across licence
# blocks ("Lulita - 1/90 part"); the production tables use the Danish names. We
# strip the "- ... part" qualifier and alias the few names that genuinely
# differ, so a delineated polygon lines up with the field's production series.
_PART_SUFFIX_RE = re.compile(r"\s*-\s*[^-]*\bpart\b\s*$", re.IGNORECASE)
FIELD_SLUG_ALIASES = {
    "south_arne": "syd_arne",       # South Arne == Syd Arne
    "tyra_southeast": "tyra_se",    # Tyra Southeast == Tyra SE
    "tyra_south_east": "tyra_se",
}

# --- Wells ------------------------------------------------------------------ #
# The ENS well shapefile ("Shape file with exploration and appraisal wells",
# media/4905) has not been refreshed since 2019-08-21 and only covers
# exploration/appraisal wells. The "All oil and gas wells" spreadsheet on the
# Drilling Activities page is maintained and covers every oil/gas well
# (production, injection, gas storage too), so it is the primary well source.
# A handful of non-hydrocarbon wells (salt research, geothermal, some old
# onshore holes) exist only in the shapefile; those are kept, tagged by source.
ENS_WELLS_XLSX_URL = "https://ens.dk/media/8533/download"
WELLS_XLSX_NAME = "All_oil_and_gas_wells.xlsx"
WELLS_SHEET = "WellsDK"
# The spreadsheet gives degrees/minutes/seconds; its Info sheet states the
# co-ordinate system is European Datum 1950 (geographic ED50).
WELLS_XLSX_EPSG = "EPSG:4230"
# Spreadsheet header -> normalised property key. All columns are required.
WELLS_XLSX_COLUMNS = {
    "Well name": "name",
    "Well number": "number",
    "Classification": "classification",
    "Well type": "well_type",
    "Licence": "licence",
    "Operator": "operator",
    "Rig": "rig",
    "N. Latitude": "_lat",
    "E. Longitude": "_lon",
    "Spud date": "spud_date",
    "Comp. date": "comp_date",
    "Location": "location",
    "Released": "released",
}
# Shapefile attribute -> normalised key (dBASE truncates names to 10 chars).
WELLS_SHP_COLUMNS = {
    "well_name": "name", "well_numb": "number", "classifica": "classification",
    "licence": "licence", "operator": "operator", "rig": "rig",
    "spud_date": "spud_date", "comp_date": "comp_date",
    "location": "location", "released": "released",
}
# Plausible extent of Danish wells (North Sea sector + onshore + Bornholm).
WELL_LAT_RANGE = (54.0, 58.5)
WELL_LON_RANGE = (2.5, 15.5)
_DMS_RE = re.compile(
    r"""^\s*(\d{1,3})\s*°\s*(\d{1,2})\s*'\s*(\d{1,2})?\s*(?:[.,]\s*(\d*))?\s*"?\s*$"""
)
_XLSX_DATE_RE = re.compile(r"^\s*(\d{1,2})_(\d{1,2})_(\d{4})\s*$")

# Map a source-file stem to a logical layer (its output filename). Substring
# match, first hit wins; anything unmatched falls back to its geometry type.
ROLE_RULES = (
    ("field", "fields"), ("felt", "fields"), ("delinea", "fields"),
    ("block", "blocks"),
    ("licen", "licences"), ("licens", "licences"),
    ("well", "wells"), ("brond", "wells"), ("boring", "wells"),
    ("install", "installations"), ("platform", "installations"),
    ("facil", "installations"),
    ("pipe", "pipelines"), ("ror", "pipelines"),
)


# --------------------------------------------------------------------------- #
# Dependency loading (kept lazy so tests that don't build can import this file)
# --------------------------------------------------------------------------- #
def _require_gis_libs():
    try:
        import shapefile  # pyshp
        import shapely  # noqa: F401
        from pyproj import CRS, Transformer  # noqa: F401
        from shapely.geometry import mapping, shape  # noqa: F401
        from shapely.ops import transform as shp_transform  # noqa: F401
    except ImportError as exc:  # pragma: no cover - environment guard
        raise C.SourceFormatError(
            f"GIS dependencies missing ({exc}); install them with "
            "`pip install -r requirements-gis.txt`"
        ) from exc
    return sys.modules["shapefile"]


# --------------------------------------------------------------------------- #
# Reading shapefiles (from a directory of .zip and/or bare .shp inputs)
# --------------------------------------------------------------------------- #
class Layer:
    """One shapefile: its stem, source CRS (WKT or None) and pyshp reader."""

    def __init__(self, stem: str, reader, prj_wkt: str | None):
        self.stem = stem
        self.reader = reader
        self.prj_wkt = prj_wkt


def _readers_from_zip(path: Path) -> Iterable[Layer]:
    import shapefile

    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        shp_names = [n for n in names if n.lower().endswith(".shp")]
        if not shp_names:
            raise C.SourceFormatError(f"{path.name}: zip contains no .shp file")
        for shp_name in sorted(shp_names):
            base = shp_name[:-4]
            dbf_name = _member(names, base, ".dbf")
            shx_name = _member(names, base, ".shx")
            prj_name = _member(names, base, ".prj")
            if dbf_name is None:
                raise C.SourceFormatError(f"{path.name}: {base}.dbf missing")
            kw: dict[str, Any] = {
                "shp": io.BytesIO(zf.read(shp_name)),
                "dbf": io.BytesIO(zf.read(dbf_name)),
            }
            if shx_name:
                kw["shx"] = io.BytesIO(zf.read(shx_name))
            prj = zf.read(prj_name).decode("utf-8", "replace") if prj_name else None
            stem = Path(base).name
            yield Layer(stem, shapefile.Reader(**kw), prj)


def _member(names: list[str], base: str, suffix: str) -> str | None:
    """Case-insensitive lookup of ``base + suffix`` among zip member names."""
    want = (base + suffix).lower()
    for n in names:
        if n.lower() == want:
            return n
    return None


def _readers_from_shp(path: Path) -> Iterable[Layer]:
    import shapefile

    prj = path.with_suffix(".prj")
    wkt = prj.read_text(encoding="utf-8", errors="replace") if prj.exists() else None
    yield Layer(path.stem, shapefile.Reader(str(path.with_suffix(""))), wkt)


def discover_layers(raw_dir: Path) -> list[Layer]:
    """Return every shapefile layer found under ``raw_dir`` (zips and .shp)."""
    _require_gis_libs()
    layers: list[Layer] = []
    zips = sorted(raw_dir.glob("*.zip"))
    for z in zips:
        layers.extend(_readers_from_zip(z))
    for shp in sorted(raw_dir.glob("*.shp")):
        layers.extend(_readers_from_shp(shp))
    return layers


# --------------------------------------------------------------------------- #
# Conversion
# --------------------------------------------------------------------------- #
def infer_role(stem: str, geom_type: str) -> str:
    low = stem.lower()
    for needle, role in ROLE_RULES:
        if needle in low:
            return role
    # No name hint: bucket by geometry so the output is still meaningful.
    if "polygon" in geom_type.lower():
        return "areas"
    if "point" in geom_type.lower():
        return "points"
    if "line" in geom_type.lower():
        return "lines"
    return "features"


def _source_crs(wkt: str | None):
    from pyproj import CRS

    if wkt:
        try:
            return CRS.from_wkt(wkt)
        except Exception as exc:  # noqa: BLE001 - fall back with a warning
            C.warn(f"unreadable .prj ({exc}); assuming {ENS_SOURCE_EPSG}")
    else:
        C.warn(f"no .prj; assuming {ENS_SOURCE_EPSG} (ED50 / UTM 32N)")
    return CRS.from_user_input(ENS_SOURCE_EPSG)


def _clean_value(v: Any) -> Any:
    if isinstance(v, bytes):
        v = v.decode("utf-8", "replace")
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, str):
        v = v.strip()
    return v


def _pick_name(props: dict[str, Any]) -> str:
    lower = {k.lower(): v for k, v in props.items()}
    for cand in NAME_CANDIDATES:
        val = lower.get(cand)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def reconcile_field_slug(label: str) -> str:
    """Normalise a delineation label to the same slug as the production data."""
    cleaned = _PART_SUFFIX_RE.sub("", label)
    slug = C.normalize_field(cleaned)
    return FIELD_SLUG_ALIASES.get(slug, slug)


def _round_coords(obj: Any, ndigits: int) -> Any:
    if isinstance(obj, float):
        return round(obj, ndigits)
    if isinstance(obj, dict):
        return {k: _round_coords(v, ndigits) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_round_coords(x, ndigits) for x in obj]
    return obj


def convert_layer(
    layer: Layer,
    *,
    tol_m: float,
    do_simplify: bool,
    precision: int,
    fields_slugs: set[str] | None,
) -> tuple[str, list[dict]]:
    """Reproject + simplify one layer to a list of WGS84 GeoJSON features.

    Returns ``(role, features)``. For the ``fields`` role each feature gets a
    ``slug``/``name`` normalised the same way as the production data, and any
    label that does not match a known field is logged.
    """
    from pyproj import Transformer
    from shapely.geometry import mapping, shape
    from shapely.ops import transform as shp_transform

    reader = layer.reader
    geom_type = reader.shapeTypeName if hasattr(reader, "shapeTypeName") else ""
    role = infer_role(layer.stem, geom_type)

    src_crs = _source_crs(layer.prj_wkt)
    is_geographic = bool(getattr(src_crs, "is_geographic", False))
    to_wgs84 = Transformer.from_crs(src_crs, WGS84, always_xy=True)
    # Simplify in the source units. If the source is already in degrees, convert
    # the metre tolerance to an approximate degree tolerance.
    tol = tol_m / 111_320.0 if is_geographic else tol_m

    fields = [f[0] for f in reader.fields[1:]]  # skip DeletionFlag
    features: list[dict] = []
    unmatched: set[str] = set()

    for sr in reader.iterShapeRecords():
        gi = sr.shape.__geo_interface__
        if not gi or not gi.get("coordinates"):
            continue  # null geometry
        geom = shape(gi)
        if not geom.is_valid:
            geom = geom.buffer(0)  # fix self-intersections cheaply
        if do_simplify and tol > 0:
            geom = geom.simplify(tol, preserve_topology=True)
        if geom.is_empty:
            continue
        geom = shp_transform(lambda x, y, z=None: to_wgs84.transform(x, y), geom)
        gmap = _round_coords(mapping(geom), precision)

        props = {}
        for k, v in zip(fields, sr.record):
            if k.lower() in DROP_FIELDS:
                continue
            cv = _clean_value(v)
            if cv not in (None, ""):
                props[k] = cv

        if role == "fields":
            label = _pick_name(props)
            slug = reconcile_field_slug(label) if label else ""
            props["name"] = label or slug
            props["slug"] = slug
            if slug and fields_slugs is not None and slug not in fields_slugs:
                unmatched.add(f"{label} -> {slug}")

        features.append({"type": "Feature", "properties": props, "geometry": gmap})

    if unmatched:
        C.warn(
            f"{layer.stem}: {len(unmatched)} field label(s) not in fields.json: "
            + ", ".join(sorted(unmatched))
        )
    C.info(f"{layer.stem}: {len(features)} feature(s) -> role '{role}'")
    return role, features


def _feature_sort_key(feat: dict):
    p = feat.get("properties", {})
    return (str(p.get("slug") or p.get("name") or ""),
            str(p.get("id", "")), str(p.get("number", "")))


# --------------------------------------------------------------------------- #
# Wells: ENS spreadsheet (primary) + legacy shapefile (extras only)
# --------------------------------------------------------------------------- #
def well_category(classification: str) -> str:
    """Bucket an ENS spud classification for the map.

    ``production`` / ``injection`` -- production and injection wells,
    *including* mixed classes such as "APPRAISAL AND PRODUCTION" or
    "EXPLORATION AND INJECTION": a well that went on to produce or inject is
    counted as such, whatever it was drilled to test first. (No ENS class
    combines production and injection; production would win if one did.)
    ``exploration`` -- wells drilled only to find or appraise.
    ``other`` -- everything else: observation and gas-storage wells and
    non-hydrocarbon holes (salt research, geothermal, ...).
    """
    c = (classification or "").upper()
    if "PRODUCTION" in c:
        return "production"
    if "INJECTION" in c:
        return "injection"
    if "EXPLORATION" in c or "APPRAISAL" in c:
        return "exploration"
    return "other"


def _well_key(name: str) -> str:
    return re.sub(r"\s+", " ", str(name or "")).strip().upper()


def parse_dms(text: Any, *, what: str = "coordinate") -> float:
    """``55°48'36,5 "`` -> decimal degrees. Decimal comma; seconds optional."""
    m = _DMS_RE.match(str(text or ""))
    if not m:
        raise C.SourceFormatError(f"{what}: cannot parse DMS value {text!r}")
    deg, minutes = int(m.group(1)), int(m.group(2))
    sec = float(f"{m.group(3) or 0}.{m.group(4) or 0}")
    # Exactly 60 seconds does occur (rounding in the source, e.g. TOSTRUP-5's
    # 09°24'60,00"); it is unambiguous (= next minute), so accept it.
    if minutes >= 60 or sec > 60:
        raise C.SourceFormatError(f"{what}: minutes/seconds out of range in {text!r}")
    return deg + minutes / 60 + sec / 3600


def parse_well_date(text: Any, *, what: str = "date") -> str | None:
    """``23_03_1977`` (or a real date cell) -> ``1977-03-23``; blank -> None."""
    if text is None or (isinstance(text, str) and not text.strip()):
        return None
    if isinstance(text, datetime):
        return text.date().isoformat()
    if isinstance(text, date):
        return text.isoformat()
    m = _XLSX_DATE_RE.match(str(text))
    if not m:
        raise C.SourceFormatError(f"{what}: unexpected date format {text!r}")
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat()
    except ValueError as exc:
        raise C.SourceFormatError(f"{what}: invalid date {text!r} ({exc})") from exc


def _parse_released(v: Any) -> bool | None:
    s = str(v or "").strip().lower()
    if s in ("y", "yes", "ja", "j"):
        return True
    if s in ("n", "no", "nej", "nei"):
        return False
    return None


def _clean_well_props(props: dict[str, Any], source: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in props.items():
        if isinstance(v, str):
            v = re.sub(r"\s+", " ", v).strip()
        if v not in (None, ""):
            out[k] = v
    out["category"] = well_category(out.get("classification", ""))
    out["source"] = source
    return out


def read_wells_xlsx(path: Path, *, precision: int) -> list[dict]:
    """Parse the ENS "All oil and gas wells" spreadsheet into WGS84 features.

    Raises ``SourceFormatError`` on a missing sheet/column or any row whose
    coordinates or dates do not parse -- a misplaced well is worse than none.
    """
    import openpyxl
    from pyproj import Transformer

    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 - zip/xml errors, wrong file type
        raise C.SourceFormatError(f"{path.name}: not a readable .xlsx ({exc})") from exc
    try:
        ws = wb[WELLS_SHEET] if WELLS_SHEET in wb.sheetnames else None
        rows_iter = None
        if ws is None:  # tolerate a renamed sheet if the header still matches
            for cand in wb.worksheets:
                it = cand.iter_rows(values_only=True)
                first = next(it, None) or ()
                if "Well name" in [str(c or "").strip() for c in first]:
                    ws, rows_iter, header = cand, it, first
                    break
        if ws is None:
            raise C.SourceFormatError(
                f"{path.name}: no '{WELLS_SHEET}' sheet (sheets: {wb.sheetnames})")
        if rows_iter is None:
            rows_iter = ws.iter_rows(values_only=True)
            header = next(rows_iter, None) or ()
        cols = [str(c or "").strip() for c in header]
        missing = [h for h in WELLS_XLSX_COLUMNS if h not in cols]
        if missing:
            raise C.SourceFormatError(
                f"{path.name}: sheet '{ws.title}' lacks column(s) {missing}; got {cols}")
        idx = {WELLS_XLSX_COLUMNS[h]: cols.index(h) for h in WELLS_XLSX_COLUMNS}
        rows = list(rows_iter)
    finally:
        wb.close()

    to_wgs84 = Transformer.from_crs(WELLS_XLSX_EPSG, WGS84, always_xy=True)
    features: list[dict] = []
    seen: set[str] = set()
    for rowno, row in enumerate(rows, start=2):
        if not row or all(c in (None, "") for c in row):
            continue
        rec = {k: (row[i] if i < len(row) else None) for k, i in idx.items()}
        name = _well_key(rec["name"])
        where = f"{path.name} row {rowno} ({name or '?'})"
        if not name:
            raise C.SourceFormatError(f"{where}: empty well name")
        if name in seen:
            raise C.SourceFormatError(f"{where}: duplicate well name")
        seen.add(name)
        lat = parse_dms(rec.pop("_lat"), what=f"{where} latitude")
        lon = parse_dms(rec.pop("_lon"), what=f"{where} longitude")
        if not (WELL_LAT_RANGE[0] <= lat <= WELL_LAT_RANGE[1]
                and WELL_LON_RANGE[0] <= lon <= WELL_LON_RANGE[1]):
            raise C.SourceFormatError(
                f"{where}: position {lat:.4f}N {lon:.4f}E is outside Denmark")
        rec["spud_date"] = parse_well_date(rec["spud_date"], what=f"{where} spud date")
        rec["comp_date"] = parse_well_date(rec["comp_date"], what=f"{where} completion date")
        rec["released"] = _parse_released(rec["released"])
        x, y = to_wgs84.transform(lon, lat)
        features.append({
            "type": "Feature",
            "properties": _clean_well_props(rec, "xlsx"),
            "geometry": {"type": "Point",
                         "coordinates": [round(x, precision), round(y, precision)]},
        })
    if not features:
        raise C.SourceFormatError(f"{path.name}: no wells found in sheet '{WELLS_SHEET}'")
    C.info(f"{path.name}: {len(features)} well(s) read")
    return features


def normalise_shapefile_wells(features: list[dict]) -> list[dict]:
    """Rename the legacy shapefile's dBASE columns to the shared well schema."""
    out = []
    for f in features:
        src = {k.lower(): v for k, v in f["properties"].items()}
        props = {new: src.get(old) for old, new in WELLS_SHP_COLUMNS.items()}
        props["released"] = _parse_released(props.get("released"))
        out.append({**f, "properties": _clean_well_props(props, "shapefile")})
    return out


def merge_wells(xlsx_wells: list[dict], shp_wells: list[dict]) -> list[dict]:
    """Spreadsheet wells, plus shapefile wells whose name it does not list."""
    have = {_well_key(f["properties"]["name"]) for f in xlsx_wells}
    extras = [f for f in shp_wells if _well_key(f["properties"].get("name")) not in have]
    if extras:
        C.info(
            f"wells: kept {len(extras)} shapefile-only well(s): "
            + ", ".join(sorted(f["properties"].get("name", "?") for f in extras)))
    return xlsx_wells + extras


def offshore_only(wells: list[dict]) -> list[dict]:
    """Keep offshore wells only -- the project covers the Danish North Sea.

    Onshore wells (ENS ``Location`` = ONSHORE; every pre-1966 well among them)
    are dropped from the published layer; the raw ENS inputs keep them. A well
    with no location at all cannot be placed either way, so it is dropped too,
    but named in a warning rather than discarded silently.
    """
    kept, onshore, unknown = [], 0, []
    for f in wells:
        loc = str(f["properties"].get("location") or "").upper()
        if loc == "OFFSHORE":
            kept.append(f)
        elif loc == "ONSHORE":
            onshore += 1
        else:
            unknown.append(f["properties"].get("name", "?"))
    C.info(f"wells: {len(kept)} offshore kept, {onshore} onshore dropped")
    if unknown:
        C.warn(f"wells: dropped {len(unknown)} well(s) with no on-/offshore location: "
               + ", ".join(sorted(unknown)))
    return kept


# Fields that produce but have no delineation polygon in ENS's shapefile, so a
# point-in-polygon test can never place their wells. Their wells are named
# after the field, so they are matched by that explicit name prefix instead
# (never by "nearest field", which would be a guess). Keep this list short and
# exact; every entry is logged when used.
WELL_FIELD_BY_NAME_PREFIX = {
    "RAVN": "ravn",
}


def assign_well_fields(wells: list[dict], field_features: list[dict]) -> list[dict]:
    """Tag each well with the field whose delineation contains its position.

    Adds ``field`` (the production-data slug) and ``field_name`` (a display
    label) to wells that fall inside a field polygon -- or, for the few fields
    without one, match ``WELL_FIELD_BY_NAME_PREFIX``. Wells outside every field
    (mostly dry exploration wells) get no ``field``. The position is the well's
    surface location, i.e. the platform for development wells: a deviated well
    is counted under the field it was drilled from.
    """
    from shapely.geometry import Point, shape
    from shapely.ops import unary_union
    from shapely.prepared import prep

    parts: dict[str, list] = {}
    names: dict[str, str] = {}
    for f in field_features:
        slug = f["properties"].get("slug")
        if not slug:
            continue
        parts.setdefault(slug, []).append(shape(f["geometry"]))
        label = _PART_SUFFIX_RE.sub("", f["properties"].get("name") or "")
        label = re.sub(r"\s*\([^)]*\)", "", label).strip()   # "Halfdan (Igor area)"
        names.setdefault(slug, label or slug)
    polys = {slug: prep(unary_union(g)) for slug, g in sorted(parts.items())}

    out, by_prefix, ambiguous = [], [], []
    for w in wells:
        props = dict(w["properties"])
        props.pop("field", None)
        props.pop("field_name", None)
        pt = Point(w["geometry"]["coordinates"])
        hits = [slug for slug, poly in polys.items() if poly.contains(pt)]
        if len(hits) > 1:
            ambiguous.append(f"{props.get('name')} ({', '.join(hits)})")
        slug = hits[0] if len(hits) == 1 else None
        if slug is None and not hits:
            key = _well_key(props.get("name"))
            for prefix, pslug in WELL_FIELD_BY_NAME_PREFIX.items():
                if key.startswith(prefix):
                    slug = pslug
                    by_prefix.append(props.get("name"))
                    break
        if slug:
            props["field"] = slug
            props["field_name"] = names.get(slug) or slug.replace("_", " ").title()
        out.append({**w, "properties": props})

    placed = sum(1 for w in out if w["properties"].get("field"))
    C.info(f"wells: {placed} placed in a field, {len(out) - placed} outside every field")
    if by_prefix:
        C.info(f"wells: matched by name prefix (field has no polygon): {', '.join(sorted(by_prefix))}")
    if ambiguous:
        # Overlapping delineations would make the count depend on dict order;
        # leave those wells unassigned and say so rather than pick one.
        C.warn(f"wells: {len(ambiguous)} well(s) inside more than one field left unassigned: "
               + "; ".join(sorted(ambiguous)))
    return out


def find_wells_xlsx(raw_dir: Path) -> Path | None:
    hits = sorted(p for p in raw_dir.glob("*.xlsx")
                  if "well" in p.name.lower() and not p.name.startswith("~$"))
    if len(hits) > 1:
        raise C.SourceFormatError(
            f"several well spreadsheets in {raw_dir}: {[p.name for p in hits]}; keep one")
    return hits[0] if hits else None


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def build(
    *,
    raw_dir: Path,
    out_dir: Path,
    tol_m: float,
    do_simplify: bool,
    precision: int,
    suffix: str = "",
) -> list[Path]:
    """Convert every layer under ``raw_dir`` into ``out_dir/<role><suffix>.geojson``."""
    _require_gis_libs()
    fields_slugs = _load_field_slugs()

    layers = discover_layers(raw_dir)
    wells_xlsx = find_wells_xlsx(raw_dir)
    if not layers and wells_xlsx is None:
        C.warn(
            f"no shapefiles under {raw_dir} -- nothing to build. Drop the ENS "
            "zip files there (or fetch with --url) and re-run."
        )
        return []

    collections: dict[str, list[dict]] = {}
    for layer in layers:
        role, feats = convert_layer(
            layer,
            tol_m=tol_m,
            do_simplify=do_simplify,
            precision=precision,
            fields_slugs=fields_slugs,
        )
        collections.setdefault(role, []).extend(feats)

    # Wells always leave in one normalised schema, whichever source(s) exist.
    sources = {}
    if "wells" in collections:
        collections["wells"] = normalise_shapefile_wells(collections["wells"])
    if wells_xlsx is not None:
        collections["wells"] = merge_wells(
            read_wells_xlsx(wells_xlsx, precision=precision),
            collections.get("wells", []),
        )
        sources["wells"] = (
            "Energistyrelsen (ENS): 'All oil and gas wells' spreadsheet "
            f"({wells_xlsx.name}) plus shapefile-only wells from the exploration/"
            "appraisal well shape file; offshore wells only; ED50 reprojected to WGS84"
        )
    if "wells" in collections:
        collections["wells"] = offshore_only(collections["wells"])
        if "fields" in collections:
            collections["wells"] = assign_well_fields(collections["wells"], collections["fields"])
        else:
            C.warn("wells: no field layer in this build -- wells are not tagged with a field")

    written: list[Path] = []
    for role, feats in sorted(collections.items()):
        feats.sort(key=_feature_sort_key)
        fc = {
            "type": "FeatureCollection",
            "generated_at": C.utc_now_iso(),
            "source": sources.get(
                role, "Energistyrelsen (ENS) shape files, reprojected to WGS84"),
            "features": feats,
        }
        name = f"{role}{('.' + suffix) if suffix else ''}.geojson"
        path = out_dir / name
        changed = C.write_json_stable(path, fc, volatile_keys=("generated_at",))
        C.info(f"{'wrote' if changed else 'unchanged'} {path} ({len(feats)} features)")
        written.append(path)

    if fields_slugs is not None and "fields" in collections:
        mapped = {
            f["properties"].get("slug")
            for f in collections["fields"]
            if f["properties"].get("slug")
        }
        missing = sorted(fields_slugs - mapped)
        if missing:
            C.warn(
                f"{len(missing)} field(s) in fields.json have no polygon: "
                + ", ".join(missing)
            )
    return written


def _load_field_slugs() -> set[str] | None:
    data = C.read_json(C.FIELDS_PATH)
    if not data:
        return None
    return set(data.get("fields", {}).keys())


def fetch_urls(urls: list[str], raw_dir: Path, *, force: bool, offline: bool) -> None:
    """Download each URL into ``raw_dir`` using the shared polite fetcher."""
    fetcher = C.Fetcher(cache_dir=raw_dir, offline=offline)
    for url in urls:
        fetcher.fetch(url, C.safe_filename(url) + ".zip", force=force, binary=True)


def fetch_wells_xlsx(raw_dir: Path, *, offline: bool) -> Path:
    """Re-download the ENS well spreadsheet (always fresh: it is updated in place)."""
    fetcher = C.Fetcher(cache_dir=raw_dir, offline=offline)
    body = fetcher.fetch(ENS_WELLS_XLSX_URL, WELLS_XLSX_NAME, force=not offline, binary=True)
    if not body.startswith(b"PK"):
        raise C.SourceFormatError(
            f"{ENS_WELLS_XLSX_URL} did not return an .xlsx (got {body[:20]!r})")
    return raw_dir / WELLS_XLSX_NAME


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--raw-dir", type=Path, default=GIS_RAW_DIR,
                    help=f"directory of ENS .zip/.shp inputs (default: {GIS_RAW_DIR})")
    ap.add_argument("--out-dir", type=Path, default=GIS_OUT_DIR,
                    help=f"where to write GeoJSON (default: {GIS_OUT_DIR})")
    ap.add_argument("--url", action="append", default=[], metavar="URL",
                    help="ENS shapefile zip to download first (repeatable)")
    ap.add_argument("--fetch-wells", action="store_true",
                    help=f"download ENS's 'All oil and gas wells' spreadsheet "
                         f"({ENS_WELLS_XLSX_URL}) into --raw-dir as {WELLS_XLSX_NAME} first")
    ap.add_argument("--force", action="store_true",
                    help="re-download even if the zip is already cached")
    ap.add_argument("--offline", action="store_true",
                    help="never hit the network (build from cached inputs only)")
    ap.add_argument("--simplify", type=float, default=DEFAULT_SIMPLIFY_M,
                    metavar="METRES", help="Douglas-Peucker tolerance in source metres")
    ap.add_argument("--no-simplify", action="store_true",
                    help="keep full-resolution geometry")
    ap.add_argument("--precision", type=int, default=DEFAULT_PRECISION,
                    help="coordinate decimal places (default: %(default)s)")
    ap.add_argument("--suffix", default="",
                    help='insert a label before .geojson, e.g. "sample" -> fields.sample.geojson')
    args = ap.parse_args(argv)

    if args.url:
        fetch_urls(args.url, args.raw_dir, force=args.force, offline=args.offline)
    if args.fetch_wells:
        fetch_wells_xlsx(args.raw_dir, offline=args.offline)

    written = build(
        raw_dir=args.raw_dir,
        out_dir=args.out_dir,
        tol_m=args.simplify,
        do_simplify=not args.no_simplify,
        precision=args.precision,
        suffix=args.suffix,
    )
    return 0 if written else 1


if __name__ == "__main__":
    raise SystemExit(main())

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
  ENS download URLs are not hard-coded because they cannot be verified from the
  build environment; pass them explicitly with ``--url`` to fetch.
* Idempotent, like the rest of the pipeline: unchanged geometry produces a
  byte-identical file (the only volatile field, ``generated_at``, is ignored
  when deciding whether anything changed), so re-runs make no git noise.

Run ``python scripts/build_gis.py --help`` for options.
"""

from __future__ import annotations

import argparse
import io
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

# Map a source-file stem to a logical layer (its output filename). Substring
# match, first hit wins; anything unmatched falls back to its geometry type.
ROLE_RULES = (
    ("field", "fields"), ("felt", "fields"),
    ("licen", "licences"), ("licens", "licences"), ("block", "licences"),
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
            cv = _clean_value(v)
            if cv not in (None, ""):
                props[k] = cv

        if role == "fields":
            label = _pick_name(props)
            slug = C.normalize_field(label) if label else ""
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
    return (str(p.get("slug") or p.get("name") or ""), str(p.get("id", "")))


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
    if not layers:
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

    written: list[Path] = []
    for role, feats in sorted(collections.items()):
        feats.sort(key=_feature_sort_key)
        fc = {
            "type": "FeatureCollection",
            "generated_at": C.utc_now_iso(),
            "source": "Energistyrelsen (ENS) shape files, reprojected to WGS84",
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

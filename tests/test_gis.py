"""Tests for the GIS pipeline (scripts/build_gis.py).

Everything runs offline against the checked-in shapefile fixture
(``tests/fixtures/gis/fields.zip``); the network is never touched. The fixture
is in EPSG:23032 (ED50 / UTM 32 N), so these tests also prove the reprojection
to WGS84 lands the polygons where they belong.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
GIS_FIXTURE_DIR = FIXTURES / "gis"
sys.path.insert(0, str(REPO_ROOT / "scripts"))

# Skip the whole module cleanly if the optional GIS stack is not installed.
pytest.importorskip("shapefile")
pytest.importorskip("pyproj")
pytest.importorskip("shapely")

import build_gis as G  # noqa: E402
import common as C  # noqa: E402


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("gis_out")
    written = G.build(
        raw_dir=GIS_FIXTURE_DIR,
        out_dir=out,
        tol_m=G.DEFAULT_SIMPLIFY_M,
        do_simplify=True,
        precision=G.DEFAULT_PRECISION,
    )
    assert written, "build produced no output"
    fc = json.loads((out / "fields.geojson").read_text(encoding="utf-8"))
    return out, fc


def _by_slug(fc):
    return {f["properties"]["slug"]: f for f in fc["features"]}


# --------------------------------------------------------------------------- #
# Structure
# --------------------------------------------------------------------------- #
def test_output_is_valid_featurecollection(built):
    _, fc = built
    assert fc["type"] == "FeatureCollection"
    assert isinstance(fc["features"], list) and len(fc["features"]) == 5
    for f in fc["features"]:
        assert f["type"] == "Feature"
        assert f["geometry"]["type"] in ("Polygon", "MultiPolygon")
        assert f["properties"].get("slug") is not None


def test_field_names_normalised_to_slugs(built):
    _, fc = built
    slugs = _by_slug(fc)
    # "Halfdan Field" -> "halfdan" (the " Field" suffix is dropped like elsewhere).
    assert "halfdan" in slugs
    assert slugs["halfdan"]["properties"]["name"] == "Halfdan Field"
    assert {"dan", "gorm", "halfdan", "tyra", "nonesuch"} == set(slugs)


def test_features_sorted_by_slug(built):
    _, fc = built
    slugs = [f["properties"]["slug"] for f in fc["features"]]
    assert slugs == sorted(slugs)


def test_original_attributes_preserved(built):
    _, fc = built
    dan = _by_slug(fc)["dan"]["properties"]
    assert dan["OPERATOR"] == "TotalEnergies"


def test_noise_columns_dropped(built):
    _, fc = built
    # Shape_Area / FID are written into the fixture but must not survive.
    for f in fc["features"]:
        keys = {k.lower() for k in f["properties"]}
        assert "shape_area" not in keys
        assert "fid" not in keys


@pytest.mark.parametrize("label,slug", [
    ("Dan", "dan"),
    ("Halfdan (Igor area)", "halfdan"),          # parenthetical area dropped
    ("South Arne - eastern part", "syd_arne"),   # English->Danish alias + "part"
    ("South Arne - western part", "syd_arne"),
    ("Tyra Southeast", "tyra_se"),               # alias
    ("Lulita - 1/90 part", "lulita"),            # licence-part qualifier stripped
    ("Solsort - 4/98 part", "solsort"),
    ("Broder Tuck - 12/06 part", "broder_tuck"), # unmatched but parts collapse
])
def test_reconcile_field_slug(label, slug):
    assert G.reconcile_field_slug(label) == slug


# --------------------------------------------------------------------------- #
# Reprojection: EPSG:23032 -> WGS84 must land in the Danish North Sea.
# --------------------------------------------------------------------------- #
def test_reprojected_to_wgs84_danish_north_sea(built):
    _, fc = built
    for f in fc["features"]:
        for lon, lat in _iter_coords(f["geometry"]):
            assert 3.5 < lon < 7.0, f"lon {lon} out of range"
            assert 55.0 < lat < 57.0, f"lat {lat} out of range"


def test_dan_polygon_near_expected_centre(built):
    _, fc = built
    from shapely.geometry import shape

    c = shape(_by_slug(fc)["dan"]["geometry"]).centroid
    assert c.x == pytest.approx(5.16, abs=0.05)
    assert c.y == pytest.approx(55.48, abs=0.05)


def test_coordinates_rounded_to_precision(built):
    _, fc = built
    for f in fc["features"]:
        for lon, lat in _iter_coords(f["geometry"]):
            assert _decimals(lon) <= G.DEFAULT_PRECISION
            assert _decimals(lat) <= G.DEFAULT_PRECISION


# --------------------------------------------------------------------------- #
# Idempotency: re-running leaves the file byte-for-byte unchanged.
# --------------------------------------------------------------------------- #
def test_build_is_idempotent(tmp_path):
    kw = dict(raw_dir=GIS_FIXTURE_DIR, out_dir=tmp_path,
              tol_m=G.DEFAULT_SIMPLIFY_M, do_simplify=True,
              precision=G.DEFAULT_PRECISION)
    G.build(**kw)
    first = (tmp_path / "fields.geojson").read_bytes()
    G.build(**kw)
    second = (tmp_path / "fields.geojson").read_bytes()
    assert first == second


# --------------------------------------------------------------------------- #
# Graceful handling of an empty input directory.
# --------------------------------------------------------------------------- #
def test_empty_raw_dir_returns_nothing(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert G.build(raw_dir=empty, out_dir=tmp_path,
                   tol_m=G.DEFAULT_SIMPLIFY_M, do_simplify=True,
                   precision=G.DEFAULT_PRECISION) == []


# --------------------------------------------------------------------------- #
# Role inference from filename / geometry.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("stem,geom,role", [
    # the real ENS filenames
    ("FieldDelination_12_12_2025", "POLYGON", "fields"),
    ("Licenses_12_12_2025", "POLYGON", "licences"),
    ("Blocks", "POLYGON", "blocks"),
    ("ExpAppWells_20190821", "POINT", "wells"),
    ("OffshoreInstallations_12_02_2025", "POINT", "installations"),
    # a few generic ones
    ("olie_felter", "POLYGON", "fields"),
    ("pipelines", "POLYLINE", "pipelines"),
    ("mystery", "POLYGON", "areas"),
    ("mystery", "POINT", "points"),
])
def test_infer_role(stem, geom, role):
    assert G.infer_role(stem, geom) == role


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _iter_coords(geom):
    def walk(x):
        if (isinstance(x, list) and len(x) == 2
                and all(isinstance(v, (int, float)) for v in x)):
            yield x[0], x[1]
        elif isinstance(x, list):
            for y in x:
                yield from walk(y)
    yield from walk(geom["coordinates"])


def _decimals(v: float) -> int:
    s = repr(float(v))
    return len(s.split(".")[1]) if "." in s else 0

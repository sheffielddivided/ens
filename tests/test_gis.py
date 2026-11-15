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


# --------------------------------------------------------------------------- #
# Wells: ENS spreadsheet (primary) merged with the legacy shapefile.
# Fixtures: tests/fixtures/gis/wells.xlsx + ExpAppWells_fixture.zip (both
# generated by make_gis_fixture.py; geographic ED50).
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def wells(built):
    out, _ = built
    fc = json.loads((out / "wells.geojson").read_text(encoding="utf-8"))
    return fc, {f["properties"]["name"]: f for f in fc["features"]}


def test_wells_merge_spreadsheet_and_shapefile_extras(wells):
    fc, by = wells
    # 3 offshore spreadsheet wells + the offshore shapefile-only TSEA-3B. The
    # shapefile's ADDA-1 duplicates the spreadsheet; TOSTRUP-5 (spreadsheet),
    # SØNDERBORG-1 and TOSTRUP-1 (shapefile) are onshore and filtered out.
    assert set(by) == {"ADDA-1", "ADDA-3", "SOL-1", "TSEA-3B"}
    assert by["ADDA-1"]["properties"]["source"] == "xlsx"
    assert by["TSEA-3B"]["properties"]["source"] == "shapefile"
    assert "spreadsheet" in fc["source"] and "offshore" in fc["source"]


def test_wells_offshore_only(wells):
    fc, _ = wells
    assert {f["properties"]["location"] for f in fc["features"]} == {"OFFSHORE"}


def test_offshore_only_drops_unknown_location_loudly(capsys):
    feats = [{"properties": {"name": n, "location": loc}}
             for n, loc in (("A", "OFFSHORE"), ("B", "ONSHORE"), ("C", None))]
    assert [f["properties"]["name"] for f in G.offshore_only(feats)] == ["A"]
    assert "WARN" in (out := capsys.readouterr().out) and ": C" in out


def test_wells_normalised_properties(wells):
    _, by = wells
    adda = by["ADDA-1"]["properties"]
    assert adda == {
        "name": "ADDA-1", "number": "5504/8-1", "classification": "EXPLORATION",
        "well_type": "OIL/GAS", "licence": "DUC ENERET 1962 A-SV",
        "operator": "Chevron", "rig": "Mærsk Explorer",
        "spud_date": "1977-03-23", "comp_date": "1977-07-16",
        "location": "OFFSHORE", "released": True,
        "category": "exploration", "source": "xlsx",
    }
    assert by["SOL-1"]["properties"]["released"] is False   # name whitespace stripped
    # Shapefile extras get the same schema; noise columns do not leak through.
    tsea = by["TSEA-3B"]["properties"]
    assert tsea["number"] == "5504/12-14" and tsea["spud_date"] == "1951-05-29"
    assert tsea["released"] is True
    assert not {k for k in tsea if k.lower() in G.DROP_FIELDS}


@pytest.mark.parametrize("name,cat", [
    ("ADDA-1", "exploration"), ("ADDA-3", "exploration"),
    ("SOL-1", "production"), ("TSEA-3B", "production"),   # APPRAISAL AND PRODUCTION
])
def test_wells_category(wells, name, cat):
    assert wells[1][name]["properties"]["category"] == cat


@pytest.mark.parametrize("cls,cat", [
    ("APPRAISAL AND PRODUCTION", "production"),   # mixed: production wins
    ("EXPLORATION AND PRODUCTION", "production"),
    ("APPRAISAL AND INJECTION", "injection"),
    ("EXPLORATION AND INJECTION", "injection"),
    ("PRODUCTION", "production"),
    ("APPRAISAL", "exploration"), ("EXPLORATION", "exploration"),
    ("INJECTION", "injection"), ("GAS STORAGE", "other"),
    ("OBSERVATION", "other"), ("GEOTERMI", "other"), ("", "other"),
])
def test_well_category_rules(cls, cat):
    assert G.well_category(cls) == cat


def test_wells_reprojected_from_ed50(wells):
    from pyproj import Transformer

    lon, lat = wells[1]["ADDA-1"]["geometry"]["coordinates"]
    ed50_lon = 4 + 52 / 60 + 9.4 / 3600
    ed50_lat = 55 + 48 / 60 + 36.5 / 3600
    x, y = Transformer.from_crs("EPSG:4230", "EPSG:4326", always_xy=True).transform(
        ed50_lon, ed50_lat)
    assert (lon, lat) == (round(x, G.DEFAULT_PRECISION), round(y, G.DEFAULT_PRECISION))
    # A real datum shift (~100 m here), not a no-op.
    shift_m = ((lon - ed50_lon) * 62_600) ** 2 + ((lat - ed50_lat) * 111_320) ** 2
    assert shift_m ** 0.5 > 50


@pytest.mark.parametrize("text,value", [
    ("55°48'36,5 \"", 55 + 48 / 60 + 36.5 / 3600),
    ("55°47'50,  \"", 55 + 47 / 60 + 50 / 3600),      # blank decimals
    ("04°50'41,18\"", 4 + 50 / 60 + 41.18 / 3600),
    ("09°24'60,00\"", 9 + 25 / 60),                    # 60 s == next minute
    ("56°05'", 56 + 5 / 60),                           # seconds omitted
])
def test_parse_dms(text, value):
    assert G.parse_dms(text) == pytest.approx(value, abs=1e-9)


@pytest.mark.parametrize("text", ["", None, "55.81", "55°61'00\"", "55°48'61,0\"", "abc"])
def test_parse_dms_rejects_garbage(text):
    with pytest.raises(C.SourceFormatError):
        G.parse_dms(text)


def test_parse_well_date():
    assert G.parse_well_date("23_03_1977") == "1977-03-23"
    assert G.parse_well_date("  ") is None
    for bad in ("1977-03-23", "31_02_1977"):
        with pytest.raises(C.SourceFormatError):
            G.parse_well_date(bad)


def _xlsx(path, header, rows, sheet="WellsDK"):
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet
    ws.append(header)
    for r in rows:
        ws.append(r)
    wb.save(path)
    return path


_HDR = list(G.WELLS_XLSX_COLUMNS)
_ROW = ["X-1", "5504/8-1", "EXPLORATION", "OIL/GAS", "L", "Op", "Rig",
        "55°48'36,5 \"", "04°52'09,4 \"", "23_03_1977", "16_07_1977", "OFFSHORE", "y"]


def test_wells_xlsx_missing_column_fails(tmp_path):
    p = _xlsx(tmp_path / "wells.xlsx", _HDR[:-1], [_ROW[:-1]])
    with pytest.raises(C.SourceFormatError, match="Released"):
        G.read_wells_xlsx(p, precision=5)


def test_wells_xlsx_position_outside_denmark_fails(tmp_path):
    row = _ROW.copy()
    row[7] = "45°00'00,0\""
    p = _xlsx(tmp_path / "wells.xlsx", _HDR, [row])
    with pytest.raises(C.SourceFormatError, match="outside Denmark"):
        G.read_wells_xlsx(p, precision=5)


def test_wells_xlsx_duplicate_name_fails(tmp_path):
    p = _xlsx(tmp_path / "wells.xlsx", _HDR, [_ROW, _ROW])
    with pytest.raises(C.SourceFormatError, match="duplicate"):
        G.read_wells_xlsx(p, precision=5)


def test_wells_xlsx_renamed_sheet_still_found(tmp_path):
    p = _xlsx(tmp_path / "wells.xlsx", _HDR, [_ROW], sheet="Ark1")
    assert len(G.read_wells_xlsx(p, precision=5)) == 1


def test_wells_without_spreadsheet_use_shapefile_schema(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "ExpAppWells_fixture.zip").write_bytes(
        (GIS_FIXTURE_DIR / "ExpAppWells_fixture.zip").read_bytes())
    G.build(raw_dir=raw, out_dir=tmp_path, tol_m=G.DEFAULT_SIMPLIFY_M,
            do_simplify=True, precision=G.DEFAULT_PRECISION)
    fc = json.loads((tmp_path / "wells.geojson").read_text(encoding="utf-8"))
    assert len(fc["features"]) == 2   # the 2 offshore of 4 shapefile wells
    assert {f["properties"]["source"] for f in fc["features"]} == {"shapefile"}
    assert all("category" in f["properties"] for f in fc["features"])


def test_wells_build_is_idempotent(tmp_path):
    kw = dict(raw_dir=GIS_FIXTURE_DIR, out_dir=tmp_path,
              tol_m=G.DEFAULT_SIMPLIFY_M, do_simplify=True,
              precision=G.DEFAULT_PRECISION)
    G.build(**kw)
    first = (tmp_path / "wells.geojson").read_bytes()
    G.build(**kw)
    assert (tmp_path / "wells.geojson").read_bytes() == first


# --------------------------------------------------------------------------- #
# Wells -> fields (point in delineation polygon, plus the explicit name-prefix
# fallback for producing fields that have no polygon).
# --------------------------------------------------------------------------- #
def _sq(slug, name, x0, y0, d=0.1):
    ring = [[x0, y0], [x0 + d, y0], [x0 + d, y0 + d], [x0, y0 + d], [x0, y0]]
    return {"type": "Feature", "properties": {"slug": slug, "name": name},
            "geometry": {"type": "Polygon", "coordinates": [ring]}}


def _well(name, x, y, **extra):
    return {"type": "Feature", "properties": {"name": name, **extra},
            "geometry": {"type": "Point", "coordinates": [x, y]}}


def test_assign_well_fields():
    fields = [
        _sq("dan", "Dan", 5.0, 55.0),
        _sq("halfdan", "Halfdan (Igor area)", 5.2, 55.0),
        _sq("lulita", "Lulita - 1/90 part", 4.0, 56.0),        # split field:
        _sq("lulita", "Lulita - 2/90 part", 4.1, 56.0),        # two parts, one slug
    ]
    wells = [
        _well("MFF-1", 5.05, 55.05),
        _well("HBA-1", 5.25, 55.05, field="stale"),            # stale tag replaced
        _well("LULITA-2", 4.15, 56.05),
        _well("DRY-1", 6.0, 57.0),                             # outside every field
        _well("RAVN A-1", 6.5, 56.5),                          # no polygon: by name
    ]
    by = {w["properties"]["name"]: w["properties"] for w in G.assign_well_fields(wells, fields)}
    assert by["MFF-1"]["field"] == "dan" and by["MFF-1"]["field_name"] == "Dan"
    assert by["HBA-1"]["field"] == "halfdan" and by["HBA-1"]["field_name"] == "Halfdan"
    assert by["LULITA-2"]["field"] == "lulita" and by["LULITA-2"]["field_name"] == "Lulita"
    assert "field" not in by["DRY-1"] and "field_name" not in by["DRY-1"]
    assert by["RAVN A-1"]["field"] == "ravn"


def test_assign_well_fields_overlap_left_unassigned(capsys):
    fields = [_sq("a", "A", 5.0, 55.0), _sq("b", "B", 5.05, 55.0)]
    out = G.assign_well_fields([_well("X-1", 5.07, 55.05)], fields)
    assert "field" not in out[0]["properties"]
    assert "more than one field" in capsys.readouterr().out


def test_wells_build_does_not_invent_fields(wells):
    # The fixture's wells sit outside the fixture's 6 km field squares, so the
    # build must run the tagging without inventing a field for any of them.
    fc, _ = wells
    assert all("field" not in f["properties"] for f in fc["features"])

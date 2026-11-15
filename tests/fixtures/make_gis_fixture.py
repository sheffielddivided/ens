#!/usr/bin/env python3
"""Generate a synthetic ENS-style field shapefile fixture (a zip).

The GIS tests must run offline against a small, checked-in shapefile that looks
like the real thing: polygons in **EPSG:23032 (ED50 / UTM 32 N)** with a
``FIELD_NAME`` attribute and a matching ``.prj``. To keep the sample map looking
plausible, the polygons are placed at the real-ish lon/lat of a few Danish North
Sea fields and back-projected into the source CRS, so a correct reprojection
lands them where they belong.

It also writes a tiny well spreadsheet shaped like ENS's "All oil and gas
wells" file (``wells.xlsx``: DMS coordinates with decimal commas, ``dd_mm_yyyy``
dates, ``y``/``n`` release flags) and a legacy-style well shapefile
(``ExpAppWells_fixture.zip``, geographic ED50) that overlaps it on one well, so
the merge of the two sources can be tested.

Run from the repo root:  ``python tests/fixtures/make_gis_fixture.py``
Regenerates ``tests/fixtures/gis/fields.zip``, ``wells.xlsx`` and
``ExpAppWells_fixture.zip``.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import datetime

import openpyxl
import shapefile  # pyshp
from pyproj import Transformer

HERE = Path(__file__).resolve().parent
OUT_DIR = HERE / "gis"
ZIP_PATH = OUT_DIR / "fields.zip"

# ED50 / UTM zone 32 N -- what ENS documents for its map shapefiles.
PRJ_WKT = (
    'PROJCS["ED50 / UTM zone 32N",GEOGCS["ED50",'
    'DATUM["European_Datum_1950",SPHEROID["International 1924",6378388,297]],'
    'PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]],'
    'PROJECTION["Transverse_Mercator"],PARAMETER["latitude_of_origin",0],'
    'PARAMETER["central_meridian",9],PARAMETER["scale_factor",0.9996],'
    'PARAMETER["false_easting",500000],PARAMETER["false_northing",0],'
    'UNIT["metre",1],AUTHORITY["EPSG","23032"]]'
)

# (label, operator, centre lon, centre lat). Labels are chosen to normalise to
# slugs present in data/fields.json ("dan", "gorm", "halfdan", "tyra"); one
# extra ("Nonesuch") exercises the unmatched-label warning path.
FIELDS = [
    ("Dan", "TotalEnergies", 5.16, 55.48),
    ("Gorm", "TotalEnergies", 5.10, 55.58),
    ("Halfdan Field", "TotalEnergies", 5.30, 55.53),
    ("Tyra", "TotalEnergies", 4.80, 55.72),
    ("Nonesuch", "N/A", 4.55, 56.05),
]

# ~6 km square (half-side in metres) around each centre in the source CRS.
HALF = 3000.0


def _square(cx: float, cy: float) -> list[list[float]]:
    # Clockwise vertex order: in the shapefile format an exterior ring is
    # clockwise (a counter-clockwise ring would be read as an interior hole).
    return [
        [cx - HALF, cy - HALF],
        [cx - HALF, cy + HALF],
        [cx + HALF, cy + HALF],
        [cx + HALF, cy - HALF],
        [cx - HALF, cy - HALF],  # closing point
    ]


# Geographic ED50 -- what ENS's well shapefile ships in its .prj.
PRJ_WKT_ED50_GEO = (
    'GEOGCS["GCS_European_1950",DATUM["D_European_1950",'
    'SPHEROID["International_1924",6378388.0,297.0]],'
    'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]]'
)

WELLS_HEADER = ["Well name", "Well number", "Classification", "Well type",
                "Licence", "Operator", "Rig", "N. Latitude", "E. Longitude",
                "Spud date", "Comp. date", "Location", "Released"]
# Coordinate strings copied in style from the real file, quirks included:
# blank decimal seconds ("50,  "), trailing blanks in names/locations, and a
# 60,00-second value that means "the next minute".
WELLS_ROWS = [
    ["ADDA-1", "5504/8-1", "EXPLORATION", "OIL/GAS", "DUC ENERET 1962 A-SV",
     "Chevron", "Mærsk Explorer", "55°48'36,5 \"", "04°52'09,4 \"",
     "23_03_1977", "16_07_1977", "OFFSHORE  ", "y"],
    ["ADDA-3", "5504/8-3", "APPRAISAL", "OIL/GAS", "DUC ENERET 1962 A-SV",
     "Chevron", "Dan Earl", "55°47'50,  \"", "04°53'26,  \"",
     "31_08_1984", "25_10_1984", "OFFSHORE  ", "y"],
    ["SOL-1  ", "5604/29-0", "PRODUCTION", "OIL/GAS", "3/09",
     "INEOS E&P A/S", "Noble Resolve", "56°05'44,46\"", "04°13'09,68\"",
     "13_07_2023", "02_03_2024", "OFFSHORE", "n"],
    ["TOSTRUP-5", "5609/10-5", "GAS STORAGE", "GAS STORAGE",
     "GAS STORAGE INVESTIGATION", "DONG", "Ideco Bir-800",
     "56°38'30,00\"", "09°24'60,00\"", "21_04_1980", "19_10_1980", "ONSHORE   ", "n"],
]
# Legacy shapefile wells: ADDA-1 duplicates the spreadsheet (must be dropped),
# the others exist only here. TSEA-3B is offshore (kept, tagged
# source=shapefile); the two onshore ones are dropped by the offshore filter.
SHP_WELLS = [
    ("ADDA-1", "5504/08-01", "EXPLORATION", 4.8700, 55.8105, "YES", "OFFSHORE"),
    ("TSEA-3B", "5504/12-14", "APPRAISAL AND PRODUCTION", 4.9200, 55.7200, "YES", "OFFSHORE"),
    ("SØNDERBORG-1", "5409/04-01", "GEOTERMI", 9.8400, 54.9295, "YES", "ONSHORE"),
    ("TOSTRUP-1", "5609/10-01", "SALT RESEARCH", 9.4100, 56.6500, None, "ONSHORE"),
]


def write_wells_xlsx(path: Path) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "WellsDK"
    ws.append(WELLS_HEADER)
    for row in WELLS_ROWS:
        ws.append(row)
    info = wb.create_sheet("Info")
    info["C2"] = "Forklaring til oversigten 'WellsDK' / Comments to the 'WellsDK' overview"
    # Fixed timestamps so regenerating does not churn the binary needlessly.
    stamp = datetime.datetime(2026, 1, 1)
    wb.properties.created = stamp
    wb.properties.modified = stamp
    wb.save(path)


def write_wells_shapefile(zip_path: Path) -> None:
    stem = OUT_DIR / "ExpAppWells_fixture"
    w = shapefile.Writer(str(stem), shapeType=shapefile.POINT)
    for name, size in (("Well_Name", 30), ("Well_Numb", 15), ("Classifica", 30),
                       ("Licence", 30), ("Operator", 30), ("Location", 10),
                       ("Released", 5)):
        w.field(name, "C", size)
    w.field("Spud_Date", "D")
    w.field("N_degrees", "N", 12, 6)  # coordinate echo column; must be dropped
    for name, number, cls, lon, lat, released, location in SHP_WELLS:
        w.point(lon, lat)
        w.record(name, number, cls, "01/35", "DAPCO", location, released or "",
                 datetime.date(1951, 5, 29), lat)
    w.close()
    stem.with_suffix(".prj").write_text(PRJ_WKT_ED50_GEO, encoding="utf-8")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for ext in (".shp", ".shx", ".dbf", ".prj"):
            member = stem.with_suffix(ext)
            zf.write(member, arcname=f"{stem.name}{ext}")
            member.unlink()


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    to_src = Transformer.from_crs("EPSG:4326", "EPSG:23032", always_xy=True)

    stem = OUT_DIR / "fields"
    w = shapefile.Writer(str(stem), shapeType=shapefile.POLYGON)
    w.field("FIELD_NAME", "C", 40)
    w.field("OPERATOR", "C", 40)
    # Noise columns ESRI often writes back; build_gis must drop these.
    w.field("Shape_Area", "N", 20, 3)
    w.field("FID", "N", 10)
    for i, (label, operator, lon, lat) in enumerate(FIELDS):
        cx, cy = to_src.transform(lon, lat)
        w.poly([_square(cx, cy)])
        w.record(label, operator, 123456.0, i)
    w.close()

    # Sidecar projection file (pyshp does not write .prj).
    (stem.with_suffix(".prj")).write_text(PRJ_WKT, encoding="utf-8")

    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as zf:
        for ext in (".shp", ".shx", ".dbf", ".prj"):
            member = stem.with_suffix(ext)
            zf.write(member, arcname=f"fields{ext}")
            member.unlink()  # keep only the zip checked in

    print(f"wrote {ZIP_PATH}")

    write_wells_xlsx(OUT_DIR / "wells.xlsx")
    write_wells_shapefile(OUT_DIR / "ExpAppWells_fixture.zip")
    print(f"wrote {OUT_DIR / 'wells.xlsx'} and {OUT_DIR / 'ExpAppWells_fixture.zip'}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Generate a synthetic ENS-style field shapefile fixture (a zip).

The GIS tests must run offline against a small, checked-in shapefile that looks
like the real thing: polygons in **EPSG:23032 (ED50 / UTM 32 N)** with a
``FIELD_NAME`` attribute and a matching ``.prj``. To keep the sample map looking
plausible, the polygons are placed at the real-ish lon/lat of a few Danish North
Sea fields and back-projected into the source CRS, so a correct reprojection
lands them where they belong.

Run from the repo root:  ``python tests/fixtures/make_gis_fixture.py``
Regenerates ``tests/fixtures/gis/fields.zip``.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

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


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    to_src = Transformer.from_crs("EPSG:4326", "EPSG:23032", always_xy=True)

    stem = OUT_DIR / "fields"
    w = shapefile.Writer(str(stem), shapeType=shapefile.POLYGON)
    w.field("FIELD_NAME", "C", 40)
    w.field("OPERATOR", "C", 40)
    for label, operator, lon, lat in FIELDS:
        cx, cy = to_src.transform(lon, lat)
        w.poly([_square(cx, cy)])
        w.record(label, operator)
    w.close()

    # Sidecar projection file (pyshp does not write .prj).
    (stem.with_suffix(".prj")).write_text(PRJ_WKT, encoding="utf-8")

    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as zf:
        for ext in (".shp", ".shx", ".dbf", ".prj"):
            member = stem.with_suffix(ext)
            zf.write(member, arcname=f"fields{ext}")
            member.unlink()  # keep only the zip checked in

    print(f"wrote {ZIP_PATH}")


if __name__ == "__main__":
    main()

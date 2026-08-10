"""Phase 6 -- ownership matrix: which companies hold which share of each field.

ENS does not publish licensee shares as a downloadable table; the "Danish
Licences and Licensees" page (see ``SOURCE_URL`` below) is an interactive tool
that has to be read by hand. This script therefore parses a manually
maintained export checked into the repo
(``data/sources/licences/danske_lisenser_komplett.xlsx``, one row per
licence/block/company) instead of fetching anything -- there is nothing
machine-readable to fetch. Re-run this script after replacing that file with a
fresher export.

The workbook has one row per (licence, block, company) triple, so the same
field is usually covered by several rows (one per co-venturer, sometimes
several rows per co-venturer when its share is split across legal entities in
the same "Gruppe"/group). ``FIELD_AREA`` maps our canonical field slugs (from
data/fields.json) to the workbook's "Område/felt" column; most producing
fields carry no separate licence area and fall back to the main "Sole
Concession of 8 July 1962" (the historic DUC concession, "Contiguous Area").
Where a field has been re-licensed over the years under more than one licence
name (e.g. Hejre, Solsort), the most recently granted licence wins -- the
older one is superseded, not additional.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import common as C

SOURCE_URL = "https://ens.dk/en/energy-sources/danish-licences-and-licensees"
LICENCES_XLSX = C.DATA_DIR / "sources" / "licences" / "danske_lisenser_komplett.xlsx"
OWNERSHIP_PATH = C.DATA_DIR / "ownership.json"

# "Område/felt" value in the workbook that carries the default DUC consortium
# split, applied to every canonical field not listed in FIELD_AREA below.
DUC_AREA = "Contiguous Area"

# Canonical field slug -> workbook "Område/felt" value, for fields whose
# ownership differs from the default DUC split.
FIELD_AREA = {
    "cecilie": "Cecilie Field",
    "lulita": "Lulita",          # + "Lulita part", merged below
    "nini": "Nini Field",
    "siri": "Siri Field",
    "solsort": "Solsort Field Delineation",  # licence 3/09, supersedes 4/98
    "syd_arne": "South Arne Field",
}
# Extra workbook areas that merge into a FIELD_AREA target above (same field,
# split across more than one licence area in the workbook).
AREA_ALIASES = {
    "Lulita part": "Lulita",
}


def load_rows(path: Path) -> list[dict]:
    import openpyxl

    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb["Lisenser per blokk"]
    rows_iter = ws.iter_rows(values_only=True)
    header = [str(h).strip() for h in next(rows_iter)]
    rows = []
    for r in rows_iter:
        if all(v is None for v in r):
            continue
        rows.append(dict(zip(header, r)))
    return rows


def build_area_shares(rows: list[dict]) -> dict[str, dict[str, float]]:
    """Return {area: {company: share}}, keeping only the latest licence per area."""
    # The workbook has one row per (licence, block, company): the same
    # (company, share) pair repeats for every block in the licence, so dedupe
    # on (licence, Selskap) before summing shares by Gruppe -- summing the raw
    # rows would multiply each share by the block count.
    by_area_licence: dict[str, dict[str, dict]] = {}
    for r in rows:
        area = r.get("Område/felt")
        area = AREA_ALIASES.get(area, area)
        if not area:
            continue
        licence = r.get("Lisensnavn")
        granted = r.get("Licence granted")
        company = r.get("Selskap")
        group = r.get("Gruppe")
        share = C.parse_number(r.get("Selskapsandel"))
        if not group or share is None:
            continue
        bucket = by_area_licence.setdefault(area, {})
        entry = bucket.setdefault(licence, {"granted": granted, "companies": {}})
        entry["companies"][company] = (group, share)

    area_shares: dict[str, dict[str, float]] = {}
    for area, licences in by_area_licence.items():
        latest = max(licences.values(), key=lambda e: e["granted"] or "")
        shares: dict[str, float] = {}
        for group, share in latest["companies"].values():
            shares[group] = shares.get(group, 0.0) + share
        area_shares[area] = {g: round(s, 6) for g, s in shares.items()}
    return area_shares


def build_ownership(fields: dict, area_shares: dict[str, dict[str, float]]) -> dict:
    if DUC_AREA not in area_shares:
        raise C.SourceFormatError(
            f"expected a {DUC_AREA!r} licence area in the workbook to use as the "
            "default consortium split; it was not found"
        )
    duc_shares = area_shares[DUC_AREA]

    ownership = {}
    for slug in fields:
        area = FIELD_AREA.get(slug, DUC_AREA)
        shares = area_shares.get(area)
        if shares is None:
            raise C.SourceFormatError(
                f"field {slug!r} maps to licence area {area!r}, which is not in the workbook"
            )
        total = sum(shares.values())
        if abs(total - 1.0) > 0.01:
            raise C.SourceFormatError(
                f"licence area {area!r} shares sum to {total:.4f}, expected ~1.0"
            )
        ownership[slug] = shares
    return {
        "schema_version": C.SCHEMA_VERSION,
        "source": SOURCE_URL,
        "note": "Manually curated from an ENS licensee export; see scripts/ingest_ownership.py.",
        "companies": sorted({g for shares in ownership.values() for g in shares}),
        "fields": ownership,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--xlsx", type=Path, default=LICENCES_XLSX)
    ap.parse_args(argv)

    fields = C.read_json(C.FIELDS_PATH, default={}).get("fields", {})
    if not fields:
        C.error(f"no fields in {C.FIELDS_PATH}; run ingest_yearly.py first")
        return 1

    rows = load_rows(LICENCES_XLSX)
    area_shares = build_area_shares(rows)
    ownership = build_ownership(fields, area_shares)
    wrote = C.write_json_stable(OWNERSHIP_PATH, ownership)
    C.info(f"ownership.json {'updated' if wrote else 'unchanged'}: "
           f"{len(ownership['fields'])} fields, {len(ownership['companies'])} companies")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

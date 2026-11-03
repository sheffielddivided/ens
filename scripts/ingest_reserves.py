"""Reserves per field (1 January, 2018 →) → data/reserves.json.

ENS publishes its field-by-field reserves only inside the yearly PDF reports
«Ressourceopgørelse og prognoser» (table 1). Those numbers have been compiled
by hand into a workbook checked into the repo
(``data/sources/reserves/ressurser_og_prognoser_2018_2026.xlsx``, sheet
«Felt – reserver»); this script parses that sheet instead of fetching
anything. Re-run it after replacing the workbook with a newer compilation.

Source units are kept as-is (oil in mill. m³, sales gas in bn. Nm³) and a
combined figure in **million barrels of oil equivalent (MMboe)** is added:

    oil  : mill. m³ × 6.29                     → mill. bbl
    gas  : bn. Nm³ ≈ mill. m³ o.e. (1000:1) × 6.29 → mill. boe

which is the same convention the web page already uses for production
(``BOE = 6.29`` in docs/app.js, gas mio. Nm³ ≙ 1000 m³ o.e.), so reserves and
production are comparable. The source rounds every field to whole mill. m³ /
bn. Nm³, so small fields show as 0 and the per-field MMboe carry about
±3 MMboe of rounding each.

Cell fill colours in the sheet carry provenance that must not be lost:
orange = a «1» reconstructed because it drops out of the PDF text extraction
for the 2018–2023 reports; grey = blank in the extraction and unresolved.
Both are recorded per value in ``flags``. Blank cells stay ``null`` (no data),
never 0.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import common as C

SOURCE_URL = "https://ens.dk/en/energy-sources/oil-and-gas-resources-and-forecasts"
RESERVES_XLSX = C.DATA_DIR / "sources" / "reserves" / "ressurser_og_prognoser_2018_2026.xlsx"
RESERVES_PATH = C.DATA_DIR / "reserves.json"
SHEET = "Felt – reserver"

BOE_PER_M3 = 6.29          # barrels per m³ oil (same as docs/app.js)
GAS_OE_PER_NM3 = 1 / 1000  # m³ oil-equivalent per Nm³ gas (same as docs/app.js)

MEASURES = {"Olje": ("oil", "mill. m³"), "Salgsgass": ("gas", "mrd. Nm³")}
# Workbook field name -> canonical slug (data/fields.json), where
# normalize_field() alone does not give the right one.
FIELD_SLUG = {
    "Tyra (inkl. Tyra Sydøst)": "tyra",
    "Solsort Vest": "solsort",
}
# Fields with no separate reserve figure: slug -> slug whose figure includes it.
INCLUDED_IN = {"tyra_se": "tyra"}

FILL_FLAGS = {"FFFCE4D6": "reconstructed", "FFE7E6E6": "unresolved"}


def _to_mmboe(measure: str, v: float) -> float:
    if measure == "oil":
        return v * BOE_PER_M3
    return v * 1e9 * GAS_OE_PER_NM3 / 1e6 * BOE_PER_M3


def parse_sheet(path: Path) -> dict:
    import openpyxl

    wb = openpyxl.load_workbook(path, data_only=True)
    if SHEET not in wb.sheetnames:
        raise C.SourceFormatError(f"{path.name}: sheet {SHEET!r} not found (have {wb.sheetnames})")
    ws = wb[SHEET]

    header_row = None
    for row in ws.iter_rows(min_row=1, max_row=10):
        if [c.value for c in row[:3]] == ["Felt", "Råvare", "Enhet"]:
            header_row = row
            break
    if header_row is None:
        raise C.SourceFormatError(f"{SHEET!r}: header row 'Felt | Råvare | Enhet | <år> …' not found")
    years: list[tuple[int, str]] = []
    for c in header_row[3:]:
        if c.value is None:
            continue
        y = str(c.value).strip()
        if not (y.isdigit() and len(y) == 4):
            raise C.SourceFormatError(f"{SHEET!r}: unexpected year header {c.value!r}")
        years.append((c.column, y))
    if not years:
        raise C.SourceFormatError(f"{SHEET!r}: no year columns")

    fields: dict[str, dict] = {}
    reported: dict[str, dict] = {}
    for row in ws.iter_rows(min_row=header_row[0].row + 1):
        name, raw_measure, unit = (row[0].value, row[1].value, row[2].value)
        if name is None:
            continue
        name = str(name).strip()
        if raw_measure not in MEASURES:
            raise C.SourceFormatError(f"{SHEET!r} row {row[0].row}: unknown commodity {raw_measure!r}")
        measure, expected_unit = MEASURES[raw_measure]
        if unit != expected_unit:
            raise C.SourceFormatError(
                f"{SHEET!r} row {row[0].row}: unit {unit!r} for {raw_measure}, expected {expected_unit!r}"
            )
        cells = {c.column: c for c in row}
        values, flags = {}, {}
        for col, y in years:
            cell = cells.get(col)
            v = C.parse_number(cell.value) if cell is not None else None
            values[y] = v
            fill = cell.fill.fgColor.rgb if cell is not None and cell.fill.fill_type else None
            if fill in FILL_FLAGS:
                flags[y] = FILL_FLAGS[fill]

        if name.startswith("Sum felt"):
            continue  # recomputed from the field rows below
        if name.startswith("Rapportert sum"):
            reported.setdefault(measure, values)
            continue
        if name.startswith("Differanse"):
            continue
        slug = FIELD_SLUG.get(name, C.normalize_field(name))
        entry = fields.setdefault(slug, {"source_name": name, "oil": {}, "gas": {}, "flags": {}})
        if entry["source_name"] != name:
            raise C.SourceFormatError(f"{SHEET!r}: {name!r} and {entry['source_name']!r} both map to {slug!r}")
        entry[measure] = values
        if flags:
            entry["flags"][measure] = flags

    return {"years": [y for _, y in years], "fields": fields, "reported": reported}


def build_reserves(parsed: dict, known_fields: dict) -> dict:
    years = parsed["years"]
    unknown = sorted(set(parsed["fields"]) - set(known_fields)) if known_fields else []
    if unknown:
        raise C.SourceFormatError(
            f"reserve fields not in fields.json: {unknown} -- add them to FIELD_SLUG"
        )
    out_fields = {}
    for slug in sorted(parsed["fields"]):
        f = parsed["fields"][slug]
        if not f["oil"] or not f["gas"]:
            raise C.SourceFormatError(f"field {slug!r}: needs both an Olje and a Salgsgass row")
        mmboe = {}
        for y in years:
            o, g = f["oil"].get(y), f["gas"].get(y)
            mmboe[y] = None if o is None and g is None else round(
                _to_mmboe("oil", o or 0) + _to_mmboe("gas", g or 0), 3)
        out_fields[slug] = {
            "source_name": f["source_name"],
            "oil_mill_m3": f["oil"],
            "gas_bn_nm3": f["gas"],
            "oil_mmboe": {y: None if v is None else round(_to_mmboe("oil", v), 3) for y, v in f["oil"].items()},
            "gas_mmboe": {y: None if v is None else round(_to_mmboe("gas", v), 3) for y, v in f["gas"].items()},
            "mmboe": mmboe,
            **({"flags": f["flags"]} if f["flags"] else {}),
        }
    rep = parsed["reported"]
    reported_mmboe = {}
    for y in years:
        o, g = rep.get("oil", {}).get(y), rep.get("gas", {}).get(y)
        reported_mmboe[y] = None if o is None or g is None else round(_to_mmboe("oil", o) + _to_mmboe("gas", g), 3)
    return {
        "schema_version": C.SCHEMA_VERSION,
        "source": SOURCE_URL,
        "note": ("Reserver per felt per 1. januar (felt i drift og besluttet utbygging), "
                 "fra ENS «Ressourceopgørelse og prognoser» tabell 1, manuelt sammenstilt; "
                 "se scripts/ingest_reserves.py."),
        "units": {
            "oil_mill_m3": "mill. m³", "gas_bn_nm3": "mrd. Nm³", "mmboe": "mill. fat o.e.",
        },
        "conversion": {
            "boe_per_m3_oil": BOE_PER_M3,
            "m3_oe_per_nm3_gas": GAS_OE_PER_NM3,
        },
        "years": years,
        "included_in": INCLUDED_IN,
        "reported_total": {
            "oil_mill_m3": rep.get("oil", {}),
            "gas_bn_nm3": rep.get("gas", {}),
            "mmboe": reported_mmboe,
        },
        "fields": out_fields,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--xlsx", type=Path, default=RESERVES_XLSX)
    args = ap.parse_args(argv)

    known = C.read_json(C.FIELDS_PATH, default={}).get("fields", {})
    try:
        reserves = build_reserves(parse_sheet(args.xlsx), known)
    except C.SourceFormatError as e:
        C.error(str(e))
        return 1
    wrote = C.write_json_stable(RESERVES_PATH, reserves)
    C.write_json_stable(C.DOCS_DATA_DIR / RESERVES_PATH.name, reserves)
    C.info(f"reserves.json {'updated' if wrote else 'unchanged'}: "
           f"{len(reserves['fields'])} fields, {reserves['years'][0]}–{reserves['years'][-1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

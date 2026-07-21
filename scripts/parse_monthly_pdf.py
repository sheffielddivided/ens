#!/usr/bin/env python3
"""Phase 4 -- parse one monthly PDF production report into records.

PDF reports (roughly 2024 ->) carry the same field x measure table as the older
HTML reports, so once the table is extracted the *shared* keyword-driven logic
in ``monthly_common`` maps columns and builds records -- guaranteeing the same
field names, measures and unit checks across both eras.

Table extraction uses pdfplumber. Because some reports are ruled and others rely
on whitespace alignment, several extraction strategies are tried and every
resulting table becomes a candidate; the table that yields the most oil/gas/water
records wins. Inspect the raw extraction first with ``--dump``.

Standalone use:

    python scripts/parse_monthly_pdf.py --dump --file report.pdf
    python scripts/parse_monthly_pdf.py --file report.pdf --year 2024 --month 1
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import common as C  # noqa: E402
import monthly_common as M  # noqa: E402

# pdfplumber table-extraction strategies, tried in order. "lines" reads ruled
# tables; the "text" strategy handles borderless, whitespace-aligned tables.
_STRATEGIES = [
    None,  # pdfplumber defaults (lines)
    {"vertical_strategy": "text", "horizontal_strategy": "text",
     "snap_tolerance": 4, "join_tolerance": 4},
    {"vertical_strategy": "text", "horizontal_strategy": "lines"},
]


def _clean(cell) -> str:
    if cell is None:
        return ""
    return " ".join(str(cell).replace("\n", " ").split())


def extract_all_tables(path: Path) -> list[list[list[str]]]:
    """Return every candidate table (cleaned) found under any strategy."""
    import pdfplumber

    tables: list[list[list[str]]] = []
    seen: set[str] = set()
    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            for settings in _STRATEGIES:
                try:
                    raw = page.extract_tables(table_settings=settings) if settings \
                        else page.extract_tables()
                except Exception as exc:  # noqa: BLE001 -- strategy may not apply
                    C.warn(f"pdf extract strategy {settings} failed: {exc}")
                    continue
                for t in raw or []:
                    grid = [[_clean(c) for c in row] for row in t if row]
                    grid = [r for r in grid if any(c for c in r)]
                    if len(grid) < 2:
                        continue
                    key = repr(grid)
                    if key not in seen:
                        seen.add(key)
                        tables.append(grid)
    return tables


def parse_pdf(path: Path, year: int, month: int, source_url: str) -> list[dict]:
    """Parse a monthly PDF report into records (raises on unknown structure)."""
    candidates = extract_all_tables(path)
    if not candidates:
        raise C.SourceFormatError(
            f"{year}-{month:02d}: pdfplumber extracted no tables from {path.name}"
        )
    best: list[dict] = []
    best_units: dict = {}
    errors = []
    for grid in candidates:
        try:
            header_rows, data_rows = M.split_by_scoring(grid)
            records, units = M.records_from_table(
                header_rows, data_rows, year=year, month=month, source_url=source_url
            )
        except C.SourceFormatError as exc:
            errors.append(str(exc))
            continue
        if len(records) > len(best):
            best, best_units = records, units
    if not best:
        raise C.SourceFormatError(
            f"{year}-{month:02d}: no PDF table yielded oil/gas/water records "
            f"(tried {len(candidates)} tables). Last issues: {errors[-2:]}"
        )
    M.check_units(best_units, year, month)
    M.plausibility_warnings(best, year, month)
    if best_units:
        C.info(f"{year}-{month:02d} PDF: detected units {best_units}")
    return C.sort_records(best, monthly=True)


def parse_source(entry: dict, fetcher: C.Fetcher, *, force: bool = False) -> list[dict]:
    """Parse a monthly index entry (fetching/caching the PDF as needed)."""
    url = entry["url"]
    filename = f"monthly_{entry['year']}_{entry['month']:02d}_{C.safe_filename(url)}"
    if not filename.endswith(".pdf"):
        filename += ".pdf"
    fetcher.fetch(url, filename, force=force, binary=True)
    return parse_pdf(fetcher.cache_path(filename), entry["year"], entry["month"], url)


def _dump(path: Path) -> None:
    for i, grid in enumerate(extract_all_tables(path)):
        C.info(f"--- candidate table {i} ({len(grid)} rows) ---")
        for row in grid:
            C.info("   " + " | ".join(row))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Parse one monthly PDF production report into records.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--file", type=Path, help="Local PDF file to parse.")
    src.add_argument("--url", help="URL to fetch (cached under data/sources/raw).")
    p.add_argument("--year", type=int)
    p.add_argument("--month", type=int)
    p.add_argument("--offline", action="store_true", help="Use only cached downloads.")
    p.add_argument("--dump", action="store_true",
                   help="Print raw extracted tables and exit (no parsing).")
    args = p.parse_args(argv)

    try:
        if args.file:
            path = args.file
        else:
            fetcher = C.Fetcher(offline=args.offline)
            fname = C.safe_filename(args.url)
            if not fname.endswith(".pdf"):
                fname += ".pdf"
            fetcher.fetch(args.url, fname, binary=True)
            path = fetcher.cache_path(fname)

        if args.dump:
            _dump(path)
            return 0
        if args.year is None or args.month is None:
            p.error("--year and --month are required unless --dump is given")
        url = args.url or f"file://{path}"
        records = parse_pdf(path, args.year, args.month, url)
    except Exception as exc:  # noqa: BLE001
        C.error(f"parse_monthly_pdf failed: {exc}")
        return 1
    import json
    print(json.dumps(records, ensure_ascii=False, indent=2))
    C.info(f"parsed {len(records)} records for {args.year}-{args.month:02d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

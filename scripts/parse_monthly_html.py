#!/usr/bin/env python3
"""Phase 3 -- parse one monthly HTML production report into records.

HTML reports (roughly 2018 -> 2023) present a table of fields x measures. The
exact columns and their order changed over the years, so we do not hard-code
positions: the table is converted to a rectangular grid (honouring
rowspan/colspan), the header rows are mapped to measures by keyword, and rows
are read from whichever table actually contains oil/gas/water columns.

Strategy selection is driven by *what is in the document*, not by date: every
table is tried and the one yielding the most field/measure records wins. A
document that matches nothing raises ``SourceFormatError``.

Standalone use:

    python scripts/parse_monthly_html.py --file report.html --year 2019 --month 5
    python scripts/parse_monthly_html.py --url <cached-url> --year 2019 --month 5
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import common as C  # noqa: E402
import monthly_common as M  # noqa: E402


def _cell_text(cell) -> str:
    return re.sub(r"\s+", " ", cell.get_text(" ", strip=True)).strip()


def _table_to_grid(table):
    """Return (rows, n_header_rows). ``rows`` is a rectangular list of str rows.

    rowspan/colspan are expanded so every logical column lines up. n_header_rows
    is the count of leading rows that came from <thead> (None if there is none).
    """
    ordered = []          # (tr, is_header)
    thead = table.find("thead")
    if thead:
        for tr in thead.find_all("tr", recursive=False):
            ordered.append((tr, True))
    for section in table.find_all(["tbody", "tfoot"], recursive=False):
        for tr in section.find_all("tr", recursive=False):
            ordered.append((tr, False))
    # Direct <tr> children (tables without thead/tbody) are body rows; the
    # header is then found by keyword scoring, not by markup section.
    for tr in table.find_all("tr", recursive=False):
        ordered.append((tr, False))
    if not ordered:
        return [], None

    grid: list[list[str]] = []
    header_count = 0
    carries: dict[int, list] = {}   # col -> [text, remaining_rows]
    for tr, is_header in ordered:
        cells = tr.find_all(["td", "th"], recursive=False)
        if not cells:
            continue
        rowd: dict[int, str] = {}
        for col, spec in list(carries.items()):
            if spec[1] > 0:
                rowd[col] = spec[0]
                spec[1] -= 1
                if spec[1] == 0:
                    del carries[col]
        ci = 0
        for cell in cells:
            while ci in rowd:
                ci += 1
            text = _cell_text(cell)
            try:
                colspan = max(1, int(cell.get("colspan", 1)))
                rowspan = max(1, int(cell.get("rowspan", 1)))
            except (TypeError, ValueError):
                colspan = rowspan = 1
            for k in range(colspan):
                c = ci
                while c in rowd:
                    c += 1
                rowd[c] = text
                if rowspan > 1:
                    carries[c] = [text, rowspan - 1]
                ci = c + 1
        width = (max(rowd) + 1) if rowd else 0
        grid.append([rowd.get(c, "") for c in range(width)])
        if is_header:
            header_count += 1

    width = max((len(r) for r in grid), default=0)
    for r in grid:
        r.extend([""] * (width - len(r)))
    return grid, (header_count if header_count else None)


def extract_candidate_tables(html: str):
    """Yield (header_rows, data_rows) for each table in the document."""
    import bs4

    soup = bs4.BeautifulSoup(html, "lxml")
    for table in soup.find_all("table"):
        grid, n_header = _table_to_grid(table)
        if not grid or len(grid) < 2:
            continue
        if n_header:
            yield grid[:n_header], grid[n_header:]
        else:
            try:
                yield M.split_by_scoring(grid)
            except C.SourceFormatError:
                continue


def parse_html(html: str, year: int, month: int, source_url: str) -> list[dict]:
    """Parse a monthly HTML report into records (raises on unknown structure)."""
    best: list[dict] = []
    best_units: dict = {}
    errors = []
    for header_rows, data_rows in extract_candidate_tables(html):
        try:
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
            f"{year}-{month:02d}: no table yielded oil/gas/water records. "
            f"Tried {len(errors)} table(s). Last issues: {errors[-2:]}"
        )
    M.check_units(best_units, year, month)
    M.plausibility_warnings(best, year, month)
    if best_units:
        C.info(f"{year}-{month:02d} HTML: detected units {best_units}")
    return C.sort_records(best, monthly=True)


def parse_source(entry: dict, fetcher: C.Fetcher, *, force: bool = False) -> list[dict]:
    """Parse a monthly index entry (fetching/caching the HTML as needed)."""
    url = entry["url"]
    filename = f"monthly_{entry['year']}_{entry['month']:02d}_{C.safe_filename(url)}"
    if not filename.endswith((".html", ".htm")):
        filename += ".html"
    html = fetcher.fetch_text(url, filename, force=force)
    return parse_html(html, entry["year"], entry["month"], url)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Parse one monthly HTML production report into records.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--file", type=Path, help="Local HTML file to parse.")
    src.add_argument("--url", help="URL to fetch (cached under data/sources/raw).")
    p.add_argument("--year", type=int, required=True)
    p.add_argument("--month", type=int, required=True)
    p.add_argument("--offline", action="store_true", help="Use only cached downloads.")
    args = p.parse_args(argv)
    try:
        if args.file:
            html = args.file.read_text(encoding="utf-8", errors="replace")
            url = f"file://{args.file}"
        else:
            fetcher = C.Fetcher(offline=args.offline)
            url = args.url
            html = fetcher.fetch_text(url, f"monthly_{args.year}_{args.month:02d}.html")
        records = parse_html(html, args.year, args.month, url)
    except Exception as exc:  # noqa: BLE001
        C.error(f"parse_monthly_html failed: {exc}")
        return 1
    import json
    print(json.dumps(records, ensure_ascii=False, indent=2))
    C.info(f"parsed {len(records)} records for {args.year}-{args.month:02d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

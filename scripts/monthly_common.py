"""Shared table logic for the monthly HTML and PDF report parsers.

Both report formats are ultimately a table of fields x measures. Rather than
hard-coding column positions (which drift across the years and differ between
HTML and PDF), we map columns to measures by matching header vocabulary. The
HTML and PDF parsers only differ in how they extract the raw 2-D table; both
then call :func:`records_from_table`.

The approach is deliberately structure-discovering so the same code copes with
column reordering and layout changes. When a table cannot be interpreted the
caller raises ``SourceFormatError`` -- we never emit dubious rows silently.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import common as C  # noqa: E402

# Header text that identifies the field-name column.
_FIELD_HEADERS = {
    "field", "felt", "fields", "felter", "field_name", "feltnavn", "name", "navn",
}

# measure -> list of (required-keywords, forbidden-keywords). A column header
# matches a measure if it contains *all* required and *none* of the forbidden
# keywords of some rule. Specific measures (injection/flare/fuel/export) are
# listed first so e.g. "gas injection" maps to gas_injection, not gas.
_MEASURE_RULES: list[tuple[str, list[str], list[str]]] = [
    ("gas_injection", ["gas", "inject"], []),
    ("gas_injection", ["gas", "injekt"], []),
    ("gas_injection", ["gas", "reinject"], []),
    ("gas_injection", ["gas", "inj"], []),      # "inj." abbreviation
    ("water_injection", ["water", "inject"], []),
    ("water_injection", ["vand", "injekt"], []),
    ("water_injection", ["water", "inj"], []),  # "inj." abbreviation
    ("water_injection", ["vand", "inj"], []),
    ("gas_export", ["gas", "export"], []),
    ("gas_export", ["gas", "eksport"], []),
    ("oil_export", ["oil", "export"], []),
    ("flare", ["flar"], []),
    ("flare", ["fakling"], []),
    ("fuel", ["fuel"], []),
    ("fuel", ["braendsel"], []),
    ("fuel", ["brndsel"], []),
    ("gas", ["sales", "gas"], []),
    ("gas", ["salgsgas"], []),
    # Plain measures last, excluding the specific variants above.
    ("oil", ["oil"], ["inject", "injekt", "export", "eksport"]),
    ("oil", ["olie"], ["injekt", "eksport"]),
    ("gas", ["gas"], ["inject", "injekt", "reinject", "export", "eksport",
                       "flar", "fakling", "fuel", "braendsel", "brndsel"]),
    ("water", ["water"], ["inject", "injekt"]),
    ("water", ["vand"], ["injekt"]),
]

# Recognised SI unit strings. Longer forms first so "1000 m3" wins over "m3";
# word boundaries stop the "m3"/"tonnes" tokens matching inside ordinary words
# (e.g. the "t" in "Water"). Bare "t" is intentionally excluded for that reason.
_UNIT_RE = re.compile(
    r"\b(mio\.?\s*n?m3|million\s*n?m3|1\s?000\s*s?n?m3|1000\s*s?n?m3|s?nm3|s?m3|tonnes?)\b",
    re.I,
)
_SUPERSCRIPT = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")


def _norm(text: str) -> str:
    """Lowercase and ASCII-fold a header cell for keyword matching."""
    if text is None:
        return ""
    s = C.normalize_field(text).replace("_", " ")
    return s.strip()


def is_field_header(text: str) -> bool:
    return _norm(text) in _FIELD_HEADERS


def classify_column(header_text: str) -> str | None:
    """Return the measure a column header denotes, or None."""
    s = " " + _norm(header_text) + " "
    if not s.strip():
        return None
    for measure, required, forbidden in _MEASURE_RULES:
        if all(k in s for k in required) and not any(f in s for f in forbidden):
            return measure
    return None


def detect_unit(header_text: str) -> str | None:
    """Extract a raw unit string from a (possibly multi-line) header cell.

    Superscripts (e.g. "m³") are folded to plain digits ("m3") first so both
    spellings are recognised.
    """
    if not header_text:
        return None
    text = header_text.replace("\n", " ").translate(_SUPERSCRIPT)
    m = _UNIT_RE.search(text)
    return m.group(0).strip() if m else None


def combine_header_columns(header_rows: list[list[str]]) -> list[str]:
    """Join multi-row headers into one combined label per column index."""
    width = max((len(r) for r in header_rows), default=0)
    combined = []
    for ci in range(width):
        parts = []
        for row in header_rows:
            if ci < len(row) and row[ci]:
                txt = str(row[ci]).strip()
                if txt and (not parts or parts[-1] != txt):
                    parts.append(txt)
        combined.append(" ".join(parts))
    return combined


def map_columns(combined_headers: list[str]):
    """Map combined headers to (field_col, {col: measure}, {measure: unit}).

    Raises SourceFormatError when no field column or no core measure is found.
    """
    field_col = None
    colmap: dict[int, str] = {}
    units: dict[str, str] = {}
    for ci, htext in enumerate(combined_headers):
        if field_col is None and is_field_header(htext):
            field_col = ci
            continue
        measure = classify_column(htext)
        if measure and ci not in colmap and measure not in colmap.values():
            colmap[ci] = measure
            unit = detect_unit(htext)
            if unit:
                units[measure] = unit
    if not colmap:
        raise C.SourceFormatError(
            f"no measure columns recognised in headers: {combined_headers!r}"
        )
    if field_col is None:
        field_col = _infer_field_column(combined_headers, colmap)
    if not (set(colmap.values()) & set(C.CORE_MEASURES)):
        raise C.SourceFormatError(
            f"no oil/gas/water column found in headers: {combined_headers!r}"
        )
    return field_col, colmap, units


def _infer_field_column(combined_headers: list[str], colmap: dict[int, str]) -> int:
    """Fallback: the first non-measure column is the field-name column."""
    for ci in range(len(combined_headers)):
        if ci not in colmap:
            return ci
    return 0


def split_by_scoring(rows: list[list[str]]):
    """Split a flat table into (header_rows, data_rows) by keyword scoring.

    The header row is the one with the most measure/field-header hits; rows
    adjacent to it that also carry measure/unit vocabulary (but no field data)
    are folded into the header -- group headers sit above the name row and unit
    rows sit below it. Everything after is data.
    """
    best_idx, best_score = -1, 0
    for ri, row in enumerate(rows):
        score = sum(
            1 for c in row if is_field_header(c) or classify_column(c) or detect_unit(c)
        )
        if score > best_score:
            best_idx, best_score = ri, score
    if best_idx < 0:
        raise C.SourceFormatError("no header row with measure keywords found")
    start = best_idx
    while start - 1 >= 0 and _looks_like_header(rows[start - 1]):
        start -= 1
    end = best_idx
    while end + 1 < len(rows) and _looks_like_header(rows[end + 1]):
        end += 1
    return rows[start: end + 1], rows[end + 1:]


def _looks_like_header(row: list[str]) -> bool:
    has_measure = any(classify_column(c) or detect_unit(c) or is_field_header(c) for c in row)
    has_number = any(C.parse_number(c) is not None for c in row)
    return has_measure and not has_number


def build_records(
    field_col: int,
    colmap: dict[int, str],
    data_rows: list[list[str]],
    *,
    year: int,
    month: int,
    source_url: str,
) -> list[dict]:
    records = []
    for row in data_rows:
        label = row[field_col] if field_col < len(row) else ""
        slug = C.normalize_field(label)
        if not slug or C.is_total_label(label):
            continue
        rec = {"field": slug, "year": year, "month": month}
        got = False
        for ci, measure in colmap.items():
            val = C.parse_number(row[ci]) if ci < len(row) else None
            if val is not None:
                rec[measure] = val
                got = True
        if got:
            rec["preliminary"] = True
            rec["source_url"] = source_url
            records.append(rec)
    return records


def records_from_table(
    header_rows: list[list[str]],
    data_rows: list[list[str]],
    *,
    year: int,
    month: int,
    source_url: str,
):
    """Turn split header/data rows into (records, detected_units)."""
    combined = combine_header_columns(header_rows)
    field_col, colmap, units = map_columns(combined)
    records = build_records(
        field_col, colmap, data_rows, year=year, month=month, source_url=source_url
    )
    if not records:
        raise C.SourceFormatError(
            "header recognised but no data rows parsed -- check table body"
        )
    # NB: unit warnings are emitted by the caller on the *winning* table only
    # (via check_units), so losing extraction candidates do not spam the log.
    return records, units


# --------------------------------------------------------------------------- #
# Stacked monthly layout (the real ENS monthly reports, HTML and PDF)
# --------------------------------------------------------------------------- #
# Real reports are one table of stacked measure blocks: a header row
# ("Oil, M m3" / "Gas, MM Nm3" / "Water, M m3"), a sub-header
# ("Field | Monthly | Daily Avg. | ..."), then one row per field. We take the
# "Monthly" column of each oil/gas/water block. (Fuel/Flare/Injection in these
# reports are national aggregates, not per-field, so they are skipped.)
_MONTHLY_MEASURE_FIRST = {"oil": "oil", "gas": "gas", "water": "water",
                          "olie": "oil", "vand": "water"}


def _monthly_measure(cell: str) -> str | None:
    if not cell:
        return None
    first = C.normalize_field(cell.split(",")[0]).replace("_", " ").strip()
    return _MONTHLY_MEASURE_FIRST.get(first)


def _is_block_boundary(cell: str) -> bool:
    if _monthly_measure(cell):
        return True
    cl = (cell or "").strip().lower()
    return cl.startswith(("use of", "note", "anvendelse", "noter"))


def parse_stacked_monthly(rows: list[list[str]], year: int, month: int,
                          source_url: str) -> list[dict]:
    """Parse stacked oil/gas/water blocks, taking each block's Monthly column."""
    headers = [(i, m) for i, row in enumerate(rows)
               if row and (m := _monthly_measure(row[0]))]
    if not headers:
        return []
    by_field: dict[str, dict] = {}
    for idx, (hi, measure) in enumerate(headers):
        end = headers[idx + 1][0] if idx + 1 < len(headers) else len(rows)
        # Locate the sub-header row carrying "Monthly" (and maybe "Field").
        monthly_col = field_col = None
        sub_i = None
        for j in range(hi + 1, min(hi + 4, end)):
            for ci, cell in enumerate(rows[j]):
                cl = str(cell).strip().lower()
                if cl == "monthly":
                    monthly_col = ci
                if cl in ("field", "felt"):
                    field_col = ci
            if monthly_col is not None:
                sub_i = j
                break
        if monthly_col is None:
            continue
        if field_col is None:
            field_col = 0
        for k in range(sub_i + 1, end):
            row = rows[k]
            c0 = row[field_col] if field_col < len(row) else ""
            if _is_block_boundary(c0):
                break
            slug = C.normalize_field(c0)
            if not slug or C.is_total_label(c0):
                continue
            val = C.parse_number(row[monthly_col] if monthly_col < len(row) else None)
            if val is not None:
                by_field.setdefault(
                    slug, {"field": slug, "year": year, "month": month}
                )[measure] = val
    records = []
    for slug, rec in by_field.items():
        if any(m in rec for m in C.CORE_MEASURES):
            rec["preliminary"] = True
            rec["source_url"] = source_url
            records.append(rec)
    return records


def parse_monthly_rows(rows: list[list[str]], year: int, month: int,
                       source_url: str):
    """Parse one table's rows into (records, units).

    Tries the real stacked-block layout first, then the simpler
    measure-as-columns layout. Raises SourceFormatError if neither yields rows.
    """
    stacked = parse_stacked_monthly(rows, year, month, source_url)
    if stacked:
        return stacked, {}
    header_rows, data_rows = split_by_scoring(rows)
    return records_from_table(header_rows, data_rows,
                              year=year, month=month, source_url=source_url)


def record_richness(records: list[dict]) -> tuple[int, int]:
    """Rank a candidate parse: (number of fields, number of filled measures).

    Used to pick the most complete table when several candidates parse (e.g. a
    merged PDF grid with all blocks beats a partial one).
    """
    filled = sum(1 for r in records for m in C.CORE_MEASURES if m in r)
    return (len(records), filled)


def check_units(units: dict[str, str], year: int, month: int) -> None:
    """WARN if a source's own unit string diverges from the documented SI unit."""
    canon = {
        "oil": {"1000m3", "1000sm3", "1 000m3"},
        "gas": {"mionm3", "mio.nm3", "millionnm3", "mnm3"},
        "water": {"1000m3", "1000sm3"},
    }
    for measure, raw in units.items():
        if measure not in canon:
            continue
        key = re.sub(r"[\s.]", "", raw.lower())
        if key and key not in canon[measure]:
            C.warn(
                f"{year}-{month:02d}: {measure} unit in source is '{raw}', "
                f"expected {C.UNIT_DEFINITIONS[measure]}; verify before trusting"
            )


def plausibility_warnings(records: list[dict], year: int, month: int) -> None:
    """Log soft warnings for suspicious magnitudes (does not stop the run)."""
    for r in records:
        for m in C.CORE_MEASURES:
            v = r.get(m)
            if v is None:
                continue
            if v < 0:
                C.warn(f"{year}-{month:02d} {r['field']}: negative {m}={v}")
            # A single field/month above these is physically implausible in
            # the documented SI units; surfaces unit or parse mistakes.
            cap = {"oil": 5000, "gas": 5000, "water": 50000}[m]
            if v > cap:
                C.warn(f"{year}-{month:02d} {r['field']}: {m}={v} exceeds "
                       f"plausibility cap {cap} ({C.UNIT_DEFINITIONS[m]})")

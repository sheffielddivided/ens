#!/usr/bin/env python3
"""Phase 5 -- orchestrator: fetch missing months and (re)build combined.json.

Steps:

1. (Re)crawl the landing page to refresh ``data/sources/index.json`` (skippable).
2. Ingest the yearly Excel if ``yearly.json`` is missing or ``--refresh-yearly``.
3. For every monthly report not yet successfully parsed, fetch + parse it with
   the HTML or PDF parser. Per-month failures are logged and marked
   ``"status": "failed"`` in the index; they never abort the whole run.
4. Merge parsed months into ``monthly.json`` (a re-published month overwrites its
   records and bumps ``retrieved_at``).
5. Rebuild ``combined.json`` (final yearly figures take precedence over monthly
   estimates) and copy the datasets into ``docs/data/`` for GitHub Pages.

Idempotency: with no new reports nothing is re-fetched and every output file is
left byte-for-byte unchanged, so a re-run produces no git diff and no commit.

Run ``python scripts/update.py --help`` for options.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import common as C  # noqa: E402
import build_index as BI  # noqa: E402
import ingest_yearly  # noqa: E402
import parse_monthly_html as PH  # noqa: E402
import parse_monthly_pdf as PP  # noqa: E402


# --------------------------------------------------------------------------- #
# Monthly fetching
# --------------------------------------------------------------------------- #
def _monthly_key(r: dict):
    return (r["field"], r["year"], r["month"])


def process_months(index: dict, fetcher: C.Fetcher, *, force: bool) -> tuple[dict, bool]:
    """Fetch/parse due months, updating index entries. Returns (monthly, changed)."""
    monthly = C.read_json(C.MONTHLY_PATH) or {
        "schema_version": C.SCHEMA_VERSION,
        "unit_definitions": C.UNIT_DEFINITIONS,
        "records": [],
    }
    records_by_key = {_monthly_key(r): r for r in monthly.get("records", [])}
    changed = False
    ok = failed = skipped = 0

    for entry in index["monthly"]:
        already_ok = entry.get("status") == "ok"
        if already_ok and not force:
            skipped += 1
            continue
        y, m, fmt = entry["year"], entry["month"], entry["format"]
        try:
            if fmt == "pdf":
                recs = PP.parse_source(entry, fetcher, force=force)
            else:
                recs = PH.parse_source(entry, fetcher, force=force)
            if not recs:
                raise C.SourceFormatError("parser returned zero records")
            retrieved = C.utc_now_iso()
            # Replace every record for this (year, month) so revisions and
            # field additions/removals are reflected exactly.
            for k in [k for k in records_by_key if k[1] == y and k[2] == m]:
                del records_by_key[k]
            for r in recs:
                r["retrieved_at"] = retrieved
                records_by_key[_monthly_key(r)] = r
            entry.update(status="ok", retrieved_at=retrieved,
                         error=None, records=len(recs))
            ok += 1
            changed = True
            C.info(f"  {y}-{m:02d} [{fmt}]: parsed {len(recs)} records")
        except Exception as exc:  # noqa: BLE001 -- isolate per-month failures
            entry.update(status="failed", error=str(exc))
            failed += 1
            changed = True
            C.error(f"  {y}-{m:02d} [{fmt}]: FAILED -- {exc}")

    monthly["records"] = C.sort_records(list(records_by_key.values()), monthly=True)
    monthly["last_updated"] = _max_retrieved(monthly["records"])
    C.info(f"monthly: {ok} parsed, {failed} failed, {skipped} already ok")
    return monthly, changed


def _max_retrieved(records: list[dict]) -> str | None:
    stamps = [r["retrieved_at"] for r in records if r.get("retrieved_at")]
    return max(stamps) if stamps else None


# --------------------------------------------------------------------------- #
# combined.json
# --------------------------------------------------------------------------- #
TOTAL_SLUG = "_total"


def build_combined(yearly: dict, monthly: dict, fields: dict) -> dict:
    """Merge yearly + monthly into the frontend time-series structure.

    For the yearly resolution, final figures from yearly.json win; a year that
    exists only as monthly data becomes a preliminary aggregated point. Every
    point carries ``p`` (preliminary) so the frontend can dash estimates.
    """
    y_recs = yearly.get("records", [])
    m_recs = monthly.get("records", [])
    measures = list(C.CORE_MEASURES)

    field_slugs = sorted({r["field"] for r in y_recs} | {r["field"] for r in m_recs})

    # --- monthly resolution (always preliminary) ---------------------------
    monthly_series: dict = {}
    for r in m_recs:
        t = f"{r['year']}-{r['month']:02d}"
        fs = monthly_series.setdefault(r["field"], {})
        for meas in measures:
            if meas in r:
                fs.setdefault(meas, []).append((t, r["year"], r["month"], r[meas]))

    # --- yearly resolution -------------------------------------------------
    y_by_field: dict = {}
    for r in y_recs:
        y_by_field.setdefault(r["field"], {})[r["year"]] = r
    # Aggregate monthly into per-year sums (used only where yearly is absent).
    m_year_sums: dict = {}
    for r in m_recs:
        d = m_year_sums.setdefault(r["field"], {}).setdefault(r["year"], {})
        for meas in measures:
            if meas in r:
                d[meas] = d.get(meas, 0.0) + r[meas]

    series: dict = {}
    for fs in field_slugs:
        series[fs] = {
            "monthly": _emit_monthly(monthly_series.get(fs, {}), measures),
            "yearly": _emit_yearly(fs, y_by_field, m_year_sums, measures),
        }

    # --- totals across all fields -----------------------------------------
    series[TOTAL_SLUG] = _build_totals(m_recs, y_by_field, m_year_sums, field_slugs, measures)

    # --- field list for the UI --------------------------------------------
    field_meta = fields.get("fields", {})
    ui_fields = [
        {"slug": fs, "display_name": field_meta.get(fs, {}).get("display_name",
                                                                 fs.replace("_", " ").title())}
        for fs in field_slugs
    ]
    ui_fields.insert(0, {"slug": TOTAL_SLUG, "display_name": "Alle felt"})

    last_updated = max(
        [s for s in (_max_retrieved(y_recs), _max_retrieved(m_recs)) if s] or [None]
    ) if (y_recs or m_recs) else None

    return {
        "schema_version": C.SCHEMA_VERSION,
        "unit_definitions": C.UNIT_DEFINITIONS,
        "measures": measures,
        "last_updated": last_updated,
        "fields": ui_fields,
        "series": series,
    }


def _emit_monthly(field_monthly: dict, measures: list[str]) -> dict:
    out = {}
    for meas in measures:
        pts = sorted(field_monthly.get(meas, []), key=lambda x: (x[1], x[2]))
        out[meas] = [{"t": t, "v": round(v, 3), "p": True} for (t, _y, _m, v) in pts]
    return out


def _emit_yearly(fs: str, y_by_field: dict, m_year_sums: dict, measures: list[str]) -> dict:
    years = set(y_by_field.get(fs, {})) | set(m_year_sums.get(fs, {}))
    out = {meas: [] for meas in measures}
    for year in sorted(years):
        for meas in measures:
            if fs in y_by_field and year in y_by_field[fs] and meas in y_by_field[fs][year]:
                out[meas].append({"t": str(year), "v": round(y_by_field[fs][year][meas], 3), "p": False})
            elif fs in m_year_sums and year in m_year_sums[fs] and meas in m_year_sums[fs][year]:
                out[meas].append({"t": str(year), "v": round(m_year_sums[fs][year][meas], 3), "p": True})
    return out


def _build_totals(m_recs, y_by_field, m_year_sums, field_slugs, measures) -> dict:
    # Monthly totals: sum across fields per (year, month).
    monthly_tot: dict = {}
    for r in m_recs:
        key = (r["year"], r["month"])
        d = monthly_tot.setdefault(key, {})
        for meas in measures:
            if meas in r:
                d[meas] = d.get(meas, 0.0) + r[meas]
    monthly_out = {meas: [] for meas in measures}
    for (y, m) in sorted(monthly_tot):
        for meas in measures:
            if meas in monthly_tot[(y, m)]:
                monthly_out[meas].append(
                    {"t": f"{y}-{m:02d}", "v": round(monthly_tot[(y, m)][meas], 3), "p": True}
                )
    # Yearly totals: sum each field's yearly-resolution value per year.
    years = set()
    for fs in field_slugs:
        years |= set(y_by_field.get(fs, {})) | set(m_year_sums.get(fs, {}))
    yearly_out = {meas: [] for meas in measures}
    for year in sorted(years):
        for meas in measures:
            total = 0.0
            prelim = False
            present = False
            for fs in field_slugs:
                if fs in y_by_field and year in y_by_field[fs] and meas in y_by_field[fs][year]:
                    total += y_by_field[fs][year][meas]
                    present = True
                elif fs in m_year_sums and year in m_year_sums[fs] and meas in m_year_sums[fs][year]:
                    total += m_year_sums[fs][year][meas]
                    prelim = True
                    present = True
            if present:
                yearly_out[meas].append({"t": str(year), "v": round(total, 3), "p": prelim})
    return {"monthly": monthly_out, "yearly": yearly_out}


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def _copy_to_docs(paths: list[Path]) -> None:
    C.DOCS_DATA_DIR.mkdir(parents=True, exist_ok=True)
    for p in paths:
        if p.exists():
            data = C.read_json(p)
            C.write_json_stable(C.DOCS_DATA_DIR / p.name, data)


def run(*, offline: bool, crawl: bool, refresh_yearly: bool, force: bool) -> int:
    fetcher = C.Fetcher(offline=offline)

    # 1) index
    if crawl and not offline:
        C.info("crawling landing page for the source index ...")
        BI.build_index([C.ENS_PRODUCTION_PAGE], offline=offline)
    index = C.read_json(C.INDEX_PATH)
    if not index:
        C.error("no index.json -- run build_index.py first (or drop --no-crawl)")
        return 1

    # 2) yearly
    if refresh_yearly or not C.YEARLY_PATH.exists():
        C.info("ingesting yearly Excel ...")
        rc = ingest_yearly.ingest(source_url=None, offline=offline, force=force,
                                  xlsx_path=None, do_inspect=False)
        if rc != 0:
            C.error("yearly ingestion failed")
            return rc

    # 3) monthly
    C.info("processing monthly reports ...")
    monthly, _ = process_months(index, fetcher, force=force)

    # 4) persist monthly + index (idempotent)
    C.write_json_stable(C.MONTHLY_PATH, monthly, volatile_keys=("last_updated",))
    index["generated_at"] = index.get("generated_at") or C.utc_now_iso()
    C.write_json_stable(C.INDEX_PATH, index, volatile_keys=("generated_at",))

    # 5) combined + docs copy
    yearly = C.read_json(C.YEARLY_PATH) or {"records": []}
    fields = C.read_json(C.FIELDS_PATH) or {"fields": {}}
    combined = build_combined(yearly, monthly, fields)
    wrote = C.write_json_stable(C.COMBINED_PATH, combined, volatile_keys=("last_updated",))
    _copy_to_docs([C.COMBINED_PATH, C.MONTHLY_PATH, C.YEARLY_PATH, C.FIELDS_PATH])

    n_fields = len(combined["fields"]) - 1
    C.info(f"combined.json {'updated' if wrote else 'unchanged'}: "
           f"{n_fields} fields, last_updated={combined['last_updated']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Fetch new monthly reports and rebuild combined.json.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--offline", action="store_true",
                   help="Use only cached downloads; do not crawl or fetch.")
    p.add_argument("--no-crawl", action="store_true",
                   help="Skip re-crawling the landing page (use existing index).")
    p.add_argument("--refresh-yearly", action="store_true",
                   help="Re-download and re-parse the yearly Excel.")
    p.add_argument("--force", action="store_true",
                   help="Re-fetch and re-parse every month (revision sweep).")
    args = p.parse_args(argv)
    try:
        return run(offline=args.offline, crawl=not args.no_crawl,
                   refresh_yearly=args.refresh_yearly, force=args.force)
    except Exception as exc:  # noqa: BLE001
        C.error(f"update failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

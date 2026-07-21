#!/usr/bin/env python3
"""Phase 5 -- quality control over the whole dataset.

Checks (ERROR fails the run with a non-zero exit; WARN is logged but tolerated):

* ERROR: any negative measure value.
* ERROR: duplicate (field, year, month) monthly or (field, year) yearly records.
* ERROR: a record missing its source URL.
* WARN:  gaps in a field's monthly time series (missing months are listed).
* WARN:  where a full year of monthly data and a yearly figure both exist, the
         monthly sum deviates from the yearly figure by more than +/-10%.
* WARN:  physically implausible magnitudes (possible unit/parse mistakes).

Definition of done requires this to pass with no ERROR. Run
``python scripts/validate.py --help`` for options.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import common as C  # noqa: E402

# Per single field/month magnitude caps in the documented SI units. A value
# above these almost certainly signals a unit or parsing mistake.
_MONTHLY_CAP = {"oil": 5000, "gas": 5000, "water": 50000}
MEASURES = (*C.CORE_MEASURES, *C.OPTIONAL_MEASURES)


class Report:
    def __init__(self) -> None:
        self.errors = 0
        self.warnings = 0

    def error(self, msg: str) -> None:
        self.errors += 1
        C.error(msg)

    def warn(self, msg: str) -> None:
        self.warnings += 1
        C.warn(msg)


def _check_records(recs: list[dict], rep: Report, *, monthly: bool) -> None:
    label = "monthly" if monthly else "yearly"
    seen: set = set()
    for r in recs:
        key = (r.get("field"), r.get("year"), r.get("month")) if monthly \
            else (r.get("field"), r.get("year"))
        if key in seen:
            rep.error(f"{label}: duplicate record {key}")
        seen.add(key)
        if not r.get("source_url"):
            rep.error(f"{label}: record {key} has no source_url")
        for meas in MEASURES:
            v = r.get(meas)
            if v is None:
                continue
            if v < 0:
                rep.error(f"{label}: {key} has negative {meas}={v}")
            if monthly and meas in _MONTHLY_CAP and v > _MONTHLY_CAP[meas]:
                rep.warn(f"{label}: {key} {meas}={v} exceeds plausibility cap "
                         f"{_MONTHLY_CAP[meas]} ({C.UNIT_DEFINITIONS.get(meas)})")


def _check_gaps(m_recs: list[dict], rep: Report) -> None:
    months_by_field: dict = defaultdict(set)
    for r in m_recs:
        months_by_field[r["field"]].add((r["year"], r["month"]))
    for field, months in sorted(months_by_field.items()):
        ordered = sorted(months)
        first, last = ordered[0], ordered[-1]
        expected = _months_between(first, last)
        missing = [f"{y}-{m:02d}" for (y, m) in expected if (y, m) not in months]
        if missing:
            rep.warn(f"gap: field '{field}' missing {len(missing)} month(s) "
                     f"between {first[0]}-{first[1]:02d} and {last[0]}-{last[1]:02d}: "
                     f"{missing[:12]}{' ...' if len(missing) > 12 else ''}")


def _months_between(first, last) -> list[tuple[int, int]]:
    (y0, m0), (y1, m1) = first, last
    out = []
    y, m = y0, m0
    while (y, m) <= (y1, m1):
        out.append((y, m))
        m += 1
        if m > 12:
            m = 1
            y += 1
    return out


def _check_monthly_vs_yearly(m_recs, y_recs, rep: Report) -> None:
    # Aggregate monthly per (field, year) and count months present.
    agg: dict = defaultdict(lambda: {"count": 0, **{m: 0.0 for m in C.CORE_MEASURES}})
    for r in m_recs:
        a = agg[(r["field"], r["year"])]
        a["count"] += 1
        for meas in C.CORE_MEASURES:
            if meas in r:
                a[meas] += r[meas]
    y_index = {(r["field"], r["year"]): r for r in y_recs}
    compared = 0
    for key, a in sorted(agg.items()):
        if key not in y_index:
            continue
        if a["count"] < 12:
            continue  # only compare complete years -- partial years understate
        compared += 1
        for meas in C.CORE_MEASURES:
            yv = y_index[key].get(meas)
            mv = a[meas]
            if yv is None or yv == 0:
                continue
            dev = (mv - yv) / yv
            if abs(dev) > 0.10:
                rep.warn(f"monthly-vs-yearly: {key} {meas} monthly-sum={mv:.1f} "
                         f"vs yearly={yv:.1f} ({dev:+.1%})")
    C.info(f"monthly-vs-yearly: compared {compared} complete field-years")


def validate(*, strict_gaps: bool = False) -> int:
    rep = Report()
    monthly = C.read_json(C.MONTHLY_PATH) or {"records": []}
    yearly = C.read_json(C.YEARLY_PATH) or {"records": []}
    m_recs = monthly.get("records", [])
    y_recs = yearly.get("records", [])

    if not m_recs and not y_recs:
        rep.error("no data: neither monthly.json nor yearly.json has records")

    C.info(f"validating {len(y_recs)} yearly + {len(m_recs)} monthly records")
    _check_records(y_recs, rep, monthly=False)
    _check_records(m_recs, rep, monthly=True)
    _check_gaps(m_recs, rep)
    _check_monthly_vs_yearly(m_recs, y_recs, rep)

    C.info("=" * 50)
    C.info(f"validation: {rep.errors} ERROR(s), {rep.warnings} WARN(ing)s")
    if strict_gaps and rep.warnings:
        C.error("strict mode: warnings treated as failures")
        return 1
    return 1 if rep.errors else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Validate the ENS production dataset.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--strict", action="store_true",
                   help="Treat warnings as failures too (non-zero exit).")
    args = p.parse_args(argv)
    try:
        return validate(strict_gaps=args.strict)
    except Exception as exc:  # noqa: BLE001
        C.error(f"validate failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

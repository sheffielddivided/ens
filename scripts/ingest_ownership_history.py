#!/usr/bin/env python3
"""Ownership history of the licences behind the Danish fields.

ENS publishes today's licensees (``danish-licences-and-licensees``) and the
changes to that overview as free text: the "Amendments" page for 2014 onwards
and a PDF for 1999-2013 (copies in ``data/sources/licences/raw/``). There is no
table to parse, so the history is curated by hand in
``data/sources/licences/ownership_history_curated.json``:

* ``entities`` -- every company, with its legal name and group over time, so a
  rename or a takeover of the company does not need a period of its own;
* ``licences`` -- per licence a timeline of periods, each with the shares in
  percent per entity (or, when the source names the owners without shares,
  ``basis: "unknown"`` and an ``owners`` list), how certain the shares are
  (``basis``: stated / derived / unknown) and verbatim quotes from the sources
  (``evidence``);
* ``fields`` -- which licence each field with production data belongs to.

This script does not trust the curated file. It fails with a clear
``SourceFormatError`` and writes nothing unless:

* every evidence quote occurs verbatim in the raw source it names (whitespace
  normalised), so nothing can be attributed to ENS that ENS did not write;
* every period's shares sum to 100 % and refer to known entities, and the
  periods of a licence are in date order;
* the last period of every current licence matches today's licensees in the
  ENS export (``data/sources/licences/danske_lisenser_komplett.xlsx``, the same
  file ``ingest_ownership.py`` reads), by legal name and share.

The output, ``data/ownership_history.json``, splits the periods further at
every rename or change of group, so each output period has one fixed name and
group per owner, and adds the shares per group. Run ``--check`` to validate
without writing.
"""

from __future__ import annotations

import argparse
import re
import unicodedata
from datetime import date
from pathlib import Path
from typing import Any

import common as C

LICENCES_DIR = C.DATA_DIR / "sources" / "licences"
CURATED_PATH = LICENCES_DIR / "ownership_history_curated.json"
RAW_DIR = LICENCES_DIR / "raw"
RAW_FILES = {
    "amendments": RAW_DIR / "amendments.html",
    "earlier": RAW_DIR / "earlier_amendments.pdf",
}
SOURCE_URLS = {
    "amendments": "https://ens.dk/en/energy-sources/amendments",
    "earlier": "https://ens.dk/media/5083/download",
    "licensees": "https://ens.dk/en/energy-sources/danish-licences-and-licensees",
}
CURRENT_XLSX = LICENCES_DIR / "danske_lisenser_komplett.xlsx"
OUTPUT_PATH = C.DATA_DIR / "ownership_history.json"

BASES = ("stated", "derived", "unknown")
SUM_TOLERANCE = 0.001        # percentage points
CURRENT_TOLERANCE = 0.0001   # percentage points, against the export
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# --------------------------------------------------------------------------- #
# Raw sources -> normalised text
# --------------------------------------------------------------------------- #
def normalise(text: str) -> str:
    """Unicode NFC, no-break spaces as spaces, all whitespace runs as one space."""
    text = unicodedata.normalize("NFC", text).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def html_text(path: Path) -> str:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "lxml")
    for tag in soup(["script", "style"]):
        tag.decompose()
    return soup.get_text(" ")


def pdf_text(path: Path) -> str:
    import pdfplumber

    with pdfplumber.open(str(path)) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


def load_sources(files: dict[str, Path] = RAW_FILES) -> dict[str, str]:
    out = {}
    for key, path in files.items():
        if not path.exists():
            raise C.SourceFormatError(f"raw source {key!r} missing: {path}")
        raw = pdf_text(path) if path.suffix.lower() == ".pdf" else html_text(path)
        out[key] = normalise(raw)
    return out


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def _need(cond: bool, msg: str) -> None:
    if not cond:
        raise C.SourceFormatError(msg)


def _as_of(timeline: list, day: str, what: str):
    """The value of a [[from, value, ...], ...] timeline on ``day``."""
    current = None
    for row in timeline:
        if row[0] <= day:
            current = row
    _need(current is not None, f"{what}: nothing defined on {day} (timeline starts {timeline[0][0]})")
    return current


def validate(curated: dict, sources: dict[str, str]) -> None:
    _need(curated.get("schema_version") == 1, "curated file: schema_version must be 1")
    entities = curated.get("entities") or {}
    licences = curated.get("licences") or {}
    _need(entities and licences, "curated file: entities and licences are required")

    for eid, ent in entities.items():
        for key in ("names", "groups"):
            rows = ent.get(key) or []
            _need(rows, f"entity {eid}: {key} is empty")
            days = [r[0] for r in rows]
            _need(all(DATE_RE.match(d) for d in days), f"entity {eid}: {key} has a bad date")
            _need(days == sorted(days) and len(set(days)) == len(days),
                  f"entity {eid}: {key} dates must be strictly increasing")
        for row in ent["names"]:
            _need(len(row) == 3 and row[2] in ("stated", "attested"),
                  f"entity {eid}: name rows are [date, name, 'stated'|'attested']")

    for lid, lic in licences.items():
        periods = lic.get("periods") or []
        _need(periods, f"licence {lid}: no periods")
        starts = [p.get("from") for p in periods]
        _need(all(isinstance(d, str) and DATE_RE.match(d) for d in starts), f"licence {lid}: bad 'from' date")
        _need(starts == sorted(starts) and len(set(starts)) == len(starts),
              f"licence {lid}: periods must be in strictly increasing date order")
        end = lic.get("relinquished")
        _need(end is None or (DATE_RE.match(end) and end > starts[-1]),
              f"licence {lid}: 'relinquished' must be a date after the last period")
        for p in periods:
            where = f"licence {lid}, period from {p['from']}"
            basis = p.get("basis")
            _need(basis in BASES, f"{where}: basis {basis!r} is not one of {BASES}")
            if basis == "unknown":
                owners = p.get("owners") or []
                _need(owners and "shares" not in p, f"{where}: an unknown period lists owners, not shares")
                ids = owners
            else:
                shares = p.get("shares") or {}
                _need(shares and "owners" not in p, f"{where}: a {basis} period gives shares")
                _need(all(isinstance(v, (int, float)) and 0 < v <= 100 for v in shares.values()),
                      f"{where}: shares must be percentages above 0")
                total = sum(shares.values())
                _need(abs(total - 100) <= SUM_TOLERANCE, f"{where}: shares sum to {total:.6f} %, not 100 %")
                ids = list(shares)
            for eid in ids:
                _need(eid in entities, f"{where}: unknown entity {eid!r}")
                _as_of(entities[eid]["names"], p["from"], f"{where}: entity {eid} name")
                _as_of(entities[eid]["groups"], p["from"], f"{where}: entity {eid} group")
            evidence = p.get("evidence") or []
            _need(evidence, f"{where}: no evidence")
            for ev in evidence:
                src, quote = ev.get("src"), ev.get("quote") or ""
                _need(src in sources, f"{where}: evidence names unknown source {src!r}")
                _need(normalise(quote) in sources[src],
                      f"{where}: quote not found verbatim in {src}: {quote[:90]!r}")

    for slug, rows in (curated.get("fields") or {}).items():
        for lid, start, stop in rows:
            _need(lid in licences, f"field {slug}: unknown licence {lid!r}")


def _clean_name(name: str) -> str:
    """The export marks the operator/concessionaire in the name; drop that."""
    return re.sub(r"\s*\((?:Concessionaire|Operator)\)\s*$", "", str(name)).strip()


def current_shares(path: Path = CURRENT_XLSX) -> dict[str, dict[str, float]]:
    """{area: {legal name: share %}} from the licensee export, per 'Område/felt'."""
    import openpyxl

    if not path.exists():
        raise C.SourceFormatError(f"licensee export missing: {path}")
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb["Lisenser per blokk"]
        rows = ws.iter_rows(values_only=True)
        header = [str(h).strip() for h in next(rows)]
        out: dict[str, dict[str, float]] = {}
        for r in rows:
            rec = dict(zip(header, r))
            area, company, share = rec.get("Område/felt"), rec.get("Selskap"), rec.get("Selskapsandel")
            if not area or not company or share is None:
                continue
            out.setdefault(area, {})[_clean_name(company)] = float(share) * 100
        return out
    finally:
        wb.close()


def check_current(curated: dict, current: dict[str, dict[str, float]], today: str) -> None:
    entities = curated["entities"]
    for lid, lic in curated["licences"].items():
        area = lic.get("current_area")
        if area is None:   # relinquished: nothing to compare with
            continue
        _need(area in current, f"licence {lid}: area {area!r} not in the licensee export")
        last = lic["periods"][-1]
        _need(last["basis"] != "unknown", f"licence {lid}: the current period cannot be unknown")
        ours = {_as_of(entities[e]["names"], today, e)[1]: v for e, v in last["shares"].items()}
        theirs = current[area]
        _need(set(ours) == set(theirs),
              f"licence {lid}: today's owners differ from the export: "
              f"curated {sorted(ours)} vs export {sorted(theirs)}")
        for name, v in ours.items():
            _need(abs(v - theirs[name]) <= CURRENT_TOLERANCE,
                  f"licence {lid}: {name} has {v} % in the curated file but {theirs[name]} % in the export")


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #
def _breaks(entities: dict, ids: list[str], start: str, stop: str | None) -> list[str]:
    """Dates inside (start, stop) where any owner's name or group changes."""
    out = set()
    for eid in ids:
        for key in ("names", "groups"):
            for row in entities[eid][key]:
                if start < row[0] and (stop is None or row[0] < stop):
                    out.add(row[0])
    return sorted(out)


def build(curated: dict, today: str) -> dict:
    entities = curated["entities"]
    current_group = {eid: ent["groups"][-1][1] for eid, ent in entities.items()}
    licences_out = {}
    for lid, lic in curated["licences"].items():
        periods = lic["periods"]
        rows = []
        for i, p in enumerate(periods):
            stop = periods[i + 1]["from"] if i + 1 < len(periods) else lic.get("relinquished")
            ids = p.get("owners") or list(p["shares"])
            cuts = [p["from"], *_breaks(entities, ids, p["from"], stop)]
            for j, start in enumerate(cuts):
                end = cuts[j + 1] if j + 1 < len(cuts) else stop
                owners = []
                for eid in ids:
                    name_row = _as_of(entities[eid]["names"], start, eid)
                    owners.append({
                        "entity": eid,
                        "name": name_row[1],
                        "name_basis": name_row[2],
                        "group": _as_of(entities[eid]["groups"], start, eid)[1],
                        "current_group": current_group[eid],
                        "share": None if p["basis"] == "unknown" else round(p["shares"][eid], 6),
                    })
                owners.sort(key=lambda o: (-(o["share"] or 0), o["name"]))
                groups: dict[str, float] = {}
                if p["basis"] != "unknown":
                    for o in owners:
                        groups[o["group"]] = round(groups.get(o["group"], 0.0) + o["share"], 6)
                rows.append({
                    "from": start, "to": end, "basis": p["basis"],
                    "owners": owners,
                    "groups": dict(sorted(groups.items(), key=lambda kv: (-kv[1], kv[0]))) or None,
                    "note": p.get("note"),
                    "evidence": [{"source": SOURCE_URLS[e["src"]], "quote": normalise(e["quote"])}
                                 for e in p["evidence"]],
                })
        licences_out[lid] = {
            "name": lic["name"],
            "licences": lic.get("licences") or [lic["name"].split(" (")[0]],
            "relinquished": lic.get("relinquished"),
            "periods": rows,
        }
    fields_out = {slug: [{"licence": lid, "from": a, "to": b} for lid, a, b in rows]
                  for slug, rows in sorted((curated.get("fields") or {}).items())}
    return {
        "schema_version": C.SCHEMA_VERSION,
        "generated_at": C.utc_now_iso(),
        "as_of": today,
        "sources": SOURCE_URLS,
        "note": ("Ownership per licence over time, curated from ENS' amendment lists (1999 onwards) and "
                 "checked against them; see scripts/ingest_ownership_history.py. Shares in percent. "
                 "basis: stated = given by the source; derived = computed from stated shares and "
                 "transfers (see note); unknown = owners known, shares not. 'group' is the owning group "
                 "at the time, 'current_group' the group the same legal entity belongs to today."),
        "licences": licences_out,
        "fields": fields_out,
    }


def run(curated_path: Path = CURATED_PATH, sources: dict[str, str] | None = None,
        current: dict[str, dict[str, float]] | None = None, today: str | None = None) -> dict:
    curated = C.read_json(curated_path)
    _need(isinstance(curated, dict), f"curated file missing or not JSON: {curated_path}")
    today = today or date.today().isoformat()
    validate(curated, sources if sources is not None else load_sources())
    check_current(curated, current if current is not None else current_shares(), today)
    return build(curated, today)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="validate only; write nothing")
    ap.add_argument("--out", type=Path, default=OUTPUT_PATH, help=f"output file (default: {OUTPUT_PATH})")
    args = ap.parse_args(argv)
    try:
        out = run()
    except C.SourceFormatError as exc:
        C.error(str(exc))
        C.error("Nothing written.")
        return 1
    n = sum(len(l["periods"]) for l in out["licences"].values())
    C.info(f"ownership history OK: {len(out['licences'])} licences, {n} periods, {len(out['fields'])} fields")
    if args.check:
        return 0
    changed = C.write_json_stable(args.out, out, volatile_keys=("generated_at", "as_of"))
    C.info(f"{'wrote' if changed else 'unchanged'} {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

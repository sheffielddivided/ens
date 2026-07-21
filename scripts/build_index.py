#!/usr/bin/env python3
"""Phase 1 -- crawl the ENS landing page and build ``data/sources/index.json``.

The landing page (https://ens.dk/en/energy-sources/monthly-and-yearly-production)
links to:

* one yearly Excel file ("Yearly production, injection, flare, fuel and export
  in SI units"), the authoritative history, and
* one monthly report per month (from ~January 2018) in two unit variants
  ("SI Units" and "Oil Field Units"); we keep only the SI variant.

Monthly reports are HTML pages up to ~2023 and PDF files thereafter. We do NOT
assume where that switch happens -- the format is read from the actual link
(``.pdf`` suffix => pdf, otherwise html).

The resulting index lets later runs diff against what is already known and fetch
only what is new. Re-running merges: previously recorded status / retrieved_at /
error for a URL that is still present is preserved; brand-new URLs start
``pending``.

Run ``python scripts/build_index.py --help`` for options.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse

# Allow "python scripts/build_index.py" from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import common as C  # noqa: E402

# Month name -> number, English and Danish (reports/URLs use either).
_MONTHS = {
    # English
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
    # Danish
    "januar": 1, "februar": 2, "marts": 3, "maj": 5, "juni": 6, "juli": 7,
    "oktober": 10,
    # (april, august, september, november, december share spelling)
}
_MONTH_ALT = {"march": 3, "mar": 3, "sept": 9}
_MONTH_NAMES_RE = re.compile(
    r"\b(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\b", re.I
)
_YEAR_RE = re.compile(r"\b(19[7-9]\d|20\d\d)\b")

# Keyword sets for classification.
_SI_HINTS = ("si unit", "si-unit", "siunit", "si_unit", " si ", "(si", "si)")
_OFU_HINTS = ("oil field unit", "field unit", "field-unit", "ofu")
_YEARLY_HINTS = ("yearly", "annual", "aarlig", "arlig", "årlig", "1972")
_MONTHLY_HINTS = ("production", "produktion", "monthly", "maaned", "måned", "manedlig")


def _classify_format(url: str) -> str:
    path = urlparse(url).path.lower()
    if path.endswith(".pdf"):
        return "pdf"
    if path.endswith((".xlsx", ".xlsm", ".xls")):
        return "xlsx"
    # A link to an ENS content page (no file suffix) is an HTML report.
    return "html"


def _monthly_format(url: str) -> str:
    """Classify a monthly report link.

    ENS serves older reports as ``.htm`` pages and newer ones through
    extension-less ``/media/<id>/download`` URLs (which are PDFs). The exact
    HTML->PDF switch is thus read from the link, not assumed from a date.
    The parser also sniffs the downloaded bytes, so a misclassification here
    self-corrects at parse time.
    """
    path = urlparse(url).path.lower()
    if path.endswith((".htm", ".html")):
        return "html"
    if path.endswith(".pdf"):
        return "pdf"
    if "/media/" in path or "download" in url.lower():
        return "pdf"
    return "html"


def _is_file_link(url: str) -> bool:
    path = urlparse(url).path.lower()
    return (
        path.endswith((".xlsx", ".xlsm", ".xls", ".pdf", ".htm", ".html"))
        or ("/media/" in path and "download" in url.lower())
    )


def _has_si(text: str) -> bool:
    t = f" {text.lower()} "
    return "si unit" in t or " si " in t


def _has_ofu(text: str) -> bool:
    t = text.lower()
    return "oil field" in t or "field unit" in t


def _extract_month_year(*texts: str) -> tuple[int | None, int | None]:
    """Return (year, month) discovered across the given text fragments."""
    blob = " ".join(t for t in texts if t).lower()
    year = None
    m_year = _YEAR_RE.search(blob)
    if m_year:
        year = int(m_year.group(1))
    month = None
    m_month = _MONTH_NAMES_RE.search(blob)
    if m_month:
        month = _MONTHS[m_month.group(1).lower()]
    else:
        for alt, num in _MONTH_ALT.items():
            if re.search(rf"\b{alt}\b", blob):
                month = num
                break
    # Numeric patterns as fallback: "mp202312si" (ENS filenames), "YYYY-MM",
    # and compact "YYYYMM".
    if month is None:
        m = (re.search(r"mp\s*(20\d\d)(0[1-9]|1[0-2])", blob)
             or re.search(r"\b(20\d\d)[-_/.](0?[1-9]|1[0-2])\b", blob)
             or re.search(r"\b(20\d\d)(0[1-9]|1[0-2])\b", blob))
        if m:
            year = int(m.group(1))
            month = int(m.group(2))
    return year, month


def _si_ofu_monthly_anchors(table):
    """Anchors in the SI column of a monthly SI/Oil-Field-Units table.

    Returns [] unless the table has a header row containing *both* an SI and an
    Oil-Field-Units cell (so we do not misfire on unrelated tables). The SI
    column is located by header text and its anchors returned; the OFU column
    is excluded.
    """
    rows = table.find_all("tr")
    for hi, tr in enumerate(rows):
        cells = tr.find_all(["td", "th"], recursive=False)
        texts = [c.get_text(" ", strip=True) for c in cells]
        si_cols = [i for i, t in enumerate(texts) if _has_si(t) and not _has_ofu(t)]
        ofu_cols = [i for i, t in enumerate(texts) if _has_ofu(t)]
        if not (si_cols and ofu_cols):
            continue
        si_col = si_cols[0]
        anchors = []
        for tr2 in rows[hi + 1:]:
            cs = tr2.find_all(["td", "th"], recursive=False)
            if si_col < len(cs):
                anchors.extend(cs[si_col].find_all("a", href=True))
        return anchors
    return []


def _extract_yearly_from_headings(soup, page: str) -> dict | None:
    """Find the yearly Excel via its heading ("Yearly ... in SI units").

    The download link text is a year range (e.g. "1972-2024"), not "yearly",
    so we anchor on the heading and take the first file link after it.
    """
    for h in soup.find_all(re.compile(r"h[1-6]")):
        ht = h.get_text(" ", strip=True).lower()
        if "yearly" in ht and "si" in ht and not _has_ofu(ht):
            for a in h.find_all_next("a", href=True)[:6]:
                url = urljoin(page, a["href"].strip())
                if _is_file_link(url):
                    text = " ".join(a.get_text(" ", strip=True).split())
                    return {"url": url, "text": text or ht, "si": True}
    return None


def _is_si(text: str, url: str) -> bool:
    blob = f" {text} {url} ".lower()
    if any(h in blob for h in _OFU_HINTS):
        return False
    return any(h in blob for h in _SI_HINTS)


def _looks_monthly(text: str, url: str) -> bool:
    blob = f"{text} {url}".lower()
    return any(h in blob for h in _MONTHLY_HINTS)


def crawl(pages: list[str], fetcher: C.Fetcher, force: bool) -> dict:
    """Return a freshly discovered index dict (no merge yet)."""
    import bs4

    yearly_candidates: list[dict] = []
    monthly_by_key: dict[tuple[int, int], dict] = {}
    seen_urls: set[str] = set()

    def add_monthly(url, text, unit_system):
        year, month = _extract_month_year(text, url)
        if year is None or month is None:
            return
        key = (year, month)
        entry = {
            "url": url, "year": year, "month": month,
            "format": _monthly_format(url), "unit_system": unit_system,
            "link_text": text,
        }
        existing = monthly_by_key.get(key)
        if existing is None or (existing["unit_system"] != "si" and unit_system == "si"):
            monthly_by_key[key] = entry
        seen_urls.add(url)

    for page in pages:
        fname = f"page_{C.safe_filename(page)}.html"
        html = fetcher.fetch_text(page, fname, force=force)
        soup = bs4.BeautifulSoup(html, "lxml")
        anchors = soup.find_all("a", href=True)
        C.info(f"scanned {page}: {len(anchors)} links")

        # (A) Real ENS layout: months live in the SI column of an SI/Oil-Field
        # Units table; the yearly Excel is linked from its own heading.
        for table in soup.find_all("table"):
            for a in _si_ofu_monthly_anchors(table):
                url = urljoin(page, a["href"].strip())   # ENS hrefs sometimes have trailing spaces
                text = " ".join(a.get_text(" ", strip=True).split())
                add_monthly(url, text, "si")
        heading_yearly = _extract_yearly_from_headings(soup, page)
        if heading_yearly:
            yearly_candidates.append(heading_yearly)

        # (B) Generic anchor scan (handles simpler layouts / other pages).
        for a in anchors:
            href = a["href"].strip()
            if not href or href.startswith(("#", "mailto:", "javascript:")):
                continue
            url = urljoin(page, href)
            if url in seen_urls:
                continue
            text = " ".join(a.get_text(" ", strip=True).split())
            fmt = _classify_format(url)

            # --- yearly Excel ------------------------------------------------
            if fmt == "xlsx":
                blob = f"{text} {url}".lower()
                if any(h in blob for h in _YEARLY_HINTS) or "production" in blob:
                    is_si = _is_si(text, url) or ("si" in blob and "field" not in blob)
                    yearly_candidates.append(
                        {"url": url, "text": text, "si": is_si}
                    )
                seen_urls.add(url)
                continue

            # --- monthly reports (html or pdf) -------------------------------
            if fmt in ("html", "pdf") and _looks_monthly(text, url):
                year, month = _extract_month_year(text, url)
                if year is None or month is None:
                    continue
                if not _is_si(text, url):
                    # Skip Oil Field Unit variants and anything not marked SI.
                    # HTML report links sometimes omit "SI" in the anchor text;
                    # keep them only if the URL/text does not say "field unit".
                    if any(h in f"{text} {url}".lower() for h in _OFU_HINTS):
                        continue
                    # Ambiguous: accept but mark unit_system unknown so the
                    # parser can confirm from the document itself.
                    unit_system = "unknown"
                else:
                    unit_system = "si"
                key = (year, month)
                entry = {
                    "url": url,
                    "year": year,
                    "month": month,
                    "format": fmt,
                    "unit_system": unit_system,
                    "link_text": text,
                }
                # Prefer an explicit SI link over an ambiguous one for the
                # same month; otherwise keep the first seen.
                existing = monthly_by_key.get(key)
                if existing is None or (
                    existing["unit_system"] != "si" and unit_system == "si"
                ):
                    monthly_by_key[key] = entry
                seen_urls.add(url)

    # Pick the best yearly candidate: prefer SI, then the one whose text most
    # resembles the documented title.
    yearly = _choose_yearly(yearly_candidates)

    monthly = [monthly_by_key[k] for k in sorted(monthly_by_key)]
    return {"yearly": yearly, "monthly": monthly, "yearly_candidates": yearly_candidates}


def _choose_yearly(candidates: list[dict]) -> dict | None:
    if not candidates:
        return None

    def score(c: dict) -> tuple:
        t = c["text"].lower()
        return (
            c["si"],
            "yearly" in t or "annual" in t,
            "injection" in t or "flare" in t or "export" in t,
            "1972" in t,
        )

    best = max(candidates, key=score)
    return {"url": best["url"], "text": best["text"], "format": "xlsx"}


def build_index(
    pages: list[str],
    *,
    offline: bool = False,
    force: bool = False,
    output: Path = C.INDEX_PATH,
) -> dict:
    fetcher = C.Fetcher(offline=offline)
    discovered = crawl(pages, fetcher, force)

    previous = C.read_json(output, default={}) or {}
    prev_monthly = {
        (e["year"], e["month"]): e for e in previous.get("monthly", [])
    }
    prev_yearly = previous.get("yearly") or {}

    # --- yearly entry (merge status) ---------------------------------------
    yearly_entry = None
    if discovered["yearly"]:
        y = discovered["yearly"]
        yearly_entry = {
            "url": y["url"],
            "link_text": y["text"],
            "format": "xlsx",
            "status": "pending",
            "retrieved_at": None,
            "error": None,
            "records": 0,
        }
        if prev_yearly.get("url") == y["url"]:
            for k in ("status", "retrieved_at", "error", "records"):
                if k in prev_yearly:
                    yearly_entry[k] = prev_yearly[k]
    elif prev_yearly:
        C.warn("no yearly Excel link found on this crawl; keeping previous entry")
        yearly_entry = prev_yearly

    # --- monthly entries (merge status) ------------------------------------
    monthly_entries = []
    for e in discovered["monthly"]:
        key = (e["year"], e["month"])
        entry = {
            "url": e["url"],
            "year": e["year"],
            "month": e["month"],
            "format": e["format"],
            "unit_system": e["unit_system"],
            "link_text": e["link_text"],
            "status": "pending",
            "retrieved_at": None,
            "error": None,
            "records": 0,
        }
        prev = prev_monthly.get(key)
        if prev and prev.get("url") == e["url"]:
            for k in ("status", "retrieved_at", "error", "records"):
                if k in prev:
                    entry[k] = prev[k]
        monthly_entries.append(entry)

    monthly_entries.sort(key=lambda e: (e["year"], e["month"]))

    index = {
        "schema_version": C.SCHEMA_VERSION,
        "generated_at": C.utc_now_iso(),
        "source_pages": pages,
        "yearly": yearly_entry,
        "monthly": monthly_entries,
    }
    # Idempotent: if only the timestamp would change, leave the file alone so a
    # re-crawl with no new sources produces no git diff.
    written = C.write_json_stable(output, index, volatile_keys=("generated_at",))
    if not written:
        index = C.read_json(output)  # keep the on-disk timestamps
    _print_summary(index)
    return index


def _print_summary(index: dict) -> None:
    monthly = index["monthly"]
    C.info("=" * 60)
    C.info("index summary")
    if index["yearly"]:
        C.info(f"  yearly Excel : {index['yearly']['url']}")
    else:
        C.warn("  yearly Excel : NOT FOUND")
    C.info(f"  monthly reports found : {len(monthly)}")
    if monthly:
        first = monthly[0]
        last = monthly[-1]
        C.info(f"  first month : {first['year']}-{first['month']:02d} ({first['format']})")
        C.info(f"  last month  : {last['year']}-{last['month']:02d} ({last['format']})")
        by_fmt: dict[str, int] = {}
        by_unit: dict[str, int] = {}
        for e in monthly:
            by_fmt[e["format"]] = by_fmt.get(e["format"], 0) + 1
            by_unit[e["unit_system"]] = by_unit.get(e["unit_system"], 0) + 1
        C.info(f"  by format : {by_fmt}")
        C.info(f"  by unit_system : {by_unit}")
    C.info("=" * 60)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Crawl the ENS landing page and build data/sources/index.json",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "--page", action="append", default=None,
        help="Landing page URL to crawl (repeatable). Defaults to the ENS "
             "monthly-and-yearly-production page.",
    )
    p.add_argument(
        "--output", type=Path, default=C.INDEX_PATH,
        help="Where to write the index JSON.",
    )
    p.add_argument(
        "--offline", action="store_true",
        help="Use only cached pages under data/sources/raw/; never hit the network.",
    )
    p.add_argument(
        "--force", action="store_true",
        help="Re-download the landing page(s) even if cached.",
    )
    args = p.parse_args(argv)

    pages = args.page or [C.ENS_PRODUCTION_PAGE]
    try:
        build_index(pages, offline=args.offline, force=args.force, output=args.output)
    except Exception as exc:  # noqa: BLE001 -- top-level: report and fail loudly
        C.error(f"build_index failed: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

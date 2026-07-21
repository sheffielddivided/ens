"""Shared utilities for the ENS oil/gas/water production pipeline.

Everything that more than one script needs lives here: repository paths, the
polite HTTP client (descriptive User-Agent, <=1 request/second, on-disk cache),
atomic JSON writes, logging helpers and field-name normalisation.

The module has no side effects on import beyond defining constants, so it is
safe to import from tests.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

# --------------------------------------------------------------------------- #
# Repository layout
# --------------------------------------------------------------------------- #
# scripts/common.py -> repo root is two levels up. This keeps every script
# runnable from the repository root regardless of the current working dir.
REPO_ROOT = Path(__file__).resolve().parent.parent

DATA_DIR = REPO_ROOT / "data"
SOURCES_DIR = DATA_DIR / "sources"
RAW_DIR = SOURCES_DIR / "raw"
DOCS_DATA_DIR = REPO_ROOT / "docs" / "data"

INDEX_PATH = SOURCES_DIR / "index.json"
YEARLY_PATH = DATA_DIR / "yearly.json"
MONTHLY_PATH = DATA_DIR / "monthly.json"
FIELDS_PATH = DATA_DIR / "fields.json"
COMBINED_PATH = DATA_DIR / "combined.json"

# The main landing page that lists the yearly Excel file and every monthly
# report. Both language variants exist; the English one is used because the
# task specifies the English "SI Units" reports.
ENS_BASE = "https://ens.dk"
ENS_PRODUCTION_PAGE = "https://ens.dk/en/energy-sources/monthly-and-yearly-production"

# Sent on every outbound request so ENS can identify (and if needed contact)
# the project. Overridable via env for forks.
USER_AGENT = os.environ.get(
    "ENS_USER_AGENT",
    "ens-production-db/1.0 (+https://github.com/sheffielddivided/ens; "
    "automated public-data mirror; contact via repository issues)",
)

# Politeness: never hit the source faster than this.
MIN_REQUEST_INTERVAL_S = float(os.environ.get("ENS_MIN_INTERVAL", "1.0"))

# --------------------------------------------------------------------------- #
# Data-model constants
# --------------------------------------------------------------------------- #
SCHEMA_VERSION = 1

# Canonical SI units used across the datasets. These are the units the ENS
# "SI Units" reports and the yearly SI-unit Excel file are documented to use.
# They are ALSO captured from the source at parse time (see the parsers): if a
# source's own unit strings ever disagree with these, the parser logs a WARN so
# the discrepancy surfaces instead of being silently trusted. Do not treat this
# dict as authoritative on its own -- it is the documented expectation.
UNIT_DEFINITIONS = {
    "oil": "1000 m3",
    "gas": "mio. Nm3",
    "water": "1000 m3",
}

# The three mandatory measures plus optional extras the yearly Excel may carry.
CORE_MEASURES = ("oil", "gas", "water")
OPTIONAL_MEASURES = (
    "gas_injection",
    "water_injection",
    "flare",
    "fuel",
    "gas_export",
    "oil_export",
)

# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #
def _log(level: str, msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"{level} {ts} {msg}", file=sys.stdout, flush=True)


def info(msg: str) -> None:
    _log("INFO", msg)


def warn(msg: str) -> None:
    _log("WARN", msg)


def error(msg: str) -> None:
    _log("ERROR", msg)


class SourceFormatError(RuntimeError):
    """Raised when a source document does not match any known structure.

    The pipeline must fail loudly rather than write dubious data, so parsers
    raise this with a message that describes exactly what was unexpected.
    """


# --------------------------------------------------------------------------- #
# Timestamps
# --------------------------------------------------------------------------- #
def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# Atomic JSON IO
# --------------------------------------------------------------------------- #
def read_json(path: Path | str, default: Any = None) -> Any:
    path = Path(path)
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path: Path | str, data: Any) -> None:
    """Write ``data`` as pretty JSON atomically.

    A partially written data file is worse than none, so we render to a temp
    file in the same directory and ``os.replace`` it into place (atomic on the
    same filesystem). A trailing newline keeps git diffs clean.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _strip_volatile(obj: Any, keys: tuple[str, ...]) -> Any:
    if isinstance(obj, dict):
        return {k: v for k, v in obj.items() if k not in keys}
    return obj


def write_json_stable(path: Path | str, data: Any, volatile_keys: tuple[str, ...] = ()) -> bool:
    """Write ``data`` only if it differs from the file already on disk.

    This is what makes the pipeline idempotent: when a re-run produces the same
    data, the file is left byte-for-byte untouched so git sees no change and no
    commit is made. ``volatile_keys`` names top-level keys (e.g. a run
    timestamp) that must be ignored when deciding whether anything really
    changed. Returns True if the file was written, False if left unchanged.
    """
    path = Path(path)
    old = read_json(path)
    if old is not None and _strip_volatile(old, volatile_keys) == _strip_volatile(data, volatile_keys):
        return False
    write_json(path, data)
    return True


# --------------------------------------------------------------------------- #
# Field-name normalisation
# --------------------------------------------------------------------------- #
# Danish North Sea field names appear in several spellings across the decades
# (case, "Field" suffix, footnote markers, satellite groupings). We normalise
# every raw label to a stable slug and keep the mapping in data/fields.json.
# Footnote markers: asterisks/daggers plus superscript digit codepoints. These
# must be stripped BEFORE Unicode compatibility folding, which would otherwise
# turn "¹" into a plain "1" and glue it onto the field name.
_FOOTNOTE_RE = re.compile("[\\*†‡²³¹⁰-⁹]+")
_PAREN_RE = re.compile(r"\([^)]*\)")
_NONWORD_RE = re.compile(r"[^a-z0-9]+")


def normalize_field(raw: str) -> str:
    """Return a stable slug for a raw field label.

    Examples: "Dan" -> "dan", "Tyra South East" -> "tyra_south_east",
    "Halfdan Field*" -> "halfdan", "Nini/Cecilie" -> "nini_cecilie".
    Returns "" for labels that are clearly not field names (empty / totals),
    letting callers decide what to do.
    """
    if raw is None:
        return ""
    s = str(raw)
    # Strip footnote markers first (before folding superscripts to digits).
    s = _FOOTNOTE_RE.sub("", s)
    # Fold accented characters (æ/ø/å appear in Danish field names) to ASCII.
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = _PAREN_RE.sub(" ", s)
    # Drop a trailing "field" word which appears inconsistently.
    s = re.sub(r"\bfield\b", " ", s)
    s = _NONWORD_RE.sub("_", s).strip("_")
    return s


# Labels that are aggregate rows / non-field rows in the source tables and must
# not be stored as fields.
TOTAL_LABELS = {
    "total", "totalt", "i_alt", "ialt", "sum", "all_fields", "alle_felter",
    "denmark", "danmark", "grand_total",
}


def is_total_label(raw: str) -> bool:
    slug = normalize_field(raw)
    return slug in TOTAL_LABELS


# --------------------------------------------------------------------------- #
# Number parsing (handles Danish/European decimal formatting)
# --------------------------------------------------------------------------- #
def parse_number(value: Any) -> float | None:
    """Parse a numeric cell that may use Danish/European formatting.

    Handles thousands separators ("1.234,5" -> 1234.5), plain floats, blanks,
    and dashes ("-", "n/a") which mean "no value". Returns None when the cell
    carries no number so callers can distinguish absent from zero.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        f = float(value)
        return f
    s = str(value).strip()
    if s == "" or s in {"-", "–", "—", "n/a", "N/A", "na", "NA", "."}:
        return None
    # A numeric data cell never contains letters. Rejecting them stops unit
    # suffixes and titles (e.g. "Nm3", "mio.") from being mined for stray digits.
    if re.search(r"[A-Za-z]", s):
        return None
    # Strip anything that is not a digit, separator or sign.
    s = re.sub(r"[^0-9.,\-+]", "", s)
    if s in {"", "-", "+", ".", ","}:
        return None
    has_comma = "," in s
    has_dot = "." in s
    if has_comma and has_dot:
        # The rightmost separator is the decimal separator.
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")   # European: . thousands, , decimal
        else:
            s = s.replace(",", "")                       # Anglo: , thousands, . decimal
    elif has_comma:
        # Comma only: treat as decimal separator unless it looks like grouping.
        if re.fullmatch(r"[+-]?\d{1,3}(,\d{3})+", s):
            s = s.replace(",", "")
        else:
            s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# Record ordering (deterministic git diffs)
# --------------------------------------------------------------------------- #
def sort_records(records: Iterable[dict], monthly: bool) -> list[dict]:
    def key(r: dict):
        if monthly:
            return (r.get("field", ""), r.get("year", 0), r.get("month", 0))
        return (r.get("field", ""), r.get("year", 0))
    return sorted(records, key=key)


# --------------------------------------------------------------------------- #
# Polite, cached HTTP client
# --------------------------------------------------------------------------- #
class Fetcher:
    """A thin wrapper over ``requests`` that is polite and reproducible.

    * Sends the descriptive project User-Agent.
    * Enforces a minimum interval between network requests.
    * Caches every downloaded body under ``data/sources/raw/`` keyed by a
      caller-supplied filename, so re-runs and tests never re-download.

    Import of ``requests`` is deferred so that pure-parsing code paths and
    tests do not require the network stack.
    """

    def __init__(self, cache_dir: Path | str = RAW_DIR, offline: bool = False):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.offline = offline
        self._last_request = 0.0
        self._session = None

    def _get_session(self):
        if self._session is None:
            import requests  # deferred

            s = requests.Session()
            s.headers.update({"User-Agent": USER_AGENT})
            self._session = s
        return self._session

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < MIN_REQUEST_INTERVAL_S:
            time.sleep(MIN_REQUEST_INTERVAL_S - elapsed)
        self._last_request = time.monotonic()

    def cache_path(self, filename: str) -> Path:
        return self.cache_dir / filename

    def fetch(
        self,
        url: str,
        filename: str,
        *,
        force: bool = False,
        binary: bool = False,
        retries: int = 4,
    ) -> bytes:
        """Return the body for ``url``, using the on-disk cache when possible.

        ``filename`` is the cache key under ``data/sources/raw/``. On a cache
        miss (or ``force``) the URL is downloaded with exponential-backoff
        retries. Raises the last exception if every attempt fails.
        """
        cache_file = self.cache_path(filename)
        if cache_file.exists() and not force:
            return cache_file.read_bytes()
        if self.offline:
            raise SourceFormatError(
                f"offline mode: {url} is not cached at {cache_file}"
            )

        import requests  # deferred

        last_exc: Exception | None = None
        for attempt in range(retries):
            try:
                self._throttle()
                info(f"GET {url}")
                resp = self._get_session().get(url, timeout=60)
                resp.raise_for_status()
                body = resp.content
                cache_file.parent.mkdir(parents=True, exist_ok=True)
                cache_file.write_bytes(body)
                return body
            except requests.RequestException as exc:  # network / HTTP error
                last_exc = exc
                wait = 2 ** (attempt + 1)
                warn(f"request failed ({exc}); retry {attempt + 1}/{retries} in {wait}s")
                time.sleep(wait)
        assert last_exc is not None
        raise last_exc

    def fetch_text(self, url: str, filename: str, **kw) -> str:
        return self.fetch(url, filename, **kw).decode("utf-8", errors="replace")


def safe_filename(url: str) -> str:
    """Derive a filesystem-safe cache filename from a URL, preserving suffix.

    ENS file URLs like ``/media/7167/download`` have a generic last segment, so
    the parent segment (the media id) is folded in to keep names unique.
    """
    parts = [p for p in url.split("?")[0].rstrip("/").split("/") if p]
    last = parts[-1] if parts else "index"
    if last.lower() in {"download", "file", "index"} and len(parts) >= 2:
        last = f"{parts[-2]}_{last}"
    return re.sub(r"[^A-Za-z0-9._-]+", "_", last)

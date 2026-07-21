"""Parser and utility tests -- run entirely against fixtures, never the network.

    python -m pytest

The fixtures in tests/fixtures/ are synthetic (see tests/fixtures/README.md) but
mirror the structure of the real ENS documents, so these tests meaningfully
exercise the parsing logic.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import common as C  # noqa: E402


# --------------------------------------------------------------------------- #
# common.py
# --------------------------------------------------------------------------- #
class TestCommon:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Dan", "dan"),
            ("DAN", "dan"),
            ("Dan Field", "dan"),
            ("Halfdan Field*", "halfdan"),
            ("Tyra South East", "tyra_south_east"),
            ("Nini/Cecilie", "nini_cecilie"),
            ("Syd Arne", "syd_arne"),
            ("Gorm¹", "gorm"),
            ("  Roar  ", "roar"),
        ],
    )
    def test_normalize_field(self, raw, expected):
        assert C.normalize_field(raw) == expected

    def test_is_total_label(self):
        assert C.is_total_label("Total")
        assert C.is_total_label("I alt")
        assert C.is_total_label("TOTAL")
        assert not C.is_total_label("Dan")

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("123.4", 123.4),
            ("1.234,5", 1234.5),     # European grouping + decimal
            ("1,234.5", 1234.5),     # Anglo grouping + decimal
            ("1234,5", 1234.5),      # comma decimal
            ("1,234", 1234.0),       # comma grouping (looks like thousands)
            ("890", 890.0),
            ("-", None),
            ("", None),
            ("n/a", None),
            (None, None),
            (56.7, 56.7),
        ],
    )
    def test_parse_number(self, raw, expected):
        assert C.parse_number(raw) == expected

    def test_sort_records_monthly(self):
        recs = [
            {"field": "gorm", "year": 2020, "month": 3},
            {"field": "dan", "year": 2020, "month": 1},
            {"field": "dan", "year": 2019, "month": 12},
        ]
        out = C.sort_records(recs, monthly=True)
        assert [(r["field"], r["year"], r["month"]) for r in out] == [
            ("dan", 2019, 12), ("dan", 2020, 1), ("gorm", 2020, 3),
        ]

    def test_write_json_atomic(self, tmp_path):
        p = tmp_path / "sub" / "out.json"
        C.write_json(p, {"a": 1})
        assert C.read_json(p) == {"a": 1}
        # No stray temp files left behind.
        assert list((tmp_path / "sub").glob(".*tmp")) == []


# --------------------------------------------------------------------------- #
# build_index.py
# --------------------------------------------------------------------------- #
class TestBuildIndex:
    def _run(self, tmp_path):
        import build_index as B

        page = C.ENS_PRODUCTION_PAGE
        cache = tmp_path / "raw"
        cache.mkdir()
        # Seed the cache with the fixture so --offline finds it.
        (cache / f"page_{C.safe_filename(page)}.html").write_text(
            (FIXTURES / "landing_page.html").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        fetcher = C.Fetcher(cache_dir=cache, offline=True)
        return B.crawl([page], fetcher, force=False)

    def test_picks_si_yearly(self, tmp_path):
        out = self._run(tmp_path)
        assert out["yearly"] is not None
        assert "si" in out["yearly"]["url"].lower()
        assert out["yearly"]["format"] == "xlsx"

    def test_monthly_dedup_and_format(self, tmp_path):
        out = self._run(tmp_path)
        months = {(m["year"], m["month"]): m for m in out["monthly"]}
        # SI variants kept, OFU dropped -> one entry per month.
        assert len(out["monthly"]) == 5
        assert months[(2018, 1)]["format"] == "html"
        assert months[(2024, 1)]["format"] == "pdf"
        for m in out["monthly"]:
            assert m["unit_system"] == "si"

    def test_excludes_unrelated_pdf(self, tmp_path):
        out = self._run(tmp_path)
        urls = [m["url"] for m in out["monthly"]]
        assert not any("energy_statistics" in u for u in urls)

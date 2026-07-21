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
            ("Nm3", None),           # unit suffix must not yield a number
            ("mio. Nm3", None),
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
        assert list((tmp_path / "sub").glob(".*tmp")) == []

    def test_write_json_stable(self, tmp_path):
        p = tmp_path / "out.json"
        assert C.write_json_stable(p, {"a": 1, "ts": "t1"}) is True
        # Same data except a volatile key -> not rewritten.
        assert C.write_json_stable(p, {"a": 1, "ts": "t2"}, volatile_keys=("ts",)) is False
        # Real change -> rewritten.
        assert C.write_json_stable(p, {"a": 2, "ts": "t2"}, volatile_keys=("ts",)) is True


# --------------------------------------------------------------------------- #
# build_index.py
# --------------------------------------------------------------------------- #
class TestBuildIndex:
    def _run(self, tmp_path):
        import build_index as B

        page = C.ENS_PRODUCTION_PAGE
        cache = tmp_path / "raw"
        cache.mkdir()
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
        assert len(out["monthly"]) == 5
        assert months[(2018, 1)]["format"] == "html"
        assert months[(2024, 1)]["format"] == "pdf"
        for m in out["monthly"]:
            assert m["unit_system"] == "si"

    def test_excludes_unrelated_pdf(self, tmp_path):
        out = self._run(tmp_path)
        urls = [m["url"] for m in out["monthly"]]
        assert not any("energy_statistics" in u for u in urls)

    def _run_real(self, tmp_path):
        import build_index as B

        page = C.ENS_PRODUCTION_PAGE
        cache = tmp_path / "raw"
        cache.mkdir()
        (cache / f"page_{C.safe_filename(page)}.html").write_text(
            (FIXTURES / "landing_page_real.html").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        fetcher = C.Fetcher(cache_dir=cache, offline=True)
        return B.crawl([page], fetcher, force=False)

    def test_real_layout_yearly_from_heading(self, tmp_path):
        out = self._run_real(tmp_path)
        # Yearly Excel is the SI one found via its heading (year-range link text,
        # /media/<id>/download URL), not the Oil-Field-Units one.
        assert out["yearly"] is not None
        assert out["yearly"]["url"].endswith("/media/7167/download")

    def test_real_layout_si_column_only(self, tmp_path):
        out = self._run_real(tmp_path)
        months = {(m["year"], m["month"]): m for m in out["monthly"]}
        # Four SI months across both eras; OFU column ids excluded.
        assert set(months) == {(2026, 6), (2024, 1), (2023, 12), (2018, 1)}
        assert all("/media/8674/" not in m["url"] and "/media/6332/" not in m["url"]
                   for m in out["monthly"])

    def test_real_layout_format_detection(self, tmp_path):
        out = self._run_real(tmp_path)
        months = {(m["year"], m["month"]): m for m in out["monthly"]}
        assert months[(2018, 1)]["format"] == "html"   # .htm
        assert months[(2023, 12)]["format"] == "html"  # .htm
        assert months[(2024, 1)]["format"] == "pdf"    # /media/download
        assert months[(2026, 6)]["format"] == "pdf"
        assert all(m["unit_system"] == "si" for m in out["monthly"])


# --------------------------------------------------------------------------- #
# ingest_yearly.py
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def parsed_yearly():
    import ingest_yearly as Y

    xlsx = FIXTURES / "yearly.xlsx"
    if not xlsx.exists():
        import subprocess
        subprocess.run(
            [sys.executable, str(FIXTURES / "make_xlsx_fixture.py")], check=True
        )
    return Y.parse_workbook(xlsx)


class TestYearly:
    def test_classify_measure(self):
        import ingest_yearly as Y

        assert Y.classify_measure("Oil production") == "oil"
        assert Y.classify_measure("Sales gas production") == "gas"
        assert Y.classify_measure("Water production") == "water"
        assert Y.classify_measure("Water injection") == "water_injection"
        assert Y.classify_measure("Gas injection") == "gas_injection"
        assert Y.classify_measure("Flaring") == "flare"
        assert Y.classify_measure("Random sheet") is None

    def test_only_real_fields(self, parsed_yearly):
        records, _ = parsed_yearly
        fields = {r["field"] for r in records}
        assert fields == {"dan", "gorm", "halfdan"}  # no title/total rows leak in

    def test_both_orientations_parsed(self, parsed_yearly):
        records, _ = parsed_yearly
        by_key = {(r["field"], r["year"]): r for r in records}
        dan18 = by_key[("dan", 2018)]
        assert dan18["oil"] == 300.0
        assert dan18["gas"] == 200.0
        assert dan18["water"] == 1200.0
        assert dan18["water_injection"] == 500.0

    def test_footnote_alias_merged(self, parsed_yearly):
        import ingest_yearly as Y

        records, report = parsed_yearly
        fields = Y.build_fields(records, report["raw_labels"])["fields"]
        assert "halfdan" in fields
        assert set(fields["halfdan"]["aliases"]) >= {"Halfdan", "Halfdan*"}

    def test_no_totals_in_records(self, parsed_yearly):
        records, _ = parsed_yearly
        assert all(not C.is_total_label(r["field"]) for r in records)


# --------------------------------------------------------------------------- #
# parse_monthly_html.py
# --------------------------------------------------------------------------- #
class TestMonthlyHTML:
    def _parse(self, name, year, month):
        import parse_monthly_html as H

        html = (FIXTURES / name).read_text(encoding="utf-8")
        return H.parse_html(html, year, month, f"file://{name}")

    def test_v1_english_thead(self):
        recs = self._parse("monthly_v1.html", 2019, 5)
        by = {r["field"]: r for r in recs}
        assert set(by) == {"dan", "gorm", "halfdan"}  # Total row dropped
        assert by["dan"] == {
            "field": "dan", "year": 2019, "month": 5,
            "oil": 123.4, "gas": 56.7, "water": 890.1,
            "preliminary": True, "source_url": "file://monthly_v1.html",
        }

    def test_v2_reordered_danish_colspan(self):
        recs = self._parse("monthly_v2.html", 2022, 12)
        by = {r["field"]: r for r in recs}
        assert set(by) == {"dan", "halfdan", "tyra"}  # "I alt" dropped
        assert by["dan"]["oil"] == 120.0
        assert by["dan"]["gas"] == 60.3
        assert by["dan"]["water"] == 1234.5
        assert by["dan"]["water_injection"] == 400.0

    def test_all_preliminary(self):
        recs = self._parse("monthly_v1.html", 2019, 5)
        assert all(r["preliminary"] is True for r in recs)

    def test_unknown_structure_raises(self):
        import parse_monthly_html as H

        with pytest.raises(C.SourceFormatError):
            H.parse_html("<html><body><p>no tables here</p></body></html>",
                         2020, 1, "x")


# --------------------------------------------------------------------------- #
# monthly_common.py column mapping
# --------------------------------------------------------------------------- #
class TestMonthlyCommon:
    def test_classify_column(self):
        import monthly_common as M

        assert M.classify_column("Oil 1000 m3") == "oil"
        assert M.classify_column("Sales gas mio. Nm3") == "gas"
        assert M.classify_column("Salgsgas") == "gas"
        assert M.classify_column("Water") == "water"
        assert M.classify_column("Gas injection") == "gas_injection"
        assert M.classify_column("Water injection") == "water_injection"
        assert M.classify_column("Vand injektion") == "water_injection"
        assert M.classify_column("Flaring") == "flare"
        assert M.classify_column("Field") is None

    def test_detect_unit_superscript(self):
        import monthly_common as M

        assert M.detect_unit("Olie 1000 m³") == "1000 m3"
        assert M.detect_unit("Salgsgas mio. Nm³") is not None
        assert M.detect_unit("Water") is None  # no stray "t"


# --------------------------------------------------------------------------- #
# parse_monthly_pdf.py
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def pdf_path():
    pdf = FIXTURES / "monthly.pdf"
    if not pdf.exists():
        import subprocess
        subprocess.run(
            [sys.executable, str(FIXTURES / "make_pdf_fixture.py")], check=True
        )
    return pdf


class TestMonthlyPDF:
    def test_parse_pdf(self, pdf_path):
        import parse_monthly_pdf as P

        recs = P.parse_pdf(pdf_path, 2024, 1, "file://monthly.pdf")
        by = {r["field"]: r for r in recs}
        assert set(by) == {"dan", "gorm", "halfdan", "tyra"}  # Total dropped
        assert by["dan"]["oil"] == 118.7
        assert by["dan"]["gas"] == 58.2
        assert by["dan"]["water"] == 905.3
        assert by["dan"]["water_injection"] == 402.1
        assert all(r["preliminary"] is True for r in recs)

    def test_pdf_matches_html_era_shape(self, pdf_path):
        """PDF-era output must be shaped like the HTML era: same keys, same
        measure vocabulary, plausible magnitudes -- the cross-era check."""
        import parse_monthly_pdf as P
        import parse_monthly_html as H

        pdf_recs = P.parse_pdf(pdf_path, 2024, 1, "x")
        html_recs = H.parse_html(
            (FIXTURES / "monthly_v1.html").read_text(encoding="utf-8"),
            2019, 5, "y",
        )
        allowed = set(C.CORE_MEASURES) | set(C.OPTIONAL_MEASURES) | {
            "field", "year", "month", "preliminary", "source_url", "retrieved_at",
        }
        for r in pdf_recs + html_recs:
            assert set(r).issubset(allowed)
            for m in C.CORE_MEASURES:
                if m in r:
                    assert 0 <= r[m] < 100000


# --------------------------------------------------------------------------- #
# update.build_combined + validate  (pure-function logic, no network)
# --------------------------------------------------------------------------- #
def _yearly(records):
    return {"records": records, "unit_definitions": C.UNIT_DEFINITIONS}


def _monthly(records):
    return {"records": records, "unit_definitions": C.UNIT_DEFINITIONS}


class TestCombine:
    def _fields(self):
        return {"fields": {"dan": {"display_name": "Dan"}, "gorm": {"display_name": "Gorm"}}}

    def test_yearly_precedence_and_preliminary_flags(self):
        import update as U

        yearly = _yearly([
            {"field": "dan", "year": 2020, "oil": 300.0, "source_url": "y"},
        ])
        monthly = _monthly([
            {"field": "dan", "year": 2021, "month": 1, "oil": 10.0, "source_url": "m"},
            {"field": "dan", "year": 2021, "month": 2, "oil": 12.0, "source_url": "m"},
        ])
        combined = U.build_combined(yearly, monthly, self._fields())
        dan = combined["series"]["dan"]
        # 2020 comes from the final yearly figure (not preliminary).
        y2020 = [p for p in dan["yearly"]["oil"] if p["t"] == "2020"][0]
        assert y2020 == {"t": "2020", "v": 300.0, "p": False}
        # 2021 has no yearly figure -> aggregated from monthly, preliminary.
        y2021 = [p for p in dan["yearly"]["oil"] if p["t"] == "2021"][0]
        assert y2021["v"] == 22.0 and y2021["p"] is True
        # Monthly resolution points are all preliminary.
        assert all(p["p"] for p in dan["monthly"]["oil"])

    def test_totals_series(self):
        import update as U

        yearly = _yearly([
            {"field": "dan", "year": 2020, "oil": 300.0, "source_url": "y"},
            {"field": "gorm", "year": 2020, "oil": 100.0, "source_url": "y"},
        ])
        combined = U.build_combined(yearly, _monthly([]), self._fields())
        tot = combined["series"][U.TOTAL_SLUG]["yearly"]["oil"]
        assert tot == [{"t": "2020", "v": 400.0, "p": False}]
        # "_total" appears first in the UI field list.
        assert combined["fields"][0]["slug"] == U.TOTAL_SLUG


class TestValidate:
    def test_detects_errors(self, tmp_path, monkeypatch):
        import validate as V

        monkeypatch.setattr(C, "MONTHLY_PATH", tmp_path / "m.json")
        monkeypatch.setattr(C, "YEARLY_PATH", tmp_path / "y.json")
        C.write_json(C.YEARLY_PATH, {"records": [
            {"field": "dan", "year": 2020, "oil": -5.0, "source_url": "y"},   # negative
            {"field": "dan", "year": 2020, "oil": 5.0},                        # dup + no source
        ]})
        C.write_json(C.MONTHLY_PATH, {"records": []})
        rc = V.validate()
        assert rc == 1  # errors present -> non-zero

    def test_clean_dataset_passes(self, tmp_path, monkeypatch):
        import validate as V

        monkeypatch.setattr(C, "MONTHLY_PATH", tmp_path / "m.json")
        monkeypatch.setattr(C, "YEARLY_PATH", tmp_path / "y.json")
        C.write_json(C.YEARLY_PATH, {"records": [
            {"field": "dan", "year": 2020, "oil": 300.0, "source_url": "y"},
        ]})
        C.write_json(C.MONTHLY_PATH, {"records": [
            {"field": "dan", "year": 2020, "month": 1, "oil": 25.0, "source_url": "m"},
        ]})
        assert V.validate() == 0

    def test_monthly_vs_yearly_deviation_warns(self, tmp_path, monkeypatch, capsys):
        import validate as V

        monkeypatch.setattr(C, "MONTHLY_PATH", tmp_path / "m.json")
        monkeypatch.setattr(C, "YEARLY_PATH", tmp_path / "y.json")
        # 12 months summing to 120 vs a yearly figure of 300 -> >10% deviation.
        months = [
            {"field": "dan", "year": 2020, "month": mth, "oil": 10.0, "source_url": "m"}
            for mth in range(1, 13)
        ]
        C.write_json(C.MONTHLY_PATH, {"records": months})
        C.write_json(C.YEARLY_PATH, {"records": [
            {"field": "dan", "year": 2020, "oil": 300.0, "source_url": "y"},
        ]})
        assert V.validate() == 0  # deviation is a WARN, not an ERROR
        assert "monthly-vs-yearly" in capsys.readouterr().out

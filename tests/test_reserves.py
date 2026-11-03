"""Tests for scripts/ingest_reserves.py -- built against an in-memory workbook."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import common as C  # noqa: E402
import ingest_reserves as R  # noqa: E402

openpyxl = pytest.importorskip("openpyxl")


def _make(path: Path, rows: list[tuple], *, sheet: str = R.SHEET, fills: dict | None = None) -> Path:
    from openpyxl.styles import PatternFill

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet
    ws.append(["Reserver per felt", None])
    ws.append(["Kilde: Tabell 1", None])
    ws.append(["Felt", "Råvare", "Enhet", "2025", "2026"])
    for r in rows:
        ws.append(list(r))
    for coord, rgb in (fills or {}).items():
        ws[coord].fill = PatternFill("solid", fgColor=rgb)
    wb.save(path)
    return path


ROWS = [
    ("Dan", "Olje", "mill. m³", 6, 6),
    ("Dan", "Salgsgass", "mrd. Nm³", 1, None),
    ("Tyra (inkl. Tyra Sydøst)", "Olje", "mill. m³", 6, 6),
    ("Tyra (inkl. Tyra Sydøst)", "Salgsgass", "mrd. Nm³", 12, 11),
    (None,),
    ("Sum felt (beregnet)", "Olje", "mill. m³", 12, 12),
    ("Rapportert sum, felt i drift og besluttet (Ressursopgørelse)", "Olje", "mill. m³", 12, 12),
    ("Rapportert sum, felt i drift og besluttet (Ressursopgørelse)", "Salgsgass", "mrd. Nm³", 13, None),
]
KNOWN = {"dan": {}, "tyra": {}, "tyra_se": {}}


def test_conversion_and_mapping(tmp_path):
    out = R.build_reserves(R.parse_sheet(_make(tmp_path / "r.xlsx", ROWS, fills={"D5": "FFFCE4D6"})), KNOWN)
    assert out["years"] == ["2025", "2026"]
    assert set(out["fields"]) == {"dan", "tyra"}
    dan = out["fields"]["dan"]
    # 6 mill. m³ oil × 6.29 + 1 bn. Nm³ gas (= 1 mill. m³ o.e.) × 6.29
    assert dan["mmboe"]["2025"] == pytest.approx(7 * 6.29)
    # blank gas cell stays null, total still uses the oil value
    assert dan["gas_bn_nm3"]["2026"] is None
    assert dan["mmboe"]["2026"] == pytest.approx(6 * 6.29)
    assert dan["flags"] == {"gas": {"2025": "reconstructed"}}
    assert out["fields"]["tyra"]["source_name"] == "Tyra (inkl. Tyra Sydøst)"
    assert out["reported_total"]["mmboe"]["2025"] == pytest.approx(25 * 6.29)
    assert out["reported_total"]["mmboe"]["2026"] is None


def test_unexpected_unit_fails(tmp_path):
    rows = [("Dan", "Olje", "1000 m³", 6, 6), ("Dan", "Salgsgass", "mrd. Nm³", 1, 1)]
    with pytest.raises(C.SourceFormatError, match="unit"):
        R.parse_sheet(_make(tmp_path / "r.xlsx", rows))


def test_missing_sheet_fails(tmp_path):
    with pytest.raises(C.SourceFormatError, match="sheet"):
        R.parse_sheet(_make(tmp_path / "r.xlsx", ROWS, sheet="Annet"))


def test_unknown_field_fails(tmp_path):
    rows = [("Nyfelt", "Olje", "mill. m³", 1, 1), ("Nyfelt", "Salgsgass", "mrd. Nm³", 0, 0)]
    with pytest.raises(C.SourceFormatError, match="fields.json"):
        R.build_reserves(R.parse_sheet(_make(tmp_path / "r.xlsx", rows)), KNOWN)

"""Tests for scripts/ingest_ownership_history.py.

Two parts: the real curated history, validated against the raw ENS sources
checked into data/sources/licences/raw (no network), with spot checks of key
facts; and small synthetic inputs that exercise each validation rule and the
splitting of periods at renames.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import common as C  # noqa: E402
import ingest_ownership_history as H  # noqa: E402

TODAY = "2026-11-15"


# --------------------------------------------------------------------------- #
# The real curated history
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def history():
    return H.run(today=TODAY)


def _at(history, lid, day):
    for p in history["licences"][lid]["periods"]:
        if p["from"] <= day and (p["to"] is None or day < p["to"]):
            return p
    raise AssertionError(f"{lid}: no period on {day}")


def _shares(period):
    return {o["name"]: o["share"] for o in period["owners"]}


def test_every_field_with_production_has_a_licence(history):
    fields = C.read_json(C.FIELDS_PATH)["fields"]
    assert set(history["fields"]) == set(fields)


def test_duc_before_nordsofonden_is_derived_pro_rata(history):
    p = _at(history, "sole_concession", "2010-01-01")
    assert p["basis"] == "derived"
    assert p["groups"] == {"Shell": 46, "A.P. Møller - Mærsk": 39, "Chevron": 15}


def test_duc_today_matches_ownership(history):
    p = _at(history, "sole_concession", TODAY)
    assert p["groups"] == {"TotalEnergies": 43.2, "BlueNord": 36.8, "Nordsøfonden": 20}
    current = C.read_json(C.DATA_DIR / "ownership.json")["fields"]["dan"]
    assert {g: round(v * 100, 6) for g, v in current.items()} == p["groups"]


def test_siri_phillips_split_2001(history):
    p = _at(history, "6/95", "2002-03-01")
    assert p["basis"] == "stated"
    assert _shares(p) == {"Statoil Efterforskning og Produktion A/S": 40,
                          "Paladin Oil Denmark Limited": 25.263,
                          "DONG Efterforskning og Produktion A/S": 23.6185,
                          "DENERCO OIL A/S": 11.1185}


def test_nini_before_2000_is_unknown_with_owners(history):
    p = _at(history, "4/95", "2000-01-01")
    assert p["basis"] == "unknown" and p["groups"] is None
    assert {o["share"] for o in p["owners"]} == {None}
    assert "Mobil Erdgas-Erdöl GmbH" in {o["name"] for o in p["owners"]}


def test_ravn_ends_at_relinquishment(history):
    last = history["licences"]["5/06"]["periods"][-1]
    assert last["to"] == "2025-12-01"
    assert last["groups"] == {"Wintershall": 63.64, "Nordsøfonden": 36.36}


def test_periods_are_contiguous_and_sum_to_100(history):
    for lid, lic in history["licences"].items():
        ps = lic["periods"]
        for a, b in zip(ps, ps[1:]):
            assert a["to"] == b["from"], lid
        for p in ps:
            if p["basis"] != "unknown":
                assert abs(sum(o["share"] for o in p["owners"]) - 100) < 1e-6, (lid, p["from"])


def test_current_group_links_history_to_today(history):
    p = _at(history, "lulita", "2000-01-01")
    by_name = {o["name"]: o for o in p["owners"]}
    assert by_name["DENERCO OIL A/S"]["group"] == "Denerco"
    assert by_name["DENERCO OIL A/S"]["current_group"] == "BlueNord"
    assert by_name["DONG Efterforskning og Produktion A/S"]["current_group"] == "INEOS"


# --------------------------------------------------------------------------- #
# Synthetic inputs: each validation rule
# --------------------------------------------------------------------------- #
SOURCES = {"amendments": H.normalise("Alpha sold 10 % to Beta. Beta was renamed Gamma."),
           "earlier": H.normalise("Licence 1/99 was granted to Alpha (60 %) and Beta (40 %).")}
CURRENT = {"Area": {"Alpha A/S": 50.0, "Gamma A/S": 50.0}}


def _curated():
    return {
        "schema_version": 1,
        "entities": {
            "a": {"names": [["1990-01-01", "Alpha A/S", "stated"]], "groups": [["1990-01-01", "Alpha"]]},
            "b": {"names": [["1990-01-01", "Beta A/S", "stated"], ["2015-06-01", "Gamma A/S", "stated"]],
                  "groups": [["1990-01-01", "Beta"], ["2016-01-01", "Gamma"]]},
        },
        "licences": {
            "1/99": {
                "name": "1/99 (Test)", "current_area": "Area",
                "periods": [
                    {"from": "1999-01-01", "basis": "stated", "shares": {"a": 60, "b": 40},
                     "evidence": [{"src": "earlier", "quote": "granted to Alpha (60 %) and Beta (40 %)"}]},
                    {"from": "2010-01-01", "basis": "stated", "shares": {"a": 50, "b": 50},
                     "evidence": [{"src": "amendments", "quote": "Alpha sold 10 % to Beta."}]},
                ],
            },
        },
        "fields": {"test": [["1/99", None, None]]},
    }


def _run(curated, tmp_path):
    path = tmp_path / "curated.json"
    path.write_text(json.dumps(curated), encoding="utf-8")
    return H.run(path, sources=SOURCES, current=CURRENT, today=TODAY)


def test_synthetic_ok_and_split_at_rename_and_group_change(tmp_path):
    out = _run(_curated(), tmp_path)
    ps = out["licences"]["1/99"]["periods"]
    assert [(p["from"], p["to"]) for p in ps] == [
        ("1999-01-01", "2010-01-01"), ("2010-01-01", "2015-06-01"),
        ("2015-06-01", "2016-01-01"), ("2016-01-01", None)]
    names = [{o["name"]: o["group"] for o in p["owners"]} for p in ps]
    assert names[1]["Beta A/S"] == "Beta"
    assert names[2]["Gamma A/S"] == "Beta"     # renamed, group not yet changed
    assert names[3]["Gamma A/S"] == "Gamma"
    assert ps[3]["groups"] == {"Alpha": 50, "Gamma": 50}


@pytest.mark.parametrize("mutate, message", [
    (lambda c: c["licences"]["1/99"]["periods"][0]["evidence"][0].update(quote="granted to Alpha (65 %)"),
     "quote not found"),
    (lambda c: c["licences"]["1/99"]["periods"][0]["shares"].update(a=61), "sum to"),
    (lambda c: c["licences"]["1/99"]["periods"].reverse(), "increasing date order"),
    (lambda c: c["licences"]["1/99"]["periods"][1]["shares"].update(a=40, b=60), "in the export"),
    (lambda c: c["licences"]["1/99"]["periods"][0].update(basis="guessed"), "basis"),
    (lambda c: c["licences"]["1/99"]["periods"][0]["shares"].update(a=50, x=10), "unknown entity"),
    (lambda c: c["licences"]["1/99"]["periods"][0].update(evidence=[]), "no evidence"),
    (lambda c: c["licences"]["1/99"]["periods"][0].update(basis="unknown"), "lists owners"),
    (lambda c: c["entities"]["a"]["names"].__setitem__(0, ["2000-01-01", "Alpha A/S", "stated"]),
     "nothing defined"),
])
def test_synthetic_validation_errors(tmp_path, mutate, message):
    c = _curated()
    mutate(c)
    with pytest.raises(C.SourceFormatError, match=message):
        _run(c, tmp_path)


def test_unknown_period_has_owners_without_shares(tmp_path):
    c = _curated()
    p0 = c["licences"]["1/99"]["periods"][0]
    p0["basis"] = "unknown"
    p0["owners"] = list(p0.pop("shares"))
    out = _run(c, tmp_path)
    first = out["licences"]["1/99"]["periods"][0]
    assert first["groups"] is None and {o["share"] for o in first["owners"]} == {None}


def test_build_is_deterministic(tmp_path):
    a = _run(_curated(), tmp_path)
    b = _run(copy.deepcopy(_curated()), tmp_path)
    for k in ("generated_at", "as_of"):
        a.pop(k), b.pop(k)
    assert a == b


# --------------------------------------------------------------------------- #
# ingest_ownership.py: fields on relinquished licences use the history
# --------------------------------------------------------------------------- #
def test_relinquished_field_takes_last_owners_from_history(history):
    import ingest_ownership as O

    shares = O.relinquished_shares(history)
    assert shares == {"ravn": {"Wintershall": 0.6364, "Nordsøfonden": 0.3636}}
    assert C.read_json(C.DATA_DIR / "ownership.json")["fields"]["ravn"] == shares["ravn"]


def test_relinquished_without_shares_is_an_error():
    import ingest_ownership as O

    hist = {"licences": {"x": {"relinquished": "2020-01-01",
                               "periods": [{"groups": None}]}},
            "fields": {"f": [{"licence": "x"}]}}
    with pytest.raises(C.SourceFormatError, match="relinquished"):
        O.relinquished_shares(hist)

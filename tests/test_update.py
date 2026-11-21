"""Tests for update.py orchestration helpers (no network)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import update  # noqa: E402


def test_refresh_yearly_bypasses_cache():
    # ENS republishes the yearly file under the same URL, so a refresh must
    # re-download rather than re-parse the committed cached copy.
    assert update.yearly_force(refresh_yearly=True, force=False, offline=False)


def test_refresh_yearly_offline_uses_cache():
    assert not update.yearly_force(refresh_yearly=True, force=False, offline=True)


def test_plain_run_uses_cache():
    assert not update.yearly_force(refresh_yearly=False, force=False, offline=False)


def test_force_still_forces():
    assert update.yearly_force(refresh_yearly=False, force=True, offline=False)
    assert update.yearly_force(refresh_yearly=False, force=True, offline=True)

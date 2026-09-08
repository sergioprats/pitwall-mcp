"""The hand-run report script.

Two things it must never do: spend a request (it only reads SQLite, and the
autouse `no_network` fixture proves it), and leak the VIN into a filename that
ends up in a screenshot or a shared folder.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import FAKE_VIN

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import report as report_script  # noqa: E402

from pitwall_mcp.cardata.errors import MissingConfigError  # noqa: E402
from pitwall_mcp.descriptors import TRAVELLED_DISTANCE  # noqa: E402
from pitwall_mcp.storage.db import Database  # noqa: E402
from pitwall_mcp.storage.history import HistoryStore  # noqa: E402

NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)


@pytest.fixture
def seeded(settings):
    """Settings whose database already holds one reading."""
    with Database(settings.db_path) as database:
        HistoryStore(database).record(
            FAKE_VIN,
            {TRAVELLED_DISTANCE: {"value": "12345", "unit": "km", "timestamp": NOW.isoformat()}},
            moment=NOW,
        )
    return settings


# --- Defaults that protect the VIN -----------------------------------------


def test_the_full_vin_flag_is_off_by_default():
    assert report_script.parse_args([]).full_vin is False


def test_the_default_output_directory_is_the_gitignored_one():
    assert report_script.DEFAULT_OUTPUT_DIR.name == "captures"


def test_the_filename_carries_the_masked_vin_not_the_real_one(seeded, tmp_path):
    path = report_script.generate(seeded, out_dir=tmp_path, now=NOW)

    assert FAKE_VIN not in path.name
    assert "KE01" in path.name


def test_the_full_vin_never_reaches_the_page_unless_asked_for(seeded, tmp_path):
    path = report_script.generate(seeded, out_dir=tmp_path, now=NOW)

    assert FAKE_VIN not in path.read_text(encoding="utf-8")


def test_the_full_vin_flag_puts_it_back_in_the_page(seeded, tmp_path):
    path = report_script.generate(seeded, out_dir=tmp_path, now=NOW, full_vin=True)

    assert FAKE_VIN in path.read_text(encoding="utf-8")


# --- What it produces -------------------------------------------------------


def test_it_writes_a_report_containing_the_stored_reading(seeded, tmp_path):
    path = report_script.generate(seeded, out_dir=tmp_path, now=NOW)

    assert path.suffix == ".html"
    assert "12.345" in path.read_text(encoding="utf-8")


def test_it_refuses_clearly_when_no_vin_is_configured(bare_settings, tmp_path):
    with pytest.raises(MissingConfigError, match="No hay VIN configurado"):
        report_script.generate(bare_settings, out_dir=tmp_path, now=NOW)

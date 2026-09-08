"""The catalogue downloader: where it writes, and that it needs no BMW quota.

The catalogue is not redistributed inside this repository, so this script is
what puts it on disk. It downloads from GitHub, never from the CarData API, and
the autouse `no_network` fixture proves no test here opens a real connection.
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import refresh_catalogue  # noqa: E402


def test_it_downloads_from_github_not_from_bmw():
    """A download that spent quota would be a bug, not a detail."""
    assert refresh_catalogue.CATALOGUE_URL.startswith("https://raw.githubusercontent.com/")
    assert "bmwgroup" not in refresh_catalogue.CATALOGUE_URL


def test_the_default_destination_is_the_gitignored_spec_directory():
    target = refresh_catalogue.resolve_target(None)

    assert target.parent.name == "spec"
    assert target.name == "telematic_catalogue.json"


def test_out_sends_it_somewhere_else(tmp_path):
    """Installed users keep it next to the database, not in a checkout."""
    elsewhere = tmp_path / "catalogo.json"

    assert refresh_catalogue.resolve_target(str(elsewhere)) == elsewhere

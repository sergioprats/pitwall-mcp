"""Downloading the catalogue from inside the installed package.

The repository does not redistribute BMW's catalogue, so a user who installs
from PyPI has neither the file nor `scripts/`. This is how they get it, and the
two things it must never do are spend CarData quota and overwrite a good
catalogue with whatever a proxy or a captive portal answered.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pitwall_mcp import fetch
from pitwall_mcp.cardata.errors import PitwallError

CATALOGUE = {
    "categories": [
        {
            "category": "VEHICLE DATA",
            "entries": [{"name": "Mileage", "technical_descriptor": "vehicle.vehicle.x"}],
        }
    ]
}


def opener_returning(body: str):
    """A stand-in downloader, so no test ever opens a connection."""

    def opener(url: str) -> str:
        return body

    return opener


# --- It costs no quota -----------------------------------------------------


def test_it_downloads_from_github_and_never_from_bmw():
    """A fetch that spent a request out of the daily 50 would be a bug."""
    assert fetch.CATALOGUE_URL.startswith("https://raw.githubusercontent.com/")
    assert "bmwgroup" not in fetch.CATALOGUE_URL


# --- Where it lands --------------------------------------------------------


def test_it_writes_where_the_server_looks_for_it(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    written = fetch.fetch_catalogue(opener=opener_returning(json.dumps(CATALOGUE)))

    assert written == tmp_path / ".local" / "share" / "pitwall-mcp" / "telematic_catalogue.json"
    assert json.loads(written.read_text(encoding="utf-8")) == CATALOGUE


def test_it_creates_the_directory_when_it_does_not_exist(tmp_path):
    target = tmp_path / "nueva" / "carpeta" / "catalogo.json"

    fetch.fetch_catalogue(target, opener=opener_returning(json.dumps(CATALOGUE)))

    assert target.is_file()


# --- What it refuses to write ----------------------------------------------


def test_a_response_that_is_not_a_catalogue_is_refused(tmp_path):
    """A captive portal answers 200 with HTML. That is not a catalogue."""
    target = tmp_path / "catalogo.json"

    with pytest.raises(PitwallError, match="no parece el catalogo"):
        fetch.fetch_catalogue(target, opener=opener_returning("<html>inicia sesion</html>"))


def test_rubbish_never_overwrites_a_catalogue_that_already_works(tmp_path):
    """Losing a good catalogue to a bad download would be the worst outcome."""
    target = tmp_path / "catalogo.json"
    target.write_text(json.dumps(CATALOGUE), encoding="utf-8")

    with pytest.raises(PitwallError):
        fetch.fetch_catalogue(target, opener=opener_returning('{"otra": "cosa"}'))

    assert json.loads(target.read_text(encoding="utf-8")) == CATALOGUE


def test_an_empty_catalogue_is_refused_too(tmp_path):
    """`{"categories": []}` parses fine and is still useless."""
    target = tmp_path / "catalogo.json"

    with pytest.raises(PitwallError, match="no parece el catalogo"):
        fetch.fetch_catalogue(target, opener=opener_returning('{"categories": []}'))

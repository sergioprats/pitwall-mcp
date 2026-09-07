"""The bootstrap script must send nothing unless explicitly told to."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import bootstrap_containers as bootstrap  # noqa: E402

from pitwall_mcp import descriptors as D  # noqa: E402


@pytest.fixture(autouse=True)
def explode_on_any_client(monkeypatch):
    """Any attempt to build an API client during a dry run fails the test."""

    def explode(*args, **kwargs):
        raise AssertionError("El modo seco no puede construir un cliente de la API")

    monkeypatch.setattr(bootstrap, "CarDataClient", explode)


def test_dry_run_is_the_default(capsys, monkeypatch, settings):
    """Running with no flags sends nothing."""
    monkeypatch.setattr(bootstrap, "load_settings", lambda: settings)
    import asyncio

    assert asyncio.run(bootstrap.main([])) == 0
    output = capsys.readouterr().out
    assert "MODO SECO" in output
    assert "no se envia NADA" in output
    assert "Cuota gastada: 0" in output


def test_dry_run_prints_the_exact_payload(capsys, monkeypatch, settings):
    """What you see is literally what would be POSTed."""
    monkeypatch.setattr(bootstrap, "load_settings", lambda: settings)
    import asyncio

    asyncio.run(bootstrap.main(["--dry-run"]))
    output = capsys.readouterr().out

    assert "POST https://api-cardata.bmwgroup.com/customers/containers" in output
    assert "x-version: v1" in output
    assert f'"name": "{D.CONTAINER_NAME}"' in output
    for descriptor in D.CONTAINER_DESCRIPTORS:
        assert descriptor in output


def test_the_request_carries_the_32_container_descriptors():
    """The payload is built from the confirmed list, not from anything else."""
    request = bootstrap.build_request()
    assert request.name == D.CONTAINER_NAME
    assert len(request.technical_descriptors) == 32
    assert D.TYRE_DIAGNOSIS not in request.technical_descriptors


def test_the_diagnosis_descriptor_can_be_added_on_purpose():
    """The flag exists so the swagger's warning can be tested empirically."""
    request = bootstrap.build_request(include_diagnosis=True)
    assert D.TYRE_DIAGNOSIS in request.technical_descriptors
    assert len(request.technical_descriptors) == 33


def test_every_descriptor_is_verified_against_the_catalogue(settings):
    """Nothing leaves this script without existing in the catalogue."""
    assert bootstrap.verify_against_catalogue(settings, bootstrap.build_request()) == []


def test_an_invented_descriptor_blocks_the_send(capsys, settings):
    """A descriptor outside the catalogue stops the dry run with an error."""
    request = bootstrap.build_request()
    request.technical_descriptors.append("vehicle.software.version")
    unknown = bootstrap.verify_against_catalogue(settings, request)

    assert unknown == ["vehicle.software.version"]
    assert bootstrap.print_dry_run(request, unknown) == 1
    assert "NO estan en el catalogo" in capsys.readouterr().out


def test_dry_run_warns_about_the_unknown_container_size_limit(capsys, monkeypatch, settings):
    """If BMW rejects the size, the quota budget doubles. Say it in advance."""
    monkeypatch.setattr(bootstrap, "load_settings", lambda: settings)
    import asyncio

    asyncio.run(bootstrap.main([]))
    output = capsys.readouterr().out
    assert "partirlo en dos" in output
    assert "2 peticiones" in output

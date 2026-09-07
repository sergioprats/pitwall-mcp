"""Shared test fixtures.

NO TEST TOUCHES THE NETWORK. Every response comes from `tests/fixtures/`, hand
written from the schemas in `spec/swagger-customer-api-v1.json`. A suite that
spends quota is a bug (CLAUDE.md, rule 8).

`no_network` is autouse: it replaces `aiohttp.ClientSession` with something that
raises, so an accidental real request fails loudly instead of quietly costing a
request out of 50.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from pitwall_mcp.catalogue import Catalogue
from pitwall_mcp.config import Settings
from pitwall_mcp.storage.db import Database

FIXTURES = Path(__file__).parent / "fixtures"
REPO_ROOT = Path(__file__).resolve().parents[1]
CATALOGUE_PATH = REPO_ROOT / "spec" / "telematic_catalogue.json"

#: The VIN used across every fixture. Obviously fake, never a real vehicle.
FAKE_VIN = "WBAU11030P0FAKE01"
FAKE_CONTAINER = "11111111-2222-3333-4444-555555555555"


def load_fixture(name: str):
    """Read one fixture file and return the parsed JSON."""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Make any real HTTP attempt fail immediately, in every test."""

    def explode(*args, **kwargs):
        raise AssertionError(
            "Un test ha intentado abrir una conexion real. Ningun test puede "
            "gastar cuota de la API."
        )

    monkeypatch.setattr("aiohttp.ClientSession.__init__", explode)


@pytest.fixture
def db() -> Database:
    """An in-memory database with the schema applied."""
    database = Database(":memory:")
    yield database
    database.close()


@pytest.fixture
def catalogue() -> Catalogue:
    """The real catalogue, loaded from the repository copy."""
    return Catalogue.load(CATALOGUE_PATH)


@pytest.fixture
def settings(tmp_path) -> Settings:
    """Settings pointing at temporary paths, with a VIN and a container."""
    return Settings(
        client_id="test-client-id",
        vin=FAKE_VIN,
        container_id=FAKE_CONTAINER,
        token_file=tmp_path / "tokens.json",
        db_path=tmp_path / "pitwall.db",
        daily_quota=20,
        log_level="INFO",
        catalogue_path=CATALOGUE_PATH,
    )


@pytest.fixture
def bare_settings(tmp_path) -> Settings:
    """Settings with nothing configured: no client id, no VIN, no container."""
    return Settings(
        client_id=None,
        vin=None,
        container_id=None,
        token_file=tmp_path / "tokens.json",
        db_path=tmp_path / "pitwall.db",
        daily_quota=20,
        log_level="INFO",
        catalogue_path=CATALOGUE_PATH,
    )


class FakeTokens:
    """Stands in for `TokenManager`: always ready, never refreshes anything."""

    def __init__(self, warning: str | None = None) -> None:
        """Optionally carry a refresh-token warning to be echoed by the adapter."""
        self.warning = warning
        self.gcid = "gcid-de-prueba"

    def require_credentials(self):
        """Pretend the credentials are in place."""
        return SimpleNamespace(access_token="token-de-prueba")

    async def access_token(self) -> str:
        """Return a fake access token."""
        return "token-de-prueba"

    def refresh_warning(self, now=None) -> str | None:
        """Return the configured warning, if any."""
        return self.warning


class FakeResponse:
    """Mimics the library DTOs: what the adapter uses is `raw_data`."""

    def __init__(self, raw_data) -> None:
        """Wrap a raw payload."""
        self.raw_data = raw_data


class FakeCarDataClient:
    """Async stand-in for `bmw_cardata.CarDataClient`, serving fixtures.

    `calls` records every method invoked, so a test can assert that a cache hit
    really did NOT reach this class.
    """

    #: Set by each test: what the next call should return, or an exception to raise.
    responses: dict = {}
    calls: list = []

    def __init__(self, *args, **kwargs) -> None:
        """Accept the same arguments as the real client and ignore them."""

    async def __aenter__(self):
        """Enter the async context."""
        return self

    async def __aexit__(self, *args) -> None:
        """Leave the async context."""

    def _result(self, name: str):
        """Return the configured payload, or raise the configured exception."""
        FakeCarDataClient.calls.append(name)
        value = FakeCarDataClient.responses[name]
        if isinstance(value, Exception):
            raise value
        return value

    async def get_mappings(self):
        """Return mapping DTO stand-ins."""
        payload = self._result("get_mappings")
        items = payload if isinstance(payload, list) else [payload]
        return [FakeResponse(item) for item in items]

    async def get_basic_data(self, vin: str):
        """Return the basic-data DTO stand-in."""
        return FakeResponse(self._result("get_basic_data"))

    async def get_telematic_data(self, vin: str, container_id: str):
        """Return the telematic DTO stand-in."""
        return FakeResponse(self._result("get_telematic_data"))

    async def get_smart_maintenance_tyre_diagnosis(self, vin: str):
        """Return the tyre-diagnosis DTO stand-in."""
        return FakeResponse(self._result("get_smart_maintenance_tyre_diagnosis"))

    async def list_containers(self):
        """Return the container listing DTO stand-in."""
        return FakeResponse(self._result("list_containers"))


@pytest.fixture
def fake_client(monkeypatch):
    """Install `FakeCarDataClient` in place of the real one, and reset it."""
    FakeCarDataClient.responses = {}
    FakeCarDataClient.calls = []
    monkeypatch.setattr("pitwall_mcp.cardata.client.CarDataClient", FakeCarDataClient)
    return FakeCarDataClient

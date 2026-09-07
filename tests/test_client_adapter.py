"""The adapter: cache first, quota always, provenance on everything.

These tests are the ones that hold CLAUDE.md rule 3 in place. They use a fake
client injected in place of `bmw_cardata.CarDataClient`; nothing reaches the
network, and `conftest.no_network` would fail the test if it tried.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from bmw_cardata.exceptions import CarDataHTTPError
from conftest import FAKE_CONTAINER, FAKE_VIN, FakeTokens, load_fixture

from pitwall_mcp.cardata import errors
from pitwall_mcp.cardata.client import (
    ENDPOINT_MAPPINGS,
    CarDataAdapter,
)
from pitwall_mcp.descriptors import BATTERY_VOLTAGE, TRAVELLED_DISTANCE
from pitwall_mcp.storage.db import Database


@pytest.fixture
def adapter(settings, db, fake_client):
    """An adapter wired to the in-memory database and the fake client."""
    return CarDataAdapter(settings, db=db, tokens=FakeTokens())


async def test_first_call_hits_the_api_and_counts_one_request(adapter, fake_client):
    """A cold cache spends exactly one request, and logs it."""
    fake_client.responses["get_mappings"] = load_fixture("mappings_list.json")
    result = await adapter.list_vehicles()

    assert result.source == "api"
    assert result.payload[0]["vin"] == FAKE_VIN
    assert fake_client.calls == ["get_mappings"]
    assert adapter.quota.status().used == 1


async def test_second_call_is_served_from_cache_without_touching_the_api(
    adapter, fake_client
):
    """The whole point of the cache: the second call spends nothing."""
    fake_client.responses["get_mappings"] = load_fixture("mappings_list.json")
    await adapter.list_vehicles()
    fake_client.calls.clear()

    result = await adapter.list_vehicles()

    assert result.source == "cache"
    assert fake_client.calls == []
    assert adapter.quota.status().used == 1


async def test_the_swagger_shape_of_mappings_is_accepted_too(adapter, fake_client):
    """The spec types mappings as one object; the live API returns an array."""
    fake_client.responses["get_mappings"] = load_fixture("mappings_single_object.json")
    result = await adapter.list_vehicles()
    assert [item["vin"] for item in result.payload] == [FAKE_VIN]


async def test_quota_blocks_the_request_when_the_cap_is_reached(settings, db, fake_client):
    """With no cache and no quota, nothing is sent and the error explains why."""
    tight = CarDataAdapter(
        settings.__class__(**{**settings.__dict__, "daily_quota": 1}),
        db=db,
        tokens=FakeTokens(),
    )
    tight.quota.record("mappings", http_status=200)
    fake_client.responses["get_mappings"] = load_fixture("mappings_list.json")

    with pytest.raises(errors.QuotaExhaustedError) as excinfo:
        await tight.list_vehicles()

    assert fake_client.calls == []
    assert "Cuota agotada" in excinfo.value.message


async def test_stale_cache_is_served_when_the_quota_is_gone(adapter, fake_client):
    """Better an old reading, clearly labelled, than nothing."""
    fake_client.responses["get_mappings"] = load_fixture("mappings_list.json")
    await adapter.list_vehicles()

    # Age the cached row past its TTL, then exhaust the quota.
    entry = adapter.cached(ENDPOINT_MAPPINGS)
    adapter._cache.put(  # noqa: SLF001 - reaching in is the point of the test
        ENDPOINT_MAPPINGS,
        entry.payload,
        ttl=timedelta(seconds=-1),
    )
    for _ in range(adapter.quota.daily_limit):
        adapter.quota.record("telematicData", http_status=200)
    fake_client.calls.clear()

    result = await adapter.list_vehicles()

    assert result.stale
    assert result.source == "cache"
    assert fake_client.calls == []
    assert "caducado" in result.provenance()


async def test_telematic_data_is_appended_to_the_history(adapter, fake_client):
    """The series is the product; every API read feeds it."""
    fake_client.responses["get_telematic_data"] = load_fixture("telematic_full.json")
    await adapter.get_telematic_data(FAKE_VIN, FAKE_CONTAINER)

    series = adapter.history.series(FAKE_VIN, TRAVELLED_DISTANCE)
    assert [reading.value for reading in series] == ["18342.0"]
    assert series[0].source == "rest"


async def test_a_cache_hit_does_not_duplicate_history(adapter, fake_client):
    """Serving from cache must not fabricate a second data point."""
    fake_client.responses["get_telematic_data"] = load_fixture("telematic_full.json")
    await adapter.get_telematic_data(FAKE_VIN, FAKE_CONTAINER)
    await adapter.get_telematic_data(FAKE_VIN, FAKE_CONTAINER)

    assert len(adapter.history.series(FAKE_VIN, BATTERY_VOLTAGE)) == 1


async def test_a_partial_container_is_not_padded(adapter, fake_client):
    """Fewer keys than asked for stays fewer keys. Nothing is invented."""
    fake_client.responses["get_telematic_data"] = load_fixture("telematic_partial.json")
    result = await adapter.get_telematic_data(FAKE_VIN, FAKE_CONTAINER)

    returned = result.payload["telematicData"]
    assert len(returned) == 4
    assert BATTERY_VOLTAGE in returned
    assert "vehicle.status.conditionBasedServices" not in returned


async def test_a_na_pressure_survives_the_round_trip(adapter, fake_client):
    """`-NA-` is stored and returned as text, never coerced to a number."""
    fake_client.responses["get_telematic_data"] = load_fixture("telematic_na_pressure.json")
    result = await adapter.get_telematic_data(FAKE_VIN, FAKE_CONTAINER)

    wheel = result.payload["telematicData"][
        "vehicle.chassis.axle.row2.wheel.right.tire.pressure"
    ]
    assert wheel["value"] == "-NA-"


async def test_a_rejected_request_still_counts_against_the_quota(adapter, fake_client):
    """BMW counted it, so we count it."""
    payload = load_fixture("error_403_cu429.json")
    fake_client.responses["get_mappings"] = CarDataHTTPError(
        status=403,
        message=payload["exveErrorMsg"],
        error_id=payload["exveErrorId"],
    )

    with pytest.raises(errors.QuotaExhaustedError):
        await adapter.list_vehicles()

    status = adapter.quota.status()
    assert status.used == 1
    assert status.remote_denied


async def test_a_failed_request_is_not_cached(adapter, fake_client):
    """An error must never become a cached answer."""
    fake_client.responses["get_basic_data"] = CarDataHTTPError(
        status=500, message="server error"
    )
    with pytest.raises(errors.CarDataUnavailableError):
        await adapter.get_basic_data(FAKE_VIN)
    assert adapter.cached("basicData", vin=FAKE_VIN) is None


async def test_provenance_is_present_on_every_result(adapter, fake_client):
    """Rule 4: source plus both timestamps, always."""
    fake_client.responses["get_basic_data"] = load_fixture("basic_data.json")
    result = await adapter.get_basic_data(FAKE_VIN)
    line = result.provenance(source_timestamp="2026-09-05T07:00:00Z")
    assert "peticion a la API de BMW" in line
    assert "Fecha del dato (BMW)" in line
    assert "Lectura realizada" in line


async def test_refresh_token_warning_reaches_the_answer(settings, db, fake_client):
    """A refresh token about to expire must be visible, not buried in a log."""
    warned = CarDataAdapter(
        settings, db=db, tokens=FakeTokens(warning="AVISO: quedan menos de 3 dias")
    )
    fake_client.responses["get_mappings"] = load_fixture("mappings_list.json")
    result = await warned.list_vehicles()
    assert "menos de 3 dias" in result.provenance()


async def test_ttls_match_the_documented_budget(adapter, fake_client):
    """12 h on the container means at most two reads a day."""
    fake_client.responses["get_telematic_data"] = load_fixture("telematic_full.json")
    result = await adapter.get_telematic_data(FAKE_VIN, FAKE_CONTAINER)
    assert result.expires_at - result.fetched_at == timedelta(hours=12)

    fake_client.responses["get_basic_data"] = load_fixture("basic_data.json")
    basic = await adapter.get_basic_data(FAKE_VIN)
    assert basic.expires_at - basic.fetched_at == timedelta(days=30)


async def test_the_adapter_exposes_no_way_to_write_to_the_vehicle(adapter):
    """Read-only by construction: no create, delete, send or command."""
    forbidden = ("create", "delete", "post", "send", "command", "activate", "lock")
    public = [name for name in dir(adapter) if not name.startswith("_")]
    assert not [name for name in public if any(word in name.lower() for word in forbidden)]


def test_the_database_carries_the_phase_two_tables(db: Database):
    """`stream_state` and `readings.source` exist from day one, unused."""
    tables = {
        row[0]
        for row in db.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert {"api_cache", "quota_log", "readings", "stream_state"} <= tables
    columns = {
        row[1] for row in db.connection.execute("PRAGMA table_info(readings)")
    }
    assert "source" in columns

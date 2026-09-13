"""`get_vehicle_status` and `get_maintenance_summary` against the real answer.

The point of these tests is not that the text is pretty. It is that the text
never claims more than the data supports: an empty value must not read as a
zero, a missing CBS breakdown must be admitted, and the count discrepancy must
survive all the way to the user.
"""

from __future__ import annotations

import pytest
from conftest import FakeTokens, load_fixture

from pitwall_mcp.cardata.client import CarDataAdapter
from pitwall_mcp.catalogue import Catalogue
from pitwall_mcp.tools import diagnosis_tools, telematic_tools


@pytest.fixture
def adapter(ready_settings, db, fake_client):
    """An adapter serving the recorded real response."""
    fake_client.responses["get_telematic_data"] = load_fixture("telematic_real.json")
    return CarDataAdapter(ready_settings, db=db, tokens=FakeTokens())


@pytest.fixture
def brake_adapter(ready_settings, db, fake_client):
    """An adapter serving the 2026-09-13 answer: brakes PENDING, one CCM."""
    fake_client.responses["get_telematic_data"] = load_fixture("telematic_brake_warning.json")
    return CarDataAdapter(ready_settings, db=db, tokens=FakeTokens())


@pytest.fixture
def catalogue_obj(catalogue) -> Catalogue:
    """The real catalogue."""
    return catalogue


# --- Check Control and the most urgent CBS item ----------------------------


async def test_status_shows_the_check_control_message(
    brake_adapter, ready_settings, catalogue_obj
):
    text = await telematic_tools.get_vehicle_status(brake_adapter, ready_settings, catalogue_obj)

    assert "The brake pads need to be replaced." in text
    assert "48.376 km" in text


async def test_summary_shows_the_check_control_message(
    brake_adapter, ready_settings, catalogue_obj
):
    text = await diagnosis_tools.get_maintenance_summary(
        brake_adapter, ready_settings, catalogue_obj
    )

    assert "The brake pads need to be replaced." in text


async def test_an_empty_check_control_is_not_read_as_all_clear(
    adapter, ready_settings, catalogue_obj
):
    """Empty coincided with no warnings once; BMW never said that is what it means."""
    text = await telematic_tools.get_vehicle_status(adapter, ready_settings, catalogue_obj)

    check_control = next(
        (line for line in text.splitlines() if line.startswith("Check Control")), ""
    )
    assert check_control
    assert "vacio" in check_control
    assert "ningun aviso" not in check_control


async def test_a_pending_item_is_not_hidden_behind_the_global_figure(
    brake_adapter, ready_settings, catalogue_obj
):
    """serviceDistance.next said 13560 km while the front brakes were PENDING at 1900."""
    for tool in (telematic_tools.get_vehicle_status, diagnosis_tools.get_maintenance_summary):
        text = await tool(brake_adapter, ready_settings, catalogue_obj)

        warning = next((line for line in text.splitlines() if "OJO" in line), "")
        assert "Frenos delanteros [PENDING]" in warning
        assert "1.900 km" in warning


async def test_no_urgency_warning_when_every_item_is_ok(adapter, ready_settings, catalogue_obj):
    text = await telematic_tools.get_vehicle_status(adapter, ready_settings, catalogue_obj)

    assert "OJO" not in text


# --- get_vehicle_status ----------------------------------------------------


async def test_status_reports_the_real_mileage(adapter, ready_settings, catalogue_obj):
    """48260 km, formatted for a human, not as a bare string."""
    text = await telematic_tools.get_vehicle_status(adapter, ready_settings, catalogue_obj)
    assert "48.260 km" in text


async def test_status_breaks_cbs_down_by_item(adapter, ready_settings, catalogue_obj):
    """The whole point of resolving risk number one."""
    text = await telematic_tools.get_vehicle_status(adapter, ready_settings, catalogue_obj)

    assert "Partidas CBS: 5" in text
    assert "Aceite de motor" in text
    assert "Frenos delanteros" in text
    assert "Inspeccion tecnica (ITV)" in text
    assert "14.000 km" in text


async def test_status_warns_when_the_service_threshold_is_close(
    adapter, ready_settings, catalogue_obj
):
    """2140 km left against a 2000 km warning threshold: 140 km of margin."""
    text = await telematic_tools.get_vehicle_status(adapter, ready_settings, catalogue_obj)
    assert "Proximo servicio en: 2140 km" in text
    assert "umbral de preaviso: 2000 km" in text
    assert "faltan 140" in text


async def test_status_carries_the_count_discrepancy_to_the_user(
    adapter, ready_settings, catalogue_obj
):
    """BMW says 9, the array has 5. Neither number is quietly dropped."""
    text = await telematic_tools.get_vehicle_status(adapter, ready_settings, catalogue_obj)

    assert "AVISO" in text
    assert "dice 9" in text
    assert "5 partidas" in text
    assert "no se explica aqui" in text


async def test_status_lists_what_came_back_empty(adapter, ready_settings, catalogue_obj):
    """11 descriptors arrived without a value, and the user is told which."""
    text = await telematic_tools.get_vehicle_status(adapter, ready_settings, catalogue_obj)

    assert "Sin lectura (11 de 32)" in text
    assert "vehicle.vehicle.deepSleepModeActive" in text
    assert "tampoco es un cero" in text


async def test_status_states_its_provenance_and_its_oldest_datum(
    adapter, ready_settings, catalogue_obj
):
    """Rule 4, and the "oldest datum used" the summary promises."""
    text = await telematic_tools.get_vehicle_status(adapter, ready_settings, catalogue_obj)

    assert "Procedencia:" in text
    assert "Fecha del dato (BMW)" in text
    assert "Dato mas antiguo utilizado:" in text


async def test_status_falls_back_to_the_global_figure_without_cbs(
    ready_settings, db, fake_client, catalogue_obj
):
    """No breakdown means saying so, not inventing partial items."""
    fake_client.responses["get_telematic_data"] = load_fixture("telematic_partial.json")
    adapter = CarDataAdapter(ready_settings, db=db, tokens=FakeTokens())

    text = await telematic_tools.get_vehicle_status(adapter, ready_settings, catalogue_obj)

    assert "Desglose CBS: no disponible" in text
    assert "serviceDistance.next" in text
    assert "Partidas CBS" not in text


# --- get_maintenance_summary -----------------------------------------------


async def test_summary_shows_pressures_in_bar_against_target(
    adapter, ready_settings, catalogue_obj
):
    """240 kPa against 250 is 2.40 bar and -0.10, not "240"."""
    text = await diagnosis_tools.get_maintenance_summary(adapter, ready_settings, catalogue_obj)

    assert "2.40 bar" in text
    assert "objetivo 2.50 bar" in text
    assert "-0.10 bar" in text
    assert "2.20 bar" in text  # the rear left, the flattest one
    assert "-0.30 bar" in text


async def test_summary_reads_the_battery_codes_not_the_raw_numbers(
    adapter, ready_settings, catalogue_obj
):
    """200 is "adecuada", not a percentage."""
    text = await diagnosis_tools.get_maintenance_summary(adapter, ready_settings, catalogue_obj)

    assert "adecuada (codigo 200)" in text
    assert "14.39 V" in text
    assert "Pide recarga: no" in text


async def test_summary_includes_mileage_cbs_and_the_oldest_datum(
    adapter, ready_settings, catalogue_obj
):
    """Everything CLAUDE.md asks this block to contain."""
    text = await diagnosis_tools.get_maintenance_summary(adapter, ready_settings, catalogue_obj)

    assert "48.260 km" in text
    assert "Partidas CBS: 5" in text
    assert "Dato mas antiguo utilizado:" in text
    assert "Proxima inspeccion legal (ITV): 17.01.2027" in text


async def test_summary_never_prints_a_missing_pressure_as_zero(
    ready_settings, db, fake_client, catalogue_obj
):
    """A `-NA-` tyre must read as "sin medida"; 0.00 bar would mean a flat."""
    fake_client.responses["get_telematic_data"] = load_fixture("telematic_na_pressure.json")
    adapter = CarDataAdapter(ready_settings, db=db, tokens=FakeTokens())

    text = await diagnosis_tools.get_maintenance_summary(adapter, ready_settings, catalogue_obj)

    assert "sin medida" in text
    assert "0.00 bar" not in text


# --- Cost ------------------------------------------------------------------


async def test_both_tools_share_one_request(adapter, ready_settings, catalogue_obj):
    """They read the same container; the second call must come from cache."""
    await telematic_tools.get_vehicle_status(adapter, ready_settings, catalogue_obj)
    await diagnosis_tools.get_maintenance_summary(adapter, ready_settings, catalogue_obj)
    await telematic_tools.get_telematic_data(adapter, ready_settings, catalogue_obj)

    assert adapter.quota.status().used == 1


async def test_the_reading_reaches_the_local_history(adapter, ready_settings, catalogue_obj):
    """Every real read feeds the series the diagnosis tool will need."""
    await telematic_tools.get_vehicle_status(adapter, ready_settings, catalogue_obj)

    from pitwall_mcp.descriptors import TRAVELLED_DISTANCE

    series = adapter.history.series(ready_settings.vin, TRAVELLED_DISTANCE)
    assert [reading.value for reading in series] == ["48260"]

"""Basic data, puStep and the tyre diagnosis, against what this vehicle answers.

Two of these tools have to report an absence rather than data. That is the hard
part: an absence must be explained, and a placeholder zero must never be dressed
up as a measurement.
"""

from __future__ import annotations

import pytest
from conftest import FakeTokens, load_fixture

from pitwall_mcp.cardata.client import CarDataAdapter
from pitwall_mcp.tools import tyre_tools, vehicle_tools


@pytest.fixture
def adapter(ready_settings, db, fake_client):
    """An adapter serving the recorded real responses."""
    fake_client.responses["get_basic_data"] = load_fixture("basic_data_real.json")
    fake_client.responses["get_smart_maintenance_tyre_diagnosis"] = load_fixture(
        "tyre_diagnosis_real.json"
    )
    fake_client.responses["get_mappings"] = load_fixture("mappings_list.json")
    return CarDataAdapter(ready_settings, db=db, tokens=FakeTokens())


# --- list_vehicles ---------------------------------------------------------


async def test_mappings_are_listed_with_their_type(adapter, ready_settings):
    """PRIMARY or SECONDARY is the fact that decides whether anything works."""
    text = await vehicle_tools.list_vehicles(adapter, ready_settings)
    assert "PRIMARY" in text
    assert "mapeado desde" in text
    assert "Procedencia:" in text


async def test_a_secondary_vin_is_called_out(ready_settings, db, fake_client):
    """And the user is told the fix is in the app, not in this server."""
    fake_client.responses["get_mappings"] = load_fixture("mappings_secondary.json")
    adapter = CarDataAdapter(ready_settings, db=db, tokens=FakeTokens())

    text = await vehicle_tools.list_vehicles(adapter, ready_settings)

    assert "ATENCION" in text
    assert "My BMW" in text


# --- get_vehicle_basic_data ------------------------------------------------


async def test_basic_data_shows_the_real_fields(adapter, ready_settings):
    """The ones this vehicle actually returns, labelled in Spanish."""
    text = await vehicle_tools.get_vehicle_basic_data(adapter, ready_settings)

    assert "Modelo: X1 sDrive18i" in text
    assert "Motor: B38N" in text
    assert "Serie de desarrollo: U11" in text
    assert "Color: ALPINWEISS  III" in text


async def test_basic_data_names_what_bmw_did_not_send(adapter, ready_settings):
    """The swagger promises fields this vehicle omits. Say which."""
    text = await vehicle_tools.get_vehicle_basic_data(adapter, ready_settings)

    assert "NO devuelve" in text
    assert "puStep" in text


async def test_the_equipment_list_is_counted_not_just_dumped(adapter, ready_settings):
    """38 SA codes are unreadable as one blob; give the count too."""
    text = await vehicle_tools.get_vehicle_basic_data(adapter, ready_settings)
    assert "38 codigos" in text


def test_unlabelled_fields_are_shown_rather_than_dropped():
    """A field we do not have a Spanish name for is still the user's data."""
    lines = vehicle_tools.render_basic_data({"brand": "BMW", "campoNuevo": "algo"})
    text = "\n".join(lines)
    assert "campoNuevo" in text
    assert "algo" in text


# --- report_product_update_step --------------------------------------------


async def test_pustep_absence_is_explained_not_substituted(adapter, ready_settings):
    """This vehicle does not return puStep. Inventing a stand-in would be worse."""
    text = await vehicle_tools.report_product_update_step(adapter, ready_settings)

    assert "NO DISPONIBLE" in text
    assert "no devuelve" in text
    assert "no es un fallo de configuracion" in text.lower()


async def test_pustep_absence_does_not_claim_a_software_version(adapter, ready_settings):
    """The descriptor never existed either; both facts must be stated."""
    text = await vehicle_tools.report_product_update_step(adapter, ready_settings)

    assert "TAMPOCO era la version de software" in text
    assert "diagnose_software_update" in text


async def test_pustep_is_reported_when_a_vehicle_does_return_it(
    ready_settings, db, fake_client
):
    """Other vehicles may send it; then it is shown, with its caveat."""
    fake_client.responses["get_basic_data"] = load_fixture("basic_data.json")
    adapter = CarDataAdapter(ready_settings, db=db, tokens=FakeTokens())

    text = await vehicle_tools.report_product_update_step(adapter, ready_settings)

    assert "puStep: 0724" in text
    assert "NO es la version de software" in text


async def test_it_does_not_promise_a_pustep_history_it_never_keeps(
    ready_settings, db, fake_client
):
    """Only /telematicData responses reach `readings`; basicData never does."""
    fake_client.responses["get_basic_data"] = load_fixture("basic_data.json")
    adapter = CarDataAdapter(ready_settings, db=db, tokens=FakeTokens())

    text = await vehicle_tools.report_product_update_step(adapter, ready_settings)

    assert "Se guarda en el historico" not in text
    assert "no se guarda en el historico" in text


# --- get_tyre_diagnosis ----------------------------------------------------


async def test_the_empty_skeleton_is_reported_as_no_data(adapter, ready_settings):
    """BMW answers with every node present and every value unfilled."""
    text = await tyre_tools.get_tyre_diagnosis(adapter, ready_settings)

    assert "SIN DATOS" in text
    assert "estructura completa" in text
    assert "errors" not in text.lower() or "vino vacia" in text


async def test_a_placeholder_zero_is_never_shown_as_a_measurement(adapter, ready_settings):
    """`dueMileage: 0` would read as "replace your tyres now". It must not."""
    text = await tyre_tools.get_tyre_diagnosis(adapter, ready_settings)

    assert "0 km" not in text
    assert "Cambio previsto" not in text
    assert "NO significa" in text


async def test_the_tyre_tool_redirects_to_where_pressures_live(adapter, ready_settings):
    """Two different sources; the user should not have to guess which."""
    text = await tyre_tools.get_tyre_diagnosis(adapter, ready_settings)

    assert "NO devuelve presiones" in text
    assert "get_maintenance_summary" in text


def test_a_wheel_with_real_data_is_rendered(catalogue):
    """When a vehicle does have tyre data, it gets shown properly."""
    wheel = load_fixture("tyre_diagnosis.json")["passengerCar"]["mountedTyres"]["frontLeft"]
    assert tyre_tools.wheel_has_data(wheel)

    lines = "\n".join(tyre_tools.describe_wheel(wheel, "Delantera izquierda"))

    assert "225/55 R18" in lines
    assert "21000" in lines
    assert "Runflat: si" in lines


def test_zero_and_false_never_count_as_data():
    """The placeholder rule, isolated."""
    assert not tyre_tools._meaningful(0)  # noqa: SLF001
    assert not tyre_tools._meaningful(False)  # noqa: SLF001
    assert not tyre_tools._meaningful("")  # noqa: SLF001
    assert not tyre_tools._meaningful("-")  # noqa: SLF001
    assert tyre_tools._meaningful(18)  # noqa: SLF001
    assert tyre_tools._meaningful("SUMMER")  # noqa: SLF001


def test_an_empty_wheel_is_detected(adapter):
    """The whole judgement rests on this one function."""
    empty = load_fixture("tyre_diagnosis_real.json")["passengerCar"]["mountedTyres"]
    assert not tyre_tools.wheel_has_data(empty["frontLeft"])
    assert not tyre_tools.wheel_has_data(empty["rearRight"])


# --- Cost ------------------------------------------------------------------


async def test_basic_data_and_pustep_share_one_request(adapter, ready_settings):
    """They read the same endpoint: the second must come from cache."""
    await vehicle_tools.get_vehicle_basic_data(adapter, ready_settings)
    await vehicle_tools.report_product_update_step(adapter, ready_settings)

    assert adapter.quota.status().used == 1

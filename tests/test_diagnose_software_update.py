"""`diagnose_software_update` against the real recorded answer.

This tool exists to answer why no Remote Software Upgrade has arrived in about
a year, and the whole point of these tests is that it NEVER answers more than
the data supports.

BMW documents three conditions that stop an update being offered: a low 12V
battery, hazard lights left on at shutdown, and parking on more than a 12%
slope. CarData exposes ONE of them, partially. The other two are not
observable, and the tool has to say so in those words rather than quietly
reasoning as if they were ruled out.

On top of that, the one observable condition is itself crippled on this
vehicle: `stateOfCharge` and `deepSleepModeActive` came back empty, so only
`voltage` and `serviceDemand` remain, and `isIgnitionOn` is empty too, which
means we cannot even tell whether a voltage was measured at rest or while
charging.
"""

from __future__ import annotations

import pytest
from conftest import FAKE_VIN, FakeTokens, load_fixture

from pitwall_mcp.cardata.client import CarDataAdapter
from pitwall_mcp.descriptors import BATTERY_VOLTAGE
from pitwall_mcp.tools import diagnosis_tools


@pytest.fixture
def adapter(ready_settings, db, fake_client):
    """An adapter serving the two recorded real responses."""
    fake_client.responses["get_telematic_data"] = load_fixture("telematic_real.json")
    fake_client.responses["get_basic_data"] = load_fixture("basic_data_real.json")
    return CarDataAdapter(ready_settings, db=db, tokens=FakeTokens())


def seed_voltage(adapter: CarDataAdapter, values: list[str]) -> None:
    """Put a voltage series into the history, oldest first.

    The stamps are later than the fixture's own reading (2026-09-07T08:55), so
    the container read the tool performs lands at the START of the series and
    the seeded values are the recent ones.
    """
    for index, value in enumerate(values):
        stamp = f"2026-09-08T1{index}:00:00.000Z"
        adapter.history.record(
            FAKE_VIN,
            {BATTERY_VOLTAGE: {"value": value, "unit": "V", "timestamp": stamp}},
        )


# --- The limit of the diagnosis, stated out loud ---------------------------


async def test_it_names_all_three_conditions_bmw_documents(adapter, ready_settings):
    text = await diagnosis_tools.diagnose_software_update(adapter, ready_settings)

    assert "bateria de 12V" in text
    assert "luces de emergencia" in text.lower()
    assert "inclinacion" in text


async def test_it_declares_the_two_conditions_cardata_cannot_see(adapter, ready_settings):
    text = await diagnosis_tools.diagnose_software_update(adapter, ready_settings)

    assert text.count("NO OBSERVABLE POR CARDATA") == 2


async def test_it_says_only_one_of_the_three_is_observable(adapter, ready_settings):
    text = await diagnosis_tools.diagnose_software_update(adapter, ready_settings)

    assert "solo permite observar una" in text


# --- The software version that does not exist ------------------------------


async def test_it_states_that_no_software_version_descriptor_exists(adapter, ready_settings):
    text = await diagnosis_tools.diagnose_software_update(adapter, ready_settings)

    assert "no existe" in text
    assert "iStep" in text


async def test_it_reports_that_pustep_does_not_arrive_for_this_vehicle(adapter, ready_settings):
    text = await diagnosis_tools.diagnose_software_update(adapter, ready_settings)

    assert "puStep" in text
    assert "no lo devuelve" in text


async def test_it_does_not_claim_a_pustep_history_it_never_stored(adapter, ready_settings):
    """Only telematic responses reach `readings`; basicData never does."""
    text = await diagnosis_tools.diagnose_software_update(adapter, ready_settings)

    assert "no hay historico de puStep" in text


# --- The battery: the only observable condition, itself crippled -----------


async def test_it_reports_the_voltage_actually_read(adapter, ready_settings):
    text = await diagnosis_tools.diagnose_software_update(adapter, ready_settings)

    assert "14.39 V" in text


async def test_it_declares_state_of_charge_empty_instead_of_reporting_zero(adapter, ready_settings):
    text = await diagnosis_tools.diagnose_software_update(adapter, ready_settings)

    assert "stateOfCharge" in text
    assert "vacio" in text
    assert "0 %" not in text


async def test_it_reports_the_two_service_demands(adapter, ready_settings):
    text = await diagnosis_tools.diagnose_software_update(adapter, ready_settings)

    assert "adecuada (codigo 200)" in text
    assert "recarga" in text


async def test_it_declares_deep_sleep_unobserved_when_it_arrives_empty(adapter, ready_settings):
    text = await diagnosis_tools.diagnose_software_update(adapter, ready_settings)

    assert "deepSleepModeActive" in text


async def test_it_admits_it_cannot_tell_whether_the_engine_was_running(adapter, ready_settings):
    """14.39 V is a charging voltage, and isIgnitionOn came back empty."""
    text = await diagnosis_tools.diagnose_software_update(adapter, ready_settings)

    assert "no se puede saber en que condicion se tomo" in text


# --- The series, not the snapshot ------------------------------------------


async def test_a_single_observation_refuses_to_conclude(adapter, ready_settings):
    text = await diagnosis_tools.diagnose_software_update(adapter, ready_settings)

    assert "SIN VEREDICTO" in text
    assert "1 observacion" in text


async def test_it_says_what_it_needs_before_it_can_speak(adapter, ready_settings):
    text = await diagnosis_tools.diagnose_software_update(adapter, ready_settings)

    assert "al menos 2 observaciones" in text


async def test_repeated_identical_values_do_not_count_as_evidence(adapter, ready_settings):
    """Request-stamped values create a row per call; they are still one observation."""
    seed_voltage(adapter, ["14.39", "14.39", "14.39"])

    text = await diagnosis_tools.diagnose_software_update(adapter, ready_settings)

    assert "SIN VEREDICTO" in text


async def test_a_real_series_produces_a_verdict(adapter, ready_settings):
    seed_voltage(adapter, ["12.60", "12.50", "12.40"])

    text = await diagnosis_tools.diagnose_software_update(adapter, ready_settings)

    assert "SIN VEREDICTO" not in text
    assert "4 observaciones" in text


async def test_a_falling_series_is_described_as_falling(adapter, ready_settings):
    seed_voltage(adapter, ["12.60", "12.20", "11.80"])

    text = await diagnosis_tools.diagnose_software_update(adapter, ready_settings)

    assert "baja" in text


async def test_a_low_voltage_is_flagged_without_being_called_the_cause(adapter, ready_settings):
    seed_voltage(adapter, ["12.10", "11.90", "11.70"])

    text = await diagnosis_tools.diagnose_software_update(adapter, ready_settings)

    assert "por debajo" in text
    assert "no demuestra" in text


async def test_a_healthy_series_does_not_rule_the_other_two_conditions_out(adapter, ready_settings):
    seed_voltage(adapter, ["12.70", "12.65", "12.60"])

    text = await diagnosis_tools.diagnose_software_update(adapter, ready_settings)

    assert "no descarta" in text
    assert "NO OBSERVABLE POR CARDATA" in text


# --- Provenance -------------------------------------------------------------


async def test_it_gives_the_newest_and_the_oldest_datum_used(adapter, ready_settings):
    text = await diagnosis_tools.diagnose_software_update(adapter, ready_settings)

    assert "Dato mas reciente" in text
    assert "Dato mas antiguo" in text


async def test_it_reports_the_mileage(adapter, ready_settings):
    text = await diagnosis_tools.diagnose_software_update(adapter, ready_settings)

    assert "48.260 km" in text


async def test_it_names_the_two_ends_of_the_trend_not_only_the_drop(adapter, ready_settings):
    """ "Baja 2.34 V" hides that it went from a charging voltage to a resting one."""
    seed_voltage(adapter, ["12.55", "12.30", "12.05"])

    text = await diagnosis_tools.diagnose_software_update(adapter, ready_settings)

    assert "de 14.39 V a 12.05 V" in text


async def test_a_trend_across_unknown_conditions_is_flagged_as_such(adapter, ready_settings):
    """With isIgnitionOn empty, part of any slope is just engine on vs engine off."""
    seed_voltage(adapter, ["12.55", "12.30", "12.05"])

    text = await diagnosis_tools.diagnose_software_update(adapter, ready_settings)

    assert "puede ser solo motor en marcha" in text


async def test_the_single_observation_line_is_written_in_plain_spanish(adapter, ready_settings):
    text = await diagnosis_tools.diagnose_software_update(adapter, ready_settings)

    assert "1 observacion distinta " in text
    assert "distinta(s)" not in text


async def test_it_warns_that_repeating_the_rest_read_may_not_add_points(adapter, ready_settings):
    """Verified 2026-09-08: voltage did not refresh across 20 h and a drive.

    Telling the user to "read again in a few days" would be advice we have
    evidence against, which is worse than saying nothing.
    """
    text = await diagnosis_tools.diagnose_software_update(adapter, ready_settings)

    assert "puede que repetir la lectura REST no anada ningun punto" in text
    assert "streaming" in text

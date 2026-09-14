"""Fuel: refuels and consumption from the tank series, and a frozen OBFCM figure.

Verified 2026-09-14 on the first read of the extended container: the tank
level and litres arrive fresh, the litres with a `null` unit and, per the
catalogue, up to 6 litres of float error. The OBFCM lifetime consumption
arrives too, stamped 30 October 2024. Every expected value is worked by hand.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from conftest import FAKE_VIN, FakeTokens, load_fixture

from pitwall_mcp.cardata.client import CarDataAdapter
from pitwall_mcp.descriptors import FUEL_LEVEL, FUEL_REMAINING, TRAVELLED_DISTANCE
from pitwall_mcp.fuel import consumption_since_refuel, detect_refuels
from pitwall_mcp.tools import fuel_tools

START = datetime(2026, 9, 1, 8, tzinfo=UTC)


def ev(day: int, level, litres, km):
    """One reading event as `HistoryStore.snapshots` returns it."""
    return START + timedelta(days=day), {
        FUEL_LEVEL: str(level),
        FUEL_REMAINING: str(litres),
        TRAVELLED_DISTANCE: str(km),
    }


# --- Refuels ---------------------------------------------------------------


def test_a_rise_of_the_tank_is_a_refuel():
    refuels = detect_refuels(
        [ev(0, 58, 24, 48000), ev(2, 40, 17, 48150), ev(3, 95, 40, 48160), ev(5, 80, 34, 48300)]
    )

    assert [(r.level_before, r.level_after) for r in refuels] == [(40, 95)]
    assert refuels[0].moment == START + timedelta(days=3)


def test_a_small_rise_is_the_float_not_a_refuel():
    assert detect_refuels([ev(0, 58, 24, 48000), ev(1, 63, 26, 48000), ev(2, 60, 25, 48050)]) == []


# --- Consumption -----------------------------------------------------------
# Verified 2026-09-14: remainingFuel travels in the slow timestamp group and
# keeps its stamp while the mileage moves. Consumption is therefore computed
# from litres MEASUREMENTS only (distinct BMW stamps), each paired with the
# mileage nearest in time, never from snapshots that carry stale litres.


def litres_at(day: float, litres: float):
    """One litres measurement, as (BMW moment, litres)."""
    return START + timedelta(days=day), float(litres)


def km_at(day: float, km: float):
    """One mileage measurement, as (BMW moment, km)."""
    return START + timedelta(days=day), float(km)


def test_consumption_over_a_stretch_without_refuel():
    """30 L in 400 km is 7.5 l/100 km; 6 L of error at each end is 12 L, 3 l/100 km."""
    consumption = consumption_since_refuel(
        [litres_at(0, 50), litres_at(3, 40), litres_at(6, 20)],
        [km_at(0, 48000), km_at(3, 48150), km_at(6, 48400)],
    )

    assert consumption is not None
    assert (consumption.litres, consumption.km) == (30, 400)
    assert consumption.l_per_100km == pytest.approx(7.5)
    assert consumption.margin == pytest.approx(3.0)
    assert not consumption.after_refuel


def test_consumption_restarts_at_the_last_refuel():
    """From the refuel (50 L, 48405 km) to 30 L at 48705 km: 20 L in 300 km."""
    consumption = consumption_since_refuel(
        [litres_at(0, 50), litres_at(6, 20), litres_at(7, 50), litres_at(10, 30)],
        [km_at(0, 48000), km_at(6, 48400), km_at(7, 48405), km_at(10, 48705)],
    )

    assert consumption is not None
    assert (consumption.litres, consumption.km) == (20, 300)
    assert consumption.l_per_100km == pytest.approx(6.667, abs=0.01)
    assert consumption.since == START + timedelta(days=7)
    assert consumption.after_refuel


def test_a_rise_within_the_float_error_is_not_a_refuel():
    """+10 L is inside 2 x 6 L of float error: the stretch does not restart."""
    consumption = consumption_since_refuel(
        [litres_at(0, 50), litres_at(3, 30), litres_at(4, 40), litres_at(8, 20)],
        [km_at(0, 48000), km_at(3, 48200), km_at(4, 48210), km_at(8, 48500)],
    )

    assert consumption is not None
    assert consumption.since == START


def test_each_measurement_takes_the_mileage_nearest_in_time():
    """Litres at day 6 noon: the mileage of day 6 at 23:00 is nearer than day 6 at 00:00."""
    consumption = consumption_since_refuel(
        [litres_at(0, 50), litres_at(6.5, 20)],
        [km_at(0, 48000), km_at(6, 48300), km_at(6 + 23 / 24, 48400)],
    )

    assert consumption is not None
    assert consumption.km == 400


def test_too_few_kilometres_give_no_consumption():
    """12 L of error over 100 km would be 12 l/100 km either way: not a figure."""
    assert (
        consumption_since_refuel(
            [litres_at(0, 50), litres_at(1, 45)], [km_at(0, 48000), km_at(1, 48100)]
        )
        is None
    )


def test_more_litres_without_a_refuel_give_no_consumption():
    """A car parked on a slope can read higher; that is not negative consumption."""
    assert (
        consumption_since_refuel(
            [litres_at(0, 20), litres_at(3, 25)], [km_at(0, 48000), km_at(3, 48400)]
        )
        is None
    )


def test_no_mileage_gives_no_consumption():
    assert consumption_since_refuel([litres_at(0, 50), litres_at(6, 20)], []) is None


# --- The tool --------------------------------------------------------------


@pytest.fixture
def adapter(ready_settings, db, fake_client):
    """An adapter serving the first read of the extended container."""
    fake_client.responses["get_telematic_data"] = load_fixture("telematic_extended.json")
    return CarDataAdapter(ready_settings, db=db, tokens=FakeTokens())


async def test_it_reports_the_tank_and_the_range(adapter, ready_settings):
    text = await fuel_tools.get_fuel_status(adapter, ready_settings)

    assert "58 %" in text
    assert "24 L" in text
    assert "+/-6 L" in text
    assert "395 km" in text


async def test_the_obfcm_figure_is_never_passed_off_as_todays_consumption(
    adapter, ready_settings
):
    """226.32 l over 2826.3 km is 8.0 l/100 km, stamped 30 Oct 2024."""
    text = await fuel_tools.get_fuel_status(adapter, ready_settings)

    line = next((line for line in text.splitlines() if "OBFCM" in line), "")
    assert "8,0 l/100 km" in line
    assert "30-10-2024" in line
    assert "NO es tu consumo actual" in text


async def test_a_single_reading_detects_no_refuel_and_says_what_it_needs(
    adapter, ready_settings
):
    text = await fuel_tools.get_fuel_status(adapter, ready_settings)

    assert "ningun repostaje" in text
    assert "300 km" in text


async def test_the_navigation_range_arriving_empty_is_said(adapter, ready_settings):
    text = await fuel_tools.get_fuel_status(adapter, ready_settings)

    line = next((line for line in text.splitlines() if "navegador" in line), "")
    assert "vacia" in line


async def test_a_refuel_in_the_history_is_reported(adapter, ready_settings):
    """30 % on 10 Sep, 58 % in the 14 Sep read: 28 points up, a refuel."""
    stamp = "2026-09-10T12:00:00.000Z"
    adapter.history.record(
        FAKE_VIN,
        {
            FUEL_LEVEL: {"value": "30", "unit": "%", "timestamp": stamp},
            FUEL_REMAINING: {"value": "13", "unit": None, "timestamp": stamp},
            TRAVELLED_DISTANCE: {"value": "48400", "unit": "km", "timestamp": stamp},
        },
        moment=datetime(2026, 9, 10, 12, tzinfo=UTC),
    )

    text = await fuel_tools.get_fuel_status(adapter, ready_settings)

    assert "1 repostaje" in text
    assert "del 30 % al 58 %" in text


async def test_stale_litres_are_not_paired_with_fresh_kilometres(
    ready_settings, db, fake_client
):
    """The bug of 2026-09-14. Litres measured on 8 Sep (50) and 9 Sep (30), then
    re-sent with the 9 Sep stamp while the car drove on to 47500 km. Only 100 km
    separate the two real measurements: too few for a figure. Pairing the stale
    30 L with the later mileage invented one."""
    fake_client.responses["get_telematic_data"] = load_fixture("telematic_brake_warning.json")
    adapter = CarDataAdapter(ready_settings, db=db, tokens=FakeTokens())

    def seed(day: int, litres: int, litres_day: int, km: int) -> None:
        stamp = f"2026-09-{day:02d}T12:00:00.000Z"
        adapter.history.record(
            FAKE_VIN,
            {
                FUEL_REMAINING: {
                    "value": str(litres),
                    "unit": None,
                    "timestamp": f"2026-09-{litres_day:02d}T12:00:00.000Z",
                },
                FUEL_LEVEL: {"value": "60", "unit": "%", "timestamp": stamp},
                TRAVELLED_DISTANCE: {"value": str(km), "unit": "km", "timestamp": stamp},
            },
            moment=datetime(2026, 9, day, 12, tzinfo=UTC),
        )

    seed(8, 50, 8, 47000)
    seed(9, 30, 9, 47100)
    seed(12, 30, 9, 47500)

    text = await fuel_tools.get_fuel_status(adapter, ready_settings)

    assert "Consumo real: aun no" in text
    assert "l/100 km +/-" not in text


async def test_an_old_container_says_why_there_is_no_tank(ready_settings, db, fake_client):
    fake_client.responses["get_telematic_data"] = load_fixture("telematic_real.json")
    adapter = CarDataAdapter(ready_settings, db=db, tokens=FakeTokens())

    text = await fuel_tools.get_fuel_status(adapter, ready_settings)

    assert "no lo ha devuelto" in text
    assert " 0 %" not in text

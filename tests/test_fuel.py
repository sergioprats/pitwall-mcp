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


def test_consumption_over_a_stretch_without_refuel():
    """30 L in 400 km is 7.5 l/100 km; 6 L of error at each end is 12 L, 3 l/100 km."""
    consumption = consumption_since_refuel(
        [ev(0, 90, 50, 48000), ev(3, 70, 40, 48150), ev(6, 35, 20, 48400)]
    )

    assert consumption is not None
    assert (consumption.litres, consumption.km) == (30, 400)
    assert consumption.l_per_100km == pytest.approx(7.5)
    assert consumption.margin == pytest.approx(3.0)


def test_consumption_restarts_at_the_last_refuel():
    """From the refuel (50 L, 48405 km) to 30 L at 48705 km: 20 L in 300 km."""
    consumption = consumption_since_refuel(
        [ev(0, 90, 50, 48000), ev(6, 35, 20, 48400), ev(7, 95, 50, 48405), ev(10, 55, 30, 48705)]
    )

    assert consumption is not None
    assert (consumption.litres, consumption.km) == (20, 300)
    assert consumption.l_per_100km == pytest.approx(6.667, abs=0.01)
    assert consumption.since == START + timedelta(days=7)


def test_too_few_kilometres_give_no_consumption():
    """12 L of error over 100 km would be 12 l/100 km either way: not a figure."""
    assert consumption_since_refuel([ev(0, 90, 50, 48000), ev(1, 80, 45, 48100)]) is None


def test_an_event_without_a_number_is_skipped():
    consumption = consumption_since_refuel(
        [ev(0, 90, 50, 48000), ev(3, 70, "-NA-", 48150), ev(6, 35, 20, 48400)]
    )

    assert consumption is not None
    assert (consumption.litres, consumption.km) == (30, 400)


def test_more_litres_without_a_refuel_give_no_consumption():
    """A car parked on a slope can read higher; that is not negative consumption."""
    assert consumption_since_refuel([ev(0, 40, 20, 48000), ev(3, 45, 25, 48400)]) is None


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


async def test_an_old_container_says_why_there_is_no_tank(ready_settings, db, fake_client):
    fake_client.responses["get_telematic_data"] = load_fixture("telematic_real.json")
    adapter = CarDataAdapter(ready_settings, db=db, tokens=FakeTokens())

    text = await fuel_tools.get_fuel_status(adapter, ready_settings)

    assert "no lo ha devuelto" in text
    assert " 0 %" not in text

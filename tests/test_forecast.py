"""Turning CBS distances into weeks and dates, and measuring the driver's pace.

The official app says "1.600 km". What it does not say is when that is, at the
pace this car is actually driven. Every expected value here is worked out by
hand from the real readings of September 2026.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from conftest import FAKE_VIN

from pitwall_mcp.descriptors import TRAVELLED_DISTANCE
from pitwall_mcp.forecast import project, rate_from_history
from pitwall_mcp.storage.history import Reading

#: The moment of the 13 Sep 2026 mileage reading.
REF = datetime(2026, 9, 13, 18, 13, 49, tzinfo=UTC)


def _km(value: str, stamp: str) -> Reading:
    """One stored mileage reading."""
    return Reading(
        vin=FAKE_VIN,
        descriptor=TRAVELLED_DISTANCE,
        value=value,
        unit="km",
        source_timestamp=stamp,
        recorded_at=REF,
        source="rest",
    )


# --- project ---------------------------------------------------------------


def test_distance_becomes_weeks_and_a_date_at_the_given_pace():
    """1900 km / 570 km per week = 3.33 weeks = 23 d 8 h after 13 Sep 18:13."""
    projection = project(1900, None, 570, REF)

    assert round(projection.weeks, 1) == 3.3
    assert projection.due_date == date(2026, 10, 7)
    assert projection.first == "km"


def test_the_distance_wins_when_it_comes_before_a_month_only_date():
    """14000 km at 570 km/week lands on 4 Mar 2027, before July 2027."""
    projection = project(14000, "2027-07", 570, REF)

    assert projection.due_date == date(2027, 3, 4)
    assert projection.first == "km"


def test_the_date_wins_when_it_comes_first():
    """45000 km at 100 km/week is 2034; the 2029-07 date comes first."""
    assert project(45000, "2029-07", 100, REF).first == "fecha"


def test_a_month_only_date_counts_from_the_first_day_of_that_month():
    """The API sends "2027-07" (the car shows 11.07.2027): July 1st is the safe end."""
    on_the_first = REF.replace(year=2027, month=7, day=1)
    km_to_first = 570 / 7 * (on_the_first - REF).total_seconds() / 86400

    assert project(int(km_to_first) + 200, "2027-07", 570, REF).first == "fecha"


def test_an_item_with_only_a_date_has_no_distance_projection():
    projection = project(None, "2027-01", 570, REF)

    assert projection.weeks is None
    assert projection.due_date is None
    assert projection.first == "fecha"


def test_no_pace_means_no_projection():
    assert project(1900, None, None, REF).due_date is None


# --- rate_from_history -----------------------------------------------------


def test_the_local_pace_comes_from_the_first_and_last_mileage():
    """48260 -> 48712 km between 7 Sep 20:40 and 14 Sep 13:41: 452 km in 6.709 days."""
    rate = rate_from_history(
        [
            _km("48260", "2026-09-07T20:40:00.000Z"),
            _km("48440", "2026-09-13T18:13:00.000Z"),
            _km("48712", "2026-09-14T13:41:00.000Z"),
        ]
    )

    assert rate is not None
    assert round(rate.km_per_week) == 472
    assert rate.days == pytest.approx(6.709, abs=0.01)


def test_less_than_two_days_of_history_gives_no_pace():
    """One long drive would pass for a weekly habit."""
    rate = rate_from_history(
        [
            _km("48440", "2026-09-13T18:13:00.000Z"),
            _km("48712", "2026-09-14T13:41:00.000Z"),
        ]
    )

    assert rate is None


def test_a_single_reading_gives_no_pace():
    assert rate_from_history([_km("48440", "2026-09-13T18:13:00.000Z")]) is None


def test_readings_out_of_order_are_sorted_by_bmws_timestamp():
    rate = rate_from_history(
        [
            _km("48712", "2026-09-14T13:41:00.000Z"),
            _km("48260", "2026-09-07T20:40:00.000Z"),
        ]
    )

    assert rate is not None
    assert rate.km_per_week > 0

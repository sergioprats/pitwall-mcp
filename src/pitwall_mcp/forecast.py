"""Maintenance forecast: CBS distances turned into weeks and dates.

The official app says "1.600 km". This module says when that is, at the pace
the car is actually driven. It is arithmetic on BMW's own figures, never a new
datum: the output is labelled as an estimate wherever it is shown.

Two paces are available. BMW sends `averageWeeklyDistanceShortTerm`; the local
history can measure one from the mileage series. Neither is privileged here:
the caller decides, and the tools pick the faster one so a forecast never
arrives late.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Final, Literal

from .formatting import parse_numeric
from .storage.history import Reading

#: Below this span a mileage series says more about one trip than about a habit.
MIN_HISTORY_DAYS: Final = 2.0


@dataclass(frozen=True)
class WeeklyRate:
    """A pace measured from the local mileage history."""

    km_per_week: float
    days: float
    km: float


@dataclass(frozen=True)
class Projection:
    """When one CBS item falls due at a given pace, and by which limit."""

    weeks: float | None
    due_date: date | None
    first: Literal["km", "fecha"] | None


def _month_start(text: str | None) -> date | None:
    """First day of a CBS "YYYY-MM" date. The API drops the day, so the first
    is the only safe reading: the car showed 11.07.2027 for "2027-07"."""
    if not text:
        return None
    try:
        year, month = text.split("-")[:2]
        return date(int(year), int(month), 1)
    except ValueError:
        return None


def project(
    distance_km: int | None,
    date_text: str | None,
    km_per_week: float | None,
    reference: datetime,
) -> Projection:
    """Project one item from `reference`, the moment its distance was read."""
    deadline = _month_start(date_text)
    weeks: float | None = None
    due: date | None = None
    if distance_km is not None and km_per_week:
        weeks = distance_km / km_per_week
        due = (reference + timedelta(weeks=weeks)).date()

    if due is None:
        first: Literal["km", "fecha"] | None = "fecha" if deadline else None
    elif deadline is None or due < deadline:
        first = "km"
    else:
        first = "fecha"
    return Projection(weeks=weeks, due_date=due, first=first)


def rate_from_history(readings: list[Reading]) -> WeeklyRate | None:
    """Weekly pace from the first and last mileage, by BMW's timestamps.

    `None` when the series is too short to stand for a habit: fewer than two
    readings, less than `MIN_HISTORY_DAYS` between them, or a mileage going
    backwards.
    """
    points = sorted(
        (
            (reading.source_moment, km)
            for reading in readings
            if reading.source_moment is not None
            and (km := parse_numeric(reading.value)) is not None
        ),
        key=lambda point: point[0],
    )
    if len(points) < 2:
        return None
    (start, first_km), (end, last_km) = points[0], points[-1]
    days = (end - start).total_seconds() / 86400
    if days < MIN_HISTORY_DAYS or last_km < first_km:
        return None
    driven = last_km - first_km
    return WeeklyRate(km_per_week=driven / days * 7, days=days, km=driven)

"""Fuel: refuels and consumption from the tank series.

Verified 2026-09-14: `fuelSystem.level` (%) and `.remainingFuel` arrive fresh,
the litres with a `null` unit. The catalogue warns the litres may be off by up
to 6 L depending on the float, so a consumption worked out from them carries
that error at both ends of the stretch. It is only given over a distance long
enough for the error not to swamp the figure, and always with its margin.

Works on `HistoryStore.snapshots()`, so each event is what one response
carried: level, litres and mileage read together.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from .descriptors import FUEL_LEVEL, FUEL_REMAINING, TRAVELLED_DISTANCE
from .formatting import parse_numeric

#: A rise of this many tank points between readings is a refuel. Less is the
#: float, or the car parked on a slope.
REFUEL_MIN_POINTS: Final = 10.0

#: Catalogue: "the specified value may differ by up to 6 litres".
FLOAT_TOLERANCE_L: Final = 6.0

#: Below this, 12 L of error across the stretch outweighs the figure itself.
MIN_CONSUMPTION_KM: Final = 300.0

Snapshot = tuple[datetime, dict[str, str | None]]


@dataclass(frozen=True)
class Refuel:
    """A rise of the tank between two consecutive readings."""

    moment: datetime
    level_before: float
    level_after: float


@dataclass(frozen=True)
class Consumption:
    """Litres used over a stretch without refuel, and what that is per 100 km."""

    litres: float
    km: float
    since: datetime
    until: datetime

    @property
    def l_per_100km(self) -> float:
        """Litres per 100 km over the stretch."""
        return self.litres / self.km * 100

    @property
    def margin(self) -> float:
        """The float error at both ends, in l/100 km."""
        return 2 * FLOAT_TOLERANCE_L / self.km * 100


def _events(snapshots: Sequence[Snapshot]) -> list[tuple[datetime, float, float, float]]:
    """(moment, level %, litres, km) for every event carrying all three numbers."""
    events = []
    for moment, values in snapshots:
        level = parse_numeric(values.get(FUEL_LEVEL))
        litres = parse_numeric(values.get(FUEL_REMAINING))
        km = parse_numeric(values.get(TRAVELLED_DISTANCE))
        if level is not None and litres is not None and km is not None:
            events.append((moment, level, litres, km))
    return events


def _is_refuel(before: tuple, after: tuple) -> bool:
    """Whether the tank rose enough between two events to be a refuel."""
    return after[1] - before[1] >= REFUEL_MIN_POINTS


def detect_refuels(snapshots: Sequence[Snapshot]) -> list[Refuel]:
    """Every refuel visible in the series, dated by the reading that saw it."""
    events = _events(snapshots)
    return [
        Refuel(moment=after[0], level_before=before[1], level_after=after[1])
        for before, after in zip(events, events[1:], strict=False)
        if _is_refuel(before, after)
    ]


def consumption_since_refuel(snapshots: Sequence[Snapshot]) -> Consumption | None:
    """Consumption from the last refuel (or the first reading) to the latest.

    `None` when the stretch is shorter than `MIN_CONSUMPTION_KM` or the litres
    did not go down: a negative consumption is a float or a slope, not a figure.
    """
    events = _events(snapshots)
    start = 0
    for index in range(1, len(events)):
        if _is_refuel(events[index - 1], events[index]):
            start = index
    stretch = events[start:]
    if len(stretch) < 2:
        return None
    first, last = stretch[0], stretch[-1]
    km = last[3] - first[3]
    litres = first[2] - last[2]
    if km < MIN_CONSUMPTION_KM or litres <= 0:
        return None
    return Consumption(litres=litres, km=km, since=first[0], until=last[0])

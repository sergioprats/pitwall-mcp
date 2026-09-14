"""Fuel: refuels and consumption from the tank series.

Verified 2026-09-14: `fuelSystem.level` (%) arrives fresh with the measurement
cohort, but `.remainingFuel` (litres, unit sent as `null`) travels in the slow
timestamp group: it keeps its stamp for hours while the mileage moves on.

So the two series are used differently:

* Refuels are seen in the level, read from `HistoryStore.snapshots()`.
* Consumption is worked out from litres MEASUREMENTS only, one per distinct BMW
  stamp, each paired with the mileage nearest to it in time. A snapshot carries
  the last known litres forward, and pairing that stale value with a fresh
  mileage invents a consumption.

The catalogue warns the litres may be off by up to 6 L depending on the float,
so a consumption carries that error at both ends. It is only given over a
distance long enough for the error not to swamp the figure, and always with
its margin.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from .descriptors import FUEL_LEVEL
from .formatting import parse_numeric

#: A rise of this many tank points between readings is a refuel. Less is the
#: float, or the car parked on a slope.
REFUEL_MIN_POINTS: Final = 10.0

#: Catalogue: "the specified value may differ by up to 6 litres".
FLOAT_TOLERANCE_L: Final = 6.0

#: A rise of the litres larger than the float error at both ends: a refuel.
REFUEL_MIN_LITRES: Final = 2 * FLOAT_TOLERANCE_L

#: Below this, 12 L of error across the stretch outweighs the figure itself.
MIN_CONSUMPTION_KM: Final = 300.0

Snapshot = tuple[datetime, dict[str, str | None]]
Point = tuple[datetime, float]


@dataclass(frozen=True)
class Refuel:
    """A rise of the tank level between two consecutive readings."""

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
    after_refuel: bool

    @property
    def l_per_100km(self) -> float:
        """Litres per 100 km over the stretch."""
        return self.litres / self.km * 100

    @property
    def margin(self) -> float:
        """The float error at both ends, in l/100 km."""
        return 2 * FLOAT_TOLERANCE_L / self.km * 100


def detect_refuels(snapshots: Sequence[Snapshot]) -> list[Refuel]:
    """Every rise of the level visible in the series, dated by the reading that saw it."""
    levels = [
        (moment, level)
        for moment, values in snapshots
        if (level := parse_numeric(values.get(FUEL_LEVEL))) is not None
    ]
    return [
        Refuel(moment=after[0], level_before=before[1], level_after=after[1])
        for before, after in zip(levels, levels[1:], strict=False)
        if after[1] - before[1] >= REFUEL_MIN_POINTS
    ]


def _nearest_km(moment: datetime, mileage: Sequence[Point]) -> float | None:
    """The mileage measured nearest in time to `moment`."""
    if not mileage:
        return None
    return min(mileage, key=lambda point: abs((point[0] - moment).total_seconds()))[1]


def consumption_since_refuel(
    litres: Sequence[Point], mileage: Sequence[Point]
) -> Consumption | None:
    """Consumption from the last refuel (or the first measurement) to the latest.

    `litres` must hold measurements, one per distinct BMW stamp. `None` when
    the stretch is shorter than `MIN_CONSUMPTION_KM`, when there is no mileage
    to pair with, or when the litres did not go down.
    """
    points = sorted(litres, key=lambda point: point[0])
    start = 0
    for index in range(1, len(points)):
        if points[index][1] - points[index - 1][1] > REFUEL_MIN_LITRES:
            start = index
    stretch = points[start:]
    if len(stretch) < 2:
        return None
    (since, first_litres), (until, last_litres) = stretch[0], stretch[-1]
    first_km, last_km = _nearest_km(since, mileage), _nearest_km(until, mileage)
    if first_km is None or last_km is None:
        return None
    km = last_km - first_km
    used = first_litres - last_litres
    if km < MIN_CONSUMPTION_KM or used <= 0:
        return None
    return Consumption(
        litres=used, km=km, since=since, until=until, after_refuel=start > 0
    )

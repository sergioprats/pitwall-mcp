"""Slow-leak detection, comparing each wheel with its axle partner.

Verified 2026-09-14: after a long drive both the pressures and their targets
rose with the tyres' heat, and the four tyre temperatures arrive empty on this
car. Watching one wheel against its own target over time would mistake the
weather for a leak.

Two wheels of the same axle, read in the same response, share temperature and
load. The gap between their deficits cancels both, and a slow leak is exactly
what moves it. Pressures come in 10 kPa steps, so a single step is noise: only
a gap that has grown by two steps, and stands at two steps or more, is flagged.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from .descriptors import tyre_descriptor
from .formatting import parse_numeric

#: Fewer events than this is a snapshot, not a trend.
MIN_EVENTS: Final = 3

#: Three reads in one afternoon are one moment.
MIN_SPAN: Final = timedelta(days=1)

#: Two sensor steps of 10 kPa.
LEAK_THRESHOLD_KPA: Final = 20

AXLES: Final = (("row1", "delantero"), ("row2", "trasero"))


@dataclass(frozen=True)
class AxleTrend:
    """How the left-right gap of one axle evolved.

    `gap` is the left wheel's deficit against its target minus the right
    wheel's, in kPa: positive means the left one is lower.
    """

    axle: str
    events: int
    first_gap: int
    last_gap: int
    flagged: bool

    @property
    def lower_side(self) -> str | None:
        """Which wheel is lower now, or `None` when they are level."""
        if self.last_gap > 0:
            return "izquierda"
        if self.last_gap < 0:
            return "derecha"
        return None


def _gap(values: dict[str, str | None], row: str) -> float | None:
    """The axle gap in one event, or `None` if any of the four values is missing."""
    numbers = [
        parse_numeric(values.get(tyre_descriptor(row, side, measure)))
        for side in ("left", "right")
        for measure in ("pressure", "pressureTarget")
    ]
    if any(number is None for number in numbers):
        return None
    left_pressure, left_target, right_pressure, right_target = numbers
    return (left_target - left_pressure) - (right_target - right_pressure)


def axle_trends(
    snapshots: Sequence[tuple[datetime, dict[str, str | None]]],
) -> list[AxleTrend] | None:
    """One trend per axle, or `None` when the history is too thin for any.

    An event with a missing or `-NA-` value is skipped, never read as zero.
    """
    trends: list[AxleTrend] = []
    for row, name in AXLES:
        points = [
            (moment, gap)
            for moment, values in snapshots
            if (gap := _gap(values, row)) is not None
        ]
        if len(points) < MIN_EVENTS or points[-1][0] - points[0][0] < MIN_SPAN:
            return None
        first, last = round(points[0][1]), round(points[-1][1])
        flagged = (
            abs(last) >= LEAK_THRESHOLD_KPA and abs(last) - abs(first) >= LEAK_THRESHOLD_KPA
        )
        trends.append(AxleTrend(name, len(points), first, last, flagged))
    return trends

"""Slow-leak detection by comparing each wheel with its axle partner.

Verified 2026-09-14: pressure AND target both rise with hot tyres, and the tyre
temperatures arrive empty. Comparing a wheel with its own target over time
would therefore confuse the weather with a leak. Two wheels of the same axle,
in the same response, share temperature and load: the gap between them is what
a slow leak moves. The sensor resolution is 10 kPa, so a 10 kPa move is noise.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pitwall_mcp.descriptors import WHEEL_POSITIONS, tyre_descriptor
from pitwall_mcp.tyre_trend import axle_trends

START = datetime(2026, 9, 7, 20, tzinfo=UTC)


def snap(day: int, pressures, targets=(250, 250, 250, 250)):
    """One reading event: FL, FR, RL, RR pressures and targets, in kPa."""
    values = {}
    for (row, side, _), pressure, target in zip(WHEEL_POSITIONS, pressures, targets, strict=True):
        values[tyre_descriptor(row, side, "pressure")] = str(pressure)
        values[tyre_descriptor(row, side, "pressureTarget")] = str(target)
    return START + timedelta(days=day), values


def rear(snapshots):
    """The rear-axle trend."""
    trends = axle_trends(snapshots)
    assert trends is not None
    return next(t for t in trends if t.axle == "trasero")


def test_a_gap_that_grows_on_one_axle_is_flagged():
    trend = rear(
        [
            snap(0, (250, 250, 250, 250)),
            snap(2, (250, 250, 230, 250)),
            snap(4, (250, 250, 210, 250)),
        ]
    )

    assert trend.flagged
    assert trend.lower_side == "izquierda"
    assert (trend.first_gap, trend.last_gap) == (0, 40)


def test_a_steady_gap_is_not_a_leak():
    trend = rear([snap(d, (250, 250, 230, 250)) for d in (0, 2, 4)])

    assert not trend.flagged
    assert (trend.first_gap, trend.last_gap) == (20, 20)


def test_the_real_september_series_is_not_a_leak():
    """Rear left 20, 10, 10 and 0 kPa below its partner, relative to target."""
    trend = rear(
        [
            snap(0, (240, 240, 220, 240)),
            snap(1, (240, 240, 230, 240), (260, 260, 250, 250)),
            snap(6, (270, 270, 250, 260), (280, 280, 270, 270)),
            snap(7, (290, 290, 270, 270), (290, 290, 280, 280)),
        ]
    )

    assert not trend.flagged
    assert (trend.first_gap, trend.last_gap) == (20, 0)


def test_hot_tyres_move_both_wheels_of_an_axle_and_cancel_out():
    trend = rear(
        [
            snap(0, (250, 250, 250, 250), (250, 250, 250, 250)),
            snap(2, (270, 270, 270, 270), (270, 270, 270, 270)),
            snap(4, (290, 290, 290, 290), (290, 290, 290, 290)),
        ]
    )

    assert not trend.flagged
    assert trend.last_gap == 0


def test_a_move_of_one_sensor_step_is_noise():
    trend = rear([snap(0, (250, 250, 250, 250)), snap(2, (250, 250, 250, 250)),
                  snap(4, (250, 250, 240, 250))])

    assert not trend.flagged


def test_the_right_wheel_can_be_the_one_losing():
    trend = rear(
        [
            snap(0, (250, 250, 250, 250)),
            snap(2, (250, 250, 250, 230)),
            snap(4, (250, 250, 250, 220)),
        ]
    )

    assert trend.flagged
    assert trend.lower_side == "derecha"


def test_too_few_readings_give_no_trend():
    assert axle_trends([snap(0, (250,) * 4), snap(2, (250,) * 4)]) is None


def test_readings_within_one_day_give_no_trend():
    """Three reads in one afternoon are one moment, not a trend."""
    snapshots = [snap(0, (250,) * 4) for _ in range(3)]
    snapshots = [(START + timedelta(hours=h), values) for h, (_, values) in enumerate(snapshots)]

    assert axle_trends(snapshots) is None


def test_an_event_missing_a_measurement_is_skipped_not_read_as_zero():
    """A -NA- tyre must not become a 250 kPa gap."""
    bad = snap(2, (250, 250, 250, 250))
    bad[1][tyre_descriptor("row2", "left", "pressure")] = "-NA-"
    trend = rear([snap(0, (250,) * 4), bad, snap(4, (250,) * 4), snap(6, (250,) * 4)])

    assert trend.events == 3
    assert not trend.flagged

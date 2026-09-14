"""Reading history: deduplication, series order and the phase-2 `source` column."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from conftest import FAKE_VIN, load_fixture

from pitwall_mcp.descriptors import BATTERY_VOLTAGE, TRAVELLED_DISTANCE
from pitwall_mcp.storage.history import HistoryStore

NOON = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


def _entries(payload):
    """Flatten a telematic fixture into what `record` expects."""
    return {
        descriptor: {
            "value": entry.get("value"),
            "unit": entry.get("unit"),
            "timestamp": entry.get("timestamp"),
        }
        for descriptor, entry in payload["telematicData"].items()
    }


def test_a_full_container_is_recorded(db):
    """Every descriptor in the response becomes a row."""
    store = HistoryStore(db)
    entries = _entries(load_fixture("telematic_full.json"))
    assert store.record(FAKE_VIN, entries, moment=NOON) == len(entries)


def test_rereading_the_same_timestamp_does_not_inflate_the_series(db):
    """Polling inside the TTL must not create fake data points."""
    store = HistoryStore(db)
    entries = _entries(load_fixture("telematic_full.json"))
    store.record(FAKE_VIN, entries, moment=NOON)
    again = store.record(FAKE_VIN, entries, moment=NOON + timedelta(hours=13))
    assert again == 0


def test_a_new_bmw_timestamp_creates_a_new_point(db):
    """A genuinely new reading does extend the series."""
    store = HistoryStore(db)
    store.record(
        FAKE_VIN,
        {BATTERY_VOLTAGE: {"value": "12.4", "unit": "V", "timestamp": "2026-09-05T07:00:00Z"}},
        moment=NOON,
    )
    store.record(
        FAKE_VIN,
        {BATTERY_VOLTAGE: {"value": "12.1", "unit": "V", "timestamp": "2026-09-06T07:00:00Z"}},
        moment=NOON + timedelta(days=1),
    )
    series = store.series(FAKE_VIN, BATTERY_VOLTAGE)
    assert [reading.value for reading in series] == ["12.4", "12.1"]


def test_snapshots_rebuild_what_each_response_carried(db):
    """An unchanged value is deduplicated, but it was still in the response.

    The target kept its BMW timestamp, so the second read inserted no row for it;
    the snapshot of that read must still carry it, next to the new pressure.
    """
    store = HistoryStore(db)
    pressure, target = "p", "t"
    store.record(
        FAKE_VIN,
        {
            pressure: {"value": "240", "unit": "kPa", "timestamp": "2026-09-07T20:40:00Z"},
            target: {"value": "250", "unit": "kPa", "timestamp": "2026-09-07T08:00:00Z"},
        },
        moment=NOON,
    )
    store.record(
        FAKE_VIN,
        {
            pressure: {"value": "230", "unit": "kPa", "timestamp": "2026-09-08T18:00:00Z"},
            target: {"value": "250", "unit": "kPa", "timestamp": "2026-09-07T08:00:00Z"},
        },
        moment=NOON + timedelta(days=1),
    )

    snapshots = store.snapshots(FAKE_VIN, (pressure, target))

    assert [moment for moment, _ in snapshots] == [NOON, NOON + timedelta(days=1)]
    assert [values for _, values in snapshots] == [
        {pressure: "240", target: "250"},
        {pressure: "230", target: "250"},
    ]


def test_snapshots_ignore_descriptors_not_asked_for(db):
    store = HistoryStore(db)
    store.record(
        FAKE_VIN,
        {BATTERY_VOLTAGE: {"value": "14.39", "unit": "V", "timestamp": "2026-09-07T08:55:27Z"}},
        moment=NOON,
    )

    assert store.snapshots(FAKE_VIN, (TRAVELLED_DISTANCE,)) == []


def test_series_is_ordered_oldest_first(db):
    """The tools read a series, so its order has to be chronological."""
    store = HistoryStore(db)
    for day, value in enumerate(["12.6", "12.4", "12.1"]):
        store.record(
            FAKE_VIN,
            {
                BATTERY_VOLTAGE: {
                    "value": value,
                    "unit": "V",
                    "timestamp": f"2026-09-0{day + 1}T07:00:00Z",
                }
            },
            moment=NOON + timedelta(days=day),
        )
    series = store.series(FAKE_VIN, BATTERY_VOLTAGE)
    assert [r.value for r in series] == ["12.6", "12.4", "12.1"]
    assert series[0].recorded_at < series[-1].recorded_at


def test_values_are_stored_exactly_as_bmw_sent_them(db):
    """`-NA-` is preserved as text, never converted to a number on the way in."""
    store = HistoryStore(db)
    entries = _entries(load_fixture("telematic_na_pressure.json"))
    store.record(FAKE_VIN, entries, moment=NOON)
    reading = store.latest(
        FAKE_VIN, "vehicle.chassis.axle.row2.wheel.right.tire.pressure"
    )
    assert reading.value == "-NA-"


def test_missing_bmw_timestamp_becomes_an_empty_string(db):
    """NULLs would defeat the unique index, so absent timestamps become ''."""
    store = HistoryStore(db)
    store.record(FAKE_VIN, {TRAVELLED_DISTANCE: {"value": "1", "unit": "km"}}, moment=NOON)
    assert store.record(
        FAKE_VIN, {TRAVELLED_DISTANCE: {"value": "1", "unit": "km"}}, moment=NOON
    ) == 0
    assert store.latest(FAKE_VIN, TRAVELLED_DISTANCE).source_timestamp == ""


def test_rest_and_mqtt_are_separate_sources(db):
    """The phase-2 daemon writes to the same table without a migration."""
    store = HistoryStore(db)
    entry = {BATTERY_VOLTAGE: {"value": "12.4", "unit": "V", "timestamp": "2026-09-05T07:00:00Z"}}
    assert store.record(FAKE_VIN, entry, source="rest", moment=NOON) == 1
    assert store.record(FAKE_VIN, entry, source="mqtt", moment=NOON) == 1
    assert {r.source for r in store.series(FAKE_VIN, BATTERY_VOLTAGE)} == {"rest", "mqtt"}


def test_stats_report_how_much_evidence_exists(db):
    """The diagnosis tools must be able to say how many readings they hold."""
    store = HistoryStore(db)
    store.record(FAKE_VIN, _entries(load_fixture("telematic_partial.json")), moment=NOON)
    stats = store.descriptor_stats(FAKE_VIN)
    count, first, last = stats[TRAVELLED_DISTANCE]
    assert count == 1
    assert first == last == NOON


def test_repeated_values_collapse_into_one_change(db):
    """Some descriptors are stamped at REQUEST time, not at measurement time.

    `battery.serviceDemand.replace` gets a new timestamp on every call even when
    the reading never moved, so counting rows would overstate the evidence.
    """
    store = HistoryStore(db)
    descriptor = "vehicle.electricalSystem.battery.serviceDemand.replace"
    for minute, stamp in enumerate(
        ["2026-09-07T21:35:06.005Z", "2026-09-07T22:07:26.877Z", "2026-09-07T23:01:00.000Z"]
    ):
        store.record(
            FAKE_VIN,
            {descriptor: {"value": "200", "unit": None, "timestamp": stamp}},
            moment=NOON + timedelta(minutes=minute),
        )

    assert len(store.series(FAKE_VIN, descriptor)) == 3
    assert len(store.changes(FAKE_VIN, descriptor)) == 1


def test_a_real_change_survives_the_collapse(db):
    """Collapsing repetitions must not hide the transition we are looking for."""
    store = HistoryStore(db)
    for day, value in enumerate(["200", "200", "140", "140", "110"]):
        store.record(
            FAKE_VIN,
            {
                BATTERY_VOLTAGE: {
                    "value": value,
                    "unit": None,
                    "timestamp": f"2026-09-0{day + 1}T07:00:00Z",
                }
            },
            moment=NOON + timedelta(days=day),
        )

    changes = store.changes(FAKE_VIN, BATTERY_VOLTAGE)
    assert [reading.value for reading in changes] == ["200", "140", "110"]


def test_collapsing_an_empty_series_is_safe(db):
    """Nothing recorded means nothing to collapse, not an error."""
    assert HistoryStore(db).changes(FAKE_VIN, TRAVELLED_DISTANCE) == []

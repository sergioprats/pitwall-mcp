"""The local HTML report built from the reading history.

NOTHING HERE TOUCHES THE NETWORK OR THE API. The report is generated from the
SQLite history alone, so these tests seed `HistoryStore` by hand and assert on
the rendered page.

The point of the report is honesty about evidence: how many DISTINCT
observations exist, which of the three empty states each descriptor is in, and
where a single point must not be drawn as a trend.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from conftest import FAKE_VIN

from pitwall_mcp.descriptors import (
    BATTERY_VOLTAGE,
    CBS_COUNT,
    CONDITION_BASED_SERVICES,
    TRAVELLED_DISTANCE,
    tyre_descriptor,
)
from pitwall_mcp.report import mask_vin, render_report
from pitwall_mcp.storage.history import HistoryStore

NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)


@pytest.fixture
def history(db) -> HistoryStore:
    """An empty history store on the in-memory database."""
    return HistoryStore(db)


def record(
    history: HistoryStore,
    descriptor: str,
    value: str | None,
    *,
    unit: str | None = None,
    minutes_ago: int = 0,
    stamp: str | None = None,
) -> None:
    """Store one reading, dated relative to `NOW`."""
    moment = NOW - timedelta(minutes=minutes_ago)
    history.record(
        FAKE_VIN,
        {descriptor: {"value": value, "unit": unit, "timestamp": stamp or moment.isoformat()}},
        moment=moment,
    )


# --- Identity and header ---------------------------------------------------


def test_mask_vin_keeps_only_the_last_four_characters():
    assert mask_vin("WBAU11030P0FAKE01") == "*************KE01"


def test_header_shows_the_masked_vin_and_never_the_full_one(history):
    html = render_report(history, FAKE_VIN, now=NOW)

    assert "*************KE01" in html
    assert FAKE_VIN not in html


def test_full_vin_is_shown_only_when_explicitly_asked_for(history):
    html = render_report(history, FAKE_VIN, now=NOW, full_vin=True)

    assert FAKE_VIN in html


def test_report_states_that_it_spent_no_requests(history):
    html = render_report(history, FAKE_VIN, now=NOW)

    assert "Ninguna peticion a la API" in html


def test_empty_history_says_so_instead_of_rendering_a_blank_page(history):
    html = render_report(history, FAKE_VIN, now=NOW)

    assert "El historico local no tiene ninguna lectura todavia" in html


def test_report_carries_no_external_resources(history):
    record(history, TRAVELLED_DISTANCE, "12345")

    html = render_report(history, FAKE_VIN, now=NOW)

    assert "http://" not in html
    assert "https://" not in html
    assert "src=" not in html


# --- Series: what the evidence actually supports ---------------------------


def test_mileage_section_shows_the_latest_value(history):
    record(history, TRAVELLED_DISTANCE, "12000", unit="km", minutes_ago=120)
    record(history, TRAVELLED_DISTANCE, "12345", unit="km", minutes_ago=10)

    html = render_report(history, FAKE_VIN, now=NOW)

    assert "12.345" in html


def test_a_single_observation_declares_that_there_is_no_trend(history):
    record(history, TRAVELLED_DISTANCE, "12345", unit="km")

    html = render_report(history, FAKE_VIN, now=NOW)

    assert "1 observacion" in html
    assert "Un solo punto: no hay tendencia" in html


def test_a_single_observation_draws_no_line(history):
    record(history, TRAVELLED_DISTANCE, "12345", unit="km")

    html = render_report(history, FAKE_VIN, now=NOW)

    assert "<polyline" not in html


def test_several_distinct_observations_draw_a_sparkline(history):
    for index, value in enumerate(["12.1", "12.3", "12.6"]):
        record(history, BATTERY_VOLTAGE, value, unit="V", minutes_ago=60 - index * 10)

    html = render_report(history, FAKE_VIN, now=NOW)

    assert "<polyline" in html
    assert "3 observaciones" in html


def test_repeated_identical_values_count_as_a_single_observation(history):
    for index in range(4):
        record(
            history,
            BATTERY_VOLTAGE,
            "12.4",
            unit="V",
            minutes_ago=40 - index * 10,
            stamp=f"2026-09-08T11:2{index}:00.005Z",
        )

    html = render_report(history, FAKE_VIN, now=NOW)

    assert "1 observacion" in html
    assert "4 observaciones" not in html


# --- Condition Based Services ----------------------------------------------

CBS_ITEMS = [
    {
        "date": "2027-07",
        "description": "Next service due when the specified distance has been covered.",
        "id": 1,
        "messageType": "CBS",
        "status": "OK",
        "title": "Engine oil",
        "text": "-",
        "unitOfLengthRemaining": "14000",
    },
    {
        "date": "null",
        "description": "-",
        "id": 2,
        "messageType": "CBS",
        "status": "OK",
        "title": "Front Brake",
        "text": "-",
        "unitOfLengthRemaining": "-",
    },
]


def record_cbs(history: HistoryStore, *, count: str | None = None) -> None:
    """Store one CBS block exactly as BMW sends it: JSON inside a string."""
    record(history, CONDITION_BASED_SERVICES, json.dumps(CBS_ITEMS))
    if count is not None:
        record(history, CBS_COUNT, count)


def test_cbs_section_labels_each_item_and_shows_its_distance(history):
    record_cbs(history)

    html = render_report(history, FAKE_VIN, now=NOW)

    assert "Aceite de motor" in html
    assert "14.000 km" in html


def test_cbs_reports_both_numbers_and_declares_the_discrepancy(history):
    record_cbs(history, count="9")

    html = render_report(history, FAKE_VIN, now=NOW)

    assert "discrepancia" in html
    assert "9" in html
    assert "2 partidas" in html


def test_cbs_null_sentinel_is_never_printed_as_a_date(history):
    record_cbs(history)

    html = render_report(history, FAKE_VIN, now=NOW)

    assert "null" not in html
    assert "sin fecha" in html


def test_cbs_dash_sentinel_is_never_printed_as_zero_kilometres(history):
    record_cbs(history)

    html = render_report(history, FAKE_VIN, now=NOW)

    assert "<td>0 km</td>" not in html
    assert "sin kilometraje" in html


# --- Pressures --------------------------------------------------------------

FRONT_LEFT = tyre_descriptor("row1", "left", "pressure")
FRONT_LEFT_TARGET = tyre_descriptor("row1", "left", "pressureTarget")


def test_pressures_are_shown_in_bar_against_their_target(history):
    record(history, FRONT_LEFT, "230", unit="kPa")
    record(history, FRONT_LEFT_TARGET, "240", unit="kPa")

    html = render_report(history, FAKE_VIN, now=NOW)

    assert "2.30 bar" in html
    assert "-0.10 bar" in html


def test_a_pressure_marked_no_measurement_is_never_rendered_as_zero(history):
    record(history, FRONT_LEFT, "-NA-", unit="kPa")
    record(history, FRONT_LEFT_TARGET, "240", unit="kPa")

    html = render_report(history, FAKE_VIN, now=NOW)

    assert "sin medida" in html
    assert "0.00 bar" not in html


# --- Coverage: the three empty states --------------------------------------


def test_coverage_marks_a_descriptor_that_arrived_present_but_empty(history):
    record(history, BATTERY_VOLTAGE, None, unit="V")

    html = render_report(history, FAKE_VIN, now=NOW)

    assert "presente y vacio" in html


def test_coverage_marks_a_descriptor_that_said_no_measurement(history):
    record(history, FRONT_LEFT, "-NA-", unit="kPa")

    html = render_report(history, FAKE_VIN, now=NOW)

    assert "sin medida" in html


def test_coverage_marks_a_descriptor_never_seen_in_any_reading(history):
    record(history, TRAVELLED_DISTANCE, "12345", unit="km")

    html = render_report(history, FAKE_VIN, now=NOW)

    assert "nunca ha llegado" in html


def test_stored_values_are_html_escaped(history):
    record(history, TRAVELLED_DISTANCE, "<b>boom</b>", unit="km")

    html = render_report(history, FAKE_VIN, now=NOW)

    assert "<b>boom</b>" not in html
    assert "&lt;b&gt;boom&lt;/b&gt;" in html


# --- The state ledger: the report's headline fact --------------------------


def test_the_ledger_carries_one_mark_per_container_descriptor(history):
    from pitwall_mcp.descriptors import CONTAINER_DESCRIPTORS

    record(history, TRAVELLED_DISTANCE, "12345", unit="km")

    html = render_report(history, FAKE_VIN, now=NOW)

    assert html.count("class='tick") == len(CONTAINER_DESCRIPTORS)


def test_the_ledger_counts_how_many_descriptors_actually_carry_a_value(history):
    from pitwall_mcp.descriptors import CONTAINER_DESCRIPTORS

    record(history, TRAVELLED_DISTANCE, "12345", unit="km")
    record(history, BATTERY_VOLTAGE, None, unit="V")

    html = render_report(history, FAKE_VIN, now=NOW)

    assert f"1 de {len(CONTAINER_DESCRIPTORS)} descriptores con valor" in html


def test_each_mark_names_the_descriptor_and_its_state(history):
    record(history, BATTERY_VOLTAGE, None, unit="V")

    html = render_report(history, FAKE_VIN, now=NOW)

    assert f"{BATTERY_VOLTAGE}: presente y vacio" in html


def test_the_masthead_states_how_much_history_there_is(history):
    record(history, TRAVELLED_DISTANCE, "12000", unit="km", minutes_ago=2880)
    record(history, TRAVELLED_DISTANCE, "12345", unit="km", minutes_ago=10)

    html = render_report(history, FAKE_VIN, now=NOW)

    assert "2 lecturas guardadas" in html
    assert "2026-09-06" in html


def test_the_sparkline_marks_the_latest_observation(history):
    for index, value in enumerate(["12.10", "12.30", "12.60"]):
        record(history, BATTERY_VOLTAGE, value, unit="V", minutes_ago=60 - index * 10)

    html = render_report(history, FAKE_VIN, now=NOW)

    assert "<circle" in html


def test_the_sparkline_states_the_range_it_spans(history):
    for index, value in enumerate(["12.10", "12.30", "12.60"]):
        record(history, BATTERY_VOLTAGE, value, unit="V", minutes_ago=60 - index * 10)

    html = render_report(history, FAKE_VIN, now=NOW)

    assert "de 12.1 a 12.6 V" in html


def test_the_coverage_table_dates_rows_absolutely_not_by_relative_age(history):
    record(history, TRAVELLED_DISTANCE, "12345", unit="km")

    html = render_report(history, FAKE_VIN, now=NOW)

    assert "2026-09-08 12:00 UTC" in html
    assert "hace" not in html

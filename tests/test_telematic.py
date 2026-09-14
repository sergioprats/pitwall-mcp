"""Parsing a real `/telematicData` answer, with its four states and its CBS trap.

Every assertion here is checked against `telematic_real.json`, recorded from
this vehicle on 2026-09-07. Nothing is asserted about a shape we have not seen.
"""

from __future__ import annotations

from conftest import load_fixture

from pitwall_mcp.descriptors import (
    BATTERY_STATE_OF_CHARGE,
    BATTERY_VOLTAGE,
    CBS_COUNT,
    CONDITION_BASED_SERVICES,
    CONTAINER_DESCRIPTORS,
    DEEP_SLEEP_MODE_ACTIVE,
    SERVICE_DISTANCE_NEXT,
    TRAVELLED_DISTANCE,
)
from pitwall_mcp.telematic import (
    CbsItem,
    TelematicSnapshot,
    ValueState,
    parse_cbs,
    parse_check_control,
)


def real() -> TelematicSnapshot:
    """The snapshot as this U11 really answered."""
    return TelematicSnapshot.from_payload(load_fixture("telematic_real.json"))


def brake_warning() -> TelematicSnapshot:
    """The answer of 2026-09-13, 157 km later: front brakes PENDING, one CCM."""
    return TelematicSnapshot.from_payload(load_fixture("telematic_brake_warning.json"))


# --- Check Control messages ------------------------------------------------


def test_check_control_needs_a_second_json_decode():
    """Like CBS, the value is a string holding a JSON array."""
    messages = parse_check_control(brake_warning())

    assert [message.text for message in messages] == ["The brake pads need to be replaced."]
    assert messages[0].id == 907


def test_the_ccm_status_string_null_is_not_a_status():
    """CCM writes the literal "NULL", in capitals, where CBS would write "OK"."""
    assert parse_check_control(brake_warning())[0].status is None


def test_the_ccm_distance_field_is_a_mileage_not_a_remaining_distance():
    """Same key as CBS, other meaning: 48376 sits between the odometer readings
    of 8 Sep (48283) and 13 Sep (48440), while CBS gives the brakes 1900 km."""
    message = parse_check_control(brake_warning())[0]

    assert message.mileage_km == 48376


def test_empty_check_control_yields_none():
    """No value means nothing to decode, not an empty list pretending to know."""
    assert parse_check_control(real()) is None


# --- The four states -------------------------------------------------------


def test_the_real_answer_has_all_keys_but_not_all_values():
    """32 keys, 21 values. Counting keys would overstate what we know."""
    snapshot = real()
    assert len(snapshot.entries) == 32
    assert len([d for d in CONTAINER_DESCRIPTORS if snapshot.get(d).has_value]) == 21
    assert len(snapshot.with_state(ValueState.EMPTY)) == 11

    # Recorded from the 32-key container of 7 Sep: the ten promoted on 14 Sep
    # were simply not asked for then.
    from pitwall_mcp.descriptors import DIAGNOSTIC_DESCRIPTORS, FUEL_DESCRIPTORS, IS_MOVING

    assert set(snapshot.missing_from(CONTAINER_DESCRIPTORS)) == {
        *FUEL_DESCRIPTORS,
        *DIAGNOSTIC_DESCRIPTORS,
        IS_MOVING,
    }


def test_an_empty_value_is_its_own_state():
    """`value: null` is not absence, and not `-NA-`."""
    entry = real().get(DEEP_SLEEP_MODE_ACTIVE)
    assert entry.state is ValueState.EMPTY
    assert not entry.has_value
    assert entry.as_float() is None
    assert entry.moment is None


def test_an_empty_value_keeps_its_unit():
    """BMW still tells us what the field would be measured in."""
    entry = real().get(BATTERY_STATE_OF_CHARGE)
    assert entry.state is ValueState.EMPTY
    assert entry.unit == "%"


def test_an_absent_descriptor_is_never_none():
    """Asking for something not in the response yields an ABSENT entry."""
    entry = real().get("vehicle.status.doorDriverFront")
    assert entry.state is ValueState.ABSENT
    assert not entry.has_value


def test_a_no_measurement_is_distinct_from_empty():
    """`-NA-` is the vehicle answering, not the vehicle staying silent."""
    snapshot = TelematicSnapshot.from_payload(load_fixture("telematic_na_pressure.json"))
    entry = snapshot.get("vehicle.chassis.axle.row2.wheel.right.tire.pressure")
    assert entry.state is ValueState.NO_MEASUREMENT
    assert not entry.has_value
    assert entry.as_float() is None


def test_real_values_parse_as_numbers():
    """The API types everything as a string; we convert on the way out."""
    snapshot = real()
    assert snapshot.get(TRAVELLED_DISTANCE).as_int() == 48260
    assert snapshot.get(SERVICE_DISTANCE_NEXT).as_int() == 2140
    assert snapshot.get(BATTERY_VOLTAGE).as_float() == 14.39


def test_the_oldest_and_newest_moments_are_available():
    """"Fecha del dato mas antiguo utilizado" needs both ends of the range."""
    snapshot = real()
    oldest, newest = snapshot.oldest_moment(), snapshot.newest_moment()
    assert oldest is not None and newest is not None
    assert oldest <= newest


# --- Condition Based Services ----------------------------------------------


def test_cbs_needs_a_second_json_decode():
    """Its `value` is a STRING containing JSON. One decode gives you a string."""
    entry = real().get(CONDITION_BASED_SERVICES)
    assert entry.has_value
    assert isinstance(entry.value, str)
    assert entry.value.lstrip().startswith("[")

    block = parse_cbs(real())
    assert block is not None
    assert len(block.items) == 5


def test_the_five_real_cbs_items_are_decoded():
    """Exactly what this vehicle reported, ids included."""
    block = parse_cbs(real())
    by_id = {item.id: item for item in block.items}

    assert set(by_id) == {1, 2, 3, 32, 100}
    assert by_id[1].title == "Engine oil"
    assert by_id[1].distance_km == 14000
    assert by_id[1].date_text == "2027-07"
    assert by_id[2].title == "Front Brake"
    assert by_id[2].distance_km == 2100


def test_the_literal_string_null_is_not_a_date():
    """`"date": "null"` is four characters, not a JSON null. Parsing it lies."""
    block = parse_cbs(real())
    front_brake = next(item for item in block.items if item.id == 2)
    assert front_brake.date_text is None


def test_a_dash_is_not_a_distance():
    """`unitOfLengthRemaining: "-"` means the item has no distance."""
    block = parse_cbs(real())
    inspection = next(item for item in block.items if item.id == 32)
    assert inspection.distance_km is None
    assert inspection.date_text == "2027-01"


def test_known_items_get_a_spanish_label_unknown_ones_keep_bmws():
    """Translating what we know, quoting verbatim what we do not."""
    assert CbsItem.from_raw({"id": 1, "title": "Engine oil"}).label == "Aceite de motor"
    assert CbsItem.from_raw({"id": 999, "title": "Something New"}).label == "Something New"


def test_the_count_discrepancy_is_detected_not_hidden():
    """BMW said 9 with 5 items. Both numbers get reported."""
    snapshot = real()
    reported = snapshot.get(CBS_COUNT).as_int()
    block = parse_cbs(snapshot, reported_count=reported)

    assert reported == 9
    assert len(block.items) == 5
    assert block.count_matches is False


def test_no_counter_means_no_verdict_about_matching():
    """Without a counter there is nothing to compare, and we say so with None."""
    assert parse_cbs(real()).count_matches is None


def test_cbs_without_a_value_yields_none():
    """A container that did not return it must not produce a fake breakdown."""
    snapshot = TelematicSnapshot.from_payload(load_fixture("telematic_partial.json"))
    assert parse_cbs(snapshot) is None


def test_undecodable_cbs_yields_none_not_a_partial_guess():
    """Better no breakdown than an invented one."""
    snapshot = TelematicSnapshot.from_payload(
        {"telematicData": {CONDITION_BASED_SERVICES: {"value": "esto no es json"}}}
    )
    assert parse_cbs(snapshot) is None


def test_an_unusable_payload_gives_an_empty_snapshot():
    """A malformed response must not raise on the way in."""
    assert TelematicSnapshot.from_payload({"nada": 1}).entries == {}
    assert TelematicSnapshot.from_payload("no soy un objeto").entries == {}

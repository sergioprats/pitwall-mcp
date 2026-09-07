"""Units, timestamps, provenance and the wording of an absence."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pitwall_mcp.formatting import (
    build_pressure,
    explain_missing,
    format_battery_recharge,
    format_battery_replace,
    format_moment,
    format_tristate,
    format_ttl,
    is_no_measurement,
    kpa_to_bar,
    parse_numeric,
    provenance_line,
)

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


def test_kpa_becomes_bar():
    """250 kPa is 2.50 bar."""
    assert kpa_to_bar(250) == 2.5


def test_no_measurement_is_never_zero():
    """`-NA-` means no measurement. Reading it as 0 bar would be a flat tyre."""
    assert is_no_measurement("-NA-")
    assert parse_numeric("-NA-") is None
    reading = build_pressure("Trasera derecha", "-NA-", "250")
    assert reading.pressure_bar is None
    assert "sin medida" in reading.describe()
    assert "0.00" not in reading.describe()


def test_pressure_is_shown_against_its_target():
    """The differential is the useful part, not the absolute value."""
    text = build_pressure("Delantera izquierda", "241", "250").describe()
    assert "2.41 bar" in text
    assert "objetivo 2.50 bar" in text
    assert "-0.09 bar" in text


def test_pressure_over_target_shows_a_plus_sign():
    """An over-inflated tyre reads as positive, unambiguously."""
    assert "+0.10 bar" in build_pressure("Trasera izquierda", "260", "250").describe()


def test_pressure_without_a_target_says_so():
    """No target means no comparison, and the text admits it."""
    text = build_pressure("Trasera derecha", "241", "-NA-").describe()
    assert "sin presion objetivo" in text


def test_tristate_unknown_is_not_false():
    """`ASN_isUnknown` is a third state, not a negative."""
    assert format_tristate("ASN_isTrue") == "si"
    assert format_tristate("ASN_isFalse") == "no"
    assert "desconocido" in format_tristate("ASN_isUnknown")


def test_battery_replace_is_a_code_not_a_percentage():
    """200/140/110/80 are health codes."""
    assert "adecuada" in format_battery_replace("200")
    assert "degradada" in format_battery_replace("80")
    assert "no documentado" in format_battery_replace("999")


def test_battery_recharge_reads_one_as_yes():
    """1 means the vehicle is asking for a recharge."""
    assert format_battery_recharge("1").startswith("si")
    assert format_battery_recharge("0") == "no"
    assert format_battery_recharge(None) == "sin dato"


def test_moment_shows_absolute_and_relative_time():
    """Both are needed: the date, and how old it is."""
    text = format_moment("2026-09-05T07:00:00Z", now=NOW)
    assert "2026-09-05 07:00 UTC" in text
    assert "hace 5 horas" in text


def test_missing_timestamp_is_stated_not_faked():
    """No timestamp is said out loud."""
    assert format_moment(None) == "sin marca de tiempo"


def test_provenance_names_the_source_and_both_timestamps():
    """CLAUDE.md rule 4, in one line."""
    line = provenance_line(
        "cache",
        source_timestamp="2026-09-05T07:00:00Z",
        read_at="2026-09-05T11:00:00Z",
        now=NOW,
    )
    assert "cache local" in line
    assert "Fecha del dato (BMW)" in line
    assert "Lectura realizada" in line


def test_absence_explains_which_of_the_four_reasons_applies():
    """Never a bare null: always a reason (rule 6)."""
    not_catalogued = explain_missing(
        "vehicle.software.version", in_catalogue=False, in_container=False
    )
    assert "no existe en el catalogo" in not_catalogued

    not_in_container = explain_missing(
        "vehicle.status.doorDriverFront", in_catalogue=True, in_container=False, container_id="c1"
    )
    assert "NO esta en el contenedor" in not_in_container
    assert "bootstrap_containers.py" in not_in_container

    not_emitted = explain_missing(
        "vehicle.status.conditionBasedServices", in_catalogue=True, in_container=True
    )
    assert "no disponible para este" in not_emitted
    assert "sueno profundo" in not_emitted


def test_a_future_moment_reads_as_dentro_de():
    """The quota reset is in the future; calling that "hace" would be nonsense."""
    text = format_moment("2026-09-05T18:00:00Z", now=NOW)
    assert "dentro de 6 horas" in text


def test_ttls_are_rendered_in_plain_spanish():
    """`30 days, 0:00:00` is a repr, not an answer."""
    assert format_ttl(timedelta(days=30)) == "30 dias"
    assert format_ttl(timedelta(days=1)) == "1 dia"
    assert format_ttl(timedelta(hours=12)) == "12 h"

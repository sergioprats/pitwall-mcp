"""The capture script: it must not spend quota by accident, nor leak a VIN.

Both failure modes are quiet ones. A stray request costs one of fifty; a VIN
left inside a fixture gets committed to a public repository.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from conftest import FAKE_CONTAINER, FAKE_VIN, FakeTokens, load_fixture

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import capture  # noqa: E402

from pitwall_mcp.cardata.client import CarDataAdapter  # noqa: E402
from pitwall_mcp.storage.db import Database  # noqa: E402

REAL_VIN = "WBA11223344556677"
REAL_GCID = "0f1e2d3c-aaaa-bbbb-cccc-1234567890ab"


# --- Nothing is sent by accident -------------------------------------------


def test_no_flag_means_no_request(capsys):
    """Running it bare must not cost anything."""
    args = capture.parse_args([])
    assert not any(getattr(args, name) for name in capture.ACTIONS)
    assert args.status is False


def test_the_endpoint_flags_are_mutually_exclusive():
    """Two captures in one invocation would spend two requests unannounced."""
    with pytest.raises(SystemExit):
        capture.parse_args(["--mappings", "--telematic"])


def test_captures_default_to_a_gitignored_directory():
    """Raw captures carry the VIN, so they must land somewhere ignored."""
    assert capture.parse_args([]).out == "captures"
    gitignore = (Path(__file__).resolve().parents[1] / ".gitignore").read_text(encoding="utf-8")
    assert "captures/" in gitignore


# --- Anonymisation ---------------------------------------------------------


def test_the_vin_is_replaced_everywhere_including_keys():
    """A VIN can be an object key as easily as a value."""
    payload = {
        "vin": REAL_VIN,
        REAL_VIN: {"nested": [f"el coche {REAL_VIN} va bien"]},
        "otro": {"lista": [{"vin": REAL_VIN}]},
    }
    clean = capture.anonymise(payload, {REAL_VIN: capture.FIXTURE_VIN})

    assert REAL_VIN not in json.dumps(clean)
    assert clean["vin"] == capture.FIXTURE_VIN
    assert capture.FIXTURE_VIN in clean
    assert clean["otro"]["lista"][0]["vin"] == capture.FIXTURE_VIN


def test_anonymising_preserves_the_shape(settings):
    """A fixture is useless if it no longer looks like BMW's answer."""
    payload = load_fixture("telematic_full.json")
    clean = capture.anonymise(payload, {"nada-que-sustituir": "x"})
    assert clean == payload


def test_replacements_cover_vin_container_and_any_vin_in_the_payload(settings):
    """Including VINs we never configured, which /mappings hands us."""
    payload = [
        {"vin": FAKE_VIN, "mappingType": "PRIMARY"},
        {"vin": "WBAOTROVIN1234567", "mappingType": "SECONDARY"},
    ]
    replacements = capture.build_replacements(settings, payload)

    assert replacements[FAKE_VIN] == capture.FIXTURE_VIN
    assert "WBAOTROVIN1234567" in replacements
    assert FAKE_CONTAINER in replacements


def test_numbers_and_booleans_survive_untouched():
    """Only strings are rewritten; a pressure must stay a pressure."""
    payload = {"value": 241, "ok": True, "nada": None, "texto": REAL_VIN}
    clean = capture.anonymise(payload, {REAL_VIN: capture.FIXTURE_VIN})
    assert clean == {"value": 241, "ok": True, "nada": None, "texto": capture.FIXTURE_VIN}


def test_an_empty_secret_is_not_replaced():
    """An unconfigured VIN must not turn every string into the placeholder."""
    clean = capture.anonymise({"a": "hola"}, {"": "XXX"})
    assert clean == {"a": "hola"}


# --- Reporting -------------------------------------------------------------


def test_the_telematic_report_names_what_did_not_arrive(capsys, settings):
    """The point of the first call: which descriptors this U11 really emits."""
    capture.report_telematic(load_fixture("telematic_partial.json"), settings)
    out = capsys.readouterr().out

    assert "Descriptores pedidos    : 42" in out
    assert "Con valor               : 4" in out
    assert "Ausentes de la respuesta: 38" in out
    assert "vehicle.electricalSystem.battery.voltage" in out
    assert "no disponible para este" in out  # the rule 6 explanation


def test_an_empty_value_is_not_counted_as_data(capsys, settings):
    """A key can arrive with `value: null`. Present is not the same as useful.

    The real U11 answered with all 32 keys but only 21 values; reporting 32
    would have overstated what we actually know.
    """
    capture.report_telematic(load_fixture("telematic_real.json"), settings)
    out = capsys.readouterr().out

    assert "Con valor               : 21" in out
    assert "Presentes pero VACIOS   : 11" in out
    # The 7 Sep answer came from the 32-key container: the ten added later are absent.
    assert "Ausentes de la respuesta: 10" in out
    assert "PRESENTES PERO VACIOS" in out
    assert "vehicle.vehicle.deepSleepModeActive" in out


def test_the_report_says_so_when_cbs_is_absent(capsys, settings):
    """Without the breakdown the only honest fallback is the global figure."""
    capture.report_telematic(load_fixture("telematic_partial.json"), settings)
    out = capsys.readouterr().out

    assert "NO ha llegado con valor" in out
    assert "serviceDistance.next" in out


def test_the_report_decodes_the_double_encoded_cbs(capsys, settings):
    """Its `value` is a STRING containing JSON: one decode is not enough."""
    capture.report_telematic(load_fixture("telematic_real.json"), settings)
    out = capsys.readouterr().out

    assert "5 partida(s)" in out
    assert "Engine oil" in out
    assert "Front Brake" in out
    assert "es un centinela" in out  # "null" and "-" are flagged, not parsed


def test_the_report_explains_what_the_cbs_counter_is(capsys, settings):
    """The counter said 9 while the array held 5, and that is not a mismatch.

    The catalogue defines it as the maximum the vehicle can transmit, so both
    numbers are reported and the counter is explained.
    """
    capture.report_telematic(load_fixture("telematic_real.json"), settings)
    out = capsys.readouterr().out

    assert "MAXIMO transmisible" in out
    assert "transmitidas de verdad" in out
    assert "DISCREPANCIA" not in out


def test_the_mappings_report_flags_a_non_primary_vin(capsys):
    """Being SECONDARY means no data at all, and it must be shouted early."""
    capture.report_mappings(load_fixture("mappings_secondary.json"))
    out = capsys.readouterr().out

    assert "PRIMARY" in out
    assert "ATENCION" in out


# --- Wiring ----------------------------------------------------------------


async def test_a_capture_goes_through_the_cache_and_the_counter(settings, db, fake_client):
    """It must not be a back door around the quota guard."""
    fake_client.responses["get_mappings"] = load_fixture("mappings_list.json")
    adapter = CarDataAdapter(settings, db=db, tokens=FakeTokens())

    first = await adapter.list_vehicles()
    second = await adapter.list_vehicles()

    assert first.source == "api"
    assert second.source == "cache"
    assert adapter.quota.status().used == 1


def test_missing_vin_and_container_are_explained(bare_settings, tmp_path):
    """The script must say what to configure, not raise a KeyError."""
    from pitwall_mcp.cardata import errors

    with pytest.raises(errors.MissingConfigError) as vin_error:
        capture._require_vin(bare_settings)  # noqa: SLF001
    assert "PITWALL_VIN" in vin_error.value.message

    with pytest.raises(errors.MissingConfigError) as container_error:
        capture._require_container(bare_settings)  # noqa: SLF001
    assert "bootstrap_containers.py" in container_error.value.message


def test_the_script_never_creates_or_deletes_anything():
    """Read-only, like everything else that touches the vehicle."""
    source = (SCRIPTS / "capture.py").read_text(encoding="utf-8")
    for forbidden in ("create_container", "delete_container", "POST", "DELETE"):
        assert forbidden not in source, f"capture.py no deberia mencionar {forbidden}"


def test_the_database_is_the_configured_one(settings, tmp_path):
    """Captures feed the same history the MCP server reads."""
    db = Database(settings.db_path)
    try:
        assert settings.db_path.exists()
    finally:
        db.close()

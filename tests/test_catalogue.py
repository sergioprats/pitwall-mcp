"""Catalogue loading and search."""

from __future__ import annotations

from pitwall_mcp.tools import catalogue_tools


def test_literal_null_unit_is_normalised(catalogue):
    """The published catalogue has one entry whose unit is the string "null"."""
    entry = catalogue.get("vehicle.vehicle.travelledDistance")
    assert entry.unit is None
    assert entry.data_type == "float"


def test_exact_descriptor_wins(catalogue):
    """Searching a full descriptor puts it first."""
    hits = catalogue.search("vehicle.electricalSystem.battery.voltage")
    assert hits[0].entry.technical_descriptor == "vehicle.electricalSystem.battery.voltage"


def test_spanish_query_finds_english_entries(catalogue):
    """The catalogue is in English; queries arrive in Spanish."""
    hits = catalogue.search("presion neumatico delantera")
    descriptors = [hit.entry.technical_descriptor for hit in hits]
    assert "vehicle.chassis.axle.row1.wheel.left.tire.pressure" in descriptors


def test_accents_do_not_break_the_search(catalogue):
    """`presión` behaves like `presion`."""
    with_accent = catalogue.search("presión")
    without = catalogue.search("presion")
    assert [h.entry.technical_descriptor for h in with_accent] == [
        h.entry.technical_descriptor for h in without
    ]


def test_all_terms_must_match(catalogue):
    """A second term narrows the result instead of widening it."""
    one = catalogue.search("battery", limit=100)
    two = catalogue.search("battery voltage", limit=100)
    assert len(two) < len(one)


def test_electric_descriptors_are_hidden_by_default(catalogue):
    """This is a petrol car, so electric-only entries stay out unless asked for."""
    hidden = catalogue.search("high-voltage battery", limit=100)
    assert all(not hit.entry.is_electric_only for hit in hidden)
    shown = catalogue.search("high-voltage battery", limit=100, include_electric=True)
    assert any(hit.entry.is_electric_only for hit in shown)


def test_unknown_descriptor_returns_nothing(catalogue):
    """Something that does not exist finds nothing; it is not approximated."""
    assert catalogue.search("iStep firmware version") == []
    assert catalogue.get("vehicle.software.version") is None


def test_search_tool_says_a_missing_thing_is_missing(catalogue):
    """The tool explains WHY there is no result, and names what does not exist."""
    text = catalogue_tools.search_descriptors(catalogue, "version de software")
    assert "Sin resultados" in text
    assert "NO existe en el catalogo" in text
    assert "puStep" in text
    assert "NO gasta cuota" in text


def test_search_tool_flags_container_membership(catalogue):
    """A hit says whether it is actually read by this project."""
    text = catalogue_tools.search_descriptors(catalogue, "deep sleep")
    assert "pitwall-maintenance" in text


def test_search_tool_handles_an_empty_query(catalogue):
    """An empty query gets guidance, not a stack trace."""
    text = catalogue_tools.search_descriptors(catalogue, "   ")
    assert "294" in text


def test_emergency_lights_and_tilt_are_reported_as_absent(catalogue):
    """The two RSU conditions CarData cannot observe are named explicitly."""
    lights = catalogue_tools.search_descriptors(catalogue, "luces de emergencia")
    tilt = catalogue_tools.search_descriptors(catalogue, "inclinacion al aparcar")
    assert "NO existe" in lights
    assert "NO existe" in tilt

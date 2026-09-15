"""The fault memory: counted, grouped and compared between readings, never translated.

Verified 2026-09-14: `diagnosticTroubleCodes.raw` arrives as XML inside the
string, each entry carrying only an ECU address and a code. No status, no date,
no description, and a header announcing 72 codes over 44 entries. The meaning
of the codes is not in the catalogue, so no test here expects one.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from conftest import FAKE_VIN, FakeTokens, load_fixture

from pitwall_mcp.cardata.client import CarDataAdapter
from pitwall_mcp.descriptors import FAULT_MEMORY
from pitwall_mcp.fault_memory import compare, parse_fault_memory
from pitwall_mcp.tools import fault_tools


def real_xml() -> str:
    """The fault memory as this U11 sent it on 14 Sep 2026, codes anonymised.

    Structure, ECU addresses, order and header are the real ones. Each code was
    renamed in arrival order to X00001, X00002, ...
    """
    return load_fixture("telematic_extended.json")["telematicData"][FAULT_MEMORY]["value"]


# --- Parsing ---------------------------------------------------------------


def test_the_real_fault_memory_is_parsed():
    memory = parse_fault_memory(real_xml())

    assert memory is not None
    assert memory.declared_count == 72
    assert len(memory.codes) == 44
    assert len(memory.by_ecu()) == 13


def test_codes_are_grouped_by_ecu_address_in_arrival_order():
    by_ecu = parse_fault_memory(real_xml()).by_ecu()

    assert by_ecu[29] == ["X00001", "X00002", "X00003", "X00004", "X00005"]
    assert len(by_ecu[16]) == 11


@pytest.mark.parametrize("raw", [None, "", "<dtcData", "<otherRoot/>", "not xml"])
def test_unreadable_values_yield_none(raw):
    assert parse_fault_memory(raw) is None


def test_an_empty_memory_is_an_empty_list_not_none():
    memory = parse_fault_memory('<dtcData dtcCount="0"></dtcData>')

    assert memory is not None
    assert memory.codes == ()
    assert memory.declared_count == 0


# --- Comparing -------------------------------------------------------------


def _memory(*codes: tuple[int, str]):
    body = "".join(f'<dtc ecuAddress="{ecu}">{code}</dtc>' for ecu, code in codes)
    return parse_fault_memory(f'<dtcData dtcCount="{len(codes)}">{body}</dtcData>')


def test_compare_names_what_appeared_and_what_was_cleared():
    appeared, cleared = compare(
        _memory((29, "X00001"), (99, "ABCDEF")),
        _memory((29, "X00001"), (13, "X00099")),
    )

    assert appeared == [(13, "X00099")]
    assert cleared == [(99, "ABCDEF")]


def test_the_same_code_on_another_ecu_is_a_different_entry():
    appeared, cleared = compare(_memory((29, "X00001")), _memory((13, "X00001")))

    assert appeared == [(13, "X00001")]
    assert cleared == [(29, "X00001")]


# --- The tool --------------------------------------------------------------


@pytest.fixture
def adapter(ready_settings, db, fake_client):
    """An adapter serving the first read of the extended container."""
    fake_client.responses["get_telematic_data"] = load_fixture("telematic_extended.json")
    return CarDataAdapter(ready_settings, db=db, tokens=FakeTokens())


async def test_it_counts_codes_and_ecus(adapter, ready_settings):
    text = await fault_tools.get_fault_memory(adapter, ready_settings)

    assert "44 codigos en 13 centralitas" in text
    assert "Centralita 16: 11" in text


async def test_it_reports_the_header_discrepancy(adapter, ready_settings):
    text = await fault_tools.get_fault_memory(adapter, ready_settings)

    assert "dice 72" in text


async def test_it_does_not_translate_codes(adapter, ready_settings):
    text = await fault_tools.get_fault_memory(adapter, ready_settings)

    assert "no se traduce" in text
    assert "sin estado" in text


async def test_the_first_reading_has_nothing_to_compare_with(adapter, ready_settings):
    text = await fault_tools.get_fault_memory(adapter, ready_settings)

    assert "no hay con que comparar" in text


async def test_it_names_codes_that_appeared_and_were_cleared(adapter, ready_settings):
    """An older memory with 99/ABCDEF instead of 29/X00001, recorded on 10 Sep."""
    older = real_xml().replace(
        '<dtc ecuAddress="29">X00001</dtc>', '<dtc ecuAddress="99">ABCDEF</dtc>'
    )
    adapter.history.record(
        FAKE_VIN,
        {FAULT_MEMORY: {"value": older, "unit": "-", "timestamp": "2026-09-10T12:00:00.000Z"}},
        moment=datetime(2026, 9, 10, 12, tzinfo=UTC),
    )

    text = await fault_tools.get_fault_memory(adapter, ready_settings)

    new = next((line for line in text.splitlines() if line.strip().startswith("Nuevos")), "")
    gone = next((line for line in text.splitlines() if line.strip().startswith("Ya no estan")), "")
    assert "X00001" in new
    assert "ABCDEF" in gone


async def test_an_old_container_says_why_there_is_no_fault_memory(
    ready_settings, db, fake_client
):
    fake_client.responses["get_telematic_data"] = load_fixture("telematic_real.json")
    adapter = CarDataAdapter(ready_settings, db=db, tokens=FakeTokens())

    text = await fault_tools.get_fault_memory(adapter, ready_settings)

    assert "no lo ha devuelto" in text

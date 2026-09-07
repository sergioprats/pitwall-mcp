"""The MCP surface: every tool registered, read-only, and honest about itself."""

from __future__ import annotations

import pytest
from conftest import FakeTokens

from pitwall_mcp.server import build_server
from pitwall_mcp.storage.db import Database
from pitwall_mcp.tools import ToolContext

EXPECTED_TOOLS = {
    "search_descriptors",
    "get_api_quota",
    "list_vehicles",
    "get_vehicle_basic_data",
    "report_product_update_step",
    "get_telematic_data",
    "get_vehicle_status",
    "get_tyre_diagnosis",
    "get_maintenance_summary",
    "diagnose_software_update",
}


@pytest.fixture
def ready_settings(settings):
    """Settings with a token file on disk, so credentials count as present."""
    settings.token_file.parent.mkdir(parents=True, exist_ok=True)
    settings.token_file.write_text("{}", encoding="utf-8")
    return settings


@pytest.fixture
def server(ready_settings, fake_client):
    """A server whose tools have credentials but no data yet."""
    context = ToolContext(settings=ready_settings, db=Database(":memory:"))
    context.__dict__["adapter"] = _adapter(context)
    return build_server(context)


@pytest.fixture
def bare_server(bare_settings):
    """A server with nothing configured at all."""
    context = ToolContext(settings=bare_settings, db=Database(":memory:"))
    return build_server(context)


def _adapter(context):
    """Build an adapter with fake tokens so nothing tries to authenticate."""
    from pitwall_mcp.cardata.client import CarDataAdapter

    return CarDataAdapter(context.settings, db=context.db, tokens=FakeTokens())


async def _call(server, name: str, arguments: dict | None = None) -> str:
    """Call a tool and return its text content."""
    result = await server.call_tool(name, arguments or {})
    return result.content[0].text


async def test_every_tool_is_registered(bare_server):
    """The MCP surface is complete and inspectable from block A onwards."""
    tools = await bare_server.list_tools()
    assert {tool.name for tool in tools} == EXPECTED_TOOLS


async def test_no_tool_can_write_to_the_vehicle(bare_server):
    """Rule 1: every tool is annotated read-only and non-destructive."""
    for tool in await bare_server.list_tools():
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.destructive_hint is False


async def test_no_tool_creates_or_deletes_containers(bare_server):
    """Rule 2: container management is not part of the MCP surface."""
    names = {tool.name for tool in await bare_server.list_tools()}
    assert not [n for n in names if "container" in n.lower()]


async def test_no_software_version_tool_exists(bare_server):
    """The descriptor does not exist, so neither does the tool."""
    names = {tool.name for tool in await bare_server.list_tools()}
    assert "get_software_version" not in names
    assert "report_product_update_step" in names


async def test_search_descriptors_works_without_credentials(bare_server):
    """One of the two tools that must genuinely work at the end of block A."""
    text = await _call(bare_server, "search_descriptors", {"query": "presion neumatico"})
    assert "vehicle.chassis.axle.row1.wheel.left.tire.pressure" in text
    assert "NO gasta cuota" in text


async def test_get_api_quota_works_without_credentials(bare_server):
    """The other one. It reports the missing credentials rather than failing."""
    text = await _call(bare_server, "get_api_quota")
    assert "0 de 20" in text
    assert "Cache vacia" in text
    assert "faltan credenciales" in text


async def test_pending_tools_ask_for_a_login_when_there_are_no_credentials(bare_server):
    """Rule: say what to run, do not just fail."""
    text = await _call(bare_server, "list_vehicles")
    assert text.startswith("ERROR")
    assert "scripts/login.py" in text


async def test_pending_tools_say_they_are_not_implemented_yet(server):
    """With credentials in place, they admit the work is not done."""
    text = await _call(server, "get_vehicle_status")
    assert "todavia no" in text
    assert "Bloque B" in text
    assert "conditionBasedServices" in text


async def test_the_diagnosis_tool_states_its_limits_up_front(server):
    """One of three conditions is observable, and the tool says so."""
    text = await _call(server, "diagnose_software_update")
    assert "SOLO PERMITE OBSERVAR UNA" in text
    assert "no observable por CarData" in text
    assert "LA SERIE" in text


async def test_the_tyre_tool_warns_it_has_no_pressures(server):
    """Pressures and diagnosis are two different sources."""
    text = await _call(server, "get_tyre_diagnosis")
    assert "NO DEVUELVE PRESIONES" in text


async def test_a_missing_container_is_reported_before_anything_else(ready_settings):
    """Configuration errors come out in the order the user has to fix them."""
    without_container = ready_settings.__class__(
        **{**ready_settings.__dict__, "container_id": None}
    )
    context = ToolContext(settings=without_container, db=Database(":memory:"))
    context.__dict__["adapter"] = _adapter(context)
    text = await _call(build_server(context), "get_telematic_data")
    assert "bootstrap_containers.py" in text


async def test_server_instructions_state_the_house_rules(bare_server):
    """A client reading the instructions learns the quota and the absences."""
    instructions = bare_server.instructions
    assert "50 peticiones" in instructions
    assert "solo lectura" in instructions.lower()
    assert "version de software" in instructions

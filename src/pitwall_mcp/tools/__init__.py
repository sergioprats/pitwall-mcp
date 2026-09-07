"""MCP tool registration.

Every tool is READ-ONLY. There is no tool that writes, commands, activates or
implies it can act on the car, and none that creates or deletes containers:
CarData is a read API and container creation lives in a hand-run script
(CLAUDE.md, rules 1 and 2). The `read_only_hint` annotation says so to any
client that cares to look.

Errors reach the user as text starting with "ERROR — " rather than as an
exception, so the Spanish instruction survives intact instead of being reduced
to a stack trace by the client.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from ..cardata.errors import PitwallError
from . import (
    catalogue_tools,
    diagnosis_tools,
    quota_tools,
    telematic_tools,
    tyre_tools,
    vehicle_tools,
)
from .context import ToolContext

_LOGGER = logging.getLogger(__name__)

READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=True,
)

#: Tools that never touch the network, and say so in their description.
LOCAL_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)


def _guard(call: Callable[[], str]) -> str:
    """Run a tool body and turn a `PitwallError` into its Spanish instruction."""
    try:
        return call()
    except PitwallError as err:
        return f"ERROR — {err.message}"


def register_tools(server: MCPServer, context: ToolContext) -> MCPServer:
    """Register every tool on the server and return it."""

    @server.tool(
        name="search_descriptors",
        title="Buscar descriptores telematicos",
        description=(
            "Busca en el catalogo telematico local de BMW CarData (294 descriptores). "
            "Acepta consultas en espanol o en ingles. NO gasta cuota de la API: el "
            "catalogo es un fichero local. Es la unica fuente de verdad sobre que "
            "descriptores existen."
        ),
        annotations=LOCAL_ONLY,
    )
    def search_descriptors(query: str, limit: int = 20, include_electric: bool = False) -> str:
        """Search the local catalogue for descriptors matching `query`."""
        return _guard(
            lambda: catalogue_tools.search_descriptors(
                context.catalogue, query, limit=limit, include_electric=include_electric
            )
        )

    @server.tool(
        name="get_api_quota",
        title="Estado de la cuota de la API",
        description=(
            "Cuantas peticiones REST se han gastado hoy, cuantas quedan, cuando se "
            "estima que se reinicia la ventana, y el estado de la cache local. Todo "
            "se deriva de SQLite: NO gasta cuota."
        ),
        annotations=LOCAL_ONLY,
    )
    def get_api_quota() -> str:
        """Report today's quota consumption and the local cache state."""
        return _guard(lambda: quota_tools.get_api_quota(context.adapter, context.settings))

    @server.tool(
        name="list_vehicles",
        title="Listar vehiculos de la cuenta",
        description=(
            "VINs mapeados a la cuenta, con mappedSince y mappingType (PRIMARY o "
            "SECONDARY). Fuente: GET /customers/vehicles/mappings. Cache de 30 dias."
        ),
        annotations=READ_ONLY,
    )
    def list_vehicles() -> str:
        """List the VINs mapped to the account."""
        return _guard(lambda: vehicle_tools.list_vehicles(context.settings))

    @server.tool(
        name="get_vehicle_basic_data",
        title="Datos basicos del vehiculo",
        description=(
            "Modelo, serie, motor, fecha de construccion, lista de equipamiento y "
            "puStep. Fuente: GET /customers/vehicles/{vin}/basicData. Cache de 30 dias."
        ),
        annotations=READ_ONLY,
    )
    def get_vehicle_basic_data() -> str:
        """Return the vehicle's basic data."""
        return _guard(lambda: vehicle_tools.get_vehicle_basic_data(context.settings))

    @server.tool(
        name="report_product_update_step",
        title="Paso de actualizacion de producto (puStep)",
        description=(
            "Expone puStep, el paso de actualizacion de PRODUCTO. NO es la version de "
            "software del vehiculo: ese descriptor no existe en el catalogo de BMW. "
            "Guarda cada lectura en el historico para detectar si alguna vez cambia."
        ),
        annotations=READ_ONLY,
    )
    def report_product_update_step() -> str:
        """Report `puStep`, explicitly labelled as a product update step."""
        return _guard(lambda: vehicle_tools.report_product_update_step(context.settings))

    @server.tool(
        name="get_telematic_data",
        title="Datos telematicos del contenedor",
        description=(
            "Lee el contenedor de mantenimiento completo: kilometraje, CBS, bateria de "
            "12V, presiones y estado de sueno. Fuente: GET /telematicData. Cache de 12 h."
        ),
        annotations=READ_ONLY,
    )
    def get_telematic_data() -> str:
        """Read the configured telematic container."""
        return _guard(lambda: telematic_tools.get_telematic_data(context.settings))

    @server.tool(
        name="get_vehicle_status",
        title="Estado del vehiculo: kilometraje y CBS",
        description=(
            "Kilometraje y avisos de mantenimiento (CBS). El desglose CBS por partida "
            "esta pendiente de verificar contra una respuesta real: la estructura de "
            "conditionBasedServices no esta documentada por BMW."
        ),
        annotations=READ_ONLY,
    )
    def get_vehicle_status() -> str:
        """Return mileage and the CBS block."""
        return _guard(lambda: telematic_tools.get_vehicle_status(context.settings))

    @server.tool(
        name="get_tyre_diagnosis",
        title="Diagnostico de neumaticos",
        description=(
            "Desgaste, defectos, dimensiones, fechas de montaje y fabricacion, "
            "temporada, runflat y dibujo. NO devuelve presiones: esas vienen del "
            "contenedor telematico. Cache de 7 dias."
        ),
        annotations=READ_ONLY,
    )
    def get_tyre_diagnosis() -> str:
        """Return the smart maintenance tyre diagnosis."""
        return _guard(lambda: tyre_tools.get_tyre_diagnosis(context.settings))

    @server.tool(
        name="get_maintenance_summary",
        title="Resumen de mantenimiento",
        description=(
            "Bloque de texto listo para pegar en un prompt: kilometraje, partidas CBS "
            "con km y fecha restantes, presiones de las cuatro ruedas en bar contra su "
            "objetivo, y la fecha del dato mas antiguo utilizado."
        ),
        annotations=READ_ONLY,
    )
    def get_maintenance_summary() -> str:
        """Return the composed maintenance summary."""
        return _guard(lambda: diagnosis_tools.get_maintenance_summary(context.settings))

    @server.tool(
        name="diagnose_software_update",
        title="Diagnostico de actualizaciones de software",
        description=(
            "Informe acotado sobre por que no llega una Remote Software Upgrade. De las "
            "tres condiciones que documenta BMW, CarData solo permite observar una (la "
            "bateria de 12V); sobre las otras dos declara 'no observable por CarData'. "
            "Trabaja sobre la SERIE del historico local, no sobre una foto puntual."
        ),
        annotations=READ_ONLY,
    )
    def diagnose_software_update() -> str:
        """Return the bounded software-update diagnosis."""
        return _guard(lambda: diagnosis_tools.diagnose_software_update(context.settings))

    _LOGGER.info("Registered pitwall-mcp tools (all read-only)")
    return server


__all__ = ["ToolContext", "register_tools"]

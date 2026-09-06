"""MCP server entry point, stdio transport.

Logging goes to stderr on purpose: stdout is the MCP channel and anything
printed there corrupts the protocol.
"""

from __future__ import annotations

import logging
import sys

from mcp.server.mcpserver import MCPServer

from . import __version__
from .config import load_settings
from .tools import ToolContext, register_tools

_LOGGER = logging.getLogger(__name__)

INSTRUCTIONS = (
    "Servidor MCP de solo lectura sobre la API BMW CarData de un BMW X1 sDrive18i "
    "(U11, gasolina). No afiliado a BMW AG.\n\n"
    "Reglas que este servidor cumple y que conviene tener presentes al usarlo:\n"
    "- Solo lectura: ninguna herramienta actua sobre el coche ni puede hacerlo.\n"
    "- La API REST esta limitada a 50 peticiones cada 24 h POR CUENTA. El servidor "
    "aplica un tope local mas bajo y sirve de cache siempre que puede. Consulta "
    "get_api_quota antes de encadenar llamadas.\n"
    "- search_descriptors y get_api_quota no gastan cuota nunca.\n"
    "- Toda respuesta indica su procedencia: timestamp del dato, timestamp de la "
    "lectura, y si viene de la API, de cache o del historico local.\n"
    "- Si un dato no existe, la herramienta explica por que. No devuelve nulos ni "
    "valores simulados: no completes tu esos huecos.\n"
    "- No existe ningun descriptor de version de software del vehiculo. Lo unico "
    "disponible es puStep, que es el paso de actualizacion de producto."
)


def build_server(context: ToolContext | None = None) -> MCPServer:
    """Build the MCP server with every tool registered."""
    resolved = context or ToolContext.build()
    server = MCPServer(
        name="pitwall-mcp",
        title="pitwall-mcp",
        version=__version__,
        instructions=INSTRUCTIONS,
    )
    return register_tools(server, resolved)


def main() -> None:
    """Run the server over stdio."""
    settings = load_settings()
    logging.basicConfig(
        stream=sys.stderr,
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    context = ToolContext.build(settings)
    _LOGGER.info(
        "pitwall-mcp %s starting (db=%s, tope de cuota=%s/dia)",
        __version__,
        context.settings.db_path,
        context.settings.daily_quota,
    )
    warning = context.adapter.tokens.refresh_warning()
    if warning:
        _LOGGER.warning(warning)
    build_server(context).run("stdio")


if __name__ == "__main__":
    main()

"""MCP server entry point, stdio transport.

Logging goes to stderr on purpose: stdout is the MCP channel and anything
printed there corrupts the protocol.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from . import __version__
from .cardata.errors import PitwallError
from .config import load_settings
from .fetch import describe, fetch_catalogue
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


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    """Parse the command line. No arguments means: run the server."""
    parser = argparse.ArgumentParser(
        prog="pitwall-mcp",
        description=(
            "Servidor MCP de solo lectura sobre la API BMW CarData. "
            "Sin argumentos arranca el servidor por stdio."
        ),
    )
    parser.add_argument(
        "--fetch-catalogue",
        action="store_true",
        help=(
            "descarga el catalogo telematico y sale. Se baja de GitHub, no de BMW, "
            "asi que NO gasta ninguna peticion de tu cuota diaria."
        ),
    )
    parser.add_argument(
        "--out",
        metavar="RUTA",
        default=None,
        help="con --fetch-catalogue, donde escribirlo (por defecto, junto a la base de datos)",
    )
    return parser.parse_args(argv)


def _fetch_catalogue_command(out: str | None) -> int:
    """Download the catalogue, report where it landed, and exit."""
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(message)s")
    try:
        path = fetch_catalogue(Path(out).expanduser() if out else None)
    except PitwallError as error:
        print(error.message, file=sys.stderr)
        return 1
    print(f"Catalogo escrito en {path}")
    print(describe(path))
    print()
    print("Ninguna peticion a la API de BMW: la descarga es de GitHub.")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the server over stdio, or fetch the catalogue and exit."""
    args = _parse_args(argv)
    if args.fetch_catalogue:
        return _fetch_catalogue_command(args.out)

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
    return 0


if __name__ == "__main__":
    main()

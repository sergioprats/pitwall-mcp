"""MCP server entry point, stdio transport.

Logging goes to stderr on purpose: stdout is the MCP channel and anything
printed there corrupts the protocol.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from . import __version__
from .cardata.auth import TokenManager
from .cardata.errors import PitwallError
from .config import load_settings
from .fetch import describe, fetch_catalogue
from .storage.db import parse_iso
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
        "--login",
        action="store_true",
        help=(
            "autoriza esta aplicacion en tu cuenta BMW y guarda los tokens. Habla con el "
            "OAuth de BMW, NO con la API de CarData, asi que no gasta cuota."
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


def _login_prompt(device) -> None:  # noqa: ANN001 - library DeviceCodeResponse
    """Print what the user has to do in the browser. Opens nothing on its own."""
    print()
    print("=" * 70)
    print("Autoriza pitwall-mcp en tu cuenta BMW:")
    print()
    print(f"  1. Abre: {device.verification_uri}")
    if device.verification_uri_complete:
        print(f"     (o directamente: {device.verification_uri_complete})")
    print(f"  2. Introduce el codigo: {device.user_code}")
    print(f"  3. Este comando espera hasta {device.expires_in} segundos.")
    print("=" * 70)
    print()
    print("Esperando a que autorices...", flush=True)


def _login_command() -> int:
    """Run the OAuth device flow and store the tokens.

    Lives here, and not only in `scripts/login.py`, because someone who installs
    the package from PyPI has no `scripts/` directory: without this they could
    not obtain a token at all. It talks to the GCDM OAuth endpoint, never to the
    CarData REST API, so it consumes none of the 50 daily requests.
    """
    settings = load_settings()
    if not settings.client_id:
        print(
            "ERROR: falta PITWALL_CLIENT_ID. Crea tu aplicacion CarData en "
            "https://bmw-cardata.bmwgroup.com y pon su client id en el fichero .env, "
            "o exportalo como variable de entorno.",
            file=sys.stderr,
        )
        return 1

    try:
        bundle = asyncio.run(TokenManager(settings).device_login(_login_prompt))
    except PitwallError as error:
        print(f"ERROR: {error.message}", file=sys.stderr)
        return 1

    print()
    print("Login completado.")
    print(f"  Tokens guardados en: {settings.token_file}")
    print(f"  Scopes concedidos: {bundle.scope or 'no informados por BMW'}")
    if parse_iso(bundle.refresh_obtained_at):
        print(
            f"  El refresh token caduca aproximadamente el "
            f"{bundle.refresh_expires_at.strftime('%Y-%m-%d %H:%M UTC')} (14 dias). "
            f"Antes de esa fecha hay que repetir este login."
        )
    print()
    print("Siguiente paso: crear el contenedor telematico. Eso NO lo hace este servidor,")
    print("vive en scripts/bootstrap_containers.py, en el repositorio:")
    print("  https://github.com/sergioprats/pitwall-mcp")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the server over stdio, or run one of the setup commands and exit."""
    args = _parse_args(argv)
    if args.fetch_catalogue:
        return _fetch_catalogue_command(args.out)
    if args.login:
        return _login_command()

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

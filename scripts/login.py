"""Interactive OAuth device-code login.

Run by hand. Opens nothing on its own: it prints a URL and a code, you finish
the flow in a browser, and the resulting tokens are written to the token file
outside the repository with mode 600.

    python scripts/login.py

This talks to the GCDM OAuth endpoint, NOT to the CarData REST API, so it does
not consume any of the 50 daily requests.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pitwall_mcp.cardata.auth import TokenManager  # noqa: E402
from pitwall_mcp.cardata.errors import PitwallError  # noqa: E402
from pitwall_mcp.config import load_settings  # noqa: E402
from pitwall_mcp.storage.db import parse_iso  # noqa: E402


def _prompt(device) -> None:  # noqa: ANN001 - library DeviceCodeResponse
    """Print what the user has to do in the browser."""
    print()
    print("=" * 70)
    print("Autoriza pitwall-mcp en tu cuenta BMW:")
    print()
    print(f"  1. Abre: {device.verification_uri}")
    if device.verification_uri_complete:
        print(f"     (o directamente: {device.verification_uri_complete})")
    print(f"  2. Introduce el codigo: {device.user_code}")
    print(f"  3. Este script espera hasta {device.expires_in} segundos.")
    print("=" * 70)
    print()
    print("Esperando a que autorices...", flush=True)


async def main() -> int:
    """Run the device-code flow and store the resulting tokens."""
    settings = load_settings()
    if not settings.client_id:
        print(
            "ERROR: falta PITWALL_CLIENT_ID. Copia .env.example a .env y pon el client id "
            "de tu aplicacion CarData del portal https://bmw-cardata.bmwgroup.com",
            file=sys.stderr,
        )
        return 1

    manager = TokenManager(settings)
    try:
        bundle = await manager.device_login(_prompt)
    except PitwallError as err:
        print(f"ERROR: {err.message}", file=sys.stderr)
        return 1

    expires = parse_iso(bundle.refresh_obtained_at)
    print()
    print("Login completado.")
    print(f"  Tokens guardados en: {settings.token_file}")
    print(f"  Scopes concedidos: {bundle.scope or 'no informados por BMW'}")
    if expires:
        print(
            f"  El refresh token caduca aproximadamente el "
            f"{(bundle.refresh_expires_at).strftime('%Y-%m-%d %H:%M UTC')} "
            f"(14 dias). Antes de esa fecha hay que repetir este login."
        )
    print()
    print("Siguiente paso, con el mismo interprete que has usado aqui:")
    print(f"  {sys.executable} scripts/bootstrap_containers.py --dry-run")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

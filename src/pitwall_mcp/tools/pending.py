"""Skeletons for the tools that need real credentials and real data.

Block A builds everything that works without BMW credentials and without
spending a single request. These tools are registered so the MCP surface is
complete and inspectable, but they do not call the API and they do not pretend
to have data. Each one says exactly what is missing and what to run.

No logic is written here on data we have not seen arrive yet: guessing the
shape of `conditionBasedServices` and then "adapting" it later is how a tool
ends up quietly returning fiction.
"""

from __future__ import annotations

from ..cardata import errors
from ..config import Settings

BLOCK_B_NOTE = (
    "Estado del proyecto: esta herramienta esta registrada pero todavia no "
    "implementada. Se completara en el Bloque B, cuando se haya visto llegar una "
    "respuesta real y se haya grabado como fixture. Hasta entonces no devuelve "
    "datos, y no los inventa."
)


def require_ready(settings: Settings, *, needs_vin: bool = False, needs_container: bool = False):
    """Raise the right Spanish error when the tool cannot possibly run yet.

    Order matters: credentials first, then VIN, then container, because that is
    the order in which the user has to fix them.
    """
    missing = settings.missing_for_api()
    if missing:
        raise errors.missing_credentials(missing)
    if needs_vin and not settings.vin:
        raise errors.missing_vin()
    if needs_container and not settings.container_id:
        raise errors.missing_container()


def pending(
    tool: str,
    settings: Settings,
    *,
    needs_vin: bool = False,
    needs_container: bool = False,
    note: str | None = None,
) -> str:
    """Return the message a not-yet-implemented tool answers with.

    If something is missing (credentials, VIN, container) that error wins,
    because it is the thing the user has to fix first.
    """
    require_ready(settings, needs_vin=needs_vin, needs_container=needs_container)
    text = f"{tool}: {BLOCK_B_NOTE}"
    if note:
        text = f"{text}\n\n{note}"
    return text

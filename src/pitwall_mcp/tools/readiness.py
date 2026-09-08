"""The readiness gate every API-touching tool passes through.

A tool that cannot possibly run must say what the user has to fix, in the order
they have to fix it: credentials, then VIN, then container. Failing later, deep
inside the adapter, would produce a worse message.

This module used to also hold the skeleton responses for the unwritten tools.
There are none left: every tool is implemented against a recorded real answer.
"""

from __future__ import annotations

from ..cardata import errors
from ..config import Settings


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

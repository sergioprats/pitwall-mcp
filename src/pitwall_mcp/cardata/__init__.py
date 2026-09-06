"""Adapter over the pinned `bmw-cardata` alpha library.

Only this package imports `bmw_cardata`. Modules under `tools/` talk to
`CarDataAdapter` and to plain dictionaries, never to the library's models.
"""

from __future__ import annotations

from .auth import TokenBundle, TokenManager, TokenStore
from .client import (
    ENDPOINT_BASIC_DATA,
    ENDPOINT_CONTAINERS,
    ENDPOINT_MAPPINGS,
    ENDPOINT_TELEMATIC,
    ENDPOINT_TYRE_DIAGNOSIS,
    ApiResult,
    CarDataAdapter,
)

__all__ = [
    "ENDPOINT_BASIC_DATA",
    "ENDPOINT_CONTAINERS",
    "ENDPOINT_MAPPINGS",
    "ENDPOINT_TELEMATIC",
    "ENDPOINT_TYRE_DIAGNOSIS",
    "ApiResult",
    "CarDataAdapter",
    "TokenBundle",
    "TokenManager",
    "TokenStore",
]

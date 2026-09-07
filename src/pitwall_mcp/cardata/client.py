"""The adapter over `bmw-cardata`.

This module, plus `auth.py` and `errors.py` beside it, is the only place that
imports the library. Nothing under `tools/` ever does, so an API change in the
pinned alpha (0.1.0a3) is absorbed here.

Two invariants are structural, not a matter of discipline:

* `_call_api` is the ONLY method that touches the network, and it is private
  and called from exactly one place, `_fetch`. It always writes a `quota_log`
  row before returning, success or failure.
* `_fetch` always consults the cache first and always asks the quota guard for
  permission before letting `_call_api` run. A public method cannot reach the
  network by any other path.

Every value returned carries its provenance: BMW's timestamp, our read time,
and whether it came from the API or from cache.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from bmw_cardata import CarDataClient
from bmw_cardata.exceptions import CarDataError, CarDataHTTPError

from ..config import TTLS, Settings
from ..formatting import Provenance, provenance_line
from ..storage.cache import CacheEntry, CacheStore
from ..storage.db import Database, utc_now
from ..storage.history import HistoryStore
from ..storage.quota import QuotaExceededError, QuotaStore
from . import errors
from .auth import TokenManager

_LOGGER = logging.getLogger(__name__)

# Endpoint keys, used for cache rows, quota rows and TTL lookup.
ENDPOINT_MAPPINGS = "mappings"
ENDPOINT_BASIC_DATA = "basicData"
ENDPOINT_TELEMATIC = "telematicData"
ENDPOINT_TYRE_DIAGNOSIS = "tyreDiagnosis"
ENDPOINT_CONTAINERS = "containers"


@dataclass(frozen=True)
class ApiResult:
    """A payload plus everything needed to say where it came from."""

    payload: Any
    source: Provenance
    fetched_at: datetime
    expires_at: datetime
    stale: bool = False
    warning: str | None = None

    def provenance(self, *, source_timestamp: str | None = None) -> str:
        """The provenance line required by CLAUDE.md rule 4."""
        line = provenance_line(
            self.source,
            source_timestamp=source_timestamp or self.fetched_at,
            read_at=self.fetched_at,
        )
        if self.stale:
            line += (
                " ATENCION: este dato esta caducado y se sirve de cache porque no se "
                "puede gastar cuota ahora mismo."
            )
        if self.warning:
            line += f" {self.warning}"
        return line


class CarDataAdapter:
    """Read-only access to BMW CarData, through cache and quota, always."""

    def __init__(
        self,
        settings: Settings,
        *,
        db: Database,
        tokens: TokenManager | None = None,
    ) -> None:
        """Wire the adapter to its settings, database and token manager."""
        self._settings = settings
        self._db = db
        self._cache = CacheStore(db)
        self._quota = QuotaStore(db, daily_limit=settings.daily_quota)
        self._history = HistoryStore(db)
        self._tokens = tokens or TokenManager(settings)

    # -- Public, read-only surface -----------------------------------------

    async def list_vehicles(self) -> ApiResult:
        """`GET /customers/vehicles/mappings` — the VINs on this account."""

        async def fetch(client: CarDataClient) -> Any:
            mappings = await client.get_mappings()
            return [mapping.raw_data for mapping in mappings]

        return await self._fetch(ENDPOINT_MAPPINGS, fetch)

    async def get_basic_data(self, vin: str) -> ApiResult:
        """`GET /customers/vehicles/{vin}/basicData` — includes `puStep`."""

        async def fetch(client: CarDataClient) -> Any:
            return (await client.get_basic_data(vin)).raw_data

        return await self._fetch(ENDPOINT_BASIC_DATA, fetch, vin=vin)

    async def get_telematic_data(
        self, vin: str, container_id: str, *, force: bool = False
    ) -> ApiResult:
        """`GET /customers/vehicles/{vin}/telematicData` for one container.

        Successful responses are also appended to the local history, because
        the value of the battery and `puStep` questions is in the SERIES.
        """

        async def fetch(client: CarDataClient) -> Any:
            return (await client.get_telematic_data(vin, container_id)).raw_data

        result = await self._fetch(
            ENDPOINT_TELEMATIC, fetch, vin=vin, container_id=container_id, force=force
        )
        if result.source == "api":
            self._store_history(vin, result.payload)
        return result

    async def get_tyre_diagnosis(self, vin: str) -> ApiResult:
        """`GET /customers/vehicles/{vin}/smartMaintenanceTyreDiagnosis`.

        Wear, defects, dimensions, mounting and production dates. It does NOT
        return pressures: those come from the telematic container instead.
        """

        async def fetch(client: CarDataClient) -> Any:
            return (await client.get_smart_maintenance_tyre_diagnosis(vin)).raw_data

        return await self._fetch(ENDPOINT_TYRE_DIAGNOSIS, fetch, vin=vin)

    async def list_containers(self) -> ApiResult:
        """`GET /customers/containers`. Listing is read-only and safe.

        Creating or deleting containers is NOT here on purpose: that lives in
        `scripts/bootstrap_containers.py` and is run by hand (CLAUDE.md rule 2).
        """

        async def fetch(client: CarDataClient) -> Any:
            return (await client.list_containers()).raw_data

        return await self._fetch(ENDPOINT_CONTAINERS, fetch)

    # -- Status, costing nothing -------------------------------------------

    @property
    def quota(self) -> QuotaStore:
        """The quota ledger, for `get_api_quota`."""
        return self._quota

    @property
    def history(self) -> HistoryStore:
        """The reading history, for the diagnosis tools."""
        return self._history

    @property
    def tokens(self) -> TokenManager:
        """The token manager, for the refresh-token warning."""
        return self._tokens

    def cached(
        self, endpoint: str, *, vin: str = "", container_id: str = ""
    ) -> CacheEntry | None:
        """Peek at the cache without any chance of a request. Costs nothing."""
        return self._cache.get(endpoint, vin=vin, container_id=container_id, include_stale=True)

    # -- The only path to the network --------------------------------------

    async def _fetch(
        self,
        endpoint: str,
        fetcher: Callable[[CarDataClient], Awaitable[Any]],
        *,
        vin: str = "",
        container_id: str = "",
        force: bool = False,
    ) -> ApiResult:
        """Serve from cache, or spend one request if the quota guard allows it.

        `force` skips the TTL and nothing else: the quota guard still decides,
        and the request is still logged. It exists because the TTL is a cost
        guard, not a correctness one, and there are moments worth one request
        out of fifty (reading right after the car is parked, say). It is NOT
        reachable from any MCP tool; only `scripts/capture.py` passes it, run by
        hand, so a model can never spend the budget by deciding data looks old.
        """
        now = utc_now()
        fresh = (
            None
            if force
            else self._cache.get(endpoint, vin=vin, container_id=container_id, moment=now)
        )
        if fresh is not None:
            _LOGGER.debug("cache hit %s vin=%s container=%s", endpoint, vin, container_id)
            return ApiResult(
                payload=fresh.payload,
                source="cache",
                fetched_at=fresh.fetched_at,
                expires_at=fresh.expires_at,
                warning=self._tokens.refresh_warning(now),
            )

        try:
            self._quota.check(now)
        except QuotaExceededError as err:
            return self._serve_stale_or_fail(endpoint, vin, container_id, err)

        payload = await self._call_api(endpoint, fetcher, vin=vin)
        ttl = TTLS.get(endpoint, timedelta(hours=12))
        entry = self._cache.put(
            endpoint,
            payload,
            ttl=ttl,
            vin=vin,
            container_id=container_id,
        )
        return ApiResult(
            payload=payload,
            source="api",
            fetched_at=entry.fetched_at,
            expires_at=entry.expires_at,
            warning=self._tokens.refresh_warning(),
        )

    async def _call_api(
        self,
        endpoint: str,
        fetcher: Callable[[CarDataClient], Awaitable[Any]],
        *,
        vin: str = "",
    ) -> Any:
        """Send exactly one request and log it in `quota_log`, whatever happens.

        The quota row is written for failures too: a rejected request has still
        been counted by BMW.
        """
        self._tokens.require_credentials()
        async with CarDataClient(self._tokens.access_token) as client:
            try:
                payload = await fetcher(client)
            except CarDataHTTPError as err:
                self._quota.record(
                    endpoint,
                    vin=vin or None,
                    http_status=err.status,
                    error_id=err.error_id,
                    note=err.message[:200] if err.message else None,
                )
                raise errors.translate_http_error(err, vin=vin or None) from err
            except CarDataError as err:
                self._quota.record(endpoint, vin=vin or None, note=str(err)[:200])
                raise errors.translate(err, vin=vin or None) from err

        self._quota.record(endpoint, vin=vin or None, http_status=200)
        return payload

    def _serve_stale_or_fail(
        self,
        endpoint: str,
        vin: str,
        container_id: str,
        err: QuotaExceededError,
    ) -> ApiResult:
        """With no quota left, hand back stale cache clearly labelled, or fail."""
        stale = self._cache.get(
            endpoint, vin=vin, container_id=container_id, include_stale=True
        )
        if stale is None:
            raise errors.quota_exhausted(err)
        _LOGGER.info("quota exhausted, serving stale cache for %s", endpoint)
        return ApiResult(
            payload=stale.payload,
            source="cache",
            fetched_at=stale.fetched_at,
            expires_at=stale.expires_at,
            stale=True,
            warning=err.status.spanish_message(),
        )

    def _store_history(self, vin: str, payload: Any) -> None:
        """Append a telematic response to the local history."""
        if not isinstance(payload, dict):
            return
        entries = payload.get("telematicData")
        if not isinstance(entries, dict):
            return
        clean = {
            descriptor: {
                "value": entry.get("value"),
                "unit": entry.get("unit"),
                "timestamp": entry.get("timestamp"),
            }
            for descriptor, entry in entries.items()
            if isinstance(entry, dict)
        }
        inserted = self._history.record(vin, clean, source="rest")
        _LOGGER.debug("stored %s new readings for %s", inserted, vin)

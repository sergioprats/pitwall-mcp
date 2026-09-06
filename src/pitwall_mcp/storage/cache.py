"""Raw response cache.

Stores the API payload exactly as it arrived, without normalising anything.
If we later understand a structure better (`conditionBasedServices` is the
obvious candidate), we can reinterpret the stored bytes without spending a
single new request.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .db import Database, parse_iso, to_iso, utc_now


@dataclass(frozen=True)
class CacheEntry:
    """A cached response together with the two timestamps that qualify it."""

    endpoint: str
    vin: str
    container_id: str
    payload: Any
    http_status: int
    fetched_at: datetime
    expires_at: datetime

    def is_fresh(self, moment: datetime | None = None) -> bool:
        """True while the entry is still inside its TTL."""
        return (moment or utc_now()) < self.expires_at

    def age_seconds(self, moment: datetime | None = None) -> int:
        """Seconds elapsed since we fetched this payload from BMW."""
        return int(((moment or utc_now()) - self.fetched_at).total_seconds())


class CacheStore:
    """Read/write access to the `api_cache` table."""

    def __init__(self, db: Database) -> None:
        """Bind the store to an open database."""
        self._db = db

    def get(
        self,
        endpoint: str,
        *,
        vin: str = "",
        container_id: str = "",
        include_stale: bool = False,
        moment: datetime | None = None,
    ) -> CacheEntry | None:
        """Return the cached entry for a key.

        By default only fresh entries are returned. `include_stale=True` is for
        the degraded path: when the quota is exhausted we would rather hand back
        an old reading, clearly labelled as old, than nothing at all.
        """
        row = self._db.connection.execute(
            """
            SELECT endpoint, vin, container_id, response_json, http_status,
                   fetched_at, expires_at
              FROM api_cache
             WHERE endpoint = ? AND vin = ? AND container_id = ?
            """,
            (endpoint, vin, container_id),
        ).fetchone()
        if row is None:
            return None

        fetched_at = parse_iso(row["fetched_at"])
        expires_at = parse_iso(row["expires_at"])
        if fetched_at is None or expires_at is None:
            return None

        entry = CacheEntry(
            endpoint=row["endpoint"],
            vin=row["vin"],
            container_id=row["container_id"],
            payload=json.loads(row["response_json"]),
            http_status=int(row["http_status"]),
            fetched_at=fetched_at,
            expires_at=expires_at,
        )
        if not include_stale and not entry.is_fresh(moment):
            return None
        return entry

    def put(
        self,
        endpoint: str,
        payload: Any,
        *,
        ttl: timedelta,
        vin: str = "",
        container_id: str = "",
        http_status: int = 200,
        moment: datetime | None = None,
    ) -> CacheEntry:
        """Store (replacing any previous entry for the same key) a fresh payload."""
        fetched_at = moment or utc_now()
        expires_at = fetched_at + ttl
        self._db.connection.execute(
            """
            INSERT OR REPLACE INTO api_cache
                (endpoint, vin, container_id, response_json, http_status,
                 fetched_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                endpoint,
                vin,
                container_id,
                json.dumps(payload, ensure_ascii=False),
                http_status,
                to_iso(fetched_at),
                to_iso(expires_at),
            ),
        )
        return CacheEntry(
            endpoint=endpoint,
            vin=vin,
            container_id=container_id,
            payload=payload,
            http_status=http_status,
            fetched_at=fetched_at,
            expires_at=expires_at,
        )

    def invalidate(self, endpoint: str, *, vin: str = "", container_id: str = "") -> None:
        """Drop a single cache entry. Never called on the vehicle's behalf."""
        self._db.connection.execute(
            "DELETE FROM api_cache WHERE endpoint = ? AND vin = ? AND container_id = ?",
            (endpoint, vin, container_id),
        )

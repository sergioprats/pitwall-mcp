"""Quota ledger.

BMW limits the CarData REST API to 50 requests per 24 h and per account;
going over returns HTTP 403 with `exveErrorId` `CU-429` until the window
resets. NOTE: that limit is documented by BMW for B2C customers but does NOT
appear anywhere in the official swagger, so we treat it as an unverified
external constraint and keep a conservative local cap well below it.

One row per request ACTUALLY SENT. Cache hits are never logged here: this
table is the single source of truth about spent quota, and the daily counter
is derived from it instead of being stored separately, so the two can never
drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .db import Database, next_utc_day_start, to_iso, utc_day_start, utc_now

QUOTA_ERROR_ID = "CU-429"

# BMW's documented ceiling. We never let the local cap exceed it.
BMW_DAILY_LIMIT = 50


class QuotaExceededError(RuntimeError):
    """Raised instead of sending a request that would go over the local cap."""

    def __init__(self, status: QuotaStatus) -> None:
        """Build the error from the quota status that blocked the request."""
        super().__init__(status.spanish_message())
        self.status = status


@dataclass(frozen=True)
class QuotaStatus:
    """Snapshot of today's quota consumption."""

    used: int
    limit: int
    window_start: datetime
    resets_at: datetime
    first_request_at: datetime | None
    last_request_at: datetime | None
    remote_denied: bool
    bmw_limit: int = BMW_DAILY_LIMIT

    @property
    def remaining(self) -> int:
        """Requests still allowed today under the local cap."""
        return max(0, self.limit - self.used)

    @property
    def exhausted(self) -> bool:
        """True when no further request may be sent today."""
        return self.remaining <= 0 or self.remote_denied

    def spanish_message(self) -> str:
        """User-facing explanation of why nothing else will be sent today."""
        if self.remote_denied:
            reason = (
                f"BMW ya ha rechazado una peticion hoy con 403 {QUOTA_ERROR_ID} "
                f"(cuota de la cuenta agotada)."
            )
        else:
            reason = (
                f"Se ha alcanzado el tope local de {self.limit} peticiones/dia "
                f"(el limite de BMW es {self.bmw_limit}/24 h)."
            )
        desde = (
            f" La primera peticion de hoy fue a las "
            f"{self.first_request_at.strftime('%H:%M UTC')}."
            if self.first_request_at
            else ""
        )
        return (
            f"Cuota agotada: {self.used}/{self.limit} peticiones gastadas hoy. "
            f"{reason}{desde} Se estima que la ventana se reinicia el "
            f"{self.resets_at.strftime('%Y-%m-%d a las %H:%M UTC')} "
            f"(suposicion: BMW resetea en dia natural UTC; el huso real no esta "
            f"documentado). Hasta entonces las herramientas solo pueden servir "
            f"datos de cache o del historico local."
        )


class QuotaStore:
    """Read/write access to the `quota_log` table."""

    def __init__(self, db: Database, *, daily_limit: int) -> None:
        """Bind the store to a database and a local daily cap."""
        self._db = db
        self.daily_limit = min(daily_limit, BMW_DAILY_LIMIT)

    def record(
        self,
        endpoint: str,
        *,
        vin: str | None = None,
        http_status: int | None = None,
        error_id: str | None = None,
        note: str | None = None,
        moment: datetime | None = None,
    ) -> None:
        """Log one request that was actually put on the wire."""
        self._db.connection.execute(
            """
            INSERT INTO quota_log (requested_at, endpoint, vin, http_status, error_id, note)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (to_iso(moment or utc_now()), endpoint, vin, http_status, error_id, note),
        )

    def status(self, moment: datetime | None = None) -> QuotaStatus:
        """Derive today's quota status from the ledger."""
        now = moment or utc_now()
        window_start = utc_day_start(now)
        row = self._db.connection.execute(
            """
            SELECT COUNT(*)                AS used,
                   MIN(requested_at)       AS first_at,
                   MAX(requested_at)       AS last_at,
                   SUM(CASE WHEN error_id = ? THEN 1 ELSE 0 END) AS denied
              FROM quota_log
             WHERE requested_at >= ?
            """,
            (QUOTA_ERROR_ID, to_iso(window_start)),
        ).fetchone()

        from .db import parse_iso  # local import keeps the module surface small

        return QuotaStatus(
            used=int(row["used"] or 0),
            limit=self.daily_limit,
            window_start=window_start,
            resets_at=next_utc_day_start(now),
            first_request_at=parse_iso(row["first_at"]),
            last_request_at=parse_iso(row["last_at"]),
            remote_denied=bool(row["denied"] or 0),
        )

    def check(self, moment: datetime | None = None) -> QuotaStatus:
        """Return the status, raising `QuotaExceededError` if nothing may be sent."""
        status = self.status(moment)
        if status.exhausted:
            raise QuotaExceededError(status)
        return status

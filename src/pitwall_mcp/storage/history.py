"""Reading history.

The point of this table is the SERIES, not the snapshot. `diagnose_software_update`
cannot conclude anything from a single point, and it is required to say so.

Values are stored exactly as BMW sent them, as text: the API types every
telematic value as a string, `-NA-` included. Interpreting them is the job of
`formatting.py`, not of the storage layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from .db import Database, parse_iso, to_iso, utc_now

Source = Literal["rest", "mqtt"]


@dataclass(frozen=True)
class Reading:
    """One stored telematic value with both timestamps and its provenance."""

    vin: str
    descriptor: str
    value: str | None
    unit: str | None
    source_timestamp: str
    recorded_at: datetime
    source: Source

    @property
    def source_moment(self) -> datetime | None:
        """BMW's own timestamp parsed, or `None` when BMW did not send one."""
        return parse_iso(self.source_timestamp)


class HistoryStore:
    """Read/write access to the `readings` table."""

    def __init__(self, db: Database) -> None:
        """Bind the store to an open database."""
        self._db = db

    def record(
        self,
        vin: str,
        entries: dict[str, dict[str, str | None]],
        *,
        source: Source = "rest",
        moment: datetime | None = None,
    ) -> int:
        """Store a batch of `{descriptor: {value, unit, timestamp}}` entries.

        Returns the number of rows actually inserted. Re-reading the same
        BMW-side timestamp is a no-op thanks to the unique index, so polling
        inside the TTL never inflates the series.
        """
        recorded_at = to_iso(moment or utc_now())
        rows = [
            (
                vin,
                descriptor,
                entry.get("value"),
                entry.get("unit"),
                entry.get("timestamp") or "",
                recorded_at,
                source,
            )
            for descriptor, entry in entries.items()
        ]
        if not rows:
            return 0
        cursor = self._db.connection.executemany(
            """
            INSERT OR IGNORE INTO readings
                (vin, descriptor, value, unit, source_timestamp, recorded_at, source)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        return cursor.rowcount

    def series(
        self,
        vin: str,
        descriptor: str,
        *,
        limit: int = 200,
    ) -> list[Reading]:
        """Return the stored series for one descriptor, oldest first."""
        rows = self._db.connection.execute(
            """
            SELECT vin, descriptor, value, unit, source_timestamp, recorded_at, source
              FROM readings
             WHERE vin = ? AND descriptor = ?
             ORDER BY recorded_at DESC, id DESC
             LIMIT ?
            """,
            (vin, descriptor, limit),
        ).fetchall()
        readings = [self._to_reading(row) for row in rows]
        readings.reverse()
        return readings

    def latest(self, vin: str, descriptor: str) -> Reading | None:
        """Return the most recently recorded value for one descriptor."""
        series = self.series(vin, descriptor, limit=1)
        return series[-1] if series else None

    def changes(self, vin: str, descriptor: str, *, limit: int = 200) -> list[Reading]:
        """Return the series with consecutive repetitions collapsed away.

        SOME DESCRIPTORS ARE STAMPED AT REQUEST TIME, NOT AT MEASUREMENT TIME.
        Verified on 2026-09-07: `battery.serviceDemand.recharge` and `.replace`
        came back with a timestamp matching our own call to the millisecond,
        while `travelledDistance` carried the moment of the actual measurement.

        Because the unique index includes `source_timestamp`, those two get a
        fresh row on every single read even when the value never moved. Counting
        rows would therefore overstate the evidence: ten calls in one afternoon
        would look like ten observations of a battery that was measured once.

        Anything reasoning about trends must use this, not `series`.
        """
        readings = self.series(vin, descriptor, limit=limit)
        collapsed: list[Reading] = []
        for reading in readings:
            if not collapsed or collapsed[-1].value != reading.value:
                collapsed.append(reading)
        return collapsed

    def snapshots(
        self, vin: str, descriptors: tuple[str, ...]
    ) -> list[tuple[datetime, dict[str, str | None]]]:
        """Rebuild, for each reading event, what the response carried.

        A reading event is one `record` call, so one `recorded_at`. The unique
        index drops a value whose BMW timestamp did not move, but that value was
        still in the response: each snapshot therefore carries forward the last
        known value of every descriptor. This is what lets two descriptors of
        different timestamp cohorts (a pressure and its target) be compared as
        they stood in the same answer. Oldest event first.
        """
        if not descriptors:
            return []
        marks = ",".join("?" * len(descriptors))
        rows = self._db.connection.execute(
            f"""
            SELECT descriptor, value, recorded_at
              FROM readings
             WHERE vin = ? AND descriptor IN ({marks})
             ORDER BY recorded_at, id
            """,
            (vin, *descriptors),
        ).fetchall()

        events: list[tuple[datetime, dict[str, str | None]]] = []
        state: dict[str, str | None] = {}
        current: str | None = None
        for row in rows:
            if current is not None and row["recorded_at"] != current:
                events.append((parse_iso(current) or utc_now(), dict(state)))
            current = row["recorded_at"]
            state[row["descriptor"]] = row["value"]
        if current is not None:
            events.append((parse_iso(current) or utc_now(), dict(state)))
        return events

    def descriptor_stats(self, vin: str) -> dict[str, tuple[int, datetime | None, datetime | None]]:
        """Return `{descriptor: (count, first recorded_at, last recorded_at)}`.

        Used by the diagnosis tools to state honestly how much evidence they
        actually hold before saying anything at all.
        """
        rows = self._db.connection.execute(
            """
            SELECT descriptor, COUNT(*) AS n,
                   MIN(recorded_at) AS first_at, MAX(recorded_at) AS last_at
              FROM readings
             WHERE vin = ?
             GROUP BY descriptor
            """,
            (vin,),
        ).fetchall()
        return {
            row["descriptor"]: (
                int(row["n"]),
                parse_iso(row["first_at"]),
                parse_iso(row["last_at"]),
            )
            for row in rows
        }

    @staticmethod
    def _to_reading(row) -> Reading:  # noqa: ANN001 - sqlite3.Row
        """Map a database row onto a `Reading`."""
        recorded_at = parse_iso(row["recorded_at"]) or utc_now()
        return Reading(
            vin=row["vin"],
            descriptor=row["descriptor"],
            value=row["value"],
            unit=row["unit"],
            source_timestamp=row["source_timestamp"] or "",
            recorded_at=recorded_at,
            source=row["source"],
        )

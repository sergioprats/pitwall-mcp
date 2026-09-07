"""SQLite connection handling and shared time helpers.

Every timestamp stored by pitwall-mcp is an ISO-8601 UTC string with a `Z`
suffix and second resolution. SQLite has no date type, but this format sorts
lexicographically in chronological order, so plain string comparisons work.

The database is deliberately synchronous (stdlib `sqlite3`). This is a
single-user local server; the queries are tiny and indexed, and an async
wrapper would buy nothing but complexity.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import TracebackType
from typing import Self

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

ISO_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def utc_now() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(UTC).replace(microsecond=0)


def to_iso(moment: datetime) -> str:
    """Serialize a datetime to the canonical ISO-8601 UTC string used in SQLite."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).strftime(ISO_FORMAT)


def parse_iso(value: str | None) -> datetime | None:
    """Parse a timestamp written by us, or `None` if it is absent or unparseable.

    Tolerates the variants BMW itself uses in `timestamp` fields (offsets,
    fractional seconds), because history rows may carry BMW's own strings.
    """
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def utc_day_start(moment: datetime | None = None) -> datetime:
    """Return midnight UTC of the day containing `moment`.

    ASSUMPTION: BMW's 24 h quota window is a natural UTC day. The real reset
    timezone is undocumented (see CLAUDE.md, open risk 5). Being wrong here
    makes our counter conservative, never permissive, only if BMW resets
    later than we do; the hard cap of 20 out of 50 absorbs the difference.
    """
    base = moment or utc_now()
    return base.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def next_utc_day_start(moment: datetime | None = None) -> datetime:
    """Return the start of the next UTC day, i.e. the assumed quota reset."""
    return utc_day_start(moment) + timedelta(days=1)


class Database:
    """Thin wrapper around a SQLite connection with the pitwall-mcp schema applied."""

    def __init__(self, path: Path | str) -> None:
        """Open (creating if needed) the database file at `path`."""
        self.path = Path(path)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        # `check_same_thread=False`: the MCP runtime may dispatch a synchronous
        # tool onto a worker thread, so the connection has to outlive the thread
        # that opened it. Access stays effectively serialized (one local user,
        # autocommit, tiny indexed queries), and Python's sqlite3 is built in
        # serialized threading mode.
        self.connection = sqlite3.connect(
            str(self.path), isolation_level=None, check_same_thread=False
        )
        self.connection.row_factory = sqlite3.Row
        self._apply_schema()

    def _apply_schema(self) -> None:
        """Create tables and indexes if they are not there yet."""
        self.connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))

    @property
    def schema_version(self) -> int:
        """Value of `PRAGMA user_version`, used for future migrations."""
        row = self.connection.execute("PRAGMA user_version").fetchone()
        return int(row[0])

    def close(self) -> None:
        """Close the underlying connection."""
        self.connection.close()

    def __enter__(self) -> Self:
        """Enter a context manager that closes the connection on exit."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close the connection when leaving the context."""
        self.close()

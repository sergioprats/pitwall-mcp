"""Parsing of a `/telematicData` response.

Written against a real answer from this U11 (2026-09-07), recorded in
`tests/fixtures/telematic_real.json`. Nothing here is guessed.

Two things this module exists to get right:

**Four states, not two.** A descriptor is not simply "there" or "not there":

* `VALUE` — a real reading.
* `EMPTY` — the key arrived with `value: null`. The vehicle knows the field and
  has no reading for it. 11 of our 32 came back like this.
* `NO_MEASUREMENT` — the vehicle said `-NA-` on purpose.
* `ABSENT` — the key is not in the response at all.

Collapsing these into "no data" would hide which of them happened, and the
difference is exactly what the user needs to know.

**`conditionBasedServices` is JSON inside a string.** Its `value` needs a
SECOND `json.loads`, and the objects inside use string sentinels that must
never be parsed as data: `"null"` (four literal characters, not JSON null) for
a missing date, and `"-"` for a missing distance.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Final

from .descriptors import CONDITION_BASED_SERVICES, NO_MEASUREMENT
from .storage.db import parse_iso

#: Strings BMW uses inside the CBS block to mean "this field does not apply".
#: `"null"` is the four-character word, not a JSON null.
CBS_SENTINELS: Final[frozenset[str]] = frozenset({"null", "-", ""})

#: Spanish labels for the CBS entries observed on this vehicle, keyed by BMW's
#: own id. Anything unknown keeps BMW's English title verbatim rather than being
#: guessed at.
CBS_LABELS: Final[dict[int, str]] = {
    1: "Aceite de motor",
    2: "Frenos delanteros",
    3: "Liquido de frenos",
    32: "Inspeccion tecnica (ITV)",
    100: "Revision del vehiculo",
}


class ValueState(StrEnum):
    """Why a descriptor does or does not carry a reading."""

    VALUE = "value"
    EMPTY = "empty"
    NO_MEASUREMENT = "no_measurement"
    ABSENT = "absent"


@dataclass(frozen=True)
class TelematicEntry:
    """One descriptor as it came back, with its state made explicit."""

    descriptor: str
    value: str | None
    unit: str | None
    timestamp: str | None
    state: ValueState

    @classmethod
    def absent(cls, descriptor: str) -> TelematicEntry:
        """Build the entry for a descriptor the response did not include."""
        return cls(descriptor, None, None, None, ValueState.ABSENT)

    @classmethod
    def from_raw(cls, descriptor: str, raw: Any) -> TelematicEntry:
        """Build an entry from BMW's `{value, unit, timestamp}` object."""
        if not isinstance(raw, dict):
            return cls.absent(descriptor)
        value = raw.get("value")
        unit = raw.get("unit")
        timestamp = raw.get("timestamp")

        if value is None:
            state = ValueState.EMPTY
        elif isinstance(value, str) and value.strip() == NO_MEASUREMENT:
            state = ValueState.NO_MEASUREMENT
        else:
            state = ValueState.VALUE
        return cls(descriptor, value, unit, timestamp, state)

    @property
    def has_value(self) -> bool:
        """True only for a real reading."""
        return self.state is ValueState.VALUE

    @property
    def moment(self) -> datetime | None:
        """BMW's own timestamp, parsed, or `None` when it did not send one."""
        return parse_iso(self.timestamp)

    def as_float(self) -> float | None:
        """The value as a number, or `None` if it is not one."""
        if not self.has_value:
            return None
        try:
            return float(str(self.value).strip().replace(",", "."))
        except ValueError:
            return None

    def as_int(self) -> int | None:
        """The value as an integer, or `None` if it is not one."""
        number = self.as_float()
        return None if number is None else int(number)


@dataclass(frozen=True)
class TelematicSnapshot:
    """A whole `/telematicData` response, indexed by descriptor."""

    entries: dict[str, TelematicEntry]

    @classmethod
    def from_payload(cls, payload: Any) -> TelematicSnapshot:
        """Parse the raw payload. An unusable payload yields an empty snapshot."""
        raw = payload.get("telematicData") if isinstance(payload, dict) else None
        if not isinstance(raw, dict):
            return cls(entries={})
        return cls(
            entries={
                descriptor: TelematicEntry.from_raw(descriptor, value)
                for descriptor, value in raw.items()
            }
        )

    def get(self, descriptor: str) -> TelematicEntry:
        """Return the entry, or an ABSENT one. Never `None`, never a guess."""
        return self.entries.get(descriptor) or TelematicEntry.absent(descriptor)

    def with_state(self, state: ValueState) -> list[str]:
        """Descriptors currently in a given state, in response order."""
        return [d for d, entry in self.entries.items() if entry.state is state]

    def missing_from(self, expected: tuple[str, ...]) -> list[str]:
        """Which of the descriptors we asked for are not in the response."""
        return [d for d in expected if d not in self.entries]

    def moments(self) -> list[datetime]:
        """Every BMW timestamp present, for "oldest datum used" reporting."""
        return [entry.moment for entry in self.entries.values() if entry.moment is not None]

    def oldest_moment(self) -> datetime | None:
        """The oldest BMW timestamp in the snapshot."""
        moments = self.moments()
        return min(moments) if moments else None

    def newest_moment(self) -> datetime | None:
        """The newest BMW timestamp in the snapshot."""
        moments = self.moments()
        return max(moments) if moments else None


# --- Condition Based Services ----------------------------------------------


def _clean(value: Any) -> str | None:
    """Return the text, or `None` when it is one of BMW's sentinels."""
    if value is None:
        return None
    text = str(value).strip()
    return None if text.lower() in CBS_SENTINELS else text


@dataclass(frozen=True)
class CbsItem:
    """One CBS entry: a maintenance item with a date, a distance, or both."""

    id: int | None
    title: str
    status: str | None
    date_text: str | None
    distance_km: int | None
    description: str | None

    @property
    def label(self) -> str:
        """Spanish label when we know the id, BMW's own title otherwise."""
        known = CBS_LABELS.get(self.id) if self.id is not None else None
        return known or self.title

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> CbsItem:
        """Build one item, turning every sentinel into `None`."""
        distance = _clean(raw.get("unitOfLengthRemaining"))
        try:
            distance_km = int(distance) if distance is not None else None
        except ValueError:
            distance_km = None
        raw_id = raw.get("id")
        return cls(
            id=raw_id if isinstance(raw_id, int) else None,
            title=str(raw.get("title") or "sin titulo"),
            status=_clean(raw.get("status")),
            date_text=_clean(raw.get("date")),
            distance_km=distance_km,
            description=_clean(raw.get("description")),
        )


@dataclass(frozen=True)
class CbsBlock:
    """The decoded CBS array, plus the count BMW reported separately."""

    items: list[CbsItem]
    reported_count: int | None

    @property
    def count_matches(self) -> bool | None:
        """Whether the separate counter agrees with the array.

        `None` when there is no counter to compare against. A mismatch is real
        and undocumented: it must be reported, not smoothed over.
        """
        if self.reported_count is None:
            return None
        return self.reported_count == len(self.items)


def parse_cbs(snapshot: TelematicSnapshot, reported_count: int | None = None) -> CbsBlock | None:
    """Decode `conditionBasedServices`, or `None` when it carries no value.

    Its `value` is a STRING containing JSON, so this decodes twice. A payload
    that cannot be decoded yields `None` rather than a partial guess.
    """
    entry = snapshot.get(CONDITION_BASED_SERVICES)
    if not entry.has_value:
        return None
    try:
        decoded = json.loads(str(entry.value))
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(decoded, list):
        return None
    return CbsBlock(
        items=[CbsItem.from_raw(item) for item in decoded if isinstance(item, dict)],
        reported_count=reported_count,
    )

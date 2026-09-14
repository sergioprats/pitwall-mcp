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

from .descriptors import CHECK_CONTROL_MESSAGES, CONDITION_BASED_SERVICES, NO_MEASUREMENT
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

    def moments(self, excluding: tuple[str, ...] = ()) -> list[datetime]:
        """Every BMW timestamp present, minus the descriptors a caller never reads."""
        return [
            entry.moment
            for descriptor, entry in self.entries.items()
            if entry.moment is not None and descriptor not in excluding
        ]

    def oldest_moment(self, excluding: tuple[str, ...] = ()) -> datetime | None:
        """The oldest BMW timestamp, ignoring descriptors the caller never reads.

        The OBFCM pair arrives stamped 30 Oct 2024: a tool that does not use it
        must not report that date as the oldest datum it relied on.
        """
        moments = self.moments(excluding)
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


def _clean_int(value: Any) -> int | None:
    """Return the number BMW sent as a string, or `None` for a sentinel."""
    text = _clean(value)
    try:
        return int(text) if text is not None else None
    except ValueError:
        return None


def _decode_array(entry: TelematicEntry) -> list[dict[str, Any]] | None:
    """Second `json.loads` of a value that is a string holding a JSON array.

    `None` when there is no value or it does not decode to a list: a partial
    guess would be worse than admitting the block is unreadable.
    """
    if not entry.has_value:
        return None
    try:
        decoded = json.loads(str(entry.value))
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(decoded, list):
        return None
    return [item for item in decoded if isinstance(item, dict)]


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
        raw_id = raw.get("id")
        return cls(
            id=raw_id if isinstance(raw_id, int) else None,
            title=str(raw.get("title") or "sin titulo"),
            status=_clean(raw.get("status")),
            date_text=_clean(raw.get("date")),
            distance_km=_clean_int(raw.get("unitOfLengthRemaining")),
            description=_clean(raw.get("description")),
        )

    @property
    def is_ok(self) -> bool:
        """True only when BMW itself marks the item OK."""
        return self.status == "OK"


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
    decoded = _decode_array(snapshot.get(CONDITION_BASED_SERVICES))
    if decoded is None:
        return None
    return CbsBlock(
        items=[CbsItem.from_raw(item) for item in decoded],
        reported_count=reported_count,
    )


# --- Check Control messages ------------------------------------------------


@dataclass(frozen=True)
class CheckControlMessage:
    """One Check Control message: a warning the car shows on its own display.

    Verified against the real answer of 2026-09-13, the first time this field
    carried a value. It uses the same keys as a CBS entry, but not the same
    meanings nor the same sentinels:

    * `status` came as the literal string `"NULL"`, while `date`, `title` and
      `description` came as real JSON nulls.
    * `unitOfLengthRemaining` is NOT a remaining distance. It read 48376 while
      the odometer went from 48283 (8 Sep) to 48440 (13 Sep), and CBS gave the
      same front brakes 1900 km left.
    * Nor is it the mileage at which the warning first fired, as first assumed.
      On 2026-09-14 the same message, id 907 with the same text, came back
      with 48712: the odometer of that moment. It is the mileage of the latest
      time the car sent the message. BMW does not document the field.
    """

    id: int | None
    text: str | None
    status: str | None
    mileage_km: int | None

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> CheckControlMessage:
        """Build one message, turning every sentinel into `None`."""
        raw_id = raw.get("id")
        return cls(
            id=raw_id if isinstance(raw_id, int) else None,
            text=_clean(raw.get("text")),
            status=_clean(raw.get("status")),
            mileage_km=_clean_int(raw.get("unitOfLengthRemaining")),
        )


def parse_check_control(snapshot: TelematicSnapshot) -> list[CheckControlMessage] | None:
    """Decode `checkControlMessages`, or `None` when it carries no usable value.

    Same trap as CBS: the value is a string holding a JSON array.
    """
    decoded = _decode_array(snapshot.get(CHECK_CONTROL_MESSAGES))
    if decoded is None:
        return None
    return [CheckControlMessage.from_raw(item) for item in decoded]

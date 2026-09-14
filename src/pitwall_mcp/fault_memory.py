"""The fault memory: parsed, grouped and compared. Never translated.

Verified 2026-09-14: `diagnosticTroubleCodes.raw` is XML inside the string,

    <dtcData dtcCount="72">
      <dtc ecuAddress="29">X00001</dtc>
      ...
    </dtcData>

Each entry carries only the ECU address and the code: no status, no date, no
description. The header announced 72 codes over 44 entries, and that
difference is reported, not explained. The codes are the manufacturer's and
the catalogue does not describe them, so nothing here attempts a meaning. What
this module can do honestly is count, group, and tell what changed between two
readings.

The XML comes from BMW's API. The standard library parser is used; the expat
bundled with Python 3.12 guards against entity-expansion attacks.
"""

from __future__ import annotations

import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass

Code = tuple[int | None, str]


@dataclass(frozen=True)
class FaultMemory:
    """The codes as they arrived, with the header's own count kept apart."""

    declared_count: int | None
    codes: tuple[Code, ...]

    def by_ecu(self) -> dict[int | None, list[str]]:
        """Codes grouped by ECU address, in arrival order."""
        grouped: dict[int | None, list[str]] = {}
        for ecu, code in self.codes:
            grouped.setdefault(ecu, []).append(code)
        return grouped


def _int(text: str | None) -> int | None:
    """An integer attribute, or `None` when it is missing or not a number."""
    try:
        return int(text) if text is not None else None
    except ValueError:
        return None


def parse_fault_memory(raw: str | None) -> FaultMemory | None:
    """Parse the XML, or `None` when it is not a `dtcData` document."""
    if not raw:
        return None
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError:
        return None
    if root.tag != "dtcData":
        return None
    codes: list[Code] = []
    for element in root.findall("dtc"):
        code = (element.text or "").strip()
        if code:
            codes.append((_int(element.get("ecuAddress")), code))
    return FaultMemory(declared_count=_int(root.get("dtcCount")), codes=tuple(codes))


def compare(old: FaultMemory, new: FaultMemory) -> tuple[list[Code], list[Code]]:
    """(appeared, cleared) between two readings.

    A code is identified with its ECU: the same code on another ECU is another
    entry.
    """
    before, after = set(old.codes), set(new.codes)
    appeared = [code for code in new.codes if code not in before]
    cleared = [code for code in old.codes if code not in after]
    return appeared, cleared

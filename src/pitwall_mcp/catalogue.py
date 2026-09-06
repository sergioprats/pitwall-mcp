"""Local telematic catalogue: loading and searching.

The catalogue is the ONLY source of truth for descriptors. Nothing is invented,
deduced by analogy or copied from third-party documentation. If a descriptor is
not in `spec/telematic_catalogue.json`, it does not exist (CLAUDE.md, rule 5).

Reading it costs no quota: it is a local file.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Final

from .descriptors import ALL_CONFIRMED_DESCRIPTORS, CONTAINER_DESCRIPTORS

#: This is a petrol U11. The whole electric category is out of scope, but it is
#: still searchable on request so a query never lies by omission.
ELECTRIC_CATEGORY: Final = "ELECTRIC VEHICLE DATA"

_WORD_RE = re.compile(r"[a-z0-9]+")

#: The catalogue is written in English; the user asks in Spanish. This maps
#: Spanish words onto the English wording BMW actually uses. It NEVER maps a
#: word onto a descriptor: it only widens the text match, so a query can still
#: only find descriptors that really exist in the catalogue.
_SPANISH_SYNONYMS: Final[dict[str, tuple[str, ...]]] = {
    "aceite": ("oil",),
    "aviso": ("warning", "message"),
    "bateria": ("battery",),
    "carga": ("charge", "charging"),
    "combustible": ("fuel",),
    "consumo": ("consumption",),
    "delantera": ("row1", "front"),
    "delantero": ("row1", "front"),
    "distancia": ("distance",),
    "encendido": ("ignition",),
    "estado": ("status", "state"),
    "fecha": ("date",),
    "freno": ("brake",),
    "frenos": ("brake",),
    "inspeccion": ("inspection",),
    "itv": ("inspection", "huandau"),
    "kilometraje": ("distance", "mileage"),
    "kilometros": ("distance", "mileage"),
    "mantenimiento": ("service", "maintenance"),
    "motor": ("engine",),
    "neumatico": ("tire", "tyre"),
    "neumaticos": ("tire", "tyre"),
    "presion": ("pressure",),
    "profundo": ("deep",),
    "puerta": ("door",),
    "revision": ("service", "inspection"),
    "rueda": ("wheel", "tire"),
    "ruedas": ("wheel", "tire"),
    "servicio": ("service",),
    "sueno": ("sleep",),
    "temperatura": ("temperature",),
    "trasera": ("row2", "rear"),
    "trasero": ("row2", "rear"),
    "ventana": ("window",),
    "voltaje": ("voltage",),
}

#: Accent folding, so "presión" and "sueño" behave like "presion" and "sueno".
_ACCENTS = str.maketrans("áàäâéèëêíìïîóòöôúùüûñç", "aaaaeeeeiiiioooouuuunc")


def _normalise(text: str) -> str:
    """Lowercase and strip accents so Spanish input matches the tables above."""
    return text.lower().translate(_ACCENTS)


def _clean(value: str | None) -> str | None:
    """Normalise the catalogue's occasional literal "null" string to None."""
    if value is None:
        return None
    text = value.strip()
    return None if text in ("", "null") else text


def _expand(term: str) -> tuple[str, ...]:
    """Return the term plus its English equivalents, if any."""
    return (term, *_SPANISH_SYNONYMS.get(term, ()))


@dataclass(frozen=True)
class CatalogueEntry:
    """One descriptor exactly as the catalogue describes it."""

    category: str
    name: str
    description: str
    technical_descriptor: str
    data_type: str | None
    value_range: str | None
    unit: str | None
    streamable: bool

    @property
    def is_electric_only(self) -> bool:
        """True for descriptors that only apply to electric vehicles."""
        return self.category == ELECTRIC_CATEGORY

    @property
    def is_confirmed_for_vehicle(self) -> bool:
        """True when CLAUDE.md lists this descriptor as confirmed for this car.

        "Confirmed" means confirmed in the catalogue and selected for this
        project, NOT proven to be emitted by the vehicle. Only a real call
        proves that.
        """
        return self.technical_descriptor in ALL_CONFIRMED_DESCRIPTORS

    @property
    def is_in_container(self) -> bool:
        """True when the descriptor is part of the pitwall-maintenance container."""
        return self.technical_descriptor in CONTAINER_DESCRIPTORS


@dataclass(frozen=True)
class SearchHit:
    """A catalogue entry plus the score that ranked it."""

    entry: CatalogueEntry
    score: int


class Catalogue:
    """In-memory view of the telematic catalogue."""

    def __init__(self, source: str, entries: list[CatalogueEntry]) -> None:
        """Build a catalogue from its source label and its entries."""
        self.source = source
        self.entries = entries
        self._by_descriptor = {entry.technical_descriptor: entry for entry in entries}

    # -- Loading ------------------------------------------------------------

    @classmethod
    def load(cls, path: Path) -> Catalogue:
        """Read and parse the catalogue JSON from disk.

        One quirk of the published catalogue: `vehicle.vehicle.travelledDistance`
        carries the literal STRING "null" as its unit instead of a JSON null.
        `_clean` normalises that, so no output ever prints "unidad null".
        """
        raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        entries: list[CatalogueEntry] = []
        for category in raw.get("categories", []):
            category_name = category.get("category", "")
            for item in category.get("entries", []):
                entries.append(
                    CatalogueEntry(
                        category=category_name,
                        name=item.get("name") or "",
                        description=item.get("description") or "",
                        technical_descriptor=item.get("technical_descriptor") or "",
                        data_type=_clean(item.get("data_type")),
                        value_range=_clean(item.get("value_range")),
                        unit=_clean(item.get("unit")),
                        streamable=bool(item.get("streamable")),
                    )
                )
        return cls(source=raw.get("source", ""), entries=entries)

    # -- Lookups ------------------------------------------------------------

    def __len__(self) -> int:
        """Number of descriptors in the catalogue."""
        return len(self.entries)

    @property
    def categories(self) -> dict[str, int]:
        """Descriptor count per category, in catalogue order."""
        counts: dict[str, int] = {}
        for entry in self.entries:
            counts[entry.category] = counts.get(entry.category, 0) + 1
        return counts

    def get(self, descriptor: str) -> CatalogueEntry | None:
        """Return the entry for an exact technical descriptor, if it exists."""
        return self._by_descriptor.get(descriptor)

    def search(
        self,
        query: str,
        *,
        limit: int = 20,
        include_electric: bool = False,
    ) -> list[SearchHit]:
        """Rank catalogue entries against a free-text query.

        Matching is deliberately simple and explainable: exact descriptor first,
        then descriptor substring, then name, then description. Every query term
        must appear somewhere in the entry, so a two-word query narrows instead
        of widening. Spanish terms are expanded to their English equivalents
        before matching.
        """
        normalised = _normalise(query)
        terms = [_expand(term) for term in _WORD_RE.findall(normalised)]
        if not terms:
            return []

        hits: list[SearchHit] = []
        for entry in self.entries:
            if entry.is_electric_only and not include_electric:
                continue
            score = _score(entry, normalised, terms)
            if score > 0:
                hits.append(SearchHit(entry=entry, score=score))

        # Ties break towards the shorter descriptor: the more general one.
        hits.sort(
            key=lambda hit: (
                -hit.score,
                len(hit.entry.technical_descriptor),
                hit.entry.technical_descriptor,
            )
        )
        return hits[:limit]


def _score(entry: CatalogueEntry, query: str, terms: list[tuple[str, ...]]) -> int:
    """Score one entry against the query, or 0 when a term is missing.

    `terms` holds one tuple of alternatives per query word; a word counts as
    present when any of its alternatives is.
    """
    descriptor = _normalise(entry.technical_descriptor)
    name = _normalise(entry.name)
    description = _normalise(entry.description)
    haystack = f"{descriptor} {name} {description}"

    if any(not any(alt in haystack for alt in alternatives) for alternatives in terms):
        return 0

    score = 1
    if descriptor == query:
        score += 100
    if query in descriptor:
        score += 40
    if query in name:
        score += 20
    score += sum(10 for alts in terms if any(alt in descriptor for alt in alts))
    score += sum(5 for alts in terms if any(alt in name for alt in alts))
    if entry.is_confirmed_for_vehicle:
        score += 15
    if entry.is_in_container:
        score += 5
    return score


@lru_cache(maxsize=4)
def load_catalogue(path: Path) -> Catalogue:
    """Load and memoise the catalogue for a given path."""
    return Catalogue.load(path)

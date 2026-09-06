"""Shared state handed to every tool.

Built once at start-up so tools never open their own database, catalogue or
adapter, and so tests can substitute any of the three.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property

from ..cardata.client import CarDataAdapter
from ..catalogue import Catalogue, load_catalogue
from ..config import Settings, load_settings
from ..storage.db import Database


@dataclass
class ToolContext:
    """Everything the tools need, assembled once."""

    settings: Settings
    db: Database

    @cached_property
    def catalogue(self) -> Catalogue:
        """The local telematic catalogue. Reading it costs no quota."""
        return load_catalogue(self.settings.catalogue_path)

    @cached_property
    def adapter(self) -> CarDataAdapter:
        """The CarData adapter. Constructing it sends nothing."""
        return CarDataAdapter(self.settings, db=self.db)

    @classmethod
    def build(cls, settings: Settings | None = None) -> ToolContext:
        """Build a context from the environment, opening the SQLite file."""
        resolved = settings or load_settings()
        return cls(settings=resolved, db=Database(resolved.db_path))

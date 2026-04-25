"""Abstract base class for catalog providers."""

from __future__ import annotations

from abc import ABC, abstractmethod

from catalog.models import CatalogDomain, CatalogEntry


class CatalogProvider(ABC):
    """Abstract base for all catalog providers."""

    name: str
    domain: CatalogDomain
    description: str = ""

    @abstractmethod
    def list_entries(
        self,
        search: str | None = None,
        tags: list[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[CatalogEntry]:
        ...

    @abstractmethod
    def get_entry(self, entry_id: str) -> CatalogEntry | None:
        ...

    def count(self, search: str | None = None, tags: list[str] | None = None) -> int:
        return len(self.list_entries(search=search, tags=tags, limit=99999))

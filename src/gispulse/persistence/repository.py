"""
Repository interfaces and in-memory implementation for GISPulse.

Provides:
- ``Repository[T]`` — abstract base defining the CRUD contract.
- ``InMemoryRepository[T]`` — dict-backed implementation (dev / tests).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Generic, Iterator, TypeVar
from uuid import UUID

from gispulse.core.models import (
    Artifact,
    Dataset,
    Job,
    Layer,
    Rule,
    Scenario,
    Trigger,
)

T = TypeVar(
    "T",
    Dataset,
    Layer,
    Job,
    Artifact,
    Rule,
    Trigger,
    Scenario,
)


class Repository(ABC, Generic[T]):
    """Abstract repository contract for GISPulse domain objects.

    Every object must expose an ``id: UUID`` attribute.
    """

    @abstractmethod
    def save(self, obj: T) -> T: ...

    @abstractmethod
    def get(self, obj_id: UUID) -> T | None: ...

    @abstractmethod
    def list_all(self) -> list[T]: ...

    @abstractmethod
    def delete(self, obj_id: UUID) -> bool: ...

    @abstractmethod
    def count(self) -> int: ...

    @abstractmethod
    def clear(self) -> None: ...


class InMemoryRepository(Repository[T]):
    """
    Dépôt in-memory générique pour les objets du domaine GISPulse.

    Chaque objet doit exposer un attribut ``id: UUID``.

    Usage::

        repo: InMemoryRepository[Dataset] = InMemoryRepository()
        repo.save(dataset)
        ds = repo.get(dataset.id)
        all_ds = repo.list_all()
        repo.delete(dataset.id)
    """

    def __init__(self) -> None:
        self._store: dict[UUID, T] = {}

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def save(self, obj: T) -> T:
        self._store[obj.id] = obj  # type: ignore[attr-defined]
        return obj

    def get(self, obj_id: UUID) -> T | None:
        return self._store.get(obj_id)

    def list_all(self) -> list[T]:
        return list(self._store.values())

    def delete(self, obj_id: UUID) -> bool:
        if obj_id in self._store:
            del self._store[obj_id]
            return True
        return False

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    def count(self) -> int:
        return len(self._store)

    def clear(self) -> None:
        self._store.clear()

    def __iter__(self) -> Iterator[T]:
        return iter(self._store.values())

    def __len__(self) -> int:
        return len(self._store)

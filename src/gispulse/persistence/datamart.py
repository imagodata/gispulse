"""Datamart provider — named, curated table stores addressable by URI.

DP2 of the *generic data provider* track. A *datamart* is a named
collection of curated tables backed by Parquet/GeoParquet (local, S3 or
HTTP), scanned by DuckDB. It adds a thin **naming indirection** on top of
:func:`gispulse.persistence.loader.load`:

    datamart://<mart>/<table>

resolves a logical ``(mart, table)`` pair to a concrete source URI through
a :class:`DatamartRegistry`, so pipelines reference stable logical names
(``datamart://referentiel/parcelles``) instead of hard-coded storage
paths. Because resolution yields an ordinary source the loader already
understands, every capability runs on the result unchanged and bbox
push-down still applies.

Marts are declared programmatically (:meth:`DatamartRegistry.register`) or
from the ``GISPULSE_DATAMARTS`` environment variable (a JSON object), e.g.::

    GISPULSE_DATAMARTS='{
      "referentiel": {"location": "s3://bucket/marts/referentiel"},
      "local": {"location": "/data/marts/local", "table_pattern": "{table}.parquet"}
    }'
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from typing import Any

from gispulse.core.logging import get_logger

log = get_logger(__name__)

#: Environment variable carrying a JSON map of datamart declarations.
DATAMARTS_ENV = "GISPULSE_DATAMARTS"

#: URI scheme that addresses a datamart table.
DATAMART_SCHEME = "datamart"


@dataclass(frozen=True)
class Datamart:
    """Declaration of one curated table store.

    Args:
        name:          Logical mart name used in ``datamart://<name>/…``.
        location:      Base location of the tables — a directory path or
                       an ``s3://`` / ``https://`` prefix. Joined with
                       ``table_pattern`` to address a single table.
        table_pattern: Filename pattern for a table, ``{table}`` being the
                       table name. Defaults to ``"{table}.parquet"``.
        crs:           Optional CRS to force on tables that declare none.
        metadata:      Free-form descriptive metadata.
    """

    name: str
    location: str
    table_pattern: str = "{table}.parquet"
    crs: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def uri_for(self, table: str) -> str:
        """Return the concrete source URI for *table* in this mart."""
        if not table:
            raise ValueError(f"datamart '{self.name}': empty table name")
        filename = self.table_pattern.format(table=table)
        base = self.location.rstrip("/")
        return f"{base}/{filename}"


class DatamartRegistry:
    """Thread-safe registry of declared :class:`Datamart` stores."""

    def __init__(self) -> None:
        self._marts: dict[str, Datamart] = {}
        self._lock = threading.Lock()
        self._env_loaded = False

    def register(self, mart: Datamart, *, override: bool = False) -> None:
        """Record *mart* under its name (raises on a taken slot unless override)."""
        with self._lock:
            if mart.name in self._marts and not override:
                raise ValueError(
                    f"datamart '{mart.name}' already registered; "
                    f"pass override=True to replace"
                )
            self._marts[mart.name] = mart
        log.debug("datamart_registered", name=mart.name, location=mart.location)

    def get(self, name: str) -> Datamart:
        """Return the mart registered as *name* or raise ``KeyError``."""
        self._ensure_env_loaded()
        try:
            return self._marts[name]
        except KeyError:
            raise KeyError(
                f"no datamart named {name!r} is registered "
                f"(available: {', '.join(self.names()) or 'none'}); "
                f"declare it via DatamartRegistry.register or {DATAMARTS_ENV}"
            ) from None

    def names(self) -> list[str]:
        return sorted(self._marts)

    def clear(self) -> None:
        """Drop every declaration and reset env loading — used by tests."""
        with self._lock:
            self._marts.clear()
            self._env_loaded = False

    def resolve(self, mart_name: str, table: str) -> str:
        """Resolve ``(mart, table)`` to a concrete source URI."""
        return self.get(mart_name).uri_for(table)

    def _ensure_env_loaded(self) -> None:
        """Lazily merge ``GISPULSE_DATAMARTS`` declarations (env never overrides)."""
        if self._env_loaded:
            return
        with self._lock:
            if self._env_loaded:
                return
            self._env_loaded = True
            raw = os.environ.get(DATAMARTS_ENV, "").strip()
            if not raw:
                return
            try:
                decls = json.loads(raw)
            except json.JSONDecodeError as exc:
                log.warning("datamart_env_invalid_json", error=str(exc))
                return
            if not isinstance(decls, dict):
                log.warning("datamart_env_not_object", got=type(decls).__name__)
                return
            for name, spec in decls.items():
                if name in self._marts:  # explicit registration wins
                    continue
                if not isinstance(spec, dict) or "location" not in spec:
                    log.warning("datamart_env_bad_decl", name=name)
                    continue
                self._marts[name] = Datamart(
                    name=name,
                    location=str(spec["location"]),
                    table_pattern=str(spec.get("table_pattern", "{table}.parquet")),
                    crs=spec.get("crs"),
                    metadata=dict(spec.get("metadata") or {}),
                )


def parse_datamart_uri(uri: str) -> tuple[str, str]:
    """Split ``datamart://<mart>/<table>`` into ``(mart, table)``.

    The table part may itself contain slashes (nested layout); it is kept
    verbatim after the first path separator.

    Raises:
        ValueError: if *uri* is not a well-formed datamart URI.
    """
    scheme, sep, rest = uri.partition("://")
    if not sep or scheme.lower() != DATAMART_SCHEME:
        raise ValueError(f"not a datamart URI: {uri!r}")
    mart, slash, table = rest.partition("/")
    if not mart or not slash or not table:
        raise ValueError(
            f"datamart URI must be 'datamart://<mart>/<table>', got {uri!r}"
        )
    return mart, table


#: Process-wide datamart registry consulted by the unified loader.
DATAMARTS = DatamartRegistry()


__all__ = [
    "DATAMARTS",
    "DATAMARTS_ENV",
    "DATAMART_SCHEME",
    "Datamart",
    "DatamartRegistry",
    "parse_datamart_uri",
]

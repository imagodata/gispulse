"""SpatialEngine ABC — unified interface for DuckDB and PostGIS backends.

Phase 3 introduces the concept of a *SpatialEngine*: a backend-agnostic
contract for loading, querying, and writing spatial data.  Both
:class:`DuckDBSession` and :class:`PostGISConnection` implement this
interface so the rest of the codebase never needs to know which backend
is active.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import geopandas as gpd


class SpatialEngine(ABC):
    """Backend-agnostic spatial data engine.

    Concrete implementations:
    - ``DuckDBEngine``  (portable / session mode)
    - ``PostGISEngine`` (persistent / server mode)
    """

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @abstractmethod
    def open(self) -> None:
        """Initialise the engine connection / session."""

    @abstractmethod
    def close(self) -> None:
        """Release all resources."""

    def __enter__(self) -> SpatialEngine:
        self.open()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Layer I/O
    # ------------------------------------------------------------------

    @abstractmethod
    def load_layer(
        self, source: str, *, layer: str | None = None, schema: str = "public"
    ) -> gpd.GeoDataFrame:
        """Read a spatial layer into a GeoDataFrame.

        Args:
            source: File path (DuckDB) or table name (PostGIS).
            layer:  Layer name within the source (for file-based backends).
            schema: Database schema (for PostGIS; ignored by DuckDB).
        """

    @abstractmethod
    def write_layer(
        self,
        gdf: gpd.GeoDataFrame,
        target: str,
        *,
        layer: str = "result",
        schema: str = "public",
        if_exists: str = "replace",
    ) -> str:
        """Write a GeoDataFrame to the backend.

        Args:
            gdf:       Data to write.
            target:    File path (DuckDB) or table name (PostGIS).
            layer:     Layer name (for file-based backends).
            schema:    Database schema (for PostGIS).
            if_exists: Behaviour when target exists ('replace', 'append', 'fail').

        Returns:
            Canonical reference to the written layer (path or schema.table).
        """

    @abstractmethod
    def list_layers(self, source: str | None = None, schema: str = "public") -> list[str]:
        """List available layers.

        Args:
            source: File path (DuckDB) or ``None`` (PostGIS lists schema tables).
            schema: Database schema (PostGIS only).
        """

    # ------------------------------------------------------------------
    # SQL
    # ------------------------------------------------------------------

    @abstractmethod
    def execute_sql(self, sql: str, params: dict[str, Any] | None = None) -> list[dict]:
        """Execute raw SQL and return rows as dicts."""

    @abstractmethod
    def sql_to_gdf(self, sql: str) -> gpd.GeoDataFrame:
        """Execute a spatial SQL query and return a GeoDataFrame."""

    # ------------------------------------------------------------------
    # Registration (in-session tables)
    # ------------------------------------------------------------------

    @abstractmethod
    def register(self, name: str, gdf: gpd.GeoDataFrame) -> None:
        """Register a GeoDataFrame as a named table in the engine."""

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    @property
    @abstractmethod
    def backend_name(self) -> str:
        """Return a human-readable backend identifier ('duckdb' or 'postgis')."""

    @property
    @abstractmethod
    def is_persistent(self) -> bool:
        """True if the engine persists data between sessions."""


class AsyncSpatialEngine:
    """Async wrapper around any SpatialEngine.

    Runs all synchronous engine methods in a thread pool via
    ``asyncio.to_thread()`` so they don't block the FastAPI event loop.

    Usage::

        async_engine = AsyncSpatialEngine(duckdb_session)
        gdf = await async_engine.load_layer("my_table")
    """

    def __init__(self, engine: SpatialEngine) -> None:
        self._engine = engine

    @property
    def engine(self) -> SpatialEngine:
        return self._engine

    async def open(self) -> None:
        import asyncio
        await asyncio.to_thread(self._engine.open)

    async def close(self) -> None:
        import asyncio
        await asyncio.to_thread(self._engine.close)

    async def load_layer(
        self, source: str, *, layer: str | None = None, schema: str = "public"
    ) -> gpd.GeoDataFrame:
        import asyncio
        return await asyncio.to_thread(
            self._engine.load_layer, source, layer=layer, schema=schema
        )

    async def write_layer(
        self,
        gdf: gpd.GeoDataFrame,
        target: str,
        *,
        layer: str = "result",
        schema: str = "public",
        if_exists: str = "replace",
    ) -> str:
        import asyncio
        return await asyncio.to_thread(
            self._engine.write_layer, gdf, target,
            layer=layer, schema=schema, if_exists=if_exists,
        )

    async def list_layers(
        self, source: str | None = None, schema: str = "public"
    ) -> list[str]:
        import asyncio
        return await asyncio.to_thread(self._engine.list_layers, source, schema=schema)

    async def execute_sql(
        self, sql: str, params: dict[str, Any] | None = None
    ) -> list[dict]:
        import asyncio
        return await asyncio.to_thread(self._engine.execute_sql, sql, params)

    async def sql_to_gdf(self, sql: str) -> gpd.GeoDataFrame:
        import asyncio
        return await asyncio.to_thread(self._engine.sql_to_gdf, sql)

    async def register(self, name: str, gdf: gpd.GeoDataFrame) -> None:
        import asyncio
        await asyncio.to_thread(self._engine.register, name, gdf)

    @property
    def backend_name(self) -> str:
        return self._engine.backend_name

    @property
    def is_persistent(self) -> bool:
        return self._engine.is_persistent

    async def __aenter__(self) -> "AsyncSpatialEngine":
        await self.open()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

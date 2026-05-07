"""DuckDB-diff engine — file-blob CDC across format-frontier formats.

Second slice of EPIC #105 (Format Frontier T1). This engine targets
file formats that have **no native trigger surface** (GeoJSON,
FlatGeobuf, Shapefile, KML, CSV+WKT, MapInfo TAB, DXF). Change
detection is delegated to :class:`FileBlobChangeDetector` (mtime watch
+ DuckDB ``ST_Read`` snapshot diff).

Auto-routing
------------

URI inference (``gispulse/runtime/engine_inference.py``) maps the
relevant suffixes to the engine kind ``"duckdb_diff"``. The engine
factory (``_duckdb_diff_factory``) instantiates this class.

Limitations vs. ``GeoPackageEngine`` (intentional v1.6.1 scope)
----------------------------------------------------------------

- **No SQL execution against the file.** ``execute_sql`` raises
  ``NotImplementedError`` — DuckDB-diff is a CDC adapter, not a query
  engine. Users who want SQL across these formats run ``gispulse run``
  with the DuckDB engine in standalone mode (DuckDB ``ST_Read`` works
  the same way; GISPulse just stops trying to be both at once).
- **INSERT/DELETE only.** A QGIS edit produces a DELETE (old hash) +
  INSERT (new hash). The trigger evaluator must declare ``when:
  [INSERT, DELETE]`` to react to either side.
- **Single layer per file.** Multi-layer files belong to the trigger
  engine path; this engine treats the file as one layer named after
  the filename stem.
- **Polling only.** Watchdog/inotify integration is v1.7+ scope.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import geopandas as gpd

from persistence.engine import SpatialEngine
from persistence.file_blob_cdc import FileBlobChangeDetector

logger = logging.getLogger(__name__)


class DuckDBDiffEngine(SpatialEngine):
    """File-blob CDC engine: ``mtime`` + DuckDB snapshot diff.

    Reads / writes via pyogrio (OGR auto-detects the driver from the
    extension). Change detection lives in
    :class:`FileBlobChangeDetector`.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        snapshot_path: str | Path | None = None,
        layer_name: str | None = None,
    ) -> None:
        self._path = Path(path)
        self._layer_name = layer_name or self._path.stem
        self._registered: dict[str, gpd.GeoDataFrame] = {}
        self._opened = False
        self._detector: FileBlobChangeDetector | None = None
        self._snapshot_path = (
            Path(snapshot_path) if snapshot_path is not None else None
        )

    @property
    def path(self) -> Path:
        return self._path

    @property
    def detector(self) -> FileBlobChangeDetector:
        """The CDC detector — fed into the watcher loop by callers."""
        if self._detector is None:
            raise RuntimeError(
                "DuckDBDiffEngine is not open. Call .open() first."
            )
        return self._detector

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def open(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._detector = FileBlobChangeDetector(
            self._path,
            snapshot_path=self._snapshot_path,
            table_name=self._layer_name,
        )
        self._opened = True
        logger.info("duckdb_diff_engine_opened: %s", self._path)

    def close(self) -> None:
        if self._detector is not None:
            self._detector.close()
            self._detector = None
        self._opened = False

    # ------------------------------------------------------------------
    # Layer I/O
    # ------------------------------------------------------------------

    def load_layer(
        self,
        source: str,
        *,
        layer: str | None = None,
        schema: str = "public",
    ) -> gpd.GeoDataFrame:
        """Read the file blob into a GeoDataFrame via pyogrio.

        ``source`` is accepted for API compatibility with the ABC but
        ignored; the engine is bound to ``self._path``. ``layer`` is
        also ignored — these formats are single-layer by design.
        """
        if not self._opened:
            raise RuntimeError("DuckDBDiffEngine is not open.")
        if not self._path.exists():
            raise FileNotFoundError(f"File blob not found: {self._path}")
        return gpd.read_file(str(self._path))

    def write_layer(
        self,
        gdf: gpd.GeoDataFrame,
        target: str | None = None,
        *,
        layer: str = "result",
        schema: str = "public",
        if_exists: str = "replace",
    ) -> str:
        """Write the GeoDataFrame to the file blob via pyogrio.

        OGR picks the driver from the path's extension. ``if_exists``
        is honoured: ``"replace"`` overwrites the file (mode="w"),
        ``"append"`` extends it (mode="a"). ``"fail"`` raises if the
        file exists.
        """
        if not self._opened:
            raise RuntimeError("DuckDBDiffEngine is not open.")
        if if_exists == "fail" and self._path.exists():
            raise FileExistsError(f"File blob already exists: {self._path}")

        mode = "a" if if_exists == "append" and self._path.exists() else "w"
        gdf.to_file(str(self._path), mode=mode)
        logger.info("duckdb_diff_layer_written: %s → %s", layer, self._path)
        return self._layer_name

    def list_layers(
        self, source: str | None = None, schema: str = "public"
    ) -> list[str]:
        """Return the single layer name (file stem) plus any registered ones."""
        layers = [self._layer_name] if self._path.exists() else []
        for name in self._registered:
            if name not in layers:
                layers.append(name)
        return layers

    # ------------------------------------------------------------------
    # SQL execution — explicitly NOT supported (see module docstring)
    # ------------------------------------------------------------------

    def execute_sql(
        self, sql: str, params: dict[str, Any] | None = None
    ) -> list[dict]:
        raise NotImplementedError(
            "DuckDBDiffEngine does not execute SQL against the file blob. "
            "Use ``gispulse run`` with the DuckDB engine for ad-hoc SQL, "
            "or migrate the data to a GPKG / SpatiaLite / PostGIS engine."
        )

    def sql_to_gdf(self, sql: str) -> gpd.GeoDataFrame:
        raise NotImplementedError(
            "DuckDBDiffEngine does not expose a SQL→GeoDataFrame surface. "
            "See ``execute_sql`` docstring for guidance."
        )

    # ------------------------------------------------------------------
    # Registration (in-session)
    # ------------------------------------------------------------------

    def register(self, name: str, gdf: gpd.GeoDataFrame) -> None:
        self._registered[name] = gdf

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    @property
    def backend_name(self) -> str:
        return "duckdb_diff"

    @property
    def is_persistent(self) -> bool:
        # The file itself is persistent on disk; the engine in-memory
        # state isn't. Match the ``GeoPackageEngine.is_persistent``
        # semantic (file-backed = True).
        return True

    # ------------------------------------------------------------------
    # CDC accessor — used by watchers and tests
    # ------------------------------------------------------------------

    def get_pending_changes(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return pending DML changes since the last poll.

        Mirrors the shape of ``GeoPackageEngine.get_pending_changes``
        so callers (watcher loop, action dispatcher) can iterate the
        same way regardless of the underlying engine. Each emitted
        dict has at minimum: ``id``, ``table_name``, ``operation``,
        ``row_pk``, ``new_values`` / ``old_values``.

        The ``limit`` parameter is honoured — extra records stay in
        the next poll. Polling is destructive (snapshot is updated
        after the diff), so the watcher should consume what it gets.
        """
        if self._detector is None:
            return []
        records = self._detector.poll()
        out: list[dict[str, Any]] = []
        for rec in records[:limit]:
            out.append(
                {
                    "id": str(rec.id),
                    "table_name": rec.table_name,
                    "operation": rec.operation.value
                    if hasattr(rec.operation, "value")
                    else str(rec.operation),
                    "row_pk": rec.feature_id,
                    "new_values": rec.new_values,
                    "old_values": rec.old_values,
                    "new_geom_wkt": rec.new_geom_wkt,
                    "old_geom_wkt": rec.old_geom_wkt,
                }
            )
        return out

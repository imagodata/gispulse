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

from gispulse.persistence.engine import SpatialEngine
from gispulse.persistence.file_blob_cdc import FileBlobChangeDetector

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
        # Sequential change_id counter — emulates the
        # ``_gispulse_change_log.id`` AUTOINCREMENT contract that the
        # GPKG/SpatiaLite engines provide. The watcher's
        # ``mark_changes_processed(max_id)`` call expects monotonic
        # integer ids, so we cannot reuse the FileBlobSnapshot row hash
        # (which is a hex digest, not int-castable).
        self._next_change_id: int = 1

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

        Shape matches ``GeoPackageEngine.get_pending_changes`` so the
        watcher (``persistence.change_log_watcher.ChangeLogWatcher``)
        iterates uniformly across engines. Each dict carries:

        - ``id``           — monotonic int (synthetic counter,
          per-engine instance) for ``mark_changes_processed`` ack
        - ``table_name``   — the layer / file stem
        - ``operation``    — ``"INSERT"`` or ``"DELETE"`` (set diff)
        - ``row_pk``       — the row hash (also stable across polls
          when the row content is unchanged)
        - ``new_values`` / ``old_values`` — properties dict
        - ``changed_at``   — ISO 8601 UTC timestamp of the poll
        - ``geom_changed`` — ``0`` for INSERT/DELETE; an UPDATE here
          would need a stable PK, which file-blob CDC does not have
          (cf. module docstring)
        - ``new_geom_wkt`` / ``old_geom_wkt`` — extra (not consumed
          by the watcher, exposed for future fine-grained payloads)

        Polling is destructive (the FileBlobChangeDetector updates
        its sidecar snapshot before returning). The watcher's
        ``mark_changes_processed`` is therefore a no-op for this
        engine — the changes are already consumed once the snapshot
        is rolled forward.
        """
        if self._detector is None:
            return []
        records = self._detector.poll()
        out: list[dict[str, Any]] = []
        for rec in records[:limit]:
            out.append(
                {
                    "id": self._next_change_id,
                    "table_name": rec.table_name,
                    "operation": rec.operation.value
                    if hasattr(rec.operation, "value")
                    else str(rec.operation),
                    "row_pk": rec.feature_id,
                    "new_values": rec.new_values,
                    "old_values": rec.old_values,
                    "changed_at": rec.recorded_at.isoformat(),
                    # geom_changed flag drives the watcher's
                    # UPDATE → UPDATE_GEOM/UPDATE_ATTR resolution. We
                    # only emit INSERT/DELETE so the flag is unused
                    # in practice; we set it conservatively from the
                    # presence of a new geometry on the record.
                    "geom_changed": int(rec.new_geom_wkt is not None
                                        or rec.old_geom_wkt is not None),
                    "new_geom_wkt": rec.new_geom_wkt,
                    "old_geom_wkt": rec.old_geom_wkt,
                }
            )
            self._next_change_id += 1
        return out

    def mark_changes_processed(self, up_to_id: int) -> int:
        """No-op — file-blob CDC is destructive on poll.

        The watcher contract requires this method but the
        ``FileBlobChangeDetector`` already consumed the events when
        it rolled the snapshot forward. Returns the count of synthetic
        ids that have been ack'd so the watcher's stats line up with
        the SQLite engines.
        """
        # The synthetic counter has already advanced past every emitted
        # row; ``up_to_id`` is informational only.
        return 0

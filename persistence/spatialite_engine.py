"""SpatiaLite-backed spatial engine — first format-frontier addition (#105 v1.6.1).

SpatiaLite is SQLite + the ``mod_spatialite`` extension, with a custom
geometry encoding (Spatialite Blob) and its own catalog tables
(``spatial_ref_sys`` / ``geometry_columns``) instead of GeoPackage's
``gpkg_*`` family.

For change tracking the picture is identical to GPKG: the underlying
SQLite engine accepts the same ``AFTER INSERT/UPDATE/DELETE`` triggers
that ``persistence.gpkg_schema._build_change_triggers`` already
generates. So :class:`SpatiaLiteEngine` reuses the bulk of
:class:`GeoPackageEngine` and only diverges on:

- The bootstrap step (no GPKG ``application_id``, no ``gpkg_contents``).
- The pyogrio write driver (``SQLite`` + ``SPATIALITE=YES`` instead of
  ``GPKG``).
- The "replace existing layer" cleanup path (``geometry_columns`` /
  ``virts_geometry_columns`` instead of ``gpkg_geometry_columns``).

Detection: a SQLite file is treated as SpatiaLite when ``geometry_columns``
exists and ``gpkg_contents`` does not. See :func:`is_spatialite_file`.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import geopandas as gpd

from core.logging import get_logger
from persistence.gpkg_engine import GeoPackageEngine
from persistence.gpkg_schema import (
    bootstrap_spatialite_project,
)

logger = get_logger(__name__)


def is_spatialite_file(path: str | Path) -> bool:
    """Return True iff ``path`` is an existing SpatiaLite-formatted SQLite file.

    Detection rule (intentionally narrow to avoid false positives):
        * file exists, can be opened by ``sqlite3``;
        * has a ``geometry_columns`` table (SpatiaLite catalog);
        * does NOT have ``gpkg_contents`` (would be a GPKG instead).

    A file that has neither table is treated as plain SQLite (return
    False); the caller may still create SpatiaLite content there by
    instantiating ``SpatiaLiteEngine`` explicitly.
    """
    p = Path(path)
    if not p.exists() or not p.is_file():
        return False
    try:
        with sqlite3.connect(str(p)) as conn:
            row = conn.execute(
                "SELECT "
                "  EXISTS(SELECT 1 FROM sqlite_master "
                "         WHERE type='table' AND name='geometry_columns') AS has_geom, "
                "  EXISTS(SELECT 1 FROM sqlite_master "
                "         WHERE type='table' AND name='gpkg_contents') AS has_gpkg"
            ).fetchone()
    except sqlite3.DatabaseError:
        return False
    if row is None:
        return False
    has_geom, has_gpkg = bool(row[0]), bool(row[1])
    return has_geom and not has_gpkg


class SpatiaLiteEngine(GeoPackageEngine):
    """SpatiaLite-backed spatial engine.

    Mirrors :class:`GeoPackageEngine` for everything that lives at the
    SQLite layer (change tracking, internal ``_gispulse_*`` tables,
    SQL execution, RTree). The two engines differ only at the file
    format identity layer.
    """

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def open(self) -> None:
        """Open the SpatiaLite file and bootstrap (lazy on fresh paths).

        On an existing file we connect immediately and install the
        ``_gispulse_*`` internal tables. On a non-existent path we
        defer file creation to the first :meth:`write_layer` call —
        pyogrio's ``SQLite + SPATIALITE=YES`` driver requires creating
        the file from scratch (mode="w") to initialise the SpatiaLite
        catalog (``geometry_columns`` etc). If we created an empty
        SQLite file here via ``sqlite3.connect``, pyogrio's later
        ``mode="a"`` would skip the catalog and the file would never
        register as SpatiaLite.

        Does NOT set the GPKG ``application_id`` and does NOT create
        ``gpkg_*`` catalog tables — those would corrupt SpatiaLite
        identity for OGR / QGIS.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if self._path.exists():
            self._open_conn()
            bootstrap_spatialite_project(self._conn)

        if self._use_duckdb_accel:
            try:
                import duckdb

                self._duckdb_conn = duckdb.connect(":memory:")
                self._duckdb_conn.install_extension("spatial")
                self._duckdb_conn.load_extension("spatial")
            except Exception as exc:
                logger.warning("duckdb_accel_unavailable: %s", exc)
                self._duckdb_conn = None
                self._use_duckdb_accel = False

        self._opened = True
        logger.info("spatialite_engine_opened: %s", self._path)

    # ------------------------------------------------------------------
    # Layer I/O — diverge from GPKG on the OGR driver + catalog cleanup
    # ------------------------------------------------------------------

    def _has_spatialite_catalog(self) -> bool:
        """Return True iff the file has the SpatiaLite ``geometry_columns``
        catalog. The write path uses this to decide between ``mode="w"``
        (initialise SpatiaLite) and ``mode="a"`` (append).

        Uses a transient connection so the check is safe even when
        :meth:`open` did not eagerly connect (fresh-file path).
        """
        if not self._path.exists():
            return False
        try:
            with sqlite3.connect(str(self._path)) as probe:
                row = probe.execute(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type='table' AND name='geometry_columns'"
                ).fetchone()
        except sqlite3.DatabaseError:
            return False
        return row is not None

    def write_layer(
        self,
        gdf: gpd.GeoDataFrame,
        target: str | None = None,
        *,
        layer: str = "result",
        schema: str = "public",
        if_exists: str = "replace",
    ) -> str:
        """Write a GeoDataFrame as a spatial layer in the SpatiaLite db.

        Uses pyogrio's ``SQLite`` driver with ``SPATIALITE=YES`` so the
        layer is registered in ``geometry_columns`` rather than
        ``gpkg_geometry_columns``.
        """
        spatialite_kwargs = {"SPATIALITE": "YES"}

        # Fresh-file path covers two cases that both need ``mode="w"``:
        # (1) the file does not exist yet, (2) the file exists but
        # :meth:`open` only created an empty SQLite skeleton with no
        # ``geometry_columns`` catalog yet (pyogrio cannot append to a
        # non-SpatiaLite SQLite file without losing spatial metadata).
        if not self._has_spatialite_catalog():
            self._close_conn()
            gdf.to_file(
                str(self._path),
                layer=layer,
                driver="SQLite",
                mode="w",
                **spatialite_kwargs,
            )
            self._open_conn()
            bootstrap_spatialite_project(self._conn)
        elif if_exists == "append":
            self._close_conn()
            gdf.to_file(
                str(self._path),
                layer=layer,
                driver="SQLite",
                mode="a",
                **spatialite_kwargs,
            )
            self._open_conn()
        else:
            existing = self.list_layers()
            if layer in existing:
                conn = self._get_conn()
                with self._lock:
                    conn.execute(f'DROP TABLE IF EXISTS "{layer}"')
                    # SpatiaLite catalog tables — defensive try/except
                    # because not every SpatiaLite file carries every
                    # virts_/views_ companion table.
                    for catalog in (
                        "geometry_columns",
                        "views_geometry_columns",
                        "virts_geometry_columns",
                    ):
                        try:
                            conn.execute(
                                f"DELETE FROM {catalog} WHERE f_table_name = ?",
                                (layer,),
                            )
                        except sqlite3.OperationalError:
                            pass
                    # SpatiaLite RTree pattern: idx_<table>_<geom_col>
                    # Drop any RTree we can find for this table.
                    rtree_rows = conn.execute(
                        "SELECT name FROM sqlite_master "
                        "WHERE type='table' AND name LIKE ? ESCAPE '\\'",
                        (f"idx\\_{layer}\\_%",),
                    ).fetchall()
                    for (rtree_name,) in rtree_rows:
                        conn.execute(f'DROP TABLE IF EXISTS "{rtree_name}"')
                    conn.commit()
            self._close_conn()
            gdf.to_file(
                str(self._path),
                layer=layer,
                driver="SQLite",
                mode="a",
                **spatialite_kwargs,
            )
            self._open_conn()

        logger.info("spatialite_layer_written: %s → %s", layer, self._path)
        return layer

    def list_layers(self, source: str | None = None, schema: str = "public") -> list[str]:
        """List spatial layers (queries ``geometry_columns``, not GPKG catalog).

        Falls back to pyogrio if the SpatiaLite catalog is missing —
        e.g. a fresh file created via :meth:`write_layer` before any
        DML.
        """
        if not self._path.exists():
            return list(self._registered.keys())

        try:
            conn = self._get_conn()
            rows = conn.execute(
                "SELECT f_table_name FROM geometry_columns"
            ).fetchall()
            spatial_layers = [
                str(r[0]) for r in rows if not str(r[0]).startswith("_gispulse_")
            ]
        except sqlite3.OperationalError:
            # Catalog missing — fall back to pyogrio enumeration.
            try:
                import pyogrio

                info = pyogrio.list_layers(str(self._path))
                spatial_layers = [
                    name for name, _ in info if not name.startswith("_gispulse_")
                ]
            except Exception:
                spatial_layers = []

        for name in self._registered:
            if name not in spatial_layers:
                spatial_layers.append(name)

        return spatial_layers

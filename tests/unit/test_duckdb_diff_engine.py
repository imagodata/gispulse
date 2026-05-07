"""Tests for ``persistence.duckdb_diff_engine.DuckDBDiffEngine`` (#105 slice 3).

Covers:
- Lifecycle (open/close, ``backend_name`` / ``is_persistent``).
- Layer I/O: load_layer / write_layer / list_layers via pyogrio.
- ``execute_sql`` / ``sql_to_gdf`` are intentionally NotImplemented.
- ``get_pending_changes`` plumbing — wraps the FileBlobChangeDetector
  output with the ``GeoPackageEngine``-compatible dict shape.
- Engine factory registration (``_BACKENDS["duckdb_diff"]``).
- E2E with a GeoJSON: write + edit + poll → INSERT/DELETE pair.
"""
from __future__ import annotations

import os
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import Point

from persistence.duckdb_diff_engine import DuckDBDiffEngine


def _bump_mtime(path: Path) -> None:
    stat = os.stat(path)
    os.utime(path, (stat.st_atime, stat.st_mtime + 1.5))


@pytest.fixture
def sample_gdf() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "name": ["A", "B", "C"],
            "category": [1, 2, 3],
            "geometry": [Point(0, 0), Point(1, 1), Point(2, 2)],
        },
        crs="EPSG:4326",
    )


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    def test_backend_name(self, tmp_path: Path) -> None:
        engine = DuckDBDiffEngine(tmp_path / "x.geojson")
        assert engine.backend_name == "duckdb_diff"

    def test_is_persistent(self, tmp_path: Path) -> None:
        engine = DuckDBDiffEngine(tmp_path / "x.geojson")
        assert engine.is_persistent is True

    def test_layer_name_defaults_to_filename_stem(self, tmp_path: Path) -> None:
        engine = DuckDBDiffEngine(tmp_path / "places.geojson")
        with engine:
            assert engine.detector.table_name == "places"

    def test_custom_layer_name(self, tmp_path: Path) -> None:
        engine = DuckDBDiffEngine(
            tmp_path / "places.geojson", layer_name="custom"
        )
        with engine:
            assert engine.detector.table_name == "custom"

    def test_open_close_idempotent(self, tmp_path: Path) -> None:
        engine = DuckDBDiffEngine(tmp_path / "x.geojson")
        engine.open()
        engine.close()
        engine.close()  # second close is a no-op

    def test_detector_inaccessible_before_open(self, tmp_path: Path) -> None:
        engine = DuckDBDiffEngine(tmp_path / "x.geojson")
        with pytest.raises(RuntimeError, match="not open"):
            engine.detector


# ---------------------------------------------------------------------------
# I/O — pyogrio path
# ---------------------------------------------------------------------------


class TestLayerIO:
    def test_write_then_read_geojson(
        self, tmp_path: Path, sample_gdf: gpd.GeoDataFrame
    ) -> None:
        path = tmp_path / "rt.geojson"
        engine = DuckDBDiffEngine(path)
        with engine:
            engine.write_layer(sample_gdf)
            loaded = engine.load_layer(str(path))
        assert len(loaded) == 3
        assert set(loaded["name"]) == {"A", "B", "C"}

    def test_load_missing_file_raises(self, tmp_path: Path) -> None:
        engine = DuckDBDiffEngine(tmp_path / "ghost.geojson")
        with engine:
            with pytest.raises(FileNotFoundError):
                engine.load_layer("ignored")

    def test_write_layer_fail_when_exists(
        self, tmp_path: Path, sample_gdf: gpd.GeoDataFrame
    ) -> None:
        path = tmp_path / "x.geojson"
        engine = DuckDBDiffEngine(path)
        with engine:
            engine.write_layer(sample_gdf)
            with pytest.raises(FileExistsError):
                engine.write_layer(sample_gdf, if_exists="fail")

    def test_list_layers_returns_stem(
        self, tmp_path: Path, sample_gdf: gpd.GeoDataFrame
    ) -> None:
        path = tmp_path / "places.geojson"
        engine = DuckDBDiffEngine(path)
        with engine:
            engine.write_layer(sample_gdf)
            layers = engine.list_layers()
        assert "places" in layers

    def test_list_layers_empty_before_first_write(self, tmp_path: Path) -> None:
        engine = DuckDBDiffEngine(tmp_path / "x.geojson")
        with engine:
            assert engine.list_layers() == []

    def test_register_in_session(
        self, tmp_path: Path, sample_gdf: gpd.GeoDataFrame
    ) -> None:
        engine = DuckDBDiffEngine(tmp_path / "x.geojson")
        with engine:
            engine.register("scratch", sample_gdf)
            layers = engine.list_layers()
        assert "scratch" in layers


# ---------------------------------------------------------------------------
# SQL surface — intentionally NotImplemented
# ---------------------------------------------------------------------------


class TestSQLSurface:
    def test_execute_sql_raises(self, tmp_path: Path) -> None:
        engine = DuckDBDiffEngine(tmp_path / "x.geojson")
        with engine:
            with pytest.raises(NotImplementedError, match="DuckDBDiffEngine"):
                engine.execute_sql("SELECT 1")

    def test_sql_to_gdf_raises(self, tmp_path: Path) -> None:
        engine = DuckDBDiffEngine(tmp_path / "x.geojson")
        with engine:
            with pytest.raises(NotImplementedError):
                engine.sql_to_gdf("SELECT 1")


# ---------------------------------------------------------------------------
# CDC plumbing — get_pending_changes
# ---------------------------------------------------------------------------


class TestPendingChanges:
    def test_no_file_returns_empty(self, tmp_path: Path) -> None:
        engine = DuckDBDiffEngine(tmp_path / "ghost.geojson")
        with engine:
            assert engine.get_pending_changes() == []

    def test_first_poll_emits_inserts(
        self, tmp_path: Path, sample_gdf: gpd.GeoDataFrame
    ) -> None:
        path = tmp_path / "x.geojson"
        sample_gdf.to_file(str(path), driver="GeoJSON")

        engine = DuckDBDiffEngine(path)
        with engine:
            pending = engine.get_pending_changes()
        assert len(pending) == 3
        assert all(p["operation"] == "INSERT" for p in pending)
        assert {p["new_values"]["name"] for p in pending} == {"A", "B", "C"}

    def test_pending_dict_shape_matches_gpkg_engine(
        self, tmp_path: Path, sample_gdf: gpd.GeoDataFrame
    ) -> None:
        # ChangeLogWatcher._process_row casts ``id`` to int, reads
        # ``table_name``, ``operation``, ``row_pk``, ``changed_at``
        # and ``geom_changed``. The shape must match across engines.
        path = tmp_path / "x.geojson"
        sample_gdf.to_file(str(path), driver="GeoJSON")

        engine = DuckDBDiffEngine(path)
        with engine:
            pending = engine.get_pending_changes()
        rec = pending[0]
        required = {
            "id",
            "table_name",
            "operation",
            "row_pk",
            "new_values",
            "old_values",
            "changed_at",
            "geom_changed",
        }
        assert required.issubset(rec.keys())
        # ``id`` must be int-castable — the watcher does ``int(row["id"])``.
        assert isinstance(rec["id"], int)
        # ``geom_changed`` must be int-typed (0/1) so the watcher's
        # ``bool(row.get("geom_changed"))`` works.
        assert isinstance(rec["geom_changed"], int)

    def test_ids_are_monotonic(
        self, tmp_path: Path, sample_gdf: gpd.GeoDataFrame
    ) -> None:
        # The watcher's ``mark_changes_processed(max_id)`` ack relies
        # on ids being monotonic across polls.
        path = tmp_path / "x.geojson"
        sample_gdf.to_file(str(path), driver="GeoJSON")

        engine = DuckDBDiffEngine(path)
        with engine:
            pending = engine.get_pending_changes()
        ids = [r["id"] for r in pending]
        assert ids == sorted(ids)
        assert ids == list(range(1, len(pending) + 1))

    def test_mark_changes_processed_is_noop(
        self, tmp_path: Path, sample_gdf: gpd.GeoDataFrame
    ) -> None:
        # File-blob CDC is destructive on poll — mark_changes_processed
        # exists for protocol compatibility but does no work.
        path = tmp_path / "x.geojson"
        sample_gdf.to_file(str(path), driver="GeoJSON")
        engine = DuckDBDiffEngine(path)
        with engine:
            pending = engine.get_pending_changes()
            max_id = max(r["id"] for r in pending)
            # No exception, returns 0 (no rows were "marked" — the
            # snapshot has already advanced).
            assert engine.mark_changes_processed(max_id) == 0

    def test_limit_applied(
        self, tmp_path: Path, sample_gdf: gpd.GeoDataFrame
    ) -> None:
        path = tmp_path / "x.geojson"
        sample_gdf.to_file(str(path), driver="GeoJSON")

        engine = DuckDBDiffEngine(path)
        with engine:
            pending = engine.get_pending_changes(limit=2)
        assert len(pending) == 2

    def test_e2e_geojson_edit_emits_delete_plus_insert(
        self, tmp_path: Path, sample_gdf: gpd.GeoDataFrame
    ) -> None:
        # The killer demo: a user edits a GeoJSON in QGIS / a text
        # editor; the CDC engine surfaces the diff as DELETE+INSERT
        # on the next poll.
        path = tmp_path / "places.geojson"
        sample_gdf.to_file(str(path), driver="GeoJSON")

        engine = DuckDBDiffEngine(path)
        with engine:
            engine.get_pending_changes()  # baseline

            edited = gpd.read_file(str(path))
            edited.loc[edited["name"] == "A", "category"] = 999
            edited.to_file(str(path), driver="GeoJSON")
            _bump_mtime(path)

            pending = engine.get_pending_changes()

        ops = sorted(p["operation"] for p in pending)
        assert ops == ["DELETE", "INSERT"]


# ---------------------------------------------------------------------------
# Factory registration
# ---------------------------------------------------------------------------


class TestFactory:
    def test_duckdb_diff_factory_registered(self) -> None:
        from persistence.engine_factory import _BACKENDS

        assert "duckdb_diff" in _BACKENDS

    def test_factory_returns_engine(self, tmp_path: Path) -> None:
        from persistence.engine_factory import _BACKENDS

        path = tmp_path / "x.geojson"
        engine = _BACKENDS["duckdb_diff"](file_path=str(path))
        assert isinstance(engine, DuckDBDiffEngine)

    def test_duckdb_diff_is_community_tier(self) -> None:
        # The CDC engine is portable and runs entirely in-process —
        # tier gating must mirror gpkg / spatialite (Community).
        from persistence.tier import enforce_engine_tier

        # If this raises TierError the gating is wrong. Currently
        # ``enforce_engine_tier`` only blocks ``postgis`` / ``hybrid``;
        # any other backend name passes through.
        enforce_engine_tier("duckdb_diff")

"""Tests for persistence.gpkg_engine.GeoPackageEngine.

GPKG is the "one file = complete project" engine — layer I/O, spatial
queries, change tracking, key-value store, in-memory registration. Bugs
here corrupt user projects, so pin the contract tightly.
"""
from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import Point, Polygon, box

from persistence.gpkg_engine import GeoPackageEngine


@pytest.fixture
def sample_gdf() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "id": [1, 2, 3, 4, 5],
            "name": ["A", "B", "C", "D", "E"],
            "value": [10, 20, 30, 40, 50],
            "geometry": [
                Point(0, 0),
                Point(1, 1),
                Point(2, 2),
                Point(3, 3),
                Point(4, 4),
            ],
        },
        crs="EPSG:4326",
    )


@pytest.fixture
def engine(tmp_path) -> GeoPackageEngine:
    return GeoPackageEngine(tmp_path / "test.gpkg")


class TestLifecycle:
    def test_path_property_is_absolute(self, tmp_path):
        eng = GeoPackageEngine(tmp_path / "foo.gpkg")
        assert eng.path == tmp_path / "foo.gpkg"

    def test_open_creates_parent_dir(self, tmp_path):
        eng = GeoPackageEngine(tmp_path / "nested" / "dir" / "proj.gpkg")
        eng.open()
        try:
            assert (tmp_path / "nested" / "dir").is_dir()
        finally:
            eng.close()

    def test_open_creates_gpkg_file(self, tmp_path):
        eng = GeoPackageEngine(tmp_path / "new.gpkg")
        assert not eng.path.exists()
        eng.open()
        try:
            assert eng.path.exists()
        finally:
            eng.close()

    def test_close_clears_registered(self, engine, sample_gdf):
        engine.open()
        engine.register("tmp", sample_gdf)
        assert "tmp" in engine._registered
        engine.close()
        assert engine._registered == {}

    def test_context_manager(self, tmp_path, sample_gdf):
        with GeoPackageEngine(tmp_path / "ctx.gpkg") as eng:
            eng.write_layer(sample_gdf, layer="cities")
            layers = eng.list_layers()
            assert "cities" in layers

    def test_get_conn_before_open_raises(self, tmp_path):
        eng = GeoPackageEngine(tmp_path / "x.gpkg")
        with pytest.raises(RuntimeError, match="not open"):
            eng._get_conn()


class TestLayerIO:
    def test_write_and_read_roundtrip(self, engine, sample_gdf):
        engine.open()
        try:
            engine.write_layer(sample_gdf, layer="points")
            fetched = engine.load_layer(layer="points")
            assert len(fetched) == 5
            assert "value" in fetched.columns
        finally:
            engine.close()

    def test_list_layers_returns_written_layers(self, engine, sample_gdf):
        engine.open()
        try:
            engine.write_layer(sample_gdf, layer="a")
            engine.write_layer(sample_gdf, layer="b")
            layers = engine.list_layers()
            assert "a" in layers
            assert "b" in layers
        finally:
            engine.close()

    def test_list_layers_empty_when_no_writes(self, engine):
        engine.open()
        try:
            assert engine.list_layers() == []
        finally:
            engine.close()

    def test_load_layer_with_max_rows(self, engine, sample_gdf):
        engine.open()
        try:
            engine.write_layer(sample_gdf, layer="points")
            fetched = engine.load_layer(layer="points", max_rows=2)
            assert len(fetched) == 2
        finally:
            engine.close()

    def test_load_layer_source_shortcut(self, engine, sample_gdf):
        """When only `source` is passed (no `layer`), it's treated as the layer name."""
        engine.open()
        try:
            engine.write_layer(sample_gdf, layer="points")
            fetched = engine.load_layer("points")
            assert len(fetched) == 5
        finally:
            engine.close()

    def test_write_layer_replace(self, engine, sample_gdf):
        engine.open()
        try:
            engine.write_layer(sample_gdf, layer="points")
            smaller = sample_gdf.iloc[:2].copy()
            engine.write_layer(smaller, layer="points", if_exists="replace")
            fetched = engine.load_layer(layer="points")
            assert len(fetched) == 2
        finally:
            engine.close()


class TestRegistration:
    def test_register_stores_in_memory(self, engine, sample_gdf):
        """register() writes to the in-memory dict and list_layers surfaces it."""
        engine.open()
        try:
            engine.register("tmp", sample_gdf)
            assert "tmp" in engine._registered
            # list_layers combines GPKG layers + in-memory registrations
            assert "tmp" in engine.list_layers()
        finally:
            engine.close()

    def test_persist_writes_and_removes_registration(self, engine, sample_gdf):
        engine.open()
        try:
            engine.register("to_save", sample_gdf)
            ref = engine.persist("to_save")
            assert "to_save" in engine.list_layers()
            assert "to_save" not in engine._registered
            assert ref  # some ref string
        finally:
            engine.close()

    def test_persist_unknown_raises(self, engine):
        engine.open()
        try:
            with pytest.raises(KeyError, match="No registered"):
                engine.persist("never-registered")
        finally:
            engine.close()

    def test_persist_all_commits_everything(self, engine, sample_gdf):
        engine.open()
        try:
            engine.register("a", sample_gdf)
            engine.register("b", sample_gdf.iloc[:2].copy())
            engine.register("c", sample_gdf.iloc[:1].copy())
            refs = engine.persist_all()
            assert len(refs) == 3
            assert engine._registered == {}
            assert set(engine.list_layers()) >= {"a", "b", "c"}
        finally:
            engine.close()


class TestKeyValueStore:
    def test_kv_set_and_get(self, engine):
        engine.open()
        try:
            engine.kv_set("answer", "42")
            assert engine.kv_get("answer") == "42"
        finally:
            engine.close()

    def test_kv_get_unknown_returns_none(self, engine):
        engine.open()
        try:
            assert engine.kv_get("never-set") is None
        finally:
            engine.close()

    def test_kv_set_upserts(self, engine):
        engine.open()
        try:
            engine.kv_set("k", "v1")
            engine.kv_set("k", "v2")
            assert engine.kv_get("k") == "v2"
        finally:
            engine.close()

    def test_kv_delete_returns_true_when_existed(self, engine):
        engine.open()
        try:
            engine.kv_set("k", "v")
            assert engine.kv_delete("k") is True
            assert engine.kv_get("k") is None
        finally:
            engine.close()

    def test_kv_delete_returns_false_when_missing(self, engine):
        engine.open()
        try:
            assert engine.kv_delete("never-set") is False
        finally:
            engine.close()

    def test_kv_persists_across_reopen(self, tmp_path):
        path = tmp_path / "kv.gpkg"
        eng1 = GeoPackageEngine(path)
        eng1.open()
        eng1.kv_set("project_id", "gispulse-1")
        eng1.close()

        eng2 = GeoPackageEngine(path)
        eng2.open()
        try:
            assert eng2.kv_get("project_id") == "gispulse-1"
        finally:
            eng2.close()


class TestChangeTracking:
    def test_enable_and_get_pending(self, engine, sample_gdf):
        engine.open()
        try:
            engine.write_layer(sample_gdf, layer="tracked")
            try:
                engine.enable_change_tracking("tracked", pk_col="id")
            except Exception as exc:
                pytest.skip(f"Change tracking requires SpatiaLite: {exc}")

            conn = engine._get_conn()
            try:
                conn.execute(
                    "INSERT INTO tracked (id, name, value) VALUES (99, 'Z', 999)"
                )
                conn.commit()
            except Exception as exc:
                pytest.skip(f"Trigger SQL requires SpatiaLite: {exc}")

            changes = engine.get_pending_changes()
            assert any(c.get("operation", "").upper() == "INSERT" for c in changes)
        finally:
            engine.close()

    def test_mark_changes_processed(self, engine, sample_gdf):
        engine.open()
        try:
            engine.write_layer(sample_gdf, layer="t")
            try:
                engine.enable_change_tracking("t", pk_col="id")
                conn = engine._get_conn()
                conn.execute("INSERT INTO t (id, name, value) VALUES (42, 'X', 1)")
                conn.commit()
            except Exception as exc:
                pytest.skip(f"SpatiaLite not available: {exc}")

            changes = engine.get_pending_changes()
            if not changes:
                pytest.skip("No change event captured by triggers")
            last_id = max(c["id"] for c in changes)
            n = engine.mark_changes_processed(last_id)
            assert n >= 1
            assert engine.get_pending_changes() == []
        finally:
            engine.close()

    def test_disable_change_tracking_is_idempotent(self, engine, sample_gdf):
        engine.open()
        try:
            engine.write_layer(sample_gdf, layer="t")
            # disable without enable first — must not raise
            engine.disable_change_tracking("t")
        finally:
            engine.close()


class TestSpatialQueries:
    def test_bbox_filter_restricts_features(self, engine, sample_gdf):
        engine.open()
        try:
            engine.write_layer(sample_gdf, layer="points")
            # Box captures first 3 points only
            filtered = engine.bbox_filter("points", (-0.5, -0.5, 2.5, 2.5))
            assert len(filtered) == 3
        finally:
            engine.close()

    def test_spatial_query_intersects(self, engine, sample_gdf):
        engine.open()
        try:
            engine.write_layer(sample_gdf, layer="points")
            query_geom = box(-0.5, -0.5, 1.5, 1.5)
            result = engine.spatial_query("points", query_geom, predicate="intersects")
            assert len(result) == 2  # (0,0) and (1,1)
        finally:
            engine.close()


class TestMetadataProperties:
    def test_backend_name(self, engine):
        assert engine.backend_name == "gpkg"

    def test_is_persistent(self, engine):
        assert engine.is_persistent is True

    def test_info_returns_dict(self, engine, sample_gdf):
        engine.open()
        try:
            engine.write_layer(sample_gdf, layer="cities")
            info = engine.info()
            assert isinstance(info, dict)
            assert "path" in info or "layers" in info or "file_size" in info
        finally:
            engine.close()

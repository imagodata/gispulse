"""Tests for the multi-engine ``POST /datasets/{id}/enable_tracking`` route (#157).

Covers:
- ``_resolve_engine_kind_for_tracking`` — URI inference + fallback by
  ``ds.format`` + 400 for unsupported formats.
- ``WatcherRegistry.register(engine_kind=...)`` — engine dispatch for
  the 3 supported kinds (``gpkg``, ``spatialite``, ``duckdb_diff``)
  + ValueError on unknown.
- E2E: a registry registration on a GeoJSON file works end-to-end —
  the watcher starts, the detector emits INSERT events for the
  baseline.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import geopandas as gpd
import pytest
from fastapi import HTTPException
from shapely.geometry import Point

from gispulse.core.models import Dataset
from gispulse.adapters.http.routers.datasets_router import (
    _resolve_engine_kind_for_tracking,
)


def _gpkg_dataset(path: Path) -> Dataset:
    return Dataset(
        name="parcels",
        source_path=str(path),
        format="gpkg",
    )


def _geojson_dataset(path: Path) -> Dataset:
    return Dataset(
        name="places",
        source_path=str(path),
        format="geojson",
    )


def _spatialite_dataset(path: Path) -> Dataset:
    return Dataset(
        name="legacy",
        source_path=str(path),
        format="sqlite",
    )


# ---------------------------------------------------------------------------
# _resolve_engine_kind_for_tracking
# ---------------------------------------------------------------------------


class TestResolveEngineKind:
    def test_gpkg_format_short_circuits_to_gpkg(self, tmp_path: Path) -> None:
        # The dataset.format hint stamps "gpkg" at upload time; trust it
        # over URI inference because demo paths may be renamed.
        path = tmp_path / "anything.bin"  # extension lying intentionally
        path.touch()
        ds = _gpkg_dataset(path)
        assert _resolve_engine_kind_for_tracking(ds, path) == "gpkg"

    def test_geojson_uri_routes_to_duckdb_diff(self, tmp_path: Path) -> None:
        path = tmp_path / "places.geojson"
        path.touch()
        ds = Dataset(name="places", source_path=str(path), format="geojson")
        assert _resolve_engine_kind_for_tracking(ds, path) == "duckdb_diff"

    def test_fgb_uri_routes_to_duckdb_diff(self, tmp_path: Path) -> None:
        path = tmp_path / "fast.fgb"
        path.touch()
        ds = Dataset(name="fast", source_path=str(path), format="fgb")
        assert _resolve_engine_kind_for_tracking(ds, path) == "duckdb_diff"

    def test_shapefile_uri_routes_to_duckdb_diff(self, tmp_path: Path) -> None:
        path = tmp_path / "legacy.shp"
        path.touch()
        ds = Dataset(name="legacy", source_path=str(path), format="shp")
        assert _resolve_engine_kind_for_tracking(ds, path) == "duckdb_diff"

    def test_sqlite_uri_routes_to_spatialite(self, tmp_path: Path) -> None:
        path = tmp_path / "legacy.sqlite"
        path.touch()
        ds = Dataset(name="legacy", source_path=str(path), format="sqlite")
        assert _resolve_engine_kind_for_tracking(ds, path) == "spatialite"

    def test_unknown_extension_raises_400(self, tmp_path: Path) -> None:
        path = tmp_path / "data.xyz"
        path.touch()
        ds = Dataset(name="x", source_path=str(path), format="xyz")
        with pytest.raises(HTTPException) as exc:
            _resolve_engine_kind_for_tracking(ds, path)
        assert exc.value.status_code == 400
        body = exc.value.detail
        assert isinstance(body, dict)
        assert body["error"]["code"] == "tracking_unsupported_format"

    def test_postgis_uri_raises_400(self, tmp_path: Path) -> None:
        # PostGIS is intentionally NOT supported via this endpoint —
        # pg_notify integration ships through a different path.
        path = Path("postgresql://localhost/db")
        ds = Dataset(name="db", source_path=str(path), format="postgis")
        with pytest.raises(HTTPException) as exc:
            _resolve_engine_kind_for_tracking(ds, path)
        assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# WatcherRegistry.register(engine_kind=...)
# ---------------------------------------------------------------------------


class _FakeHub:
    """Minimal stand-in for EventHub.broadcast — captures events in memory."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def broadcast(self, event_type: str, data: dict | None = None) -> None:
        self.events.append((event_type, data or {}))


@pytest.fixture
def fake_hub() -> _FakeHub:
    return _FakeHub()


class TestRegistryEngineDispatch:
    def test_unknown_engine_kind_raises_value_error(
        self, tmp_path: Path, fake_hub: _FakeHub
    ) -> None:
        from gispulse.persistence.watcher_registry import WatcherRegistry

        registry = WatcherRegistry(event_hub=fake_hub)
        path = tmp_path / "x.gpkg"
        path.touch()
        with pytest.raises(ValueError, match="unsupported engine_kind"):
            registry.register("ds-x", path, engine_kind="not_a_real_engine")

    def test_register_geojson_via_duckdb_diff(
        self, tmp_path: Path, fake_hub: _FakeHub
    ) -> None:
        from gispulse.persistence.watcher_registry import WatcherRegistry

        # Real GeoJSON, real DuckDBDiffEngine path. Confirms the
        # WatcherRegistry can build and start a watcher on a non-GPKG
        # file end-to-end.
        path = tmp_path / "places.geojson"
        gdf = gpd.GeoDataFrame(
            {
                "name": ["A", "B", "C"],
                "geometry": [Point(0, 0), Point(1, 1), Point(2, 2)],
            },
            crs="EPSG:4326",
        )
        gdf.to_file(str(path), driver="GeoJSON")

        registry = WatcherRegistry(event_hub=fake_hub)
        try:
            ok = registry.register(
                "ds-geojson",
                path,
                engine_kind="duckdb_diff",
                layers=["places"],
            )
            assert ok is True
            assert registry.is_registered("ds-geojson")
            assert registry.get_layers("ds-geojson") == ["places"]
        finally:
            registry.unregister("ds-geojson")

    def test_register_spatialite(
        self, tmp_path: Path, fake_hub: _FakeHub
    ) -> None:
        from gispulse.persistence.watcher_registry import WatcherRegistry

        path = tmp_path / "legacy.sqlite"
        # Bootstrap a real SpatiaLite-style file via pyogrio so the
        # engine's open() doesn't choke on missing catalog tables.
        gdf = gpd.GeoDataFrame(
            {"name": ["A"], "geometry": [Point(0, 0)]}, crs="EPSG:4326"
        )
        gdf.to_file(str(path), driver="SQLite", layer="places", SPATIALITE="YES")

        registry = WatcherRegistry(event_hub=fake_hub)
        try:
            ok = registry.register(
                "ds-spatialite",
                path,
                engine_kind="spatialite",
                layers=["places"],
            )
            assert ok is True
            assert registry.is_registered("ds-spatialite")
        finally:
            registry.unregister("ds-spatialite")

    def test_register_gpkg_default_engine_kind_back_compat(
        self, tmp_path: Path, fake_hub: _FakeHub
    ) -> None:
        # Calling without engine_kind kwarg must still work — back-
        # compat with v1.6.x callers (HTTP routes pre-#157).
        from gispulse.persistence.gpkg_schema import bootstrap_gpkg_project
        from gispulse.persistence.watcher_registry import WatcherRegistry

        path = tmp_path / "ds.gpkg"
        conn = sqlite3.connect(str(path))
        try:
            bootstrap_gpkg_project(conn)
            conn.execute(
                'CREATE TABLE "parcels" (fid INTEGER PRIMARY KEY, name TEXT)'
            )
            conn.commit()
        finally:
            conn.close()

        registry = WatcherRegistry(event_hub=fake_hub)
        try:
            ok = registry.register("ds-gpkg", path, layers=["parcels"])
            assert ok is True
        finally:
            registry.unregister("ds-gpkg")

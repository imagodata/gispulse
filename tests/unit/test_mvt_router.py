"""
Tests for MVT tiles router (adapters/http/routers/tiles_router.py).

The tiles router is only mounted when engine == 'postgis', so most tests
patch the engine backend. TestClient is used for HTTP-layer tests and
unit-level tests cover tile math and caching helpers directly.
"""

from __future__ import annotations

import os
import time
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from gispulse.adapters.http.routers.tiles_router import (
    _cache_get,
    _cache_put,
    _tile_bounds,
    _tile_bounds_3857,
    _tile_cache,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_postgis_app():
    """Create a full-mode app with the tiles router mounted (PostGIS path)."""
    os.environ.setdefault("GISPULSE_STORAGE", "memory")
    # Don't hard-set GISPULSE_ENGINE here — the test uses a mock spatial engine
    # injected on app.state. Mutating the env permanently from a helper leaked
    # across tests and broke test_default_gpkg in the full suite.

    from gispulse.adapters.http.app import create_app
    from gispulse.persistence.engine import SpatialEngine

    app = create_app(mode="full")

    # Inject a mock SpatialEngine that reports backend_name = "postgis"
    mock_engine = MagicMock(spec=SpatialEngine)
    mock_engine.backend_name = "postgis"
    mock_engine.is_persistent = True

    # Mount tiles router manually (normally mounted only for postgis)
    from gispulse.adapters.http.routers.tiles_router import router as tiles_router
    app.include_router(tiles_router)

    # Override app.state.spatial_engine after creation
    app.state.spatial_engine = mock_engine
    return app, mock_engine


# ---------------------------------------------------------------------------
# Tests — tile math
# ---------------------------------------------------------------------------


class TestTileBounds:
    def test_zoom_0_covers_world(self):
        minx, miny, maxx, maxy = _tile_bounds(0, 0, 0)
        assert minx == pytest.approx(-180.0, abs=0.1)
        assert maxx == pytest.approx(180.0, abs=0.1)

    def test_zoom_1_nw_tile(self):
        minx, miny, maxx, maxy = _tile_bounds(1, 0, 0)
        assert minx == pytest.approx(-180.0, abs=0.1)
        assert maxx == pytest.approx(0.0, abs=0.1)
        assert maxy > 0  # northern hemisphere

    def test_bounds_lat_range(self):
        minx, miny, maxx, maxy = _tile_bounds(5, 16, 11)
        assert -90 <= miny <= maxy <= 90
        assert -180 <= minx <= maxx <= 180

    def test_tile_bounds_3857_zoom_0(self):
        xmin, ymin, xmax, ymax = _tile_bounds_3857(0, 0, 0)
        HALF_CIRC = 20037508.3427892
        assert xmin == pytest.approx(-HALF_CIRC, rel=1e-5)
        assert xmax == pytest.approx(HALF_CIRC, rel=1e-5)

    def test_tile_bounds_3857_zoom_1(self):
        xmin, ymin, xmax, ymax = _tile_bounds_3857(1, 0, 0)
        HALF_CIRC = 20037508.3427892
        assert xmin == pytest.approx(-HALF_CIRC, rel=1e-5)
        assert xmax == pytest.approx(0.0, abs=1.0)


# ---------------------------------------------------------------------------
# Tests — tile cache helpers
# ---------------------------------------------------------------------------


class TestTileCache:
    def setup_method(self):
        """Clear tile cache before each test."""
        _tile_cache.clear()

    def test_cache_miss_returns_none(self):
        assert _cache_get(("abc", 1, 0, 0)) is None

    def test_cache_put_and_get(self):
        key = ("collection_1", 5, 3, 2)
        data = b"fake_tile_bytes"
        _cache_put(key, data)
        result = _cache_get(key)
        assert result == data

    def test_cache_expired_returns_none(self):
        key = ("collection_exp", 5, 0, 0)
        _tile_cache[key] = (b"data", time.time() - 4000)  # expired
        result = _cache_get(key)
        assert result is None

    def test_cache_put_multiple_keys(self):
        _cache_put(("c1", 1, 0, 0), b"tile1")
        _cache_put(("c2", 2, 0, 0), b"tile2")
        assert _cache_get(("c1", 1, 0, 0)) == b"tile1"
        assert _cache_get(("c2", 2, 0, 0)) == b"tile2"

    def test_cache_eviction_over_10k(self):
        """Cache should evict oldest entries when over 10 000 tiles."""
        # Fill cache with 10 001 entries (old timestamps)
        base_ts = time.time() - 10000  # old entries
        for i in range(10001):
            _tile_cache[(str(i), 0, 0, 0)] = (b"data", base_ts + i)

        # Adding one more triggers eviction
        _cache_put(("new", 0, 0, 0), b"new_tile")

        # Total should be less than 10 001 + 1
        assert len(_tile_cache) <= 10001


# ---------------------------------------------------------------------------
# Tests — HTTP endpoint (with TestClient)
# ---------------------------------------------------------------------------


class TestTilesEndpointValidation:
    """Test the tiles endpoint validation (invalid coords, missing collection)."""

    def setup_method(self):
        _tile_cache.clear()
        os.environ["GISPULSE_STORAGE"] = "memory"

    def _make_client_with_mock_engine(self, backend_name="postgis"):
        """Build a TestClient that has the tiles router mounted.

        We force GISPULSE_ENGINE=postgis so create_app mounts the tiles router,
        then override app.state.spatial_engine with our mock.
        """
        import os
        from gispulse.adapters.http.app import create_app
        from gispulse.persistence.engine import SpatialEngine

        mock_engine = MagicMock(spec=SpatialEngine)
        mock_engine.backend_name = backend_name
        mock_engine.is_persistent = backend_name == "postgis"

        _orig = os.environ.get("GISPULSE_ENGINE")
        os.environ["GISPULSE_ENGINE"] = "postgis"
        try:
            with patch("gispulse.adapters.http.app.create_spatial_engine", return_value=mock_engine):
                app = create_app(mode="full")
        finally:
            if _orig is None:
                os.environ.pop("GISPULSE_ENGINE", None)
            else:
                os.environ["GISPULSE_ENGINE"] = _orig

        app.state.spatial_engine = mock_engine
        return TestClient(app), mock_engine

    def test_invalid_zoom_returns_400(self):
        client, _ = self._make_client_with_mock_engine()
        ds_id = uuid4()
        resp = client.get(f"/tiles/{ds_id}/99/0/0.mvt")
        assert resp.status_code == 400

    def test_tile_out_of_range_returns_400(self):
        client, _ = self._make_client_with_mock_engine()
        ds_id = uuid4()
        # At zoom 0, only tile (0,0) exists
        resp = client.get(f"/tiles/{ds_id}/0/5/5.mvt")
        assert resp.status_code == 400

    def test_missing_collection_returns_404(self):
        client, mock_engine = self._make_client_with_mock_engine(backend_name="postgis")
        ds_id = uuid4()
        resp = client.get(f"/tiles/{ds_id}/5/10/10.mvt")
        assert resp.status_code == 404

    def _make_app_with_tiles(self, backend_name: str, execute_sql_return=None):
        """Build an app with tiles router mounted.

        Forces GISPULSE_ENGINE=postgis so tiles router is included by create_app,
        then patches app.state.spatial_engine with a mock.
        """
        import os
        from gispulse.adapters.http.app import create_app
        from gispulse.persistence.engine import SpatialEngine

        mock_engine = MagicMock(spec=SpatialEngine)
        mock_engine.backend_name = backend_name
        mock_engine.is_persistent = backend_name == "postgis"
        if execute_sql_return is not None:
            mock_engine.execute_sql.return_value = execute_sql_return

        _orig = os.environ.get("GISPULSE_ENGINE")
        os.environ["GISPULSE_ENGINE"] = "postgis"
        try:
            with patch("gispulse.adapters.http.app.create_spatial_engine", return_value=mock_engine):
                app = create_app(mode="full")
        finally:
            if _orig is None:
                os.environ.pop("GISPULSE_ENGINE", None)
            else:
                os.environ["GISPULSE_ENGINE"] = _orig

        app.state.spatial_engine = mock_engine
        return app, mock_engine

    def test_duckdb_backend_returns_501(self):
        """DuckDB backend cannot encode MVT — must return 501."""
        from gispulse.core.models import Dataset

        app, _ = self._make_app_with_tiles("duckdb")
        ds = Dataset(name="test", source_path="/data/test.gpkg")
        app.state.dataset_repo.save(ds)

        client = TestClient(app)
        resp = client.get(f"/tiles/{ds.id}/5/10/10.mvt")
        assert resp.status_code == 501

    def test_postgis_empty_tile_returns_204(self):
        """PostGIS backend returning no tile data → 204 No Content."""
        from gispulse.core.models import Dataset

        app, _ = self._make_app_with_tiles("postgis", execute_sql_return=[{"tile": None}])
        ds = Dataset(name="test_postgis", source_path="public.test_table")
        app.state.dataset_repo.save(ds)

        client = TestClient(app)
        resp = client.get(f"/tiles/{ds.id}/5/10/10.mvt")
        assert resp.status_code == 204

    def test_postgis_tile_returns_binary(self):
        """PostGIS backend returning tile bytes → 200 with MVT content-type."""
        from gispulse.core.models import Dataset

        app, _ = self._make_app_with_tiles(
            "postgis", execute_sql_return=[{"tile": b"\x1a\x05fake_tile"}]
        )
        ds = Dataset(name="test_tile", source_path="public.test_tile_table")
        app.state.dataset_repo.save(ds)

        client = TestClient(app)
        resp = client.get(f"/tiles/{ds.id}/5/10/10.mvt")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/vnd.mapbox-vector-tile"
        assert resp.content == b"\x1a\x05fake_tile"

    def test_tile_served_from_cache(self):
        """Second request for same tile uses cache (execute_sql called once)."""
        from gispulse.core.models import Dataset

        _tile_cache.clear()

        app, mock_engine = self._make_app_with_tiles(
            "postgis", execute_sql_return=[{"tile": b"\x1a\x05cached"}]
        )
        ds = Dataset(name="cached_ds", source_path="public.cached")
        app.state.dataset_repo.save(ds)

        client = TestClient(app)
        client.get(f"/tiles/{ds.id}/5/10/10.mvt")
        client.get(f"/tiles/{ds.id}/5/10/10.mvt")

        # execute_sql should only have been called once (second served from cache)
        assert mock_engine.execute_sql.call_count == 1

"""
Tests for the tiles router (adapters/http/routers/tiles_router.py).

Complementary to test_mvt_router.py — focuses on error cases and
edge conditions for the GET /tiles/{collection_id}/{z}/{x}/{y}.mvt endpoint.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from gispulse.adapters.http.routers.tiles_router import _tile_cache


def _make_tiles_client(backend_name: str = "postgis") -> tuple[TestClient, MagicMock]:
    """Build a TestClient with the tiles router mounted.

    Forces GISPULSE_ENGINE=postgis so create_app mounts the tiles router,
    then overrides app.state.spatial_engine with a mock.
    """
    from gispulse.adapters.http.app import create_app
    from persistence.engine import SpatialEngine

    mock_engine = MagicMock(spec=SpatialEngine)
    mock_engine.backend_name = backend_name
    mock_engine.is_persistent = backend_name == "postgis"

    # Save originals so we restore exact prior state (caller may have left
    # GISPULSE_ENGINE unset, which a finally that hard-codes "duckdb" would
    # corrupt and leak across tests — was the source of the test_default_gpkg
    # full-suite failure on 2026-04-25).
    _orig_storage = os.environ.get("GISPULSE_STORAGE")
    _orig_engine = os.environ.get("GISPULSE_ENGINE")
    os.environ["GISPULSE_STORAGE"] = "memory"
    os.environ["GISPULSE_ENGINE"] = "postgis"
    try:
        with patch("gispulse.adapters.http.app.create_spatial_engine", return_value=mock_engine):
            app = create_app(mode="full")
    finally:
        if _orig_storage is None:
            os.environ.pop("GISPULSE_STORAGE", None)
        else:
            os.environ["GISPULSE_STORAGE"] = _orig_storage
        if _orig_engine is None:
            os.environ.pop("GISPULSE_ENGINE", None)
        else:
            os.environ["GISPULSE_ENGINE"] = _orig_engine

    app.state.spatial_engine = mock_engine
    return TestClient(app), mock_engine


@pytest.fixture(autouse=True)
def _clean_cache():
    _tile_cache.clear()
    yield
    _tile_cache.clear()


# ---------------------------------------------------------------------------
# GET /tiles/{collection_id}/{z}/{x}/{y}.mvt — error cases
# ---------------------------------------------------------------------------


class TestTilesNotFound:
    def test_nonexistent_collection_returns_404(self):
        client, _ = _make_tiles_client()
        fake_id = uuid4()
        resp = client.get(f"/tiles/{fake_id}/0/0/0.mvt")
        assert resp.status_code == 404

    def test_404_contains_detail(self):
        client, _ = _make_tiles_client()
        fake_id = uuid4()
        body = client.get(f"/tiles/{fake_id}/0/0/0.mvt").json()
        assert "error" in body
        assert body["error"]["code"] == "NOT_FOUND"


class TestTilesInvalidCoords:
    def test_negative_zoom_returns_400(self):
        client, _ = _make_tiles_client()
        fake_id = uuid4()
        resp = client.get(f"/tiles/{fake_id}/-1/0/0.mvt")
        # FastAPI may return 422 for path validation or 400 from the handler
        assert resp.status_code in (400, 422)

    def test_zoom_too_high_returns_400(self):
        client, _ = _make_tiles_client()
        fake_id = uuid4()
        resp = client.get(f"/tiles/{fake_id}/99/0/0.mvt")
        assert resp.status_code == 400

    def test_x_out_of_range_returns_400(self):
        client, _ = _make_tiles_client()
        fake_id = uuid4()
        # At zoom 0, only x=0 y=0 is valid
        resp = client.get(f"/tiles/{fake_id}/0/5/0.mvt")
        assert resp.status_code == 400

    def test_y_out_of_range_returns_400(self):
        client, _ = _make_tiles_client()
        fake_id = uuid4()
        resp = client.get(f"/tiles/{fake_id}/0/0/5.mvt")
        assert resp.status_code == 400

    def test_invalid_uuid_returns_422(self):
        client, _ = _make_tiles_client()
        resp = client.get("/tiles/not-a-uuid/0/0/0.mvt")
        assert resp.status_code == 422


class TestTilesDuckdbFallback:
    def test_duckdb_backend_returns_501(self):
        """DuckDB backend cannot encode MVT, must return 501."""
        from core.models import Dataset

        client, mock_engine = _make_tiles_client("duckdb")
        ds = Dataset(name="test_duckdb", source_path="/tmp/test.gpkg")
        app = client.app
        app.state.dataset_repo.save(ds)

        resp = client.get(f"/tiles/{ds.id}/5/10/10.mvt")
        assert resp.status_code == 501


class TestTilesPostgisPath:
    def test_empty_tile_returns_204(self):
        from core.models import Dataset

        client, mock_engine = _make_tiles_client("postgis")
        mock_engine.execute_sql.return_value = [{"tile": None}]

        ds = Dataset(name="empty_tile", source_path="public.empty_table")
        client.app.state.dataset_repo.save(ds)

        resp = client.get(f"/tiles/{ds.id}/5/10/10.mvt")
        assert resp.status_code == 204

    def test_valid_tile_returns_200_with_mvt_content_type(self):
        from core.models import Dataset

        client, mock_engine = _make_tiles_client("postgis")
        mock_engine.execute_sql.return_value = [{"tile": b"\x1a\x03mvt"}]

        ds = Dataset(name="good_tile", source_path="public.good_table")
        client.app.state.dataset_repo.save(ds)

        resp = client.get(f"/tiles/{ds.id}/5/10/10.mvt")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/vnd.mapbox-vector-tile"
        assert resp.content == b"\x1a\x03mvt"

    def test_cache_control_header_present(self):
        from core.models import Dataset

        client, mock_engine = _make_tiles_client("postgis")
        mock_engine.execute_sql.return_value = [{"tile": b"\x1a\x03mvt"}]

        ds = Dataset(name="cached", source_path="public.cached")
        client.app.state.dataset_repo.save(ds)

        resp = client.get(f"/tiles/{ds.id}/5/10/10.mvt")
        assert "cache-control" in resp.headers

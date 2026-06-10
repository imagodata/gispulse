"""Tests for the public /public/maps/* surface — read-only published maps (#406)."""

from __future__ import annotations

import os
from uuid import uuid4

import geopandas as gpd
import pytest
from fastapi.testclient import TestClient
from shapely.geometry import Point

from gispulse.adapters.http.app import create_app


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("GISPULSE_STORAGE", "memory")
    from gispulse.adapters.http.rate_limit import limiter

    limiter.enabled = False


@pytest.fixture()
def ctx():
    """App + client with one dataset preloaded in the layer cache."""
    os.environ["GISPULSE_STORAGE"] = "memory"
    app = create_app()
    from unittest.mock import MagicMock

    app.state.spatial_engine = MagicMock()

    dataset_id = str(uuid4())
    gdf = gpd.GeoDataFrame(
        {"name": ["a", "b", "c"]},
        geometry=[Point(2.35, 48.85), Point(2.36, 48.86), Point(10, 10)],
        crs="EPSG:4326",
    )
    app.state.layer_cache = {dataset_id: {"pts": gdf}}
    return TestClient(app), dataset_id


def _publish(client: TestClient, dataset_id: str, layer: str = "pts") -> str:
    payload = {
        "name": "Carte publique",
        "layers": [{"dataset_id": dataset_id, "layer": layer, "visible": True}],
        "styles": {"pts": {"renderer": "single", "color": "#00f"}},
        "view": {"center": [2.35, 48.85], "zoom": 11},
        "basemap": "osm",
    }
    mid = client.post("/maps", json=payload).json()["id"]
    return client.post(f"/maps/{mid}/publish").json()["token"]


class TestPublicMapSnapshot:
    def test_get_snapshot(self, ctx) -> None:
        client, ds = ctx
        token = _publish(client, ds)
        resp = client.get(f"/public/maps/{token}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["name"] == "Carte publique"
        assert body["layers"][0]["layer"] == "pts"
        assert body["styles"]["pts"]["renderer"] == "single"
        assert body["view"]["zoom"] == 11

    def test_unknown_token_404(self, ctx) -> None:
        client, _ = ctx
        assert client.get("/public/maps/pub_doesnotexist").status_code == 404


class TestPublicMapFeatures:
    def test_serves_published_layer(self, ctx) -> None:
        client, ds = ctx
        token = _publish(client, ds)
        resp = client.get(f"/public/maps/{token}/layers/pts/features")
        assert resp.status_code == 200
        body = resp.json()
        assert body["type"] == "FeatureCollection"
        assert body["total_count"] == 3
        assert len(body["features"]) == 3

    def test_bbox_filter(self, ctx) -> None:
        client, ds = ctx
        token = _publish(client, ds)
        # bbox around Paris excludes the (10,10) point
        resp = client.get(
            f"/public/maps/{token}/layers/pts/features?bbox=2.0,48.0,3.0,49.0"
        )
        assert resp.status_code == 200
        assert resp.json()["total_count"] == 2

    def test_invalid_bbox_400(self, ctx) -> None:
        client, ds = ctx
        token = _publish(client, ds)
        resp = client.get(f"/public/maps/{token}/layers/pts/features?bbox=1,2,3")
        assert resp.status_code == 400

    def test_layer_not_in_allowlist_404(self, ctx) -> None:
        client, ds = ctx
        token = _publish(client, ds)
        # 'secret' is not part of the published snapshot
        resp = client.get(f"/public/maps/{token}/layers/secret/features")
        assert resp.status_code == 404

    def test_unknown_token_404(self, ctx) -> None:
        client, _ = ctx
        assert (
            client.get("/public/maps/pub_x/layers/pts/features").status_code == 404
        )


class TestPublicReadOnly:
    """The public surface is GET-only: any mutation is 405 by construction."""

    def test_post_405(self, ctx) -> None:
        client, ds = ctx
        token = _publish(client, ds)
        assert client.post(f"/public/maps/{token}").status_code == 405

    def test_put_405(self, ctx) -> None:
        client, ds = ctx
        token = _publish(client, ds)
        assert client.put(f"/public/maps/{token}", json={}).status_code == 405

    def test_delete_405(self, ctx) -> None:
        client, ds = ctx
        token = _publish(client, ds)
        assert client.delete(f"/public/maps/{token}").status_code == 405

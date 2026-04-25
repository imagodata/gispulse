"""
Unit tests for the Datasets router — list, get, upload validation, SSRF protection.
"""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from gispulse.adapters.http.app import create_app
from core.models import Dataset


@pytest.fixture(autouse=True)
def _use_memory_storage(monkeypatch):
    monkeypatch.setenv("GISPULSE_STORAGE", "memory")


@pytest.fixture()
def client() -> TestClient:
    os.environ["GISPULSE_STORAGE"] = "memory"
    app = create_app()
    return TestClient(app)


@pytest.fixture()
def client_with_dataset() -> TestClient:
    os.environ["GISPULSE_STORAGE"] = "memory"
    app = create_app()
    ds = Dataset(name="parcels", source_path="/data/parcels.gpkg", crs="EPSG:4326", format="gpkg")
    app.state.dataset_repo.save(ds)
    return TestClient(app)


class TestListDatasets:
    def test_list_empty(self, client: TestClient) -> None:
        r = client.get("/datasets")
        assert r.status_code == 200
        body = r.json()
        assert body["items"] == []
        assert body["total"] == 0

    def test_list_with_dataset(self, client_with_dataset: TestClient) -> None:
        r = client_with_dataset.get("/datasets")
        body = r.json()
        assert body["total"] == 1
        assert body["items"][0]["name"] == "parcels"

    def test_list_pagination(self, client: TestClient) -> None:
        app = client.app
        for i in range(5):
            ds = Dataset(name=f"ds_{i}", source_path=f"/data/ds_{i}.gpkg")
            app.state.dataset_repo.save(ds)
        r = client.get("/datasets?limit=2&offset=0")
        body = r.json()
        assert len(body["items"]) == 2
        assert body["total"] == 5


class TestGetDataset:
    def test_get_existing(self, client_with_dataset: TestClient) -> None:
        items = client_with_dataset.get("/datasets").json()["items"]
        ds_id = items[0]["id"]
        r = client_with_dataset.get(f"/datasets/{ds_id}")
        assert r.status_code == 200
        assert r.json()["name"] == "parcels"

    def test_get_nonexistent_returns_404(self, client: TestClient) -> None:
        r = client.get(f"/datasets/{uuid4()}")
        assert r.status_code == 404


class TestUploadValidation:
    def test_upload_no_file_returns_422(self, client: TestClient) -> None:
        r = client.post("/datasets/upload")
        assert r.status_code == 422

    def test_upload_unsupported_format(self, client: TestClient) -> None:
        r = client.post(
            "/datasets/upload",
            files={"file": ("test.xyz", b"invalid data", "application/octet-stream")},
        )
        # May be 400 (explicit check) or 422 (Pydantic validation)
        assert r.status_code in (400, 422)


class TestSSRFProtection:
    """Verify OGC dataset registration blocks private/internal URLs."""

    _OGC_BASE = {
        "source_type": "wfs",
        "layer_name": "test:layer",
    }

    def _assert_blocked(self, r) -> None:
        assert r.status_code == 400
        body = r.json()
        # Error envelope: {"error": {"message": "..."}} or {"detail": "..."}
        msg = body.get("detail", "") or body.get("error", {}).get("message", "")
        assert "not allowed" in msg.lower()

    def test_blocks_localhost(self, client: TestClient) -> None:
        r = client.post(
            "/datasets/ogc",
            json={**self._OGC_BASE, "name": "evil", "url": "http://localhost:8080/wfs"},
        )
        self._assert_blocked(r)

    def test_blocks_private_ip(self, client: TestClient) -> None:
        r = client.post(
            "/datasets/ogc",
            json={**self._OGC_BASE, "name": "evil", "url": "http://192.168.1.1/wfs"},
        )
        self._assert_blocked(r)

    def test_blocks_loopback(self, client: TestClient) -> None:
        r = client.post(
            "/datasets/ogc",
            json={**self._OGC_BASE, "name": "evil", "url": "http://127.0.0.1:9999/wfs"},
        )
        self._assert_blocked(r)

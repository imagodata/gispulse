"""
Unit tests for the three HTTP routers that had no coverage:
  - portal_router  (/api/portal/...)
  - projects_router (/projects/...)
  - ws_router       (/ws/events)

Each test class uses a fresh app instance for full isolation.
Storage is forced to in-memory (GISPULSE_STORAGE=memory) to avoid
any SQLite or filesystem side-effects.
"""

from __future__ import annotations

import io
import json
import os
import uuid

import pytest
from fastapi.testclient import TestClient

from gispulse.adapters.http.app import create_app
from gispulse.adapters.http.portal_app import create_portal_app


# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _use_memory_storage(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force in-memory repositories for every test in this module."""
    monkeypatch.setenv("GISPULSE_STORAGE", "memory")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _PortalClientContext:
    """Context manager that triggers the portal app lifespan.

    The portal app initialises ``app.state.layer_cache`` inside an
    asynccontextmanager lifespan.  Starlette's TestClient only runs the
    lifespan when used as a context manager (``with TestClient(app)``).
    """

    def __init__(self, tmp_path) -> None:
        os.environ["GISPULSE_STORAGE"] = "memory"
        self._app = create_portal_app(data_dir=str(tmp_path))
        self._tc = TestClient(self._app, raise_server_exceptions=True)

    def __enter__(self) -> TestClient:
        self._tc.__enter__()
        return self._tc

    def __exit__(self, *args) -> None:
        self._tc.__exit__(*args)


class _EngineClientContext:
    """Context manager that triggers the main engine app lifespan.

    ``create_app()`` initialises ``app.state.spatial_engine`` inside an
    asynccontextmanager lifespan, so the TestClient must be used as a
    context manager to avoid AttributeError on ``spatial_engine``.
    """

    def __init__(self) -> None:
        os.environ["GISPULSE_STORAGE"] = "memory"
        self._app = create_app()
        self._tc = TestClient(self._app, raise_server_exceptions=True)

    def __enter__(self) -> TestClient:
        self._tc.__enter__()
        return self._tc

    def __exit__(self, *args) -> None:
        self._tc.__exit__(*args)


def _engine_client() -> TestClient:
    """Return a plain TestClient for tests that do NOT hit spatial_engine."""
    os.environ["GISPULSE_STORAGE"] = "memory"
    return TestClient(create_app())


def _minimal_geojson_bytes() -> bytes:
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [2.3522, 48.8566]},
                "properties": {"name": "Paris"},
            }
        ],
    }
    return json.dumps(geojson).encode()


# ===========================================================================
# portal_router -- /api/portal/...
# ===========================================================================


class TestPortalListDatasets:
    def test_list_datasets_empty_on_fresh_app(self, tmp_path) -> None:
        with _PortalClientContext(tmp_path) as client:
            response = client.get("/api/portal/datasets")
            assert response.status_code == 200
            assert response.json() == []

    def test_list_datasets_returns_list_type(self, tmp_path) -> None:
        with _PortalClientContext(tmp_path) as client:
            response = client.get("/api/portal/datasets")
            assert isinstance(response.json(), list)


class TestPortalUploadDataset:
    def test_upload_geojson_returns_201(self, tmp_path) -> None:
        with _PortalClientContext(tmp_path) as client:
            response = client.post(
                "/api/portal/datasets/upload",
                files={"file": ("test.geojson", io.BytesIO(_minimal_geojson_bytes()), "application/json")},
            )
            assert response.status_code == 201

    def test_upload_returns_valid_uuid(self, tmp_path) -> None:
        with _PortalClientContext(tmp_path) as client:
            response = client.post(
                "/api/portal/datasets/upload",
                files={"file": ("test.geojson", io.BytesIO(_minimal_geojson_bytes()), "application/json")},
            )
            body = response.json()
            assert "id" in body
            uuid.UUID(body["id"])

    def test_upload_response_has_layers_key(self, tmp_path) -> None:
        with _PortalClientContext(tmp_path) as client:
            response = client.post(
                "/api/portal/datasets/upload",
                files={"file": ("test.geojson", io.BytesIO(_minimal_geojson_bytes()), "application/json")},
            )
            body = response.json()
            assert "layers" in body
            assert isinstance(body["layers"], list)

    def test_upload_response_name_contains_file_stem(self, tmp_path) -> None:
        with _PortalClientContext(tmp_path) as client:
            response = client.post(
                "/api/portal/datasets/upload",
                files={"file": ("myfile.geojson", io.BytesIO(_minimal_geojson_bytes()), "application/json")},
            )
            # The router prefixes the name with a UUID to avoid collisions;
            # the original stem must still be present in the stored name.
            assert "myfile" in response.json()["name"]

    def test_upload_unsupported_format_returns_400(self, tmp_path) -> None:
        with _PortalClientContext(tmp_path) as client:
            response = client.post(
                "/api/portal/datasets/upload",
                files={"file": ("report.pdf", io.BytesIO(b"not a spatial file"), "application/pdf")},
            )
            assert response.status_code == 400

    def test_uploaded_dataset_appears_in_list(self, tmp_path) -> None:
        with _PortalClientContext(tmp_path) as client:
            upload_resp = client.post(
                "/api/portal/datasets/upload",
                files={"file": ("parcels.geojson", io.BytesIO(_minimal_geojson_bytes()), "application/json")},
            )
            assert upload_resp.status_code == 201
            dataset_id = upload_resp.json()["id"]
            ids = [d["id"] for d in client.get("/api/portal/datasets").json()]
            assert dataset_id in ids


class TestPortalLayerFeatures:
    def _upload(self, client: TestClient) -> tuple[str, str]:
        resp = client.post(
            "/api/portal/datasets/upload",
            files={"file": ("places.geojson", io.BytesIO(_minimal_geojson_bytes()), "application/json")},
        )
        assert resp.status_code == 201
        body = resp.json()
        layer_name = body["layers"][0]["name"] if body["layers"] else "places"
        return body["id"], layer_name

    def test_features_returns_geojson_feature_collection(self, tmp_path) -> None:
        with _PortalClientContext(tmp_path) as client:
            dataset_id, layer_name = self._upload(client)
            response = client.get(
                f"/api/portal/datasets/{dataset_id}/layers/{layer_name}/features"
            )
            assert response.status_code == 200
            body = response.json()
            assert body["type"] == "FeatureCollection"
            assert "features" in body

    def test_features_unknown_dataset_returns_404(self, tmp_path) -> None:
        with _PortalClientContext(tmp_path) as client:
            response = client.get(
                f"/api/portal/datasets/{uuid.uuid4()}/layers/dummy/features"
            )
            assert response.status_code == 404

    def test_features_unknown_layer_returns_404(self, tmp_path) -> None:
        with _PortalClientContext(tmp_path) as client:
            dataset_id, _ = self._upload(client)
            response = client.get(
                f"/api/portal/datasets/{dataset_id}/layers/nonexistent/features"
            )
            assert response.status_code == 404

    def test_features_invalid_bbox_returns_400(self, tmp_path) -> None:
        with _PortalClientContext(tmp_path) as client:
            dataset_id, layer_name = self._upload(client)
            response = client.get(
                f"/api/portal/datasets/{dataset_id}/layers/{layer_name}/features",
                params={"bbox": "not,a,valid"},
            )
            assert response.status_code == 400

    def test_features_total_count_present(self, tmp_path) -> None:
        with _PortalClientContext(tmp_path) as client:
            dataset_id, layer_name = self._upload(client)
            response = client.get(
                f"/api/portal/datasets/{dataset_id}/layers/{layer_name}/features"
            )
            assert response.status_code == 200
            assert "total_count" in response.json()

    def test_features_limit_respected(self, tmp_path) -> None:
        with _PortalClientContext(tmp_path) as client:
            dataset_id, layer_name = self._upload(client)
            response = client.get(
                f"/api/portal/datasets/{dataset_id}/layers/{layer_name}/features",
                params={"limit": 1, "offset": 0},
            )
            assert response.status_code == 200
            assert len(response.json()["features"]) <= 1


class TestPortalCapabilities:
    def test_capabilities_returns_non_empty_list(self, tmp_path) -> None:
        with _PortalClientContext(tmp_path) as client:
            response = client.get("/api/portal/capabilities")
            assert response.status_code == 200
            body = response.json()
            assert isinstance(body, list)
            assert len(body) > 0

    def test_capabilities_items_have_name_field(self, tmp_path) -> None:
        with _PortalClientContext(tmp_path) as client:
            response = client.get("/api/portal/capabilities")
            for item in response.json():
                assert "name" in item


# ===========================================================================
# projects_router -- /projects/...
# ===========================================================================


class TestProjectsCRUD:
    @pytest.fixture()
    def client(self) -> TestClient:
        return _engine_client()

    def test_list_projects_empty_on_fresh_app(self, client: TestClient) -> None:
        response = client.get("/projects")
        assert response.status_code == 200
        data = response.json()
        assert data["items"] == []
        assert data["total"] == 0

    def test_create_project_returns_201(self, client: TestClient) -> None:
        response = client.post("/projects", json={"name": "my-project"})
        assert response.status_code == 201

    def test_create_project_response_has_valid_id(self, client: TestClient) -> None:
        body = client.post("/projects", json={"name": "proj"}).json()
        assert "id" in body
        uuid.UUID(body["id"])

    def test_create_project_preserves_name_and_description(self, client: TestClient) -> None:
        body = client.post(
            "/projects", json={"name": "named", "description": "a description"}
        ).json()
        assert body["name"] == "named"
        assert body["description"] == "a description"

    def test_create_project_default_fields(self, client: TestClient) -> None:
        body = client.post("/projects", json={"name": "defaults"}).json()
        assert body["schema_name"] == "public"
        assert body["engine_backend"] == "duckdb"
        assert body["datasets"] == []
        assert body["rules"] == []
        assert body["triggers"] == []

    def test_list_projects_includes_created(self, client: TestClient) -> None:
        client.post("/projects", json={"name": "proj-list-test"})
        names = [p["name"] for p in client.get("/projects").json()["items"]]
        assert "proj-list-test" in names

    def test_get_project_by_id_returns_200(self, client: TestClient) -> None:
        project_id = client.post("/projects", json={"name": "fetch-me"}).json()["id"]
        response = client.get(f"/projects/{project_id}")
        assert response.status_code == 200
        assert response.json()["id"] == project_id

    def test_get_nonexistent_project_returns_404(self, client: TestClient) -> None:
        response = client.get(f"/projects/{uuid.uuid4()}")
        assert response.status_code == 404
        assert "not found" in response.json()["error"]["message"].lower()

    def test_delete_project_returns_204(self, client: TestClient) -> None:
        project_id = client.post("/projects", json={"name": "delete-me"}).json()["id"]
        assert client.delete(f"/projects/{project_id}").status_code == 204

    def test_deleted_project_is_gone(self, client: TestClient) -> None:
        project_id = client.post("/projects", json={"name": "gone"}).json()["id"]
        client.delete(f"/projects/{project_id}")
        assert client.get(f"/projects/{project_id}").status_code == 404

    def test_delete_nonexistent_project_returns_404(self, client: TestClient) -> None:
        assert client.delete(f"/projects/{uuid.uuid4()}").status_code == 404

    def test_update_project(self, client: TestClient) -> None:
        project_id = client.post("/projects", json={"name": "original"}).json()["id"]
        response = client.put(
            f"/projects/{project_id}",
            json={"name": "updated", "description": "now updated"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["name"] == "updated"
        assert body["description"] == "now updated"

    def test_update_nonexistent_project_returns_404(self, client: TestClient) -> None:
        response = client.put(f"/projects/{uuid.uuid4()}", json={"name": "ghost"})
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Tier-gated multi-project limit (issue #456 — S0-1.1)
# ---------------------------------------------------------------------------


class TestProjectsTierLimitGate:
    """`POST /projects` enforces the `core/pricing_catalog.yml` `projects` limit:
    community=1, pro=5, team=∞, enterprise=∞ → HTTP 402 when exceeded.
    """

    def _make_client(self, monkeypatch: pytest.MonkeyPatch, tier: str) -> TestClient:
        from gispulse.adapters.http.rate_limit import limiter

        monkeypatch.setenv("GISPULSE_TIER", tier)
        if tier in ("pro", "team", "enterprise"):
            monkeypatch.setenv("GISPULSE_LICENCE_SKIP_VERIFY", "true")
        # Reset the process-wide slowapi storage so cascaded tests don't bleed.
        limiter.reset()
        return TestClient(create_app())

    def test_community_limit_is_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = self._make_client(monkeypatch, "community")
        assert client.post("/projects", json={"name": "p1"}).status_code == 201
        resp = client.post("/projects", json={"name": "p2"})
        assert resp.status_code == 402
        assert "community" in resp.json()["error"]["message"].lower()

    def test_pro_allows_five(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = self._make_client(monkeypatch, "pro")
        for i in range(5):
            assert client.post("/projects", json={"name": f"p{i}"}).status_code == 201
        resp = client.post("/projects", json={"name": "p6"})
        assert resp.status_code == 402
        assert "pro" in resp.json()["error"]["message"].lower()

    def test_team_unlimited(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = self._make_client(monkeypatch, "team")
        for i in range(7):  # > pro limit
            assert client.post("/projects", json={"name": f"p{i}"}).status_code == 201


class TestProjectDatasetAssociation:
    @pytest.fixture()
    def client(self) -> TestClient:
        return _engine_client()

    @pytest.fixture()
    def project_id(self, client: TestClient) -> str:
        return client.post("/projects", json={"name": "assoc-project"}).json()["id"]

    def test_add_dataset_returns_204(self, client: TestClient, project_id: str) -> None:
        assert client.post(
            f"/projects/{project_id}/datasets/{uuid.uuid4()}"
        ).status_code == 204

    def test_added_dataset_appears_in_project(
        self, client: TestClient, project_id: str
    ) -> None:
        dataset_id = str(uuid.uuid4())
        client.post(f"/projects/{project_id}/datasets/{dataset_id}")
        assert dataset_id in client.get(f"/projects/{project_id}").json()["datasets"]

    def test_remove_dataset_returns_204(
        self, client: TestClient, project_id: str
    ) -> None:
        dataset_id = str(uuid.uuid4())
        client.post(f"/projects/{project_id}/datasets/{dataset_id}")
        assert client.delete(
            f"/projects/{project_id}/datasets/{dataset_id}"
        ).status_code == 204

    def test_removed_dataset_absent_from_project(
        self, client: TestClient, project_id: str
    ) -> None:
        dataset_id = str(uuid.uuid4())
        client.post(f"/projects/{project_id}/datasets/{dataset_id}")
        client.delete(f"/projects/{project_id}/datasets/{dataset_id}")
        assert dataset_id not in client.get(f"/projects/{project_id}").json()["datasets"]

    def test_add_dataset_to_nonexistent_project_returns_404(
        self, client: TestClient
    ) -> None:
        assert client.post(
            f"/projects/{uuid.uuid4()}/datasets/{uuid.uuid4()}"
        ).status_code == 404

    def test_remove_dataset_from_nonexistent_project_returns_404(
        self, client: TestClient
    ) -> None:
        assert client.delete(
            f"/projects/{uuid.uuid4()}/datasets/{uuid.uuid4()}"
        ).status_code == 404


class TestProjectLayers:
    """GET /projects/{id}/layers requires the spatial engine (lifespan)."""

    def test_list_layers_returns_list(self) -> None:
        with _EngineClientContext() as client:
            project_id = client.post(
                "/projects", json={"name": "layers-project"}
            ).json()["id"]
            response = client.get(f"/projects/{project_id}/layers")
            assert response.status_code == 200
            assert isinstance(response.json(), list)

    def test_list_layers_nonexistent_project_returns_404(self) -> None:
        with _EngineClientContext() as client:
            assert client.get(f"/projects/{uuid.uuid4()}/layers").status_code == 404


# ===========================================================================
# ws_router -- /ws/events
# ===========================================================================


class TestWebSocketConnect:
    @pytest.fixture()
    def client(self) -> TestClient:
        return _engine_client()

    def test_websocket_connects_without_error(self, client: TestClient) -> None:
        """The WebSocket handshake must succeed."""
        with client.websocket_connect("/ws/events") as ws:
            assert ws is not None

    def test_websocket_receives_broadcast_event(self, client: TestClient) -> None:
        """A message broadcast by the hub must arrive at the connected client."""
        from gispulse.adapters.http.event_hub import get_event_hub

        hub = get_event_hub()
        with client.websocket_connect("/ws/events") as ws:
            hub.broadcast("layer_updated", {"table": "public.parcelles"})
            payload = json.loads(ws.receive_text())
            assert payload["type"] == "layer_updated"
            assert payload["data"]["table"] == "public.parcelles"
            assert "timestamp" in payload

    def test_websocket_event_has_required_keys(self, client: TestClient) -> None:
        """Every broadcast event must carry type, data and timestamp."""
        from gispulse.adapters.http.event_hub import get_event_hub

        hub = get_event_hub()
        with client.websocket_connect("/ws/events") as ws:
            hub.broadcast("job_completed", {"job_id": "abc-123"})
            payload = json.loads(ws.receive_text())
            assert "type" in payload
            assert "data" in payload
            assert "timestamp" in payload

    def test_websocket_multiple_events_delivered_in_order(
        self, client: TestClient
    ) -> None:
        """Multiple broadcasts must be received in the order they were sent."""
        from gispulse.adapters.http.event_hub import get_event_hub

        hub = get_event_hub()
        events = ["first_event", "second_event", "third_event"]
        with client.websocket_connect("/ws/events") as ws:
            for evt in events:
                hub.broadcast(evt)
            received = [json.loads(ws.receive_text())["type"] for _ in events]
        assert received == events

    def test_websocket_disconnect_removes_subscriber(self, client: TestClient) -> None:
        """After disconnect the hub must have one fewer subscriber than during the session."""
        from gispulse.adapters.http.event_hub import get_event_hub

        hub = get_event_hub()
        before = hub.subscriber_count
        with client.websocket_connect("/ws/events"):
            during = hub.subscriber_count
        after = hub.subscriber_count

        assert during == before + 1
        assert after == before

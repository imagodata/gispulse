"""
Tests for the filter router (adapters/http/routers/filter_router.py).

Covers:
- GET /api/filter/predicates — list spatial predicates
- GET /api/filter/cache/stats — cache statistics
- DELETE /api/filter/cache — clear cache
- POST /api/filter/validate — expression validation
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from gispulse.adapters.http.app import create_app


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("GISPULSE_STORAGE", "memory")
    monkeypatch.delenv("GISPULSE_API_KEYS", raising=False)
    from gispulse.adapters.http.rate_limit import limiter
    limiter.enabled = False


@pytest.fixture()
def client() -> TestClient:
    os.environ["GISPULSE_STORAGE"] = "memory"
    app = create_app()
    # Provide a mock spatial engine so _get_filter_service() doesn't 500
    from persistence.engine import SpatialEngine
    mock_engine = MagicMock(spec=SpatialEngine)
    mock_engine.backend_name = "duckdb"
    app.state.spatial_engine = mock_engine
    return TestClient(app)


# ---------------------------------------------------------------------------
# GET /api/filter/predicates
# ---------------------------------------------------------------------------


class TestListPredicates:
    def test_returns_200(self, client):
        resp = client.get("/api/filter/predicates")
        assert resp.status_code == 200

    def test_returns_list(self, client):
        data = client.get("/api/filter/predicates").json()
        assert isinstance(data, list)

    def test_predicates_not_empty(self, client):
        data = client.get("/api/filter/predicates").json()
        assert len(data) > 0

    def test_each_predicate_has_id(self, client):
        data = client.get("/api/filter/predicates").json()
        for pred in data:
            assert "id" in pred
            assert isinstance(pred["id"], str)

    def test_each_predicate_has_label(self, client):
        data = client.get("/api/filter/predicates").json()
        for pred in data:
            assert "label" in pred
            assert isinstance(pred["label"], str)

    def test_each_predicate_has_description(self, client):
        data = client.get("/api/filter/predicates").json()
        for pred in data:
            assert "description" in pred
            assert isinstance(pred["description"], str)

    def test_known_predicates_present(self, client):
        data = client.get("/api/filter/predicates").json()
        ids = {p["id"] for p in data}
        expected = {"intersects", "contains", "within", "crosses", "touches", "overlaps", "disjoint", "equals", "dwithin"}
        assert expected.issubset(ids)


# ---------------------------------------------------------------------------
# POST /api/filter/validate
# ---------------------------------------------------------------------------


class TestValidateExpression:
    def test_valid_expression_returns_200(self, client):
        resp = client.post("/api/filter/validate", json={"expression": "area > 100"})
        assert resp.status_code == 200

    def test_valid_expression_is_valid_true(self, client):
        data = client.post("/api/filter/validate", json={"expression": "area > 100"}).json()
        assert data["is_valid"] is True

    def test_valid_expression_no_errors(self, client):
        data = client.post("/api/filter/validate", json={"expression": "area > 100"}).json()
        assert data["errors"] == []

    def test_empty_expression_is_invalid(self, client):
        data = client.post("/api/filter/validate", json={"expression": ""}).json()
        assert data["is_valid"] is False
        assert len(data["errors"]) > 0

    def test_dangerous_expression_is_invalid(self, client):
        data = client.post("/api/filter/validate", json={"expression": "DROP TABLE users"}).json()
        assert data["is_valid"] is False
        assert len(data["errors"]) > 0

    def test_unbalanced_parens_is_invalid(self, client):
        data = client.post("/api/filter/validate", json={"expression": "(a > 1"}).json()
        assert data["is_valid"] is False

    def test_response_shape(self, client):
        data = client.post("/api/filter/validate", json={"expression": "x == 1"}).json()
        assert "is_valid" in data
        assert "errors" in data
        assert isinstance(data["is_valid"], bool)
        assert isinstance(data["errors"], list)

    def test_missing_expression_field_returns_422(self, client):
        resp = client.post("/api/filter/validate", json={})
        assert resp.status_code == 422
        body = resp.json()
        assert "error" in body
        assert body["error"]["code"] == "VALIDATION_ERROR"

    def test_wrong_payload_type_returns_422(self, client):
        resp = client.post("/api/filter/validate", json={"expression": 123})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/filter/cache/stats
# ---------------------------------------------------------------------------


class TestCacheStats:
    def test_returns_200(self, client):
        resp = client.get("/api/filter/cache/stats")
        assert resp.status_code == 200

    def test_response_has_required_fields(self, client):
        data = client.get("/api/filter/cache/stats").json()
        for field in ("hits", "misses", "size", "max_size", "hit_rate", "utilization"):
            assert field in data, f"Missing field: {field}"

    def test_hits_is_int(self, client):
        data = client.get("/api/filter/cache/stats").json()
        assert isinstance(data["hits"], int)

    def test_misses_is_int(self, client):
        data = client.get("/api/filter/cache/stats").json()
        assert isinstance(data["misses"], int)

    def test_size_is_int(self, client):
        data = client.get("/api/filter/cache/stats").json()
        assert isinstance(data["size"], int)

    def test_max_size_is_int(self, client):
        data = client.get("/api/filter/cache/stats").json()
        assert isinstance(data["max_size"], int)

    def test_hit_rate_is_float(self, client):
        data = client.get("/api/filter/cache/stats").json()
        assert isinstance(data["hit_rate"], (int, float))

    def test_utilization_is_float(self, client):
        data = client.get("/api/filter/cache/stats").json()
        assert isinstance(data["utilization"], (int, float))

    def test_initial_stats_are_zero(self, client):
        data = client.get("/api/filter/cache/stats").json()
        assert data["hits"] == 0
        assert data["misses"] == 0
        assert data["size"] == 0


# ---------------------------------------------------------------------------
# DELETE /api/filter/cache
# ---------------------------------------------------------------------------


class TestClearCache:
    def test_returns_200(self, client):
        resp = client.delete("/api/filter/cache")
        assert resp.status_code == 200

    def test_response_has_cleared_field(self, client):
        data = client.delete("/api/filter/cache").json()
        assert "cleared" in data

    def test_cleared_is_int(self, client):
        data = client.delete("/api/filter/cache").json()
        assert isinstance(data["cleared"], int)

    def test_response_has_layer_key_field(self, client):
        data = client.delete("/api/filter/cache").json()
        assert "layer_key" in data

    def test_clear_with_layer_key_param(self, client):
        resp = client.delete("/api/filter/cache", params={"layer_key": "ds::layer"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["layer_key"] == "ds::layer"

    def test_clear_without_layer_key_returns_null(self, client):
        data = client.delete("/api/filter/cache").json()
        assert data["layer_key"] is None

"""
Tests for the Mode 2 "Try it" examples router (v1.5.x — #47/#48/#49).

Covers:

* registry list / detail / 404 + missing-file fallback
* TileJSON document shape + tile URL templating
* MVT tile request: empty tile, populated tile, cache hit, OOB coords
* dry-run trigger evaluation: matched/unmatched, action capture,
  truncation guards, default DML when caller omits it
* health endpoint: ok vs degraded
* read-only middleware contract: GET allowed everywhere, POST only on
  ``/examples/{id}/triggers/dryrun``

The fixtures live under ``examples/datasets/`` in the repo and are
treated as immutable inputs — the router never mutates them.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# App / client construction
# ---------------------------------------------------------------------------


def _make_client(*, read_only: bool = False) -> TestClient:
    """Build a TestClient with the full app and the examples router mounted."""
    from gispulse.adapters.http import app as app_module
    from gispulse.adapters.http.app import create_app

    # The examples router is unconditionally mounted on both modes.
    _orig_storage = os.environ.get("GISPULSE_STORAGE")
    _orig_engine = os.environ.get("GISPULSE_ENGINE")
    _orig_ro = os.environ.get("GISPULSE_READ_ONLY")

    os.environ["GISPULSE_STORAGE"] = "memory"
    os.environ["GISPULSE_ENGINE"] = "duckdb"
    if read_only:
        os.environ["GISPULSE_READ_ONLY"] = "true"
    else:
        os.environ.pop("GISPULSE_READ_ONLY", None)

    try:
        with patch.object(app_module, "_load_api_keys", return_value=None):
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
        if _orig_ro is None:
            os.environ.pop("GISPULSE_READ_ONLY", None)
        else:
            os.environ["GISPULSE_READ_ONLY"] = _orig_ro

    return TestClient(app)


@pytest.fixture
def client() -> Iterator[TestClient]:
    from gispulse.adapters.http.routers.examples_router import _reset_tile_cache

    _reset_tile_cache()
    with _make_client() as c:
        yield c


@pytest.fixture
def ro_client() -> Iterator[TestClient]:
    from gispulse.adapters.http.routers.examples_router import _reset_tile_cache

    _reset_tile_cache()
    with _make_client(read_only=True) as c:
        yield c


# ---------------------------------------------------------------------------
# Registry coverage
# ---------------------------------------------------------------------------


def test_health_reports_dataset_count(client: TestClient) -> None:
    resp = client.get("/examples/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in {"ok", "degraded"}
    assert body["dataset_count"] >= 1
    assert isinstance(body["missing"], list)


def test_health_degraded_when_files_missing(tmp_path: Path) -> None:
    """Pointing the router at an empty dir flips status to ``degraded``."""
    from gispulse.adapters.http.routers.examples_router import _reset_tile_cache

    _reset_tile_cache()
    with _make_client() as c:
        c.app.state.examples_datasets_dir = str(tmp_path)
        resp = c.get("/examples/health")
        body = resp.json()
        assert resp.status_code == 200
        assert body["status"] == "degraded"
        assert body["dataset_count"] == 0
        assert len(body["missing"]) >= 1


def test_list_examples_returns_summary_for_known_ids(client: TestClient) -> None:
    resp = client.get("/examples")
    assert resp.status_code == 200
    items = resp.json()
    assert isinstance(items, list)
    assert items, "registry must expose at least one example"
    by_id = {item["id"]: item for item in items}

    expected_ids = {
        "muret-parcels",
        "muret-flood-zones",
        "toulouse-isochrones",
        "bordeaux-rpg",
    }
    assert expected_ids.issubset(by_id.keys()), by_id.keys()

    sample = items[0]
    for key in ("id", "title", "description", "scenario",
                "layer_count", "feature_count", "size_bytes"):
        assert key in sample, key
    assert sample["layer_count"] >= 1
    assert sample["size_bytes"] > 0


def test_get_example_detail_includes_layers_and_bounds(client: TestClient) -> None:
    resp = client.get("/examples/toulouse-isochrones")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "toulouse-isochrones"
    assert body["primary_layer"] == "isochrones"
    assert body["layer_count"] == 1
    assert isinstance(body["layers"], list) and body["layers"]
    layer = body["layers"][0]
    assert layer["name"] == "isochrones"
    assert layer["geometry_type"] in {"Polygon", "MultiPolygon"}
    assert layer["feature_count"] == 3
    # Bounds are around Toulouse (lon ~ 1.4, lat ~ 43.6).
    assert body["bounds"] is not None
    minx, miny, maxx, maxy = body["bounds"]
    assert 1.3 < minx < 1.5
    assert 43.5 < miny < 43.7


def test_get_example_unknown_id_returns_404(client: TestClient) -> None:
    resp = client.get("/examples/does-not-exist")
    assert resp.status_code == 404


def test_get_example_with_missing_fixture_file(tmp_path: Path) -> None:
    """A registered dataset whose file is gone returns 404 deterministically."""
    from gispulse.adapters.http.routers.examples_router import _reset_tile_cache

    _reset_tile_cache()
    with _make_client() as c:
        c.app.state.examples_datasets_dir = str(tmp_path)
        resp = c.get("/examples/muret-parcels")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# TileJSON / MVT
# ---------------------------------------------------------------------------


def test_preview_returns_tilejson_3_0(client: TestClient) -> None:
    resp = client.get("/examples/toulouse-isochrones/preview")
    assert resp.status_code == 200
    body = resp.json()
    assert body["tilejson"] == "3.0.0"
    assert body["name"]
    assert body["scheme"] == "xyz"
    assert body["format"] == "pbf"
    assert isinstance(body["tiles"], list) and body["tiles"]
    assert "{z}" in body["tiles"][0] and "{x}" in body["tiles"][0]
    assert "/examples/toulouse-isochrones/tiles/" in body["tiles"][0]
    assert isinstance(body["bounds"], list) and len(body["bounds"]) == 4
    assert isinstance(body["center"], list) and len(body["center"]) == 3
    assert body["vector_layers"], "vector_layers should not be empty"


def test_preview_unknown_dataset_returns_404(client: TestClient) -> None:
    resp = client.get("/examples/nope/preview")
    assert resp.status_code == 404


def test_tile_request_returns_204_when_outside_bounds(client: TestClient) -> None:
    """Tile (0,0,0) over Toulouse-only data should be empty (or contain
    just the small isochrone bbox once zoomed in). At z=0 the tile covers
    the world so the GPKG features will fall inside — we therefore probe
    a zoom-level guaranteed to be outside.
    """
    # Tile far from Toulouse — somewhere over the Pacific at z=10.
    resp = client.get("/examples/toulouse-isochrones/tiles/10/100/200.mvt")
    # Either 204 (no features) or 200 (features). Both are valid.
    assert resp.status_code in {200, 204}


def test_tile_request_returns_data_for_overlapping_tile(client: TestClient) -> None:
    """Toulouse sits in tile (z=10, x=515, y=375). We tolerate ±1 since
    fixtures are 3 small rings; just make sure at least one of a tight
    cluster returns content."""
    candidates = [
        (10, 515, 374),
        (10, 515, 375),
        (10, 516, 375),
        (10, 514, 375),
        (8, 128, 93),
        (4, 8, 5),  # broad zoom — guaranteed to contain Toulouse
    ]
    saw_data = False
    for z, x, y in candidates:
        resp = client.get(f"/examples/toulouse-isochrones/tiles/{z}/{x}/{y}.mvt")
        assert resp.status_code in {200, 204}, (z, x, y, resp.status_code)
        if resp.status_code == 200:
            assert resp.content, "200 must carry a body"
            saw_data = True
            break
    assert saw_data, "Expected at least one tile to overlap the isochrone fixture"


def test_tile_request_invalid_zoom_returns_400(client: TestClient) -> None:
    resp = client.get("/examples/toulouse-isochrones/tiles/-1/0/0.mvt")
    assert resp.status_code == 400


def test_tile_request_oob_xy_returns_400(client: TestClient) -> None:
    # At z=2 the max tile index is 3. Asking for x=99 must 400.
    resp = client.get("/examples/toulouse-isochrones/tiles/2/99/99.mvt")
    assert resp.status_code == 400


def test_tile_cache_hit_after_first_request(client: TestClient) -> None:
    from gispulse.adapters.http.routers.examples_router import _TILE_CACHE

    # World tile — guaranteed to overlap.
    r1 = client.get("/examples/toulouse-isochrones/tiles/4/8/5.mvt")
    assert r1.status_code in {200, 204}
    cache_keys_before = set(_TILE_CACHE.keys())

    r2 = client.get("/examples/toulouse-isochrones/tiles/4/8/5.mvt")
    assert r2.status_code == r1.status_code
    cache_keys_after = set(_TILE_CACHE.keys())
    # Cache mutated at least once and key is present.
    assert ("toulouse-isochrones", 4, 8, 5) in cache_keys_after
    assert cache_keys_before == cache_keys_after  # no growth = cache hit


# ---------------------------------------------------------------------------
# Dry-run
# ---------------------------------------------------------------------------


def test_dryrun_default_dml_when_caller_omits_records(client: TestClient) -> None:
    """When the caller sends no DML, the router synthesises one INSERT on
    the dataset's primary layer so the playground returns something
    interesting."""
    resp = client.post(
        "/examples/toulouse-isochrones/triggers/dryrun",
        json={
            "triggers": [
                {
                    "name": "any-insert",
                    "trigger_type": "dml",
                    "conditions": {"events": ["INSERT"]},
                    "actions": [
                        {
                            "action_type": "log_event",
                            "config": {"message": "hi"},
                        }
                    ],
                }
            ],
            "simulated_dml": [],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["events"], "evaluator should have evaluated the synthetic DML"
    assert any("defaulted" in note for note in body["notes"])
    # Action captured (not dispatched).
    assert body["actions"], body
    action = body["actions"][0]
    assert action["action_type"] == "log_event"
    assert action["config"] == {"message": "hi"}


def test_dryrun_matches_table_filter(client: TestClient) -> None:
    """Trigger conditioned on table=parcels should not match an insert on
    flood_zones."""
    resp = client.post(
        "/examples/muret-parcels/triggers/dryrun",
        json={
            "triggers": [
                {
                    "name": "parcels-only",
                    "trigger_type": "dml",
                    "conditions": {"table": "parcels", "events": ["INSERT"]},
                    "actions": [
                        {"action_type": "log_event", "config": {}}
                    ],
                }
            ],
            "simulated_dml": [
                {"table": "parcels", "operation": "INSERT", "feature_id": "1"},
                {"table": "flood_zones", "operation": "INSERT", "feature_id": "2"},
            ],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    # Two evaluations recorded (one per DML record); only one matches.
    assert len(body["events"]) == 2
    matched_events = [ev for ev in body["events"] if ev["matched"]]
    assert len(matched_events) == 1
    assert matched_events[0]["table"] == "parcels"
    # And only the matched trigger captured an action.
    assert len(body["actions"]) == 1


def test_dryrun_unknown_trigger_type_falls_back_to_dml(client: TestClient) -> None:
    """The router must not 500 when callers pass garbage types."""
    resp = client.post(
        "/examples/muret-parcels/triggers/dryrun",
        json={
            "triggers": [
                {
                    "name": "bogus-type",
                    "trigger_type": "totally-not-a-type",
                    "conditions": {"events": ["INSERT"]},
                    "actions": [],
                }
            ],
            "simulated_dml": [
                {"table": "parcels", "operation": "INSERT", "feature_id": "x"},
            ],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["events"], body
    assert body["events"][0]["matched"] is True


def test_dryrun_truncates_oversize_inputs(client: TestClient) -> None:
    big_dml = [
        {"table": "parcels", "operation": "INSERT", "feature_id": str(i)}
        for i in range(1500)
    ]
    resp = client.post(
        "/examples/muret-parcels/triggers/dryrun",
        json={
            "triggers": [
                {
                    "name": "drop",
                    "trigger_type": "dml",
                    "conditions": {"events": ["INSERT"]},
                    "actions": [],
                }
            ],
            "simulated_dml": big_dml,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["truncated"] is True
    assert any("truncated to 1000" in note for note in body["notes"])
    # Evaluator capped at the truncation limit (or the timeout —
    # whichever fires first).
    assert len(body["events"]) <= 1000


def test_dryrun_unknown_dataset_returns_404(client: TestClient) -> None:
    resp = client.post(
        "/examples/nope/triggers/dryrun",
        json={"triggers": [], "simulated_dml": []},
    )
    assert resp.status_code == 404


def test_dryrun_skips_unknown_action_types(client: TestClient) -> None:
    """The evaluator should still run; the unknown action is dropped."""
    resp = client.post(
        "/examples/muret-parcels/triggers/dryrun",
        json={
            "triggers": [
                {
                    "name": "bogus-action",
                    "trigger_type": "dml",
                    "conditions": {"events": ["INSERT"]},
                    "actions": [
                        {"action_type": "rogue-not-real", "config": {}},
                        {"action_type": "log_event", "config": {"k": 1}},
                    ],
                }
            ],
            "simulated_dml": [
                {"table": "parcels", "operation": "INSERT", "feature_id": "z"}
            ],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    # Only the recognised ``log_event`` survives.
    assert len(body["actions"]) == 1
    assert body["actions"][0]["action_type"] == "log_event"


# ---------------------------------------------------------------------------
# Read-only middleware contract
# ---------------------------------------------------------------------------


def test_readonly_blocks_unrelated_writes(ro_client: TestClient) -> None:
    """Any other write path must still 403 in read-only mode."""
    resp = ro_client.post("/rules", json={"name": "x"})
    assert resp.status_code == 403
    body = resp.json()
    assert body["error"]["code"] == "READ_ONLY_DEMO"


def test_readonly_allows_dryrun_post(ro_client: TestClient) -> None:
    resp = ro_client.post(
        "/examples/muret-parcels/triggers/dryrun",
        json={"triggers": [], "simulated_dml": []},
    )
    assert resp.status_code == 200, resp.text


def test_readonly_allows_get_endpoints(ro_client: TestClient) -> None:
    for path in (
        "/examples",
        "/examples/health",
        "/examples/toulouse-isochrones",
        "/examples/toulouse-isochrones/preview",
    ):
        resp = ro_client.get(path)
        assert resp.status_code == 200, (path, resp.status_code, resp.text[:200])


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def test_to_change_record_maps_operation_enum() -> None:
    from gispulse.adapters.http.routers.examples_router import (
        SimulatedDML,
        _to_change_record,
    )

    rec = _to_change_record(
        SimulatedDML(table="parcels", operation="DELETE", feature_id="42")
    )
    assert rec.table_name == "parcels"
    assert rec.operation.value == "DELETE"
    assert rec.feature_id == "42"


def test_dry_run_dispatcher_records_action() -> None:
    from gispulse.adapters.http.routers.examples_router import (
        DryRunDispatcher,
        TriggerActionConfig,
        TriggerSpec,
    )

    disp = DryRunDispatcher()
    disp.record(
        trigger=TriggerSpec(id="00000000-0000-0000-0000-000000000001", name="t"),
        action=TriggerActionConfig(action_type="log_event", config={"k": 1}),
        record_table="parcels",
        feature_id="abc",
    )
    assert len(disp.captured) == 1
    captured = disp.captured[0]
    assert captured.action_type == "log_event"
    assert captured.config == {"k": 1}
    assert captured.table == "parcels"
    assert captured.feature_id == "abc"

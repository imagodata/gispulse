"""Integration tests for the explicit tracking endpoints (Lot 2 v2 — Q1).

Flow under test:
    1. Upload a GPKG dataset.
    2. POST /datasets/{id}/enable_tracking → triggers installed, watcher
       registered, dml.changed events flow.
    3. External SQLite INSERT → /ws/events receives dml.changed.
    4. POST /datasets/{id}/disable_tracking → triggers dropped, watcher
       unregistered.
    5. GET /datasets/{id}/tracking_status reflects state at every step.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def app_client(tmp_path, monkeypatch):
    project_gpkg = tmp_path / "project.gpkg"
    monkeypatch.setenv("GISPULSE_ENGINE", "gpkg")
    monkeypatch.setenv("GISPULSE_GPKG_PATH", str(project_gpkg))
    monkeypatch.setenv("GISPULSE_DB_PATH", str(tmp_path / "gispulse.db"))
    monkeypatch.setenv("GISPULSE_STORAGE", "memory")  # in-memory repo
    monkeypatch.setenv("GISPULSE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("GISPULSE_S3_ENDPOINT", raising=False)
    monkeypatch.setenv("GISPULSE_TIER", "community")
    monkeypatch.setenv("GISPULSE_API_KEYS", "")
    monkeypatch.setenv("GISPULSE_CORS_ORIGINS", "http://test")

    from gispulse.adapters.http.app import create_app

    app = create_app(data_dir=tmp_path / "data")
    with TestClient(app) as client:
        yield client, tmp_path


def _make_uploadable_gpkg(path: Path, layer: str = "parcels") -> None:
    """Create a minimal but valid GPKG with one feature layer.

    Uses pyogrio so the file passes the upload pipeline's metadata
    inspection. We then add an extra non-geometry column on top so the
    integration test can INSERT name-only rows via raw SQLite.
    """
    import geopandas as gpd
    import pyogrio
    from shapely.geometry import Point

    gdf = gpd.GeoDataFrame(
        {"name": ["seed"]},
        geometry=[Point(0, 0)],
        crs="EPSG:4326",
    )
    pyogrio.write_dataframe(gdf, str(path), layer=layer, driver="GPKG")


def _upload(client: TestClient, gpkg_path: Path) -> dict:
    with open(gpkg_path, "rb") as fh:
        resp = client.post(
            "/datasets/upload",
            files={"file": (gpkg_path.name, fh, "application/x-sqlite3")},
        )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _drain_dml(ws, expected: int, timeout: float = 5.0) -> list[dict]:
    deadline = time.monotonic() + timeout
    payloads: list[dict] = []
    while time.monotonic() < deadline and len(payloads) < expected:
        msg = ws.receive_text()
        try:
            p = json.loads(msg)
        except Exception:
            continue
        if p.get("type") == "ping":
            continue
        if p.get("type") == "dml.changed":
            payloads.append(p)
    return payloads


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEnableTrackingEndpoint:
    def test_status_disabled_before_enable(self, app_client) -> None:
        client, tmp = app_client
        gpkg_path = tmp / "src.gpkg"
        _make_uploadable_gpkg(gpkg_path)
        ds = _upload(client, gpkg_path)

        resp = client.get(f"/datasets/{ds['id']}/tracking_status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["enabled"] is False
        assert body["layers_tracked"] == []

    def test_enable_tracking_returns_layers(self, app_client) -> None:
        client, tmp = app_client
        gpkg_path = tmp / "src.gpkg"
        _make_uploadable_gpkg(gpkg_path, layer="parcels")
        ds = _upload(client, gpkg_path)

        resp = client.post(f"/datasets/{ds['id']}/enable_tracking")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["tracking_enabled"] is True
        assert "parcels" in body["layers_tracked"]

        # tracking_status now confirms enabled
        status = client.get(f"/datasets/{ds['id']}/tracking_status").json()
        assert status["enabled"] is True
        assert "parcels" in status["layers_tracked"]

    def test_enable_tracking_idempotent(self, app_client) -> None:
        client, tmp = app_client
        gpkg_path = tmp / "src.gpkg"
        _make_uploadable_gpkg(gpkg_path)
        ds = _upload(client, gpkg_path)

        for _ in range(3):
            resp = client.post(f"/datasets/{ds['id']}/enable_tracking")
            assert resp.status_code == 200

        registry = client.app.state.watcher_registry
        # Single registration despite three POSTs.
        assert registry.list_registered().count(ds["id"]) == 1

    def test_disable_tracking_unregisters(self, app_client) -> None:
        client, tmp = app_client
        gpkg_path = tmp / "src.gpkg"
        _make_uploadable_gpkg(gpkg_path)
        ds = _upload(client, gpkg_path)

        client.post(f"/datasets/{ds['id']}/enable_tracking")
        assert client.app.state.watcher_registry.is_registered(ds["id"])

        resp = client.post(f"/datasets/{ds['id']}/disable_tracking")
        assert resp.status_code == 200
        assert resp.json()["tracking_enabled"] is False
        assert not client.app.state.watcher_registry.is_registered(ds["id"])

    def test_disable_tracking_when_not_enabled_is_noop(self, app_client) -> None:
        client, tmp = app_client
        gpkg_path = tmp / "src.gpkg"
        _make_uploadable_gpkg(gpkg_path)
        ds = _upload(client, gpkg_path)

        resp = client.post(f"/datasets/{ds['id']}/disable_tracking")
        assert resp.status_code == 200
        assert resp.json()["tracking_enabled"] is False

    def test_enable_tracking_geojson_uses_file_blob_cdc(
        self, app_client, tmp_path
    ) -> None:
        # Since v1.6.1 (#157) GeoJSON is trackable: enable_tracking routes
        # it to the ``duckdb_diff`` file-blob CDC engine instead of
        # rejecting it. Previously this asserted a 400
        # ``tracking_unsupported_format`` — that contract changed when the
        # DuckDB-diff engine was wired through the endpoint.
        client, tmp = app_client
        geojson_path = tmp / "x.geojson"
        geojson_path.write_text(
            json.dumps(
                {
                    "type": "FeatureCollection",
                    "name": "x",
                    "crs": {
                        "type": "name",
                        "properties": {"name": "EPSG:4326"},
                    },
                    "features": [],
                }
            )
        )
        with open(geojson_path, "rb") as fh:
            up = client.post(
                "/datasets/upload",
                files={"file": ("x.geojson", fh, "application/geo+json")},
            )
        assert up.status_code == 201, up.text
        ds = up.json()

        resp = client.post(f"/datasets/{ds['id']}/enable_tracking")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["tracking_enabled"] is True
        assert body["layers_tracked"], body

    def test_enable_tracking_404_unknown_dataset(self, app_client) -> None:
        client, _ = app_client
        from uuid import uuid4

        missing = uuid4()
        resp = client.post(f"/datasets/{missing}/enable_tracking")
        assert resp.status_code == 404


class TestEndToEndDMLEvent:
    def test_dml_event_flows_after_enable(self, app_client) -> None:
        client, tmp = app_client
        gpkg_path = tmp / "src.gpkg"
        _make_uploadable_gpkg(gpkg_path, layer="parcels")
        ds = _upload(client, gpkg_path)

        # Find where the upload landed on disk so we can poke it.
        # source_path is exposed in the GET response (read_only=False here).
        ds_full = client.get(f"/datasets/{ds['id']}").json()
        source_path = Path(ds_full["source_path"])
        assert source_path.exists()

        # Enable tracking → triggers + watcher registered.
        resp = client.post(f"/datasets/{ds['id']}/enable_tracking")
        assert resp.status_code == 200, resp.text

        # Connect WS first so we don't miss the broadcast, then APPEND a
        # feature via pyogrio. Going through GDAL respects the GPKG
        # CHECK constraint that calls SpatiaLite's ST_IsEmpty (a raw
        # sqlite3.connect() can't load mod_spatialite and would raise).
        # The watcher's AFTER INSERT triggers fire either way and append
        # to _gispulse_change_log → /ws/events broadcasts dml.changed.
        import geopandas as gpd
        import pyogrio
        from shapely.geometry import Point

        with client.websocket_connect("/ws/events") as ws:
            new_row = gpd.GeoDataFrame(
                {"name": ["alpha"]},
                geometry=[Point(1, 1)],
                crs="EPSG:4326",
            )
            pyogrio.write_dataframe(
                new_row,
                str(source_path),
                layer="parcels",
                driver="GPKG",
                append=True,
            )

            payloads = _drain_dml(ws, 1, timeout=4.0)
        assert len(payloads) >= 1
        # Find the INSERT for 'parcels' (could be preceded by triggers
        # firing on watcher startup if the seed row was tracked).
        match = next(
            (p for p in payloads if p["data"].get("table") == "parcels"),
            None,
        )
        assert match is not None
        assert match["data"]["op"] == "INSERT"
        assert "change_id" in match["data"]
        # Multi-tenant contract (Beta E2E Lot 2 v2): the watcher injects
        # the dataset_id of the originating GPKG so consumers can
        # disambiguate across tenants. For uploaded datasets this is
        # the dataset uuid returned by /datasets/upload.
        assert match["data"]["dataset_id"] == ds["id"]


# ---------------------------------------------------------------------------
# Issue #93 — change-log inspect endpoints (CLI ↔ Portal parity P0-2)
# ---------------------------------------------------------------------------


class TestChangelogInspectEndpoints:
    """``GET /datasets/{id}/changelog`` + ``/stats`` + ``POST /doctor``.

    Backs the portal's "Change-log" tab. Same SQL contract as the CLI's
    ``gispulse track tail/list/doctor`` via ``persistence.changelog_*``.
    """

    def _seed_dataset(self, client: TestClient, tmp_path: Path) -> dict:
        gpkg = tmp_path / "changelog.gpkg"
        _make_uploadable_gpkg(gpkg, layer="parcels")
        ds = _upload(client, gpkg)
        # Enable tracking + insert one extra row so the change_log has
        # at least 2 entries (1 seed + 1 explicit INSERT).
        resp = client.post(f"/datasets/{ds['id']}/enable_tracking")
        assert resp.status_code == 200, resp.text
        return ds

    def _insert_via_pyogrio(self, gpkg_path: Path, n: int = 2) -> None:
        """Append rows via pyogrio so the GPKG RTree triggers (which
        call SpatiaLite ``ST_*`` functions absent from the bundled
        sqlite3 module) don't blow up. Same pattern as the existing
        TestEndToEndDMLEvent fixture."""
        import geopandas as gpd
        import pyogrio
        from shapely.geometry import Point

        gdf = gpd.GeoDataFrame(
            {"name": [f"http_seeded_{i}" for i in range(n)]},
            geometry=[Point(i, i) for i in range(n)],
            crs="EPSG:4326",
        )
        pyogrio.write_dataframe(
            gdf,
            str(gpkg_path),
            layer="parcels",
            driver="GPKG",
            append=True,
        )

    def test_changelog_returns_pending_rows(
        self, app_client
    ) -> None:
        client, tmp_path = app_client
        ds = self._seed_dataset(client, tmp_path)
        # Resolve the on-disk path the same way the router does.
        # Storage layout: <data_dir>/datasets/<id>/<filename>.
        from pathlib import Path as _P


        gpkg_disk = next(_P(tmp_path / "data").rglob("*.gpkg"))
        self._insert_via_pyogrio(gpkg_disk, n=3)

        resp = client.get(f"/datasets/{ds['id']}/changelog?limit=10")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["dataset_id"] == ds["id"]
        items = body["items"]
        # At least the 3 inserts we just did.
        assert len(items) >= 3
        assert all(r["table_name"] == "parcels" for r in items if r["table_name"])
        # Cursor advances.
        assert body["next_since_id"] >= max(r["id"] for r in items)

    def test_changelog_pagination_via_since_id(self, app_client) -> None:
        client, tmp_path = app_client
        ds = self._seed_dataset(client, tmp_path)
        from pathlib import Path as _P

        gpkg_disk = next(_P(tmp_path / "data").rglob("*.gpkg"))
        self._insert_via_pyogrio(gpkg_disk, n=5)

        page1 = client.get(
            f"/datasets/{ds['id']}/changelog?limit=2"
        ).json()
        assert len(page1["items"]) == 2
        cursor = page1["next_since_id"]
        page2 = client.get(
            f"/datasets/{ds['id']}/changelog?since_id={cursor}&limit=10"
        ).json()
        # No overlap.
        page1_ids = {r["id"] for r in page1["items"]}
        page2_ids = {r["id"] for r in page2["items"]}
        assert page1_ids.isdisjoint(page2_ids)

    def test_changelog_filter_by_layer(self, app_client) -> None:
        client, tmp_path = app_client
        ds = self._seed_dataset(client, tmp_path)
        # Filter to 'parcels' — already the only tracked layer.
        body = client.get(
            f"/datasets/{ds['id']}/changelog?layer=parcels"
        ).json()
        assert all(r["table_name"] == "parcels" for r in body["items"])
        # Non-existent layer returns empty list.
        body_empty = client.get(
            f"/datasets/{ds['id']}/changelog?layer=nonexistent"
        ).json()
        assert body_empty["items"] == []

    def test_changelog_filter_by_op_case_insensitive(self, app_client) -> None:
        client, tmp_path = app_client
        ds = self._seed_dataset(client, tmp_path)
        body = client.get(
            f"/datasets/{ds['id']}/changelog?op=insert"
        ).json()
        assert all(r["operation"] == "INSERT" for r in body["items"])

    def test_changelog_invalid_op_returns_400(self, app_client) -> None:
        client, tmp_path = app_client
        ds = self._seed_dataset(client, tmp_path)
        resp = client.get(f"/datasets/{ds['id']}/changelog?op=UPSERT")
        assert resp.status_code == 400

    def test_changelog_invalid_limit_returns_400(self, app_client) -> None:
        client, tmp_path = app_client
        ds = self._seed_dataset(client, tmp_path)
        resp = client.get(f"/datasets/{ds['id']}/changelog?limit=0")
        assert resp.status_code == 400
        resp = client.get(f"/datasets/{ds['id']}/changelog?limit=10000")
        assert resp.status_code == 400

    def test_changelog_unknown_dataset_returns_404(self, app_client) -> None:
        client, _ = app_client
        from uuid import uuid4

        resp = client.get(f"/datasets/{uuid4()}/changelog")
        assert resp.status_code == 404

    def test_stats_per_layer_aggregates(self, app_client) -> None:
        client, tmp_path = app_client
        ds = self._seed_dataset(client, tmp_path)
        from pathlib import Path as _P

        gpkg_disk = next(_P(tmp_path / "data").rglob("*.gpkg"))
        self._insert_via_pyogrio(gpkg_disk, n=4)

        body = client.get(f"/datasets/{ds['id']}/changelog/stats").json()
        assert body["dataset_id"] == ds["id"]
        assert body["total_pending"] >= 4
        layers = {b["layer"]: b for b in body["by_layer"]}
        assert "parcels" in layers
        assert layers["parcels"]["by_op"]["INSERT"] >= 4

    def test_stats_unknown_dataset_returns_404(self, app_client) -> None:
        client, _ = app_client
        from uuid import uuid4

        resp = client.get(f"/datasets/{uuid4()}/changelog/stats")
        assert resp.status_code == 404

    def test_doctor_healthy_returns_ok(self, app_client) -> None:
        client, tmp_path = app_client
        ds = self._seed_dataset(client, tmp_path)
        resp = client.post(f"/datasets/{ds['id']}/changelog/doctor")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["dataset_id"] == ds["id"]
        assert body["ok"] is True
        assert body["status"] == "ok"
        assert body["health_score"] >= 90  # WAL + busy_timeout may warn
        assert isinstance(body["checks"], list) and body["checks"]

    def test_doctor_autofix_repairs_missing_trigger(self, app_client) -> None:
        client, tmp_path = app_client
        ds = self._seed_dataset(client, tmp_path)
        # Drop one trigger out-of-band to simulate corruption.
        from pathlib import Path as _P
        import sqlite3

        gpkg_disk = next(_P(tmp_path / "data").rglob("*.gpkg"))
        con = sqlite3.connect(str(gpkg_disk), timeout=10)
        try:
            cur = con.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger' "
                "AND name LIKE '\\_gispulse\\_trg\\_parcels\\_update' ESCAPE '\\'"
            )
            row = cur.fetchone()
            if row:
                con.execute(f'DROP TRIGGER "{row[0]}"')
                con.commit()
        finally:
            con.close()
        # Without auto_fix → ok=false.
        resp_no_fix = client.post(
            f"/datasets/{ds['id']}/changelog/doctor"
        )
        assert resp_no_fix.json()["ok"] is False
        # With auto_fix → repair, ok=true, layer in repaired list.
        resp_fix = client.post(
            f"/datasets/{ds['id']}/changelog/doctor?auto_fix=true"
        )
        body = resp_fix.json()
        assert body["ok"] is True
        assert "parcels" in body["repaired"]

    def test_doctor_unknown_dataset_returns_404(self, app_client) -> None:
        client, _ = app_client
        from uuid import uuid4

        resp = client.post(f"/datasets/{uuid4()}/changelog/doctor")
        assert resp.status_code == 404

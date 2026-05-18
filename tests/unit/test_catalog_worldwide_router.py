"""Unit tests for A10 (#236) — worldwide aggregator HTTP endpoints.

The ``worldwide`` data source is mocked in :data:`SOURCES` with a
fake over a *local* GeoParquet fixture, so the preview / materialise
roundtrips move zero bytes off the box.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gispulse.core.plugin_model import (
    AccessProtocol,
    AccessSpec,
    Payload,
    SourceDomain,
)
from gispulse.core.sources import SOURCES, SourceEntryRef
from gispulse.persistence.virtual_dataset import VIRTUAL_DATASETS


# -- a local-parquet fixture + a fake worldwide source ----------------------


@pytest.fixture(scope="module")
def parquet_fixture(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A 3-row GeoParquet file with an Overture-style ``bbox`` struct."""
    import duckdb

    path = tmp_path_factory.mktemp("ww") / "places.parquet"
    con = duckdb.connect()
    con.execute(
        f"""
        COPY (
          SELECT * FROM (VALUES
            (1, 'alpha', {{'xmin': 0.0,  'ymin': 0.0,  'xmax': 1.0,  'ymax': 1.0}}),
            (2, 'beta',  {{'xmin': 10.0, 'ymin': 10.0, 'xmax': 11.0, 'ymax': 11.0}}),
            (3, 'gamma', {{'xmin': 0.5,  'ymin': 0.5,  'xmax': 1.5,  'ymax': 1.5}})
          ) AS t(id, name, bbox)
        ) TO '{path}' (FORMAT PARQUET)
        """
    )
    con.close()
    return path


def _entry(endpoint: str) -> SourceEntryRef:
    return SourceEntryRef(
        id="test-places",
        name="Test Places",
        access=AccessSpec(
            protocol=AccessProtocol.REMOTE_TABLE,
            endpoint=endpoint,
            params={"glob": "", "hive_partitioning": False},
        ),
        domain=SourceDomain.OBSERVATION,
        payload=Payload.VECTOR,
        jurisdiction="world",
        revision_token="t0",
        metadata={"family": "test-family", "provider": "Test"},
    )


class _FakeWorldwideSource:
    """A minimal stand-in for ``WorldwideCatalogSource`` (SOURCES mock)."""

    name = "worldwide"

    def __init__(self, entries: list[SourceEntryRef]) -> None:
        self._entries = list(entries)

    def catalog(
        self,
        search: str | None = None,
        *,
        domain: str | None = None,
        payload: str | None = None,
        jurisdiction: str | None = None,
        protocol: str | None = None,
        family: str | None = None,
    ) -> list[SourceEntryRef]:
        out = []
        for e in self._entries:
            q = search.lower() if search else None
            if q and q not in e.id.lower() and q not in e.name.lower():
                continue
            if domain and (not e.domain or e.domain.value != domain):
                continue
            if payload and (not e.payload or e.payload.value != payload):
                continue
            if jurisdiction and e.jurisdiction != jurisdiction:
                continue
            if protocol and e.access.protocol.value != protocol:
                continue
            if family and e.metadata.get("family") != family:
                continue
            out.append(e)
        return out

    def entries(self) -> list[SourceEntryRef]:
        return list(self._entries)


@pytest.fixture()
def client(
    parquet_fixture: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> TestClient:
    from gispulse.adapters.http.app import create_app
    from gispulse.adapters.http.rate_limit import limiter

    monkeypatch.setenv("GISPULSE_STORAGE", "memory")
    os.environ["GISPULSE_STORAGE"] = "memory"
    limiter.enabled = False

    SOURCES.clear()
    SOURCES.register(_FakeWorldwideSource([_entry(str(parquet_fixture))]))
    VIRTUAL_DATASETS.clear()

    app = create_app(data_dir=tmp_path)
    try:
        yield TestClient(app)
    finally:
        SOURCES.clear()
        VIRTUAL_DATASETS.clear()


# -- GET /api/catalog/worldwide ---------------------------------------------


def test_list_worldwide_returns_entries(client: TestClient) -> None:
    r = client.get("/api/catalog/worldwide")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    entry = body[0]
    assert entry["id"] == "test-places"
    assert entry["domain"] == "observation"
    assert entry["payload"] == "vector"
    assert entry["jurisdiction"] == "world"
    assert entry["protocol"] == "remote-table"
    assert entry["family"] == "test-family"


def test_list_worldwide_filters_by_axis(client: TestClient) -> None:
    assert len(client.get("/api/catalog/worldwide?domain=observation").json()) == 1
    assert client.get("/api/catalog/worldwide?domain=imagerie").json() == []
    assert len(client.get("/api/catalog/worldwide?family=test-family").json()) == 1
    assert len(client.get("/api/catalog/worldwide?protocol=remote-table").json()) == 1


# -- POST /api/catalog/virtual ----------------------------------------------


def test_create_virtual_dataset(client: TestClient) -> None:
    r = client.post("/api/catalog/virtual", json={"entry_id": "test-places"})
    assert r.status_code == 201
    body = r.json()
    assert body["id"] == "virtual:worldwide/test-places"
    assert body["source_type"] == "virtual"
    assert body["file_size"] == 0
    assert body["feature_count"] is None


def test_create_virtual_unknown_entry_returns_404(client: TestClient) -> None:
    r = client.post("/api/catalog/virtual", json={"entry_id": "missing"})
    assert r.status_code == 404


# -- GET /api/catalog/virtual/{id}/preview ----------------------------------


def test_preview_virtual_dataset(client: TestClient) -> None:
    client.post("/api/catalog/virtual", json={"entry_id": "test-places"})
    r = client.get("/api/catalog/virtual/virtual:worldwide/test-places/preview")
    assert r.status_code == 200
    body = r.json()
    assert body["source_type"] == "virtual"
    assert body["feature_count"] == 3


def test_preview_virtual_dataset_with_bbox(client: TestClient) -> None:
    client.post("/api/catalog/virtual", json={"entry_id": "test-places"})
    r = client.get(
        "/api/catalog/virtual/virtual:worldwide/test-places/preview?bbox=0,0,5,5"
    )
    assert r.status_code == 200
    body = r.json()
    assert body["feature_count"] == 2
    assert body["virtual_bbox"] == [0.0, 0.0, 5.0, 5.0]


def test_preview_unknown_virtual_returns_404(client: TestClient) -> None:
    r = client.get("/api/catalog/virtual/virtual:worldwide/ghost/preview")
    assert r.status_code == 404


def test_preview_rejects_malformed_bbox(client: TestClient) -> None:
    client.post("/api/catalog/virtual", json={"entry_id": "test-places"})
    r = client.get(
        "/api/catalog/virtual/virtual:worldwide/test-places/preview?bbox=1,2,3"
    )
    assert r.status_code == 400


# -- POST /api/catalog/virtual/{id}/materialize -----------------------------


def test_materialize_virtual_dataset(client: TestClient) -> None:
    client.post("/api/catalog/virtual", json={"entry_id": "test-places"})
    r = client.post(
        "/api/catalog/virtual/virtual:worldwide/test-places/materialize",
        json={"name": "Materialised places"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "Materialised places"
    assert body["virtual_id"] == "virtual:worldwide/test-places"
    assert Path(body["source_path"]).exists()


def test_materialize_unknown_virtual_returns_404(client: TestClient) -> None:
    r = client.post(
        "/api/catalog/virtual/virtual:worldwide/ghost/materialize", json={}
    )
    assert r.status_code == 404

"""End-to-end test for the v1.9.0 worldwide aggregator (A15, #241).

Walks the full EPIC #226 journey against a *local* GeoParquet fixture so
CI moves zero bytes off the box:

    browse catalogue → create virtual dataset → lazy bbox preview
    → run an ETL pipeline on the virtual input → materialise to disk
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gispulse.core.plugin_model import AccessProtocol, AccessSpec, Payload, SourceDomain
from gispulse.core.sources import SOURCES, SourceEntryRef
from gispulse.persistence.virtual_dataset import VIRTUAL_DATASETS


# -- a local GeoParquet fixture with a real geometry column -----------------


@pytest.fixture(scope="module")
def geo_parquet(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A 3-point GeoParquet fixture: WKB ``geometry`` + Overture ``bbox`` struct."""
    import duckdb

    path = tmp_path_factory.mktemp("e2e") / "points.parquet"
    con = duckdb.connect()
    con.load_extension("spatial")
    con.execute(
        f"""
        COPY (
          SELECT id, name,
                 ST_AsWKB(ST_Point(x, y)) AS geometry,
                 {{'xmin': x, 'ymin': y, 'xmax': x, 'ymax': y}} AS bbox
          FROM (VALUES
            (1, 'alpha', 0.5, 0.5),
            (2, 'beta',  10.0, 10.0),
            (3, 'gamma', 1.5, 1.5)
          ) AS t(id, name, x, y)
        ) TO '{path}' (FORMAT PARQUET)
        """
    )
    con.close()
    return path


def _entry(endpoint: str) -> SourceEntryRef:
    return SourceEntryRef(
        id="test-points",
        name="Test Points",
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
    name = "worldwide"

    def __init__(self, entries: list[SourceEntryRef]) -> None:
        self._entries = list(entries)

    def catalog(self, search: str | None = None, **_: object) -> list[SourceEntryRef]:
        return list(self._entries)

    def entries(self) -> list[SourceEntryRef]:
        return list(self._entries)


@pytest.fixture
def client(
    geo_parquet: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> TestClient:
    from gispulse.adapters.http.app import create_app
    from gispulse.adapters.http.rate_limit import limiter

    monkeypatch.setenv("GISPULSE_STORAGE", "memory")
    os.environ["GISPULSE_STORAGE"] = "memory"
    limiter.enabled = False

    SOURCES.clear()
    SOURCES.register(_FakeWorldwideSource([_entry(str(geo_parquet))]))
    VIRTUAL_DATASETS.clear()

    app = create_app(data_dir=tmp_path)
    try:
        yield TestClient(app)
    finally:
        SOURCES.clear()
        VIRTUAL_DATASETS.clear()


# -- the full journey --------------------------------------------------------


def test_worldwide_aggregator_end_to_end(client: TestClient) -> None:
    # 1. Browse the worldwide catalogue.
    listing = client.get("/api/catalog/worldwide")
    assert listing.status_code == 200
    assert [e["id"] for e in listing.json()] == ["test-points"]

    # 2. Create a lazy virtual dataset from the entry.
    created = client.post("/api/catalog/virtual", json={"entry_id": "test-points"})
    assert created.status_code == 201
    virtual_id = created.json()["id"]
    assert virtual_id == "virtual:worldwide/test-points"

    # 3. Lazy bbox-scoped preview — (0,0)-(5,5) keeps 2 of the 3 points.
    preview = client.get(
        f"/api/catalog/virtual/{virtual_id}/preview?bbox=0,0,5,5"
    )
    assert preview.status_code == 200
    assert preview.json()["feature_count"] == 2

    # 4. ETL — run a pipeline whose primary input is the virtual dataset
    #    (A11 pipeline-prepare hook materialises it before execution).
    etl = client.post(
        "/pipelines/execute",
        json={
            "name": "buffer_worldwide",
            "input_path": virtual_id,
            "bbox": [0.0, 0.0, 5.0, 5.0],
            "steps": [
                {"id": "buf", "type": "capability", "capability": "buffer",
                 "params": {"distance": 100.0}},
            ],
        },
    )
    assert etl.status_code == 200, etl.text
    etl_body = etl.json()
    assert etl_body["steps_executed"] == 1
    assert etl_body["total_features_out"] == 2  # non-empty ETL result

    # 5. Materialise the virtual dataset into a real on-disk project dataset.
    materialised = client.post(
        f"/api/catalog/virtual/{virtual_id}/materialize",
        json={"name": "Materialised points", "bbox": [0.0, 0.0, 5.0, 5.0]},
    )
    assert materialised.status_code == 201
    body = materialised.json()
    assert body["virtual_id"] == virtual_id
    assert Path(body["source_path"]).exists()


def test_virtual_ref_layer_feeds_a_two_node_pipeline(client: TestClient) -> None:
    """A virtual dataset wired as a ref layer feeds a downstream capability."""
    created = client.post("/api/catalog/virtual", json={"entry_id": "test-points"})
    virtual_id = created.json()["id"]

    resp = client.post(
        "/pipelines/execute",
        json={
            "name": "ref_layer_virtual",
            "input_path": virtual_id,
            "bbox": [0.0, 0.0, 5.0, 5.0],
            "ref_layers": {"ww": virtual_id},
            "steps": [
                {"id": "buf", "type": "capability", "capability": "buffer",
                 "params": {"distance": 50.0}, "input": "ww"},
            ],
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["total_features_out"] == 2

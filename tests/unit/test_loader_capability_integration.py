"""Integration: any source loaded via load() feeds any capability (DP3).

The promise of the data-provider track is that ``gispulse.load`` returns a
plain GeoDataFrame, so ``gispulse.apply`` runs on its output unchanged —
whatever the source was (file, datamart, remote). These tests pin that
contract end-to-end.
"""

from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import Point

import gispulse
from gispulse.persistence.datamart import DATAMARTS, Datamart
from gispulse.persistence.loader import LazyDataset, load


@pytest.fixture
def points_gdf() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"v": [1, 2, 3]},
        geometry=[Point(0, 0), Point(10, 10), Point(20, 20)],
        crs="EPSG:3857",
    )


@pytest.fixture(autouse=True)
def _clean_datamarts():
    DATAMARTS.clear()
    yield
    DATAMARTS.clear()


def test_load_file_then_apply_buffer(tmp_path, points_gdf):
    path = tmp_path / "pts.parquet"
    points_gdf.to_parquet(path)

    gdf = load(str(path))
    out = gispulse.apply("buffer", gdf, distance=1.0)

    assert isinstance(out, gpd.GeoDataFrame)
    assert len(out) == 3
    assert set(out.geometry.geom_type) == {"Polygon"}


def test_load_datamart_then_apply_cluster(tmp_path, points_gdf):
    points_gdf.to_parquet(tmp_path / "sites.parquet")
    DATAMARTS.register(Datamart(name="ref", location=str(tmp_path)))

    gdf = load("datamart://ref/sites")
    out = gispulse.apply("cluster_dbscan", gdf, eps=5.0, min_samples=1)

    assert isinstance(out, gpd.GeoDataFrame)
    assert "cluster" in out.columns
    assert len(out) == 3


def test_lazy_handle_materialises_then_applies(monkeypatch, points_gdf):
    # A LazyDataset materialises to a GeoDataFrame on demand; the result
    # feeds a capability exactly like an eagerly-loaded frame. We stub the
    # DuckDB scan so the contract is exercised without a live backend.
    handle = LazyDataset(scan_sql="read_parquet('s3://b/x.parquet')", crs="EPSG:3857")
    monkeypatch.setattr(
        "gispulse.persistence.loader._scan_to_gdf",
        lambda scan_sql, *, crs, bbox: points_gdf,
    )

    gdf = handle.to_geodataframe()
    out = gispulse.apply("buffer", gdf, distance=2.0)

    assert isinstance(out, gpd.GeoDataFrame)
    assert set(out.geometry.geom_type) == {"Polygon"}

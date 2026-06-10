"""Tests for bounded vector-file to parquet materialization."""

from __future__ import annotations

import inspect
from pathlib import Path

import geopandas as gpd
import pyogrio
import pytest
from shapely.geometry import Point

from gispulse.core.io.vector_stream import stream_vector_to_parquet

pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")


@pytest.fixture
def sample_gpkg(tmp_path: Path) -> Path:
    path = tmp_path / "sample.gpkg"
    gdf = gpd.GeoDataFrame(
        {
            "id": [1, 2, 3, 4, 5],
            "name": ["alpha", "beta", "gamma", "delta", "epsilon"],
            "category": ["keep", "keep", "drop", "keep", "keep"],
            "value": [10, 20, 30, 40, 50],
        },
        geometry=[
            Point(0, 0),
            Point(1, 1),
            Point(2, 2),
            Point(5, 5),
            Point(10, 10),
        ],
        crs="EPSG:4326",
    )
    pyogrio.write_dataframe(gdf, str(path), layer="places", driver="GPKG")
    return path


def test_stream_vector_to_parquet_roundtrips_gpkg_features(sample_gpkg: Path, tmp_path: Path):
    out = tmp_path / "places.parquet"

    stream_vector_to_parquet(sample_gpkg, out, layer="places", batch_size=2)

    expected = pyogrio.read_dataframe(sample_gpkg, layer="places").sort_values("id")
    table = pq.read_table(out)
    geom_col = _geometry_column_name(table)
    actual = table.drop([geom_col]).to_pandas().sort_values("id")

    assert actual.to_dict(orient="list") == expected.drop(columns="geometry").to_dict(orient="list")
    assert _wkb_by_id(table, geom_col) == dict(zip(expected["id"], expected.geometry.to_wkb()))


def test_stream_vector_to_parquet_pushes_layer_where_bbox_and_columns(
    sample_gpkg: Path, tmp_path: Path
):
    out = tmp_path / "filtered.parquet"

    stream_vector_to_parquet(
        sample_gpkg,
        out,
        layer="places",
        where="category = 'keep'",
        bbox=(-0.5, -0.5, 1.5, 1.5),
        columns=["id", "category"],
        batch_size=1,
    )

    table = pq.read_table(out)
    geom_col = _geometry_column_name(table)
    actual = table.drop([geom_col]).to_pandas().sort_values("id")

    assert actual.to_dict(orient="list") == {
        "id": [1, 2],
        "category": ["keep", "keep"],
    }
    assert "name" not in table.column_names
    assert geom_col in table.column_names


def test_stream_vector_to_parquet_writes_multiple_row_groups(sample_gpkg: Path, tmp_path: Path):
    out = tmp_path / "batched.parquet"

    stream_vector_to_parquet(sample_gpkg, out, layer="places", batch_size=2)

    metadata = pq.ParquetFile(out).metadata
    assert metadata.num_rows == 5
    assert metadata.num_row_groups >= 3


def test_stream_vector_to_parquet_uses_incremental_parquet_writer():
    import gispulse.core.io.vector_stream as vector_stream

    source = inspect.getsource(vector_stream)

    assert "pq.ParquetWriter" in source
    assert "for batch in reader" in source
    assert ".write_batch(" in source
    assert "list(reader)" not in source
    assert "pa.Table.from_batches" not in source


def _geometry_column_name(table: pa.Table) -> str:
    for field in table.schema:
        metadata = field.metadata or {}
        extension_name = metadata.get(b"ARROW:extension:name", b"")
        if b"geoarrow" in extension_name.lower() or b"wkb" in extension_name.lower():
            return field.name
    for candidate in ("geometry", "geom", "wkb_geometry"):
        if candidate in table.column_names:
            return candidate
    binary_columns = [
        field.name
        for field in table.schema
        if pa.types.is_binary(field.type) or pa.types.is_large_binary(field.type)
    ]
    assert len(binary_columns) == 1
    return binary_columns[0]


def _wkb_by_id(table: pa.Table, geom_col: str) -> dict[int, bytes | None]:
    ids = table.column("id").to_pylist()
    wkbs = []
    for value in table.column(geom_col).to_pylist():
        wkbs.append(bytes(value) if value is not None else None)
    return dict(zip(ids, wkbs))

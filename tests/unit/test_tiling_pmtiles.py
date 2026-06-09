from __future__ import annotations

import time
from importlib import import_module
from importlib.util import find_spec
from pathlib import Path

import geopandas as gpd
from pmtiles.reader import MmapSource, Reader, all_tiles
from pmtiles.tile import Compression, TileType
from shapely.geometry import Point

from gispulse.core.plugin_model import AccessProtocol, Payload, SourceResult, WriteSpec
from gispulse.core.sources import ProtocolRegistry


def _toy_geoparquet(tmp_path: Path) -> Path:
    path = tmp_path / "toy.parquet"
    gdf = gpd.GeoDataFrame(
        {"id": [1, 2], "name": ["paris", "lyon"]},
        geometry=[Point(2.35, 48.85), Point(4.84, 45.76)],
        crs="EPSG:4326",
    )
    gdf.to_parquet(path)
    return path


def _read_pmtiles(path: Path) -> tuple[dict[str, object], dict[str, object], list[tuple]]:
    with path.open("rb") as fh:
        get_bytes = MmapSource(fh)
        reader = Reader(get_bytes)
        header = reader.header()
        metadata = reader.metadata()
        tiles = list(all_tiles(get_bytes))
        if tiles:
            first_zxy, first_tile = tiles[0]
            assert reader.get(*first_zxy) == first_tile
        return header, metadata, tiles


def test_write_pmtiles_writes_valid_readable_deterministic_archive(tmp_path: Path) -> None:
    assert find_spec("gispulse.tiling") is not None
    write_pmtiles = import_module("gispulse.tiling").write_pmtiles
    source = _toy_geoparquet(tmp_path)
    out_a = tmp_path / "toy-a.pmtiles"
    out_b = tmp_path / "toy-b.pmtiles"

    report = write_pmtiles(source, out_a, layer="places", min_zoom=5, max_zoom=5)
    time.sleep(1.1)
    write_pmtiles(source, out_b, layer="places", min_zoom=5, max_zoom=5)

    assert out_a.read_bytes()[:7] == b"PMTiles"
    header, metadata, tiles = _read_pmtiles(out_a)
    assert header["tile_type"] is TileType.MVT
    assert header["tile_compression"] is Compression.NONE
    assert header["min_zoom"] == 5
    assert header["max_zoom"] == 5
    assert header["addressed_tiles_count"] >= 1
    assert len(tiles) == header["addressed_tiles_count"]
    assert len(tiles[0][1]) > 0
    assert metadata["vector_layers"] == [
        {"id": "places", "fields": {"id": "Number", "name": "String"}}
    ]
    assert report.destination == str(out_a)
    assert report.rows_written == header["addressed_tiles_count"]
    assert report.created is True
    assert out_a.read_bytes() == out_b.read_bytes()


def test_pmtiles_writer_dispatches_write_spec_with_result_crs(tmp_path: Path) -> None:
    tiling = import_module("gispulse.tiling")
    registry = ProtocolRegistry()
    tiling.register_pmtiles_writer(registry)
    writer = registry.get_writer(AccessProtocol.PMTILES)
    gdf = gpd.GeoDataFrame(
        {"id": [1], "name": ["paris"]},
        geometry=[Point(2.35, 48.85)],
        crs=None,
    )
    result = SourceResult(payload=Payload.VECTOR, data=gdf, crs="EPSG:4326")
    spec = WriteSpec(
        protocol=AccessProtocol.PMTILES,
        destination=str(tmp_path / "writer.pmtiles"),
        layer="places",
        options={"min_zoom": 5, "max_zoom": 5},
    )

    report = writer.write(result, spec)

    header, metadata, tiles = _read_pmtiles(Path(report.destination))
    assert header["addressed_tiles_count"] == 1
    assert metadata["vector_layers"] == [
        {"id": "places", "fields": {"id": "Number", "name": "String"}}
    ]
    assert len(tiles[0][1]) > 0


def test_coverage_spans_all_features_not_just_the_first(tmp_path: Path) -> None:
    """Regression: la couverture de tuiles doit englober TOUTES les features.

    ``_features_bounds_4326`` utilisait ``ST_Extent`` (scalaire, bbox d'une
    seule geometrie) dans un ``SELECT`` sans ``GROUP BY`` ; ``.fetchone()`` ne
    lisait que la 1re feature et le bounds de couverture s'effondrait sur un
    point -> une seule tuile par zoom (la colonne de cette feature), la couche
    se retrouvant tronquee a la frontiere de cette tuile. ``ST_Envelope_Agg``
    agrege l'enveloppe de toutes les features.

    Source = 2 points distants (paris -> tuile (129, 88), lyon -> (131, 91) a
    z8) : la couche doit ecrire les deux tuiles. Avec le bug, une seule.
    """
    write_pmtiles = import_module("gispulse.tiling").write_pmtiles
    source = _toy_geoparquet(tmp_path)  # Point(2.35, 48.85), Point(4.84, 45.76)
    out = tmp_path / "coverage.pmtiles"

    write_pmtiles(source, out, layer="places", min_zoom=8, max_zoom=8)

    _, _, tiles = _read_pmtiles(out)
    written = {zxy for zxy, _data in tiles}
    expected = {(8, 129, 88), (8, 131, 91)}
    assert expected.issubset(written), f"couverture incomplete: {sorted(written)}"

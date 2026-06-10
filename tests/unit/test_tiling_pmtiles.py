from __future__ import annotations

import time
from importlib import import_module
from importlib.util import find_spec
from pathlib import Path

import geopandas as gpd
import pytest
from pmtiles.reader import MmapSource, Reader, all_tiles
from pmtiles.tile import Compression, TileType
from shapely.geometry import Point

from gispulse.core.plugin_model import AccessProtocol, Payload, SourceResult, WriteSpec
from gispulse.core.sources import ProtocolRegistry
from gispulse.tiling import TileLayer


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


def test_write_pmtiles_matches_pre_refactor_golden(tmp_path: Path) -> None:
    """Garde byte-a-byte contre l'archive produite par le code AVANT refactor."""
    write_pmtiles = import_module("gispulse.tiling").write_pmtiles
    golden = Path(__file__).parent / "fixtures" / "golden_places_z5.pmtiles"
    src = tmp_path / "src.parquet"
    gpd.GeoDataFrame(
        {"id": [1, 2], "name": ["paris", "lyon"]},
        geometry=[Point(2.35, 48.85), Point(4.84, 45.76)],
        crs="EPSG:4326",
    ).to_parquet(src)
    out = tmp_path / "out.pmtiles"
    write_pmtiles(src, out, layer="places", min_zoom=5, max_zoom=5)
    assert out.read_bytes() == golden.read_bytes()


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


def _grid_lines(n: int) -> list:
    """``n`` LineStrings reparties sur une grille (densite multi-tuiles)."""
    from shapely.geometry import LineString

    lines = []
    for i in range(n):
        x = 2.5 + (i % 50) * 0.06
        y = 49.5 + (i // 50) * 0.04
        lines.append(LineString([(x, y), (x + 0.05, y + 0.03)]))
    return lines


def test_write_pmtiles_handles_many_line_features(tmp_path: Path) -> None:
    """Regression : un volume de lignes ne doit plus corrompre l'encodeur MVT.

    La forme « un appel ST_AsMVT par tuile » segfaultait (corruption memoire de
    l'extension spatiale DuckDB) au-dela de quelques centaines de lignes. La forme
    groupee (une requete, GROUP BY tuile) doit produire une archive valide.
    """
    write_pmtiles = import_module("gispulse.tiling").write_pmtiles
    path = tmp_path / "lines.parquet"
    gpd.GeoDataFrame(
        {"id": list(range(600))},
        geometry=_grid_lines(600),
        crs="EPSG:4326",
    ).to_parquet(path)

    out = tmp_path / "lines.pmtiles"
    report = write_pmtiles(path, out, layer="lines", min_zoom=0, max_zoom=10)
    assert report.rows_written > 0
    _, _, tiles = _read_pmtiles(out)
    assert len(tiles) > 0


def test_write_pmtiles_coerces_unsupported_property_types(tmp_path: Path) -> None:
    """Regression : une colonne DATE/timestamp ne doit pas casser l'encodage.

    ``ST_AsMVT`` n'accepte que VARCHAR/FLOAT/DOUBLE/INTEGER/BIGINT/BOOLEAN ; les
    autres types doivent etre castes (ici DATE -> VARCHAR) au lieu de lever.
    """
    import datetime as _dt

    write_pmtiles = import_module("gispulse.tiling").write_pmtiles
    path = tmp_path / "dated.parquet"
    gpd.GeoDataFrame(
        {"id": [1, 2], "target_date": [_dt.date(2030, 7, 1), _dt.date(2031, 1, 15)]},
        geometry=[Point(4.35, 50.85), Point(4.40, 50.80)],
        crs="EPSG:4326",
    ).to_parquet(path)

    out = tmp_path / "dated.pmtiles"
    report = write_pmtiles(path, out, layer="dated", min_zoom=0, max_zoom=10)
    assert report.rows_written > 0


def test_tilelayer_rejects_overlapping_zoom_ranges(tmp_path: Path) -> None:
    write_pmtiles_pyramid = import_module("gispulse.tiling").write_pmtiles_pyramid
    source = _toy_geoparquet(tmp_path)
    layers = [
        TileLayer(source=source, layer="r_low", min_zoom=0, max_zoom=6),
        TileLayer(source=source, layer="r_high", min_zoom=6, max_zoom=10),
    ]
    with pytest.raises(ValueError, match="zoom ranges must be disjoint"):
        write_pmtiles_pyramid(layers, tmp_path / "x.pmtiles")


def test_tilelayer_rejects_duplicate_layer_names(tmp_path: Path) -> None:
    write_pmtiles_pyramid = import_module("gispulse.tiling").write_pmtiles_pyramid
    source = _toy_geoparquet(tmp_path)
    layers = [
        TileLayer(source=source, layer="dup", min_zoom=0, max_zoom=4),
        TileLayer(source=source, layer="dup", min_zoom=5, max_zoom=8),
    ]
    with pytest.raises(ValueError, match="layer names must be unique"):
        write_pmtiles_pyramid(layers, tmp_path / "x.pmtiles")

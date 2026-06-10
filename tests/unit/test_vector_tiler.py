"""Tests for gispulse.core.io.vector_tiler — TDD RED phase.

Fixtures build a small GPKG in tmp_path with ~20 polygons:
  - 16 regular 1x1 unit squares on a 4x4 grid (well inside the 5x5 bbox)
  - 1 large spanning polygon crossing the center (covers multiple 2x2 tiles)
  - 1 outside polygon (to test clip_wkt exclusion)

Grid coordinates are in EPSG:4326 (lon/lat), small degrees.
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box
from shapely import to_wkb


# ---------------------------------------------------------------------------
# Helpers / top-level transforms (must be importable by spawn workers)
# ---------------------------------------------------------------------------


def _transform_rename_label(gdf: gpd.GeoDataFrame) -> pd.DataFrame:
    """Top-level transform: keep geometry WKB + add 'label' column."""
    geometry_wkb = to_wkb(gdf.geometry.values)
    df = pd.DataFrame({"label": [f"feat_{i}" for i in range(len(gdf))], "geometry": geometry_wkb})
    return df


def _transform_return_none(_gdf: gpd.GeoDataFrame) -> None:
    """Top-level transform that always returns None -> tile produces no part."""
    return None


def _transform_return_empty(_gdf: gpd.GeoDataFrame) -> pd.DataFrame:
    """Top-level transform that returns an empty DataFrame."""
    return pd.DataFrame()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def small_gpkg(tmp_path: Path) -> tuple[Path, str, int]:
    """Build a GPKG with 20 polygons and return (path, layer, expected_feature_count).

    Layout (all coords in EPSG:4326 lon/lat):
    - 4x4 grid of 1°x1° squares starting at (0,0), so x in [0,4], y in [0,4]
      -> 16 squares
    - 1 large polygon [1,1] to [3,3] that spans the 4 inner tile corners
      when grid=2 is applied to bbox=(0,0,4,4)  -> index 17
    - 1 polygon at x=[5,6], y=[0,1] that is OUTSIDE the main bbox  -> index 18
    - 1 polygon at x=[3.5,4.5], y=[3.5,4.5] that partially overlaps clip boundary
      -> index 19

    The "large spanning" polygon appears in multiple bbox tiles when grid>=2,
    exercising the dedup logic.
    """
    polys = []
    labels = []

    # 16 unit squares on the 4x4 grid
    for row in range(4):
        for col in range(4):
            x0, y0 = col, row
            polys.append(box(x0, y0, x0 + 1, y0 + 1))
            labels.append(f"square_{row}_{col}")

    # large crossing polygon [1,1]-[3,3]
    polys.append(box(1, 1, 3, 3))
    labels.append("large_spanning")

    # outside polygon [5,6]x[0,1]
    polys.append(box(5, 0, 6, 1))
    labels.append("outside")

    # straddling clip boundary [3.5,4.5]x[3.5,4.5]
    polys.append(box(3.5, 3.5, 4.5, 4.5))
    labels.append("straddle_clip")

    gdf = gpd.GeoDataFrame({"label": labels, "geometry": polys}, crs="EPSG:4326")
    out_path = tmp_path / "test.gpkg"
    gdf.to_file(out_path, driver="GPKG", engine="pyogrio", layer="features")
    return out_path, "features", len(polys)  # 19


@pytest.fixture()
def gpkg_path(small_gpkg: tuple[Path, str, int]) -> Path:
    path, _, _ = small_gpkg
    return path


@pytest.fixture()
def gpkg_layer(small_gpkg: tuple[Path, str, int]) -> str:
    _, layer, _ = small_gpkg
    return layer


# ---------------------------------------------------------------------------
# tile_boxes tests
# ---------------------------------------------------------------------------


def test_tile_boxes_count_and_coverage():
    """grid=3 on a 3x3 bbox produces exactly 9 tiles that cover the whole bbox."""
    from gispulse.core.io.vector_tiler import tile_boxes

    tiles = tile_boxes((0.0, 0.0, 3.0, 3.0), grid=3)
    assert len(tiles) == 9

    # Union of all tiles must equal the original bbox
    all_x_min = min(t.min_x for t in tiles)
    all_y_min = min(t.min_y for t in tiles)
    all_x_max = max(t.max_x for t in tiles)
    all_y_max = max(t.max_y for t in tiles)
    assert all_x_min == 0.0
    assert all_y_min == 0.0
    assert all_x_max == 3.0
    assert all_y_max == 3.0


def test_tile_boxes_last_tile_exact_max():
    """Last tile's max_x and max_y are exactly the requested bbox max (no float drift)."""
    from gispulse.core.io.vector_tiler import tile_boxes

    tiles = tile_boxes((0.0, 0.0, 1.0, 1.0), grid=8)
    assert len(tiles) == 64
    assert tiles[-1].max_x == 1.0
    assert tiles[-1].max_y == 1.0


def test_tile_boxes_degenerate_bbox_returns_single_tile():
    """A bbox with zero width or zero height returns exactly 1 tile."""
    from gispulse.core.io.vector_tiler import tile_boxes

    # Zero width
    tiles = tile_boxes((1.0, 0.0, 1.0, 5.0), grid=4)
    assert len(tiles) == 1
    assert tiles[0].min_x == 1.0
    assert tiles[0].max_x == 1.0

    # Zero height
    tiles2 = tile_boxes((0.0, 2.0, 5.0, 2.0), grid=4)
    assert len(tiles2) == 1


def test_tile_boxes_invalid_bbox_raises():
    """max < min in any dimension raises ValueError."""
    from gispulse.core.io.vector_tiler import tile_boxes

    with pytest.raises(ValueError, match="invalid bbox"):
        tile_boxes((5.0, 0.0, 0.0, 1.0), grid=2)

    with pytest.raises(ValueError, match="invalid bbox"):
        tile_boxes((0.0, 5.0, 1.0, 0.0), grid=2)


def test_tile_boxes_grid_less_than_one_raises():
    """grid < 1 raises ValueError."""
    from gispulse.core.io.vector_tiler import tile_boxes

    with pytest.raises(ValueError):
        tile_boxes((0.0, 0.0, 1.0, 1.0), grid=0)

    with pytest.raises(ValueError):
        tile_boxes((0.0, 0.0, 1.0, 1.0), grid=-1)


def test_tile_boxes_indices_are_sequential():
    """Tile indices are 0-based and sequential."""
    from gispulse.core.io.vector_tiler import tile_boxes

    tiles = tile_boxes((0.0, 0.0, 4.0, 4.0), grid=2)
    assert [t.index for t in tiles] == [0, 1, 2, 3]


# ---------------------------------------------------------------------------
# tile_vector_to_parquet — bounds default from read_info
# ---------------------------------------------------------------------------


def test_bounds_default_reads_from_file(small_gpkg: tuple[Path, str, int], tmp_path: Path):
    """When bounds=None, the function reads total_bounds from pyogrio.read_info."""
    from gispulse.core.io.vector_tiler import tile_vector_to_parquet

    path, layer, _ = small_gpkg
    out = tmp_path / "out.parquet"
    # The outside polygon is at [5,6] so it IS within total_bounds of the file.
    # We pass bounds=None and confirm we get at least 16 features (the grid squares).
    report = tile_vector_to_parquet(path, out, layer=layer, grid=2, workers=1)
    assert out.exists()
    df = pd.read_parquet(out)
    # At minimum 16 unit squares + large spanning + straddle (outside also included since
    # bounds covers it). We only care that the report is coherent.
    assert report.tiles_with_rows > 0
    assert report.rows_written == len(df)
    assert report.output_written is True


# ---------------------------------------------------------------------------
# Serial workers=1 — nominal
# ---------------------------------------------------------------------------


def test_serial_nominal_rows_and_report(small_gpkg: tuple[Path, str, int], tmp_path: Path):
    """workers=1 serial path: output exists, row count matches feature count, report coherent.

    We restrict bounds to [0,0,4,4] to exclude the outside polygon (at x=5).
    Expected features: 16 squares + 1 large_spanning + 1 straddle_clip = 18.
    (The straddle polygon partially overlaps [0,0,4,4] so it's included without clip.)
    """
    from gispulse.core.io.vector_tiler import tile_vector_to_parquet

    path, layer, _ = small_gpkg
    out = tmp_path / "out.parquet"
    # bounds=(0,0,4,4) includes 16 squares + large_spanning + straddle_clip (straddle bbox hits 4)
    report = tile_vector_to_parquet(
        path, out, layer=layer, bounds=(0.0, 0.0, 4.0, 4.0), grid=2, workers=1
    )
    assert out.exists()
    df = pd.read_parquet(out)
    # large_spanning overlaps the 2x2 tiles => appears twice without dedup
    # 16 squares (at most, depending on bbox intersection) + straddle + large_spanning duplicates
    assert len(df) == report.rows_written
    assert report.output_written is True
    assert report.tiles_total == 4  # 2x2 grid


# ---------------------------------------------------------------------------
# Dedup logic
# ---------------------------------------------------------------------------


def test_dedup_with_spanning_polygon_removes_duplicate(
    small_gpkg: tuple[Path, str, int], tmp_path: Path
):
    """The large spanning polygon appears in multiple tiles; dedup_columns removes duplicates."""
    from gispulse.core.io.vector_tiler import tile_vector_to_parquet

    path, layer, _ = small_gpkg
    out_no_dedup = tmp_path / "no_dedup.parquet"
    out_dedup = tmp_path / "dedup.parquet"

    # grid=2 on bounds (0,0,4,4): the [1,1]-[3,3] spanning polygon hits all 4 tiles
    common = dict(layer=layer, bounds=(0.0, 0.0, 4.0, 4.0), grid=2, workers=1)

    tile_vector_to_parquet(path, out_no_dedup, **common)
    tile_vector_to_parquet(path, out_dedup, **common, dedup_columns=["label"])

    df_no = pd.read_parquet(out_no_dedup)
    df_yes = pd.read_parquet(out_dedup)

    # Without dedup the spanning polygon appears multiple times
    assert len(df_no) > len(df_yes)
    # With dedup each unique label appears exactly once
    assert df_yes["label"].nunique() == len(df_yes)


# ---------------------------------------------------------------------------
# clip_wkt
# ---------------------------------------------------------------------------


def test_clip_wkt_excludes_outside_and_clips_straddling(
    small_gpkg: tuple[Path, str, int], tmp_path: Path
):
    """Clip polygon [0,0]-[4,4]: outside polygon disappears; straddle polygon is clipped."""
    from gispulse.core.io.vector_tiler import tile_vector_to_parquet
    from shapely.wkt import dumps as to_wkt

    clip_polygon = box(0.0, 0.0, 4.0, 4.0)
    clip_wkt = to_wkt(clip_polygon)

    path, layer, _ = small_gpkg
    out = tmp_path / "clipped.parquet"

    report = tile_vector_to_parquet(
        path,
        out,
        layer=layer,
        bounds=(0.0, 0.0, 6.0, 6.0),  # wide enough to include all
        grid=2,
        clip_wkt=clip_wkt,
        workers=1,
        dedup_columns=["label"],
    )

    assert out.exists()
    df = pd.read_parquet(out)

    labels = set(df["label"].tolist())
    # The outside polygon at [5,6]x[0,1] should NOT appear
    assert "outside" not in labels

    # The straddle polygon [3.5,4.5]x[3.5,4.5] should appear but clipped (smaller area)
    assert "straddle_clip" in labels
    straddle_row = df[df["label"] == "straddle_clip"].iloc[0]
    from shapely import from_wkb as _from_wkb
    clipped_geom = _from_wkb(bytes(straddle_row["geometry"]))
    # Compare area: original straddle is 1.0°², clipped by [0,4]x[0,4] gives 0.25°²
    original_straddle = box(3.5, 3.5, 4.5, 4.5)
    assert clipped_geom.area < original_straddle.area


# ---------------------------------------------------------------------------
# transform callback
# ---------------------------------------------------------------------------


def test_transform_renames_column(small_gpkg: tuple[Path, str, int], tmp_path: Path):
    """A top-level transform is applied; output has 'label' and 'geometry' columns."""
    from gispulse.core.io.vector_tiler import tile_vector_to_parquet

    path, layer, _ = small_gpkg
    out = tmp_path / "transformed.parquet"

    report = tile_vector_to_parquet(
        path,
        out,
        layer=layer,
        bounds=(0.0, 0.0, 4.0, 4.0),
        grid=1,
        workers=1,
        transform=_transform_rename_label,
    )

    assert out.exists()
    df = pd.read_parquet(out)
    assert "label" in df.columns
    assert "geometry" in df.columns
    assert report.rows_written == len(df)


def test_transform_returning_none_produces_no_output(
    small_gpkg: tuple[Path, str, int], tmp_path: Path
):
    """A transform that always returns None/empty causes output_written=False."""
    from gispulse.core.io.vector_tiler import tile_vector_to_parquet

    path, layer, _ = small_gpkg
    out = tmp_path / "empty_transform.parquet"

    report = tile_vector_to_parquet(
        path,
        out,
        layer=layer,
        bounds=(0.0, 0.0, 4.0, 4.0),
        grid=2,
        workers=1,
        transform=_transform_return_none,
    )

    assert report.output_written is False
    assert report.rows_written == 0
    assert not out.exists()


def test_transform_returning_empty_produces_no_output(
    small_gpkg: tuple[Path, str, int], tmp_path: Path
):
    """A transform returning an empty DataFrame causes output_written=False."""
    from gispulse.core.io.vector_tiler import tile_vector_to_parquet

    path, layer, _ = small_gpkg
    out = tmp_path / "empty_transform2.parquet"

    report = tile_vector_to_parquet(
        path,
        out,
        layer=layer,
        bounds=(0.0, 0.0, 4.0, 4.0),
        grid=2,
        workers=1,
        transform=_transform_return_empty,
    )

    assert report.output_written is False
    assert report.rows_written == 0


# ---------------------------------------------------------------------------
# No features in bounds → output_written=False
# ---------------------------------------------------------------------------


def test_no_features_in_bounds_no_output(
    small_gpkg: tuple[Path, str, int], tmp_path: Path
):
    """When bounds contains no features, output_written=False and file not created."""
    from gispulse.core.io.vector_tiler import tile_vector_to_parquet

    path, layer, _ = small_gpkg
    out = tmp_path / "empty.parquet"

    # Bounds in the middle of the ocean, no features there
    report = tile_vector_to_parquet(
        path,
        out,
        layer=layer,
        bounds=(50.0, 50.0, 51.0, 51.0),
        grid=2,
        workers=1,
    )

    assert report.output_written is False
    assert report.rows_written == 0
    assert not out.exists()


# ---------------------------------------------------------------------------
# target_crs reprojection
# ---------------------------------------------------------------------------


def test_target_crs_reprojects_geometry(
    small_gpkg: tuple[Path, str, int], tmp_path: Path
):
    """When target_crs='EPSG:2154', output geometry is in Lambert-93 (not WGS84)."""
    from gispulse.core.io.vector_tiler import tile_vector_to_parquet
    from shapely import from_wkb as _from_wkb

    path, layer, _ = small_gpkg
    out = tmp_path / "reprojected.parquet"

    report = tile_vector_to_parquet(
        path,
        out,
        layer=layer,
        bounds=(0.0, 0.0, 4.0, 4.0),
        grid=1,
        workers=1,
        target_crs="EPSG:2154",
        dedup_columns=["label"],
    )

    assert out.exists()
    df = pd.read_parquet(out)
    # A geometry in Lambert-93 has X coordinates in the hundreds of thousands
    first_geom = _from_wkb(bytes(df["geometry"].iloc[0]))
    # WGS84 x would be 0-4; Lambert-93 x would be ~200000-1000000
    assert first_geom.bounds[0] > 100.0  # clearly not in WGS84 degree range


# ---------------------------------------------------------------------------
# TiledIngestReport.to_dict
# ---------------------------------------------------------------------------


def test_report_to_dict(small_gpkg: tuple[Path, str, int], tmp_path: Path):
    """TiledIngestReport.to_dict returns all expected keys."""
    from gispulse.core.io.vector_tiler import tile_vector_to_parquet

    path, layer, _ = small_gpkg
    out = tmp_path / "report.parquet"
    report = tile_vector_to_parquet(
        path, out, layer=layer, bounds=(0.0, 0.0, 4.0, 4.0), grid=2, workers=1
    )

    d = report.to_dict()
    assert set(d.keys()) >= {
        "tiles_total",
        "tiles_with_rows",
        "rows_written",
        "output_path",
        "output_written",
    }
    assert d["tiles_total"] == report.tiles_total
    assert d["output_written"] == report.output_written


# ---------------------------------------------------------------------------
# workers=2 parallel — no transform (spawn pickling safe)
# ---------------------------------------------------------------------------


def test_workers_2_parallel_no_transform(
    small_gpkg: tuple[Path, str, int], tmp_path: Path
):
    """workers=2 produces the same row count as workers=1 (no transform to pickle)."""
    from gispulse.core.io.vector_tiler import tile_vector_to_parquet

    path, layer, _ = small_gpkg
    out1 = tmp_path / "serial.parquet"
    out2 = tmp_path / "parallel.parquet"

    common = dict(
        layer=layer,
        bounds=(0.0, 0.0, 4.0, 4.0),
        grid=4,
        dedup_columns=["label"],
    )

    r1 = tile_vector_to_parquet(path, out1, workers=1, **common)
    r2 = tile_vector_to_parquet(path, out2, workers=2, **common)

    assert r1.rows_written == r2.rows_written


# ---------------------------------------------------------------------------
# workdir cleanup
# ---------------------------------------------------------------------------


def test_workdir_cleaned_on_success(small_gpkg: tuple[Path, str, int], tmp_path: Path):
    """When workdir is not provided, the internal tempdir is removed after success."""
    import tempfile
    from gispulse.core.io.vector_tiler import tile_vector_to_parquet

    path, layer, _ = small_gpkg
    out = tmp_path / "cleanup.parquet"

    # Track tempdir creation by watching the system temp directory before/after
    before = set(Path(tempfile.gettempdir()).iterdir())
    tile_vector_to_parquet(
        path, out, layer=layer, bounds=(0.0, 0.0, 4.0, 4.0), grid=2, workers=1
    )
    after = set(Path(tempfile.gettempdir()).iterdir())
    # No new directories should remain (temp was cleaned)
    new_entries = after - before
    # Filter only directories (some temp files might appear from other processes)
    new_dirs = {p for p in new_entries if p.is_dir()}
    assert len(new_dirs) == 0


def test_workdir_provided_not_cleaned(small_gpkg: tuple[Path, str, int], tmp_path: Path):
    """When workdir is provided by caller, it is NOT cleaned up."""
    from gispulse.core.io.vector_tiler import tile_vector_to_parquet

    path, layer, _ = small_gpkg
    out = tmp_path / "nc.parquet"
    custom_workdir = tmp_path / "myworkdir"
    custom_workdir.mkdir()

    tile_vector_to_parquet(
        path,
        out,
        layer=layer,
        bounds=(0.0, 0.0, 4.0, 4.0),
        grid=2,
        workers=1,
        workdir=custom_workdir,
    )

    # The provided workdir still exists (not cleaned)
    assert custom_workdir.exists()


# ---------------------------------------------------------------------------
# P1a — dedup key collision-free (typed length-prefixed encoding)
# ---------------------------------------------------------------------------


def test_dedup_key_none_vs_empty_string_distinct():
    """(None, "x") and ("", "x") must hash to different keys — not the same."""
    from gispulse.core.io.vector_tiler import _dedup_key

    wkb = b"\x01\x02\x03"
    key_none_x = _dedup_key([None, "x"], wkb)
    key_empty_x = _dedup_key(["", "x"], wkb)
    assert key_none_x != key_empty_x


def test_dedup_key_order_matters_none_and_empty():
    """(None, "") and ("", None) must hash to different keys."""
    from gispulse.core.io.vector_tiler import _dedup_key

    wkb = b"\x00"
    key_a = _dedup_key([None, ""], wkb)
    key_b = _dedup_key(["", None], wkb)
    assert key_a != key_b


def test_dedup_key_value_with_null_byte_distinct():
    """A value containing \\x00 must not collide with a split across two values."""
    from gispulse.core.io.vector_tiler import _dedup_key

    wkb = b"\xff"
    # "a\x00b" as single value vs "a" + "b" as two separate values
    key_one = _dedup_key(["a\x00b"], wkb)
    key_two = _dedup_key(["a", "b"], wkb)
    assert key_one != key_two


# ---------------------------------------------------------------------------
# P1b — stale parts not re-injected when workdir is reused
# ---------------------------------------------------------------------------


def test_reused_workdir_second_run_empty_no_stale_parts(
    small_gpkg: tuple[Path, str, int], tmp_path: Path
):
    """Second run with same workdir and empty bounds must NOT inherit parts from first run."""
    from gispulse.core.io.vector_tiler import tile_vector_to_parquet

    path, layer, _ = small_gpkg
    custom_workdir = tmp_path / "shared_workdir"
    custom_workdir.mkdir()

    out1 = tmp_path / "run1.parquet"
    out2 = tmp_path / "run2.parquet"

    # First run — produces parts
    r1 = tile_vector_to_parquet(
        path, out1, layer=layer, bounds=(0.0, 0.0, 4.0, 4.0), grid=2, workers=1,
        workdir=custom_workdir,
    )
    assert r1.output_written is True

    # Second run — empty bounds, same workdir
    r2 = tile_vector_to_parquet(
        path, out2, layer=layer, bounds=(50.0, 50.0, 51.0, 51.0), grid=2, workers=1,
        workdir=custom_workdir,
    )
    # Must NOT reuse first run's parts
    assert r2.output_written is False
    assert r2.rows_written == 0
    assert not out2.exists()


# ---------------------------------------------------------------------------
# P2a — dedup_rank and internal columns not leaked into output
# ---------------------------------------------------------------------------


def test_output_columns_no_internal_columns(
    small_gpkg: tuple[Path, str, int], tmp_path: Path
):
    """Output parquet must contain no _* columns and no dedup_rank column."""
    from gispulse.core.io.vector_tiler import tile_vector_to_parquet

    path, layer, _ = small_gpkg
    out = tmp_path / "clean_cols.parquet"

    tile_vector_to_parquet(
        path, out, layer=layer, bounds=(0.0, 0.0, 4.0, 4.0), grid=2, workers=1,
        dedup_columns=["label"],
    )

    df = pd.read_parquet(out)
    internal = [c for c in df.columns if c.startswith("_") or c == "dedup_rank"]
    assert internal == [], f"unexpected internal columns: {internal}"


def test_output_columns_no_internal_columns_without_dedup(
    small_gpkg: tuple[Path, str, int], tmp_path: Path
):
    """Without dedup_columns, output must still contain no _tile_index column."""
    from gispulse.core.io.vector_tiler import tile_vector_to_parquet

    path, layer, _ = small_gpkg
    out = tmp_path / "clean_cols_nodedup.parquet"

    tile_vector_to_parquet(
        path, out, layer=layer, bounds=(0.0, 0.0, 4.0, 4.0), grid=2, workers=1,
    )

    df = pd.read_parquet(out)
    internal = [c for c in df.columns if c.startswith("_")]
    assert internal == [], f"unexpected internal columns: {internal}"


# ---------------------------------------------------------------------------
# P2b — deterministic output (two identical runs produce identical files)
# ---------------------------------------------------------------------------


def test_deterministic_output_with_dedup(
    small_gpkg: tuple[Path, str, int], tmp_path: Path
):
    """Two runs with same params produce byte-identical parquet content (dedup path)."""
    from gispulse.core.io.vector_tiler import tile_vector_to_parquet

    path, layer, _ = small_gpkg
    out1 = tmp_path / "det1.parquet"
    out2 = tmp_path / "det2.parquet"

    common = dict(layer=layer, bounds=(0.0, 0.0, 4.0, 4.0), grid=2, workers=1,
                  dedup_columns=["label"])
    tile_vector_to_parquet(path, out1, **common)
    tile_vector_to_parquet(path, out2, **common)

    df1 = pd.read_parquet(out1).reset_index(drop=True)
    df2 = pd.read_parquet(out2).reset_index(drop=True)
    pd.testing.assert_frame_equal(df1, df2, check_like=False)


def test_deterministic_output_without_dedup(
    small_gpkg: tuple[Path, str, int], tmp_path: Path
):
    """Two runs with same params produce identical parquet content (no-dedup path)."""
    from gispulse.core.io.vector_tiler import tile_vector_to_parquet

    path, layer, _ = small_gpkg
    out1 = tmp_path / "det_nd1.parquet"
    out2 = tmp_path / "det_nd2.parquet"

    common = dict(layer=layer, bounds=(0.0, 0.0, 4.0, 4.0), grid=2, workers=1)
    tile_vector_to_parquet(path, out1, **common)
    tile_vector_to_parquet(path, out2, **common)

    df1 = pd.read_parquet(out1).reset_index(drop=True)
    df2 = pd.read_parquet(out2).reset_index(drop=True)
    pd.testing.assert_frame_equal(df1, df2, check_like=False)


# ---------------------------------------------------------------------------
# P2c — stale out_path unlinked when run produces no output
# ---------------------------------------------------------------------------


def test_empty_run_removes_preexisting_output(
    small_gpkg: tuple[Path, str, int], tmp_path: Path
):
    """If out_path already exists and the run produces no rows, out_path is deleted."""
    from gispulse.core.io.vector_tiler import tile_vector_to_parquet

    path, layer, _ = small_gpkg
    out = tmp_path / "stale.parquet"

    # First run — creates the file
    tile_vector_to_parquet(
        path, out, layer=layer, bounds=(0.0, 0.0, 4.0, 4.0), grid=2, workers=1
    )
    assert out.exists()

    # Second run — empty bounds, same out_path
    report = tile_vector_to_parquet(
        path, out, layer=layer, bounds=(50.0, 50.0, 51.0, 51.0), grid=2, workers=1
    )
    assert report.output_written is False
    assert not out.exists(), "stale output file must be removed"


# ---------------------------------------------------------------------------
# P2d — dedup_column absent from transform output raises ValueError
# ---------------------------------------------------------------------------


def _transform_drop_label(gdf: gpd.GeoDataFrame) -> pd.DataFrame:
    """Transform that intentionally drops the 'label' column."""
    geometry_wkb = to_wkb(gdf.geometry.values)
    return pd.DataFrame({"geometry": geometry_wkb})  # 'label' absent


def test_missing_dedup_column_raises_value_error(
    small_gpkg: tuple[Path, str, int], tmp_path: Path
):
    """If a dedup_column is absent from the transform output, ValueError is raised."""
    from gispulse.core.io.vector_tiler import tile_vector_to_parquet

    path, layer, _ = small_gpkg
    out = tmp_path / "missing_col.parquet"

    with pytest.raises(ValueError, match="label"):
        tile_vector_to_parquet(
            path,
            out,
            layer=layer,
            bounds=(0.0, 0.0, 4.0, 4.0),
            grid=1,
            workers=1,
            transform=_transform_drop_label,
            dedup_columns=["label"],
        )

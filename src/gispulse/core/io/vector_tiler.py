"""Bounded vector tiler — ingest parallèle par tuiles bbox.

Reads a large vector file (GPKG, etc.) by dividing its bounding box into an
NxN grid of non-overlapping tiles.  Each tile is read independently through
pyogrio, keeping memory bounded per worker.  Parts are combined and deduplicated
by DuckDB.

Public API::

    from gispulse.core.io.vector_tiler import (
        TileBox,
        TiledIngestReport,
        tile_boxes,
        tile_vector_to_parquet,
    )

Notes
-----
* Workers do **not** use DuckDB — each writes a pandas parquet part.
* Empty tiles produce no file (the combine glob only sees non-empty parts).
* Serial mode (workers=1) runs without ProcessPoolExecutor for testability.
* When ``transform`` is used with workers > 1, it must be a **top-level
  importable function** — spawn pickle cannot capture closures or lambdas.
"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
from collections.abc import Callable, Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TileBox:
    """A single grid cell in the tiling decomposition."""

    index: int
    min_x: float
    min_y: float
    max_x: float
    max_y: float

    def as_bbox(self) -> tuple[float, float, float, float]:
        return self.min_x, self.min_y, self.max_x, self.max_y


@dataclass(frozen=True)
class TiledIngestReport:
    """Summary of a tile_vector_to_parquet run."""

    tiles_total: int
    tiles_with_rows: int
    rows_written: int
    output_path: str
    output_written: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "tiles_total": self.tiles_total,
            "tiles_with_rows": self.tiles_with_rows,
            "rows_written": self.rows_written,
            "output_path": self.output_path,
            "output_written": self.output_written,
        }


# ---------------------------------------------------------------------------
# tile_boxes
# ---------------------------------------------------------------------------


def tile_boxes(
    bounds: tuple[float, float, float, float],
    grid: int,
) -> list[TileBox]:
    """Decompose *bounds* into a grid of TileBox objects.

    Parameters
    ----------
    bounds:
        (min_x, min_y, max_x, max_y).  max must be >= min in each dimension.
    grid:
        Number of cells per axis (grid x grid total).  Must be >= 1.

    Returns
    -------
    list[TileBox]
        Ordered list (row-major).  Last row and column are always capped at
        ``max_x``/``max_y`` to avoid floating-point over-run.

    Raises
    ------
    ValueError
        If max < min in any dimension, or if grid < 1.
    """
    if grid < 1:
        raise ValueError(f"grid must be >= 1, got {grid!r}")

    min_x, min_y, max_x, max_y = bounds
    if max_x < min_x or max_y < min_y:
        raise ValueError(f"invalid bbox {bounds!r}: max must be >= min in each dimension")

    # Degenerate bbox → single tile
    if max_x == min_x or max_y == min_y:
        return [TileBox(0, min_x, min_y, max_x, max_y)]

    width = (max_x - min_x) / grid
    height = (max_y - min_y) / grid
    tiles: list[TileBox] = []
    for row in range(grid):
        tile_min_y = min_y + row * height
        tile_max_y = max_y if row == grid - 1 else min_y + (row + 1) * height
        for col in range(grid):
            tile_min_x = min_x + col * width
            tile_max_x = max_x if col == grid - 1 else min_x + (col + 1) * width
            tiles.append(
                TileBox(
                    len(tiles),
                    tile_min_x,
                    tile_min_y,
                    tile_max_x,
                    tile_max_y,
                )
            )
    return tiles


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _sql_string_literal(value: object) -> str:
    """Return *value* as a single-quoted SQL string literal (quotes doubled)."""
    return "'" + str(value).replace("'", "''") + "'"


def _dedup_key(
    column_values: Sequence[Any],
    geometry_wkb: bytes,
) -> str:
    """Blake2b(16) hash of dedup column values + WKB geometry.

    Encoding is typed and length-prefixed to eliminate ambiguity:
    - None  → tag byte b"N"  (no payload)
    - str   → tag byte b"S" + 4-byte big-endian UTF-8 length + UTF-8 bytes
    - Other → coerced to str first
    The WKB is appended with a 4-byte big-endian length prefix.

    This prevents collisions between (None, "x") vs ("", "x"), values
    containing ``\\x00`` bytes, and reorderings of None vs "".
    """
    digest = hashlib.blake2b(digest_size=16)
    for val in column_values:
        if val is None:
            digest.update(b"N")
        else:
            encoded = (val if isinstance(val, str) else str(val)).encode("utf-8")
            digest.update(b"S")
            digest.update(len(encoded).to_bytes(4, "big"))
            digest.update(encoded)
    digest.update(len(geometry_wkb).to_bytes(4, "big"))
    digest.update(geometry_wkb)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# Worker state (used in parallel mode via module-level global)
# ---------------------------------------------------------------------------

_WORKER_STATE: "_WorkerState | None" = None


@dataclass(frozen=True)
class _WorkerState:
    src_path: str
    layer: str | None
    target_crs: str | None
    clip_wkt: str | None
    transform: Callable | None
    dedup_columns: tuple[str, ...] | None
    parts_dir: Path


def _init_tile_worker(
    src_path: str,
    layer: str | None,
    target_crs: str | None,
    clip_wkt: str | None,
    transform: Callable | None,
    dedup_columns: tuple[str, ...] | None,
    parts_dir: Path,
) -> None:
    """Initialise immutable tile-read state once per spawn worker process.

    Note: ``transform`` must be a top-level importable function when workers > 1
    (spawn pickle cannot capture closures or lambdas).
    """
    global _WORKER_STATE
    _WORKER_STATE = _WorkerState(
        src_path=src_path,
        layer=layer,
        target_crs=target_crs,
        clip_wkt=clip_wkt,
        transform=transform,
        dedup_columns=dedup_columns,
        parts_dir=Path(parts_dir),
    )


def _write_tile_part_worker(tile: TileBox) -> bool:
    """Worker entry point — called once per tile in parallel mode."""
    state = _WORKER_STATE
    if state is None:
        raise RuntimeError("vector_tiler worker was not initialised")
    part_path = state.parts_dir / f"part{tile.index:05d}.parquet"
    return _process_tile(
        tile=tile,
        src_path=state.src_path,
        layer=state.layer,
        target_crs=state.target_crs,
        clip_wkt=state.clip_wkt,
        transform=state.transform,
        dedup_columns=state.dedup_columns,
        part_path=part_path,
    )


# ---------------------------------------------------------------------------
# Core tile processing (single tile, pure logic)
# ---------------------------------------------------------------------------


def _process_tile(
    *,
    tile: TileBox,
    src_path: str,
    layer: str | None,
    target_crs: str | None,
    clip_wkt: str | None,
    transform: Callable | None,
    dedup_columns: Sequence[str] | None,
    part_path: Path,
) -> bool:
    """Read one tile from *src_path*, apply clip/transform/dedup, write parquet part.

    Returns True when the tile produced at least one row.
    """
    try:
        import pyogrio
    except ImportError as exc:
        raise RuntimeError(
            "pyogrio is required for vector tiling; install with: pip install pyogrio"
        ) from exc

    try:
        import pandas as pd
    except ImportError as exc:
        raise RuntimeError("pandas is required for vector tiling") from exc

    read_kwargs: dict[str, Any] = {"bbox": tile.as_bbox(), "fid_as_index": True}
    if layer is not None:
        read_kwargs["layer"] = layer

    gdf = pyogrio.read_dataframe(src_path, **read_kwargs)
    if gdf.empty:
        return False

    # CRS handling
    if target_crs is not None:
        if gdf.crs is None:
            gdf = gdf.set_crs(target_crs)
        elif str(gdf.crs).upper() != target_crs.upper():
            gdf = gdf.to_crs(target_crs)

    # Optional clip
    if clip_wkt is not None:
        gdf = _clip_to_wkt(gdf, clip_wkt)
        if gdf is None or gdf.empty:
            return False

    # Build output DataFrame
    if transform is not None:
        result = transform(gdf)
        if result is None or (hasattr(result, "empty") and result.empty):
            return False
        frame = result
    else:
        try:
            from shapely import to_wkb
        except ImportError as exc:
            raise RuntimeError("shapely is required for vector tiling") from exc
        geometry_wkb = to_wkb(gdf.geometry.values)
        attr_cols = {col: gdf[col].tolist() for col in gdf.columns if col != gdf.geometry.name}
        attr_cols["geometry"] = list(geometry_wkb)
        frame = pd.DataFrame(attr_cols)

    if frame.empty:
        return False

    # Add dedup key + tile index
    frame = frame.copy()
    frame["_tile_index"] = tile.index
    if dedup_columns is not None:
        # Fail fast: every dedup column must be present in the frame.
        # A missing column means the transform dropped it, which would silently
        # produce wrong dedup keys.  Note: transform must preserve dedup_columns.
        for col in dedup_columns:
            if col not in frame.columns:
                raise ValueError(
                    f"dedup column {col!r} is absent from the output frame "
                    f"(tile index {tile.index}); ensure transform preserves all dedup_columns"
                )
        keys: list[str] = []
        for _i in range(len(frame)):
            row_vals = [frame[c].iloc[_i] for c in dedup_columns]
            geom_bytes = bytes(frame["geometry"].iloc[_i])
            keys.append(_dedup_key(row_vals, geom_bytes))
        frame["_dedup_key"] = keys

    part_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(part_path, index=False)
    return True


def _clip_to_wkt(gdf: Any, clip_wkt: str) -> Any | None:
    """Clip *gdf* geometries to the polygon described by *clip_wkt*.

    Mirrors the pattern in ingest_ocsge_dept.py:
    - make_valid on the clip polygon AND on all source geometries
    - intersects mask → early-exit if nothing intersects
    - within mask → apply intersection only on geometries that cross the boundary
    - drop empty results
    """
    try:
        from shapely import intersection, intersects, is_empty, make_valid, within
        from shapely.wkt import loads
    except ImportError as exc:
        raise RuntimeError("shapely is required for clip_wkt support") from exc

    clip_geom = make_valid(loads(clip_wkt))
    geometries = make_valid(gdf.geometry.values)

    intersects_mask = intersects(geometries, clip_geom)
    if not intersects_mask.any():
        return None

    gdf = gdf.loc[intersects_mask].copy()
    geometries = geometries[intersects_mask]

    within_mask = within(geometries, clip_geom)
    clipped = geometries.copy()
    if (~within_mask).any():
        clipped[~within_mask] = intersection(geometries[~within_mask], clip_geom)

    non_empty = ~is_empty(clipped)
    if not non_empty.any():
        return None

    gdf = gdf.loc[non_empty].copy()
    gdf = gdf.set_geometry(list(clipped[non_empty]))
    return gdf


# ---------------------------------------------------------------------------
# Combine parts via DuckDB
# ---------------------------------------------------------------------------


def _combine_parts(
    parts_dir: Path,
    out_path: Path,
    *,
    dedup_columns: Sequence[str] | None,
    duckdb_memory_limit: str | None,
) -> int:
    """Glob part parquets and write deduplicated output via DuckDB.  Returns row count."""
    try:
        import duckdb
    except ImportError as exc:
        raise RuntimeError("duckdb is required to combine parquet parts") from exc

    parts_glob = str(parts_dir / "*.parquet")
    con = duckdb.connect(":memory:")
    try:
        if duckdb_memory_limit:
            safe = duckdb_memory_limit.replace("'", "''")
            con.execute(f"SET memory_limit = '{safe}'")

        src_sql = f"read_parquet({_sql_string_literal(parts_glob)}, union_by_name=true)"
        out_lit = _sql_string_literal(str(out_path))
        out_path.parent.mkdir(parents=True, exist_ok=True)

        if dedup_columns is not None:
            copy_sql = (
                f"COPY ("
                f"SELECT * EXCLUDE (_tile_index, _dedup_key, dedup_rank) FROM ("
                f"SELECT *, row_number() OVER ("
                f"PARTITION BY _dedup_key ORDER BY _tile_index, _dedup_key"
                f") AS dedup_rank FROM {src_sql}"
                f") WHERE dedup_rank = 1"
                f" ORDER BY _dedup_key"
                f") TO {out_lit} (FORMAT PARQUET)"
            )
        else:
            copy_sql = (
                f"COPY ("
                f"SELECT * EXCLUDE (_tile_index) FROM {src_sql}"
                f" ORDER BY _tile_index, geometry"
                f") TO {out_lit} (FORMAT PARQUET)"
            )

        con.execute(copy_sql)
        row = con.execute(
            f"SELECT COUNT(*) FROM read_parquet({_sql_string_literal(str(out_path))})"
        ).fetchone()
        return int(row[0]) if row else 0
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Main public function
# ---------------------------------------------------------------------------


def tile_vector_to_parquet(
    src_path: str | Path,
    out_path: str | Path,
    *,
    layer: str | None = None,
    bounds: tuple[float, float, float, float] | None = None,
    grid: int = 8,
    workers: int = 1,
    target_crs: str | None = None,
    clip_wkt: str | None = None,
    transform: Callable | None = None,
    dedup_columns: Sequence[str] | None = None,
    duckdb_memory_limit: str | None = None,
    workdir: str | Path | None = None,
) -> TiledIngestReport:
    """Ingest a large vector file by tiling its bounding box, then combine via DuckDB.

    Parameters
    ----------
    src_path:
        Path to the source vector file (GPKG, FlatGeobuf, …).
    out_path:
        Path where the final deduplicated parquet is written.
    layer:
        Layer name inside the source file (None = default layer).
    bounds:
        (min_x, min_y, max_x, max_y) of the tiling grid.  Defaults to the
        ``total_bounds`` reported by ``pyogrio.read_info``.
    grid:
        Number of tiles per axis (grid x grid total).
    workers:
        Number of parallel worker processes.  workers=1 uses a direct loop
        (no ProcessPoolExecutor) for testability.
    target_crs:
        Reproject geometries to this CRS (e.g. ``"EPSG:4326"``).  None keeps
        the source CRS.
    clip_wkt:
        Optional WKT polygon (same CRS as *target_crs* or source CRS if
        target_crs is None).  Features outside the clip are dropped; features
        that straddle the boundary are clipped.
    transform:
        Optional ``(GeoDataFrame) -> pandas.DataFrame | None`` callback applied
        after clip/CRS conversion.  The returned DataFrame must contain a
        ``geometry`` column with WKB bytes.  Return ``None`` or an empty
        DataFrame to suppress the tile part.

        **Must be a top-level importable function when workers > 1** — spawn
        pickle cannot capture closures or lambdas.  **Must preserve all columns
        listed in** ``dedup_columns`` — a missing dedup column raises
        ``ValueError`` at tile processing time.
    dedup_columns:
        Column names used (together with the geometry WKB) to build a blake2b
        dedup key.  Rows with the same key in different tiles are deduplicated
        during the DuckDB combine step (lowest tile index wins).
    duckdb_memory_limit:
        DuckDB ``memory_limit`` setting applied during the combine step
        (e.g. ``"4GB"``).
    workdir:
        Directory for temporary part files.  If None, a tmpdir is created and
        cleaned up automatically (even on failure).  If provided, it is **not**
        cleaned up.

    Returns
    -------
    TiledIngestReport
    """
    try:
        import pyogrio
    except ImportError as exc:
        raise RuntimeError(
            "pyogrio is required for vector tiling; install with: pip install pyogrio"
        ) from exc

    src_path = Path(src_path)
    out_path = Path(out_path)

    # Resolve bounds
    if bounds is None:
        read_info_kwargs: dict[str, Any] = {}
        if layer is not None:
            read_info_kwargs["layer"] = layer
        info = pyogrio.read_info(str(src_path), **read_info_kwargs)
        raw_bounds = info.get("total_bounds")
        if raw_bounds is None:
            raise ValueError(f"Could not determine bounds from {src_path}")
        bounds = tuple(float(v) for v in raw_bounds)  # type: ignore[assignment]

    tiles = tile_boxes(bounds, grid)
    log.info(
        "vector_tiler.start",
        src=str(src_path),
        grid=grid,
        tiles=len(tiles),
        workers=workers,
    )

    # Manage workdir.
    # The parts directory is always a fresh mkdtemp so that re-runs with the
    # same caller-provided workdir never see parts from a previous run.
    # The parts dir is always cleaned in finally; the caller-provided parent
    # workdir is never touched.
    if workdir is None:
        _parent_dir: Path | None = None
    else:
        _parent_dir = Path(workdir)  # type: ignore[arg-type]
        _parent_dir.mkdir(parents=True, exist_ok=True)

    parts_dir = Path(tempfile.mkdtemp(prefix="parts-", dir=_parent_dir))

    dedup_tuple = tuple(dedup_columns) if dedup_columns is not None else None

    try:
        tiles_with_rows = 0

        if workers == 1:
            # Serial path — no pool
            for tile in tiles:
                part_path = parts_dir / f"part{tile.index:05d}.parquet"
                had_rows = _process_tile(
                    tile=tile,
                    src_path=str(src_path),
                    layer=layer,
                    target_crs=target_crs,
                    clip_wkt=clip_wkt,
                    transform=transform,
                    dedup_columns=dedup_columns,
                    part_path=part_path,
                )
                if had_rows:
                    tiles_with_rows += 1
        else:
            # Parallel path — spawn workers
            import multiprocessing as _mp

            executor = ProcessPoolExecutor(
                max_workers=workers,
                mp_context=_mp.get_context("spawn"),
                initializer=_init_tile_worker,
                initargs=(
                    str(src_path),
                    layer,
                    target_crs,
                    clip_wkt,
                    transform,
                    dedup_tuple,
                    parts_dir,
                ),
            )
            futures = {executor.submit(_write_tile_part_worker, tile): tile for tile in tiles}
            try:
                for future in as_completed(futures):
                    tile = futures[future]
                    try:
                        had_rows = future.result()
                        if had_rows:
                            tiles_with_rows += 1
                    except Exception as exc:
                        for pending in futures:
                            if pending is not future:
                                pending.cancel()
                        log.error(
                            "vector_tiler.tile_failure",
                            tile_index=tile.index,
                            bbox=tile.as_bbox(),
                            error=str(exc),
                        )
                        raise
            finally:
                executor.shutdown(wait=True, cancel_futures=True)

        # Combine
        part_files = list(parts_dir.glob("*.parquet"))
        if not part_files:
            # Remove any stale output from a previous run so callers cannot
            # accidentally consume it.
            out_path.unlink(missing_ok=True)
            log.info("vector_tiler.combine_done", rows=0, output_written=False)
            return TiledIngestReport(
                tiles_total=len(tiles),
                tiles_with_rows=tiles_with_rows,
                rows_written=0,
                output_path=str(out_path),
                output_written=False,
            )

        rows = _combine_parts(
            parts_dir,
            out_path,
            dedup_columns=dedup_columns,
            duckdb_memory_limit=duckdb_memory_limit,
        )
        log.info("vector_tiler.combine_done", rows=rows, output_written=True)
        return TiledIngestReport(
            tiles_total=len(tiles),
            tiles_with_rows=tiles_with_rows,
            rows_written=rows,
            output_path=str(out_path),
            output_written=True,
        )

    finally:
        # parts_dir is always our own mkdtemp — always clean it up
        shutil.rmtree(parts_dir, ignore_errors=True)

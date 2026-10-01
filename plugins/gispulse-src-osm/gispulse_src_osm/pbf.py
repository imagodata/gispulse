"""DuckDB-backed helpers for local OSM PBF extracts.

The source plugin declares where the PBF comes from; this module owns the
generic PBF-to-GeoDataFrame extraction. Domain-specific harmonisation, for
example mapping ``surface=*`` to a cost model, remains in consumers.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np

log = logging.getLogger(__name__)

_SOURCE_CRS = "EPSG:4326"
_DEFAULT_TAGS = ("highway", "surface", "footway")
_TAG_RE = re.compile(r"^[A-Za-z0-9_:-]+$")
_RESERVED_COLUMNS = frozenset({"id", "geometry"})

# Qualified so cleanup can never drop a caller's persistent table of the same name.
_T_WAYS = "temp.main._gp_pbf_ways"
_T_NODES = "temp.main._gp_pbf_nodes"


class OsmPbfReadError(ValueError):
    """Structured error raised when an OSM PBF cannot be read."""

    def __init__(self, *, code: str, context: str, recovery: str) -> None:
        self.code = code
        self.context = context
        self.recovery = recovery
        super().__init__({"code": code, "context": context, "recovery": recovery})


def read_pbf_roads(
    pbf_path: str | Path,
    *,
    tags: Sequence[str] = _DEFAULT_TAGS,
    target_crs: str | None = "EPSG:31370",
    connection_factory: Callable[[], Any] | None = None,
) -> gpd.GeoDataFrame:
    """Read road ways and selected OSM tags from a local ``.osm.pbf`` extract.

    Args:
        pbf_path: Local PBF file path.
        tags: OSM tag keys to flatten into columns. ``highway`` is always
            included because it defines the road-way filter.
        target_crs: Optional CRS for the returned GeoDataFrame, applied in DuckDB
            with ``always_xy`` (lon/lat axis order). ``None`` keeps the native OSM
            WGS84 geometry.
        connection_factory: Test hook returning a DuckDB connection with ``spatial``
            loaded. Not safe for concurrent calls on the same database.

    Way geometry is rebuilt from node coordinates (``ST_ReadOSM`` only returns node
    ids for ways). Dropped ways are counted, never silent: ``gdf.attrs["diagnostics"]``
    holds ``ways_total``, ``ways_kept``, ``ways_dropped_incomplete`` (< 2 resolved
    nodes), ``ways_dropped_degenerate`` (all resolved nodes at one location) and
    ``ways_partially_resolved`` (kept, built from a subset of their nodes: a straight
    segment bridges the missing ones). ``gdf.attrs["partially_resolved_ids"]`` lists
    the latter. A warning is logged when anything is dropped or partial.

    Returns:
        GeoDataFrame sorted by ``id`` with ``id`` (int64), ``highway``, one string
        column per requested tag (missing tag -> NA), and LineString geometry in
        ``target_crs`` (EPSG:4326 when None).

    Raises:
        FileNotFoundError: If ``pbf_path`` does not exist.
        OsmPbfReadError: If tag keys or the target CRS are invalid, the extract has
            duplicate ids, DuckDB runs out of memory, or DuckDB/spatial cannot read it.
    """
    path = Path(pbf_path)
    if not path.exists():
        raise FileNotFoundError(f"OSM PBF file not found: {path}")

    tag_keys = _normalise_tags(tags)
    target = _normalise_target_crs(target_crs)

    owns_connection = connection_factory is None
    conn: Any = None
    try:
        if connection_factory is None:
            conn = _open_connection()
        else:
            conn = connection_factory()
        ways, coords = _read_ways_and_coords(conn, str(path), tag_keys, target)
    except OsmPbfReadError:
        raise
    except Exception as exc:
        raise _read_error(path, exc) from exc
    finally:
        if owns_connection and conn is not None:
            conn.close()

    gdf, diagnostics, partial_ids = _assemble_lines(
        ways, coords, tag_keys, crs=target or _SOURCE_CRS
    )
    gdf.attrs["diagnostics"] = diagnostics
    gdf.attrs["partially_resolved_ids"] = partial_ids
    dropped = diagnostics["ways_dropped_incomplete"] + diagnostics["ways_dropped_degenerate"]
    if dropped or diagnostics["ways_partially_resolved"]:
        log.warning(
            "OSM PBF %s: %d/%d highway ways dropped (%d with <2 resolved nodes, "
            "%d degenerate), %d kept with some unresolved nodes",
            path.name,
            dropped,
            diagnostics["ways_total"],
            diagnostics["ways_dropped_incomplete"],
            diagnostics["ways_dropped_degenerate"],
            diagnostics["ways_partially_resolved"],
        )
    return gdf


def _open_connection() -> Any:
    try:
        import duckdb
    except ImportError as exc:  # pragma: no cover - dependency is installed in gispulse
        raise OsmPbfReadError(
            code="OSM_PBF_DUCKDB_MISSING",
            context="duckdb is required to read OSM PBF extracts",
            recovery="install gispulse with its DuckDB dependency, then retry",
        ) from exc

    from gispulse.core.duckdb_limits import configure_duckdb_limits

    conn = duckdb.connect(":memory:")
    try:
        configure_duckdb_limits(conn)
        conn.execute("INSTALL spatial; LOAD spatial;")
    except Exception as exc:
        conn.close()
        raise OsmPbfReadError(
            code="OSM_PBF_SPATIAL_UNAVAILABLE",
            context=f"cannot set up DuckDB with the spatial extension: {exc}",
            recovery=(
                "check GISPULSE_DUCKDB_* settings; the first INSTALL spatial needs network "
                "access (or a pre-installed extension)"
            ),
        ) from exc
    return conn


def _read_error(path: Path, exc: Exception) -> OsmPbfReadError:
    if isinstance(exc, MemoryError) or type(exc).__name__ == "OutOfMemoryException":
        return OsmPbfReadError(
            code="OSM_PBF_OUT_OF_MEMORY",
            context=f"ran out of memory reading OSM PBF roads from {path}: {exc}",
            recovery=(
                "raise GISPULSE_DUCKDB_MEMORY_LIMIT, set GISPULSE_DUCKDB_TEMP_DIRECTORY "
                "so DuckDB can spill, or lower GISPULSE_DUCKDB_THREADS"
            ),
        )
    return OsmPbfReadError(
        code="OSM_PBF_READ_FAILED",
        context=f"failed to read OSM PBF roads from {path}: {exc}",
        recovery="verify the file is a valid .osm.pbf and DuckDB spatial can load ST_ReadOSM",
    )


def _normalise_target_crs(target_crs: Any) -> str | None:
    """Return the CRS string to transform to, or ``None`` when WGS84 is kept as is."""
    if target_crs is None or target_crs == "":
        return None
    try:
        from pyproj import CRS

        crs = CRS.from_user_input(target_crs)
    except Exception as exc:
        raise OsmPbfReadError(
            code="OSM_PBF_CRS_INVALID",
            context=f"unknown target CRS: {target_crs!r}",
            recovery="pass an authority string such as 'EPSG:31370', or None to keep EPSG:4326",
        ) from exc
    if crs == CRS.from_user_input(_SOURCE_CRS):
        return None
    return crs.to_string()


def _normalise_tags(tags: Sequence[str]) -> tuple[str, ...]:
    keys = ["highway", *list(tags)]
    normalised: list[str] = []
    for raw in keys:
        key = raw.strip()
        if not key or not _TAG_RE.fullmatch(key):
            raise OsmPbfReadError(
                code="OSM_PBF_TAG_INVALID",
                context=f"unsafe OSM tag key: {raw!r}",
                recovery="use plain OSM tag keys containing only letters, digits, _, :, or -",
            )
        if key in _RESERVED_COLUMNS:
            raise OsmPbfReadError(
                code="OSM_PBF_TAG_INVALID",
                context=f"OSM tag key {raw!r} collides with an output column",
                recovery=f"do not request {sorted(_RESERVED_COLUMNS)} as tag keys",
            )
        if key not in normalised:
            normalised.append(key)
    return tuple(normalised)


def _read_ways_and_coords(
    conn: Any, path: str, tags: Sequence[str], target_crs: str | None
) -> tuple[Any, dict[str, np.ndarray]]:
    """Extract highway ways and their resolved node coordinates from ``ST_ReadOSM``.

    ``ST_ReadOSM`` is a raw reader (``kind, id, tags, refs, lat, lon, ...``): a way
    carries node ids only. Passes: (1) highway ways with the requested tags only,
    (2) only the nodes those ways reference, projected once per node, (3) one
    ``(way_id, x, y)`` row per resolved ref, sorted by way then node position. The
    PBF is scanned twice; every DuckDB step is a scan, a hash join or a sort, which
    stay within DuckDB's ``memory_limit`` (the sort spills to disk). Process RSS is
    higher: ``ST_ReadOSM`` read buffers and the numpy/shapely assembly are not counted
    by that limit (Belgium: 2.0 GB peak RSS).
    """
    _drop_temp_tables(conn)
    try:
        conn.execute(_ways_sql(tags), [path])
        with _build_hash_on_refs(conn):
            params = [path] if target_crs is None else [target_crs, path]
            conn.execute(_nodes_sql(projected=target_crs is not None), params)
        dup_ways, dup_nodes = conn.execute(_DUPLICATES_SQL).fetchone()
        if dup_ways or dup_nodes:
            raise OsmPbfReadError(
                code="OSM_PBF_DUPLICATE_IDS",
                context=f"{path} repeats {dup_ways} way id(s) and {dup_nodes} node id(s)",
                recovery="deduplicate the extract (e.g. osmium merge / osmium sort) first",
            )
        ways = conn.execute(_ways_frame_sql(tags)).fetchdf()
        coords = conn.execute(_COORDS_SQL).fetchnumpy()
    except BaseException:
        _drop_temp_tables(conn, quiet=True)  # never mask the original error
        raise
    _drop_temp_tables(conn)
    return ways, coords


def _drop_temp_tables(conn: Any, *, quiet: bool = False) -> None:
    for table in (_T_WAYS, _T_NODES):
        try:
            conn.execute(f"DROP TABLE IF EXISTS {table}")
        except Exception:
            if not quiet:
                raise


@contextmanager
def _build_hash_on_refs(conn: Any) -> Iterator[None]:
    """Keep the node semi-join hash table on the (small) referenced-ids side.

    DuckDB's ``build_side_probe_side`` optimizer flips it onto the ``ST_ReadOSM`` node
    scan, hashing every node of the file: 3.2 GB instead of 0.8 GB peak RSS on the
    Flanders extract. ``disabled_optimizers`` is database-global, so it is restored
    right after the query; DuckDB versions without that optimizer just run the query
    unchanged (correct, but hungrier).
    """
    previous: str | None = None
    try:
        previous = str(conn.execute("SELECT current_setting('disabled_optimizers')").fetchone()[0])
        disabled = ",".join(filter(None, [previous, "build_side_probe_side"]))
        conn.execute(f"SET disabled_optimizers = '{_sql_string(disabled)}'")
    except Exception:
        previous = None
    try:
        yield
    finally:
        if previous is not None:
            try:
                conn.execute(f"SET disabled_optimizers = '{_sql_string(previous)}'")
            except Exception:  # e.g. aborted caller transaction: keep the original error
                log.warning(
                    "could not restore DuckDB disabled_optimizers=%r; "
                    "build_side_probe_side stays disabled on this database",
                    previous,
                )


def _ways_sql(tags: Sequence[str]) -> str:
    # Tags are stored as t0..tn and renamed in pandas: no tag key can collide with an
    # internal column, and DuckDB's case-insensitive aliases never merge two keys.
    tag_cols = ", ".join(
        f"map_extract(tags, '{_sql_string(tag)}')[1]::VARCHAR AS t{i}" for i, tag in enumerate(tags)
    )
    return f"""
    CREATE TEMP TABLE {_T_WAYS} AS
    SELECT id, {tag_cols}, refs
    FROM ST_ReadOSM(?)
    WHERE kind = 'way'
      AND map_extract(tags, 'highway')[1] IS NOT NULL
    """


def _nodes_sql(*, projected: bool) -> str:
    # always_xy: EPSG:4326 is (lat, lon) in authority order; without it PROJ reads our
    # (lon, lat) points swapped and the whole layer lands thousands of km away.
    point = (
        "ST_Transform(ST_Point(lon, lat), 'EPSG:4326', ?, always_xy := true)"
        if projected
        else "ST_Point(lon, lat)"
    )
    return f"""
    CREATE TEMP TABLE {_T_NODES} AS
    SELECT id, ST_X(p) AS x, ST_Y(p) AS y
    FROM (
        SELECT id, {point} AS p
        FROM ST_ReadOSM(?)
        WHERE kind = 'node'
          AND id IN (SELECT DISTINCT unnest(refs) FROM {_T_WAYS})
    )
    """


_DUPLICATES_SQL = f"""
    SELECT
        (SELECT count(*) - count(DISTINCT id) FROM {_T_WAYS}),
        (SELECT count(*) - count(DISTINCT id) FROM {_T_NODES})
"""


def _ways_frame_sql(tags: Sequence[str]) -> str:
    tag_cols = ", ".join(f"t{i}" for i in range(len(tags)))
    return f"SELECT id, {tag_cols}, len(refs) AS n_refs FROM {_T_WAYS} ORDER BY id ASC"


# Explicit ASC: a caller connection may have SET default_order = 'DESC'.
_COORDS_SQL = f"""
    SELECT w.id AS way_id, n.x, n.y
    FROM (
        SELECT id, unnest(refs) AS ref, generate_subscripts(refs, 1) AS ord
        FROM {_T_WAYS}
    ) AS w
    JOIN {_T_NODES} AS n ON n.id = w.ref
    ORDER BY w.id ASC, w.ord ASC
"""


def _assemble_lines(
    ways: Any, coords: dict[str, np.ndarray], tags: Sequence[str], *, crs: str
) -> tuple[gpd.GeoDataFrame, dict[str, int], list[int]]:
    """Build one LineString per way from ``(way_id, x, y)`` rows sorted by way, ord."""
    import shapely

    way_ids = np.asarray(coords["way_id"], dtype=np.int64)
    xy = np.column_stack([np.asarray(coords["x"]), np.asarray(coords["y"])])

    if len(way_ids):
        starts = np.flatnonzero(np.r_[True, way_ids[1:] != way_ids[:-1]])
        counts = np.diff(np.r_[starts, len(way_ids)])
        same_x = np.minimum.reduceat(xy[:, 0], starts) == np.maximum.reduceat(xy[:, 0], starts)
        same_y = np.minimum.reduceat(xy[:, 1], starts) == np.maximum.reduceat(xy[:, 1], starts)
        degenerate = (counts >= 2) & same_x & same_y
        keep = (counts >= 2) & ~degenerate
        group_ids = way_ids[starts]
    else:
        counts = np.zeros(0, dtype=np.int64)
        degenerate = keep = np.zeros(0, dtype=bool)
        group_ids = np.zeros(0, dtype=np.int64)

    kept_ids = group_ids[keep]
    if len(kept_ids):
        lines = shapely.linestrings(
            xy[np.repeat(keep, counts)],
            indices=np.repeat(np.arange(len(kept_ids)), counts[keep]),
        )
    else:
        lines = np.empty(0, dtype=object)

    # ways is sorted by unique id (duplicates are rejected upstream).
    rows = np.searchsorted(ways["id"].to_numpy(dtype=np.int64), kept_ids)
    out = ways.iloc[rows].reset_index(drop=True)
    partial = counts[keep] < out["n_refs"].to_numpy()
    out = out.drop(columns="n_refs").rename(columns={f"t{i}": tag for i, tag in enumerate(tags)})
    out[list(tags)] = out[list(tags)].astype("string")  # same dtype when empty or all-null
    gdf = gpd.GeoDataFrame(out, geometry=gpd.GeoSeries(lines, crs=crs), crs=crs)
    gdf["id"] = gdf["id"].astype("int64")

    diagnostics = {
        "ways_total": len(ways),
        "ways_kept": len(kept_ids),
        "ways_dropped_incomplete": len(ways) - len(kept_ids) - int(degenerate.sum()),
        "ways_dropped_degenerate": int(degenerate.sum()),
        "ways_partially_resolved": int(partial.sum()),
    }
    return gdf, diagnostics, kept_ids[partial].tolist()


def _sql_string(value: str) -> str:
    return value.replace("'", "''")


__all__ = ["OsmPbfReadError", "read_pbf_roads"]

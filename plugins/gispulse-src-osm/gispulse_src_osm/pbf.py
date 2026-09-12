"""DuckDB-backed helpers for local OSM PBF extracts.

The source plugin declares where the PBF comes from; this module owns the
generic PBF-to-GeoDataFrame extraction. Domain-specific harmonisation, for
example mapping ``surface=*`` to a cost model, remains in consumers.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import geopandas as gpd

_SOURCE_CRS = "EPSG:4326"
_DEFAULT_TAGS = ("highway", "surface", "footway")
_TAG_RE = re.compile(r"^[A-Za-z0-9_:-]+$")


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
        target_crs: Optional CRS for the returned GeoDataFrame. ``None`` keeps
            the native OSM WGS84 geometry.
        connection_factory: Test hook returning a DuckDB-like connection.

    Returns:
        GeoDataFrame with ``id``, one column per requested tag, and line geometry.

    Raises:
        FileNotFoundError: If ``pbf_path`` does not exist.
        OsmPbfReadError: If tag keys are unsafe or DuckDB/spatial cannot read PBF.
    """
    path = Path(pbf_path)
    if not path.exists():
        raise FileNotFoundError(f"OSM PBF file not found: {path}")

    tag_keys = _normalise_tags(tags)
    sql = _roads_sql(tag_keys)

    owns_connection = connection_factory is None
    if connection_factory is None:
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
        configure_duckdb_limits(conn)
        conn.execute("INSTALL spatial; LOAD spatial;")
    else:
        conn = connection_factory()

    try:
        frame = conn.execute(sql, [str(path)]).fetchdf()
    except Exception as exc:
        raise OsmPbfReadError(
            code="OSM_PBF_READ_FAILED",
            context=f"failed to read OSM PBF roads from {path}",
            recovery="verify the file is a valid .osm.pbf and DuckDB spatial can load ST_ReadOSM",
        ) from exc
    finally:
        if owns_connection:
            conn.close()

    return _frame_to_gdf(frame, target_crs=target_crs)


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
        if key not in normalised:
            normalised.append(key)
    return tuple(normalised)


def _roads_sql(tags: Sequence[str]) -> str:
    tag_select = ",\n        ".join(
        f"tags['{_sql_string(tag)}']::VARCHAR AS \"{_sql_identifier(tag)}\"" for tag in tags
    )
    return f"""
    SELECT
        id,
        {tag_select},
        ST_AsWKB(geom) AS geometry
    FROM ST_ReadOSM(?)
    WHERE kind = 'way'
      AND tags['highway'] IS NOT NULL
      AND geom IS NOT NULL
    """


def _frame_to_gdf(frame: Any, *, target_crs: str | None) -> gpd.GeoDataFrame:
    if "geometry" not in frame:
        raise OsmPbfReadError(
            code="OSM_PBF_GEOMETRY_MISSING",
            context="DuckDB ST_ReadOSM result did not include a geometry column",
            recovery="keep ST_AsWKB(geom) in the PBF road extraction query",
        )

    data = frame.copy()
    data["geometry"] = data["geometry"].map(_load_wkb)
    gdf = gpd.GeoDataFrame(data, geometry="geometry", crs=_SOURCE_CRS)
    gdf = gdf.loc[~(gdf.geometry.is_empty | gdf.geometry.isna())].reset_index(drop=True)
    if target_crs and target_crs != _SOURCE_CRS:
        gdf = gdf.to_crs(target_crs)
    return gdf


def _load_wkb(value: Any) -> object:
    from shapely import wkb

    if value is None:
        return None
    if isinstance(value, memoryview):
        return wkb.loads(value.tobytes())
    return wkb.loads(bytes(value))


def _sql_identifier(value: str) -> str:
    return value.replace('"', '""')


def _sql_string(value: str) -> str:
    return value.replace("'", "''")


__all__ = ["OsmPbfReadError", "read_pbf_roads"]

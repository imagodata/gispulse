"""SQL builders for the single-layer 1:1 geometry capabilities (ELT Lot 3, #246).

One builder per Tier-1 geometry capability whose operation is a pure
*per-feature* geometry transform — ``centroid``, ``boundary``,
``make_valid``, ``convex_hull``, ``envelope``, ``concave_hull`` — plus
``area_length`` (geometry untouched, two metric columns added).

Each builder produces a ``SELECT`` that rewrites the geometry column
in place (under its registered name — ``__wkb`` on DuckDB, ``geometry``
on PostGIS) so no result post-processing is needed: the engine's own
decoder rebuilds the GeoDataFrame. Builders raise
:class:`~gispulse.capabilities.sql_pushdown.Untranslatable` to defer the
non-1:1 modes (``by_group`` / ``dissolve``) and unsupported options to
the Python implementation.

Wiring lives at the bottom of the capability modules via
:func:`attach_sql_pushdown`.
"""

from __future__ import annotations

import geopandas as gpd

from gispulse.capabilities.sql_pushdown import Untranslatable, qi
from gispulse.persistence.sql_dialect import SQLDialect


def _geom_reg(dialect: SQLDialect, gdf: gpd.GeoDataFrame) -> str:
    """Name of the geometry column in the *registered* table."""
    return dialect.geom_column if dialect.name == "duckdb" else gdf.geometry.name


def _replace_geom_projection(
    dialect: SQLDialect, gdf: gpd.GeoDataFrame, geom_expr: str
) -> str:
    """Projection that rewrites the geometry column in place, order-preserving.

    Every attribute column is passed through; the geometry slot is
    replaced by *geom_expr* aliased back to the registered geometry
    column name — uniform across DuckDB and PostGIS, no ``* REPLACE``.
    """
    geom_name = gdf.geometry.name
    reg = _geom_reg(dialect, gdf)
    parts: list[str] = []
    for col in gdf.columns:
        if col == geom_name:
            parts.append(f"{geom_expr} AS {qi(reg)}")
        else:
            parts.append(qi(col))
    return ", ".join(parts)


def _epsg(crs_spec: str) -> int:
    """Parse an ``EPSG:nnnn`` string to its integer code."""
    s = str(crs_spec).strip().upper()
    if s.startswith("EPSG:"):
        s = s[5:]
    try:
        return int(s)
    except ValueError:
        raise Untranslatable(f"crs {crs_spec!r} is not an EPSG code") from None


# ===========================================================================
# Pure 1:1 geometry transforms
# ===========================================================================


def build_centroid(dialect, gdf, params, tables) -> str:
    expr = dialect.st_centroid(dialect.geom_ref())
    return f"SELECT {_replace_geom_projection(dialect, gdf, expr)} FROM {tables['input']}"


def build_boundary(dialect, gdf, params, tables) -> str:
    expr = dialect.st_boundary(dialect.geom_ref())
    sql = f"SELECT {_replace_geom_projection(dialect, gdf, expr)} FROM {tables['input']}"
    if params.get("drop_empty", True):
        # A point's boundary is empty — drop those rows like the Python path.
        sql += f" WHERE {expr} IS NOT NULL AND {dialect.st_is_empty(expr)} = FALSE"
    return sql


def build_make_valid(dialect, gdf, params, tables) -> str:
    if params.get("keep_geom_type", False):
        raise Untranslatable("make_valid keep_geom_type is not SQL-expressible")
    expr = dialect.st_make_valid(dialect.geom_ref())
    sql = f"SELECT {_replace_geom_projection(dialect, gdf, expr)} FROM {tables['input']}"
    if params.get("drop_empty", True):
        sql += f" WHERE {expr} IS NOT NULL AND {dialect.st_is_empty(expr)} = FALSE"
    return sql


def build_convex_hull(dialect, gdf, params, tables) -> str:
    expr = dialect.st_convex_hull(dialect.geom_ref())
    return f"SELECT {_replace_geom_projection(dialect, gdf, expr)} FROM {tables['input']}"


def build_envelope(dialect, gdf, params, tables) -> str:
    expr = dialect.st_envelope(dialect.geom_ref())
    return f"SELECT {_replace_geom_projection(dialect, gdf, expr)} FROM {tables['input']}"


def build_concave_hull(dialect, gdf, params, tables) -> str:
    ratio = params.get("ratio", 0.3)
    if not (0.0 <= float(ratio) <= 1.0):
        raise Untranslatable("concave_hull ratio outside [0, 1]")
    expr = dialect.st_concave_hull(
        dialect.geom_ref(), ratio, allow_holes=bool(params.get("allow_holes", False))
    )
    return f"SELECT {_replace_geom_projection(dialect, gdf, expr)} FROM {tables['input']}"


# ===========================================================================
# area_length — metric columns, geometry untouched
# ===========================================================================


def build_area_length(dialect, gdf, params, tables) -> str:
    area_col = params.get("area_col", "area_m2")
    length_col = params.get("length_col", "length_m")
    compute_area = params.get("compute_area", True)
    compute_length = params.get("compute_length", True)
    if not compute_area and not compute_length:
        raise Untranslatable("area_length computes neither area nor length")
    if compute_area and area_col in gdf.columns:
        raise Untranslatable(f"area_length area_col {area_col!r} already exists")
    if compute_length and length_col in gdf.columns:
        raise Untranslatable(f"area_length length_col {length_col!r} already exists")

    geom = dialect.geom_ref()
    if gdf.crs is not None:
        src = gdf.crs.to_epsg()
        if src is None:
            raise Untranslatable("area_length: source CRS has no EPSG code")
        geom = dialect.st_transform(
            geom, src_srid=src, dst_srid=_epsg(params.get("crs_meters", "EPSG:3857"))
        )
    adds: list[str] = []
    if compute_area:
        adds.append(f"ST_Area({geom}) AS {qi(area_col)}")
    if compute_length:
        # GeoPandas `.length` is the total 1-D measure: a polygon's
        # perimeter, a line's length. ST_Length alone returns 0 for
        # areal geometries, so add ST_Perimeter — the two are mutually
        # exclusive per geometry, so the sum reproduces `.length`.
        adds.append(
            f"(ST_Length({geom}) + ST_Perimeter({geom})) AS {qi(length_col)}"
        )
    return f"SELECT *, {', '.join(adds)} FROM {tables['input']}"

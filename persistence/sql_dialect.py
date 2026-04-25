"""
SQL dialect abstraction for cross-backend spatial SQL.

PostGIS, DuckDB, and SpatiaLite use different spatial function syntax.
This module provides a unified interface so trigger evaluators, operation
executors, and capability strategies can generate correct SQL for any backend.

Usage::

    dialect = get_dialect("postgis")
    sql = f"SELECT {dialect.st_area('geom')} AS area FROM parcels"
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class SQLDialect(ABC):
    """Abstract base for spatial SQL dialect adapters."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Backend name: 'postgis', 'duckdb', or 'spatialite'."""
        ...

    @abstractmethod
    def st_area(self, geom_col: str) -> str:
        """SQL expression for geometry area (in square meters where possible)."""
        ...

    @abstractmethod
    def st_length(self, geom_col: str) -> str:
        """SQL expression for geometry length (in meters where possible)."""
        ...

    @abstractmethod
    def st_distance(self, a: str, b: str) -> str:
        """SQL expression for distance between two geometries."""
        ...

    @abstractmethod
    def st_buffer(self, geom_col: str, distance: str) -> str:
        """SQL expression for buffering a geometry."""
        ...

    @abstractmethod
    def st_intersects(self, a: str, b: str) -> str:
        """SQL expression for intersection test."""
        ...

    @abstractmethod
    def st_within(self, a: str, b: str) -> str:
        """SQL expression for within test."""
        ...

    @abstractmethod
    def st_contains(self, a: str, b: str) -> str:
        """SQL expression for contains test."""
        ...

    @abstractmethod
    def st_geom_from_text(self, wkt_param: str, srid: int | None = None) -> str:
        """SQL expression to create geometry from WKT."""
        ...

    @abstractmethod
    def st_is_valid(self, geom_col: str) -> str:
        """SQL expression to check geometry validity."""
        ...

    @abstractmethod
    def st_centroid(self, geom_col: str) -> str:
        """SQL expression for centroid."""
        ...

    @abstractmethod
    def string_agg(self, col: str, sep: str = ", ") -> str:
        """SQL expression for string aggregation."""
        ...

    def st_overlaps(self, a: str, b: str) -> str:
        """SQL expression for overlaps test."""
        return f"ST_Overlaps({a}, {b})"

    def st_crosses(self, a: str, b: str) -> str:
        """SQL expression for crosses test."""
        return f"ST_Crosses({a}, {b})"


class PostGISDialect(SQLDialect):
    """PostgreSQL/PostGIS spatial SQL dialect."""

    @property
    def name(self) -> str:
        return "postgis"

    def st_area(self, geom_col: str) -> str:
        return f"ST_Area({geom_col}::geography)"

    def st_length(self, geom_col: str) -> str:
        return f"ST_Length({geom_col}::geography)"

    def st_distance(self, a: str, b: str) -> str:
        return f"ST_Distance({a}::geography, {b}::geography)"

    def st_buffer(self, geom_col: str, distance: str) -> str:
        return f"ST_Buffer({geom_col}::geography, {distance})::geometry"

    def st_intersects(self, a: str, b: str) -> str:
        return f"ST_Intersects({a}, {b})"

    def st_within(self, a: str, b: str) -> str:
        return f"ST_Within({a}, {b})"

    def st_contains(self, a: str, b: str) -> str:
        return f"ST_Contains({a}, {b})"

    def st_geom_from_text(self, wkt_param: str, srid: int | None = None) -> str:
        if srid is not None:
            return f"ST_GeomFromText({wkt_param}, {srid})"
        return f"ST_GeomFromText({wkt_param})"

    def st_is_valid(self, geom_col: str) -> str:
        return f"ST_IsValid({geom_col})"

    def st_centroid(self, geom_col: str) -> str:
        return f"ST_Centroid({geom_col})"

    def string_agg(self, col: str, sep: str = ", ") -> str:
        return f"STRING_AGG({col}::TEXT, '{sep}')"


class DuckDBDialect(SQLDialect):
    """DuckDB spatial extension SQL dialect."""

    @property
    def name(self) -> str:
        return "duckdb"

    def st_area(self, geom_col: str) -> str:
        # DuckDB spatial: planaire only (no geography cast)
        return f"ST_Area({geom_col})"

    def st_length(self, geom_col: str) -> str:
        return f"ST_Length({geom_col})"

    def st_distance(self, a: str, b: str) -> str:
        return f"ST_Distance({a}, {b})"

    def st_buffer(self, geom_col: str, distance: str) -> str:
        return f"ST_Buffer({geom_col}, {distance})"

    def st_intersects(self, a: str, b: str) -> str:
        return f"ST_Intersects({a}, {b})"

    def st_within(self, a: str, b: str) -> str:
        return f"ST_Within({a}, {b})"

    def st_contains(self, a: str, b: str) -> str:
        return f"ST_Contains({a}, {b})"

    def st_geom_from_text(self, wkt_param: str, srid: int | None = None) -> str:
        # DuckDB spatial does not support SRID parameter in ST_GeomFromText
        return f"ST_GeomFromText({wkt_param})"

    def st_is_valid(self, geom_col: str) -> str:
        return f"ST_IsValid({geom_col})"

    def st_centroid(self, geom_col: str) -> str:
        return f"ST_Centroid({geom_col})"

    def string_agg(self, col: str, sep: str = ", ") -> str:
        return f"STRING_AGG({col}::VARCHAR, '{sep}')"


class SpatiaLiteDialect(SQLDialect):
    """SpatiaLite SQL dialect."""

    @property
    def name(self) -> str:
        return "spatialite"

    def st_area(self, geom_col: str) -> str:
        return f"Area({geom_col})"

    def st_length(self, geom_col: str) -> str:
        return f"GLength({geom_col})"

    def st_distance(self, a: str, b: str) -> str:
        return f"Distance({a}, {b})"

    def st_buffer(self, geom_col: str, distance: str) -> str:
        return f"Buffer({geom_col}, {distance})"

    def st_intersects(self, a: str, b: str) -> str:
        return f"Intersects({a}, {b})"

    def st_within(self, a: str, b: str) -> str:
        return f"Within({a}, {b})"

    def st_contains(self, a: str, b: str) -> str:
        return f"Contains({a}, {b})"

    def st_geom_from_text(self, wkt_param: str, srid: int | None = None) -> str:
        if srid is not None:
            return f"GeomFromText({wkt_param}, {srid})"
        return f"GeomFromText({wkt_param})"

    def st_is_valid(self, geom_col: str) -> str:
        return f"IsValid({geom_col})"

    def st_centroid(self, geom_col: str) -> str:
        return f"Centroid({geom_col})"

    def string_agg(self, col: str, sep: str = ", ") -> str:
        return f"GROUP_CONCAT({col}, '{sep}')"

    def st_overlaps(self, a: str, b: str) -> str:
        return f"Overlaps({a}, {b})"

    def st_crosses(self, a: str, b: str) -> str:
        return f"Crosses({a}, {b})"


class GeoPackageDialect(SQLDialect):
    """GeoPackage SQL dialect — attribute queries only.

    Spatial functions are NOT available at the SQL level (no mod_spatialite).
    Use GeoPackageEngine.spatial_query() for spatial operations, or enable
    DuckDB acceleration for spatial SQL.

    Attribute functions (GROUP_CONCAT, etc.) work normally via SQLite.
    """

    @property
    def name(self) -> str:
        return "gpkg"

    def _spatial_not_supported(self, fn_name: str) -> str:
        raise NotImplementedError(
            f"GPKG backend does not support {fn_name}() in SQL. "
            f"Use engine.spatial_query() or enable DuckDB acceleration."
        )

    def st_area(self, geom_col: str) -> str:
        return self._spatial_not_supported("ST_Area")

    def st_length(self, geom_col: str) -> str:
        return self._spatial_not_supported("ST_Length")

    def st_distance(self, a: str, b: str) -> str:
        return self._spatial_not_supported("ST_Distance")

    def st_buffer(self, geom_col: str, distance: str) -> str:
        return self._spatial_not_supported("ST_Buffer")

    def st_intersects(self, a: str, b: str) -> str:
        return self._spatial_not_supported("ST_Intersects")

    def st_within(self, a: str, b: str) -> str:
        return self._spatial_not_supported("ST_Within")

    def st_contains(self, a: str, b: str) -> str:
        return self._spatial_not_supported("ST_Contains")

    def st_geom_from_text(self, wkt_param: str, srid: int | None = None) -> str:
        return self._spatial_not_supported("ST_GeomFromText")

    def st_is_valid(self, geom_col: str) -> str:
        return self._spatial_not_supported("ST_IsValid")

    def st_centroid(self, geom_col: str) -> str:
        return self._spatial_not_supported("ST_Centroid")

    def string_agg(self, col: str, sep: str = ", ") -> str:
        return f"GROUP_CONCAT({col}, '{sep}')"

    def st_overlaps(self, a: str, b: str) -> str:
        return self._spatial_not_supported("ST_Overlaps")

    def st_crosses(self, a: str, b: str) -> str:
        return self._spatial_not_supported("ST_Crosses")


# Singleton instances
_DIALECTS: dict[str, SQLDialect] = {
    "postgis": PostGISDialect(),
    "duckdb": DuckDBDialect(),
    "spatialite": SpatiaLiteDialect(),
    "gpkg": GeoPackageDialect(),
}


def get_dialect(backend: str) -> SQLDialect:
    """Get the SQL dialect for a backend name.

    Args:
        backend: One of 'postgis', 'duckdb', 'spatialite'.

    Returns:
        The corresponding SQLDialect instance.

    Raises:
        ValueError: If the backend is not recognized.
    """
    dialect = _DIALECTS.get(backend)
    if dialect is None:
        raise ValueError(
            f"Unknown SQL dialect: {backend!r}. "
            f"Available: {sorted(_DIALECTS.keys())}"
        )
    return dialect

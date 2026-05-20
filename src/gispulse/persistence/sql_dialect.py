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
from dataclasses import dataclass

from gispulse.core.sql_safety import validate_identifier


class UnsupportedInDialect(NotImplementedError):
    """A spatial SQL feature has no equivalent in the target dialect.

    Subclasses :class:`NotImplementedError` so existing callers that
    catch the broad type keep working, while new code can catch this
    precise type — e.g. a styled buffer or the KNN operator routed
    through DuckDB — and fall back to the Python strategy.
    """

    def __init__(self, feature: str, dialect: str) -> None:
        super().__init__(
            f"{feature!r} is not supported by the {dialect!r} SQL dialect"
        )
        self.feature = feature
        self.dialect = dialect


# ---------------------------------------------------------------------------
# Buffer style (ELT Lot 1 — divergence #2)
# ---------------------------------------------------------------------------

_CAP_STYLES: frozenset[str] = frozenset({"round", "flat", "square"})
_JOIN_STYLES: frozenset[str] = frozenset({"round", "mitre", "bevel"})


@dataclass(frozen=True)
class BufferStyle:
    """Validated styling parameters for ``ST_Buffer``.

    The default instance (``BufferStyle()``) is the only style DuckDB
    can honour. Anything else has :attr:`is_default` ``False`` and only
    PostGIS can express it as SQL — see :meth:`SQLDialect.st_buffer_styled`.
    """

    quad_segs: int = 8
    cap_style: str = "round"
    join_style: str = "round"
    mitre_limit: float = 5.0
    single_sided: bool = False

    def __post_init__(self) -> None:
        if self.cap_style not in _CAP_STYLES:
            raise ValueError(
                f"Invalid cap_style {self.cap_style!r}. Expected one of "
                f"{sorted(_CAP_STYLES)}."
            )
        if self.join_style not in _JOIN_STYLES:
            raise ValueError(
                f"Invalid join_style {self.join_style!r}. Expected one of "
                f"{sorted(_JOIN_STYLES)}."
            )
        if self.quad_segs < 1:
            raise ValueError("quad_segs must be >= 1.")

    @property
    def is_default(self) -> bool:
        """True when the style is the round/8-seg/two-sided default."""
        return (
            self.quad_segs == 8
            and self.cap_style == "round"
            and self.join_style == "round"
            and float(self.mitre_limit) == 5.0
            and not self.single_sided
        )

    def to_postgis_style(self) -> str:
        """Render the PostGIS ``ST_Buffer`` style-string third argument.

        Built from enum-constrained, validated fields, so it is safe to
        inline into SQL.
        """
        side = "left" if self.single_sided else "both"
        return (
            f"quad_segs={int(self.quad_segs)} endcap={self.cap_style} "
            f"join={self.join_style} mitre_limit={float(self.mitre_limit):.3f} "
            f"side={side}"
        )


def _fmt_number(value: float) -> str:
    """Format a numeric SQL literal (buffer distances may be negative)."""
    return repr(float(value))


def _qualified(column: str, table: str | None) -> str:
    """Return a validated ``table.column`` (or bare ``column``)."""
    validate_identifier(column, "geometry column")
    if table is None:
        return column
    validate_identifier(table, "table alias")
    return f"{table}.{column}"


class SQLDialect(ABC):
    """Abstract base for spatial SQL dialect adapters."""

    #: Column a registered GeoDataFrame's geometry lands in for this backend.
    geom_column: str = "geometry"
    #: Whether ``ST_Buffer`` accepts a non-default :class:`BufferStyle`.
    supports_styled_buffer: bool = False
    #: Whether the KNN ``<->`` distance operator is available.
    supports_knn: bool = False
    #: Whether ``ST_Coverage*`` topological functions are available.
    supports_coverage: bool = False

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

    # ------------------------------------------------------------------
    # ELT Lot 1 (#244) — the five audited DuckDB/PostGIS divergences
    # ------------------------------------------------------------------

    def geom_ref(self, column: str | None = None, *, table: str | None = None) -> str:
        """Return a GEOMETRY-typed expression for a *registered* column.

        Divergence #1 — DuckDB registers a GeoDataFrame as a ``__wkb``
        BLOB and must lift it with ``ST_GeomFromWKB``; engines with a
        native geometry column just reference it. Base behaviour is the
        native reference; :class:`DuckDBDialect` overrides.
        """
        return _qualified(column or self.geom_column, table)

    def st_intersection(self, a: str, b: str) -> str:
        """SQL expression for the geometric intersection (overlay) of *a*, *b*."""
        return f"ST_Intersection({a}, {b})"

    def st_buffer_styled(
        self,
        geom_expr: str,
        distance: float,
        style: BufferStyle | None = None,
    ) -> str:
        """Planar ``ST_Buffer`` with an optional :class:`BufferStyle`.

        Divergence #2 — distinct from :meth:`st_buffer`, which buffers in
        ``geography`` space. Here the caller has already reprojected to a
        metric CRS, so the buffer is planar. Base behaviour honours only
        the default style; :class:`PostGISDialect` overrides to emit the
        style string.

        Raises:
            UnsupportedInDialect: when *style* is non-default and the
                dialect cannot express it.
        """
        if style is not None and not style.is_default:
            raise UnsupportedInDialect("styled ST_Buffer", self.name)
        return f"ST_Buffer({geom_expr}, {_fmt_number(distance)})"

    def st_transform(
        self, geom_expr: str, *, src_srid: int, dst_srid: int
    ) -> str:
        """Reproject a geometry between SRIDs.

        Divergence #4 — base behaviour is the 2-arg ``(geom, target)``
        form; :class:`DuckDBDialect` overrides with the 4-arg form that
        pins axis order via ``always_xy``.
        """
        return f"ST_Transform({geom_expr}, {int(dst_srid)})"

    def st_knn_distance(self, a: str, b: str) -> str:
        """KNN ``<->`` index-backed nearest-neighbour distance operator.

        Divergence #3 — PostGIS-only.

        Raises:
            UnsupportedInDialect: on every dialect but PostGIS.
        """
        raise UnsupportedInDialect("KNN <-> operator", self.name)

    def project_with_geometry(
        self,
        table_alias: str | None,
        geom_expr: str,
        *,
        result_suffix: str = "out",
    ) -> tuple[str, str]:
        """Build a ``SELECT`` projection that derives a new geometry column.

        Returns ``(projection, result_geom_column)`` — the text placed
        after ``SELECT`` and the name the derived geometry lands under.

        *table_alias* qualifies the star (``i.*``); pass ``None`` for an
        unqualified ``*`` (single-table queries).

        Base behaviour appends *geom_expr* as a new
        ``<geom_column>_<result_suffix>`` column next to the star.
        :class:`DuckDBDialect` overrides this to use the ``* REPLACE``
        projection so the geometry keeps its canonical ``__wkb`` name —
        the DuckDB result decoder keys on that name.
        """
        star = self._star(table_alias)
        result_col = f"{self.geom_column}_{result_suffix}"
        validate_identifier(result_col, "result geometry column")
        return f"{star}, {geom_expr} AS {result_col}", result_col

    @staticmethod
    def _star(table_alias: str | None) -> str:
        """Return ``*`` or a validated ``<alias>.*``."""
        if not table_alias:
            return "*"
        validate_identifier(table_alias, "table alias")
        return f"{table_alias}.*"

    # ------------------------------------------------------------------
    # ELT Lot 3 (#246) — single-layer 1:1 geometry transforms
    # ------------------------------------------------------------------
    # DuckDB-spatial and PostGIS spell these identically (``ST_*``); they
    # are concrete here. :class:`GeoPackageDialect` overrides them to
    # raise. They are only ever invoked through the DuckDB/PostGIS
    # push-down strategies, so the SpatiaLite spelling is not specialised.

    def st_boundary(self, geom: str) -> str:
        """Topological boundary of a geometry."""
        return f"ST_Boundary({geom})"

    def st_envelope(self, geom: str) -> str:
        """Axis-aligned bounding-box envelope of a geometry."""
        return f"ST_Envelope({geom})"

    def st_convex_hull(self, geom: str) -> str:
        """Convex hull of a geometry."""
        return f"ST_ConvexHull({geom})"

    def st_concave_hull(
        self, geom: str, ratio: float, *, allow_holes: bool = False
    ) -> str:
        """Concave hull — three-arg form, the one DuckDB-spatial accepts."""
        holes = "true" if allow_holes else "false"
        return f"ST_ConcaveHull({geom}, {float(ratio)}, {holes})"

    def st_make_valid(self, geom: str) -> str:
        """Repaired (validity-corrected) geometry."""
        return f"ST_MakeValid({geom})"

    def st_simplify(self, geom: str, tolerance: float) -> str:
        """Douglas-Peucker simplified geometry."""
        return f"ST_Simplify({geom}, {float(tolerance)})"

    def st_is_empty(self, geom: str) -> str:
        """Boolean — whether a geometry is empty."""
        return f"ST_IsEmpty({geom})"

    def st_difference(self, a: str, b: str) -> str:
        """Geometric difference ``a - b``."""
        return f"ST_Difference({a}, {b})"

    def st_simplify_preserve_topology(self, geom: str, tolerance: float) -> str:
        """Topology-preserving Douglas-Peucker simplification."""
        return f"ST_SimplifyPreserveTopology({geom}, {float(tolerance)})"

    def st_union_agg(self, geom_col: str) -> str:
        """Aggregate union of a geometry column.

        Base spelling is the PostGIS aggregate ``ST_Union``;
        :class:`DuckDBDialect` overrides with ``ST_Union_Agg``.
        """
        return f"ST_Union({geom_col})"

    def st_sym_difference(self, a: str, b: str) -> str:
        """Symmetric difference ``a XOR b``.

        Base spelling is the PostGIS native ``ST_SymDifference``;
        :class:`DuckDBDialect` — which lacks it — overrides with the
        equivalent ``(a - b) UNION (b - a)``.
        """
        return f"ST_SymDifference({a}, {b})"

    def first_agg(self, col: str) -> str:
        """Pick one value per group — the GeoPandas ``dissolve`` aggfunc.

        PostgreSQL has no ``first`` aggregate; the base spelling picks
        the first element of ``array_agg``. :class:`DuckDBDialect`
        overrides with the native ``first()`` aggregate.
        """
        return f"(array_agg({col}))[1]"


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

    # -- ELT Lot 1 divergences ----------------------------------------

    geom_column = "geometry"
    supports_styled_buffer = True
    supports_knn = True
    supports_coverage = True

    def geom_ref(self, column: str | None = None, *, table: str | None = None) -> str:
        # `register` writes a native `geometry` column; the explicit cast
        # is a harmless no-op that keeps the SQL symmetric with DuckDB.
        return f"{_qualified(column or self.geom_column, table)}::geometry"

    def st_buffer_styled(
        self,
        geom_expr: str,
        distance: float,
        style: BufferStyle | None = None,
    ) -> str:
        # A supplied BufferStyle is always rendered as the explicit style
        # string — including a default one — so the emitted SQL is
        # byte-stable regardless of the style's values. ``style=None``
        # gives the bare two-argument form.
        if style is None:
            return f"ST_Buffer({geom_expr}, {_fmt_number(distance)})"
        return (
            f"ST_Buffer({geom_expr}, {_fmt_number(distance)}, "
            f"'{style.to_postgis_style()}')"
        )

    def st_knn_distance(self, a: str, b: str) -> str:
        return f"({a} <-> {b})"


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

    # -- ELT Lot 1 divergences ----------------------------------------
    # DuckDB is the ADR 0001 contract dialect. Styled buffers and the
    # KNN operator inherit the base behaviour (raise UnsupportedInDialect)
    # — there is no DuckDB-spatial equivalent.

    geom_column = "__wkb"

    def geom_ref(self, column: str | None = None, *, table: str | None = None) -> str:
        # `register_gdf` stores geometry as a `__wkb` BLOB column; it must
        # be parsed back to GEOMETRY before any spatial function.
        return f"ST_GeomFromWKB({_qualified(column or self.geom_column, table)})"

    def st_transform(
        self, geom_expr: str, *, src_srid: int, dst_srid: int
    ) -> str:
        # 4-arg form: always_xy := true pins lon/lat axis order so a
        # geographic source CRS is not silently transposed.
        return (
            f"ST_Transform({geom_expr}, 'EPSG:{int(src_srid)}', "
            f"'EPSG:{int(dst_srid)}', always_xy := true)"
        )

    def project_with_geometry(
        self,
        table_alias: str | None,
        geom_expr: str,
        *,
        result_suffix: str = "out",
    ) -> tuple[str, str]:
        # `* REPLACE (expr AS col)` is a DuckDB extension: it swaps a
        # column in place, so the derived geometry keeps the canonical
        # __wkb name the result decoder expects. result_suffix is unused.
        star = self._star(table_alias)
        return (
            f"{star} REPLACE ({geom_expr} AS {self.geom_column})",
            self.geom_column,
        )

    def st_union_agg(self, geom_col: str) -> str:
        # DuckDB-spatial spells the aggregate union ST_Union_Agg.
        return f"ST_Union_Agg({geom_col})"

    def st_sym_difference(self, a: str, b: str) -> str:
        # DuckDB-spatial has no ST_SymDifference — compose it from the
        # two one-sided differences: (a - b) unioned with (b - a).
        return f"ST_Union(ST_Difference({a}, {b}), ST_Difference({b}, {a}))"

    def first_agg(self, col: str) -> str:
        # DuckDB has a native first() aggregate — pick one element per group.
        return f"first({col})"


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

    # -- ELT Lot 1 divergences ----------------------------------------
    # SpatiaLite drops the ST_ prefix and has no styled buffer.

    def st_intersection(self, a: str, b: str) -> str:
        return f"Intersection({a}, {b})"

    def st_buffer_styled(
        self,
        geom_expr: str,
        distance: float,
        style: BufferStyle | None = None,
    ) -> str:
        if style is not None and not style.is_default:
            raise UnsupportedInDialect("styled ST_Buffer", self.name)
        return f"Buffer({geom_expr}, {_fmt_number(distance)})"

    def st_transform(
        self, geom_expr: str, *, src_srid: int, dst_srid: int
    ) -> str:
        return f"Transform({geom_expr}, {int(dst_srid)})"


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

    # -- ELT Lot 1 divergences ----------------------------------------
    # GPKG has no SQL-level spatial functions — every spatial primitive
    # below raises, as the existing st_* methods do.

    def geom_ref(self, column: str | None = None, *, table: str | None = None) -> str:
        return self._spatial_not_supported("geom_ref")

    def st_intersection(self, a: str, b: str) -> str:
        return self._spatial_not_supported("ST_Intersection")

    def st_buffer_styled(
        self,
        geom_expr: str,
        distance: float,
        style: BufferStyle | None = None,
    ) -> str:
        return self._spatial_not_supported("ST_Buffer")

    def st_transform(
        self, geom_expr: str, *, src_srid: int, dst_srid: int
    ) -> str:
        return self._spatial_not_supported("ST_Transform")

    def st_boundary(self, geom: str) -> str:
        return self._spatial_not_supported("ST_Boundary")

    def st_envelope(self, geom: str) -> str:
        return self._spatial_not_supported("ST_Envelope")

    def st_convex_hull(self, geom: str) -> str:
        return self._spatial_not_supported("ST_ConvexHull")

    def st_concave_hull(
        self, geom: str, ratio: float, *, allow_holes: bool = False
    ) -> str:
        return self._spatial_not_supported("ST_ConcaveHull")

    def st_make_valid(self, geom: str) -> str:
        return self._spatial_not_supported("ST_MakeValid")

    def st_simplify(self, geom: str, tolerance: float) -> str:
        return self._spatial_not_supported("ST_Simplify")

    def st_is_empty(self, geom: str) -> str:
        return self._spatial_not_supported("ST_IsEmpty")

    def st_difference(self, a: str, b: str) -> str:
        return self._spatial_not_supported("ST_Difference")

    def st_simplify_preserve_topology(self, geom: str, tolerance: float) -> str:
        return self._spatial_not_supported("ST_SimplifyPreserveTopology")

    def st_union_agg(self, geom_col: str) -> str:
        return self._spatial_not_supported("ST_Union")

    def st_sym_difference(self, a: str, b: str) -> str:
        return self._spatial_not_supported("ST_SymDifference")

    def first_agg(self, col: str) -> str:
        return self._spatial_not_supported("first_agg")


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

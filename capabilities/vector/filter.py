from __future__ import annotations


import geopandas as gpd

from capabilities.base import Capability
from capabilities.registry import register
from capabilities.strategy import ExecutionContext, ExecutionStrategy, StrategyMode
from capabilities.vector.calculate import _validate_query_expression


# ---------------------------------------------------------------------------
# Filter execution strategies
# ---------------------------------------------------------------------------


class _FilterPythonStrategy(ExecutionStrategy):
    """GeoPandas fallback — attribute + spatial filtering via pandas/shapely."""

    mode = StrategyMode.PYTHON

    def can_execute(self, ctx: ExecutionContext) -> bool:
        return True

    def execute(self, gdf: gpd.GeoDataFrame, ctx: ExecutionContext) -> gpd.GeoDataFrame:

        expression: str = ctx.params.get("expression", "")
        spatial_predicate: str | None = ctx.params.get("spatial_predicate")
        ref_gdf: gpd.GeoDataFrame | None = ctx.params.get("ref_gdf")
        ref_wkt: str | None = ctx.params.get("ref_wkt")
        ref_geojson: dict | None = ctx.params.get("ref_geojson")
        ref_filter: str | None = ctx.params.get("ref_filter")
        buffer_distance: float | None = ctx.params.get("buffer_distance")
        crs_meters: str = ctx.params.get("crs_meters", "EPSG:3857")

        result = gdf

        # 1. Attribute filter
        if expression:
            _validate_query_expression(expression)
            result = result.query(expression).reset_index(drop=True)

        # 2. Spatial filter
        if spatial_predicate:
            if ref_filter and ref_gdf is not None:
                _validate_query_expression(ref_filter)
                ref_gdf = ref_gdf.query(ref_filter).reset_index(drop=True)
            ref_geom = _resolve_ref_geom(ref_gdf, ref_wkt, ref_geojson, gdf.crs)
            if ref_geom is not None:
                if buffer_distance and buffer_distance > 0:
                    ref_geom = _buffer_geom(ref_geom, buffer_distance, gdf.crs, crs_meters)
                result = _apply_predicate_geopandas(
                    result, ref_geom, spatial_predicate, buffer_distance, crs_meters=crs_meters,
                )

        return result

    @property
    def priority(self) -> int:
        return 10


class _FilterDuckDBStrategy(ExecutionStrategy):
    """DuckDB spatial strategy — SQL-based filtering for large datasets."""

    mode = StrategyMode.DUCKDB

    def can_execute(self, ctx: ExecutionContext) -> bool:
        return ctx.engine.backend_name == "duckdb"

    def execute(self, gdf: gpd.GeoDataFrame, ctx: ExecutionContext) -> gpd.GeoDataFrame:
        from core.filter.expression import Dialect, FilterExpression
        from core.filter.expression_converter import ExpressionConverter

        expression: str = ctx.params.get("expression", "")
        spatial_predicate: str | None = ctx.params.get("spatial_predicate")
        ref_wkt: str | None = ctx.params.get("ref_wkt")
        buffer_distance: float | None = ctx.params.get("buffer_distance")

        # Build FilterExpression for the converter
        if spatial_predicate:
            from core.filter.expression import SpatialPredicate
            try:
                pred = SpatialPredicate(spatial_predicate)
            except ValueError:
                valid = [p.value for p in SpatialPredicate]
                raise ValueError(
                    f"Invalid spatial_predicate: {spatial_predicate!r}. Valid: {valid}"
                ) from None
            expr = FilterExpression.create_spatial(
                [pred],
                buffer_value=buffer_distance or 0,
                dialect=Dialect.DUCKDB,
                ref_wkt=ref_wkt,
            )
            if expression:
                # Mixed: attribute + spatial
                expr = FilterExpression.create(
                    expression,
                    dialect=Dialect.DUCKDB,
                    ref_wkt=ref_wkt,
                )
                # Manually set spatial fields via new instance
                expr = FilterExpression(
                    raw=expression,
                    sql="",
                    dialect=Dialect.DUCKDB,
                    is_spatial=True,
                    spatial_predicates=(pred,),
                    buffer_value=buffer_distance,
                    ref_wkt=ref_wkt,
                )
        elif expression:
            expr = FilterExpression.create(expression, dialect=Dialect.DUCKDB)
        else:
            return gdf

        converter = ExpressionConverter()
        ctx.engine.register("_filter_input", gdf)
        sql, _params = converter.to_duckdb_sql(expr, "_filter_input")
        return ctx.engine.sql_to_gdf(sql)

    @property
    def priority(self) -> int:
        return 80


class _FilterPostGISStrategy(ExecutionStrategy):
    """PostGIS strategy — server-side SQL filtering, most scalable."""

    mode = StrategyMode.POSTGIS

    def can_execute(self, ctx: ExecutionContext) -> bool:
        return ctx.engine.backend_name == "postgis"

    def execute(self, gdf: gpd.GeoDataFrame, ctx: ExecutionContext) -> gpd.GeoDataFrame:
        from core.filter.expression import Dialect, FilterExpression
        from core.filter.expression_converter import ExpressionConverter

        expression: str = ctx.params.get("expression", "")
        spatial_predicate: str | None = ctx.params.get("spatial_predicate")
        ref_wkt: str | None = ctx.params.get("ref_wkt")
        buffer_distance: float | None = ctx.params.get("buffer_distance")
        schema: str = ctx.params.get("schema", "public")
        table_name: str = ctx.params.get("table_name", "")

        if not table_name:
            # Fallback to Python strategy via registration
            ctx.engine.register("_filter_input", gdf)
            table_name = "_filter_input"
            schema = "public"

        if spatial_predicate:
            from core.filter.expression import SpatialPredicate
            try:
                pred = SpatialPredicate(spatial_predicate)
            except ValueError:
                valid = [p.value for p in SpatialPredicate]
                raise ValueError(
                    f"Invalid spatial_predicate: {spatial_predicate!r}. Valid: {valid}"
                ) from None
            expr = FilterExpression(
                raw=expression or f"Spatial filter: {spatial_predicate}",
                sql="",
                dialect=Dialect.POSTGIS,
                is_spatial=True,
                spatial_predicates=(pred,),
                buffer_value=buffer_distance,
                ref_wkt=ref_wkt,
            )
        elif expression:
            expr = FilterExpression.create(expression, dialect=Dialect.POSTGIS)
        else:
            return gdf

        converter = ExpressionConverter()
        sql, _params = converter.to_postgis_sql(expr, schema, table_name)
        return ctx.engine.sql_to_gdf(sql)

    @property
    def priority(self) -> int:
        return 100


# ---------------------------------------------------------------------------
# Helpers for Python strategy
# ---------------------------------------------------------------------------


def _resolve_ref_geom(
    ref_gdf: gpd.GeoDataFrame | None,
    ref_wkt: str | None,
    ref_geojson: dict | None,
    target_crs: object | None,
) -> object | None:
    """Resolve a reference geometry from multiple possible sources."""
    from shapely import wkt as shapely_wkt
    from shapely.geometry import shape

    if ref_gdf is not None:
        if target_crs and ref_gdf.crs != target_crs:
            ref_gdf = ref_gdf.to_crs(target_crs)
        return ref_gdf.union_all()
    if ref_geojson:
        return shape(ref_geojson)
    if ref_wkt:
        return shapely_wkt.loads(ref_wkt)
    return None


def _buffer_geom(
    geom: object,
    distance_m: float,
    crs: object | None,
    crs_meters: str = "EPSG:3857",
) -> object:
    """Buffer a shapely geometry in a metric CRS.

    Args:
        geom:       Input shapely geometry.
        distance_m: Buffer distance expressed in the units of *crs_meters*.
        crs:        Source CRS of *geom*.
        crs_meters: Metric CRS used for the buffer. Default EPSG:3857 preserves
            backward compatibility but distorts area/distance with latitude
            (~x1.9 at Toulouse). Use EPSG:2154 for France, UTM zones, or any
            equal-area projection for precise metric buffers.
    """
    import pyproj
    from shapely.ops import transform

    if crs is not None and not getattr(crs, "is_projected", False):
        to_metric = pyproj.Transformer.from_crs(crs, crs_meters, always_xy=True)
        from_metric = pyproj.Transformer.from_crs(crs_meters, crs, always_xy=True)
        geom_m = transform(to_metric.transform, geom)
        buffered = geom_m.buffer(distance_m)
        return transform(from_metric.transform, buffered)
    return geom.buffer(distance_m)


def _apply_predicate_geopandas(
    gdf: gpd.GeoDataFrame,
    ref_geom: object,
    predicate: str,
    buffer_distance: float | None = None,
    crs_meters: str = "EPSG:3857",
) -> gpd.GeoDataFrame:
    """Apply a spatial predicate using GeoPandas geometry methods.

    ``dwithin`` reprojects the GDF + reference geometry to ``crs_meters``
    when the input is angular so ``buffer_distance`` is interpreted in
    meters. Pass a local metric CRS (EPSG:2154 in France) for accurate
    distances; the default EPSG:3857 distorts up to ~x1.9 with latitude.
    """
    if predicate == "dwithin":
        dist = buffer_distance or 0
        if gdf.crs and not gdf.crs.is_projected:
            gdf_proj = gdf.to_crs(crs_meters)
            import pyproj
            from shapely.ops import transform
            transformer = pyproj.Transformer.from_crs(gdf.crs, crs_meters, always_xy=True)
            ref_proj = transform(transformer.transform, ref_geom)
            mask = gdf_proj.geometry.distance(ref_proj) <= dist
        else:
            mask = gdf.geometry.distance(ref_geom) <= dist
        return gdf[mask].reset_index(drop=True)

    method = getattr(gdf.geometry, predicate, None)
    if method is None:
        raise ValueError(f"Unknown spatial predicate: {predicate}")
    mask = method(ref_geom)
    return gdf[mask].reset_index(drop=True)


# ---------------------------------------------------------------------------
# FilterCapability
# ---------------------------------------------------------------------------


@register
class FilterCapability(Capability):
    """Filters features using attribute expressions and/or spatial predicates.

    Supports three execution strategies:
    - PostGIS (priority 100): server-side SQL
    - DuckDB (priority 80): in-process SQL
    - Python (priority 10): GeoPandas fallback
    """

    name = "filter"
    description = "Filters features using attribute expressions and/or spatial predicates."

    _strategies = [
        _FilterPostGISStrategy(),
        _FilterDuckDBStrategy(),
        _FilterPythonStrategy(),
    ]

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        expression: str = "",
        spatial_predicate: str | None = None,
        ref_gdf: gpd.GeoDataFrame | None = None,
        ref_wkt: str | None = None,
        ref_geojson: dict | None = None,
        ref_filter: str | None = None,
        buffer_distance: float | None = None,
        crs_meters: str = "EPSG:3857",
        **_,
    ) -> gpd.GeoDataFrame:
        """Fallback Python execution (no strategy context).

        Args:
            gdf:               Input GeoDataFrame.
            expression:        Pandas query string for attribute filter.
            spatial_predicate: Spatial predicate (intersects, within, etc.).
            ref_gdf:           Reference GeoDataFrame for spatial filter.
            ref_wkt:           WKT reference geometry.
            ref_geojson:       GeoJSON reference geometry.
            ref_filter:        Pandas query applied to *ref_gdf* before the
                               spatial predicate (e.g. keep only one toponym).
            buffer_distance:   Buffer distance in units of *crs_meters*.
            crs_meters:        Metric CRS used for the buffer. Default EPSG:3857
                               distorts with latitude; prefer EPSG:2154 in France
                               or an appropriate UTM zone for accurate distances.
        """
        result = gdf

        if expression:
            _validate_query_expression(expression)
            result = result.query(expression).reset_index(drop=True)

        if spatial_predicate:
            if ref_filter and ref_gdf is not None:
                _validate_query_expression(ref_filter)
                ref_gdf = ref_gdf.query(ref_filter).reset_index(drop=True)
            ref_geom = _resolve_ref_geom(ref_gdf, ref_wkt, ref_geojson, gdf.crs)
            if ref_geom is not None:
                if buffer_distance and buffer_distance > 0:
                    ref_geom = _buffer_geom(ref_geom, buffer_distance, gdf.crs, crs_meters)
                result = _apply_predicate_geopandas(
                    result, ref_geom, spatial_predicate, buffer_distance, crs_meters=crs_meters,
                )

        return result

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "Pandas query expression, e.g. \"population > 1000\".",
                },
                "spatial_predicate": {
                    "type": ["string", "null"],
                    "description": "Spatial predicate: intersects, contains, within, crosses, touches, overlaps, disjoint, equals, dwithin.",
                    "enum": [
                        "intersects", "contains", "within", "crosses",
                        "touches", "overlaps", "disjoint", "equals", "dwithin",
                    ],
                },
                "ref_wkt": {
                    "type": ["string", "null"],
                    "description": "WKT reference geometry for spatial filter.",
                },
                "ref_geojson": {
                    "type": ["object", "null"],
                    "description": "GeoJSON reference geometry for spatial filter.",
                },
                "ref_layer": {
                    "type": ["string", "null"],
                    "description": "Reference layer name for spatial filter (resolved to ref_gdf by engine).",
                },
                "ref_filter": {
                    "type": ["string", "null"],
                    "description": "Pandas query applied to ref_gdf before the spatial predicate (e.g. \"toponyme == 'la Garonne'\").",
                },
                "buffer_distance": {
                    "type": ["number", "null"],
                    "description": "Buffer distance in units of crs_meters applied to reference geometry.",
                },
                "crs_meters": {
                    "type": "string",
                    "default": "EPSG:3857",
                    "description": "Metric CRS for buffer_distance. Use EPSG:2154 for France.",
                },
            },
        }



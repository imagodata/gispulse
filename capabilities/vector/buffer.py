from __future__ import annotations


import geopandas as gpd

from capabilities.base import Capability
from capabilities.registry import register
from capabilities.strategy import ExecutionContext, ExecutionStrategy, StrategyMode


# ---------------------------------------------------------------------------
# Buffer style helpers
# ---------------------------------------------------------------------------

_BUFFER_CAP_STYLES = {"round": 1, "flat": 2, "square": 3}
_BUFFER_JOIN_STYLES = {"round": 1, "mitre": 2, "bevel": 3}


def _buffer_kwargs(
    quad_segs: int,
    cap_style: str,
    join_style: str,
    mitre_limit: float,
    single_sided: bool,
) -> dict:
    """Build validated kwargs for GeoSeries.buffer().

    GeoPandas exposes the number of segments per quadrant as ``resolution``
    (which it forwards to shapely as ``quad_segs``); we mirror that naming
    on the user-facing API while translating to the geopandas kwarg here.
    """
    if cap_style not in _BUFFER_CAP_STYLES:
        raise ValueError(
            f"Invalid cap_style '{cap_style}'. Expected one of {list(_BUFFER_CAP_STYLES)}."
        )
    if join_style not in _BUFFER_JOIN_STYLES:
        raise ValueError(
            f"Invalid join_style '{join_style}'. Expected one of {list(_BUFFER_JOIN_STYLES)}."
        )
    if quad_segs < 1:
        raise ValueError("quad_segs must be >= 1.")
    return dict(
        resolution=int(quad_segs),
        cap_style=_BUFFER_CAP_STYLES[cap_style],
        join_style=_BUFFER_JOIN_STYLES[join_style],
        mitre_limit=float(mitre_limit),
        single_sided=bool(single_sided),
    )


def _buffer_style_sql(
    quad_segs: int,
    cap_style: str,
    join_style: str,
    mitre_limit: float,
    single_sided: bool,
) -> str:
    """Build the PostGIS/DuckDB ST_Buffer style string."""
    side = "left" if single_sided else "both"
    # DuckDB spatial implements ST_Buffer(geom, dist) — no style string yet.
    # For PostGIS this returns the 3rd parameter.
    return (
        f"quad_segs={int(quad_segs)} endcap={cap_style} "
        f"join={join_style} mitre_limit={float(mitre_limit):.3f} side={side}"
    )


# ---------------------------------------------------------------------------
# Buffer execution strategies
# ---------------------------------------------------------------------------


class _BufferPythonStrategy(ExecutionStrategy):
    """GeoPandas fallback — always available."""

    mode = StrategyMode.PYTHON

    def can_execute(self, ctx: ExecutionContext) -> bool:
        return True

    def execute(self, gdf: gpd.GeoDataFrame, ctx: ExecutionContext) -> gpd.GeoDataFrame:
        distance: float = ctx.params.get("distance", 0.0)
        distance_col = ctx.params.get("distance_col")
        crs_meters: str = ctx.params.get("crs_meters", "EPSG:3857")
        kwargs = _buffer_kwargs(
            ctx.params.get("quad_segs", 8),
            ctx.params.get("cap_style", "round"),
            ctx.params.get("join_style", "round"),
            ctx.params.get("mitre_limit", 5.0),
            ctx.params.get("single_sided", False),
        )

        if gdf.empty:
            return gdf.copy()
        if distance_col and distance_col in gdf.columns:
            dists = gdf[distance_col].fillna(distance).astype(float)
        else:
            dists = distance

        original_crs = gdf.crs
        if original_crs is None:
            result = gdf.copy()
            result["geometry"] = gdf.geometry.buffer(dists, **kwargs)
            return result
        projected = gdf.to_crs(crs_meters)
        buffered = projected.copy()
        buffered["geometry"] = projected.geometry.buffer(dists, **kwargs)
        return buffered.to_crs(original_crs)

    @property
    def priority(self) -> int:
        return 10


def _buffer_params_are_default(ctx: ExecutionContext) -> bool:
    """Return True when only distance/crs_meters are customized.

    DuckDB spatial cannot express styled buffers; when the user asks for
    a non-default quad_segs / cap_style / join_style / single_sided, we
    must fall back to the Python strategy so styles are honored.
    """
    p = ctx.params
    return (
        p.get("quad_segs", 8) == 8
        and p.get("cap_style", "round") == "round"
        and p.get("join_style", "round") == "round"
        and float(p.get("mitre_limit", 5.0)) == 5.0
        and not p.get("single_sided", False)
        and not p.get("distance_col")
    )


class _BufferDuckDBStrategy(ExecutionStrategy):
    """DuckDB spatial strategy — preferred for large local datasets (>50k features).

    Falls back via ``can_execute`` when styled buffers are requested: DuckDB
    spatial does not expose ``ST_Buffer(geom, dist, params)`` so keeping the
    behavior consistent requires deferring to the Python strategy.
    """

    mode = StrategyMode.DUCKDB

    def can_execute(self, ctx: ExecutionContext) -> bool:
        return (
            ctx.engine.backend_name == "duckdb"
            and ctx.feature_count > 50_000
            and _buffer_params_are_default(ctx)
        )

    def execute(self, gdf: gpd.GeoDataFrame, ctx: ExecutionContext) -> gpd.GeoDataFrame:
        distance = float(ctx.params.get("distance", 0.0))
        crs_meters: str = ctx.params.get("crs_meters", "EPSG:3857")

        original_crs = gdf.crs
        if original_crs is not None:
            gdf = gdf.to_crs(crs_meters)

        ctx.engine.register("_input", gdf)
        # register_gdf stores geometry as __wkb binary column;
        # convert back to GEOMETRY via ST_GeomFromWKB before ST_Buffer
        result = ctx.engine.sql_to_gdf(
            f"SELECT *, ST_Buffer(ST_GeomFromWKB(__wkb), {distance}) AS __wkb_buf "
            f"FROM _input"
        )
        # Replace geometry column with the buffered one
        if "__wkb_buf" in result.columns:
            result = result.set_geometry("__wkb_buf").drop(
                columns=["__wkb"], errors="ignore"
            )
            result = result.rename_geometry("geometry")

        if original_crs is not None:
            result = result.to_crs(original_crs)
        return result

    @property
    def priority(self) -> int:
        return 80


class _BufferPostGISStrategy(ExecutionStrategy):
    """PostGIS strategy — server-side buffer, supports full style string."""

    mode = StrategyMode.POSTGIS

    def can_execute(self, ctx: ExecutionContext) -> bool:
        return ctx.engine.backend_name == "postgis"

    def execute(self, gdf: gpd.GeoDataFrame, ctx: ExecutionContext) -> gpd.GeoDataFrame:
        distance = float(ctx.params.get("distance", 0.0))
        crs_meters: str = ctx.params.get("crs_meters", "EPSG:3857")
        style = _buffer_style_sql(
            int(ctx.params.get("quad_segs", 8)),
            ctx.params.get("cap_style", "round"),
            ctx.params.get("join_style", "round"),
            float(ctx.params.get("mitre_limit", 5.0)),
            bool(ctx.params.get("single_sided", False)),
        )

        original_crs = gdf.crs
        if original_crs is not None:
            gdf = gdf.to_crs(crs_meters)

        ctx.engine.register("_input", gdf)
        # Style string is built from validated params (enum-constrained),
        # safe to inline. Distance is a numeric literal.
        result = ctx.engine.sql_to_gdf(
            f"SELECT *, ST_Buffer(geometry::geometry, {distance}, '{style}') "
            f"AS geometry_buf FROM _input"
        )
        if "geometry_buf" in result.columns:
            result = result.set_geometry("geometry_buf").drop(
                columns=["geometry"], errors="ignore"
            )
            result = result.rename_geometry("geometry")

        if original_crs is not None:
            result = result.to_crs(original_crs)
        return result

    @property
    def priority(self) -> int:
        return 100


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------


@register
class BufferCapability(Capability):
    """Creates a fixed-distance buffer around each geometry."""

    name = "buffer"
    description = "Creates a fixed-distance buffer around each geometry."

    _strategies = [
        _BufferPostGISStrategy(),
        _BufferDuckDBStrategy(),
        _BufferPythonStrategy(),
    ]

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        distance: float = 0.0,
        distance_col: str | None = None,
        crs_meters: str = "EPSG:3857",
        quad_segs: int = 8,
        cap_style: str = "round",
        join_style: str = "round",
        mitre_limit: float = 5.0,
        single_sided: bool = False,
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:          Input GeoDataFrame.
            distance:     Uniform buffer distance in units of *crs_meters*.
                          Used when *distance_col* is not provided. Negative
                          values shrink (erosion).
            distance_col: Optional column name holding a per-feature buffer
                          distance. When set, each feature is buffered by
                          its own value. Missing values fall back to
                          *distance*.
            crs_meters:   Metric CRS used for the buffer. The result is
                          reprojected back to the original CRS.
            quad_segs:    Number of linear segments per quadrant for round
                          caps and joins. Default 8.
            cap_style:    'round' | 'flat' | 'square' — line endcap style.
            join_style:   'round' | 'mitre' | 'bevel' — segment join style.
            mitre_limit:  Ratio limit for 'mitre' joins before degenerating
                          to bevel. Only used when join_style='mitre'.
            single_sided: When True, only buffer one side of the geometry
                          (left for positive distance, right for negative).
                          Useful for asymmetric road/river corridors.
        """
        if gdf.empty:
            return gdf.copy()
        kwargs = _buffer_kwargs(quad_segs, cap_style, join_style, mitre_limit, single_sided)

        if distance_col and distance_col in gdf.columns:
            distances = gdf[distance_col].fillna(distance).astype(float)
        else:
            distances = distance

        original_crs = gdf.crs
        if original_crs is None:
            result = gdf.copy()
            result["geometry"] = gdf.geometry.buffer(distances, **kwargs)
            return result
        projected = gdf.to_crs(crs_meters)
        buffered = projected.copy()
        buffered["geometry"] = projected.geometry.buffer(distances, **kwargs)
        return buffered.to_crs(original_crs)

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "distance": {
                    "type": "number",
                    "description": "Uniform buffer distance in units of crs_meters. Negative shrinks.",
                },
                "distance_col": {
                    "type": ["string", "null"],
                    "description": "Optional column name for per-feature buffer distance (overrides distance).",
                },
                "crs_meters": {
                    "type": "string",
                    "default": "EPSG:3857",
                    "description": "Metric CRS used for buffering.",
                },
                "quad_segs": {
                    "type": "integer",
                    "default": 8,
                    "minimum": 1,
                    "description": "Segments per quadrant for round caps/joins.",
                },
                "cap_style": {
                    "type": "string",
                    "default": "round",
                    "enum": ["round", "flat", "square"],
                    "description": "Endcap style for line buffers.",
                },
                "join_style": {
                    "type": "string",
                    "default": "round",
                    "enum": ["round", "mitre", "bevel"],
                    "description": "Segment join style.",
                },
                "mitre_limit": {
                    "type": "number",
                    "default": 5.0,
                    "description": "Mitre ratio limit before degenerating to bevel.",
                },
                "single_sided": {
                    "type": "boolean",
                    "default": False,
                    "description": "Buffer only one side of the geometry.",
                },
            },
        }



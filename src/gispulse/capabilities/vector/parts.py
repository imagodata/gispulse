from __future__ import annotations


import geopandas as gpd

from gispulse.capabilities.base import Capability
from gispulse.capabilities.registry import register


# ---------------------------------------------------------------------------
# Multipart / singlepart transforms
# ---------------------------------------------------------------------------


@register
class MultipartToSinglepartsCapability(Capability):
    """Explodes Multi* geometries into one feature per part.

    Each MultiPoint/MultiLineString/MultiPolygon is split into N rows, one
    per child geometry. Attributes are duplicated across the parts. Useful
    before a 1-1 spatial_join or to compute per-part areas/lengths.

    Example::

        {"reset_index": true, "drop_empty": true}
    """

    name = "multipart_to_singleparts"
    description = (
        "Explodes Multi* geometries — one row per part. Attributes are "
        "duplicated across the resulting features."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        reset_index: bool = True,
        drop_empty: bool = True,
        **_,
    ) -> gpd.GeoDataFrame:
        if gdf.empty:
            return gdf.copy()

        exploded = gdf.explode(index_parts=False, ignore_index=reset_index)
        if drop_empty:
            exploded = exploded[~exploded.geometry.is_empty]
            if reset_index:
                exploded = exploded.reset_index(drop=True)
        return exploded

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "reset_index": {
                    "type": "boolean",
                    "default": True,
                    "description": "Renumber rows from 0 (drops the explode MultiIndex).",
                },
                "drop_empty": {
                    "type": "boolean",
                    "default": True,
                    "description": "Drop empty / null geometries produced by explode.",
                },
            },
        }


@register
class SinglepartsToMultipartCapability(Capability):
    """Collects single-part geometries into Multi* by attribute group.

    Inverse of ``multipart_to_singleparts``. When ``by`` is set, geometries
    sharing the same value(s) are grouped into one Multi*; attributes are
    aggregated by ``agg`` (defaults to ``first``). Without ``by``, *all*
    features collapse into a single Multi* row.

    Example::

        {"by": ["commune"], "agg": "first"}
    """

    name = "singleparts_to_multipart"
    description = (
        "Collects single-part geometries into Multi* features, optionally "
        "grouped by attribute value(s)."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        by: list[str] | str | None = None,
        agg: str | dict[str, str] = "first",
        **_,
    ) -> gpd.GeoDataFrame:
        if gdf.empty:
            return gdf.copy()

        if by is None:
            geom_col = gdf.geometry.name
            from shapely.geometry import (
                GeometryCollection,
                MultiLineString,
                MultiPoint,
                MultiPolygon,
            )

            geoms = [g for g in gdf.geometry if g is not None and not g.is_empty]
            if not geoms:
                return gdf.iloc[0:0].copy()

            # P0 CF3 (beta-test 2026-04-24 v3): mirror the by=<col> homogeneity
            # check on the by=None path. Without it, [Point, LineString, Polygon]
            # crashes deep in the MultiPoint constructor or silently drops
            # off-type features depending on which type comes first.
            _COMPATIBLE_GROUPS = (
                {"Point", "MultiPoint"},
                {"LineString", "MultiLineString"},
                {"Polygon", "MultiPolygon"},
            )
            types_seen = {g.geom_type for g in geoms}
            if len(types_seen) > 1 and not any(
                types_seen <= group for group in _COMPATIBLE_GROUPS
            ):
                raise ValueError(
                    f"Layer contains mixed geometry types {sorted(types_seen)!r} — "
                    f"singleparts_to_multipart with by=None cannot collect them "
                    f"into a single Multi*. Use force_geometry_type first or "
                    f"split per type.",
                )

            sample = geoms[0]
            gtype = sample.geom_type
            if gtype in {"Point", "MultiPoint"}:
                merged = MultiPoint([
                    p for g in geoms
                    for p in (g.geoms if g.geom_type == "MultiPoint" else [g])
                ])
            elif gtype in {"LineString", "MultiLineString"}:
                merged = MultiLineString([
                    line for g in geoms
                    for line in (g.geoms if g.geom_type == "MultiLineString" else [g])
                ])
            elif gtype in {"Polygon", "MultiPolygon"}:
                merged = MultiPolygon([
                    poly for g in geoms
                    for poly in (g.geoms if g.geom_type == "MultiPolygon" else [g])
                ])
            else:
                merged = GeometryCollection(geoms)

            row = gdf.iloc[0].to_dict()
            row[geom_col] = merged
            return gpd.GeoDataFrame([row], geometry=geom_col, crs=gdf.crs)

        cols = [by] if isinstance(by, str) else list(by)
        for c in cols:
            if c not in gdf.columns:
                raise KeyError(f"Group column '{c}' not in layer.")

        # P0-4 (beta-test 2026-04-24): refuse mixed geometry types within a
        # group. dissolve()/unary_union silently collapses Point + LineString +
        # Polygon into a single Polygon, dropping the others. Pre-validate.
        type_groups = (
            gdf.assign(_gtype=gdf.geometry.geom_type)
            .groupby(cols)["_gtype"]
            .nunique()
        )
        bad_groups = type_groups[type_groups > 1]
        if not bad_groups.empty:
            sample_key = bad_groups.index[0]
            raise ValueError(
                f"Group {sample_key!r} contains mixed geometry types — "
                f"singleparts_to_multipart cannot collect them into a single "
                f"Multi*. Use force_geometry_type first or split per type.",
            )
        return gdf.dissolve(by=cols, aggfunc=agg, as_index=False)

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "by": {
                    "type": ["array", "string", "null"],
                    "items": {"type": "string"},
                    "description": "Column(s) to group by. None collects all features into one row.",
                },
                "agg": {
                    "type": ["string", "object"],
                    "default": "first",
                    "description": "Aggregation strategy ('first', 'sum'…) or per-column dict.",
                },
            },
        }


# ---------------------------------------------------------------------------

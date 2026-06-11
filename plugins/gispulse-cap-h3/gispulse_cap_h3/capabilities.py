"""H3 hexagonal analysis capabilities."""

from __future__ import annotations

import geopandas as gpd
import pandas as pd
from shapely.geometry import Polygon

from gispulse.plugins.api import Capability
from gispulse.plugins.api import register_capability as register

# Multi-metric spec: output column name -> (source column or None for count, func).
AggSpec = dict[str, "tuple[str | None, str]"]


def _h3_available() -> bool:
    try:
        import h3  # noqa: F401 — availability probe, not a real use
        return True
    except ImportError:
        return False


@register
class H3AggregateCapability(Capability):
    """Aggregate point data into H3 hexagonal grid cells."""

    name = "h3_aggregate"
    description = "Convert point datasets to H3 hexagonal grids at any resolution with aggregation."

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "resolution": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 15,
                    "default": 7,
                    "description": "H3 resolution (0=coarsest, 15=finest)",
                },
                "agg_column": {
                    "type": "string",
                    "description": "Column to aggregate (count if omitted)",
                },
                "agg_func": {
                    "type": "string",
                    "enum": ["count", "sum", "mean", "min", "max"],
                    "default": "count",
                    "description": "Aggregation function",
                },
                "aggregations": {
                    "type": "object",
                    "description": (
                        "Multi-metric aggregation: output column name -> [source column "
                        "or null for count, function]. Computed in a single H3 pass. "
                        "Overrides agg_column/agg_func when provided."
                    ),
                },
            },
            "additionalProperties": False,
        }

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        *,
        resolution: int = 7,
        agg_column: str | None = None,
        agg_func: str = "count",
        aggregations: AggSpec | None = None,
        **_kw,
    ) -> gpd.GeoDataFrame:
        if not _h3_available():
            raise ImportError(
                "h3 package is required for H3 aggregation. "
                "Install with: pip install h3"
            )
        import h3

        src = gdf.copy()
        if src.crs and src.crs.to_epsg() != 4326:
            src = src.to_crs(epsg=4326)

        # Assign the H3 index to each point ONCE (shared by every metric).
        src["_h3_index"] = src.geometry.apply(
            lambda g: h3.latlng_to_cell(g.y, g.x, resolution)
        )

        # One groupby, one column per requested metric. Single-metric callers
        # (agg_column/agg_func) keep the historical {"value": ...} shape and its
        # lenient "missing column -> count" fallback. Explicit multi-metric specs
        # are validated up front: a missing source column raises instead of
        # silently producing a count where a sum/mean was asked.
        if aggregations is None:
            specs: AggSpec = {"value": (agg_column, agg_func)}
        else:
            if not aggregations:
                raise ValueError(
                    "aggregations must be a non-empty mapping {name: (column or None, func)}"
                )
            for name, (col, _func) in aggregations.items():
                if col is not None and col not in src.columns:
                    raise ValueError(
                        f"aggregations[{name!r}]: source column {col!r} not found in input"
                    )
            specs = aggregations
        groups = src.groupby("_h3_index")
        columns = {
            name: (groups[col].agg(func) if col and col in src.columns else groups.size())
            for name, (col, func) in specs.items()
        }
        grouped = pd.DataFrame(columns).reset_index().rename(columns={"_h3_index": "h3_index"})

        # Convert H3 cells to polygons ONCE (boundary is shared across metrics).
        def h3_to_polygon(h3_index):
            boundary = h3.cell_to_boundary(h3_index)
            return Polygon([(lng, lat) for lat, lng in boundary])

        grouped["geometry"] = grouped["h3_index"].apply(h3_to_polygon)
        return gpd.GeoDataFrame(grouped, geometry="geometry", crs="EPSG:4326")

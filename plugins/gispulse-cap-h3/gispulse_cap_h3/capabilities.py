"""H3 hexagonal analysis capabilities."""

from __future__ import annotations

import geopandas as gpd
from shapely.geometry import Polygon

from capabilities.base import Capability
from capabilities.registry import register


def _h3_available() -> bool:
    try:
        import h3
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

        # Assign H3 index to each point
        src["_h3_index"] = src.geometry.apply(
            lambda g: h3.latlng_to_cell(g.y, g.x, resolution)
        )

        # Aggregate
        if agg_column and agg_column in src.columns:
            grouped = src.groupby("_h3_index")[agg_column].agg(agg_func).reset_index()
            grouped.columns = ["h3_index", "value"]
        else:
            grouped = src.groupby("_h3_index").size().reset_index(name="value")
            grouped.columns = ["h3_index", "value"]

        # Convert H3 cells to polygons
        def h3_to_polygon(h3_index):
            boundary = h3.cell_to_boundary(h3_index)
            return Polygon([(lng, lat) for lat, lng in boundary])

        grouped["geometry"] = grouped["h3_index"].apply(h3_to_polygon)
        return gpd.GeoDataFrame(grouped, geometry="geometry", crs="EPSG:4326")

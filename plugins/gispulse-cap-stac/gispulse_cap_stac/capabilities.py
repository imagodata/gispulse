"""STAC catalog connector capabilities."""

from __future__ import annotations

import geopandas as gpd
from shapely.geometry import shape

from capabilities.base import Capability
from capabilities.registry import register


@register
class STACSearchCapability(Capability):
    """Search a STAC catalog and return item footprints as a GeoDataFrame."""

    name = "stac_search"
    description = "Search a STAC API catalog and return matching item footprints."

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "catalog_url": {
                    "type": "string",
                    "description": "STAC API root URL",
                },
                "collections": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Collection IDs to search",
                },
                "bbox": {
                    "type": "array",
                    "items": {"type": "number"},
                    "minItems": 4,
                    "maxItems": 4,
                    "description": "Bounding box [west, south, east, north]",
                },
                "datetime_range": {
                    "type": "string",
                    "description": "ISO 8601 datetime range (e.g. 2024-01-01/2024-12-31)",
                },
                "max_items": {
                    "type": "integer",
                    "default": 100,
                    "description": "Maximum items to return",
                },
            },
            "required": ["catalog_url"],
            "additionalProperties": False,
        }

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        *,
        catalog_url: str | None = None,
        collections: list[str] | None = None,
        bbox: list[float] | None = None,
        datetime_range: str | None = None,
        max_items: int = 100,
        **_kw,
    ) -> gpd.GeoDataFrame:
        if not catalog_url:
            raise ValueError("catalog_url is required")

        try:
            from pystac_client import Client
        except ImportError:
            raise ImportError(
                "pystac-client is required for STAC search. "
                "Install with: pip install pystac-client"
            )

        client = Client.open(catalog_url)
        search_kwargs: dict = {"max_items": max_items}
        if collections:
            search_kwargs["collections"] = collections
        if bbox:
            search_kwargs["bbox"] = bbox
        if datetime_range:
            search_kwargs["datetime"] = datetime_range

        search = client.search(**search_kwargs)
        items = list(search.items())

        if not items:
            return gpd.GeoDataFrame(
                columns=["id", "collection", "datetime", "geometry"],
                geometry="geometry",
                crs="EPSG:4326",
            )

        records = []
        for item in items:
            records.append({
                "id": item.id,
                "collection": item.collection_id,
                "datetime": str(item.datetime) if item.datetime else None,
                "geometry": shape(item.geometry),
                **{f"prop_{k}": v for k, v in (item.properties or {}).items()
                   if k not in ("datetime",)},
            })

        return gpd.GeoDataFrame(records, geometry="geometry", crs="EPSG:4326")

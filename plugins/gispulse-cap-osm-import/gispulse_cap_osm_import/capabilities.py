"""OSM import capabilities — import and filter OpenStreetMap data."""

from __future__ import annotations

import json

import geopandas as gpd
from shapely.geometry import shape

from capabilities.base import Capability
from capabilities.registry import register


@register
class OSMImportCapability(Capability):
    """Import features from an OSM/Overpass JSON extract."""

    name = "osm_import"
    description = "Import features from OSM GeoJSON/Overpass JSON with tag-based filtering."

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "geojson_path": {
                    "type": "string",
                    "description": "Path to a GeoJSON file containing OSM data",
                },
                "tag_filter": {
                    "type": "object",
                    "description": "OSM tag key-value pairs to filter on (e.g. {\"building\": \"yes\"})",
                    "additionalProperties": {"type": "string"},
                },
            },
            "additionalProperties": False,
        }

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        *,
        geojson_path: str | None = None,
        tag_filter: dict[str, str] | None = None,
        **_kw,
    ) -> gpd.GeoDataFrame:
        if geojson_path:
            imported = gpd.read_file(geojson_path)
        else:
            imported = gdf.copy()

        if tag_filter:
            for key, value in tag_filter.items():
                if key in imported.columns:
                    if value == "*":
                        imported = imported[imported[key].notna()]
                    else:
                        imported = imported[imported[key] == value]

        return imported


@register
class OSMTagExtractCapability(Capability):
    """Extract and flatten OSM tags into columns."""

    name = "osm_tag_extract"
    description = "Extract nested OSM tags JSON into flat GeoDataFrame columns."

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "tags_column": {
                    "type": "string",
                    "default": "tags",
                    "description": "Column containing tags (JSON string or dict)",
                },
                "extract_keys": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tag keys to extract as columns (empty = all)",
                },
            },
            "additionalProperties": False,
        }

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        *,
        tags_column: str = "tags",
        extract_keys: list[str] | None = None,
        **_kw,
    ) -> gpd.GeoDataFrame:
        result = gdf.copy()
        if tags_column not in result.columns:
            return result

        def parse_tags(val):
            if isinstance(val, dict):
                return val
            if isinstance(val, str):
                try:
                    return json.loads(val)
                except (json.JSONDecodeError, TypeError):
                    return {}
            return {}

        tags_series = result[tags_column].apply(parse_tags)

        if extract_keys:
            for key in extract_keys:
                result[f"tag_{key}"] = tags_series.apply(lambda t, k=key: t.get(k))
        else:
            all_keys = set()
            for t in tags_series:
                all_keys.update(t.keys())
            for key in sorted(all_keys):
                result[f"tag_{key}"] = tags_series.apply(lambda t, k=key: t.get(k))

        return result

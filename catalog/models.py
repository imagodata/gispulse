"""Domain models for the GIS catalog system."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class CatalogDomain(str, Enum):
    PROJECTION = "projection"
    BASEMAP = "basemap"
    FLUX = "flux"
    OPENDATA = "opendata"


class FluxProtocol(str, Enum):
    WMS = "wms"
    WFS = "wfs"
    WMTS = "wmts"
    TMS = "tms"
    XYZ = "xyz"
    OGC_FEATURES = "ogc-features"  # OGC API Features
    OGC_TILES = "ogc-tiles"        # OGC API Tiles


@dataclass
class CatalogEntry:
    """Universal catalog entry."""

    id: str
    domain: CatalogDomain
    provider: str
    name: str
    description: str = ""
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProjectionEntry(CatalogEntry):
    epsg_code: int = 0
    proj4: str = ""
    wkt: str = ""
    bounds: list[float] | None = None  # [west, south, east, north]
    area_of_use: str = ""
    unit: str = "metre"


@dataclass
class BasemapEntry(CatalogEntry):
    url_template: str = ""
    protocol: FluxProtocol = FluxProtocol.XYZ
    attribution: str = ""
    max_zoom: int = 19
    thumbnail_url: str | None = None


@dataclass
class FluxEntry(CatalogEntry):
    """A WMS/WFS/WMTS/TMS service endpoint."""

    service_url: str = ""
    protocol: FluxProtocol = FluxProtocol.WMS
    layer_name: str = ""
    attribution: str = ""
    bounds: list[float] | None = None
    default_crs: str = "EPSG:4326"


@dataclass
class OpenDataEntry(CatalogEntry):
    """An open data source (downloadable or API-accessible)."""

    source_url: str = ""
    format: str = ""
    license: str = ""
    update_frequency: str = ""
    spatial_coverage: str = ""
    download_url: str | None = None

"""GISPulse persistence layer — multi-format I/O, DuckDB, PostGIS, in-memory repository."""

from persistence.engine import SpatialEngine
from persistence.engine_factory import create_spatial_engine
from persistence.repository import InMemoryRepository, Repository
from persistence.sqlite_repository import SQLiteRepository

# GeoPackage engine (default backend — zero-dependency).
try:
    from persistence.gpkg_engine import GeoPackageEngine
    from persistence.gpkg_repository import GpkgRepository

    _GPKG_ENGINE_AVAILABLE = True
except ImportError:
    _GPKG_ENGINE_AVAILABLE = False
    GeoPackageEngine = None  # type: ignore[assignment,misc]
    GpkgRepository = None  # type: ignore[assignment,misc]

# PostGIS is now optional (requires `pip install gispulse[postgis]`).
try:
    from persistence.postgis import PostGISConnection

    _POSTGIS_AVAILABLE = True
except ImportError:
    _POSTGIS_AVAILABLE = False
    PostGISConnection = None  # type: ignore[assignment,misc]

# DuckDB session engine (Phase 1 default).
try:
    from persistence.duckdb_engine import DuckDBSession
    # Lot 3: change-log adapter wrapping DuckDBSession for live-sync.
    from persistence.duckdb_engine_adapter import DuckDBSpatialEngine

    _DUCKDB_AVAILABLE = True
except ImportError:
    _DUCKDB_AVAILABLE = False
    DuckDBSession = None  # type: ignore[assignment,misc]
    DuckDBSpatialEngine = None  # type: ignore[assignment,misc]

# DuckDB-PostGIS hybrid bridge (Phase 4).
try:
    from persistence.bridge import DuckDBPostGISBridge, HybridEngine

    _HYBRID_AVAILABLE = True
except ImportError:
    _HYBRID_AVAILABLE = False
    DuckDBPostGISBridge = None  # type: ignore[assignment,misc]
    HybridEngine = None  # type: ignore[assignment,misc]

# fiona est une dépendance optionnelle au runtime (absente dans certains envs).
# On importe les helpers GPKG de façon lazy pour ne pas casser les imports
# d'autres modules (orchestration, rules…) quand fiona n'est pas installé.
try:
    from persistence.gpkg import dataset_from_gpkg, list_layers, read_gpkg, write_gpkg

    _GPKG_AVAILABLE = True
except ImportError:
    _GPKG_AVAILABLE = False

    def _gpkg_unavailable(*_args, **_kwargs):  # type: ignore[misc]
        raise ImportError(
            "GPKG helpers require 'fiona'. Install with: pip install fiona"
        )

    dataset_from_gpkg = _gpkg_unavailable  # type: ignore[assignment]
    list_layers = _gpkg_unavailable  # type: ignore[assignment]
    read_gpkg = _gpkg_unavailable  # type: ignore[assignment]
    write_gpkg = _gpkg_unavailable  # type: ignore[assignment]

# Unified multi-format vector I/O (fiona/geopandas required).
try:
    from persistence.io import (
        dataset_from_file,
        detect_format,
        read_geoparquet,
        read_vector,
        supported_extensions,
        write_geoparquet,
        write_vector,
    )

    _IO_AVAILABLE = True
except ImportError:
    _IO_AVAILABLE = False

    def _io_unavailable(*_args, **_kwargs):  # type: ignore[misc]
        raise ImportError(
            "Multi-format I/O requires 'fiona' and 'geopandas'. "
            "Install with: pip install fiona geopandas"
        )

    dataset_from_file = _io_unavailable  # type: ignore[assignment]
    detect_format = _io_unavailable  # type: ignore[assignment]
    read_vector = _io_unavailable  # type: ignore[assignment]
    supported_extensions = _io_unavailable  # type: ignore[assignment]
    write_vector = _io_unavailable  # type: ignore[assignment]

# Raster I/O (rasterio required).
try:
    from persistence.raster_io import (
        dataset_from_raster,
        read_raster,
        read_raster_metadata,
        raster_layer_from_file,
        write_raster,
    )

    _RASTER_IO_AVAILABLE = True
except ImportError:
    _RASTER_IO_AVAILABLE = False

    def _raster_io_unavailable(*_args, **_kwargs):  # type: ignore[misc]
        raise ImportError(
            "Raster I/O requires 'rasterio'. Install with: pip install rasterio"
        )

    dataset_from_raster = _raster_io_unavailable  # type: ignore[assignment]
    read_raster = _raster_io_unavailable  # type: ignore[assignment]
    read_raster_metadata = _raster_io_unavailable  # type: ignore[assignment]
    raster_layer_from_file = _raster_io_unavailable  # type: ignore[assignment]
    write_raster = _raster_io_unavailable  # type: ignore[assignment]


__all__ = [
    # GPKG (legacy, kept for backward compat)
    "dataset_from_gpkg",
    "list_layers",
    "read_gpkg",
    "write_gpkg",
    "_GPKG_AVAILABLE",
    # Multi-format vector I/O
    "dataset_from_file",
    "detect_format",
    "read_geoparquet",
    "read_vector",
    "write_geoparquet",
    "write_vector",
    "supported_extensions",
    "_IO_AVAILABLE",
    # Raster I/O
    "dataset_from_raster",
    "read_raster",
    "read_raster_metadata",
    "raster_layer_from_file",
    "write_raster",
    "_RASTER_IO_AVAILABLE",
    # DuckDB (Phase 1)
    "DuckDBSession",
    "DuckDBSpatialEngine",
    "_DUCKDB_AVAILABLE",
    # Hybrid bridge (Phase 4)
    "DuckDBPostGISBridge",
    "HybridEngine",
    "_HYBRID_AVAILABLE",
    # PostGIS (optional)
    "PostGISConnection",
    "_POSTGIS_AVAILABLE",
    # Phase 3 — Engine abstraction
    "SpatialEngine",
    "create_spatial_engine",
    # GPKG engine (Phase 5 — unified project file)
    "GeoPackageEngine",
    "GpkgRepository",
    "_GPKG_ENGINE_AVAILABLE",
    # Core
    "Repository",
    "InMemoryRepository",
    "SQLiteRepository",
]

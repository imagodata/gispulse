"""Engine factory — instantiate the right SpatialEngine from configuration.

Usage::

    from gispulse.persistence.engine_factory import create_engine

    engine = create_engine()          # reads GISPULSE_ENGINE env var
    engine = create_engine("postgis") # explicit override

Environment variables:
    GISPULSE_ENGINE        "gpkg" (default), "duckdb", "postgis", or "hybrid"
    GISPULSE_DSN           PostgreSQL DSN (required when engine=postgis)
    GISPULSE_TIER          "community" (default), "pro", or "enterprise"
    GISPULSE_LICENSE_KEY   Required for paid tiers
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from gispulse.core.config import settings
from gispulse.persistence.engine import SpatialEngine
from gispulse.persistence.tier import enforce_engine_tier

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Backend registry
# ---------------------------------------------------------------------------

_BACKENDS: dict[str, Callable[..., SpatialEngine]] = {}


def register_engine_backend(
    name: str,
    factory: Callable[..., SpatialEngine],
    *,
    override: bool = False,
) -> None:
    """Register a spatial engine backend factory.

    Args:
        name:     Backend identifier (e.g. ``"duckdb"``, ``"postgis"``).
        factory:  Callable accepting keyword arguments ``dsn`` and
                  ``duckdb_path`` and returning a :class:`SpatialEngine`.
        override: If *True*, allow replacing an existing backend.

    Raises:
        ValueError: If *name* is already registered and *override* is *False*.
    """
    if name in _BACKENDS and not override:
        raise ValueError(
            f"Engine backend {name!r} already registered. "
            f"Pass override=True to replace."
        )
    _BACKENDS[name] = factory
    logger.info("engine_backend_registered: %s", name)


# ---------------------------------------------------------------------------
# Built-in backends
# ---------------------------------------------------------------------------

def _duckdb_factory(*, dsn: str | None = None, duckdb_path: str = ":memory:", **_kw: Any) -> SpatialEngine:
    # Lot 3: return the change-log adapter (subclass of DuckDBSession)
    # so the lifespan can wire it into WatcherRegistry without further
    # branching. All DuckDBSession behaviour is preserved via inheritance;
    # the adapter only adds get_pending_changes / mark_changes_processed
    # and a DML-proxy ``execute``.
    from gispulse.persistence.duckdb_engine_adapter import DuckDBSpatialEngine

    return DuckDBSpatialEngine(database=duckdb_path)


def _postgis_factory(*, dsn: str | None = None, duckdb_path: str = ":memory:", **_kw: Any) -> SpatialEngine:
    dsn = dsn or settings.database.dsn
    if not dsn:
        raise ValueError(
            "PostGIS engine requires a DSN. "
            "Set GISPULSE_DSN or pass dsn= explicitly."
        )
    from gispulse.persistence.postgis import PostGISConnection

    return PostGISConnection(dsn=dsn)


def _hybrid_factory(*, dsn: str | None = None, duckdb_path: str = ":memory:", **_kw: Any) -> SpatialEngine:
    dsn = dsn or settings.database.dsn
    if not dsn:
        raise ValueError(
            "Hybrid engine requires a PostGIS DSN. "
            "Set GISPULSE_DSN or pass dsn= explicitly."
        )
    from gispulse.persistence.bridge import HybridEngine

    return HybridEngine(pg_dsn=dsn)


def _gpkg_factory(*, dsn: str | None = None, duckdb_path: str = ":memory:", gpkg_path: str | None = None, **_kw: Any) -> SpatialEngine:
    path = gpkg_path or settings.database.gpkg_path
    from gispulse.persistence.gpkg_engine import GeoPackageEngine

    return GeoPackageEngine(path=path)


def _spatialite_factory(*, dsn: str | None = None, duckdb_path: str = ":memory:", spatialite_path: str | None = None, gpkg_path: str | None = None, **_kw: Any) -> SpatialEngine:
    # Accept ``gpkg_path`` as an alias to keep call sites uniform with
    # the GPKG factory — both engines are file-based SQLite, only the
    # geometry encoding differs.
    path = spatialite_path or gpkg_path or settings.database.gpkg_path
    from gispulse.persistence.spatialite_engine import SpatiaLiteEngine

    return SpatiaLiteEngine(path=path)


def _duckdb_diff_factory(*, dsn: str | None = None, duckdb_path: str = ":memory:", file_path: str | None = None, gpkg_path: str | None = None, **_kw: Any) -> SpatialEngine:
    # Accept ``gpkg_path`` as an alias for routing-uniformity — the
    # config_loader passes the dataset URI through that name for every
    # file-backed engine. The ``duckdb_path`` kwarg is silently dropped;
    # this engine uses an ephemeral in-memory DuckDB for diff snapshots,
    # persisted as a sidecar next to the file.
    path = file_path or gpkg_path or settings.database.gpkg_path
    from gispulse.persistence.duckdb_diff_engine import DuckDBDiffEngine

    return DuckDBDiffEngine(path=path)


def _register_builtins() -> None:
    """Register the built-in engine backends."""
    register_engine_backend("duckdb", _duckdb_factory)
    register_engine_backend("postgis", _postgis_factory)
    register_engine_backend("hybrid", _hybrid_factory)
    register_engine_backend("gpkg", _gpkg_factory)
    register_engine_backend("spatialite", _spatialite_factory)
    register_engine_backend("duckdb_diff", _duckdb_diff_factory)


# ---------------------------------------------------------------------------
# Entry-point discovery
# ---------------------------------------------------------------------------

def _discover_engine_plugins() -> list[dict[str, str]]:
    """Discover engine backends from installed packages via entry-points.

    Scans the ``gispulse.engine_backends`` entry-point group. Each
    entry-point must resolve to a factory callable.

    Returns:
        List of dicts with ``name``, ``module``, and ``status``.
    """
    loaded: list[dict[str, str]] = []
    try:
        from importlib.metadata import entry_points

        eps = entry_points(group="gispulse.engine_backends")
        for ep in eps:
            try:
                factory = ep.load()
                register_engine_backend(ep.name, factory)
                logger.info("engine_plugin_loaded: %s (%s)", ep.name, ep.value)
                loaded.append({"name": ep.name, "module": ep.value, "status": "ok"})
            except Exception as exc:
                logger.warning("engine_plugin_failed: %s — %s", ep.name, exc)
                loaded.append({"name": ep.name, "module": ep.value, "status": f"error: {exc}"})
    except Exception:
        pass
    return loaded


# ---------------------------------------------------------------------------
# Initialise on import
# ---------------------------------------------------------------------------

_register_builtins()
_discover_engine_plugins()


# ---------------------------------------------------------------------------
# Public factory
# ---------------------------------------------------------------------------

def create_spatial_engine(
    backend: str | None = None,
    *,
    dsn: str | None = None,
    duckdb_path: str = ":memory:",
) -> SpatialEngine:
    """Create a :class:`SpatialEngine` based on configuration.

    Args:
        backend:     ``"gpkg"``, ``"duckdb"``, ``"postgis"``, or ``"hybrid"``.
                     Falls back to ``GISPULSE_ENGINE`` env var, then ``"gpkg"``.
        dsn:         PostgreSQL DSN.  Falls back to ``GISPULSE_DSN``.
        duckdb_path: DuckDB database path (default ``:memory:``).

    Returns:
        Ready-to-use (but **not yet opened**) engine instance.

    Raises:
        ValueError: If backend requires a DSN but none is provided.
        ValueError: If backend is unknown.
        TierError:  If the current tier does not allow the requested backend.
    """
    backend = backend or settings.engine.backend

    # --- Tier gating ---
    enforce_engine_tier(backend)

    factory = _BACKENDS.get(backend)
    if factory is None:
        available = sorted(_BACKENDS.keys())
        raise ValueError(
            f"Unknown engine backend: {backend!r}. "
            f"Available: {available}"
        )
    return factory(dsn=dsn, duckdb_path=duckdb_path)

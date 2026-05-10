"""
FastAPI dependency providers for GISPulse HTTP adapter.

All repositories and engine instances are held on ``app.state`` and
exposed through typed dependency functions so routers stay decoupled
from construction details.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException, Request

from persistence.engine import SpatialEngine
from persistence.map_io import MapRepository
from persistence.repository import Repository
from rules.engine import RuleEngine
from orchestration.runner import JobRunner
from gispulse.adapters.http.event_hub import EventHub


def get_rule_repo(request: Request) -> Repository:
    """Return the shared Rule repository from app state."""
    return request.app.state.rule_repo  # type: ignore[return-value]


def get_job_repo(request: Request) -> Repository:
    """Return the shared Job repository from app state."""
    return request.app.state.job_repo  # type: ignore[return-value]


def get_dataset_repo(request: Request) -> Repository:
    """Return the shared Dataset repository from app state."""
    return request.app.state.dataset_repo  # type: ignore[return-value]


def get_scenario_repo(request: Request) -> Repository:
    """Return the shared Scenario repository from app state."""
    return request.app.state.scenario_repo  # type: ignore[return-value]


def get_rule_engine(request: Request) -> RuleEngine:
    """Return the shared RuleEngine from app state."""
    return request.app.state.rule_engine  # type: ignore[return-value]


def get_job_runner(request: Request) -> JobRunner:
    """Return the shared JobRunner from app state."""
    return request.app.state.job_runner  # type: ignore[return-value]


def get_viewer_state(request: Request) -> dict:
    """Return the shared viewer state from app state.

    The viewer state contains:
    - ``file_path``: Path to the loaded spatial file.
    - ``layer_cache``: dict mapping layer names to cached metadata + GeoDataFrames.
    """
    return request.app.state.viewer_state  # type: ignore[return-value]


def get_data_dir(request: Request) -> Path:
    """Return the directory where uploaded datasets are stored."""
    return request.app.state.data_dir  # type: ignore[return-value]


def get_results_dir(request: Request) -> Path:
    """Return the directory where job result files are stored."""
    return request.app.state.results_dir  # type: ignore[return-value]


def get_spatial_engine(request: Request) -> SpatialEngine:
    """Return the active SpatialEngine (DuckDB or PostGIS) from app state."""
    return request.app.state.spatial_engine  # type: ignore[return-value]


def get_project_repo(request: Request) -> Repository:
    """Return the shared Project repository from app state."""
    return request.app.state.project_repo  # type: ignore[return-value]


def get_trigger_repo(request: Request) -> Repository:
    """Return the shared Trigger repository from app state."""
    return request.app.state.trigger_repo  # type: ignore[return-value]


def get_relation_repo(request: Request) -> Repository:
    """Return the shared TableRelation repository from app state."""
    return request.app.state.relation_repo  # type: ignore[return-value]


def get_event_hub(request: Request) -> EventHub:
    """Return the shared EventHub from app state."""
    return request.app.state.event_hub  # type: ignore[return-value]


def get_watcher_registry(request: Request):
    """Return the shared WatcherRegistry from app state.

    May be ``None`` if the app was started in portal mode or before the
    lifespan finished its startup phase. Callers must handle ``None``.
    """
    return getattr(request.app.state, "watcher_registry", None)


from persistence.storage import DatasetStorage  # noqa: E402


def get_storage(request: Request) -> DatasetStorage:
    """Return the shared DatasetStorage from app state."""
    return request.app.state.storage  # type: ignore[return-value]


from persistence.session_provisioner import SessionProvisioner  # noqa: E402


def get_session_provisioner(request: Request) -> SessionProvisioner:
    """Return the shared SessionProvisioner from app state."""
    return request.app.state.session_provisioner  # type: ignore[return-value]


from persistence.auth_repository import AuthRepository  # noqa: E402


def get_auth_repo(request: Request) -> AuthRepository | None:
    """Return the shared AuthRepository from app state, or None if RBAC is disabled."""
    return getattr(request.app.state, "auth_repo", None)


from orchestration.job_queue import JobQueue  # noqa: E402


def get_job_queue(request: Request) -> JobQueue:
    """Return the shared JobQueue from app state."""
    return request.app.state.job_queue  # type: ignore[return-value]


def get_postgis_sqlalchemy_engine(request: Request):  # noqa: ANN201
    """Return a cached SQLAlchemy engine for PostGIS, or None if no DSN.

    Creates the engine once on app.state and reuses it across requests
    to avoid the overhead of creating a new connection pool per request.
    """
    from core.config import settings

    existing = getattr(request.app.state, "_sqla_engine", None)
    if existing is not None:
        return existing

    dsn = settings.database.postgis_dsn
    if not dsn:
        return None

    from sqlalchemy import create_engine

    if dsn.startswith("postgresql://") and "+psycopg2" not in dsn:
        dsn = dsn.replace("postgresql://", "postgresql+psycopg2://", 1)

    engine = create_engine(dsn, future=True, pool_size=5, max_overflow=10)
    request.app.state._sqla_engine = engine
    return engine


from orchestration.metering import Metering  # noqa: E402


def get_metering(request: Request) -> Metering:
    """Return the shared Metering service from app state."""
    return request.app.state.metering  # type: ignore[return-value]


from orchestration.scheduler import PipelineScheduler  # noqa: E402


def get_scheduler(request: Request) -> PipelineScheduler | None:
    """Return the shared PipelineScheduler from app state, or None."""
    return getattr(request.app.state, "scheduler", None)


def get_map_repo(request: Request) -> MapRepository:
    """Return the shared MapRepository (Cocarte) from app state.

    Raises 503 if the instance was started in in-memory mode (the Map
    repository is SQLite-backed and has no in-memory variant).
    """
    repo = getattr(request.app.state, "map_repo", None)
    if repo is None:
        raise HTTPException(
            status_code=503,
            detail="Cocarte map endpoints require sqlite storage mode.",
        )
    return repo

"""
GISPulse FastAPI application factory.

Single entry-point for both modes:
- ``create_app(mode="full")``   — full API with auth, rate limiting, all routers
- ``create_app(mode="portal")`` — portal mode used by ``gispulse serve``

Authentication is controlled via the ``GISPULSE_API_KEYS`` environment
variable (comma-separated list of valid keys). Absent or empty = auth disabled
(development mode).
"""

from __future__ import annotations

import inspect
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal
from typing import get_type_hints

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request

from gispulse.core.config import settings as cfg
from gispulse.core.plugin_contracts import PluginHostContext, RouterFactory
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from gispulse.adapters.http.auth import get_api_key_validator, require_scope
from gispulse.core.cache import BoundedLayerCache
from gispulse.adapters.http.error_handlers import register_error_handlers
from gispulse.adapters.http.event_hub import get_event_hub
from gispulse.adapters.http.routers.capabilities_router import router as capabilities_router
from gispulse.adapters.http.routers.catalog_router import router as catalog_router
from gispulse.adapters.http.routers.datasets_router import router as datasets_router
from gispulse.adapters.http.routers.examples_router import router as examples_router
from gispulse.adapters.http.routers.filter_router import router as filter_router
from gispulse.adapters.http.routers.esb_router import router as esb_router
from gispulse.adapters.http.routers.jobs_router import router as jobs_router, recover_stale_jobs
from gispulse.adapters.http.routers.portal_router import router as portal_router
from gispulse.adapters.http.routers.projects_router import router as projects_router
from gispulse.adapters.http.routers.rules_router import router as rules_router
from gispulse.adapters.http.routers.scenarios_router import router as scenarios_router
from gispulse.adapters.http.routers.sessions_router import router as sessions_router
from gispulse.adapters.http.routers.schedules_router import router as schedules_router
from gispulse.adapters.http.routers.system_router import router as system_router
from gispulse.adapters.http.routers.templates_router import router as templates_router
from gispulse.adapters.http.routers.triggers_router import router as triggers_router
from gispulse.adapters.http.routers.watchers_router import router as watchers_router
from gispulse.adapters.http.routers.relations_router import router as relations_router
from gispulse.adapters.http.routers.marketplace_router import router as marketplace_router
from gispulse.adapters.http.routers.pipelines_router import router as pipelines_router
from gispulse.adapters.http.routers.ws_router import router as ws_router
from gispulse.adapters.http.schemas import CapabilityInfo, HealthResponse
from gispulse.capabilities import registry
from gispulse.core.logging import get_logger
from gispulse.core.models import Dataset, Job, Project, Rule, Scenario, TableRelation, Trigger
from gispulse.core.observability import MetricsCollector
from gispulse.orchestration.runner import JobRunner
from gispulse.persistence.engine_factory import create_spatial_engine
from gispulse.persistence.repository import InMemoryRepository
from gispulse.persistence.session_provisioner import SessionProvisioner
from gispulse.persistence.sqlite_repository import SQLiteRepository
from gispulse.rules.engine import RuleEngine

from gispulse import __version__ as _VERSION

log = get_logger(__name__)

_PORTAL_DIST = Path(__file__).resolve().parents[4] / "portal" / "dist"
_VIEWER_DIST = Path(__file__).resolve().parents[4] / "viewer" / "dist"


async def _run_plugin_startup(app: FastAPI) -> None:
    hub = getattr(app.state, "plugin_hub", None)
    if hub is None:
        return
    for plugin in hub.lifecycle:
        try:
            result = plugin.on_startup(app)
            if inspect.isawaitable(result):
                await result
            log.info("plugin_lifecycle_startup_complete", plugin=plugin.name)
        except Exception as exc:
            log.warning("plugin_lifecycle_startup_failed", plugin=plugin.name, error=str(exc))


async def _run_plugin_shutdown(app: FastAPI) -> None:
    hub = getattr(app.state, "plugin_hub", None)
    if hub is None:
        return
    for plugin in reversed(hub.lifecycle):
        try:
            result = plugin.on_shutdown(app)
            if inspect.isawaitable(result):
                await result
            log.info("plugin_lifecycle_shutdown_complete", plugin=plugin.name)
        except Exception as exc:
            log.warning("plugin_lifecycle_shutdown_failed", plugin=plugin.name, error=str(exc))


def _load_api_keys() -> set[str] | None:
    return cfg.api.get_api_keys_set()


def _setup_repos(app: FastAPI, storage_mode: str, db_path: Path) -> None:
    """Attach SQLite or in-memory repositories to app.state."""
    if storage_mode == "sqlite":
        app.state.rule_repo = SQLiteRepository(Rule, db_path=db_path)
        app.state.job_repo = SQLiteRepository(Job, db_path=db_path)
        app.state.dataset_repo = SQLiteRepository(Dataset, db_path=db_path)
        app.state.scenario_repo = SQLiteRepository(Scenario, db_path=db_path)
        app.state.trigger_repo = SQLiteRepository(Trigger, db_path=db_path)
        app.state.project_repo = SQLiteRepository(Project, db_path=db_path)
        app.state.relation_repo = SQLiteRepository(TableRelation, db_path=db_path)
    else:
        app.state.rule_repo = InMemoryRepository()
        app.state.job_repo = InMemoryRepository()
        app.state.dataset_repo = InMemoryRepository()
        app.state.scenario_repo = InMemoryRepository()
        app.state.trigger_repo = InMemoryRepository()
        app.state.project_repo = InMemoryRepository()
        app.state.relation_repo = InMemoryRepository()

    # RBAC auth repository (opt-in via GISPULSE_RBAC=true)
    rbac_enabled = cfg.api.rbac
    if rbac_enabled:
        from gispulse.persistence.auth_repository import AuthRepository
        app.state.auth_repo = AuthRepository(db_path=db_path)
        log.info("rbac_enabled", db_path=str(db_path))
    else:
        app.state.auth_repo = None

    # OIDC SSO (Enterprise tier — opt-in via GISPULSE_OIDC_ISSUER)
    app.state.oidc_provider = None
    try:
        from gispulse.adapters.http.oidc import OIDCConfig, OIDCProvider

        oidc_config = OIDCConfig.from_env()
        if oidc_config is not None:
            app.state.oidc_provider = OIDCProvider(oidc_config)
            log.info("oidc_configured", issuer=oidc_config.issuer_url)
            # OIDC requires RBAC — enable it if not already
            if app.state.auth_repo is None:
                from gispulse.persistence.auth_repository import AuthRepository
                app.state.auth_repo = AuthRepository(db_path=db_path)
                log.info("rbac_auto_enabled_for_oidc", db_path=str(db_path))
    except ImportError:
        log.debug("oidc_not_available", msg="PyJWT not installed — SSO disabled")


def _setup_services(app: FastAPI) -> None:
    """Attach RuleEngine, JobRunner, EventHub, JobQueue, Metering to app.state."""
    app.state.rule_engine = RuleEngine(repository=app.state.rule_repo)
    app.state.job_runner = JobRunner(
        repository=app.state.rule_repo,
        rule_engine=app.state.rule_engine,
    )
    app.state.event_hub = get_event_hub()

    # Job queue and metering (Mode C / Pro)
    from gispulse.orchestration.job_queue_factory import create_job_queue
    from gispulse.orchestration.metering import create_metering
    app.state.job_queue = create_job_queue()
    app.state.metering = create_metering()


def _setup_cors(app: FastAPI, origins: list[str]) -> None:
    from fastapi.middleware.cors import CORSMiddleware

    allow_creds = origins != ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=allow_creds,
    )


def _create_plugin_router(
    factory: RouterFactory,
    app: FastAPI,
    context: PluginHostContext,
) -> APIRouter | None:
    """Create a plugin router with context support and legacy fallback."""
    parameters = list(inspect.signature(factory.create).parameters.values())
    first_param = parameters[0] if parameters else None
    wants_context = first_param is not None and _plugin_factory_wants_context(
        factory.create,
        first_param,
    )
    if wants_context:
        return factory.create(context)  # type: ignore[arg-type]
    return factory.create(app)


def _plugin_factory_wants_context(method: Any, first_param: inspect.Parameter) -> bool:
    if first_param.name in {"context", "ctx", "host_context", "plugin_context"}:
        return True
    if first_param.annotation is PluginHostContext:
        return True
    if isinstance(first_param.annotation, str):
        normalized = first_param.annotation.strip("'\"").rsplit(".", 1)[-1]
        if normalized == "PluginHostContext":
            return True
    try:
        return get_type_hints(method).get(first_param.name) is PluginHostContext
    except Exception:
        return False


def create_app(
    mode: Literal["full", "portal"] = "full",
    data_dir: str | Path | None = None,
    static_dir: Path | None = None,
) -> FastAPI:
    """Factory for creating the GISPulse FastAPI application.

    Args:
        mode:       ``"full"`` — full API with auth and all routers (default).
                    ``"portal"`` — portal mode used by ``gispulse serve``.
        data_dir:   Override data directory (portal mode only).
        static_dir: Override SPA dist directory (portal mode only).

    Returns:
        Configured :class:`FastAPI` instance.
    """
    is_portal = mode == "portal"

    # ------------------------------------------------------------------ Auth
    if is_portal:
        api_keys = None
        import warnings
        warnings.warn(
            "Portal mode: authentication is DISABLED. "
            "Do NOT expose this server on a network without --bind 127.0.0.1. "
            "All endpoints are publicly accessible.",
            stacklevel=2,
        )
    else:
        api_keys = _load_api_keys()
        if not api_keys:
            import warnings
            warnings.warn(
                "GISPULSE_API_KEYS is not set — API authentication is DISABLED. "
                "All protected endpoints are publicly accessible. "
                "Set GISPULSE_API_KEYS to enable authentication.",
                stacklevel=2,
            )
            # P0-1: scream loud in production. The /ws/events router will
            # also fail-closed when this combination ships; the boot-time
            # CRITICAL gives ops a single grep target.
            if cfg.api.env == "production":
                log.critical(
                    "ws_fail_closed_production_boot",
                    reason="no_api_keys_and_no_oidc",
                    detail=(
                        "GISPULSE_ENV=production with no GISPULSE_API_KEYS "
                        "and no OIDC provider — /ws/events will refuse every "
                        "connection. Configure auth or downgrade GISPULSE_ENV."
                    ),
                )
    validate_api_key = get_api_key_validator(api_keys)

    # ------------------------------------------------------------------ Storage
    storage_mode = cfg.storage.mode
    db_path = cfg.storage.db_path

    # Data / results directories
    if data_dir is not None:
        _data_path = Path(data_dir).expanduser().resolve()
    else:
        _data_path = db_path.parent / "data"
    _results_path = db_path.parent / "results"
    _data_path.mkdir(parents=True, exist_ok=True)
    _results_path.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ Lifespan
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Lot 2 v2 (Beta E2E): bind the running loop to EventHub so
        # cross-thread producers (ChangeLogWatcher daemons) push through
        # ``call_soon_threadsafe`` instead of touching asyncio.Queue from
        # outside its loop. Done unconditionally — portal mode also has
        # a hub on app.state.event_hub.
        try:
            import asyncio as _asyncio

            hub = getattr(app.state, "event_hub", None)
            if hub is not None and hasattr(hub, "bind_loop"):
                hub.bind_loop(_asyncio.get_running_loop())
        except Exception as exc:  # pragma: no cover — defensive
            log.warning("event_hub_bind_loop_failed", error=str(exc))

        if is_portal:
            log.info("portal_startup", data_dir=str(_data_path))
            app.state.layer_cache = BoundedLayerCache(maxsize=50)
            # #95 (P0-3 dashboard): the WatcherRegistry must exist in
            # portal mode too so ``GET /watchers`` returns an empty list
            # (200) rather than 503 on a fresh install. The registry is
            # inert until something calls register() on it — full-mode
            # paths handle that elsewhere; portal mode just needs the
            # dashboard surface to be reachable.
            from gispulse.persistence.watcher_registry import WatcherRegistry

            app.state.watcher_registry = WatcherRegistry(
                event_hub=app.state.event_hub
            )
        else:
            spatial_engine = create_spatial_engine(backend=cfg.engine.backend)
            spatial_engine.open()
            app.state.spatial_engine = spatial_engine

            # Recover stale jobs left PENDING/RUNNING by a prior crash
            try:
                n = recover_stale_jobs(
                    job_repo=app.state.job_repo,
                    dataset_repo=app.state.dataset_repo,
                    runner=app.state.job_runner,
                    results_dir=_results_path,
                )
                if n:
                    log.info("stale_jobs_recovered", count=n)
            except Exception as exc:
                log.warning("stale_job_recovery_failed", error=str(exc))

            # Start the job worker (polls the queue and executes jobs)
            from gispulse.orchestration.worker import JobWorker
            worker = JobWorker(
                queue=app.state.job_queue,
                runner=app.state.job_runner,
                dataset_repo=app.state.dataset_repo,
                job_repo=app.state.job_repo,
                results_dir=_results_path,
            )
            app.state.job_worker = worker
            import asyncio
            worker_task = asyncio.create_task(worker.start())
            app.state._worker_task = worker_task

            # Start the pipeline scheduler (Pro tier, cron-based)
            try:
                from gispulse.orchestration.scheduler import PipelineScheduler
                from gispulse.persistence.schedule_repository import ScheduleRepository
                schedule_repo = ScheduleRepository(db_path=db_path)
                scheduler = PipelineScheduler(
                    job_queue=app.state.job_queue,
                    schedule_repo=schedule_repo,
                )
                await scheduler.start()
                app.state.scheduler = scheduler
            except Exception as exc:
                # Non-fatal: scheduler is Pro-only, skip if tier check fails
                # or croniter is not installed
                log.info("scheduler_not_started", reason=str(exc))
                app.state.scheduler = None

            # Lot 2 v2 (P0-2): WatcherRegistry replaces the single
            # lifespan-bound ChangeLogWatcher. The registry holds one
            # (engine, watcher) pair per *registered* GPKG dataset.
            #
            # Compat: when the project engine itself is GPKG, we register
            # it under the synthetic id "__project__" so
            # ``app.state.change_log_watcher`` keeps working for tests
            # that asserted on the lifespan watcher (Lot 2 v1 contract).
            from gispulse.persistence.watcher_registry import WatcherRegistry

            registry = WatcherRegistry(event_hub=app.state.event_hub)
            app.state.watcher_registry = registry
            app.state.change_log_watcher = None  # back-compat sentinel

            backend = getattr(spatial_engine, "backend_name", "")
            # Lot 3: extend Lot 2 v2 wiring to DuckDB. The Lot 2 v2 GPKG
            # branch only checked ``backend == "gpkg"``. DuckDB uses a
            # different change-detection mechanism (application-level
            # via :class:`DuckDBChangeDetector` instead of native SQLite
            # triggers), but the ``DuckDBSpatialEngine`` adapter exposes
            # the same ``get_pending_changes`` /
            # ``mark_changes_processed`` shape, so the same watcher
            # plumbing works once we widen the gate.
            #
            # The structural check (``hasattr(... "get_pending_changes")``)
            # is preserved as a defensive guard against custom
            # SpatialEngine subclasses that ship without the change-log
            # surface.
            if backend in ("gpkg", "duckdb") and hasattr(
                spatial_engine, "get_pending_changes"
            ):
                try:
                    from gispulse.persistence.change_log_watcher import ChangeLogWatcher

                    trigger_repo = app.state.trigger_repo

                    def _active_triggers():
                        try:
                            items = trigger_repo.list_all()
                        except Exception:
                            return []
                        return [t for t in items if getattr(t, "enabled", True)]

                    # ESB pipeline wiring (#458): bridge fired triggers
                    # to ActionDispatcher so NOTIFY / WEBHOOK / SET_FIELD
                    # / RUN_SQL run end-to-end, not just /ws/events
                    # broadcast. Each handler is wrapped in try/except by
                    # the dispatcher (see action_dispatcher.dispatch_all)
                    # so a single failing action cannot abort the tick.
                    action_dispatcher = None
                    try:
                        from gispulse.adapters.esb.action_dispatcher import (
                            ActionDispatcher,
                        )
                        from gispulse.adapters.webhooks import HttpWebhookClient
                        from gispulse.runtime.sqlite_retry import (
                            RetryingSqlExecutor,
                        )

                        # S6: parity with the CLI/headless runtime —
                        # ``engine.execute`` is the sandbox'd DML path
                        # (see persistence.sql_guardrails). We always
                        # wrap it in :class:`RetryingSqlExecutor` so a
                        # transient SQLITE_BUSY (concurrent QGIS save,
                        # peer ChangeLog tick) gets retried instead of
                        # failing the action on first lock contention.
                        # ``SecurityError`` is **not** an
                        # ``OperationalError`` and so bypasses the
                        # retry — the wrapper fails fast on a guardrail
                        # violation.
                        raw_executor = getattr(spatial_engine, "execute", None)
                        sql_executor = (
                            RetryingSqlExecutor(raw_executor)
                            if raw_executor is not None
                            else None
                        )

                        action_dispatcher = ActionDispatcher(
                            event_hub=app.state.event_hub,
                            sql_executor=sql_executor,
                            webhook_client=HttpWebhookClient().post,
                        )
                    except Exception as exc:
                        log.warning(
                            "action_dispatcher_init_failed",
                            error=str(exc),
                        )

                    # The registry would normally open its own engine on
                    # the project file, but the SpatialEngine factory has
                    # already opened the project engine. Reuse it directly
                    # to avoid a second handle on the same database
                    # (SQLite WAL contention for GPKG; duplicated
                    # _change_log polling for DuckDB).
                    watcher = ChangeLogWatcher(
                        engine=spatial_engine,
                        event_hub=app.state.event_hub,
                        dataset_id="__project__",
                        triggers_provider=_active_triggers,
                        action_dispatcher=action_dispatcher,
                    )
                    watcher.start()
                    # Stash inside the registry under the synthetic id so
                    # shutdown_all() stops the project watcher too. We
                    # bypass register() because the engine is already open
                    # and owned by the lifespan. Tuple shape MUST match the
                    # registry contract: (engine, watcher, layers).
                    registry._entries["__project__"] = (  # noqa: SLF001
                        spatial_engine,
                        watcher,
                        [],
                    )
                    app.state.change_log_watcher = watcher
                    app.state.action_dispatcher = action_dispatcher
                    log.info(
                        "change_log_watcher_started",
                        backend=backend,
                        dataset_id="__project__",
                        action_dispatcher_wired=action_dispatcher is not None,
                    )
                    if backend == "duckdb":
                        # Lot 3: surface the limitation loudly at boot so
                        # operators don't expect external-write capture.
                        log.warning(
                            "duckdb_change_detection_app_level",
                            detail=(
                                "DuckDB has no native triggers. Only DML "
                                "routed through the engine's execute() proxy "
                                "is captured by /ws/events. External "
                                "duckdb.connect() writes bypass detection. "
                                "Use the gpkg backend or PostGIS (Pro) for "
                                "full external-write capture."
                            ),
                        )
                except Exception as exc:
                    log.warning("change_log_watcher_failed", error=str(exc))

        await _run_plugin_startup(app)

        yield

        await _run_plugin_shutdown(app)

        if not is_portal:
            # Graceful watcher registry shutdown — stops every per-dataset
            # watcher started via /enable_tracking. The project engine
            # was registered as ``__project__`` and is owned by the
            # lifespan, so we pop it before shutdown_all to avoid a
            # double-close on the SpatialEngine.
            registry = getattr(app.state, "watcher_registry", None)
            if registry is not None:
                project_entry = registry._entries.pop("__project__", None)  # noqa: SLF001
                if project_entry is not None:
                    # 3-tuple matches the registry contract:
                    # (engine, watcher, layers).
                    _project_engine, project_watcher, _layers = project_entry
                    try:
                        project_watcher.stop()
                    except Exception as exc:
                        log.warning(
                            "change_log_watcher_stop_failed", error=str(exc)
                        )
                try:
                    registry.shutdown_all()
                except Exception as exc:
                    log.warning("watcher_registry_shutdown_failed", error=str(exc))
            # Graceful scheduler shutdown
            if getattr(app.state, "scheduler", None) is not None:
                await app.state.scheduler.stop()
            # Graceful worker shutdown
            if hasattr(app.state, "job_worker"):
                app.state.job_worker.stop()
                try:
                    await app.state._worker_task
                except Exception:
                    pass
            # Close queue and metering connections
            await app.state.job_queue.close()
            await app.state.metering.close()
            spatial_engine.close()
        else:
            log.info("portal_shutdown")

    # ------------------------------------------------------------------ App
    title = "GISPulse Portal" if is_portal else "GISPulse"
    description = (
        "GISPulse Portal — visual geospatial pipeline editor."
        if is_portal
        else "Headless geospatial engine exposing capabilities, rules, jobs and datasets over a REST API."
    )

    app = FastAPI(
        title=title,
        version=_VERSION,
        description=description,
        lifespan=lifespan,
    )

    register_error_handlers(app)

    # ------------------------------------------------------------------ CORS
    if is_portal:
        cors_origins = ["http://localhost:*", "http://127.0.0.1:*"]
    else:
        raw_origins = cfg.api.cors_origins
        if not raw_origins:
            cors_origins = []
            import warnings
            warnings.warn(
                "GISPULSE_CORS_ORIGINS is not set — CORS is closed by default. "
                "Set GISPULSE_CORS_ORIGINS to enable cross-origin requests.",
                stacklevel=2,
            )
        else:
            cors_origins = [o.strip() for o in raw_origins.split(",")]
    app.state.cors_origins = cors_origins
    _setup_cors(app, cors_origins)

    # ------------------------------------------------------------------ Read-only mode (public demo)
    # When GISPULSE_READ_ONLY=true, block all state-mutating HTTP methods
    # except a small allowlist of compute-only POSTs (preview/validate/etc.).
    # The configured admin key (GISPULSE_SQL_ADMIN_KEY) is honored so the
    # seed worker can still write at boot.
    if cfg.api.read_only and not is_portal:
        from gispulse.adapters.http.middleware.read_only import ReadOnlyMiddleware

        admin_keys: set[str] = set()
        if cfg.api.sql_admin_key.strip():
            admin_keys.add(cfg.api.sql_admin_key.strip())
        app.add_middleware(ReadOnlyMiddleware, admin_keys=admin_keys)
        app.state.read_only = True
        log.info("read_only_mode_active", admin_keys_count=len(admin_keys))
    else:
        app.state.read_only = False

    # ------------------------------------------------------------------ Plugin middleware (both modes)
    # Middleware contributed by external packages (gispulse-enterprise's
    # ProductionAuthMiddleware, etc.) is discovered through the
    # ``gispulse.middleware`` entry-point group and installed for every
    # mode — including ``portal`` — so production deployments stay
    # protected regardless of how the app was created. Routers stay
    # mode-gated below; only middleware applies universally.
    from gispulse.core.plugin_hub import ExtensionHub

    hub = ExtensionHub.get()
    app.state.plugin_hub = hub
    plugin_context = PluginHostContext(
        app=app,
        settings=cfg,
        logger=log,
        plugin_hub=hub,
    )
    app.state.plugin_host_context = plugin_context
    for mw in hub.middleware:
        try:
            mw.install(app)
            log.info("plugin_middleware_installed", plugin=mw.name)
        except Exception as exc:
            log.warning("plugin_middleware_install_failed", plugin=mw.name, error=str(exc))

    # Production-env warning is emitted in OSS (factory-agnostic) so
    # operators get a single grep target whether or not the enterprise
    # plugin is installed. The factory in gispulse-enterprise re-checks
    # the same condition and silently skips when no keys are set.
    if cfg.api.env == "production":
        _middleware_keys: set[str] = set(api_keys) if api_keys else set()
        if cfg.api.api_key.strip():
            _middleware_keys.add(cfg.api.api_key.strip())
        if not _middleware_keys:
            import warnings
            warnings.warn(
                "GISPULSE_ENV=production but no API keys configured "
                "(GISPULSE_API_KEYS / GISPULSE_API_KEY). "
                "Routes /filter/*, /ogc/*, /ws/* are UNPROTECTED.",
                stacklevel=2,
            )

    # ------------------------------------------------------------------ Audit middleware (Pro, opt-in)
    audit_enabled = cfg.audit.enabled
    if audit_enabled and not is_portal:
        try:
            from gispulse.persistence.tier import check_tier
            check_tier("pro")

            from gispulse.persistence.audit import AuditLogger
            from gispulse.adapters.http.middleware.audit_middleware import AuditMiddleware

            audit_logger = AuditLogger(db_path=db_path)
            app.state.audit_logger = audit_logger
            app.add_middleware(AuditMiddleware, audit_logger=audit_logger)

            retention_days = cfg.audit.retention_days
            app.state.audit_retention_days = retention_days

            log.info(
                "audit_middleware_active",
                retention_days=retention_days,
                db_path=str(db_path),
            )
        except Exception as exc:
            log.warning("audit_middleware_disabled", reason=str(exc))

    # ------------------------------------------------------------------ Rate limiting (all modes)
    try:
        from slowapi import _rate_limit_exceeded_handler
        from slowapi.errors import RateLimitExceeded
        from slowapi.middleware import SlowAPIMiddleware
        from gispulse.adapters.http.rate_limit import limiter

        app.state.limiter = limiter
        app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
        app.add_middleware(SlowAPIMiddleware)
    except ImportError:
        log.warning("slowapi_not_installed", detail="Rate limiting disabled")

    # ------------------------------------------------------------------ Metrics middleware
    from gispulse.adapters.http.middleware.metrics_middleware import MetricsMiddleware
    app.add_middleware(MetricsMiddleware)

    # ------------------------------------------------------------------ Repos & services
    _setup_repos(app, storage_mode, db_path)
    _setup_services(app)

    app.state.data_dir = _data_path
    app.state.results_dir = _results_path

    # Dataset storage backend (local or S3/MinIO)
    from gispulse.persistence.storage import create_storage as _create_storage

    app.state.storage = _create_storage()

    if not is_portal:
        from gispulse.core.config import settings as _cfg
        app.state.session_provisioner = SessionProvisioner(
            base_dsn=_cfg.database.postgis_dsn or ""
        )
        app.state.layer_cache = BoundedLayerCache(maxsize=50)

    # ------------------------------------------------------------------ Health
    mode_tag = "portal" if is_portal else "full"

    @app.get("/health", response_model=HealthResponse if not is_portal else None, tags=["meta"])
    def health(request: Request) -> dict:
        checks: dict[str, dict] = {}
        all_ok = True

        # Check database (SQLite/auth repo)
        auth_repo = getattr(request.app.state, "auth_repo", None)
        if auth_repo is not None:
            try:
                auth_repo.user_count()
                checks["database"] = {"status": "ok", "detail": ""}
            except Exception as exc:
                log.warning("health_check_database_error", error=str(exc))
                checks["database"] = {"status": "error", "detail": "database check failed"}
                all_ok = False
        else:
            # Don't disclose RBAC mode to anonymous callers — internals are
            # not visible from /health.
            checks["database"] = {"status": "ok", "detail": ""}

        # Check Redis (job queue)
        job_queue = getattr(request.app.state, "job_queue", None)
        if job_queue is not None and hasattr(job_queue, "_redis"):
            try:
                import asyncio
                loop = asyncio.get_running_loop()
                # Cannot run_until_complete inside an active loop — use a sync ping instead
                if hasattr(job_queue, "ping_sync"):
                    job_queue.ping_sync()
                else:
                    # Queue is accessible — mark as OK (full check via /health async endpoint)
                    pass
                checks["redis"] = {"status": "ok", "detail": ""}
            except Exception as exc:
                log.warning("health_check_redis_error", error=str(exc))
                checks["redis"] = {"status": "error", "detail": "redis check failed"}
                all_ok = False

        # Check disk (storage writable)
        storage = getattr(request.app.state, "storage", None)
        if storage is not None and hasattr(storage, "_base"):
            try:
                base = storage._base
                if base.exists() and os.access(str(base), os.W_OK):
                    checks["disk"] = {"status": "ok", "detail": ""}
                else:
                    checks["disk"] = {"status": "error", "detail": "storage dir not writable"}
                    all_ok = False
            except Exception as exc:
                log.warning("health_check_disk_error", error=str(exc))
                checks["disk"] = {"status": "error", "detail": "disk check failed"}
                all_ok = False

        status_code = 200 if all_ok else 503
        from fastapi.responses import JSONResponse
        return JSONResponse(
            content={
                "status": "ok" if all_ok else "degraded",
                "version": _VERSION,
                "mode": mode_tag,
                "checks": checks,
            },
            status_code=status_code,
        )

    # ------------------------------------------------------------------ Metrics (full only)
    if not is_portal:
        _metrics_token = cfg.api.metrics_token

        @app.get("/metrics", tags=["meta"], summary="Prometheus metrics", response_class=PlainTextResponse)
        def metrics(request: Request) -> PlainTextResponse:
            if _metrics_token:
                auth = request.headers.get("Authorization", "")
                if auth != f"Bearer {_metrics_token}":
                    from fastapi.responses import JSONResponse
                    return JSONResponse(
                        status_code=401,
                        content={"detail": "Unauthorized: valid Bearer token required for /metrics."},
                    )
            body = MetricsCollector.get().to_prometheus_text()
            return PlainTextResponse(content=body, media_type="text/plain; version=0.0.4")

    # ------------------------------------------------------------------ Routers
    protected = {"dependencies": [Depends(validate_api_key)]}

    # Auth router — always mounted so the portal UI can call /auth/providers
    # and /auth/me without 404. Engine ships OSS stubs (empty providers, 401
    # on /me); the gispulse-enterprise plugin overrides with full OIDC SSO
    # endpoints when installed and an OIDC provider is configured.
    try:
        from gispulse.adapters.http.routers.auth_router import router as auth_router
        app.include_router(auth_router)
        log.info("auth_router_mounted", oidc_configured=app.state.oidc_provider is not None)
    except ImportError:
        log.debug("auth_router_not_available")

    # Mode 2 "Try it" router (v1.5.x) — read-only, no auth, both modes.
    # Exposes a fixed registry of bundled GPKG datasets for the public
    # portal demo (#47/#48/#49). Hardened by ReadOnlyMiddleware which
    # only whitelists the dryrun POST.
    app.include_router(examples_router)
    log.info("examples_router_mounted")

    if is_portal:
        # Portal mode: no auth on routers
        app.include_router(portal_router)
        app.include_router(projects_router)
        app.include_router(rules_router)
        app.include_router(triggers_router)
        app.include_router(jobs_router)
        app.include_router(scenarios_router)
        app.include_router(capabilities_router)
        app.include_router(templates_router)
        app.include_router(marketplace_router)
        app.include_router(relations_router)
        app.include_router(filter_router)
        app.include_router(schedules_router)
        app.include_router(pipelines_router)
        app.include_router(system_router)
        app.include_router(watchers_router)
        try:
            app.include_router(catalog_router)
        except Exception:
            log.warning("catalog_router_skipped")
        try:
            app.include_router(ws_router)
        except ImportError:
            log.warning("ws_router_skipped")

        # API catch-all for unknown /api/* paths
        @app.api_route(
            "/api/{rest_of_path:path}",
            methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            include_in_schema=False,
        )
        async def api_catch_all(rest_of_path: str):
            raise HTTPException(status_code=404, detail=f"/api/{rest_of_path} not found")

    else:
        # Full mode: auth-protected routers with scope enforcement
        read_protected = {"dependencies": [Depends(validate_api_key), Depends(require_scope("read"))]}
        write_protected = {"dependencies": [Depends(validate_api_key), Depends(require_scope("write"))]}

        app.include_router(capabilities_router, **read_protected)
        app.include_router(templates_router, **read_protected)
        app.include_router(rules_router, **write_protected)
        app.include_router(jobs_router, **protected)
        app.include_router(datasets_router, **protected)
        app.include_router(projects_router, **protected)
        app.include_router(scenarios_router, **protected)
        app.include_router(triggers_router, **write_protected)
        app.include_router(relations_router, **write_protected)
        app.include_router(sessions_router, **protected)
        app.include_router(portal_router, **protected)
        app.include_router(catalog_router)
        app.include_router(filter_router)
        app.include_router(schedules_router, **write_protected)
        app.include_router(pipelines_router, **write_protected)
        app.include_router(system_router, **protected)
        app.include_router(watchers_router, **read_protected)

        # Marketplace (read endpoints open, install/uninstall admin-gated internally)
        app.include_router(marketplace_router)

        # Admin (RBAC) and Billing (Stripe) routers ship in the gispulse-enterprise
        # plugin and are mounted by the ExtensionHub block below via
        # ``gispulse.routers`` entry-points — no legacy try/except needed.

        from gispulse.adapters.http.routers.ogc_features_router import router as ogc_features_router
        app.include_router(ogc_features_router)

        if cfg.engine.backend == "postgis":
            from gispulse.adapters.http.routers.tiles_router import router as tiles_router
            app.include_router(tiles_router)

        app.include_router(ws_router)
        app.include_router(esb_router, **protected)

        # ------------------------------------------------------------------ Plugin routers
        # Routers contributed by external packages (e.g. gispulse-enterprise's
        # admin/billing/auth) are discovered through the ``gispulse.routers``
        # entry-point group and mounted here in full mode only. Plugin
        # middleware was already installed earlier in ``create_app`` so it
        # protects both portal and full deployments — see
        # ``docs/PLUGIN_CONTRACT.md``.
        for plugin_name, factory in hub.routers.items():
            try:
                router = _create_plugin_router(factory, app, plugin_context)
            except Exception as exc:
                log.warning("plugin_router_create_failed", plugin=plugin_name, error=str(exc))
                continue
            if router is None:
                log.info("plugin_router_skipped", plugin=plugin_name)
                continue
            app.include_router(router)
            log.info("plugin_router_mounted", plugin=plugin_name)

        app.state.event_hub = get_event_hub()
        app.state.viewer_state = {"file_path": None, "layer_cache": {}}

        @app.get(
            "/schema/capabilities",
            tags=["meta"],
            summary="Dynamic capability JSON schemas",
            response_model=list[CapabilityInfo],
            dependencies=[Depends(validate_api_key)],
        )
        def schema_capabilities() -> list[dict[str, Any]]:
            return [
                CapabilityInfo(
                    name=item["name"],
                    description=item["description"],
                    json_schema=item["schema"],
                )
                for item in registry.list_all()
            ]

    # ------------------------------------------------------------------ SPA static files
    dist = static_dir or (_PORTAL_DIST if is_portal else None)

    if is_portal and dist and dist.exists() and dist.is_dir():
        index_html = dist / "index.html"
        assets_dir = dist / "assets"

        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="portal-assets")

            from starlette.middleware.base import BaseHTTPMiddleware

            class CacheControlMiddleware(BaseHTTPMiddleware):
                async def dispatch(self, request: Request, call_next):
                    response = await call_next(request)
                    if request.url.path.startswith("/assets/"):
                        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
                    return response

            app.add_middleware(CacheControlMiddleware)

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa_fallback_portal(request: Request, full_path: str):
            static_file = dist / full_path
            if (
                full_path
                and not full_path.startswith("assets/")
                and static_file.exists()
                and static_file.is_file()
            ):
                import mimetypes
                from fastapi.responses import Response
                mime, _ = mimetypes.guess_type(str(static_file))
                return Response(
                    static_file.read_bytes(),
                    media_type=mime or "application/octet-stream",
                )
            return HTMLResponse(index_html.read_text(), headers={"Cache-Control": "no-cache"})

        log.info("portal_static_mounted", directory=str(dist))

    elif not is_portal and _PORTAL_DIST.exists():
        _index_html = _PORTAL_DIST / "index.html"

        if _VIEWER_DIST.exists():
            app.mount("/viewer", StaticFiles(directory=str(_VIEWER_DIST), html=True), name="viewer")

        app.mount("/assets", StaticFiles(directory=str(_PORTAL_DIST / "assets")), name="portal-assets")

        _SPA_ROUTES = {"", "explorer", "map", "datasets", "workflows", "catalog", "schema"}

        @app.get("/{path:path}", include_in_schema=False)
        async def spa_fallback_full(request: Request, path: str):
            # Try a static file at the dist root first (favicon.svg, icons.svg,
            # robots.txt, etc. — anything Vite emits outside /assets/). Path
            # traversal is blocked by the resolved-relative-to check.
            if path:
                candidate = _PORTAL_DIST / path
                try:
                    resolved = candidate.resolve()
                    dist_root = _PORTAL_DIST.resolve()
                    if resolved.is_relative_to(dist_root) and resolved.is_file():
                        import mimetypes
                        from fastapi.responses import Response
                        mime, _ = mimetypes.guess_type(str(resolved))
                        return Response(
                            resolved.read_bytes(),
                            media_type=mime or "application/octet-stream",
                        )
                except (OSError, ValueError):
                    pass

            # Match the first path segment so deep-links like /explorer/foo/bar
            # also fall back to the SPA index.
            first_segment = path.lstrip("/").split("/", 1)[0]
            if first_segment in _SPA_ROUTES:
                if _index_html.exists():
                    return HTMLResponse(_index_html.read_text())
                return PlainTextResponse(
                    "Portal not built. Run: cd portal && npm run build",
                    status_code=404,
                )
            raise HTTPException(status_code=404, detail="Not Found")

    return app

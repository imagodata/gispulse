# GISPulse — Project Overview

**Purpose:** Moteur geospatial modulaire, client-agnostique, avec regles metier, triggers et double mode d'exploitation (portable DuckDB / persistant PostGIS).

**Current Phase:** Phase 3 — Mode persistant PostGIS

**Sprint A10 — terminé (2026-04-04):**
- #238 (S0): DSN supprimé des corps de requêtes HTTP — env GISPULSE_POSTGIS_DSN uniquement
- #239 (S0): Endpoints /sql/execute et /sql/export protégés via X-Admin-Key header
- #240 (S0): SQL identifier validation dans operation_executor.py et trigger_evaluator.py
- #241 (S0): SSRF IP blocklist dans portal_upload_router.py et datasets_router.py
- #242 (S0): ProductionAuthMiddleware dans app.py (GISPULSE_ENV=production)
- #243 (S1): cli.py déjà présent (512L) — fermé
- #244 (S1): tests/unit/test_filter.py déjà présent (844L, 93 tests) — fermé
- #245 (S1): bridge.py list_layers() déjà corrigé — fermé
- #246 (S1): raster_io.py numpy import OK — fermé
- #247 (S2): MetricsCollector → core/observability.py — déjà fait
- #248 (S2): portal_router.py découpé en 4 sous-routers — déjà fait
- Suite de tests: 931 passed, 6 skipped
- Tous les issues A9 fermés sur GitHub

**Sprint A9 — terminé (2026-04-04):**
- #249 (S2): PredicateEvaluator fusionné — adapters/esb/predicate_evaluator.py est un shim vers rules/predicates.py
- #250 (S2): portal_app.py est un shim vers app.create_app(mode="portal")
- #251 (S2): TriggerEvaluator dans rules/trigger_evaluator.py, injecté via SpatiaLiteSession.start_polling()
- #252 (S3): tests/unit/test_capabilities_strategy.py créé (16 tests, dispatch multi-backend)
- #253 (S3): tests/unit/test_ogc_router.py (23 tests) + tests/unit/test_mvt_router.py (17 tests)
- #254 (S3): pip-audit dans CI (.github/workflows/ci.yml) + bornes sup dans pyproject.toml
- #255 (S3): sanitisation Path(file.filename).name dans datasets_router.py et portal_upload_router.py
- SpatialEngine ABC — unified interface DuckDB/PostGIS
- PostGISConnection implements SpatialEngine (persistent mode)
- Engine factory with GISPULSE_ENGINE env switch
- Project model for multi-dataset persistent workspaces
- TriggerManager — PostgreSQL LISTEN/NOTIFY + rule dispatch
- WebSocket live sync endpoint (/ws/events) + EventHub
- Projects REST API (CRUD + list layers)
- Portal viewer live status indicator (green/red dot)

**Previous phases:**
- Phase 2: API HTTP + edition — SQLite persistence, rules CRUD, job execution, geometry editing, deck.gl draw tools
- Phase 1: MVP CLI — DuckDB engine, 3 capabilities, rules engine, GPKG adapter
- Phase 1.5: Viewer — deck.gl + Turf.js, FastAPI serve mode, layer inspection

**Stack:**
- Python 3.10+, setuptools
- GeoPandas, Shapely, DuckDB, Fiona, Typer, structlog
- Optional: PostGIS (SQLAlchemy, GeoAlchemy2), FastAPI, FastMCP
- Dev: pytest, ruff, httpx

**Architecture:** core → capabilities → rules → orchestration → persistence → adapters

**Entry point:** `cli.py` (Typer CLI), installable via `gispulse` command

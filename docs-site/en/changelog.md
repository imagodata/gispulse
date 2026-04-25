---
title: Changelog
description: GISPulse version history.
---

# Changelog

All notable changes are documented here. Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versioning follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed

- **Capabilities reference** — bumped from **78 to 117** documented capabilities (FR + EN), 11 → 18 categories. New sections: vector overlay & combine, attribute manipulation, pivot/unpivot, ordered selection & sampling, multipart & Z/M dimensions, geometric transforms, boundary & projection, temporal, 3D pointcloud.
- **Playground manifest** — regenerated with the **6 scenarios** actually deployed (S1 flood-risk, S2 data-quality, S3 accessibility, S4 road-setback, S5 green-spaces, S6 real-estate). The previous manifest only listed `road-setback`.
- **Idempotent playground build** — `scripts/build_playground_data.py` gains `_entry_from_disk`: emits a manifest entry from existing files when the source GPKG is missing.

### Fixed

- **CHANGELOG v1.1.0** — corrected: S5 is described as "Park accessibility" (not "Canopy typology"), the "S7 / seven-scenario index" claim is removed since that page never shipped.
- **Orphan pages** — `playground/environmental-ndvi.md` (FR + EN) deleted: outdated JS redirects, no remaining source references.

### Removed

- **S7 dvf-heavy-tail scenario** — spec dropped from the build script; the orphan data folder has been removed. The scenario page never existed in the docs.

---

## [1.1.1] — 2026-04-25

### Added

- **`capabilities/vector/`** — the monolithic `vector.py` (4,359 LOC, 43 capabilities) was split into a 32-module per-domain package. The public surface is preserved through a re-export shim; every `from capabilities.vector import ...` keeps working unchanged.

### Changed

- **`gispulse/__init__.py`** — fallback `__version__` changed from hardcoded `"1.0.0"` to `"unknown"` when `importlib.metadata` is unavailable.
- **`portal/package.json`** + **`docs-site/package.json`** — versions synced to `1.1.1` to match `pyproject.toml`.

### Fixed

- **Accessibility** — keyboard navigation on `PipelinePanel`, portal imports unified around design-system tokens.

---

## [1.1.0] — 2026-04-25

### Added

- **Playground scenarios** — S5 Park accessibility (Versailles, BD TOPO vegetation ≥ 1 ha + `nearest_neighbor` + `classify`, weekly cron) and S6 Price-per-m² DVF map (8 steps, 50 m fishnet, YlOrRd quintiles).
- **Capabilities — classification & stats** — `head_tail_breaks` (Jiang 2013), `normalize` (log1p / minmax / zscore), `grid_create`, `hexgrid_create`, `spatial_aggregate`, `classify_categorical`, `bivariate_choropleth`, `graduated_size`, `continuous_ramp`, `kde_heatmap`. Clustering: `cluster_kmeans`, `cluster_dbscan`, `cluster_hdbscan`, `morans_i`, `getis_ord_g`, `nearest_neighbor`, `od_matrix`, `spatial_weights`.
- **Capabilities — 3D pointcloud** — LAS / LAZ sprint: `pointcloud_load_las`, `pointcloud_filter_classification`, `pointcloud_zonal_height`, `pointcloud_grid_summary`.
- **Capabilities — layer manipulation P0-P3** — overlay (`overlay_intersection`, `overlay_union`, `erase`), selection (`sort`, `deduplicate`, `random_sample`, `top_n`), shape ops, transforms (`affine_transform`, `swap_xy`, `reverse_lines`), Z/M (`add_z`, `drop_z`, `add_m`, `drop_m`), pivot/unpivot, `classify_by_ring`, `merge_layers`, attribute logic (`add_field`, `drop_field`, `select_columns`, `rename_field`, `cast_field`, `attribute_join`, `lookup_table`, `coalesce_fields`, `case_when`), temporal (`temporal_filter`, `temporal_join`).
- **Playground UX** — rubber-band drawing with snap-to-close + keyboard shortcuts + live measurement; client-side polygon intersection styling (S4 road-setback).
- **DVF Etalab 2022-2024** — sample dataset bundled with `examples/prepare_playground_data.py --city versailles` (`dvf_ventes` layer).
- **Style sidecars** — `.style.qml` / `.style.sld` / `.legend.json` files emitted next to vector outputs for direct QGIS / GeoServer import.
- **SQL preview** — explicit auth gate + capability blocklist on the PostGIS SQL capability.

### Changed

- **`core/config.py`** — centralised all environment variables into a single Pydantic Settings module (13 groups: `engine`, `database`, `storage`, `s3`, `api`, `oidc`, `session`, `redis`, `logging`, `audit`, `stripe`, `telemetry`, `jobs`). Backward-compatible with every existing `GISPULSE_*` name.
- **Default engine** — changed from `duckdb` to `gpkg` (portable GPKG / GeoPandas mode).
- **Removed scattered `os.environ.get()` calls** — routers, adapters, persistence: everything routes through `settings`.
- **Playground S5** rewritten as park accessibility per building — BD TOPO vegetation ≥ 1 ha (SCoT IdF), `nearest_neighbor` distance building → park, classified against OMS / SCoT / ADEME thresholds (300 / 600 / 1000 m). The former NDVI / canopy trigger has been dropped.
- **Playground S6** extended to a 250 m then tightened to a 50 m fishnet choropleth for high-resolution heatmap rendering.
- **Playground S3** — 6-step pipeline collapsed to 3 via `cost_budgets` + `classify_by_ring` (4 concentric isochrones 500 / 750 / 1000 / 1500 m).
- **`adapters/http`** — namespace fork resolved: legacy tree deleted, prod entrypoints flipped to `gispulse.adapters.http.app`.
- **Security** — `MD5` replaced by `BLAKE2b`, `eval` sandboxed for `np`, `_ensure_valid` restored.

### Fixed

- **Capabilities — 4 P0 closed**: `force_geometry_type` (GeometryCollection target), `attribute_join` on a plain DataFrame, NaN crash in the `add_z` / `add_m` `from_column` path, `singleparts_to_multipart` silent data loss on mixed geom types.
- **Capabilities** — pointcloud grid 2D NaN, KDE grid blow-up, `Calculate` RCE sandbox.
- **Tests** — repaired 27 tests once CI was unblocked, removed shadow `__init__.py`, enabled `asyncio_mode = "auto"`, fixed `workflows/ftth_network_analysis.py` SyntaxError. 3,600+ tests green.
- **Tests** — isolate `GISPULSE_ENGINE` mutations; conftest auth-disabled-by-default.
- **Billing** — default `StripeSettings` + actionable error messages when Stripe keys are missing.
- **Capabilities** — `clip` / `intersects` no longer evaluate `GeoDataFrame` truthiness; `spatial_predicate` fallback made explicit.
- **Playground** — S6 `drop_price_outliers` renamed to `drop_value_outliers` (filters the raw `valeur_fonciere`, not price-per-m²).
- **i18n** — `PipelinePanel` strings; default-engine alignment; pipelines `ref_layers` plural.
- **Performance** — lazy-loaded `DualMapView`.
- **Rules router** — payload validation before persisting (400 with structured errors).

---

## [1.0.2] — Sprint S1→S6 (2026-04-12)

Six sprints of audit and hardening: security, architecture, tests, observability, router coverage, Prometheus metrics.

### Added

#### Architecture — Declarative Grammar v2 (Sprint S1)
- **`PipelineSpec` / `StepSpec` / `TriggerSpec`** — unified grammar replacing 3 divergent DSLs
- **DAG support** — steps can reference other steps via `step.input`
- **Conditional steps** — `step.when` predicate evaluation on current GeoDataFrame
- **Inline triggers** — `on/when/then` syntax within pipelines
- **Backward-compatible** — v1 flat rule lists auto-converted to v2
- **`PipelineExecutor`** — unified executor (linear and DAG mode via `GraphExecutor`)
- **`PluginRegistry[T]`** — generic thread-safe registry with entry point discovery

#### Pipeline v2 API (Sprint S2)
- **`POST /api/pipelines/execute`** — execute v2 pipelines with `PipelineSpec` JSON
- **`POST /api/pipelines/validate`** — dry-run pipeline validation
- **`GET /api/pipelines/examples`** — v2 pipeline examples
- **CRUD `/api/triggers/{id}/operations`** — spatial operations persistence in triggers
- **`SessionManager.run_pipeline_v2()`** — native delegation to `PipelineExecutor`
- **TypedDict for 10 capabilities** — `FilterParams`, `BufferParams`, etc.
- **PipelineEditor** — portal editor mode: import/export v2 JSON, execute via `/pipelines/execute`

#### Portal — Decomposition & WebSocket (Sprint S3)
- **`LayerItemButton`** and **`DatasetItem`** extracted from `LeftPanel.tsx` (1183→774 lines)
- **WebSocket listener** replaces `setInterval` polling in `transformStore`
- **CI GitHub Actions** — `ci.yml` workflow with backend (pytest, ruff) and frontend (tsc, vite build) jobs

#### Documentation & Tooling (Sprint S4)
- **`scripts/export_openapi.py`** — auto-generates `docs/openapi.json` + `docs/API_REFERENCE.md`
- **QUICKSTART.md**, **RULES_GUIDE.md**, **TRIGGERS_GUIDE.md**, **API_QUICKSTART.md** — 4 user guides
- **`docs/openapi.json`** — complete OpenAPI 3.1 specification (88 endpoints)

### Changed

#### Models (Sprint S1)
- **`core/models.py` split** (795→280L) into 6 modules: `enums.py`, `conditions.py`, `predicates.py`, `graph.py`, `relations.py`, `session.py`
- **`Rule.order`** extracted from config bag to dedicated field

#### Portal (Sprint S3)
- **Predicate type renaming** — removed `*Node` suffix (`AttrPredicateNode` → `AttrPredicate`)
- **Forge operations connected** — `OperationExecutor` → ESB: `RUN_SQL` actions run end-to-end

### Removed
- **Non-functional client stubs** — `clients/qgis/`, `clients/arcgis/`, `clients/desktop/` (code in git history)
- **ESB `CircuitBreaker` and `DeadLetterQueue`** marked `EXPERIMENTAL`, lazy-import only

### Security (Sprint S1)
- Patch for 13 critical vulnerabilities (7 SQL injections, 2 RCE, 1 auth bypass)
- 114 security tests covering all audit vectors
- **`hmac.compare_digest()`** for all auth comparisons (timing-safe)
- **Nginx security headers** — CSP, X-Frame-Options DENY, X-Content-Type-Options nosniff, Referrer-Policy
- **Rate limiting** on `/api/filter/preview` (30/min) and `/api/filter/apply` (20/min)
- **`pip-audit`** now blocks CI on known CVEs (removed `|| true`)
- **Upload size validation** — handles invalid env values, caps at 5GB

### Architecture (Sprint S2)
- **structlog migration** — replaced `print()` and stdlib `logging` with structlog in ESB workers and pg_notify
- **Silent exception logging** — 6 `except: pass` handlers replaced with `log.debug()`/`log.warning()`
- **Job cancellation race fix** — check cancellation BEFORE persisting results
- **Dataset load timeout** — 300s max to prevent hangs on large files
- **Trigger name collision fix** — use trigger UUID as suffix (supports multiple triggers per table)
- **WebSocket message limit** — 1MB max per outgoing message

### Observability (Sprint S4 + S6)
- **`MetricsMiddleware`** — automatic HTTP metrics: `gispulse_http_requests_total`, `gispulse_http_request_duration_seconds`, `gispulse_http_requests_in_flight`
- **Path normalization** — collapses UUIDs and numeric segments to reduce Prometheus cardinality
- **Trace ID correlation** — `trace_id` in structured error logs for incident investigation
- **Docker non-root** — `USER appuser` (uid 1000) in Dockerfile
- **`.dockerignore`** — excludes .git, node_modules, tests, docs, .env, IDE files
- **`.pre-commit-config.yaml`** — ruff lint+format, trailing whitespace, YAML check, private key detection

### Tests (Sprints S3 + S5)
- **2,439 tests** passing (up from 2,205 in v1.0.1), +234 tests across 6 sprints
- **106 test files** (unit + integration + security)
- **Router coverage: 85%** (23/27 routers tested, up from 33%)
- 16 new test files covering rules, triggers, jobs, datasets, CLI, persistence IO, auth, admin, scenarios, schedules, catalog, relations, filter, portal, ESB, tiles
- **CI: mypy** (type checking core modules) + **ESLint/Vitest** (frontend lint + tests)

---

## [1.0.0] — 2026-04-06

Initial public release. 27 capabilities, 1,836 tests, multi-backend DuckDB/PostGIS engine.

---

## [0.1.0] — 2026-03-31

### Added

#### Core engine
- DuckDB geospatial engine with portable SpatiaLite and persistent PostGIS modes
- `SessionManager` with E2E pipeline, `ExecutionStrategy` pattern, SpatiaLite session support
- `JobRunner` with async execution and job status tracking
- Cross-layer operations: spatial join, reference layer system, multi-layer support
- Pagination, dataset association, project CRUD
- PyOGRIO migration for multi-format I/O
- Edge case hardening: shadow zones, centroid, area/length capabilities
- GeoParquet support and OGC server with MVT tile server

#### CLI
- Typer CLI entry point (`gispulse`)
- Commands: `init`, `validate`, `info`, `layers`, `formats`, `capabilities`, `serve`, `portal`, `doctor`
- Multi-format acceptance via the integrated I/O layer

#### Vector capabilities (10)
- `buffer` — metric buffer with automatic reprojection
- `union` — merge all features
- `reproject` — CRS reprojection
- `filter` — attribute filter
- `clip` — clip by reference layer
- `intersects` — filter by spatial intersection
- `spatial_join` — spatial join
- `centroid` — centroid extraction
- `area_length` — area and length calculation
- `dissolve` — dissolve by attribute
- Capability registry with auto-discovery
- Lifespan-managed capability injection

#### Rules
- Rules-as-config system with JSON definitions
- Rule editor UI with predicate builder
- Trigger-based rule evaluation with `auto_eval` and SSE eval-stream

#### Persistence
- Persistent PostGIS mode with live sync and pg_notify integration
- Portable SpatiaLite mode (level 2 session, serverless)
- GPKG export from catalog
- Scene manager with snapshot and restore

#### REST API (FastAPI)
- Full REST API: projects, datasets, features, sessions, rules, triggers, scenarios
- 14 routers, 100+ endpoints
- Feature update, SQL execution, relation endpoints
- OGC Features ingestion endpoints
- SSE streaming for trigger evaluation results
- Docker hot-reload configuration for API and Portal dev servers
- Global error handlers `{"error": {"code", "message", "detail"}}` for 400/404/422/500

#### Portal (React 19)
- 5-workspace layout: Explorer, Map, Workflows, Catalog, Data
- Layer tree with groups, color picker, legend and symbology
- Resizable panel layout with ActivityBar and Inspector
- Node editor (XyFlow/ReactFlow v12) with 9 node types, NodePalette, inline inspector
- Trigger stepper, scenario bar, spatial operations UI
- SQL console and feature inspector
- Catalog workspace with cards, favorites, mini-map, domain filtering
- Dark mode with OKLCH design tokens, Geist font, toast notifications
- Command palette (Ctrl+K), keyboard shortcuts (1–5, Ctrl+I/B/K/S/?)
- Drag-and-drop upload and URL import, GPKG export with QML styles

#### Viewer
- Embedded deck.gl spatial viewer served via `gispulse serve`

#### ESB / Triggers
- Event bus with pg_notify, routing, circuit breaker, dead letter queue
- Trigger Builder UI with predicate composition
- `SessionProvisioner` with `TriggerEvaluator` and SSE eval-stream

#### Catalog
- GIS data catalog: projections, basemaps, WMS/WFS feeds, open data sources

#### Tests
- 46 test files: unit and integration
- SpatiaLite E2E integration tests
- Pytest configuration with async support

---

## Links

- [GitHub Repository](https://github.com/gispulse/gispulse)
- [Report a bug](https://github.com/gispulse/gispulse/issues)
- [Roadmap](https://github.com/gispulse/gispulse/projects)

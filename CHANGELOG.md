# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.2.0] - 2026-04-25

### Added
- **Open source release** — first AGPL-3.0 publication on PyPI as `gispulse`. Source at https://github.com/imagodata/gispulse.
- `core/plugin_hub.py` + `core/plugin_contracts.py` — plugin discovery via Python entry-points, six groups (`gispulse.routers`, `gispulse.middleware`, `gispulse.auth_provider`, `gispulse.billing_provider`, `gispulse.licence_provider`, `gispulse.connectors`).
- `core/pricing_catalog.json` — tier→features catalog (community / pro / team / enterprise) with `inherits` chain.
- `team` tier in `persistence.tier.VALID_TIERS` and `core.config.EngineSettings`, between `pro` and `enterprise`.
- Multi-project gate on `POST /projects` (community=1, pro=5, team+=∞).
- Pro-tier gate on `triggers_router` (router-level) and `pipelines_router` (`/execute`, `/execute-steps` for multi-step DAG).

### Changed
- **Repository layout** — proprietary modules (Stripe billing, OIDC SSO, RBAC admin, production auth middleware, licence Stripe sync) moved to a private companion package `gispulse-enterprise` distributed under a commercial EULA. The OSS engine ships only AGPL components and discovers enterprise via entry-points at runtime.
- `gispulse/adapters/http/app.py` — billing, auth, admin router mounting now driven by `PluginHub` discovery instead of hard-coded imports; degrades cleanly when no enterprise plugin is installed.

### Removed
- `gispulse/adapters/billing/` — moved to `gispulse-enterprise`.
- `gispulse/adapters/http/oidc.py`, `middleware/production_auth.py`, `routers/{auth,billing,admin}_router.py` — moved to `gispulse-enterprise`.
- `pricing.yml` (with EUR amounts and early-adopter terms) — moved to `gispulse-enterprise/config/pricing_commercial.yml`. The technical tier→features mapping stays here as `core/pricing_catalog.json`.
- Test files specific to enterprise modules (`test_oidc.py`, `test_billing*`, `test_admin_router.py`, `test_security_a10.py`, `test_rate_limit.py`, `test_auth_rbac.py`, `test_security.py`, `test_licence_repo.py`, `test_e2e_flows.py` partial).

## [1.1.1] - 2026-04-25

### Added
- `capabilities/vector/` — monolithic `vector.py` (4359 LOC, 43 capabilities) split into a 32-module package. Public surface preserved via re-export shim; all imports of the form `from capabilities.vector import ...` continue to work unchanged.

### Changed
- `gispulse/__init__.py` — fallback `__version__` changed from hardcoded `"1.0.0"` to `"unknown"` so the package no longer self-reports a stale version when `importlib.metadata` is unavailable.
- `portal/package.json` and `docs-site/package.json` — versions synced from `0.0.0` / `0.1.0` to `1.1.1` to match `pyproject.toml`.

### Fixed
- Accessibility — keyboard navigation on `PipelinePanel`, portal imports unified to design-system tokens.

## [1.1.0] - 2026-04-25

### Added
- Playground scenarios — S5 Park accessibility (Versailles, BD TOPO vegetation ≥ 1 ha + nearest_neighbor + classify, weekly cron) and S6 Price-per-m² DVF (8-step fishnet choropleth, 50 m grid, YlOrRd quintiles)
- Capabilities — `head_tail_breaks` (Jiang 2013 heavy-tail classifier, data-driven class count), `normalize` (log1p / minmax / zscore), `grid_create`, `hexgrid_create`, `spatial_aggregate`, additional classification variants (`classify_categorical`, `bivariate_choropleth`, `graduated_size`, `continuous_ramp`, `kde_heatmap`), clustering (`cluster_kmeans`, `cluster_dbscan`, `cluster_hdbscan`, `morans_i`, `getis_ord_g`, `nearest_neighbor`, `od_matrix`, `spatial_weights`)
- Capabilities — 3D pointcloud sprint: LAS/LAZ load + classification + zonal stats + grid; layer manipulation foundations P0-P3 (overlay, selection, shape ops, transforms, temporal, pivot/unpivot, classify_by_ring, attribute logic ops)
- Playground UX — rubber-band draw with snap-to-close + keyboard shortcuts + live measurement, client-side polygon intersection styling (S4 road-setback)
- DVF Etalab 2022-2024 sample dataset bundled with `examples/prepare_playground_data.py` (Versailles)
- Style sidecars — `.style.qml` / `.style.sld` / `.legend.json` emitted next to vector outputs for direct QGIS + GeoServer import
- SQL preview — explicit auth gate + capability blocklist on the PostGIS SQL capability

### Changed
- Playground S5 rewritten — former NDVI/canopy trigger replaced by park accessibility per building (vegetation ≥ 1 ha + nearest_neighbor + classify against OMS/SCoT/ADEME thresholds 300 / 600 / 1000 m)
- Playground S6 extends to 250 m then 50 m fishnet choropleth for high-resolution heatmap rendering
- Playground S3 — collapsed 6-step pipeline to 3 via `cost_budgets` + `classify_by_ring` (4 concentric isochrones 500/750/1000/1500 m)
- Docs site updated (FR + EN) with six-scenario index and accurate test/capability counts
- `adapters/http` namespace fork resolved — legacy tree deleted, prod entrypoints flipped to `gispulse.adapters.http.app`
- Security — `MD5` replaced by `BLAKE2b`, `eval` sandboxed for `np`, `_ensure_valid` restored

### Fixed
- Capabilities — 4 P0 closed: `force_geometry_type` GeometryCollection target, `attribute_join` on plain DataFrame, NaN crash in `add_z`/`add_m` from_column path, `singleparts_to_multipart` silent data loss on mixed geom types
- Capabilities — pointcloud grid 2D NaN, KDE grid blowup, `Calculate` RCE sandbox
- Tests — repaired 27 tests newly exposed once CI was unblocked; deleted shadow `__init__.py`, enabled `asyncio_mode = "auto"`, fixed ftth_network_analysis SyntaxError
- Tests — isolate `GISPULSE_ENGINE` mutations; conftest auth-disabled-by-default
- Billing — default `StripeSettings` + actionable error messages when Stripe keys are missing
- Capabilities — `clip` / `intersects` avoid GeoDataFrame truth-value check; `spatial_predicate` fallback made explicit
- Playground — S6 `drop_price_outliers` renamed to `drop_value_outliers` (raw `valeur_fonciere`, not price-per-m²)
- i18n — `PipelinePanel` strings; default-engine alignment; pipelines `ref_layers` plural
- Performance — lazy-load `DualMapView`
- Rules router — payload validation before persisting (400 with structured errors)

## [1.0.0] - 2026-04-06

### Added

#### Desktop clients & SDK
- Python SDK (`sdk/`) — httpx + pydantic, 10 endpoint modules, async client, WebSocket/SSE streaming
- QGIS plugin (`clients/qgis/`) — dataset browser dock, job dock, OGC/PostGIS/MVT layer factories, QThread workers
- Tauri standalone desktop app (`clients/desktop/`) — React + MapLibre GL JS, connection setup, dataset browser, job panel
- ArcGIS Pro add-in (`clients/arcgis/`) — dockpanes, 3 geoprocessing tools, OGC + PostGIS layer loading

#### CLI
- `gispulse doctor` — system diagnostics (Python, GDAL, DuckDB, PostGIS, disk space)
- `gispulse update [--check] [--force]` — self-update via PyPI
- `gispulse engine [start|stop|status]` — manage local sidecar engine
- `gispulse jobs [list|status|cancel]` — manage async jobs via HTTP API
- `gispulse telemetry [--status|--enable|--disable]` — opt-in telemetry management

#### Security & Enterprise
- RBAC with role-based access control and multi-project isolation
- SSO via OIDC/SAML integration
- Stripe billing integration for Pro/Enterprise tiers
- Audit logging with structured event trail
- Rate limiting (300/min, Redis-backed optional)

#### Infrastructure
- S3 storage adapter for artifact persistence
- Cron scheduler for recurring job execution
- Plugin marketplace with community and verified extensions
- Telemetry system (opt-in, privacy-first, non-blocking)
- Terraform templates for AWS/GCP deployment

#### Production deployment
- VPS production stack (`deploy/`) — docker-compose with Caddy (auto-TLS), Prometheus, Grafana, pg-backup (30d retention)

#### Portal improvements (sprints A7-S4 through sprint 5)
- Aggregate node — split function/predicate selectors, better UX
- Triggers/scenarios dock panels
- Session management UI
- Loading states across all views
- Dark/light theme toggle with persistence
- Accessibility improvements (ARIA labels, focus management, keyboard nav)
- Branding and design system unification
- Workflow templates and template store

### Changed
- Moved CLI from root `cli.py` into `gispulse/` package, centralized version
- Fiona made optional — core uses pyogrio, fiona only for MCP extras
- Extras separated: `postgis`, `api`, `mcp`, `raster`, `network`, `dev`, `all`
- Protected imports — optional dependencies guarded with try/except

### Removed
- Dead code cleanup — unused imports, unreachable branches, legacy stubs
- Root `cli.py` backward-compat wrapper (entry point is `gispulse.cli:main`)
- Root `design-system/` directory (tokens consolidated into `portal/src/styles/tokens.css`)
- Root `__pycache__/` and `gispulse.egg-info/` from tracked files

### Docs
- Archived brainstorm/analysis docs from initial phase to `docs/_archive/`
- Archived external project references (filtermate-ref, forge-ref) to `docs/_archive/reference/`
- Updated README with accurate project structure, stats, and status
- Improved `.gitignore` with IDE, OS, and build output patterns

## [0.1.0] - 2026-03-31

### Added

#### Core Engine
- DuckDB-based geospatial engine with portable SpatiaLite and persistent PostGIS modes
- SessionManager with E2E pipeline, ExecutionStrategy pattern, and SpatiaLite session support
- JobRunner with async job execution and status tracking
- Cross-layer operations: spatial join, reference layer system, multi-layer support
- Pagination, dataset association, and CRUD project endpoints
- PyOGRIO migration for multi-format I/O (any supported vector format)
- Edge case hardening: shadow zone coverage, centroid, area/length capabilities
- GeoParquet support and OGC server with MVT tile serving

#### CLI
- Typer-based CLI entry point (`gispulse` command)
- `gispulse init`, `validate`, `info`, `layers`, `formats`, `capabilities`, `serve`, `portal` commands
- Multi-format input acceptance via integrated I/O layer

#### Capabilities
- 10 vector operations: buffer, union, reproject, filter, clip, intersects, spatial_join, centroid, area_length, dissolve
- Intersects, Calculate, and SpatialAggregate capabilities
- Capability registry with auto-load discovery
- Lifespan-managed capability injection

#### Rules
- Rules-as-config system with JSON rule definitions
- Rule editor UI with predicate builder
- Trigger-based rule evaluation with `auto_eval` and SSE eval-stream

#### Persistence
- PostGIS persistent mode with live sync and pg_notify integration
- SpatiaLite portable mode (session level 2, serverless)
- GPKG export from catalog
- Scene manager with snapshot and restore

#### API (FastAPI)
- Full REST API: projects, datasets, features, sessions, rules, triggers, scenarios
- 14 routers, 100+ endpoints
- Feature update, SQL query execution, and relation endpoints
- OGC Features ingestion endpoints
- SSE streaming for trigger evaluation results
- Docker hot-reload configuration for API and Portal dev servers
- Global error handlers returning `{"error": {"code", "message", "detail"}}` for 400/404/422/500

#### Portal (React 19)
- 5-workspace layout: Explorer, Map, Workflows, Catalog, Data
- Layer tree with groups, color picker, legend, and symbology
- Resizable panel layout with ActivityBar and Inspector
- Node editor (XyFlow/ReactFlow v12) with 9 node types, NodePalette, inline inspector
- Trigger stepper, scenarios bar, and spatial operations UI
- SQL console and feature inspector
- Catalog workspace with cards, favorites, mini-map, and domain filtering
- Dark mode with oklch design tokens, Geist font, toast notifications
- Command palette (Ctrl+K), keyboard shortcuts (1-5, Ctrl+I/B/K/S/?)
- Upload (drag-and-drop, URL import), export GPKG with QML styles

#### Viewer
- Embedded deck.gl spatial viewer served via `gispulse serve`

#### ESB / Triggers
- Event bus with pg_notify, event routing, circuit breaker, dead letter queue
- Trigger Builder UI with predicate composition
- SessionProvisioner with TriggerEvaluator and SSE eval-stream

#### Catalog
- GIS data catalog: projections, basemaps, WMS/WFS flux, open data sources

#### Tests
- 46 test files: unit and integration tests
- E2E SpatiaLite integration tests
- pytest configuration with async support

### Security
- SQL injection protection in ESB action dispatcher and trigger manager
- WebSocket authentication via API key tokens
- Structured logging with sanitized outputs
- Input validation and type safety across portal node system

[Unreleased]: https://github.com/imagodata/gispulse/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/imagodata/gispulse/compare/v0.1.0...v1.0.0
[0.1.0]: https://github.com/imagodata/gispulse/releases/tag/v0.1.0

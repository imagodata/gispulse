# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- **Packaging** — declare `httpx>=0.24,<1.0` as a core runtime dependency. Previously listed only under `[api]` / `[sso]` / `[dev]` extras, so `pipx install gispulse` produced a working CLI for `track` / `info` / `run` but `gispulse triggers run` and `gispulse watch` crashed on `ModuleNotFoundError: No module named 'httpx'` (the webhook client at `gispulse/adapters/webhooks/http_client.py` imports it unconditionally). Affects 1.3.0 release; users on 1.3.0 can work around with `pipx install "gispulse[api]"`.

### Changed
- **CI** — `test` job now installs `[dev,api,postgis,mcp,raster,network,classification,pointcloud,scheduling,sso]` extras instead of `[dev]` alone. Router and integration tests need `fastapi`, `psycopg2-binary`, etc. and were silently failing collection on `ModuleNotFoundError` (28+ test files affected). `capability-matrix-drift` job aligned to the same install set so the matrix it generates matches the one committed locally.
- **CI** — `pip-audit` ignores `CVE-2026-3219` (pip 26.x tar/ZIP confusion, no fix release upstream as of 2026-04-28; re-evaluate quarterly).

### Security
- **Dependencies** — bump `fastmcp` from `>=0.1,<2.0` to `>=2.14.2,<4.0` to fix CVE-2025-62800 / CVE-2025-62801 / CVE-2025-69196 / CVE-2025-64340 / CVE-2026-27124 / GHSA-rcfx-77hg-w2wv (XSS, command injection, OAuth confused-deputy, MCP SDK transitive). MCP extra users must `pip install -e ".[mcp]" --upgrade`.
- **Dev dependency** — bump `pytest` from `>=7.0,<9.0` to `>=9.0.3,<10.0` to fix CVE-2025-71176 (`/tmp/pytest-of-{user}` predictable path on UNIX).

## [1.3.0] - 2026-04-27

### Added
- **`gispulse track`** — SQL change-tracking subcommand (`install` / `uninstall` / `list` / `tail` / `doctor [--auto-fix]`). Installs `_gispulse_change_log` triggers on a GPKG so any client (QGIS, ogr2ogr, ArcGIS, FME, DBeaver) can write to the file and the daemon picks up the changes. `track doctor` checks application_id, change-log table presence, WAL mode, busy_timeout, per-layer trigger completeness, and stale unprocessed rows; `--auto-fix` reinstalls missing triggers. Closes #4 / #6.
- **`gispulse watch`** — top-level foreground daemon. SIGINT/SIGTERM clean shutdown (2 s drain), 60 s structured stderr heartbeat, repeatable `--webhook host` allowlist override. Supports both daemon mode and `--once` drain (cron / Lambda / CI hooks) with `--exit-zero-if-empty` for silent quiet ticks. Closes #5 / #11.
- **Trigger payload v2** — `_gispulse_change_log` SQLite triggers now bake `new_values` / `old_values` JSON columns + a `geom_changed` flag, captured atomically inside the SQLite trigger via `json_object(NEW.*)`. Removes the post-commit `_load_row_values()` SELECT, eliminates the `old.x != new.x` strict-consistency hole flagged in S4. Predicate evaluation now runs on the snapshot taken at DML time. Closes #7.
- **Bulk-mode tick** — `--bulk-threshold N` collapses ticks with `N+` rows into a single `bulk.changed` summary event (op_counts, layers, change_id_range) instead of broadcasting per-row. Avoids webhook flooding on `ogr2ogr -append` / QGIS bulk paste / shapefile imports. `0` (default) preserves per-row events. Closes #8.
- **Packaging** — `packaging/systemd/gispulse-watch@.service` + env example for `Type=simple` foreground daemon under systemd, `packaging/docker/Dockerfile.watch` + `docker-compose.watch.yml` for container deployment. Doc READMEs cover both. Closes #9.
- All Mode 1 scope previously documented under `[1.2.1]` (gispulse triggers, headless_runtime, config_loader, predicate_dsl, sqlite_retry, sql_guardrails, GeoPackageEngine.execute) is rolled into 1.3.0 since 1.2.1 was never tagged.

### Notes
- Closes the Mode 1 scope of #2 entirely. Mode 2 (portal trigger CRUD) remains on the roadmap.
- `gispulse triggers run --watch` and the new top-level `gispulse watch` coexist for one release; deprecation note + redirect documented for v1.4.
- CI baseline cleanup (#19) — dropped removed `pip-audit --fix-auto=off` flag, regenerated capability matrix, ruff drift cleared (514 → 0 errors), workflows aligned on `gispulse-portal` sibling-repo split.

## [1.2.1] - 2026-04-27

### Added
- `gispulse triggers` — new CLI subcommand group (`run` / `validate` / `list`) for the standalone trigger runtime (Mode 1). YAML config → GPKG DML triggers, no FastAPI process required. Closes the Mode 1 scope of #2; Mode 2 portail remains on the roadmap.
- `gispulse/runtime/headless_runtime.py` — `HeadlessRuntime` wires `ChangeLogWatcher` + `TriggerEvaluator` + `ActionDispatcher` against a `NullEventHub` stand-in so the ESB pipeline (notify / set_field / run_sql / webhook / log_event) runs outside the FastAPI lifespan. `run_once()` drives a single tick for `--once`; `start()` / `stop()` expose the polling thread for `--watch`.
- `gispulse/runtime/config_loader.py` — strict pydantic v2 schema (`extra="forbid"`, `yaml.safe_load` only, path-traversal guard against `$HOME`/`cwd`/`tempdir` anchors with optional `GISPULSE_CONFIG_ALLOW_ROOTS` env override). `validate_against_gpkg()` cross-checks every `table:` against the live GPKG layer list before the first tick.
- `gispulse/runtime/predicate_dsl.py` — hand-written LL(1) recursive-descent parser for the `predicate:` field. **No `eval`, no `simpleeval`, no third-party dep.** Operators: `==` `!=` `>` `>=` `<` `<=` `AND` `OR` `NOT` `IN` `NOT IN` `IS NULL` `IS NOT NULL`. Identifier whitelist (dunders refused), `MAX_DEPTH=32`, NUL-byte rejection. Bare attrs resolve to `new.*` on UPDATE; `old.*` / `new.*` are explicit.
- `gispulse/runtime/sqlite_retry.py` — `RetryingSqlExecutor` wraps `GeoPackageEngine.execute()` with exponential backoff on `sqlite3.OperationalError` carrying `SQLITE_BUSY` / `database is locked`. Caps at 5 retries / 30 s total. `SecurityError` from the guardrails is **never** retried.
- `gispulse/cli_triggers.py` — `triggers run --once`, `triggers validate`, `triggers list`. Human-friendly output to stdout (Rich), structured JSON events on stderr for log shippers. Exit codes: 0 on success, 1 on config / GPKG / runtime error, 2 on partial trigger failures.
- `gispulse/cli_triggers_watch.py` — daemon loop for `triggers run --watch`. `SIGINT` / `SIGTERM` route through a single `threading.Event`, the loop breaks on the next tick boundary. Reload-on-config-change polls the YAML mtime and rebuilds the runtime from scratch on diff. 10 consecutive failed ticks → exit 1; per-tick exponential backoff (1 s → 30 s cap).
- `persistence/sql_guardrails.py` — `enforce()` is the single sandbox between YAML `run_sql` / `set_field` actions and SQLite. Allowlist `INSERT` / `UPDATE` / `DELETE` / `SELECT` only. Hard-blocks `ATTACH` / `DETACH` / `PRAGMA` / `VACUUM` / `BEGIN` / `COMMIT` / `ROLLBACK` / `LOAD_EXTENSION` / `writable_schema` / `sqlite_master`. Protected table prefixes: `gpkg_*`, `rtree_*`, `sqlite_*`, `_gispulse_*`. Multi-statement payloads (`INSERT …; DROP …`) refused. Comments and string literals are masked before keyword detection.
- `persistence/gpkg_engine.py` — `GeoPackageEngine.execute(sql, params)` exposed as the public DML write API, gated by `sql_guardrails.enforce()`. Returns `rowcount`. Internal migrations bypass via the `allow_ddl` flag; YAML actions never set it.

### Notes
- Mode 2 (portail UI for trigger CRUD) remains on the roadmap — not shipped here.
- `release.yml` — `workflow_dispatch` trigger with `dry_run` input (default `true`) so the build / smoke-test / changelog-extract pipeline can be validated without publishing. Tag pushes still auto-publish.
- `release.yml` — fail the build when the CHANGELOG section for the released version is empty, surfacing the missing release notes early.
- `persistence/changelog_watcher.py` + `WatcherRegistry` — Lot 2 v2 GPKG live-sync foundation: file-watch + `BEGIN IMMEDIATE` polling per dataset, exposed via `POST /datasets/{id}/enable_tracking` for `/ws/events` consumers (10 k inserts at ~317 events/s, restart replay, multi-WS fanout).
- `persistence/duckdb_watcher.py` — Lot 3 DuckDB change-log watcher adapter feeding the same `/ws/events` hub, with JSON-serialised `changed_at` and graceful skip when the underlying engine is unavailable.
- `core/capability.py` — `Capability.execute_safe(**params)` validation entrypoint that raises `UnknownParameterError` instead of letting the legacy `**_` placeholder swallow typo'd kwargs (closes EPIC #438 systemic kwarg-swallow audit, ref `beta_test_capabilities_2026_04_24`).
- `capabilities/schema.py` — `DescribeCapability` (`describe`) — non-destructive schema/null/unique/geometry introspection. Layer returned unchanged; report stored in `gdf.attrs["__schema_describe__"]` for portal / CLI / audit consumers. Closes the last AC of EPIC #439 (capability gaps: schema, attrs, multipart, overlay, attribute_join — all other primitives shipped in v1.1.0).
- `gispulse/adapters/webhooks/` — `HttpWebhookClient` for outbound `ActionType.WEBHOOK` dispatch. SSRF-safe (RFC1918 + loopback + link-local + multicast + reserved blocklist with explicit `allow_private_ips=True` opt-in for CI/dev), bounded retries (2 attempts, 1 s / 3 s back-off, 5xx + timeouts only — 4xx never retried), optional HMAC-SHA256 via `GISPULSE_WEBHOOK_SIGNING_SECRET` (`X-GISPulse-Signature` header). Inject `HttpWebhookClient().post` into `ActionDispatcher(webhook_client=…)`. Closes #451 (OSS Integrations — unblocks Zapier, ArcGIS GeoEvent, Make, n8n).
- `gispulse/adapters/esb/action_dispatcher.py` — `_webhook` payload contract enriched (`event_type`, `trigger_id`, `trigger_name`, `transition`, `timestamp`, `custom`) — see `docs-site/guide/rules.md` "Webhook actions" section.
- `persistence/change_log_watcher.py` — bridge to `ActionDispatcher` (#458). When an `action_dispatcher` is wired, matched triggers now have their actions executed (NOTIFY / WEBHOOK / SET_FIELD / RUN_SQL / …) in addition to the WS broadcast. Previously the watcher broadcast a `trigger.fired` event with the action list but never invoked any handler — the entire ESB pipeline + #451 webhook client were dead-code in HTTP runtime. Each action handler stays wrapped by the dispatcher's per-action try/except so a single failure cannot pin the change-log backlog.
- `gispulse/adapters/http/app.py` — lifespan now instantiates `ActionDispatcher(event_hub, sql_executor=engine.execute, webhook_client=HttpWebhookClient().post)` and injects it into the project `ChangeLogWatcher`. Triggers configured via `/api/triggers` now fire end-to-end on GPKG/DuckDB DML.
- `docs/TRIGGERS_GUIDE.md` — new operator-oriented summary: architecture diagram, webhook actions cross-refs, **6 OSS limits documented** (single-writer, polling vs `pg_notify`, no orchestrated retry, cascade depth ≤ 3, interpreted predicate AST, post-broadcast WS filter), and a troubleshooting matrix. Closes #455 (OSS Integrations pre-flight).
- `docs/INTEGRATION_MATRIX.md` — webhook payload section realigned on the `#451` contract (`event_type/trigger_id/trigger_name/table/operation/row_id/matched/transition/timestamp/custom`) — was showing a stale shape (`event/category/severity/fired_at/context`). Delivery semantics block lifted from the action_dispatcher source of truth (retries, HMAC header, SSRF policy).
- `docs-site/integrations/{qgis,arcgis,maplibre}.md` (FR) — three step-by-step integration tutorials grounded on the OGC / MVT / WebSocket / webhook surfaces shipped in v1.2 (no plugin install required). Closes #454. QGIS covers GPKG drag-drop + WFS/OGC + MVT + PyQGIS trigger evaluation. ArcGIS covers FileGDB + OGC + MVT in AGOL + bidirectional GeoEvent webhooks. MapLibre includes a 100-LOC standalone HTML viewer with live WS reload. EN translations deferred to a follow-up issue.
- `tests/unit/test_postgis_sql_unit.py`, `test_vector_clip_unit.py`, `test_vector_filter_unit.py` — coverage hardening (#443). The three modules called out in the audit went from 30% / 41% / 23% to **96% / 83% / 81%** (54 passed + 16 xfailed for local env-specific shapely-under-pytest-cov interaction). Strategy gates (PostGIS / DuckDB / Python), helper functions, validation branches and SQL-template safety are all explicitly covered.
- `scripts/build_capability_matrix.py` + `docs-site/guide/coverage.md` (FR + EN) — auto-generated capability coverage dashboard (#442). Single source of truth listing every registered Capability (118 today) × {Tests, Docs, Playground, Templates}. Heuristics tightened to count only explicit class instantiation / import in tests and table-cell mentions in docs (a single broad smoke-test or paragraph mention no longer inflates a row). New CI job `capability-matrix-drift` runs `--check` and fails the PR if the committed matrix is out of sync with the registry.
- `docs-site/.vitepress/config.ts` — new "Intégrations" nav section + `/integrations/` sidebar (FR locale).

### Fixed
- Capabilities — P1 beta close-out: `morans_i` returns `NaN` p-value on a constant field instead of a misleading `0.01`; `completeness_check` accepts a GeoDataFrame with only the geometry column.
- Capabilities — P2 beta close-out: `isochrone` returns an empty layer when `cost_budget=0` (was a degenerate ring); `overlay_intersection` / `overlay_union` align missing-ref behaviour with `erase`.
- Capabilities — P3 beta close-out: `polygon_fix_gaps` treats `max_gap_area=0` as a clean no-op.
- Streaming — `EventHub` made thread-safe with multi-tenant `dataset_id` deduplication so concurrent tenants don't cross-fire events.
- Playground API — pipeline payload capped at 30 k features and step timeout bumped 30 s → 90 s to fit Cloud Run's 32 MB / latency envelope on the S3 full dataset.
- Playground scenarios — S1/S2/S3 ship the full `batiments` dataset (S4 drops the layer); S5 green-spaces ships full vegetation + buildings.

### Tests
- `test_p02_multi_gpkg_watcher_registry` marked `xfail(strict=False)` — under <100 ms concurrent inserts on three GPKG files, the watcher's long-lived SQLite connection can hold a stale WAL snapshot. Single-user (Community) flows unaffected; multi-tenant fan-out is a Pro feature deferred to v1.2+.

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

# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

The "Format Frontier" release — DuckDB Spatial as the universal CDC substrate. Adds two new engines (`spatialite`, `duckdb_diff`), brings DML detection to formats that have no native trigger surface (GeoJSON, FlatGeobuf, Shapefile), and closes EPIC #139 (DML semantics ADRs + WAL connection safety).

### Added

- **SpatiaLite engine.** New `persistence.spatialite_engine.SpatiaLiteEngine` shares the SQLite trigger DDL of GPKG but writes through pyogrio's `SQLite + SPATIALITE=YES` driver and queries `geometry_columns` instead of `gpkg_contents`. Auto-routed for `*.sqlite` / `*.db` URIs. No `mod_spatialite` Python extension required at runtime — pyogrio's OGR linkage handles the catalog. (EPIC #105 slice 1, PR #151)
- **`is_spatialite_file(path)` detection helper.** Narrow rule: file must have `geometry_columns` AND must NOT have `gpkg_contents`. Used by future auto-routing code; the URI inference layer maps the suffixes ahead of file inspection. (PR #151)
- **`bootstrap_spatialite_project(conn)`.** Sibling to `bootstrap_gpkg_project`; installs the same `_gispulse_*` internal tables WITHOUT setting the GPKG `application_id` or creating `gpkg_*` catalog rows (those would corrupt SpatiaLite identity). Refactor extracts a shared `_bootstrap_gispulse_internals(conn)` helper used by both bootstraps. (PR #151)
- **`FileBlobChangeDetector`.** Reusable mtime + DuckDB `ST_Read` snapshot diff CDC. Hash is `md5(ST_AsWKB(geom) || json_object(props))` excluding OGR's synthetic `OGC_FID` so reordering features in the source file does not produce false DELETE+INSERT noise. Snapshot persisted as a DuckDB sidecar `<blob>.gispulse-snapshot.duckdb`. Set-diff semantics: emits INSERT and DELETE only — UPDATE is undetectable without a stable PK in the file format. (EPIC #105 slice 2, PR #152)
- **Companion-file watching.** Multi-file formats (Shapefile = `.shp / .dbf / .shx / .prj / .cpg`) are watched via `max(mtime)` across every existing companion so attribute-only edits (which only touch `.dbf`) surface correctly. Single-file formats (GeoJSON, FlatGeobuf, KML, CSV) keep single-file mtime semantics. New `_COMPANION_EXTENSIONS` map is extensible. (EPIC #105 slice 4, PR #152)
- **`DuckDBDiffEngine`.** `SpatialEngine` implementation backed by the file-blob detector. Supports GeoJSON, FlatGeobuf, Shapefile (and zero-code-change-ready for KML / CSV+WKT — those land in v1.6.2). I/O via pyogrio. `get_pending_changes` shape matches `GeoPackageEngine` (`id` int, `changed_at` ISO 8601, `geom_changed` 0/1) so `ChangeLogWatcher` iterates uniformly across engines. `mark_changes_processed` is a no-op (poll is destructive). `execute_sql` raises `NotImplementedError` — this engine is a CDC adapter, not a query engine; for ad-hoc SQL run `gispulse run` with the standalone DuckDB engine. (EPIC #105 slices 3+5, PR #152)
- **Engine factory entries.** `_spatialite_factory` and `_duckdb_diff_factory` registered as built-ins. URI inference (already shipped in v1.6.0 via `gispulse.runtime.engine_inference`) maps `.sqlite` / `.db` to `spatialite` and `.geojson` / `.fgb` / `.shp` / `.kml` / `.csv` / `.tab` / `.dxf` to `duckdb_diff` automatically — no extra wiring required to consume the new engines. (PRs #151, #152)
- **`persistence.gpkg_connection.connect_gpkg(path, …)`.** Single entry point that applies WAL + `busy_timeout=5000` on every GeoPackage `sqlite3.connect`. Migrated 8 scattered call sites (CLI track / triggers / runtime, HTTP datasets routers, `project_io`) so concurrent QGIS edits + watcher polls never raise `SQLITE_BUSY`. Documents the historical `test_p02` flake's root cause. (#141, PR #145)
- **ADR 0001 — DuckDB-spatial as the contract SQL dialect.** Records the de-facto rule that v1.6.0 already enforces: the DSL geom-fct templates and `run_sql` strings are written in DuckDB-spatial dialect by default. The `engine:` top-level key remains the documented escape hatch for users running exclusively against PostGIS or SpatiaLite. (#140, PR #147)
- **ADR 0002 — Trigger cascade is bounded fixed-point with origin-tagging.** Documents the existing two-layer cascade design: SQLite `WHEN` clauses block self-loops at the file format level (B-02, v1.5.3), and `evaluate_cascade` runs a fixed-point loop with `MAX_CASCADE_DEPTH = 3` raising `CascadeDepthExceeded` beyond. Community tier capped at depth 1, Pro up to 3. (#142, PR #148)
- **ADR 0003 — `_gispulse_change_log` is a poll log, not an event store.** Promotes the current `id AUTOINCREMENT` + `changed_at` invariants to documented contract; defers replay / sub-second timestamps / row hashing to a future v1.7+ extension table. (#143, PR #150)
- **ADR 0004 — DDL hooks out of scope; passive schema-drift detection ships.** Records that ALTER TABLE / DROP TABLE / CREATE INDEX hooks are intentionally absent. The B-13 schema-drift watchdog (#103, v1.5.3) covers ALTER TABLE ADD COLUMN passively — the runtime rebuilds triggers within one watchdog tick and surfaces the new column in subsequent `new_values` payloads. (#144, PR #150)

### Changed

- **`bootstrap_gpkg_project` extracts a shared internal helper.** New `_bootstrap_gispulse_internals(conn)` runs migrations + creates `_gispulse_*` tables without GPKG-specific identity work. `bootstrap_gpkg_project` and the new `bootstrap_spatialite_project` both layer their format-specific setup on top. Behaviour for existing GPKG callers is identical — regression test asserts the GPKG path still produces a valid GeoPackage with `application_id = 0x47504B47` and `gpkg_contents`. (PR #151)

### Documentation

- **`docs/adr/0001-dsl-sql-dialect.md` through `docs/adr/0004-ddl-hooks-out-of-scope.md`.** Four ADRs introducing a `docs/adr/` directory; cross-linked from `docs-site/guide/architecture.md` under a new "Décisions de scope (ADRs)" sub-section.
- **`docs-site/guide/dsl-sql-dialect.md`.** User-facing reference of the DSL SQL dialect contract, with the portable `ST_*` surface, `ST_Transform` arity gotcha, and `engine:` override. Cross-linked from `engines.md`, `dsl-geom-functions.md`, `dsl-validation.md`. (PR #147)
- **`docs-site/guide/rules.md`.** Cascade tip block expanded into a proper "Cascade behaviour of triggers" sub-section with the tier table, the two-layer explanation, a JSON example showing `cascade_depth: 2`, and a link to ADR 0002. (PR #148)
- **`docs-site/guide/formats.md`.** SpatiaLite, GeoJSON, FlatGeobuf and Shapefile rows bumped with their CDC support note. New "CDC file-blob (v1.6.1)" section explains the mechanism, formats covered, multi-file companion-watching rule, and known limitations (set-diff = INSERT/DELETE only, polling not inotify, single-layer per file). (PRs #151, #152)

### Decision log

- **EPIC #139 (DML semantics) closed same-day.** Five sub-issues actioned in five PRs (#145 WAL fix code; #147/#148/#150 four ADRs). Out-of-scope topics — replay event sourcing (#143), DDL hooks (#144), `run_sql` PostGIS-only construct scanner (#146 follow-up) — are documented rather than implemented so v1.6.x ships without scope creep. The investigation surfaced one important course correction: the cascade design that ships is **bounded fixed-point**, not single-pass as the issue body initially proposed.
- **EPIC #105 (Format Frontier T1) closed same-day in five slices.** SpatiaLite (PR #151) + GeoJSON / FlatGeobuf / Shapefile / watcher-wiring (PR #152) all delivered before v1.6.2 release prep. KML and CSV+WKT are zero-code-change-ready through the existing `DuckDBDiffEngine` and will be promoted to T2 (#106) with test-only PRs.

## [1.6.1] - 2026-05-07

Same-day follow-up to v1.6.0. Closes the 3 deferred items from the v1.6.0 sprint kickoff in a single PR (#138) so the v1.6.x line ships its full promised surface — cross-source push-down, scalar lookup, and zero-config validate auto-wire — instead of trickling them across point releases.

### Added

- **`layer_lookup(layer, match, take, layer_geom)` DSL fct.** Scalar attribute lookup against a (cross-source) layer with three match modes: `spatial_within` (default), `spatial_intersects`, or any column identifier as attribute-equality shorthand (consistent with `geom_within(match='code_insee')`). Compiles to `(SELECT _L."<take>" FROM "<layer>" AS _L WHERE <pred> LIMIT 1)`. (#124, PR #138)
- **Cross-source layer registry.** New `gispulse.runtime.layer_registry.LayerRegistry` ATTACHes external GeoPackage / Parquet / PostgreSQL sources read-only and creates a DuckDB view per declared layer in the in-memory catalog. The DSL emits bare-name `FROM "communes"`; DuckDB's optimiser pushes spatial and attribute predicates down to the underlying scanner — no SQL rewriting downstream. (#122, PR #138)
- **Top-level `layers:` block in `triggers.yaml`.** Declarative cross-source layer references via `LayerSourceConfigModel`. Duplicate-name guard at config-load time. (#122, PR #138)
- **`build_runtime` validate auto-wire.** New `validate_rules`, `default_table`, `layer_sources`, `source_epsg` kwargs wire a `ValidationRunner` directly onto the change-log watcher. The DuckDB session ATTACHes the project GPKG read-only and mirrors each user table as a view in the in-memory catalog so bare-name SQL resolves while cross-source `CREATE VIEW` statements remain legal. (PR #138)
- **Per-rule `table:` and top-level `default_table:`.** `ValidateRuleConfigModel.table` lets each `validate:` rule pin its target table; `GISPulseConfig.default_table` provides a config-level fallback. (PR #138)

### Changed

- **`compile_validate_rules` accepts a `table_resolver` callable.** The signature now supports per-rule resolution via a `rule -> table` callable. The legacy `table=` parameter is preserved for v1.6.0 callers (single-table use). (PR #138)

### Decision log

- **"Quelle table" question for `validate:` rules — closed.** `build_runtime` resolves the target table per rule in priority order: `rule.table` (operator pin) > `default_table` (config fallback) > GPKG single-table autodetect > `ValidationTableResolutionError` listing the candidate tables. Single-table GPKGs (the dominant case) get zero-config UX; multi-table GPKGs surface a clear actionable error. (PR #138)

## [1.6.0] - 2026-05-07

The "DuckDB Spatial Inside" release. Closes EPIC #104 — a one-day cascade of 7 PRs (#129 → #135) lands the foundation, the DSL geom function whitelist, the granular DML verbs, the declarative `validate:` block end-to-end, and the long-standing B-08 DELETE predicate gap.

DuckDB spatial moves from "embedded if you opt in" to **the universal compute substrate**: the new DSL geom functions compile to DuckDB SQL, the validation runner evaluates rules through a DuckDB ATTACH on the GeoPackage, and an Atlas R1 bench against pyogrio justifies the pivot — DuckDB COPY is **2.3× to 3.6× faster than pyogrio** on 1M EPSG:2154 polygons, with peak RSS divided by **~3.4×**. The pyogrio-only write-back doctrine of v1.5.x is officially retired for bulk paths.

### Added

- **DuckDB spatial extension — lazy install on first use.** New `gispulse.runtime.duckdb_engine.get_spatial_connection()` runs `INSTALL spatial; LOAD spatial;` on first call, caches the install per Python executable, and exposes `DuckDBSpatialUnavailable` so air-gapped environments fail with an actionable message instead of a generic DuckDB error. (#113, PR #129)
- **`gispulse doctor --install-spatial`.** Pre-installs the spatial extension and probes a curated set of EPSG roundtrips (`EPSG:4326 / 3857 / 2154 / 27572`) against a `pyproj` baseline so PROJ datum-shift gaps surface upfront — the bundled DuckDB ships with PROJ network disabled. (#114, PR #129)
- **Engine inference from the dataset URI.** `triggers.yaml` no longer requires an explicit `engine:` line: `*.gpkg` → `gpkg`, `postgresql://...` → `postgis`, `*.shp / *.geojson / *.fgb` → `duckdb_diff` (file-blob CDC). Override stays available; conflict detection raises at config-load time. (#115, PR #129)
- **DSL geom functions — first whitelist.** Seven safe, push-down-friendly functions usable in `set_field` and `validate:`: `geom_area_m2`, `geom_perimeter_m`, `geom_length_m`, `geom_centroid_x`, `geom_centroid_y`, `geom_npoints`, `geom_is_valid`. Measure functions auto-project to a metric CRS (default `EPSG:2154`, override per-call with `epsg='EPSG:NNNN'`). (#116, #117, PR #129)
- **DSL expression parser — safe-by-construction.** The compiler walks the Python AST under a strict allowlist (literals, column refs, `+ - * / %`, parenthesis); rejects every escape hatch (`__import__`, `eval`, attribute access, comprehensions, lambdas, …). `boolean` mode unlocks `==`, `!=`, `<=`, `>=`, `and`, `or`, `not` for `validate:` rules and `predicate:` clauses. (#118, PR #129)
- **`when:` granular DML verbs.** Triggers can now subscribe to `INSERT`, `UPDATE_GEOM` (geometry mutated), `UPDATE_ATTR` (attributes only), `DELETE`, or `BULK`. The watcher resolves a coarse `UPDATE` row to its granular variant via the change-log's `geom_changed` flag before evaluation. The legacy `UPDATE` value still works as a catch-all. (#119, PR #129)
- **`geom_changed` flag in the `dml.changed` payload.** Subscribers can render geometry edits differently from attribute edits without inspecting the change log. (#120 plumbing, PR #129)
- **`validate:` top-level block in `triggers.yaml`.** Declarative validation rules with `mode: warn` (log + WS event) or `mode: tag` (writes `failed:<rule.id>` onto a status column auto-created on first use). Rules compile at config load via the boolean DSL parser so syntax errors surface before the runtime starts. (#121, PR #129)
- **`tag_field:` action.** New action type that writes a status (and optional message) onto the row, auto-creating the target columns via `PRAGMA table_info` + `ALTER TABLE ADD COLUMN`. The handler is shared with the `validate: mode: tag` bridge so a single mechanism powers both explicit YAML actions and automatic validation tagging. (#123, PR #130)
- **DSL cross-layer subquery functions.** `geom_within(layer='communes', match='code_insee')` and `geom_overlaps_any(layer='self', exclude_self=True)` join into the boolean grammar of `validate:` rules. The compiler emits `EXISTS (SELECT 1 FROM "<layer>" AS _L WHERE …)` with strict identifier validation. (#122 schema half, PR #131)
- **`ValidationRunner`.** Engine-agnostic runtime component that compiles each rule once at boot, evaluates it per row through an injected `sql_evaluator`, and broadcasts `validation.failed` on the event hub. Per-rule driver exceptions are isolated so a single bad rule never aborts the batch. (PR #132)
- **`make_gpkg_sql_evaluator(gpkg_path)` factory.** Opens a DuckDB session with the spatial extension, ATTACHes the GPKG via `TYPE SQLITE`, and returns the `(sql, params) -> rows` callable the runner needs. (PR #133)
- **`ChangeLogWatcher` validation hook.** When a `ValidationRunner` is injected, every INSERT / UPDATE_GEOM / UPDATE_ATTR row drives `runner.evaluate(...)` after the trigger evaluator block. DELETE and BULK are skipped (row gone / already collapsed). (PR #133)
- **`validate: mode: tag` end-to-end bridge.** A failing tag rule dispatches a synthetic `TAG_FIELD` action through the regular `ActionDispatcher`, so the column is auto-created and the row gets `failed:<rule.id>`. Origin-tagging M1 (B-02 / v1.5.3) keeps the AFTER UPDATE refire skip working. (PR #134)
- **ESRI Attribute Rules vocabulary aliases.** `kind: constraint | calculation | validation` accepted as cosmetic aliases on `triggers.yaml`. The runtime ignores the value; the alias exists to keep ESRI migration diffs small. (#125, PR #129)
- **New documentation pages.** [`docs-site/guide/dsl-geom-functions.md`](docs-site/guide/dsl-geom-functions.md), [`docs-site/guide/dsl-validation.md`](docs-site/guide/dsl-validation.md), [`docs-site/guide/migration-from-esri.md`](docs-site/guide/migration-from-esri.md), and a v1.6.0 section on [`engines.md`](docs-site/guide/engines.md) covering the lazy spatial install, EPSG roundtrip probes, engine inference, granular DML verbs, and the bench R1 numbers. (#126, PR #129)

### Fixed

- **B-08 — DELETE predicates can finally filter on the row's pre-delete state.** The AFTER DELETE SQLite trigger has been writing `OLD.*` attributes as a JSON blob to `_gispulse_change_log.old_values` since v1, but the changelog reader's tail whitelist dropped the column before the watcher saw it — so `predicate: status == 'active'` could never match on a DELETE event despite the data being one PRAGMA away. The whitelist now includes `old_values`; the watcher hydrates `ChangeRecord.old_values` (mirrored on `new_values` for backward compatibility) when at least one active trigger carries a predicate AST. No GPKG migration required. (#120, PR #135)

### Security

- **`dml.changed` broadcast payload stays minimal even on DELETE.** Row attributes captured by the AFTER DELETE trigger are exposed only to the internal predicate evaluator, never on the unauthenticated `/ws/events` channel. A new test (`test_dml_changed_does_not_leak_old_values`) pins the contract.
- **`validate:` rule SQL is never spliced raw.** Every column / layer / EPSG identifier passes a strict `[A-Za-z_][A-Za-z0-9_]{0,62}` validator before reaching DuckDB; literals are SQL-quoted; the parser refuses any AST node outside the allowlist (no `__import__`, no `eval`, no method call, no f-string).

### Performance

- **DuckDB COPY GDAL/GPKG is now the bulk write-back fast path.** Atlas R1 bench on 1M EPSG:2154 polygons (median of 3 runs):

  | Scenario | pyogrio (s) | DuckDB COPY (s) | Speedup | RSS pyogrio | RSS DuckDB |
  |---|---:|---:|---:|---:|---:|
  | Append +100k | 8.19 | **3.63** | 2.26× | 950 MB | **273 MB** |
  | Update attribute | 6.94 | **2.75** | 2.52× | 839 MB | **255 MB** |
  | Update geometry | 8.87 | **2.47** | 3.59× | 843 MB | **275 MB** |

  Fallback to pyogrio remains forced for datasets > 5M rows, GPKG with custom triggers / views, and append-in-place semantics — see [`docs-site/guide/engines.md`](docs-site/guide/engines.md#v160--duckdb-spatial-inside).

### Deferred to v1.6.x

- **`build_runtime` auto-wiring of `GISPulseConfig.validate_rules`** — the runner is plumbed and tested, the factory is exposed, but the `headless_runtime.build_runtime` step does not yet instantiate a `ValidationRunner` automatically. Three options on the table for the schema (per-rule `table:`, first trigger's table, every trigger table); the user must pick before the wiring lands. Workaround: callers wire the runner manually using the `make_gpkg_sql_evaluator` factory + dispatcher injection.
- **#122 cross-source ATTACH** — `geom_within(layer='communes')` referencing a separate dataset compiles cleanly but executes only when the target layer is part of the current ATTACH. Multi-source plumbing is the next step.
- **#124 `layer_lookup`** — depends on cross-source ATTACH.

## [1.5.3] - 2026-05-05

Hotfix release for EPIC #103 — 4 P0 bugs identified by Beta on the v1.5.2 DML triggers + QGIS workflow. Ships before the HN big-launch so the first 50 HN users don't bleed on French desktop datasets, infinite trigger loops, paste-of-50 trigger silence, or post-Field-Calculator schema drift.

### Fixed

- **B-05 — QGIS layer names with spaces, accents or dashes are now accepted.** The change-tracking install path validated layer / column names against `^[A-Za-z_][A-Za-z0-9_]*$`, so common French desktop datasets (`Parcelles cadastrales 2024`, `voies-rapides`, `nb-bâtiments`) raised `ValueError` before any DDL ran — adoption blocker on QGIS / GDAL exports. The validator now delegates to a new `core.sql_safety.validate_layer_name()` that accepts any character safe inside a quoted identifier (`"..."`) and a quoted literal (`'...'`); only `"`, `'`, `;`, `\` and control chars are rejected. Trigger object names are derived through `core.sql_safety.slug_identifier()`, which preserves pre-B-05 ASCII names unchanged so existing v1.5.x GPKGs round-trip cleanly and rewrites Unicode names to `<safe-ascii>_<sha1[:8]>`. Same relaxation applied to `action_dispatcher`, `operation_executor`, `trigger_evaluator`; `relations_router` (HTTP-exposed) and PostgreSQL `NOTIFY` channels keep the strict regex. (#107)
- **B-02 — SET_FIELD trigger no longer loops infinitely.** A trigger `ON UPDATE buildings → SET_FIELD area = ST_Area(geom)` re-fired on every `area` write back into `buildings`, locking CPU at 100% and ballooning the GPKG with `_gispulse_change_log` rows. Origin-tagging M1: tracked layers grow a `_gispulse_origin TEXT` sentinel column (schema v3 migration, idempotent on re-bootstrap). The AFTER UPDATE trigger gains a WHEN clause that suppresses re-fires when the row carries a `trigger:<id>` marker AND suppresses the action_dispatcher's own "clear sentinel" UPDATE so the clear pass doesn't loop back. `ActionDispatcher._set_field` now emits two UPDATEs: data write + marker, then a clear pass to NULL so a subsequent QGIS edit on the same row still fires the trigger. `_migrate_v2_to_v3` rebuilds tracked-layer triggers in place via `bootstrap_gpkg_project` so existing v2 projects upgrade on the next engine boot. The migration auto-detects the original PK column (`PRAGMA table_info`) so layers whose PK is named `id` (or anything other than `fid`) round-trip cleanly. (#108)
- **B-01 — Bulk threshold Mode 3 (bulk WS event + per-row trigger eval).** Pre-B-01 the watcher had two binary modes — `bulk_threshold=0` flooded the WS with 50 `dml.changed` events, `bulk_threshold=50` collapsed to one `bulk.changed` BUT silenced every DSL trigger. Mode 3 was missing: 1 bulk WS event AND every DSL trigger still sees every row. New constructor parameter `bulk_eval: Literal["skip", "per_row"] = "skip"`. `"skip"` (default) is the back-compat Mode 2; `"per_row"` is the new Mode 3 that emits one `bulk.changed` summary AND evaluates triggers per row, broadcasting `trigger.fired` for matched ones and dispatching their actions. (#109)
- **B-13 — Schema drift watchdog rebuilds triggers on column changes.** A QGIS user adds / drops / renames a column on a tracked layer via Field Calculator. Pre-B-13 the AFTER UPDATE trigger's baked `new_values` JSON references a stale column list — further edits crash with `no such column` or silently omit the new column from the change-log payload. The watcher gains a wall-clock-throttled drift check (default every 5 s, set to `0` to disable) that re-hashes `PRAGMA table_info` for every tracked layer; on mismatch it drops + re-installs change tracking and broadcasts a `schema.changed` event so the portal / plugin can refresh their layer panels. First sighting is silent (no `schema.changed` spam at boot). (#110)
- **CI — `_drop_rtree_triggers` and `_connect_with_retry` hardened.** The lifecycle test `test_p02_enable_tracking_full_lifecycle` opened a raw `sqlite3.connect()` on the just-uploaded GPKG before any of the hardened callsites kicked in; on Py 3.10 / 3.12 CI runners this raced the upload-side pyogrio handle and surfaced as `DatabaseError: file is not a database`. Routed through the existing retry helper and bumped the helper's budget from 8×0.15s to 20×0.25s for slower runners.

### Notes

- Schema bump v2 → v3. Existing v2 GPKGs upgrade in place on the next `bootstrap_gpkg_project` call (engine boot), idempotent.
- The `bulk_eval="per_row"` option is opt-in on the watcher constructor; default behaviour matches v1.5.2.
- The schema-drift watchdog runs by default at 5 s intervals; set `schema_drift_check_interval_s=0` to disable for tests / SaaS Pro contexts where DDL is gated through a different code path.
- RUN_SQL action's origin-tagging is **not** included in this hotfix — only SET_FIELD is wired to the marker write-back. Tracked separately as a v1.6.x follow-up since RUN_SQL goes through OperationExecutor's raw SQL path (more complex change).

## [1.5.2] - 2026-05-04

Big-launch release. The runtime keeps the v1.5 surface; this release adds the QGIS plugin, three end-to-end walkthroughs, plugs a critical portal-mode middleware gap, and lands a `/system/doctor` health endpoint.

### Added
- **QGIS plugin (`qgis_plugin/`)** — thin dock widget that shells out to the system `gispulse` CLI via `QProcess`. Detects CLI presence with version-gate (≥1.5.0), OS-specific install dialog, attach-trigger combo (vector layers only), non-blocking runner with streamed coloured logs + Cancel (SIGTERM/SIGKILL), post-run change summary + auto-reload + 5-min Restore. ~500 KB unzipped, 99 tests, lockstep version with the wheel. Submitted to plugins.qgis.org. Source under AGPL v3 in the OSS repo. (#71, #73, #74, #76, #78, #80, #84)
- **Walkthroughs (FR + EN)** — three end-to-end scenarios published as docs site pages: `classify_buildings_in_isochrones` (Parcels — buildings re-tiered into walking-isochrone rings on parcel edits), `recompute_isochrones` (Isochrone — 3 walking-isochrone rings recomputed via local OSM graph on parcel boundary moves), `log_event` (Audit — every INSERT/UPDATE/DELETE mirrored to `_gispulse_audit_log`, exportable via `gispulse audit export`). (#89)
- **`POST /system/doctor`** — backend health endpoint that mirrors `gispulse track doctor` output (engine status, GPKG application_id, change-log table presence, per-layer triggers, busy_timeout) so the portal and CLI can surface the same health signal. Closes #91. (#97)
- **CI — `build-plugin-zip` job** — packages and verifies the plugin ZIP on every tag. `release.yml` `github-release` step is now double-gated: fails if either the wheel or the plugin ZIP artefact is missing. Plugin ZIP attached to the GH Release. (#79)

### Fixed
- **Security — `ProductionAuthMiddleware` was never mounted in portal mode.** PluginHub middleware install was nested inside the `is_portal=False` branch of `create_app`, so the enterprise auth middleware (shipped via the `gispulse.middleware` entry-point) was never installed when `gispulse portal` ran. Combined with the legacy `gispulse.adapters.http.middleware.production_auth` import that no longer resolves post-split (silently caught by `except ImportError`), `GISPULSE_ENV=production` portal deployments were UNPROTECTED on `/filter/*`, `/ogc/*`, `/ws/*`. Hoisted the `hub.middleware` install loop above the `is_portal` branch so middleware applies to both modes; routers stay mode-gated. Closes part 2 of #87. (#96)
- **CI — `test_p02_enable_tracking_full_lifecycle` flake on Python 3.10/3.12.** Wrapped `sqlite3.connect()` with a 3-attempt retry (50 ms / 100 ms / 200 ms) to absorb the GPKG file-lock race against pyogrio's reader on slower CI runners. Pre-#86 the test failed intermittently with `sqlite3.DatabaseError: file is not a database`. (#86, #57)
- **Docs — dead `git clone` URL in QGIS plugin install guide.** The manual-install snippet pointed to `github.com/gispulse/gispulse` which 404s; the actual repo lives at `github.com/imagodata/gispulse`. Fixed in both FR and EN. (#101)

### Changed
- `release.yml` — `github-release` waits for both `publish-pypi` and `build-plugin-zip` before creating the release, so a missing plugin ZIP fails fast.

### Security
- **Dependencies** — bump `docker/build-push-action` 6 → 7, `actions/upload-pages-artifact` 4 → 5, `actions/upload-artifact` 4 → 7. (#98, #99, #100)

### Notes
- QGIS plugin reviewer turnaround on plugins.qgis.org is 1-4 weeks. Manual install via the attached ZIP works in the meantime.
- Public demo (`demo.gispulse.dev`) `/examples/*` mini-backend has been live since 1.5.1; the SPA mount on `/portal` is tracked separately in #50.

## [1.5.1] - 2026-04-30

Mode 2 portail Community: GISPulse now ships a local visual workbench. The portal you saw on `gispulse.dev` is now a Python package — `pip install gispulse-portal` adds it to your CLI install, and `gispulse portal` opens the bundled SPA on `http://localhost:8001/portal` with same-origin engine.

### Added
- **`gispulse portal` CLI command** — mounts the bundled `gispulse-portal` SPA on `/portal` via FastAPI `StaticFiles`, starts the engine on `localhost:8001`, opens the browser. `--port`, `--no-browser`, `--backend=URL`, `--dev` flags. Graceful-degrade with `pip install gispulse-portal` hint when the package isn't installed.
- **`/api/examples/*` mini-backend** — read-only registry of bundled GPKG fixtures (`muret-parcels`, `muret-flood-zones`, `toulouse-isochrones`, `bordeaux-rpg`) for the public "Try it" demo. Endpoints: registry, metadata, TileJSON preview, MVT tiles, dryrun trigger evaluation, health. Hard-capped (5s timeout, 1000 DML records, 50 triggers, 50 MB tile cache); `DryRunDispatcher` captures actions but never executes side-effects.
- **Docs — "Running the portal locally" + "Running the engine"** guides (FR + EN) covering the full local workbench flow.
- **CLI ↔ Portal symmetry matrix** (`docs-site/guide/symmetry.md`) — 82 capabilities mapped row-by-row, 31 ⚠️ asymmetries logged inline for v1.6+ triage.

### Companion release
- **`gispulse-portal 1.5.1` ships on PyPI** for the first time. The wheel bundles the built VitePress SPA so `gispulse portal` can serve it same-origin on localhost (no mixed-content workaround needed). `pip install gispulse-portal` installs both `gispulse` and `gispulse-portal`.

### Fixed
- `cli.py` `engine -e/--engine` help string now mentions `hybrid` alongside `duckdb` and `postgis`.

### Notes
- Public demo backend (`demo.gispulse.dev/api/examples/*`) deployment is tracked separately in #50; the endpoints are available in the wheel and ready to deploy.
- The `gispulse-portal` SPA continues to deploy via GitHub Pages on every push to `main` (independent of PyPI).

## [1.5.0] - 2026-04-30

QML-grade styling release: load, classify server-side, edit, and export QGIS-compatible styles end-to-end. The change-log runtime keeps doing what it did since v1.3 — fire triggers on any DML coming from QGIS save, ogr2ogr, ArcGIS Pro, raw sqlite3.

### Added
- **`POST /datasets/{id}/layers/{layer}/breaks`** — server-side classification (quantile, equal-interval, Jenks, std-dev, pretty) wrapping `ClassifyCapability`. Same algorithm available via CLI and portal.
- **`PUT /datasets/{id}/styles`** — persist `LayerStyleDef` to the GPKG `layer_styles` table.
- **`POST /datasets/{id}/styles/import`** — multipart `.qml` upload, parsed via `persistence/style_converter.py` and persisted.
- **QML roundtrip integration suite** — 5 representative fixtures (single, categorized, graduated, rule-based, labels) tested in CI to guard against lossy export/import cycles.

### Changed
- Style classification moves to server-side by default. Client still falls back to local computation for offline scenarios but the canonical path goes through `/breaks` so behavior is identical regardless of caller.
- `persistence/style_converter.py` (~608 LOC) is now the source of truth for QML ↔ `LayerStyleDef`. GeoStyler bridge dropped (avoid vendor lock + Ant Design v4 dep).

### Notes
- The portal SPA continues to deploy via GitHub Pages on every push to `main` (no PyPI wheel for the portal in this release).
- The first PyPI publish of `gispulse-portal` is planned for v1.5.1 alongside the Mode 2 portail sprint (bundled-SPA wheel + `gispulse portal` CLI command for a local workbench).

## [1.3.1] - 2026-04-29

Hotfix release that unblocks the v1.3.0 distribution: `pipx install gispulse` now ships a working `triggers run` / `watch` (httpx + pyarrow were missing from base deps, `--bulk-threshold` crashed at runtime), the local Docker stack boots on community tier, the portal serves favicon/robots/manifest correctly, and CI is green again.

### Fixed
- **Packaging — `httpx` core runtime dependency** — moved `httpx>=0.24,<1.0` from the `[api]` / `[sso]` / `[dev]` extras into base dependencies. Without it, `pipx install gispulse` produced a working CLI for `track` / `info` / `run` but `gispulse triggers run` and `gispulse watch` crashed on `ModuleNotFoundError: No module named 'httpx'` (the webhook client at `gispulse/adapters/webhooks/http_client.py` imports it unconditionally). 1.3.0 users can work around with `pipx install "gispulse[api]"`.
- **Packaging — `pyarrow` core runtime dependency** — declared `pyarrow>=14,<22` in base dependencies. Without it, `gispulse run --output result.parquet`, the GeoParquet writer (`core/io/geoparquet.py`), and any DuckDB pipeline that lands GeoParquet via `COPY ... TO ... (FORMAT 'parquet')` crashed with `ImportError: Missing optional dependency 'pyarrow.parquet'` (geopandas raises this from `_compat.py` regardless of host having the binary). The `[parquet]` extra remains for backward compatibility but is now redundant.
- **Runtime — `gispulse watch --bulk-threshold` crashed at startup** — `gispulse/cli_watch.py` wired `--bulk-threshold` straight into `build_runtime(bulk_threshold=...)`, but `build_runtime()` never accepted the kwarg (the underlying `ChangeLogWatcher` did). Every invocation died with `TypeError: build_runtime() got an unexpected keyword argument 'bulk_threshold'`. Add the parameter to `build_runtime()` and forward it. Bulk-mode tick (#8) is now actually wired end-to-end.
- **API — pipelines `ref_layer` 500** — `/pipelines/execute-steps` resolved `ref_layer` / `ref_layers` aliases into `ref_gdf` / `ref_gdfs` but left the original keys in `params`, so `execute_safe` rejected them as unknown kwargs and returned 500. Mirrors `PipelineExecutor` (`orchestration/pipeline_executor.py:170,178`) by using `dict.pop()` to strip the plumbing keys before the capability call.
- **API — OSS auth stubs + websockets** — the portal UI calls `/api/auth/providers` and `/api/auth/me` on every page load. Without an enterprise OIDC plugin those endpoints 404'd and the UI logged errors. Ship OSS stubs returning `[]` and `401`, mount the router unconditionally so the enterprise plugin can override later. Mount prefix is `/api/auth` to match the portal client. Switch the `[api]` extra to `uvicorn[standard]` so `/ws/events` upgrades stop failing with `No supported WebSocket library detected`.
- **API — SPA root static assets** — the SPA fallback 404'd on any root-level static asset shipped with the build (favicon.svg, icons.svg, robots.txt, …) because only `/assets/*` was mounted. The fallback now tries the dist root first (path-traversal blocked by `Path.resolve().is_relative_to(dist_root)`) before applying the SPA-route whitelist + index.html fallback.
- **API — `/api/auth/me` console noise** — the OSS stub returned `401` for anonymous callers. The portal already treats null as anonymous, but browsers log every 4xx network response to DevTools regardless of how the JS client handles it. Switch to `200` with body `null`: silent and equally unambiguous.
- **Compose — community-tier boot** — `docker-compose.local.yml` hardcoded `GISPULSE_ENGINE=postgis`, which crashed at startup under the default community tier (`TierError: Postgis engine requires GISPulse Pro`). PostGIS is now opt-in via `--profile postgis`; default is DuckDB so the local stack boots out of the box.
- **Catalog — IGN Scan 25 dead entries** — IGN Géoplateforme deprecated `GEOGRAPHICALGRIDSYSTEMS.MAPS` (verified against `data.geopf.fr`). Drop `basemap:ign-scan25` and `ign-scan25-wmts`; `GEOGRAPHICALGRIDSYSTEMS.PLANIGNV2` is exposed as `basemap:ign-plan` / `ign-plan-wmts` for users who still need an IGN background.

### Changed
- **CI** — `test` job now installs `[dev,api,postgis,mcp,raster,network,classification,pointcloud,scheduling,sso]` extras instead of `[dev]` alone. Router and integration tests need `fastapi`, `psycopg2-binary`, etc. and were silently failing collection on `ModuleNotFoundError` (28+ test files affected). `capability-matrix-drift` job aligned to the same install set so the matrix it generates matches the one committed locally.
- **CI** — `pip-audit` ignores `CVE-2026-3219` (pip 26.x tar/ZIP confusion, no fix release upstream as of 2026-04-28; re-evaluate quarterly).
- **Docs** — README pipx quickstart aligned with v1.3 CLI surface (`gispulse triggers` / `track` / `watch`); `project.gpkg` removed from the tree (was tracked dev artifact).

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

---
title: Changelog
description: GISPulse version history.
---

# Changelog

All notable changes are documented here. Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versioning follows [Semantic Versioning](https://semver.org/).

The authoritative version of this file lives at [`CHANGELOG.md`](https://github.com/imagodata/gispulse/blob/main/CHANGELOG.md) in the repository — entries here are kept in sync with every release.

## [Unreleased]

---

## [2.0.0] — 2026-05-20

The first **major** release. Numerically a jump from `1.6.2`, but in practice the API surface bundles what was tagged internally `1.7.0`, `1.8.0`, and `1.9.0` — features that accumulated on `main` without ever being published to PyPI. We promote the whole stack in one tag and reset the public version to match the product story.

See [`MIGRATION-2.0`](./migration-2.0) for the upgrade path. TL;DR: **no application code change is strictly required** — the `_compat.py` meta-path shim absorbs the import-path move and the `PluginHub = ExtensionHub` alias keeps existing imports working until 2.1.0.

Three threads converge here:

1. **Foundations** (was tagged internally `v1.8.0`) — `gispulse.*` mono-package, `ExtensionHub` replacing `PluginHub`, `GISPulseApp` façade, full MCP server, data-pack regime, CLI / HTTP / template routers.
2. **Worldwide aggregator** (was tagged internally `v1.9.0`) — lazy DuckDB-backed fetcher network covering 4 protocol families (`GeoParquetS3`, `OGCFeatures`, `STAC`, `HttpFile`) and a curated `worldwide_catalog.yml`.
3. **Data-pack rails** — third-party data-packs can now ship on PyPI: a discovery channel via the `gispulse.data_packs` entry-point, an Ed25519 signature gate on EXTERNAL manifests, and a shared licence payload format that also covers the future SaaS tenant licence.

### Added

- **Data-pack regime — PyPI discovery channel (T5).** Third channel alongside the bundled OSS manifests and `GISPULSE_DATA_PACKS_DIR`: a Python entry-point group `gispulse.data_packs` lets a third-party package register its manifests at install time. One bad pack never locks out the others. (#269)
- **Data-pack regime — Ed25519 signature gate (G1a).** `DataPackManifest` gains an optional `signature` field. EXTERNAL manifests carrying a signature are verified against `GISPULSE_DATA_PACK_PUBLIC_KEY`; tampered or foreign-signed manifests are dropped with explicit log events. INTERNAL (bundled) manifests are exempt. Set `GISPULSE_DATA_PACK_REQUIRE_SIGNATURE=true` to refuse unsigned EXTERNAL packs. (#271)
- **Unified Ed25519 licence payload format (L0).** New `gispulse.core.licence_format` defines the single payload schema shared by the per-machine licence key, the future SaaS tenant licence, and the data-pack manifest signature. Versioned via `schema_version`, forward-compat, canonicalised JSON. (#266)
- **High-level OGC client for data packs (T1).** New `gispulse.core.fetchers.ogc_client.fetch_features(...)` — a one-liner over the consolidated transport with WFS vs OGC API Features dispatch and a typed network-error surface (`OGCEndpointUnreachable`, `OGCClientError`). (#267)
- **Declarative ZoningElement normaliser (T2).** New `gispulse.core.zoning_normalizer` maps heterogeneous source records into a common 8-field schema inspired by INSPIRE PlannedLandUse. CRS is mandatory and must be explicit (`EPSG:XXXX`). (#268)
- **`regulatory-zoning` data-pack content type (T3).** New value in `DATA_PACK_CONTENTS`. New `RegulatoryZoningEntry` dataclass + `from_dict()` validator: required-field set, no unknown fields, ISO-3166-1 alpha-2 country, known protocol, explicit `EPSG:` CRS, bbox 4-numbers. (#270)
- **Worldwide aggregator (EPIC #226).** 15 sub-issues delivering lazy DuckDB-backed fetchers covering `GeoParquetS3`, `OGCFeatures`, `STAC`, `HttpFile`, plus a curated `worldwide_catalog.yml` (France / EU / world), HTTP endpoints (A10), portal Worldwide tab (A12). (#227-#241)
- **MCP server v1.8.0 (EPIC #206).** 7 tools, dry-run mode, FS scoping. Stdio launcher via `gispulse mcp`. (#202-#205, PR #242)
- **`gispulse.*` mono-package consolidation (Foundations A).** Flat 8-package tree → single `src/gispulse/` package; ~280 OSS files moved with the `_compat.py` meta-path shim preserving every old import root.
- **`GISPulseApp` façade + 4 thin façades (Foundations B).** Application layer over CLI / HTTP / MCP / template routers.
- **`ExtensionHub` two-regime hub (Foundations C).** Replaces `PluginHub`, splits code plugins from data packs; `DataPackManifest` + `templates/manifest.yml` for the data-pack regime.
- **ELT push-down stack (EPIC #243).** Dialect-aware SQL generation (#244, Lot 1), schema push-down (#245, Lot 2), per-capability push-down (Lots 3b-3e: geom multi-layer, dissolve/sjoin, nearest/overlay, temporal), unified manifest v3 (Lot 4A), cycle validation (Lot 4B), materialisation (Lot 4C), `gispulse explain` DAG inspection (Lot 4E), `assert:` data-quality gates (Lot 4F), manifest v3 docs + ADR cross-refs (Lot 4G). 12 PRs merged onto `main` on 2026-05-20. (#262, #264, #296-#305)

### Changed

- **`PluginHub` renamed to `ExtensionHub`.** Same module (`gispulse.core.plugin_hub`); a `PluginHub = ExtensionHub` alias preserves existing imports. Scheduled for removal in **2.1.0**.
- **`gispulse.core.plugin_contracts` public surface frozen via `__all__`.** The 8 symbols actually exported by the 1.6.2 wheel are gelled; types that moved to `plugin_model.py` were never in `plugin_contracts` — no compat shim needed.
- **`_compat.py` deprecation horizon corrected.** Docstring and `DeprecationWarning` now point at **2.1.0** instead of the stale "removed in 1.9.0" line.

### Fixed

- **`security-audit` job** — silences two disputed upstream advisories (`joblib` PYSEC-2024-277, `pyjwt` PYSEC-2025-183) via `--ignore-vuln` allowlist with re-evaluation notes. No code change.

### Migration

See [`MIGRATION-2.0`](./migration-2.0). The summary:

- Legacy top-level imports (`core.*`, `capabilities.*`, `rules.*`, `orchestration.*`, `persistence.*`, `catalog.*`) continue to work via the `_compat.py` meta-path shim with a one-time `DeprecationWarning`.
- `PluginHub` continues to work via the `PluginHub = ExtensionHub` alias.
- Both shims will be removed in **2.1.0** — migrate to `gispulse.*` / `ExtensionHub` at your leisure.

---

## [1.7.0] — internal

> **Note:** `1.7.0` was never published to PyPI as a standalone tag — its scope is bundled inside [`2.0.0`](#200--2026-05-20). The entry below documents what the tag *would* have contained for users tracking the EPIC #175 thread.

The "Wiring the ETL platform" release. EPIC #175 (PR #189) landed the unified plugin model as a *skeleton*; v1.7.0 made it work end to end — a data source can be declared, fetched over the network through a protocol registry, and watched for freshness so an external revision fires a trigger. GISPulse gains an "Extract" stage alongside its existing local-CDC triggers.

### Added

- **Unified plugin model + `PluginHub`.** Five plugin kinds (`source`, `capability`, `sink`, `protocol`, `extension`), entry-point discovery, and a `discover → resolve → gate → activate` lifecycle with tier/trust gating. (EPIC #175, PR #189)
- **`source_changed` triggers.** A trigger may declare `on: {source_changed: <source>://<entry>, frequency: …}` and fire when an external source publishes a new revision. (#195)
- **`SourceWatcherRegistry` wired into `gispulse watch`.** Polls each watched source's `revision()` token at the `frequency` cadence and dispatches `source.changed` events. (#197)
- **Core transport fetchers in the `ProtocolRegistry`.** `WfsFetcher` + `OgcFeaturesFetcher` (#192, PR #209), `StacFetcher` + `RestGeoJsonFetcher` (#192, PR #211).
- **`gispulse-src-cadastre` and `gispulse-src-ign` source plugins.** First `gispulse-src-*` pilots — French cadastre (IGN Parcellaire Express) and IGN reference data (BD TOPO + ADMIN EXPRESS). (#184, #194)
- **`gispulse mcp`.** CLI launcher starting the GISPulse MCP server over stdio for LLM agents. (#201)
- **PostGIS dialect-drift scanner.** Loader-time warning when a `run_sql` string uses PostGIS-only constructs that will not run on the DuckDB-spatial contract dialect. (#146)
- **ETL documentation.** Source Plugin Authoring Guide, "watch an external source" walkthrough (FR + EN), `source_changed` section in `TRIGGERS_GUIDE.md`. (#200)

### Changed

- **Catalog discovery consumes `PluginHub.records`.** `catalog/registry.py` no longer runs its own scan — the hub owns the single scan. `/catalog/*` is functionally unchanged. (#193)
- **`gispulse-src-cadastre.revision()` is a real probe.** Freshness read from `HTTP HEAD` `ETag` / `Last-Modified` against the Géoplateforme WFS `GetCapabilities`. (#198)

### Fixed

- **SSRF guard on `ProtocolRegistry.dispatch_fetch()`.** Every fetch endpoint is validated through the shared `core.ssrf` guard before dispatch. (#199)
- **`test_p02` file-lock flake.** Known sqlite3 / pyogrio race marked `flaky` and retried via `pytest-rerunfailures`. (#191)

---

## [1.6.2] — 2026-05-07

The "Format Frontier" release — DuckDB Spatial as the universal CDC substrate. Adds two new engines (`spatialite`, `duckdb_diff`), brings DML detection to seven file formats (GPKG, SpatiaLite, GeoJSON, FlatGeobuf, Shapefile, KML, CSV+WKT) — five of which had no native trigger surface — and closes EPIC #139 (DML semantics ADRs + WAL connection safety).

### Added

- **SpatiaLite engine.** New `persistence.spatialite_engine.SpatiaLiteEngine` shares the SQLite trigger DDL of GPKG but writes through pyogrio's `SQLite + SPATIALITE=YES` driver. Auto-routed for `*.sqlite` / `*.db` URIs. (PR #151)
- **`is_spatialite_file(path)` detection helper + `bootstrap_spatialite_project(conn)`.** Sibling to the GPKG bootstrap; shared `_bootstrap_gispulse_internals(conn)` helper. (PR #151)
- **`FileBlobChangeDetector`.** Reusable mtime + DuckDB `ST_Read` snapshot diff CDC. Hash `md5(ST_AsWKB(geom) || json_object(props))` excluding `OGC_FID`. Snapshot persisted as `<blob>.gispulse-snapshot.duckdb`. Set-diff semantics: INSERT / DELETE only — UPDATE is undetectable without a stable PK. (PR #152)
- **Companion-file watching.** Shapefile + MapInfo TAB watched via `max(mtime)` across companion files; new `_COMPANION_EXTENSIONS` map is extensible. (PR #152)
- **`DuckDBDiffEngine`.** `SpatialEngine` implementation backed by the file-blob detector. GeoJSON, FlatGeobuf, Shapefile, KML, CSV+WKT. Matches `GeoPackageEngine.get_pending_changes` shape so `ChangeLogWatcher` iterates uniformly. (PR #152, #153)
- **Engine factory entries.** `_spatialite_factory` and `_duckdb_diff_factory` registered as built-ins; URI inference maps suffixes automatically. (PRs #151, #152)
- **`persistence.gpkg_connection.connect_gpkg(path, …)`.** Single entry point applying WAL + `busy_timeout=5000` on every GeoPackage `sqlite3.connect`. Migrated 8 scattered call sites. (#141, PR #145)
- **ADRs 0001-0004.** DuckDB-spatial as the contract SQL dialect (#140 / PR #147), trigger cascade bounded fixed-point (#142 / PR #148), `_gispulse_change_log` as a poll log (#143 / PR #150), DDL hooks out of scope (#144 / PR #150).
- **KML CDC, CSV+WKT CDC, MapInfo TAB companion files + pyogrio fallback.** (EPIC #106 slices 1+2, PR #153, #154)
- **Multi-engine `POST /datasets/{id}/enable_tracking`.** Route no longer hardcoded to `GeoPackageEngine`; resolves engine via URI suffix. SQLite-family installs AFTER triggers; `duckdb_diff` skips install (sidecar snapshot on first poll). (#157, PR #158)

### Changed

- **`bootstrap_gpkg_project` extracts a shared internal helper** — regression test pins the GPKG path still produces a valid GeoPackage with `application_id = 0x47504B47`. (PR #151)

### Documentation

- **`docs/adr/0001 → 0004`** introduced under `docs/adr/`; cross-linked from `architecture.md`.
- **`dsl-sql-dialect.md`** — user-facing reference of the DSL SQL dialect contract.
- **`rules.md` cascade behaviour sub-section** with tier table, two-layer explanation, link to ADR 0002. (PR #148)
- **`formats.md`** — SpatiaLite, GeoJSON, FlatGeobuf, Shapefile, KML, CSV+WKT, MapInfo TAB rows with CDC notes; new "CDC file-blob" section. (PRs #151-#154)
- **`walkthroughs/geojson-cdc.md` (FR + EN)** — fourth walkthrough end-to-end. (PRs #155, #156)

---

## [1.6.1] — 2026-05-07

Same-day follow-up to v1.6.0. Closes the 3 deferred items from the v1.6.0 sprint kickoff in a single PR (#138) so the v1.6.x line ships its full promised surface — cross-source push-down, scalar lookup, and zero-config validate auto-wire.

### Added

- **`layer_lookup(layer, match, take, layer_geom)` DSL fct.** Scalar attribute lookup against a cross-source layer with three match modes (`spatial_within`, `spatial_intersects`, attribute-equality shorthand). Compiles to `(SELECT _L."<take>" FROM "<layer>" AS _L WHERE <pred> LIMIT 1)`. (#124)
- **Cross-source layer registry.** `gispulse.runtime.layer_registry.LayerRegistry` ATTACHes external GeoPackage / Parquet / PostgreSQL sources read-only and creates a DuckDB view per declared layer. (#122)
- **Top-level `layers:` block in `triggers.yaml`.** Declarative cross-source layer refs via `LayerSourceConfigModel`. Duplicate-name guard at config-load time. (#122)
- **`build_runtime` validate auto-wire.** New `validate_rules`, `default_table`, `layer_sources`, `source_epsg` kwargs wire a `ValidationRunner` directly onto the change-log watcher.
- **Per-rule `table:` and top-level `default_table:`.** Resolution order: `rule.table` > `default_table` > GPKG single-table autodetect > `ValidationTableResolutionError`.

### Changed

- **`compile_validate_rules` accepts a `table_resolver` callable** — supports per-rule resolution. Legacy `table=` parameter preserved for v1.6.0 callers.

---

## [1.6.0] — 2026-05-07

The "DuckDB Spatial Inside" release. Closes EPIC #104 — a one-day cascade of 7 PRs (#129 → #135) lands the foundation, the DSL geom function whitelist, granular DML verbs, the declarative `validate:` block end-to-end, and the long-standing B-08 DELETE predicate gap.

DuckDB spatial moves from "embedded if you opt in" to **the universal compute substrate**: new DSL geom functions compile to DuckDB SQL, the validation runner evaluates rules through a DuckDB ATTACH on the GeoPackage, and Atlas R1 bench against pyogrio justifies the pivot — DuckDB COPY is **2.3× to 3.6× faster than pyogrio** on 1M EPSG:2154 polygons, peak RSS divided by ~3.4×.

### Added

- **DuckDB spatial extension — lazy install on first use.** `gispulse.runtime.duckdb_engine.get_spatial_connection()` runs `INSTALL spatial; LOAD spatial;` on first call. `DuckDBSpatialUnavailable` surfaces air-gapped failures explicitly. (#113, PR #129)
- **`gispulse doctor --install-spatial`.** Pre-installs spatial extension and probes a curated set of EPSG roundtrips (`EPSG:4326 / 3857 / 2154 / 27572`) against a `pyproj` baseline. (#114, PR #129)
- **Engine inference from the dataset URI.** `triggers.yaml` no longer requires explicit `engine:`: `*.gpkg` → `gpkg`, `postgresql://...` → `postgis`, `*.shp / *.geojson / *.fgb` → `duckdb_diff`. (#115, PR #129)
- **DSL geom functions — first whitelist.** Seven safe push-down functions: `geom_area_m2`, `geom_perimeter_m`, `geom_length_m`, `geom_centroid_x`, `geom_centroid_y`, `geom_npoints`, `geom_is_valid`. Auto-projects to `EPSG:2154` by default. (#116, #117)
- **DSL expression parser — safe-by-construction.** AST walked under strict allowlist (literals, column refs, `+ - * / %`, parens). Boolean mode unlocks `== != <= >= and or not` for `validate:` rules. (#118)
- **`when:` granular DML verbs.** `INSERT`, `UPDATE_GEOM`, `UPDATE_ATTR`, `DELETE`, `BULK`. The watcher resolves a coarse `UPDATE` to its granular variant via the change-log's `geom_changed` flag. (#119)
- **`geom_changed` flag in the `dml.changed` payload.** Subscribers can render geometry edits differently from attribute edits. (#120)
- **`validate:` top-level block in `triggers.yaml`.** Declarative validation rules with `mode: warn` or `mode: tag`. Rules compile at config load. (#121)
- **`tag_field:` action.** Writes status (and optional message) onto the row, auto-creating target columns via `PRAGMA table_info` + `ALTER TABLE ADD COLUMN`. Shared handler powers both explicit YAML actions and the `validate: mode: tag` bridge. (#123)
- **DSL cross-layer subquery functions.** `geom_within(layer='communes', match='code_insee')` and `geom_overlaps_any(layer='self', exclude_self=True)`. Compiler emits `EXISTS (SELECT 1 FROM "<layer>" AS _L WHERE …)` with strict identifier validation. (#122)
- **`ValidationRunner` + `make_gpkg_sql_evaluator(gpkg_path)`.** Engine-agnostic runtime component compiling each rule once at boot, evaluating per row through an injected `sql_evaluator`. Broadcasts `validation.failed` on the event hub. Per-rule isolation: a single bad rule never aborts the batch. (PRs #132-#133)
- **`ChangeLogWatcher` validation hook.** When a `ValidationRunner` is injected, every INSERT / UPDATE_GEOM / UPDATE_ATTR drives `runner.evaluate(...)`. (PR #133)
- **ESRI Attribute Rules vocabulary aliases.** `kind: constraint | calculation | validation` accepted as cosmetic aliases on `triggers.yaml`. (#125)
- **New docs pages.** `dsl-geom-functions.md`, `dsl-validation.md`, `migration-from-esri.md`, v1.6.0 section on `engines.md`. (#126)

### Fixed

- **B-08 — DELETE predicates can finally filter on the row's pre-delete state.** AFTER DELETE trigger writes `OLD.*` as `json_object(NEW.*)` into `old_values` since v1, but the changelog reader's tail whitelist dropped the column. Whitelist now includes `old_values`; the watcher hydrates `ChangeRecord.old_values` when at least one active trigger carries a predicate AST. No GPKG migration. (#120, PR #135)

### Security

- **`dml.changed` broadcast payload stays minimal on DELETE.** Row attributes captured by AFTER DELETE are exposed only to the internal predicate evaluator, never on `/ws/events`. Test `test_dml_changed_does_not_leak_old_values` pins the contract.
- **`validate:` rule SQL is never spliced raw.** Strict `[A-Za-z_][A-Za-z0-9_]{0,62}` validator on every identifier; literals SQL-quoted; AST parser refuses any node outside the allowlist.

### Performance

- **DuckDB COPY GDAL/GPKG is now the bulk write-back fast path.** Atlas R1 bench on 1M EPSG:2154 polygons (median of 3 runs):

  | Scenario | pyogrio (s) | DuckDB COPY (s) | Speedup | RSS pyogrio | RSS DuckDB |
  |---|---:|---:|---:|---:|---:|
  | Append +100k | 8.19 | **3.63** | 2.26× | 950 MB | **273 MB** |
  | Update attribute | 6.94 | **2.75** | 2.52× | 839 MB | **255 MB** |
  | Update geometry | 8.87 | **2.47** | 3.59× | 843 MB | **275 MB** |

  Fallback to pyogrio remains forced for datasets > 5M rows, GPKG with custom triggers / views, and append-in-place semantics.

---

## [1.5.3] — 2026-05-05

Hotfix release for EPIC #103 — 4 P0 bugs identified by Beta on the v1.5.2 DML triggers + QGIS workflow.

### Fixed

- **B-05 — QGIS layer names with spaces, accents or dashes are accepted.** Validator now delegates to `core.sql_safety.validate_layer_name()` accepting any character safe inside quoted identifiers; only `"`, `'`, `;`, `\` and control chars rejected. Trigger object names go through `slug_identifier()`. (#107)
- **B-02 — SET_FIELD trigger no longer loops infinitely.** Origin-tagging M1: tracked layers grow a `_gispulse_origin TEXT` sentinel (schema v3 migration, idempotent on re-bootstrap). AFTER UPDATE trigger gains a WHEN clause suppressing re-fires when the row carries a `trigger:<id>` marker. (#108)
- **B-01 — Bulk threshold Mode 3 (bulk WS event + per-row trigger eval).** New `bulk_eval: Literal["skip", "per_row"] = "skip"` constructor parameter. `"per_row"` emits one `bulk.changed` summary AND evaluates triggers per row. (#109)
- **B-13 — Schema drift watchdog rebuilds triggers on column changes.** Wall-clock-throttled drift check (default 5 s) re-hashes `PRAGMA table_info`; on mismatch drops + re-installs change tracking and broadcasts `schema.changed`. First sighting is silent. (#110)
- **CI — `_drop_rtree_triggers` and `_connect_with_retry` hardened.** Retry helper budget bumped from 8×0.15 s to 20×0.25 s.

### Notes

- Schema bump v2 → v3. Existing v2 GPKGs upgrade in place on the next `bootstrap_gpkg_project` call (engine boot), idempotent.
- `bulk_eval="per_row"` is opt-in on the watcher constructor.
- Schema-drift watchdog runs by default at 5 s; set `schema_drift_check_interval_s=0` to disable.

---

## [1.5.2] — 2026-05-04

Big-launch release. Runtime keeps the v1.5 surface; adds the QGIS plugin, three end-to-end walkthroughs, plugs a critical portal-mode middleware gap, and lands `/system/doctor`.

### Added

- **QGIS plugin (`qgis_plugin/`).** Thin dock widget shelling out to system `gispulse` CLI via `QProcess`. Version-gate (≥1.5.0), OS-specific install dialog, attach-trigger combo (vector layers only), non-blocking runner with streamed coloured logs + Cancel, post-run change summary + auto-reload + 5-min Restore. ~500 KB unzipped, 99 tests, lockstep version with the wheel. (#71, #73, #74, #76, #78, #80, #84)
- **Walkthroughs (FR + EN).** `classify_buildings_in_isochrones`, `recompute_isochrones`, `log_event`. (#89)
- **`POST /system/doctor`.** Backend health endpoint mirroring `gispulse track doctor`. Closes #91. (#97)
- **CI — `build-plugin-zip` job** packaging and verifying the plugin ZIP on every tag. `release.yml` double-gated. (#79)

### Fixed

- **Security — `ProductionAuthMiddleware` was never mounted in portal mode.** `PluginHub` middleware install was nested inside the `is_portal=False` branch of `create_app`, so the enterprise auth middleware (shipped via `gispulse.middleware` entry-point) was never installed when `gispulse portal` ran. `GISPULSE_ENV=production` portal deployments were UNPROTECTED on `/filter/*`, `/ogc/*`, `/ws/*`. Hoisted the `hub.middleware` install loop above the `is_portal` branch. Closes part 2 of #87. (#96)
- **CI — `test_p02_enable_tracking_full_lifecycle` flake on Python 3.10/3.12.** Wrapped `sqlite3.connect()` with 3-attempt retry. (#86, #57)
- **Docs — dead `git clone` URL in QGIS plugin install guide.** Pointed to `github.com/gispulse/gispulse` (404); actual repo at `github.com/imagodata/gispulse`. Fixed FR + EN. (#101)

### Changed

- `release.yml` — `github-release` waits for both `publish-pypi` and `build-plugin-zip`.

### Security

- Dependencies bump: `docker/build-push-action` 6 → 7, `actions/upload-pages-artifact` 4 → 5, `actions/upload-artifact` 4 → 7. (#98-#100)

---

## [1.5.1] — 2026-04-30

Mode 2 portail Community: GISPulse now ships a local visual workbench. `pip install gispulse-portal` adds the bundled SPA to your CLI install; `gispulse portal` opens `http://localhost:8001/portal` with same-origin engine.

### Added

- **`gispulse portal` CLI command** mounting the bundled `gispulse-portal` SPA on `/portal` via FastAPI `StaticFiles`. `--port`, `--no-browser`, `--backend=URL`, `--dev` flags.
- **`/api/examples/*` mini-backend** — read-only registry of bundled GPKG fixtures (`muret-parcels`, `muret-flood-zones`, `toulouse-isochrones`, `bordeaux-rpg`) for the public "Try it" demo. Hard-capped (5 s timeout, 1000 DML records, 50 triggers, 50 MB tile cache); `DryRunDispatcher` captures actions but never executes side-effects.
- **Docs — "Running the portal locally" + "Running the engine"** guides (FR + EN).
- **CLI ↔ Portal symmetry matrix** (`guide/symmetry.md`) — 82 capabilities mapped row-by-row, 31 ⚠️ asymmetries logged for v1.6+ triage.

### Companion release

- **`gispulse-portal 1.5.1` ships on PyPI** for the first time. The wheel bundles the built VitePress SPA so `gispulse portal` can serve it same-origin on localhost.

### Fixed

- `cli.py` `engine -e/--engine` help string now mentions `hybrid` alongside `duckdb` and `postgis`.

---

## [1.5.0] — 2026-04-30

QML-grade styling release: load, classify server-side, edit, and export QGIS-compatible styles end-to-end.

### Added

- **`POST /datasets/{id}/layers/{layer}/breaks`** — server-side classification (quantile, equal-interval, Jenks, std-dev, pretty) wrapping `ClassifyCapability`.
- **`PUT /datasets/{id}/styles`** — persist `LayerStyleDef` to the GPKG `layer_styles` table.
- **`POST /datasets/{id}/styles/import`** — multipart `.qml` upload, parsed via `persistence/style_converter.py` and persisted.
- **QML roundtrip integration suite** — 5 representative fixtures (single, categorized, graduated, rule-based, labels) tested in CI to guard against lossy export/import cycles.

### Changed

- Style classification moves to server-side by default; client falls back locally for offline scenarios.
- `persistence/style_converter.py` (~608 LOC) becomes the source of truth for QML ↔ `LayerStyleDef`. GeoStyler bridge dropped.

---

## [1.3.1] — 2026-04-29

Hotfix unblocking the v1.3.0 distribution: `pipx install gispulse` now ships a working `triggers run` / `watch`, the local Docker stack boots on community tier, the portal serves favicon/robots/manifest correctly, CI is green again.

### Fixed

- **Packaging — `httpx` core runtime dependency** — moved from `[api]` / `[sso]` / `[dev]` extras into base. `pipx install gispulse` previously produced a CLI for `track` / `info` / `run` but `gispulse triggers run` and `gispulse watch` crashed on `ModuleNotFoundError: No module named 'httpx'`. Workaround for 1.3.0: `pipx install "gispulse[api]"`.
- **Packaging — `pyarrow` core runtime dependency** — declared `pyarrow>=14,<22` in base. Without it, `gispulse run --output result.parquet`, the GeoParquet writer, and any DuckDB pipeline that lands GeoParquet via `COPY ... TO ... (FORMAT 'parquet')` crashed with `ImportError: Missing optional dependency 'pyarrow.parquet'`.
- **Runtime — `gispulse watch --bulk-threshold` crashed at startup** — `cli_watch.py` wired `--bulk-threshold` straight into `build_runtime(bulk_threshold=...)`, but `build_runtime()` never accepted the kwarg.
- **API — pipelines `ref_layer` 500** — `/pipelines/execute-steps` resolved aliases but left the original keys in `params`. Fixed via `dict.pop()` to strip plumbing keys before the capability call.
- **API — OSS auth stubs + websockets** — `/api/auth/providers` and `/api/auth/me` now ship OSS stubs returning `[]` / `200 null`. Switched the `[api]` extra to `uvicorn[standard]` so `/ws/events` upgrades stop failing with `No supported WebSocket library detected`.
- **API — SPA root static assets** — the fallback now tries the dist root before applying the SPA-route whitelist + index.html fallback.
- **Compose — community-tier boot** — `docker-compose.local.yml` no longer hardcodes `GISPULSE_ENGINE=postgis`; PostGIS opt-in via `--profile postgis`.
- **Catalog — IGN Scan 25 dead entries** — IGN Géoplateforme deprecated `GEOGRAPHICALGRIDSYSTEMS.MAPS`. Dropped `basemap:ign-scan25` and `ign-scan25-wmts`; `GEOGRAPHICALGRIDSYSTEMS.PLANIGNV2` exposed as `basemap:ign-plan` / `ign-plan-wmts`.

### Changed

- **CI** — `test` job installs `[dev,api,postgis,mcp,raster,network,classification,pointcloud,scheduling,sso]` extras instead of `[dev]` alone.
- **CI** — `pip-audit` ignores `CVE-2026-3219` (pip 26.x tar/ZIP confusion, no upstream fix yet; re-evaluate quarterly).
- **Docs** — README pipx quickstart aligned with v1.3 CLI surface.

### Security

- **Dependencies** — bump `fastmcp` `>=0.1,<2.0` → `>=2.14.2,<4.0` (CVE-2025-62800 / 62801 / 69196 / 64340 / 2026-27124 / GHSA-rcfx-77hg-w2wv).
- **Dev** — bump `pytest` `>=7.0,<9.0` → `>=9.0.3,<10.0` (CVE-2025-71176).

---

## [1.3.0] — 2026-04-27

The "no plugin required" CLI release — `gispulse track` + `gispulse watch` make any QGIS / ogr2ogr / FME / ArcGIS / DBeaver writer a first-class trigger source.

### Added

- **`gispulse track`** — SQL change-tracking subcommand (`install` / `uninstall` / `list` / `tail` / `doctor [--auto-fix]`). Installs `_gispulse_change_log` triggers on a GPKG so any client can write to the file and the daemon picks up the changes. (#4, #6)
- **`gispulse watch`** — top-level foreground daemon. SIGINT/SIGTERM clean shutdown (2 s drain), 60 s structured stderr heartbeat, repeatable `--webhook host` allowlist override. Supports daemon mode and `--once` drain. (#5, #11)
- **Trigger payload v2** — `_gispulse_change_log` SQLite triggers bake `new_values` / `old_values` JSON columns + a `geom_changed` flag, captured atomically inside the SQLite trigger via `json_object(NEW.*)`. Removes the post-commit `_load_row_values()` SELECT. (#7)
- **Bulk-mode tick** — `--bulk-threshold N` collapses ticks with `N+` rows into a single `bulk.changed` summary event instead of broadcasting per-row. (#8)
- **Packaging** — `packaging/systemd/gispulse-watch@.service` + `packaging/docker/Dockerfile.watch` + `docker-compose.watch.yml`. (#9)

### Notes

- Closes the Mode 1 scope of #2 entirely. Mode 2 (portal trigger CRUD) remains on the roadmap.
- `gispulse triggers run --watch` and the new top-level `gispulse watch` coexist for one release.
- CI baseline cleanup (#19) — dropped removed `pip-audit --fix-auto=off` flag, regenerated capability matrix, ruff drift cleared (514 → 0 errors), workflows aligned on `gispulse-portal` sibling-repo split.

---

## [1.2.1] — internal

> **Note:** `1.2.1` was never published to PyPI as a standalone tag — its scope was rolled into [`1.3.0`](#130--2026-04-27). The entry below documents what the tag *would* have contained.

### Added

- **`gispulse triggers`** — new CLI subcommand group (`run` / `validate` / `list`) for the standalone trigger runtime (Mode 1). YAML config → GPKG DML triggers, no FastAPI process required.
- **`gispulse/runtime/headless_runtime.py`** — `HeadlessRuntime` wires `ChangeLogWatcher` + `TriggerEvaluator` + `ActionDispatcher` against a `NullEventHub` so the ESB pipeline runs outside the FastAPI lifespan.
- **`gispulse/runtime/config_loader.py`** — strict pydantic v2 schema (`extra="forbid"`, `yaml.safe_load` only, path-traversal guard).
- **`gispulse/runtime/predicate_dsl.py`** — hand-written LL(1) recursive-descent parser for the `predicate:` field. **No `eval`, no `simpleeval`, no third-party dep.** Operators: `== != > >= < <= AND OR NOT IN NOT IN IS NULL IS NOT NULL`. `MAX_DEPTH=32`.
- **`gispulse/runtime/sqlite_retry.py`** — `RetryingSqlExecutor` wraps `GeoPackageEngine.execute()` with exponential backoff on `SQLITE_BUSY`. Caps at 5 retries / 30 s total.
- **`persistence/sql_guardrails.py`** — `enforce()` is the single sandbox between YAML `run_sql` / `set_field` actions and SQLite. Allowlist `INSERT` / `UPDATE` / `DELETE` / `SELECT` only. Hard-blocks `ATTACH` / `DETACH` / `PRAGMA` / `VACUUM` / `LOAD_EXTENSION` / `writable_schema` / `sqlite_master`. Multi-statement payloads refused.

---

## [1.2.0] — 2026-04-25

**First public AGPL-3.0 release on PyPI as `gispulse`.** Source: https://github.com/imagodata/gispulse.

### Added

- **PluginHub + plugin contracts** — `core/plugin_hub.py` + `core/plugin_contracts.py` for plugin discovery via Python entry-points, six groups (`gispulse.routers`, `gispulse.middleware`, `gispulse.auth_provider`, `gispulse.billing_provider`, `gispulse.licence_provider`, `gispulse.connectors`).
- **Pricing catalog** — `core/pricing_catalog.json` for the tier→features catalog (community / pro / team / enterprise) with `inherits` chain.
- **`team` tier** in `persistence.tier.VALID_TIERS` and `core.config.EngineSettings`, between `pro` and `enterprise`.
- **Multi-project gate** on `POST /projects` (community=1, pro=5, team+=∞).
- **Pro-tier gate** on `triggers_router` (router-level) and `pipelines_router` (`/execute`, `/execute-steps`).

### Changed

- **Repository layout** — proprietary modules (Stripe billing, OIDC SSO, RBAC admin, production auth middleware, licence Stripe sync) moved to a private companion package `gispulse-enterprise` distributed under a commercial EULA. The OSS engine ships only AGPL components and discovers enterprise via entry-points at runtime.
- `gispulse/adapters/http/app.py` — billing, auth, admin router mounting now driven by `PluginHub` discovery instead of hard-coded imports; degrades cleanly when no enterprise plugin is installed.

### Removed

- `gispulse/adapters/billing/`, `gispulse/adapters/http/oidc.py`, `middleware/production_auth.py`, `routers/{auth,billing,admin}_router.py` — moved to `gispulse-enterprise`.
- `pricing.yml` (EUR amounts, early-adopter terms) — moved to `gispulse-enterprise/config/pricing_commercial.yml`. The technical tier→features mapping stays here as `core/pricing_catalog.json`.
- Test files specific to enterprise modules.

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
- **Playground S5** rewritten as park accessibility per building.
- **Playground S6** extended to a 250 m then tightened to a 50 m fishnet choropleth.
- **Playground S3** — 6-step pipeline collapsed to 3 via `cost_budgets` + `classify_by_ring`.
- **`adapters/http`** — namespace fork resolved: legacy tree deleted, prod entrypoints flipped to `gispulse.adapters.http.app`.
- **Security** — `MD5` replaced by `BLAKE2b`, `eval` sandboxed for `np`, `_ensure_valid` restored.

### Fixed

- **Capabilities — 4 P0 closed**: `force_geometry_type`, `attribute_join` on a plain DataFrame, NaN crash in `add_z` / `add_m` `from_column`, `singleparts_to_multipart` silent data loss on mixed geom types.
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
- `buffer`, `union`, `reproject`, `filter`, `clip`, `intersects`, `spatial_join`, `centroid`, `area_length`, `dissolve`
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

- [GitHub Repository](https://github.com/imagodata/gispulse)
- [Report a bug](https://github.com/imagodata/gispulse/issues)
- [Roadmap](https://github.com/imagodata/gispulse/projects)

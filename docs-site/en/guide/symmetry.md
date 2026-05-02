---
title: CLI ↔ Portal symmetry
description: Invariance matrix — every public GISPulse capability has a CLI entry point AND a portal entry point (or an explicitly logged UX debt).
---

# CLI ↔ Portal symmetry

GISPulse exposes two equivalent UIs over the same source of truth (`triggers.yaml` + the SQLite/PostGIS change-log): a **CLI** for terminal-first power users and a **web portal** for visual-first onboarding. **This page is the invariance test**: any public feature must appear in both columns — otherwise the UX debt is logged explicitly.

> Product doctrine confirmed 2026-04-30. No GIS-client plugin required: QGIS save, ogr2ogr, ArcGIS Pro export, raw `sqlite3`, CLI or web portal — every DML statement fires the triggers via the change-log. See [Architecture](./architecture).

**Status legend**

| Status | Meaning |
|---|---|
| ✅ | Symmetric: feature available on **both** CLI and portal |
| ⚠️ | Asymmetric: present on one side only, UX debt logged (see issue) |
| ❌ | Deferred: not implemented on either side (see milestone) |
| 🔧 | "Ops" surface intentionally CLI-only (no UI planned) |

---

## 1. Rules — pipeline CRUD

Source of truth: JSON / YAML rules loaded by `rules.loader`. API: [`rules_router.py`](https://github.com/imagodata/gispulse/blob/main/gispulse/adapters/http/routers/rules_router.py).

| Capability | CLI | Portal | Status |
|---|---|---|---|
| Create a rule | `gispulse template use <preset>` (scaffold) then manual JSON edit | `RuleEditorModal` (drag-and-drop registry → schema-driven form) — `components/rules/RuleEditorModal.tsx`, `NodeEditor.tsx` | ✅ |
| List the rules of a pipeline | `gispulse capabilities` (registry) + reading the JSON | `NodeEditor` workspace — renders the pipeline DAG, registry-driven palette | ✅ |
| Edit a rule | Manual JSON edit + `gispulse validate` | `NodePropertyPanel` (schema-driven form) + live validation — `components/nodes/NodePropertyPanel.tsx` | ✅ |
| Delete a rule | Manual JSON delete | Delete node from `NodeEditor` (`Delete` key / context menu) | ✅ |
| Validate a pipeline | `gispulse validate <rules.json>` | Auto-validate on save in `NodeEditor` (POST `/rules/{id}/validate`) | ✅ |
| Convert rule ↔ node | _N/A_ (the CLI manipulates raw JSON) | GET `/rules/{id}/to-node` + POST `/rules/from-node` exposed to `NodeEditor` | ⚠️ |
| Run a pipeline | `gispulse run <input> --rules <pipeline.json> -o <output>` | `WorkflowsView` → "Run" button (POST `/pipelines/execute`) | ✅ |
| Export pipeline as triggers YAML | _N/A_ — the CLI consumes YAML directly | _N/A_ — the portal writes YAML for the runtime | 🔧 |

**Logged asymmetries:**
- ⚠️ **rule ↔ node converter**: only exposed via REST API, no dedicated CLI command. → suggest issue `feat(cli): gispulse rules to-node / from-node` (v1.6+).

---

## 2. Triggers — configuration & runtime

Source of truth: YAML triggers + `_gispulse_change_log` table. API: [`triggers_router.py`](https://github.com/imagodata/gispulse/blob/main/gispulse/adapters/http/routers/triggers_router.py). CLI code: [`cli_triggers.py`](https://github.com/imagodata/gispulse/blob/main/gispulse/cli_triggers.py), [`cli_watch.py`](https://github.com/imagodata/gispulse/blob/main/gispulse/cli_watch.py).

| Capability | CLI | Portal | Status |
|---|---|---|---|
| Create a trigger | Manual YAML edit | `TriggerBuilderInline` / `TriggerBuilderModal` — `PredicateBuilder` + `ActionEditor` + `CronBuilder` (POST `/triggers`) | ✅ |
| List triggers | `gispulse triggers list --gpkg <path>` (installed SQLite triggers) | GET `/triggers` — `ScenariosPanel` / `TriggerHistoryPanel` | ✅ |
| Edit a trigger | Manual YAML edit | `TriggerBuilderModal` (PUT `/triggers/{id}`) | ✅ |
| Delete a trigger | Manual YAML edit + `gispulse triggers validate` | DELETE `/triggers/{id}` from `ScenariosPanel` | ✅ |
| Enable / disable | _N/A_ (comment out in YAML) | POST `/triggers/{id}/toggle` (UI switch in `TriggerBuilderInline`) | ⚠️ |
| Validate a triggers YAML | `gispulse triggers validate --config <yaml> --gpkg <path>` | Live validation on save in `TriggerBuilderModal` (reuses `validate_against_gpkg`) | ✅ |
| Single tick (run-once) | `gispulse triggers run --config <yaml> --once` | POST `/triggers/{id}/evaluate` — "Test" button in `TriggerBuilderInline` | ✅ |
| Long-running daemon | `gispulse triggers run --config <yaml> --watch` or `gispulse watch <gpkg> -r <rules>` | _N/A_ — the portal configures a trigger, the local **runtime** (CLI or daemon) executes it | 🔧 |
| Stream live events | `gispulse triggers run --watch` (JSON logs on stderr) | GET `/triggers/eval-stream` (SSE) consumed by `TriggerHistoryPanel` + `ActivityTimeline` | ✅ |
| Dryrun (preview actions) | _N/A_ — `--once` mode actually executes | POST `/examples/{id}/triggers/dryrun` — preview actions without persisting (Mode 2 Try-it) | ⚠️ |
| Inspect trigger operations | _N/A_ | GET `/triggers/{id}/operations` — execution history in `TriggerHistoryPanel` | ⚠️ |

**Logged asymmetries:**
- ⚠️ **CLI toggle**: no `gispulse triggers enable/disable <id>`. → suggest issue `feat(cli): gispulse triggers toggle <id> --enabled/--disabled` (v1.6+).
- ⚠️ **CLI dryrun**: no CLI equivalent of `POST /examples/{id}/triggers/dryrun`. → suggest issue `feat(cli): gispulse triggers run --dry-run` (v1.6+, deferrable).
- ⚠️ **CLI operations history**: no `gispulse triggers history <id>`. → suggest issue `feat(cli): gispulse triggers history <id>` (v1.6+).

---

## 3. Tracking — SQLite change-log

Source of truth: `_gispulse_change_log` table inside the GPKG. CLI code: [`cli_track.py`](https://github.com/imagodata/gispulse/blob/main/gispulse/cli_track.py). API: `datasets_router.py` (`enable_tracking` / `disable_tracking` / `tracking_status`).

| Capability | CLI | Portal | Status |
|---|---|---|---|
| Install tracking on a layer | `gispulse track install <gpkg> --layer <name>` | POST `/datasets/{id}/enable_tracking` — "Enable tracking" button in `DatasetCard` | ✅ |
| Install on every layer | `gispulse track install <gpkg> --all-layers` | POST `/datasets/{id}/enable_tracking` (no "all" toggle) | ⚠️ |
| Uninstall tracking | `gispulse track uninstall <gpkg> --layer <name>` | POST `/datasets/{id}/disable_tracking` — `DatasetContextMenu` action | ✅ |
| List tracked layers | `gispulse track list <gpkg>` (triggers + pending counts) | GET `/datasets/{id}/tracking_status` — shown on `DatasetCard` | ✅ |
| Tail pending changes | `gispulse track tail <gpkg> --limit 50` | _N/A_ — `ActivityTimeline` consumes post-dispatch events, not the raw change-log | ⚠️ |
| Diagnostic + auto-fix | `gispulse track doctor <gpkg> [--auto-fix]` | _N/A_ | ⚠️ |
| Global env diagnostic | `gispulse doctor` | _N/A_ — intentional CLI-only "ops" surface | 🔧 |

**Logged asymmetries:**
- ⚠️ **all-layers UI**: enable_tracking only handles one layer at a time. → suggest issue `feat(portal): bulk enable tracking from DatasetCard` (v1.6+).
- ⚠️ **change-log tail**: useful for debug, no UI panel. → suggest issue `feat(portal): raw change-log inspector panel` (v1.6+, deferrable).
- ⚠️ **track doctor UI**: trigger healthcheck + auto-fix should be exposed in `DatasetCard`. → suggest issue `feat(portal): tracking health badge + repair action` (v1.6+).

---

## 4. Datasets — upload, listing, deletion

API: [`datasets_router.py`](https://github.com/imagodata/gispulse/blob/main/gispulse/adapters/http/routers/datasets_router.py), [`portal_upload_router.py`](https://github.com/imagodata/gispulse/blob/main/gispulse/adapters/http/routers/portal_upload_router.py).

| Capability | CLI | Portal | Status |
|---|---|---|---|
| Upload a dataset (local file) | `gispulse run` consumes a local file directly | `CatalogImportDialog` + `DragDropOverlay` (POST `/datasets/upload`) | ⚠️ |
| Upload from URL | _N/A_ | POST `/datasets/import-url` — input in `CatalogImportDialog` | ⚠️ |
| Import from OGC API Features | _N/A_ | POST `/datasets/ogc` — `CatalogPanel` connector | ⚠️ |
| List datasets | `gispulse layers <file>` (single-file); `gispulse info <file>` | GET `/datasets` → `DatasetsView` + `DatasetCard` grid | ⚠️ |
| Inspect metadata (CRS, layers, styles) | `gispulse info <file>` | GET `/datasets/{id}` → `InspectorPanel` + `DatasetSchemaGraph` | ✅ |
| Delete a dataset | _N/A_ — `rm <file>` manually | DELETE `/datasets/{id}` — `DatasetContextMenu` | ⚠️ |
| Rename a dataset | _N/A_ | PATCH `/datasets/{id}` → `RenameDialog` | ⚠️ |
| Export to GPKG | `gispulse run -o <output.gpkg>` (pipeline output) | POST `/datasets/export-gpkg` — "Export" button in `DatasetCard` | ✅ |
| Export (other formats) | `gispulse run -o <output.{geojson,shp,parquet,fgb,...}>` | POST `/datasets/export` (16+ formats — see [Formats I/O](./formats)) | ✅ |

**Logged asymmetries:**
- ⚠️ **CLI dataset registry**: datasets are implicit on the CLI side (a file on disk) vs explicit on the portal (persistent registry). → this design gap is intentional for Mode 1, but we could expose `gispulse datasets list/add/rm` pointing to an optional local registry. To debate v1.6+ — issue `feat(cli): optional dataset registry`.
- ⚠️ **import-url / OGC CLI**: no `gispulse import url <URL>` or `gispulse import ogc <endpoint>`. → suggest issue `feat(cli): gispulse import` (v1.6+).

---

## 5. Examples — Mode 2 portal "Try it"

API: [`examples_router.py`](https://github.com/imagodata/gispulse/blob/main/gispulse/adapters/http/routers/examples_router.py). Sprint v1.5.1, fixed read-only datasets registry.

| Capability | CLI | Portal | Status |
|---|---|---|---|
| List available examples | _N/A_ — intentional portal-only surface (Mode 2 Community demo) | GET `/examples` → `MarketplacePage` + landing | 🔧 |
| Example details | _N/A_ | GET `/examples/{id}` → preview card | 🔧 |
| Tile / MVT preview | _N/A_ — the viewer reads the local GPKG directly | GET `/examples/{id}/preview` + `/examples/{id}/tiles/{z}/{x}/{y}.mvt` → `MapView` | 🔧 |
| Dryrun triggers on example | `gispulse triggers run --once --config <yaml> --gpkg <example.gpkg>` (locally, after `pipx install gispulse`) | POST `/examples/{id}/triggers/dryrun` — `TriggerBuilderModal` "Test on this example" | ✅ |

**"Try it" surface:** by design the portal exposes examples as the **on-ramp** to `pipx install gispulse`. CLI users who clone the repo access the same datasets via `examples/`. No UX debt here — this is the funnel.

---

## 6. Styles — QML / SLD roundtrip

API: `portal_datasets_router.py` (styles import / export / breaks). Sprint v1.5.0.

| Capability | CLI | Portal | Status |
|---|---|---|---|
| Import a QML style | _N/A_ — QML already copied by `gispulse run --all-layers` | POST `/datasets/{id}/styles/import` — `LayerColorPicker` / `SchemaView` action | ⚠️ |
| Export a QML style | `gispulse run` automatically copies styles from the input GPKG | GET `/datasets/{id}/styles` → "Download QML" button | ✅ |
| Update style | _N/A_ | PUT `/datasets/{id}/styles` — `LayerColorPicker` + `MapLegend` editing | ⚠️ |
| Compute breaks (Jenks / quantile / equal interval) | _N/A_ | POST `/datasets/{id}/layers/{layer}/breaks` — `LayerColorPicker` classification picker | ⚠️ |
| List distinct field values | _N/A_ | GET `/datasets/{id}/layers/{layer}/distinct/{field}` | ⚠️ |
| Descriptive stats (min/max/mean/quantiles) | _N/A_ | GET `/datasets/{id}/layers/{layer}/stats/{field}` — `InspectorPanel` | ⚠️ |

**Logged asymmetries:**
- ⚠️ **CLI styling**: import / classify breaks / stats are **inherently visual** cartographic operations. CLI symmetry is low value here. → loggable as *non-priority* issue `feat(cli): gispulse style classify --field <f> --method jenks --bins 5` for CI / batch. v1.7+.

---

## 7. Run — pipeline execution

Source of truth: `core.pipeline` + `orchestration.session_manager`. API: [`pipelines_router.py`](https://github.com/imagodata/gispulse/blob/main/gispulse/adapters/http/routers/pipelines_router.py), [`jobs_router.py`](https://github.com/imagodata/gispulse/blob/main/gispulse/adapters/http/routers/jobs_router.py).

| Capability | CLI | Portal | Status |
|---|---|---|---|
| Execute a pipeline (sync) | `gispulse run <input> --rules <pipeline.json> -o <output>` | POST `/pipelines/execute` — `WorkflowsView` "Run" | ✅ |
| Step-by-step execution | _N/A_ (no dedicated CLI — the engine runs the whole pipeline) | POST `/pipelines/execute-steps` — debug mode in `NodeEditor` | ⚠️ |
| Validate a pipeline | `gispulse validate <pipeline.json>` | POST `/pipelines/validate` — auto-validate on save | ✅ |
| List jobs | `gispulse jobs list [--host HOST] [--api-key KEY]` | GET `/jobs` → `JobTrackerCorner` (lazy panel) | ✅ |
| Job status | `gispulse jobs status <JOB_ID>` | GET `/jobs/{id}` → `JobTrackerCorner` detail | ✅ |
| Stream job events | _N/A_ (the CLI runs sync, no SSE) | GET `/jobs/{id}/events` (SSE) → progress in `JobTrackerCorner` | ⚠️ |
| Cancel a job | `gispulse jobs cancel <JOB_ID>` | POST `/jobs/{id}/cancel` → `JobTrackerCorner` action | ✅ |
| Download job features | _N/A_ — output already written locally by `gispulse run` | GET `/jobs/{id}/features` + `/jobs/{id}/download` | ⚠️ |
| Submit an async job | _N/A_ (`gispulse run` is synchronous) | POST `/jobs` — submit async via `WorkflowsView` | ⚠️ |
| Pipeline examples / presets | `gispulse template list` + `gispulse template use <name>` | GET `/pipelines/examples` → palette or `WorkflowList` | ✅ |

**Logged asymmetries:**
- ⚠️ **execute-steps CLI**: useful for step-by-step debugging. → suggest issue `feat(cli): gispulse run --step <id>` (v1.7+, deferrable).
- ⚠️ **jobs SSE / async CLI**: `gispulse run` is synchronous by design (script-friendly). The async pattern is portal-only, justified for long-running workflows. Not urgent.

---

## 8. Schedules — cron jobs

API: [`schedules_router.py`](https://github.com/imagodata/gispulse/blob/main/gispulse/adapters/http/routers/schedules_router.py). Component: `components/schedules/ScheduleForm.tsx`.

| Capability | CLI | Portal | Status |
|---|---|---|---|
| Create a schedule | _N/A_ — use native OS `cron` / `systemd timers` to wrap `gispulse run` | POST `/schedules` → `ScheduleForm` (`CronBuilder` reused from triggers) | ⚠️ |
| List schedules | _N/A_ | GET `/schedules` | ⚠️ |
| View / edit schedule | _N/A_ | GET / PATCH `/schedules/{id}` | ⚠️ |
| Delete schedule | _N/A_ | DELETE `/schedules/{id}` | ⚠️ |
| Manual run-now | `gispulse run` directly | POST `/schedules/{id}/run-now` | ⚠️ |

**Logged asymmetries:**
- ⚠️ **schedules CLI absent**: product decision pending — either we assume "use cron" for CLI users, or we expose `gispulse schedules add/list/rm`. → suggest issue `decision: gispulse schedules CLI subcommand` (v1.6+).

---

## 9. Marketplace — third-party plugins / capabilities

API: [`marketplace_router.py`](https://github.com/imagodata/gispulse/blob/main/gispulse/adapters/http/routers/marketplace_router.py). Components: `components/marketplace/`, `pages/MarketplacePage.tsx`.

| Capability | CLI | Portal | Status |
|---|---|---|---|
| List installed plugins | `gispulse marketplace list [QUERY]` | GET `/marketplace/plugins` → `MarketplacePage` | ✅ |
| Search the catalogue | `gispulse marketplace search QUERY` | GET `/marketplace/search` + `/marketplace/catalog` | ✅ |
| Plugin details | `gispulse marketplace info NAME` | GET `/marketplace/plugins/{name}` | ✅ |
| Install a plugin | `gispulse marketplace install NAME` | POST `/marketplace/install` | ✅ |
| Uninstall a plugin | `gispulse marketplace uninstall NAME` | DELETE `/marketplace/plugins/{name}` | ✅ |

✅ **Full symmetry.** Marketplace surface aligned by construction since v1.1.0.

---

## 10. Templates — project scaffolding

API: `pipelines_router.py` `/examples`. CLI: `gispulse template`.

| Capability | CLI | Portal | Status |
|---|---|---|---|
| List templates | `gispulse template list` | GET `/pipelines/examples` (preset library exposed in `WorkflowList`) | ✅ |
| Scaffold a project from a template | `gispulse template use <NAME> [--output-dir DIR]` | `OnboardingFlow` (first launch) + `SaveTemplateDialog` | ✅ |
| Create a workflow from a template | `gispulse template workflow` | `WorkflowList` → "From template" | ✅ |

✅ **Full symmetry.**

---

## 11. Viewer / Portal / Engine — process lifecycle

"Ops" surface — how to launch GISPulse.

| Capability | CLI | Portal | Status |
|---|---|---|---|
| Launch viewer (read-only) | `gispulse serve <file> [--port 8765]` | _N/A_ — the viewer is embedded in the portal | 🔧 |
| Launch portal | `gispulse portal [--port 8001]` | _N/A_ — the portal **is** the portal (meta) | 🔧 |
| Launch full engine | `gispulse engine [--port 8001]` (Tauri sidecar JSON) | _N/A_ | 🔧 |
| Connect "My engine" from public portal | `gispulse portal --backend=<URL>` (Mode 2 — sprint v1.5.1) | `BackendStatusBanner` + `SettingsPanel` (backend URL input, persisted in localStorage) — **shipped gispulse-portal #30** | ✅ |
| Diagnose environment | `gispulse doctor` | _N/A_ | 🔧 |
| Update | `gispulse update [--check] [--force]` | _N/A_ — the web portal self-updates, the CLI manages its own version | 🔧 |
| Initialize a project | `gispulse init [DIR] [--name NAME]` | `OnboardingFlow` (visual equivalent for the first session) | ✅ |
| Telemetry opt-in | `gispulse telemetry --enable / --disable / --status` | _N/A_ — CLI-only config (env var `GISPULSE_TELEMETRY=1` for scripts) | 🔧 |

**Intentional 🔧 surface:** process lifecycle and telemetry are CLI-only by design — the portal _is_ already running when the user clicks. No debt.

---

## 12. SQL Console — SQL editing / preview

API: [`portal_sql_router.py`](https://github.com/imagodata/gispulse/blob/main/gispulse/adapters/http/routers/portal_sql_router.py). Component: `components/sql/SQLConsole.tsx`.

| Capability | CLI | Portal | Status |
|---|---|---|---|
| Execute a SQL query | _N/A_ — `gispulse run` accepts the pipeline + `postgis_sql` capability | POST `/sql/execute` → `SQLConsole` | ⚠️ |
| Preview SQL results | _N/A_ | `SQLPreviewTable` (auth + blocklist on the backend, v1.1.0) | ⚠️ |
| Export SQL results | _N/A_ | POST `/sql/export` | ⚠️ |

**Logged asymmetries:**
- ⚠️ **CLI SQL**: feature is mainly "interactive exploration" — already covered for batch via the `postgis_sql` capability inside a pipeline. Low priority. → deferrable issue `feat(cli): gispulse sql --execute "SELECT ..."` (v1.7+).

---

## 13. Auth — SSO and identity

API: [`auth_router.py`](https://github.com/imagodata/gispulse/blob/main/gispulse/adapters/http/routers/auth_router.py). OSS: anonymous stub. Pro/Enterprise: OIDC (Google / Azure / Keycloak — see `gispulse-enterprise`).

| Capability | CLI | Portal | Status |
|---|---|---|---|
| List SSO providers | _N/A_ (no CLI auth in OSS) | GET `/auth/providers` → `pages/auth/` | 🔧 |
| User info | _N/A_ | GET `/auth/me` → `UserMenu` + `AuthGuard` | 🔧 |

**Intentional 🔧 surface:** OSS Mode 1 = single-user CLI without auth. Mode 2 portal SaaS Pro v1.6+ will add visual auth. CLI auth ships with `gispulse login` (issue v1.7+).

---

## Summary

| Area | ✅ Symmetric | ⚠️ Asymmetric | 🔧 Intentional CLI/Portal-only | ❌ Deferred |
|---|---|---|---|---|
| Rules | 7 | 1 | 1 | 0 |
| Triggers | 6 | 4 | 1 | 0 |
| Tracking | 4 | 3 | 1 | 0 |
| Datasets | 3 | 6 | 0 | 0 |
| Examples | 1 | 0 | 3 | 0 |
| Styles | 1 | 5 | 0 | 0 |
| Run | 5 | 4 | 0 | 0 |
| Schedules | 0 | 5 | 0 | 0 |
| Marketplace | 5 | 0 | 0 | 0 |
| Templates | 3 | 0 | 0 | 0 |
| Lifecycle / Engine | 2 | 0 | 6 | 0 |
| SQL | 0 | 3 | 0 | 0 |
| Auth | 0 | 0 | 2 | 0 |
| **Total** | **37** | **31** | **14** | **0** |

**Reading:** of 82 public capabilities, 37 are already symmetric, 14 are CLI-only or portal-only by intentional design, and **31 UX debts are identified and listed** above with their suggested issue. No capability is silently missing from either surface.

---

## How this page stays up to date

This matrix is currently **maintained manually**. Any new feature (CLI or portal) must be added to the corresponding row with its status. A v1.6+ issue (`feat(scripts): generate symmetry.md from CLI ↔ portal mapping`) explores automatic generation from a declarative source-code mapping — for now manual content stays authoritative.

**Process for any new PR adding a feature:**
1. Identify the row to add or update in this matrix
2. If the PR introduces an asymmetry, **log the corresponding debt issue** in the same session
3. Request review from Marco (gis-lead-dev) or Jordan (jordan-po) to validate the status

**See also:**
- [Capability coverage matrix](./coverage) — for the 100+ pipeline capabilities (test ✕ docs ✕ playground ✕ template)
- [CLI Reference](./cli)
- [Architecture](./architecture)
- [`cli_portal_symmetry_axiom` doctrine](https://github.com/imagodata/gispulse/blob/main/docs/CLI_PORTAL_AXIOM.md) (memory)

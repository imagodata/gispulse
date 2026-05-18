# ADR 0005 — Unified GISPulse manifest (`version: 3`): `sources` / `models` / `triggers`

**Status:** Proposed
**Date:** 2026-05-18
**Deciders:** GISPulse maintainers
**Issue:** EPIC [#243](https://github.com/imagodata/gispulse/issues/243)

## Context

GISPulse exposes **three concurrent configuration surfaces** today:

1. `triggers.yaml` → `GISPulseConfig` (`version: 1`, `runtime/config_loader.py`)
   — `triggers`, `layers`, `security`, `runtime`.
2. Pipeline JSON → `PipelineSpec` (`version: 2`, `core/pipeline.py`) —
   `steps[]`, inline `triggers[]`, `ref_layers{}`.
3. `rules` JSON → a bare ordered list `[{capability, config}]`
   (`rules/loader.py`).

The goal is twofold: (a) evolve GISPulse from ETL toward **ELT**
("dbt for spatial") — declarative, DAG-based, incremental; and (b)
**unify the configuration model** so triggers, transformation models,
and table relations live in one schema, not three.

An architecture audit (2026-05-18) reviewed an earlier draft of this ADR
and found it re-invented at least **five existing subsystems** under new
names. This ADR supersedes that draft. The corrected principle:
**the unified manifest is a declarative surface that compiles down to,
and reuses, the engine that already exists.** Nothing below builds a new
DAG engine, a new ATTACH layer, or a new dispatcher.

### Existing subsystems this ADR builds on

| Need | Existing component |
|---|---|
| Declarative DAG of steps | `core/pipeline.py` — `PipelineSpec`, `StepSpec.input`, `is_dag` |
| Topological sort + cycle detection | `orchestration/graph_executor.py` — `_topo_sort` (Kahn) |
| Step execution, multi-input, parallel branches | `orchestration/pipeline_executor.py`, `graph_executor.py` |
| Per-node ELT/ETL dispatch | `capabilities/strategy.py` — `select_strategy()`, `ExecutionContext` |
| Lazy `ATTACH` + spatial views | `runtime/layer_registry.py` — `LayerRegistry.install()` |
| Lazy zero-copy remote fetch | `core/fetchers/base.py` — `LazyFetcher`, `FetchMode.REFERENCE` (v1.9.0 EPIC #226) |
| Change data capture | `persistence/` — `duckdb_change_detector`, `file_blob_cdc`, `duckdb_diff_engine`, `change_log_watcher` |
| Declared sources | `runtime/config_loader.py` `LayerSourceConfigModel` + `core/sources.py` `SOURCES` registry |
| Engine inference from URI | `engine_inference.resolve_engine()`, `core/config.py` `EngineSettings` |
| Application facade | `app.py` — `GISPulseApp` |

## Decision

A **single unified manifest, `version: 3`**, supersedes `version: 1`
(`triggers.yaml`) and `version: 2` (pipeline JSON). One file, one schema,
one loader, anchored on `GISPulseApp`. It fuses five top-level sections:
`sources`, `models`, `triggers`, `security`, `runtime`.

### Settled design questions

**D — One schema, two sections (not one merged node concept).**
`models:` (batch derivation of a dataset) and `triggers:` (per-row
reactive side effects) stay **distinct sections of the same schema**.
They are not collapsed into a single "node" type: `rules/
operation_executor.py` is explicit that per-row trigger handling and
batch capability pipelines must not share an execution path
(latency/throughput). One file, one schema — two semantics.

**A — `version: 3`.** A clean new number. `version: 2` already means
`PipelineSpec`; reusing it would make `version: 2` ambiguous (two
schemas depending on whether `models:` is present). `version: 3` is the
single unambiguous current schema.

**B — Deprecation window for v1/v2.** `version: 1` and `version: 2`
keep loading when v3 ships (target release **v1.10.1**), but are
**marked deprecated** at that point and **removal is targeted for the
next major, v2.0.0**. A `gispulse migrate` command rewrites a v1/v2 file
into a v3 manifest. Three code paths are *not* maintained indefinitely.

**C — `models:` nested syntax is the single authoring form.** The
authored DAG uses the nested, dbt-like form (`select:` + a short
`transform:` list). `steps:` / `StepSpec` is retained **only as the
internal compiled representation**: the loader compiles `models:` →
`PipelineSpec.steps`. There is one syntax for humans, one internal form
for the engine — no duplicate authoring grammar.

These four sit on top of the earlier framing decisions, unchanged:
single manifest file (Q1), `rules` JSON kept as a degenerate model (Q2),
geometry-agnostic DSL (Q3), one embedded expression sublanguage (Q4).

## How each section maps to the existing engine

This is the core of the audit correction. **No section introduces a new
mechanism.**

| Manifest section | Compiles to / delegates to |
|---|---|
| `sources` | `LayerSourceConfigModel` (extended with logical `geometry`/`crs`) for local inputs; `core/sources.py` `DataSource` / `SOURCES` registry for remote/declarative ones. The loader routes by URI scheme. |
| `models` | Compiles to `PipelineSpec.steps`; `select:`/`with:` references become `StepSpec.input` edges. Executed by `PipelineExecutor` + `GraphExecutor`. |
| DAG / cycle check | `GraphExecutor._topo_sort` (Kahn) — extracted into a shared utility so `rules/validation.py` can run cycle detection at **load time**. The algorithm is not rewritten. |
| per-node ELT/ETL | `select_strategy(strategies, ExecutionContext)`, already called by `GraphExecutor` via `execute_with_context`. The real work is writing the missing SQL strategies for the 58 "ELT-able" capabilities — not a dispatcher. |
| `staging` | A declarative facade over `LayerRegistry.install()` (lazy `ATTACH` + views) and `LazyFetcher` / `FetchMode.REFERENCE` + the `VirtualDatasetRegistry` (v1.9.0 A9, #235). **No new `ATTACH` code.** |
| `staging.cdc` | Wires the existing `persistence/` CDC modules. No new diff engine. |
| `staging.engine` | The existing `GISPulseConfig.engine` / `EngineSettings`, with `resolve_engine()` URI inference. |
| anchor point | All use cases exposed via `GISPulseApp` (`run_manifest()`, `explain()`). The engine is **not** wired into `cli.py` directly — CLI, portal, and MCP must share the same facade (CLI↔Portal symmetry axiom). |

Genuinely **new** and retained as-is: `materialize` (`view` / `table` /
`incremental`) and the `gispulse explain` subcommand.

## Specification (draft v0.1)

```yaml
version: 3

sources:                    # declared inputs + table relations
  cadastre:
    uri: ./parcelles.gpkg
    layer: parcelles
    geometry: geom          # logical geometry column (Q3)
    crs: EPSG:2154          # logical CRS — never the physical encoding
  plu:
    uri: s3://bucket/plu.parquet

staging:                    # facade over LayerRegistry / LazyFetcher
  engine: duckdb            # GISPulseConfig.engine — global, not per-model
  attach: true              # → LayerRegistry.install() ; remote → LazyFetcher REFERENCE
  cdc: incremental          # off | snapshot | incremental — wires persistence/ CDC

models:                     # the DAG — compiles to PipelineSpec.steps
  zones_u:
    select: plu
    transform:
      - filter: { where: "zone == 'U'" }     # where: = predicate expression (Q4)
    materialize: view

  parcelles_constructibles:
    select: cadastre
    transform:
      - spatial_join: { with: zones_u, predicate: intersects }   # edge = relation
      - area: { as: surface_m2 }
    materialize: incremental
    refresh: on_change

triggers:                   # reactive, per-row side effects — unchanged
  - name: notify
    table: parcelles_constructibles
    when: [INSERT]
    actions: [{ type: webhook, url: https://example.com/hook }]

security: { webhook_allowlist: [example.com] }
runtime:  { poll_interval_ms: 1000, max_batch: 200 }
```

### `materialize` semantics

| Value | Behaviour |
|---|---|
| `view` | non-materialized SQL view (ELT) / recompute on read (ETL) |
| `table` | materialized, full recompute each run |
| `incremental` | re-transform only changed rows — requires `staging.cdc: incremental` + an increment key; DELETEs cascade to descendant models, reusing the bounded fixed-point cascade of ADR 0002 |

### ETL/ELT boundary

Each DAG node is compiled independently against the SQL-ability matrix
(audit 2026-05-18: 4 ELT-native / 58 ELT-able / 26 ETL-only /
36 ETL-strict). A fully SQL-able model compiles to SQL push-down (ELT) in
`staging.engine`. A model containing an ETL-strict capability breaks into
a Python node — the node is materialized, run via `capabilities/`,
re-injected as a staging table. The decision is `select_strategy()`
applied per node, surfaced by `gispulse explain`.

### Validation

A `version: 3` JSON Schema is added to `core/pipeline_schema.py`
(alongside the deprecated v1/v2 schemas). Load-time validation covers
structure, resolved `select`/`with` references, **cycle absence** (shared
`_topo_sort` utility), capability existence, and parsable `predicate`
expressions. The manifest is authored in YAML (`gispulse.yaml`,
canonical) or JSON (portal node editor / API) — one schema, two
serializations.

## Consequences

### Positive

- One manifest, one schema, one loader — the three config surfaces
  converge instead of multiplying.
- No new engine: the DAG executor, ATTACH layer, CDC, and dispatcher are
  all reused. The implementation effort is *compilation + missing SQL
  strategies*, not infrastructure.
- `staging` is explicitly subordinate to the v1.9.0 fetcher foundation —
  no parallel ATTACH implementation.

### Negative

- v1/v2 users must migrate before v2.0.0 (mitigated by `gispulse
  migrate` and the deprecation window).
- Requires a dialect-aware SQL generation layer as a hard prerequisite
  (5 DuckDB/PostGIS divergences found by the audit).
- 36 ETL-strict capabilities never join the ELT path — a structural
  limit.

### Prerequisite & dependencies

- Depends on EPIC [#226](https://github.com/imagodata/gispulse/issues/226)
  (v1.9.0 fetcher foundation): `staging` consumes `LazyFetcher` /
  `VirtualDatasetRegistry`. This ADR must not ship before that lands.
- The dialect-aware SQL layer must land before the `model` engine code.
- Depends on ADR 0001 (DuckDB-spatial contract dialect) and ADR 0002
  (trigger cascade semantics).

## Status of related work

- The EPIC [#243](https://github.com/imagodata/gispulse/issues/243) has
  been **re-scoped** against this ADR (2026-05-18): "build a DAG
  resolver" → "extract `_topo_sort` as a load-time validator"; "build
  per-node dispatch" → "write missing SQL strategies"; "new `version: 2`
  grammar" → "`version: 3` manifest + `models:`→`PipelineSpec` compiler".
  Child #250 (per-node dispatch) is **closed** — `select_strategy()`
  already exists; the real work moved into #245/#246 (SQL strategies)
  and #251 (`gispulse explain` inspection). Implementation has not
  started: v1.9.0 (EPIC #226) ships first.
- SQL-ability audit (2026-05-18): effort to reach "majority ELT"
  estimated 6–8 weeks (1 dev) — to be re-estimated downward now that the
  DAG engine and dispatcher are reused, not built.

[pipeline]: ../../gispulse/core/pipeline.py
[graph_executor]: ../../gispulse/orchestration/graph_executor.py
[strategy]: ../../gispulse/capabilities/strategy.py
[layer_registry]: ../../gispulse/runtime/layer_registry.py
[fetchers]: ../../gispulse/core/fetchers/base.py
[sources]: ../../gispulse/core/sources.py
[app]: ../../gispulse/app.py

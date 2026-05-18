# ADR 0005 — ELT `model`/`staging` DSL (declarative spatial transformation)

**Status:** Proposed
**Date:** 2026-05-18
**Deciders:** GISPulse maintainers
**Issue:** _(none yet — this ADR precedes the EPIC)_

## Context

GISPulse exposes **two** configuration surfaces today:

1. **`triggers.yaml`** (`examples/cli/triggers.yaml`) — *reactive*: a CDC
   event (`INSERT`/`UPDATE`/`DELETE`) on a table, gated by a `predicate`,
   fires `actions` (`webhook`, `set_field`, `run_sql`). Executed per-row,
   trigger-time, by `rules/operation_executor.py` (SQL, server-side).
2. **`rules` JSON** (`rules/loader.py`) — *imperative batch*: an ordered
   list `[{capability, config}]` applied to a `GeoDataFrame` in-memory by
   `RuleEngine` (`rules/engine.py`) via the `capabilities/` registry.

The `rules` JSON is purely imperative. It has **no notion** of named
intermediate results, dependencies, a DAG, materialization, or lineage.
Meanwhile the ELT infrastructure already exists but is unused: the
`Strategy` pattern (`capabilities/strategy.py`, `StrategyMode
{PYTHON, DUCKDB, POSTGIS}`) is wired through to `orchestration/
capability_executor.py`, yet **only 4 of 124 capabilities** declare SQL
strategies (`buffer`, `clip`, `intersects`, `filter`). A SQL-ability
audit (2026-05-18) classified the 124 capabilities as: 4 ELT-native,
58 ELT-able (gap), 26 ETL-only, 36 ETL-strict.

To position GISPulse as a "dbt for spatial" — declarative,
DAG-based, incremental — the imperative `rules` list must be superseded
by a **declarative model graph**. The YAML contract is a public API:
once published in a release, every user's config depends on it. It must
therefore be specified before any engine code is written.

## Decision

Introduce a **`version: 2`** manifest with three new top-level sections —
`sources`, `staging`, `models` — alongside the unchanged `triggers`,
`security`, `runtime`. The four framing questions were settled as
follows.

### Q1 — Single manifest file

`models` and `triggers` live in **one file**, as distinct top-level
sections. A model's incremental refresh is declared inline (`refresh:`),
*not* as a separate `triggers` entry. Rationale: the CLI↔Portal symmetry
axiom requires CLI and portal to be equivalent UIs over the *same*
config file. The `triggers` action system is **not** merged into
`models` — a trigger produces side effects, a model produces a derived
dataset.

### Q2 — `rules` JSON is kept, no deprecation

The imperative `rules` JSON (`rules/loader.py`) keeps loading verbatim.
The loader treats it as a **degenerate single anonymous model**: implicit
`select` + `transform` = the ordered list, `materialize: table`. The
`model` YAML is the strict **superset** (it adds DAG + materialization +
incremental on top). The portal node editor keeps emitting JSON. No
breaking change.

### Q3 — Geometry-agnostic DSL

The DSL never exposes the physical geometry representation. `sources`
declare a **logical** geometry (`geometry: <col>` + `crs:`). The
dialect-aware SQL layer (see "Prerequisite" below) maps it to
WKB-in-DuckDB (`ST_GeomFromWKB`) vs. native `geometry` in PostGIS. This
is consistent with ADR 0001: DuckDB-spatial remains the contract
dialect; `staging.engine` is the v2 successor of the existing top-level
`engine:` escape hatch.

### Q4 — One expression sublanguage, two scopes

The existing `predicate` DSL (`dsl/expression_parser.py`,
`dsl/geom_fcts.py`) remains the **single** boolean/scalar expression
sublanguage. It is *embedded* wherever a `transform` operation needs a
condition (`filter.where`, `case_when`, `calculate.expr`). `transform:`
is the pipeline DSL. There is no second grammar and no second parser.

## Specification (draft v0.1)

### Grammar

```yaml
version: 2                  # v1 (or absent) = current strict behaviour

sources:                    # Extract — raw inputs
  cadastre:
    uri: ./parcelles.gpkg
    layer: parcelles
    geometry: geom          # logical geometry column (Q3)
    crs: EPSG:2154          # logical CRS — never the physical encoding
  plu:
    uri: s3://bucket/plu.parquet

staging:                    # Load — how raw data enters the engine
  engine: duckdb            # duckdb | postgis — global, not per-model (see below)
  attach: true              # lazy ATTACH, no RAM copy ; false = ingest
  cdc: incremental          # off | snapshot | incremental

models:                     # Transform — the declarative DAG
  zones_u:
    select: plu
    transform:
      - filter: { where: "zone == 'U'" }     # where: = predicate expression (Q4)
    materialize: view

  parcelles_constructibles:
    select: cadastre
    transform:
      - spatial_join: { with: zones_u, predicate: intersects }
      - area: { as: surface_m2 }
      - calculate: { as: ratio, expr: "surface_m2 / valeur" }
    materialize: incremental
    refresh: on_change       # inline refresh (Q1) — not a triggers entry
    assert:                  # model-level data tests (spec'd now, implemented later)
      - not_null: [geom, surface_m2]
      - unique: [fid]
      - geometry_valid: geom
      - expect_rows: { min: 1 }

triggers:                    # unchanged — reactive, side effects
  - name: notify
    table: parcelles_constructibles    # a model MAY be a trigger source
    when: [INSERT]
    actions: [{ type: webhook, url: https://example.com/hook }]
```

### DAG resolution

- The engine scans every `select:` / `with:` reference and builds a
  directed graph. Nodes = `sources` (leaves) + `models`.
- **Cycle detection is mandatory at load time** (`rules/validation.py`
  extended) — a blocking error, never a runtime failure.
- Execution order = topological sort; independent models run in
  parallel.
- A model with no dependents and no `refresh`/trigger emits an
  "orphan model" *warning*, not an error.

### `materialize` semantics

| Value | Behaviour | Use |
|---|---|---|
| `view` | non-materialized SQL view (ELT) / recompute on read (ETL) | light intermediate models |
| `table` | materialized table/file, full recompute each run | final output, heavy reused models |
| `incremental` | re-transform only changed rows since last run | the differentiator — coupled to CDC |

`incremental` requires `staging.cdc: incremental` and an increment key
(the source `pk_col`, or an `updated_at` column). The engine applies the
CDC delta, not the whole source. **DELETEs cascade** to descendant
models, reusing the bounded fixed-point cascade of ADR 0002. The first
run is a full `snapshot`; a source schema change forces a re-snapshot.

### ETL/ELT boundary

Each DAG node is compiled independently against the SQL-ability matrix:

- Fully SQL-able model → compiled to SQL push-down (**ELT**) in
  `staging.engine`.
- A model containing ≥1 ETL-strict capability (raster, network,
  pointcloud) → **the node breaks**: the engine materializes an extract,
  runs Python (`capabilities/`), re-injects the result as a staging
  table. The DAG stays coherent; one node leaves SQL.
- An ELT-able-but-not-yet-implemented operation falls back to Python via
  `Strategy.can_execute()` — already the pattern of
  `_BufferDuckDBStrategy`. Silent degradation, but **visible in
  `explain`**.
- The decision is global and inspectable, never implicit.

### `gispulse explain`

A new subcommand renders the DAG with, per model: mode (ELT/ETL),
engine, materialization, dependencies; and per operation: the chosen
backend plus the `ST_*` function or fallback reason. Predictability is
a selling point against FME.

### Backward compatibility

- `version: 1` (or absent) → current strict behaviour, `triggers` only.
- `version: 2` → activates `sources`/`staging`/`models`.
- `rules` JSON loads verbatim as a degenerate anonymous model (Q2).
- File naming: **`triggers.yaml` keeps working forever**; `gispulse.yaml`
  becomes the canonical name for `version: 2`; the CLI auto-discovers
  both; new scaffolds emit `gispulse.yaml`.

### Validation

A v2 JSON Schema is added to `core/pipeline_schema.py`. Load-time
validation covers structure, types, resolved `select`/`with` references,
**cycle absence**, capability existence in the registry, and parsable
`predicate` expressions. `gispulse triggers validate` covers v1 and v2.

## Resolved open questions

1. **Per-model `staging.engine`** — *rejected for v0.1.* Engine is
   global. ADR 0001 rejects pushing polyglot complexity onto rule
   authors; a cross-engine DAG edge requires inter-engine data transfer,
   a sub-project of its own (cf. deferred #122). A per-model override is
   a possible v0.2 if demand emerges.
2. **`assert:` model tests** — *spec'd in v0.1, implemented later.*
   Including the block now keeps the YAML contract stable (avoids a
   future breaking schema change). Minimal set: `not_null`, `unique`,
   `geometry_valid`, `expect_rows`. Each compiles to a SQL check or a
   Python check.
3. **File naming** — resolved as above: `triggers.yaml` forever,
   `gispulse.yaml` canonical for v2.

## Consequences

### Positive

- One declarative manifest; DAG gives lineage, partial re-run, and
  incremental materialization (the "spatial CDC" endgame).
- ETL/ELT dispatch becomes a per-node, inspectable decision.
- No breaking change: v1 configs and `rules` JSON keep working.

### Negative

- Requires a **dialect-aware SQL generation layer** as a hard
  prerequisite (replacing today's inline f-strings in the 4 existing
  strategies). The SQL-ability audit found 5 DuckDB/PostGIS divergences
  (WKB vs native geometry, styled `ST_Buffer`, KNN `<->`, `ST_Transform`
  axis order, topology coverage).
- 36 ETL-strict capabilities will never join the ELT path — a
  structural limit, not an effort gap.

### Prerequisite

The dialect-aware SQL layer + a cross-engine result-equivalence test
harness must land **before** the `model` engine code. Spec-first,
because the YAML contract is an irreversible public API.

## Status of related work

- SQL-ability audit (2026-05-18): 124 capabilities, 4 ELT-native /
  58 ELT-able / 26 ETL-only / 36 ETL-strict. Estimated effort to reach
  "majority ELT": 6–8 weeks (1 dev).
- ADR 0001 (DuckDB-spatial contract dialect) and ADR 0002 (trigger
  cascade semantics) are direct dependencies of this design.
- EPIC + issues to be opened from this ADR.

[strategy]: ../../gispulse/capabilities/strategy.py
[operation_executor]: ../../gispulse/rules/operation_executor.py
[loader]: ../../gispulse/rules/loader.py
[expression_parser]: ../../gispulse/dsl/expression_parser.py

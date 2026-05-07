# ADR 0001 — DuckDB-spatial is the contract SQL dialect of the DSL

**Status:** Accepted
**Date:** 2026-05-07
**Deciders:** GISPulse maintainers
**Issue:** [#140](https://github.com/imagodata/gispulse/issues/140) (Q1 of EPIC [#139](https://github.com/imagodata/gispulse/issues/139))

## Context

`triggers.yaml` exposes two SQL surfaces to the user:

1. **Whitelisted geom functions** in `set_field` and `validate:` rules
   (`geom_area_m2()`, `geom_within(layer, …)`, …). These compile down to
   SQL via `gispulse/dsl/geom_fcts.py`.
2. **Free-form SQL** in `run_sql` actions (and the `predicate` field of
   `change` triggers). The user writes the SQL; GISPulse forwards it to
   the active engine.

The active engine can be **gpkg** (SQLite + SpatiaLite via geopandas),
**duckdb** (DuckDB-spatial), or **postgis** (PostgreSQL/PostGIS) — see
[`docs-site/guide/engines.md`][engines]. Each engine ships a different
ST_* surface:

| Engine | ST_* surface | `ST_Transform` arity |
|---|---|---|
| DuckDB-spatial | ~140 functions (matches PostGIS naming) | 4-arg `(geom, src, target, always_xy)` |
| PostGIS 3.x | ~300 functions, broader coverage | 2-arg `(geom, target_srid)` |
| SpatiaLite | ~150 functions, partial coverage | 2-arg, plus engine-specific helpers |

A `triggers.yaml` written against PostGIS today (e.g.
`SELECT ST_Transform(geom, 4326)` in `run_sql`, or use of
`geography(...)`) silently breaks when the same file routes through the
gpkg/DuckDB engines — and vice versa.

## Decision

**DuckDB-spatial is the contract dialect of the DSL.**

- Whitelisted geom functions in [`gispulse/dsl/geom_fcts.py`][geom_fcts]
  emit DuckDB-spatial SQL (`ST_Transform(geom, src, target, always_xy)`)
  by construction. They are not portable to PostGIS / SpatiaLite as-is.
- `run_sql` actions and `change` predicates **MUST be written in
  DuckDB-spatial dialect** unless the file declares `engine:` to
  override the default routing.
- The `engine:` top-level key in `GISPulseConfig` is the documented
  escape hatch for users who run exclusively against PostGIS or
  SpatiaLite. When set, the user owns the dialect contract and the DSL
  geom functions may produce engine-incompatible SQL.

We picked option **(a) — single portable surface** over options
**(b) — runtime transpilation** and **(c) — per-rule `engine:` tag**:

- **(b)** would mean shipping `sqlglot` (or equivalent) and maintaining
  per-engine pretty-printers. Cost: a third-party dependency, a
  permanent test matrix, and a class of subtle bugs (CTE handling,
  geography vs. geometry, NULL semantics). Reward: portability that
  almost no GISPulse user has asked for.
- **(c)** would push complexity onto every rule author. The intended
  primary user is a QGIS power-user with a GPKG, not a DBA polyglot.

DuckDB-spatial is also the largest portable ST_* surface available
without a server: ~140 functions, native GeoPackage reader, and
embeddable in the wheel (cf. v1.6.0 lazy install of the spatial
extension). It is the natural single dialect for the rule-author tier.

## Consequences

### Positive

- One dialect to document, test, and teach.
- DSL geom functions stay simple — no template-per-engine matrix.
- Loader can introduce a single allow-list of portable function names
  and warn on the rest (follow-up).

### Negative

- Users with an existing PostGIS-only stack cannot copy-paste
  `triggers.yaml` between deployments without reading the migration
  notes (`engine: postgis` + run_sql adapted).
- DuckDB ST_* surface is smaller than PostGIS. Functions like
  `ST_ClusterWithin` or `ST_3DDistance` have no DuckDB equivalent
  today; rules that need them require `engine: postgis` and lose
  cross-engine portability.

### Mitigations

- Document the portable surface explicitly in
  [`docs-site/guide/dsl-sql-dialect.md`][dsl-sql-dialect].
- Cross-link from
  [`docs-site/guide/dsl-geom-functions.md`][dsl-geom-functions] and
  [`docs-site/guide/engines.md`][engines] so users discover the
  contract before writing `run_sql`.
- Follow-up issue: parser pass that scans `run_sql` for known
  PostGIS-only constructs (`geography(`, `ST_Transform(geom, INTEGER)`
  signature, …) and warns when no `engine:` override is set.

## Status of related work

- v1.6.0 (#129) shipped the geom-function whitelist with DuckDB-spatial
  templates — already aligned with this ADR.
- `engine:` field in `GISPulseConfig` already exists (introduced
  v1.6.x #115). No code change needed for this ADR.
- The follow-up loader-time `run_sql` scanner is tracked in
  [#146](https://github.com/imagodata/gispulse/issues/146) so this ADR
  can land doc-only.

[geom_fcts]: ../../gispulse/dsl/geom_fcts.py
[engines]: ../../docs-site/guide/engines.md
[dsl-geom-functions]: ../../docs-site/guide/dsl-geom-functions.md
[dsl-sql-dialect]: ../../docs-site/guide/dsl-sql-dialect.md

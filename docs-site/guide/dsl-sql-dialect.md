---
title: SQL dialect contract
description: GISPulse triggers.yaml is written in DuckDB-spatial dialect by default. Other engines require an explicit override.
---

# SQL dialect contract

GISPulse routes the SQL inside your `triggers.yaml` through one of three
spatial engines: **gpkg** (default Community), **duckdb** (Community
acceleration), or **postgis** (Pro). They expose different `ST_*`
surfaces and different argument signatures. To keep rule files portable
between deployments, GISPulse declares a single contract dialect.

> **TL;DR — write your DSL expressions and `run_sql` strings in
> DuckDB-spatial dialect.** Set `engine: postgis` (or `engine: sqlite`)
> at the top of your `triggers.yaml` only if you accept losing
> cross-engine portability.

The decision is recorded in [ADR 0001 — DuckDB-spatial is the contract
SQL dialect of the DSL](https://github.com/imagodata/gispulse/blob/main/docs/adr/0001-dsl-sql-dialect.md).

## What this means in practice

### Geom functions (`set_field`, `validate:` rules)

The whitelisted geom functions documented in
[DSL geom functions](./dsl-geom-functions.md) are compiled to
DuckDB-spatial SQL by construction. They will **not** evaluate
correctly on a PostGIS engine without first transforming the templates
yourself.

Portable across engines = ❌ for now. Use `geom_*()` only when the
runtime resolves to **gpkg** (auto via `*.gpkg`) or **duckdb**.

### Free-form SQL in `run_sql` actions

`run_sql` forwards your SQL string to the active engine verbatim. The
**default contract** is DuckDB-spatial:

```yaml
- type: run_sql
  sql: |
    UPDATE parcels
    SET surface_m2 = ST_Area(ST_Transform(geom, 'EPSG:4326', 'EPSG:2154', true))
    WHERE fid = ?
```

The 4-argument `ST_Transform(geom, source_crs, target_crs, always_xy)`
is DuckDB-spatial syntax. PostGIS uses the 2-argument form. SpatiaLite
ditto. If you write the 2-arg form, your rule will only work when the
file is routed through PostGIS / SpatiaLite — and you must declare it.

### Engine override

The `engine:` top-level key opts you out of the default contract:

```yaml
version: 1
gpkg: postgresql://gispulse:secret@db.internal/gispulse
engine: postgis
triggers:
  - name: parcels_set_surface
    table: parcels
    when: [INSERT, UPDATE_GEOM]
    actions:
      - type: run_sql
        sql: |
          UPDATE parcels SET surface_m2 = ST_Area(ST_Transform(geom, 2154))
          WHERE fid = ?
```

When `engine:` is set explicitly:

- The DSL **geom functions are not guaranteed to compile**. They emit
  DuckDB-spatial templates and may produce SQL the target engine
  rejects (notably `ST_Transform` arity).
- Your `run_sql` and `predicate` strings may use the dialect of the
  declared engine.
- You own the cross-deployment portability story — if you also want
  the file to run on a GPKG/DuckDB stack, keep a separate variant.

## Portable function surface

The following SQL fragments are safe across all GISPulse engines (they
appear in every dialect with identical semantics for our use cases):

```text
ST_AsText, ST_AsBinary, ST_GeomFromText, ST_GeomFromWKB,
ST_X, ST_Y, ST_StartPoint, ST_EndPoint,
ST_Centroid, ST_PointOnSurface,
ST_Area, ST_Length, ST_Perimeter,
ST_Intersects, ST_Within, ST_Contains, ST_Crosses, ST_Overlaps,
ST_Touches, ST_Disjoint, ST_Equals,
ST_Distance, ST_DWithin,
ST_Buffer, ST_Intersection, ST_Union, ST_Difference, ST_SymDifference,
ST_Envelope, ST_ConvexHull, ST_Boundary,
ST_GeometryType, ST_IsValid, ST_IsEmpty, ST_NPoints, ST_SRID,
ST_Simplify, ST_SimplifyPreserveTopology
```

Use these in `run_sql` and you can move between engines by changing
only `engine:` (and the `gpkg:` URI). Anything outside this list is
engine-specific by default — check the active engine's documentation.

## Known gotchas

### `ST_Transform` arity

| Engine | Signature |
|---|---|
| DuckDB-spatial | `ST_Transform(geom, src_crs::TEXT, tgt_crs::TEXT, always_xy::BOOL)` |
| PostGIS | `ST_Transform(geom, target_srid::INTEGER)` |
| SpatiaLite | `ST_Transform(geom, target_srid::INTEGER)` |

If you write a portable rule, transform the geometry **outside** of
the SQL via the `geom_*()` DSL helpers and avoid hand-rolled
`ST_Transform`.

### Geography vs. geometry

PostGIS distinguishes `geography(...)` and `geometry(...)` types.
DuckDB-spatial and SpatiaLite expose only `geometry`. Casting to
`geography` is **PostGIS-only** — keep it inside an
`engine: postgis` file.

### Index hints

DuckDB infers spatial indices from RTree extensions automatically.
PostGIS requires explicit `CREATE INDEX ... USING GIST(...)` and you
may want to write `WHERE geom && bbox` to leverage it. SpatiaLite uses
`SpatialIndex` virtual tables. None of these hints transfer.

### Date / time literals

DuckDB and PostGIS both accept ISO 8601 (`'2026-05-07'`). SQLite
accepts the same string but stores it as TEXT, not a typed date —
`<` comparisons may surprise you. Use `julianday()` (SQLite) or
`EXTRACT(epoch FROM ...)` (DuckDB / PostGIS) for arithmetic.

## Roadmap

A loader-time scanner that warns when `run_sql` references a
PostGIS-only construct without an `engine: postgis` override is
tracked in [issue #146](https://github.com/imagodata/gispulse/issues/146).
Today the contract is documentary — errors surface at execution time,
not at config-load time.

## See also

- [DSL geom functions](./dsl-geom-functions.md) — the whitelist that
  always compiles to DuckDB.
- [Spatial engines](./engines.md) — when each engine kicks in.
- [DSL validation rules](./dsl-validation.md) — `validate:` syntax.
- [ADR 0001](https://github.com/imagodata/gispulse/blob/main/docs/adr/0001-dsl-sql-dialect.md)
  — full decision record.

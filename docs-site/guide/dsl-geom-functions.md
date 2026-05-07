---
title: DSL geom functions
description: Whitelisted geometry functions exposed by the GISPulse DSL — area, perimeter, centroid, validity, point count.
---

# DSL geom functions

The GISPulse DSL exposes a small whitelist of geometry functions you can
call from your `triggers.yaml`. Each function compiles down to a DuckDB
spatial expression, runs against the dataset's geometry column, and
returns a typed value usable in `set_field` or `validate:` rules.

The compiler never executes user-supplied Python — expressions are
parsed via Python's `ast` module and rejected unless every node fits
the strict allowlist. See [DSL design notes](./dsl-design.md) for the
gory details.

> **Dialecte SQL** : ces fonctions compilent vers DuckDB-spatial par
> construction (4-arg `ST_Transform`). Voir [Contrat de dialecte
> SQL](./dsl-sql-dialect.md) pour les détails sur la portabilité
> PostGIS / SpatiaLite et le mécanisme d'override `engine:`.

## Quick reference

| Function | Returns | CRS-aware | Default `epsg=` | DuckDB call |
|---|---|---|---|---|
| `geom_area_m2()` | double (m²) | yes | `EPSG:2154` | `ST_Area(ST_Transform(geom, src, target, true))` |
| `geom_perimeter_m()` | double (m) | yes | `EPSG:2154` | `ST_Perimeter(...)` |
| `geom_length_m()` | double (m) | yes | `EPSG:2154` | `ST_Length(...)` |
| `geom_centroid_x()` | double | yes | `EPSG:2154` | `ST_X(ST_Transform(ST_Centroid(geom), …))` |
| `geom_centroid_y()` | double | yes | `EPSG:2154` | `ST_Y(ST_Transform(ST_Centroid(geom), …))` |
| `geom_npoints()` | integer | no | — | `ST_NPoints(geom)` |
| `geom_is_valid()` | boolean | no | — | `ST_IsValid(geom)` |

## Examples

### Auto-fill the surface in hectares

```yaml
version: 1
gpkg: ./data/parcels.gpkg
triggers:
  - name: parcels_set_surface
    table: parcels
    when: [INSERT, UPDATE_GEOM]
    actions:
      - type: set_field
        field: surface_ha
        value: "geom_area_m2() / 10000"
```

The compiler emits the SQL fragment
`(ST_Area(ST_Transform("geom", 'EPSG:4326', 'EPSG:2154', true)) / 10000)`,
which DuckDB pushes down on the underlying GeoPackage.

### Tag invalid geometries

```yaml
version: 1
gpkg: ./data/parcels.gpkg
validate:
  - id: shape_valid
    rule: "geom_is_valid()"
    mode: tag
    tag_field: validation_status
    message: "Geometry self-intersects"
```

Failing rows are tagged with `validation_status = 'failed:shape_valid'`
without touching the rest of the row.

### Override the metric CRS per call

```yaml
- type: set_field
  field: distance_to_origin_m
  value: "geom_centroid_x(epsg='EPSG:3857')"
```

## CRS handling

Measure functions (`geom_area_m2`, `geom_perimeter_m`, `geom_length_m`)
project the geometry to a metric CRS before calling DuckDB's `ST_Area`
/ `ST_Perimeter` / `ST_Length`. The default target is **`EPSG:2154`**
(Lambert 93) — the right pick for FR cadastre / topo data. Override
per-call with `epsg='EPSG:NNNN'`, or change the per-dataset default in
your trigger runtime configuration.

The dataset's source CRS is never inferred from the file. The runtime
reads it from the GeoPackage's `gpkg_geometry_columns.srs_id` (or the
PostGIS `geometry_columns` row); you pass it through to the compiler
via `CompilationContext.source_epsg`.

## Allowed expression grammar

Inside a `set_field` value or a `validate:` `rule:` you may write:

- Integer / float / boolean literals (`50`, `1.2`, `True`).
- Column references — bare identifiers like `price`, `tax_rate`. Names
  must match `[A-Za-z_][A-Za-z0-9_]{0,62}` and SQL reserved words are
  rejected (`SELECT`, `FROM`, …).
- Arithmetic: `+ - * / %` plus parentheses.
- Calls to whitelisted geom functions only (the seven above). Bare
  references to a function name are rejected — call them with `()`.
- Inside `validate:` rules, also: comparisons (`==` `!=` `<` `<=` `>`
  `>=`) and boolean ops (`and`, `or`, `not`).

Everything else is rejected at config-load time:

- Method calls (`geom.area()`), attribute access (`row.x`), indexing
  (`a[0]`), slicing.
- Lambdas, comprehensions, f-strings, list/dict/set literals.
- `__import__`, `eval`, `globals`, any function not on the whitelist.
- The `**` exponent (use repeated multiplication if you really need it).

This is by design: the parser exists to give you derived columns and
declarative validation, not a sandbox for arbitrary computation. If
you need that, use a `run_sql` action with a hand-written SQL statement
that lives under your security review.

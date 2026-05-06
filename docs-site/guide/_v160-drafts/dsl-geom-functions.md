---
title: DSL geometric functions
description: The 7 push-down geometric functions available in triggers.yaml — area, perimeter, length, centroid, npoints, is_valid.
---

# DSL geometric functions

GISPulse v1.6 ships a curated whitelist of **geometric functions** that can be used directly in `set_field` actions and `validate` rules. They compile to DuckDB `ST_*` calls and run push-down — no `run_sql` boilerplate, no Python `eval`.

## The whitelist

| Function | Returns | CRS-aware | Use in `set_field` | Use in `validate` |
|---|---|---|---|---|
| `geom_area_m2()` | `DOUBLE` (m²) | ✅ | ✅ | ✅ |
| `geom_perimeter_m()` | `DOUBLE` (m) | ✅ | ✅ | ✅ |
| `geom_length_m()` | `DOUBLE` (m) | ✅ | ✅ | ✅ |
| `geom_centroid_x(epsg=...)` | `DOUBLE` | ✅ | ✅ | ❌ |
| `geom_centroid_y(epsg=...)` | `DOUBLE` | ✅ | ✅ | ❌ |
| `geom_npoints()` | `INTEGER` | — | ✅ | ✅ |
| `geom_is_valid()` | `BOOLEAN` | — | ✅ | ✅ |

CRS handling — the three metric functions auto-pick a metric projection based on the source CRS. Override via `epsg='EPSG:2154'` if you need a specific Lambert.

## Reference

### `geom_area_m2()`

Compute the surface area of a polygon in square meters, regardless of the source CRS.

**Signature** : `geom_area_m2(epsg=auto)`
- `epsg` *(optional)* — explicit metric EPSG. Default : auto-pick from the centroid (Lambert 93 in France, Web Mercator otherwise).

**Compiles to** :
```sql
ST_Area(ST_Transform(geom, source_epsg, target_epsg))
```

**Example** :
```yaml
triggers:
  - table: parcels
    when: [INSERT, UPDATE_GEOM]
    actions:
      - type: set_field
        field: surface_ha
        value: "geom_area_m2() / 10000"   # hectares
```

**Limitations** — auto-pick uses a hard-coded country mapping (FR → 2154, world → 3857). For high-latitude geometries (>60°N), pass `epsg='EPSG:3035'` (LAEA Europe) explicitly.

---

### `geom_perimeter_m()`

Compute the perimeter of a polygon (or multi-polygon) in meters.

**Signature** : `geom_perimeter_m(epsg=auto)`

**Compiles to** :
```sql
ST_Perimeter(ST_Transform(geom, source_epsg, target_epsg))
```

**Example** :
```yaml
- type: set_field
  field: ratio_compacite
  value: "(4 * 3.14159 * geom_area_m2()) / (geom_perimeter_m() * geom_perimeter_m())"
```

---

### `geom_length_m()`

Compute the length of a line geometry in meters. Returns `0` for non-line geometries.

**Signature** : `geom_length_m(epsg=auto)`

**Example** :
```yaml
triggers:
  - table: roads
    when: [INSERT, UPDATE_GEOM]
    actions:
      - type: set_field
        field: longueur_km
        value: "geom_length_m() / 1000"
```

---

### `geom_centroid_x(epsg=...)` / `geom_centroid_y(epsg=...)`

Extract the X / Y coordinate of the geometry's centroid in the requested CRS.

**Signature** :
- `geom_centroid_x(epsg='EPSG:4326')`
- `geom_centroid_y(epsg='EPSG:4326')`

`epsg` is **required** — there is no metric default for centroids (the user choice between WGS84 degrees vs. local Lambert is too project-specific to auto-pick).

**Compiles to** :
```sql
ST_X(ST_Centroid(ST_Transform(geom, source_epsg, target_epsg)))
ST_Y(ST_Centroid(ST_Transform(geom, source_epsg, target_epsg)))
```

**Example** :
```yaml
- type: set_field
  field: lat
  value: "geom_centroid_y(epsg='EPSG:4326')"
- type: set_field
  field: lon
  value: "geom_centroid_x(epsg='EPSG:4326')"
```

---

### `geom_npoints()`

Return the number of vertices in the geometry (sum across all parts for multi-geometries).

**Signature** : `geom_npoints()`

**Compiles to** :
```sql
ST_NPoints(geom)
```

**Use case** — flag oversampled geometries before Douglas-Peucker simplification :

```yaml
validate:
  - id: too_many_vertices
    rule: "geom_npoints() <= 1000"
    mode: warn
    message: "Vertex count > 1000, consider simplifying"
```

---

### `geom_is_valid()`

Test whether the geometry passes OGC simple-features validity (no self-intersection, no orphan rings, etc).

**Signature** : `geom_is_valid()`

**Compiles to** :
```sql
ST_IsValid(geom)
```

**Use case** — block invalid commits at validation time :

```yaml
validate:
  - id: ogc_validity
    rule: "geom_is_valid()"
    mode: tag
    tag_field: validation_status
    message: "Invalid geometry — see ST_IsValidReason"
```

## Mini-expression arithmetic

Geometric functions can be combined with **arithmetic operators** in expressions :

| Operator | Effect |
|---|---|
| `+` `-` `*` `/` | Standard numeric ops |
| `%` | Modulo |
| `( )` | Grouping |

Constants are numeric (int, float). Column references use the column name directly (`price`, `tax_rate`).

**No** function chaining (you cannot pass one geom function's output as input to another), **no** Python `eval`, **no** custom user functions. The surface area is intentionally small.

**Examples** :
```yaml
- type: set_field
  field: surface_ha
  value: "geom_area_m2() / 10000"

- type: set_field
  field: density_per_km2
  value: "(population * 1000000) / geom_area_m2()"

- type: set_field
  field: total_with_tax
  value: "price * (1 + tax_rate / 100)"
```

The parser rejects anything outside the whitelist with a `DSLValidationError` pointing at the offending token.

## Cross-source lookup — `layer_lookup`

While not a "geometric function" per se, `layer_lookup` belongs in the same DSL family. It lets you fetch a value from another layer via spatial or attribute match :

```yaml
- type: set_field
  field: code_commune
  value: "layer_lookup(layer='communes', match='spatial_within', take='code_insee')"
```

Match modes :
- `spatial_within` — current geom inside the lookup geom (`ST_Within`)
- `spatial_intersects` — any overlap (`ST_Intersects`)
- `attribute='self.col=layer.col'` — attribute join (no spatial check)

DuckDB `ATTACH` federates the lookup across formats — local GPKG can lookup into a remote PostGIS table in the same expression.

## Validation-only functions

Two extra functions are available in `validate:` rules but **not** in `set_field` (their output is boolean and they're cross-source) :

### `geom_within(layer, match=...)`

Verify the current feature falls within a feature in another layer, optionally constrained by attribute match.

```yaml
validate:
  - id: in_correct_commune
    rule: "geom_within(layer='communes', match='code_insee')"
    mode: tag
    tag_field: validation_status
```

The `match` clause says : "the feature must be `ST_Within` a polygon in `communes` whose `code_insee` matches the current feature's `code_insee`".

### `geom_overlaps_any(layer, exclude_self=true)`

Detect overlap with any other feature in a layer, optionally excluding the current feature itself.

```yaml
validate:
  - id: no_overlap_with_neighbours
    rule: "NOT geom_overlaps_any(layer='self', exclude_self=true)"
    mode: warn
    message: "Parcel overlaps a neighbouring parcel"
```

## See also

- [Architecture](./architecture.md) — why these functions push down to DuckDB
- [Choose your engine](./engines.md) — engine selection for federated lookups

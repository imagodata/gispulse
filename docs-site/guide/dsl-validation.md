---
title: DSL validation rules
description: The `validate:` top-level key — declarative rules with warn/tag modes for spatial data quality.
---

# Declarative validation

`triggers.yaml` accepts a top-level `validate:` key that lists rules
the runtime evaluates on every INSERT and UPDATE event. A rule is just
a boolean DSL expression plus a mode telling the runtime what to do
when the rule fails.

## Schema

```yaml
version: 1
gpkg: ./data/parcels.gpkg
validate:
  - id: surface_min
    rule: "geom_area_m2() >= 50"
    mode: warn
    message: "Parcel surface < 50 m²"

  - id: shape_valid
    rule: "geom_is_valid()"
    mode: tag
    tag_field: validation_status
    message: "Geometry self-intersects"
```

| Field | Required | Notes |
|---|---|---|
| `id` | yes | Stable identifier used in log lines and tag values. |
| `rule` | yes | Boolean DSL expression — see [DSL geom functions](./dsl-geom-functions.md). |
| `mode` | no (default `warn`) | `warn` logs and broadcasts. `tag` writes the failure on the row. |
| `tag_field` | only when `mode: tag` | Column receiving `failed:<id>` on failure. |
| `message` | no | Human-readable detail attached to the log / WS event. |
| `enabled` | no (default `true`) | Toggle without removing the rule. |

## Modes

### `mode: warn`

The default. Emits a structured log line and a `validation.failed`
event over the runtime's event hub. Use this when downstream
consumers (dashboards, alerts) need to know about failures but the
data should keep flowing untouched.

### `mode: tag`

The runtime calls a `tag_field` action that writes `failed:<rule.id>`
into the column you point at via `tag_field:`. This is the right
default when QGIS / portal clients need to render bad rows in red, or
when a downstream pipeline filters on a status column. The column is
auto-created on first use; subsequent failures of the same rule
overwrite the value.

> **Note (v1.6.0 scope):** the validation runner that picks up
> `validate:` rules at runtime ships with v1.6.0 — every INSERT and
> UPDATE event triggers a per-rule evaluation against the row, and
> failures land in the log + a `validation.failed` broadcast over the
> event hub.
>
> The `mode: tag` dispatch (column auto-create + `tag_field` action
> emit) is wired in two places independently: the schema accepts it
> and the `tag_field` action dispatcher knows how to write it. The
> bridge that connects a failing `mode: tag` rule to an automatic
> `tag_field` action emit is the last mile and tracked as a follow-up.
> Until it lands, `mode: tag` configs degrade to `mode: warn`
> semantics — log + WS event, no row mutation. Track
> [#123](https://github.com/imagodata/gispulse/issues/123) and the
> validation-runner-in-watcher follow-up for status.

## Cross-source validation (preview)

Two cross-source helpers are planned for the v1.6.x line:

```yaml
validate:
  - id: in_known_commune
    rule: "geom_within(layer='communes', match='code_insee')"
    mode: warn

  - id: no_overlap
    rule: "not geom_overlaps_any(layer='self', exclude_self=true)"
    mode: tag
    tag_field: validation_status
```

Both compile to a DuckDB sub-query and require the engine to attach
the cross-source layer; the runtime piece is tracked in
[#122](https://github.com/imagodata/gispulse/issues/122). The schema
side already accepts the syntax so you can prepare your YAML now.

## Migration from ESRI Attribute Rules

If you are coming from ESRI's Attribute Rules, see
[Migrating from ESRI](./migration-from-esri.md) for a side-by-side
table of `kind:` aliases and rule equivalents.

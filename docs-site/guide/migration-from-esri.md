---
title: Migrating from ESRI Attribute Rules
description: Mapping ESRI Attribute Rules → GISPulse triggers.yaml — kinds, triggering events, and the pieces that don't translate.
---

# Migrating from ESRI Attribute Rules

ESRI's Attribute Rules and GISPulse's `triggers.yaml` solve the same
problem (declarative reactions on edits) with different vocabularies.
This page walks you through the renames and the cases that need
rethinking when you bring an Attribute Rules file across.

## TL;DR

| ESRI concept | GISPulse equivalent | Notes |
|---|---|---|
| `type: constraint` | `kind: constraint` (alias of `validation`) | use `validate:` rule with `mode: tag` to land in the row |
| `type: calculation` | `kind: calculation` (alias of `trigger`) | trigger with a `set_field` action |
| `type: validation` | `kind: validation` (native) | trigger with a `validate:` rule |
| `triggeringEvents: ["Insert"]` | `when: [INSERT]` | identical semantics |
| `triggeringEvents: ["Update"]` | `when: [UPDATE]` (catch-all) | use `UPDATE_GEOM` / `UPDATE_ATTR` for granular dispatch |
| `triggeringEvents: ["Delete"]` | `when: [DELETE]` | identical |
| `evaluationOrder: 1` | not yet — order = YAML order | tracked in v1.7+ |
| Arcade expression | DSL geom functions + arithmetic | see [DSL geom functions](./dsl-geom-functions.md) |
| `subtype: 1` filter | `predicate: "subtype = 1"` | DSL predicate |

## kind: aliases

The `kind:` field on a trigger is optional — if omitted, the trigger
defaults to the GISPulse-native `trigger` kind. ESRI users can keep
their vocabulary by spelling out the alias:

```yaml
triggers:
  - name: parcels_constraint_min_surface
    kind: constraint           # alias of "validation"
    table: parcels
    when: [INSERT, UPDATE_GEOM]
    actions: [...]
```

| Alias | Maps to | When to use it |
|---|---|---|
| `constraint` | `validation` | The rule rejects or tags rows that fail a check (ESRI "constraint" type) |
| `calculation` | `trigger` | The rule writes a derived attribute (ESRI "calculation" type) |
| `validation` | `validation` | Native — same as ESRI |
| `trigger` | `trigger` | Native default for GISPulse |

The alias is metadata only today: it does not change runtime
behaviour. The GISPulse runtime decides what to do based on the
trigger's `actions:` (and on a top-level `validate:` block); the
`kind:` label is there to help operators read the YAML and to keep
your migration diff small.

## Triggering events: granular UPDATE

ESRI fires `Update` whenever any field changes. GISPulse v1.6.0
introduces `UPDATE_GEOM` and `UPDATE_ATTR` so you can react only to
geometry edits or only to attribute edits:

```yaml
when: [UPDATE_GEOM]   # only when the geometry actually changed
when: [UPDATE_ATTR]   # only when only attributes changed
when: [UPDATE]        # catch-all (= UPDATE_GEOM ∪ UPDATE_ATTR), v1.5.x-compatible
```

The runtime resolves a coarse `UPDATE` change-log row to one of the
granular variants by reading the `geom_changed` flag the GeoPackage
trigger captured at edit time. Existing v1.5.x configs keep working
because `UPDATE` is still accepted and expanded internally.

`BULK` is a fourth value used by the watcher when many rows arrive at
once (paste from QGIS, CSV import). Triggers that need to run a
different code path on bulk events can declare `when: [BULK]`.

## Arcade vs the GISPulse DSL

The GISPulse DSL is intentionally smaller than Arcade. You get
geometry helpers (`geom_area_m2`, `geom_is_valid`, …), arithmetic, and
boolean/comparison operators inside `validate:` rules. You do **not**
get `IIf`, lookups by `OID`, schema mutations, or arbitrary script
execution — those land you in `run_sql` territory, where the YAML
ships a hand-written SQL fragment that goes through your security
review.

Cross-source lookups (`layer_lookup`, `geom_within`,
`geom_overlaps_any`) are coming in the v1.6.x line; track
[#122](https://github.com/imagodata/gispulse/issues/122) and
[#124](https://github.com/imagodata/gispulse/issues/124) for status.

## What does not translate

| Feature | Status | Workaround |
|---|---|---|
| Subtypes | not modelled | use `predicate:` to filter rows |
| Domains | not modelled | use a `validate:` rule that compares against a list |
| Editor tracking fields | not auto-managed | add `set_field` actions for `last_edited_user` etc. |
| Attribute Rules error codes | not modelled | use `validate:` `id:` + `message:` + a tag column |
| Asynchronous batch evaluation | not the GISPulse model | rules fire immediately on the change-log tail |

## Checklist for a clean migration

1. Export your Attribute Rules to JSON via `arcpy.management.ExportAttributeRules`.
2. For each rule, decide whether it belongs in `triggers:` (mutates
   the row, ESRI `calculation`) or `validate:` (just checks, ESRI
   `constraint` or `validation`).
3. Translate the Arcade expression: simple math + geom checks map
   directly; anything Arcade-only goes into a `run_sql` action.
4. Set `kind:` to the ESRI vocabulary if it helps your team read the
   YAML — it is purely cosmetic.
5. Run `gispulse triggers run --dry` to see the compile output before
   wiring it to a live dataset.

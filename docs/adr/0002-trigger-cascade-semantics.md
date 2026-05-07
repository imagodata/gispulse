# ADR 0002 — Trigger cascade is bounded fixed-point with origin-tagging

**Status:** Accepted (records existing design)
**Date:** 2026-05-07
**Deciders:** GISPulse maintainers
**Issue:** [#142](https://github.com/imagodata/gispulse/issues/142) (Q3 of EPIC [#139](https://github.com/imagodata/gispulse/issues/139))

## Context

Trigger A modifies a row that trigger B is also subscribed to. Two
questions arise:

1. **Within the same poll cycle**, does B see A's write?
2. **What stops infinite loops** when A and B mutually trigger each
   other?

Issue #142 originally framed the choice as **single-pass vs. fixed-point**,
but the existing v1.6.0 codebase already ships a third design that
neither label captures cleanly: **bounded fixed-point with
origin-tagging at the SQLite trigger DDL layer**. This ADR records
that design so future contributors don't try to reinvent it.

## Decision

GISPulse uses a **two-layer cascade control**:

### Layer 1 — origin-tagging at the SQLite trigger DDL

Every `_gispulse_trg_<table>_update` trigger carries a `WHEN` clause
that suppresses re-fires when the row was last touched by an
`action_dispatcher` write-back (cf. B-02, v1.5.3, #103):

```sql
CREATE TRIGGER "_gispulse_trg_parcels_update"
AFTER UPDATE ON "parcels"
WHEN (NEW."_gispulse_origin" IS NULL
      OR NEW."_gispulse_origin" NOT LIKE 'trigger:%')
  AND NOT (
    NEW."_gispulse_origin" IS NULL
    AND OLD."_gispulse_origin" IS NOT NULL
    AND OLD."_gispulse_origin" LIKE 'trigger:%'
  )
BEGIN
  INSERT INTO _gispulse_change_log ...
END;
```

This breaks **self-loops** at the file format level — the dispatcher
tags rows it writes with `_gispulse_origin = 'trigger:<id>'`, the
SQLite trigger sees that tag and skips the changelog INSERT.

### Layer 2 — bounded fixed-point in the Python evaluator

`rules/trigger_evaluator.py` exposes
[`evaluate_cascade()`](../../rules/trigger_evaluator.py) which runs a
fixed-point loop:

```text
depth = 1
while current_records:
    fired = evaluate(current_records, triggers, depth=depth)
    matched = [ft for ft in fired if ft.matched]
    if not matched:
        break
    next_depth = depth + 1
    if next_depth > MAX_CASCADE_DEPTH:
        if next_records_fn(matched):
            raise CascadeDepthExceeded(next_depth)
        break
    current_records = next_records_fn(matched)
    depth = next_depth
```

`MAX_CASCADE_DEPTH = 3`. Beyond that, `CascadeDepthExceeded` is raised
— the runtime fails loudly rather than silently truncating user
intent.

### Tier semantics

| Tier | Cascade depth | Notes |
|---|---|---|
| Community | Capped at 1 (`_LOCAL_TRIGGER_MAX_CASCADE_DEPTH = 1`) | Effectively single-pass. `cascade_depth > 1` rejected at HTTP create/update time. |
| Pro | Up to `MAX_CASCADE_DEPTH = 3` | Full cascade with fail-fast at depth 4. |

`Trigger.cascade_depth` defaults to `1` (`core/models.py:308`), so the
out-of-the-box experience matches Community behaviour.

## Why this layout

Three concerns drove this two-layer design:

1. **Self-loops vs. cross-trigger cascades are different problems.**
   A trigger writing back to its own table is the common foot-gun and
   deserves a zero-cost guard at the DDL level (origin-tagging). Real
   cross-trigger cascades (A inserts into table X → B fires on X) are
   rarer, justified user intent, and worth a Python-side loop.
2. **Determinism over latency.** A pure single-pass design would push
   cascading work to the *next* poll cycle, adding watcher latency.
   Fixed-point with `max_depth = 3` keeps everything in the same
   cycle, observable in one log span, predictable.
3. **Loud failure beats silent truncation.** Raising
   `CascadeDepthExceeded` instead of capping at 3 forces the user to
   either lift the cap (Pro) or re-think their rule. We saw the
   alternative on Beta — silent truncation produced "ghost" non-fires
   that took days to debug.

## Alternatives considered

- **Pure single-pass.** Rejected: pushes cascade work to the next
  poll cycle, breaks the one-cycle observability story, and the
  cascade evaluator was already shipped + tested before #142 opened.
- **Unbounded fixed-point.** Rejected: a self-referential trigger
  (e.g. `set_field` referencing the same field) loops until file
  size or wall-clock kills the watcher. The B-02 origin-tag breaks
  most self-loops, but a 4-trigger cycle (A → B → C → D → A) would
  still slip past it.
- **Per-trigger `max_cascade` knob.** Rejected for v1.6.x — adds
  config surface for a corner case. The tier-level cap (1 vs. 3) is
  a coarser but adequate proxy.

## Consequences

### Positive

- **Determinism.** Same `triggers.yaml` produces the same fired
  sequence on Pattern A (SQLite triggers + watcher poll) and Pattern
  B (DuckDB snapshot diff) — `evaluate_cascade()` is engine-agnostic.
- **Observability.** `FiredTrigger.cascade_depth` is recorded on
  every event, so the watcher dashboard (#95) can chart cascade
  depth distributions per trigger.
- **Self-loops are blocked at the file format level.** A Community
  user editing in QGIS while running a `set_field` trigger never
  sees a runaway loop, even without ever touching cascade depth.

### Negative

- **Two layers to learn.** Contributors must understand both the
  SQLite `WHEN` clause and the Python loop to debug "why didn't my
  cascade fire". Documented here + in [Cascade behaviour](../../docs-site/guide/rules.md#cascade-behaviour-of-triggers).
- **Cap of 3.** A handful of pipelines (multi-step audit cascades)
  may want depth 5-10. Today they must drop to direct
  [`run_sql`](../../docs-site/guide/dsl-sql-dialect.md) chains. Worth
  revisiting if Pro customers ask.

## Status of related work

- v1.6.0 (#129) ships the cascade evaluator unchanged from v1.5.x.
- B-02 (v1.5.3, #103) origin-tagging on AFTER UPDATE was the missing
  piece for self-loop suppression — already merged.
- #142 implementation tasks ("force `PRAGMA recursive_triggers=0`",
  "single-pass on Pattern A and B") are **moot**: the existing design
  is more conservative than single-pass at the SQLite layer
  (origin-tagging) and richer than single-pass at the Python layer
  (bounded fixed-point with fail-fast).

## See also

- `rules/trigger_evaluator.py` — `MAX_CASCADE_DEPTH`, `evaluate_cascade`
- `persistence/gpkg_schema.py:_build_change_triggers` — origin-tag DDL
- `gispulse/adapters/http/routers/triggers_router.py` — Community cap
- `tests/unit/test_cascade_depth.py` — depth limiter tests
- `docs-site/guide/rules.md#cascade-behaviour-of-triggers`
- ADR [0001](./0001-dsl-sql-dialect.md) — DSL SQL dialect contract

# ADR 0003 — `_gispulse_change_log` is a poll log, not an event store

**Status:** Accepted
**Date:** 2026-05-07
**Deciders:** GISPulse maintainers
**Issue:** [#143](https://github.com/imagodata/gispulse/issues/143) (Q4 of EPIC [#139](https://github.com/imagodata/gispulse/issues/139))

## Context

Issue #143 asked whether v1.6.x should formalise `_gispulse_change_log`
as an event source — adding stable hashing, sub-second timestamps, and
a `replay_changes()` primitive — to enable time-travel / mirror
replication / audit re-execution.

Investigation of the current schema shows the table already covers more
of an event source than the issue brief assumed. Today's columns
([`persistence/schema.py`][schema]):

```text
id              INTEGER PRIMARY KEY AUTOINCREMENT   -- stable global seq
table_name      TEXT NOT NULL
operation       TEXT NOT NULL                       -- INSERT/UPDATE/DELETE
row_pk          TEXT
old_values      TEXT                                -- JSON of OLD.*
new_values      TEXT                                -- JSON of NEW.*
changed_at      TEXT DEFAULT (datetime('now'))      -- ISO 8601 second-precision
processed       INTEGER DEFAULT 0
geom_changed    INTEGER DEFAULT 0
```

So we already have:

- ✅ Append-only (the trigger DDL only INSERTs; no UPDATE / DELETE on
  this table from inside GISPulse).
- ✅ Stable global ordering (`id AUTOINCREMENT` is monotonic
  per-database).
- ✅ Stable timestamps (`changed_at` is ISO 8601 UTC).
- ✅ Full JSON payload of old / new values.

What is missing for a "real" event store:

- ❌ Sub-second timestamp resolution (matters when many events fire in
  the same second on bulk operations).
- ❌ Tamper-evidence (no row hash, no signature chain).
- ❌ Schema-versioning of the JSON payloads (rename a column → replay
  against new schema breaks).
- ❌ A documented `replay_changes()` primitive — there is no API that
  takes a (since_id, until_id) range and reapplies the deltas to a
  fresh GPKG / mirror.

## Decision

**`_gispulse_change_log` stays a poll log.** It is the buffer the
watcher reads; it is *not* a Source-of-Truth event store, and v1.6.x
does not promise it will be one.

This means:

- Public docs describe the table as the watcher's input queue
  ([`docs-site/guide/track.md`][track]) — not as the audit trail of
  truth.
- We commit to **append-only** semantics (we will not break tools that
  treat it as monotonic).
- We do **not** commit to `(id, changed_at)` being suitable for replay
  against a different GPKG file. Re-running deltas against a divergent
  state is undefined.
- Adding sub-second timestamps, hashing, or replay primitives requires
  a new opt-in extension table — it will not silently change the
  current schema.

Why not turn it into an event store now?

1. **No customer ask.** Replay / mirror / time-travel has not appeared
   on Pro lead calls or Beta feedback. Building it on spec adds schema
   churn risk on a column that triggers and runtime depend on.
2. **Schema churn is a watcher hazard.** B-13 (#103) added a
   schema-drift watchdog that hashes layer schemas; touching the
   changelog DDL itself would need its own migration story.
3. **Scope.** v1.6.x ships [DuckDB-spatial DSL][duckdb-pivot] +
   [bounded fixed-point cascade][adr0002] + [WAL connection
   safety][adr-pr-wal]. Adding event sourcing now would inflate the
   release scope past the v1.6.x window.

## Alternatives considered

- **(a) In-scope v1.6.x — full event store.** Rejected: scope creep
  + schema migration + replay primitive + extensive testing for a
  feature with no current demand.
- **(b) In-scope v1.6.x — partial (sub-second timestamp + row hash
  only).** Rejected: half-step that exposes us to schema changes
  without delivering the customer value (replay).
- **(c) Out-of-scope, no future commitment.** Too aggressive — the
  changelog is *already* event-source-ish and customers may eventually
  ask. Better to leave the door open.
- **(d) Out-of-scope v1.6.x, opt-in extension table later (chosen).**
  Future event-source extensions live in a sibling table
  (`_gispulse_change_event_extra` or similar) that links by `id`.
  Keeps the core changelog stable while leaving room for v1.7+
  audit/replay features.

## Consequences

### Positive

- v1.6.x ships without schema churn on the changelog.
- The current `id AUTOINCREMENT` + `changed_at` invariants are
  promoted from "happens to exist" to documented contract.
- Future event-store work has a clear place to live (extension table)
  without breaking existing watchers.

### Negative

- Customers expecting "GISPulse = full audit log" will be disappointed
  until v1.7+. We need to be clear in marketing that this is *change
  capture for triggers*, not *audit trail of record*.

## Status of related work

- v1.6.0 (#129) ships the changelog schema unchanged from v1.5.x.
- B-13 (#103) shipped the schema-drift watchdog — touching the
  changelog DDL would interact with that subsystem and is best
  deferred.
- A v1.7+ "audit & replay" epic should be opened when the first
  customer asks; until then, no work scheduled.
- Issue #143 closed by this ADR.

## See also

- [`persistence/schema.py`][schema] — `change_log` table definition
- [`docs-site/guide/track.md`][track] — user-facing description of the
  poll-log model
- ADR [0001][adr0001] — DSL SQL dialect
- ADR [0002][adr0002] — Cascade semantics

[schema]: ../../persistence/schema.py
[track]: ../../docs-site/guide/track.md
[duckdb-pivot]: ../../docs-site/guide/architecture.md
[adr0001]: ./0001-dsl-sql-dialect.md
[adr0002]: ./0002-trigger-cascade-semantics.md
[adr-pr-wal]: https://github.com/imagodata/gispulse/pull/145

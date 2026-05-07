# ADR 0004 — DDL hooks are out of scope; passive schema-drift detection ships

**Status:** Accepted
**Date:** 2026-05-07
**Deciders:** GISPulse maintainers
**Issue:** [#144](https://github.com/imagodata/gispulse/issues/144) (Q5 of EPIC [#139](https://github.com/imagodata/gispulse/issues/139))

## Context

Issue #144 asked whether GISPulse should expose hooks on DDL operations
— `ALTER TABLE ADD COLUMN`, `DROP TABLE`, `CREATE INDEX` — alongside
the existing DML triggers. The motivation: a user evolves the GPKG
schema in QGIS (add column, rename, drop layer), and rules /
integrations downstream may want to react.

Investigation shows v1.5.3 already ships the relevant *passive*
behaviour via the schema-drift watchdog (B-13, #103,
`persistence/change_log_watcher.py`):

- Every `schema_drift_check_interval_s` (default 5 s) the watcher
  hashes each tracked layer's `PRAGMA table_info(...)`.
- On hash mismatch the watcher rebuilds the DML triggers for that
  layer so newly-added columns appear in `new_values` JSON
  immediately.
- A missing layer (DROP TABLE) drops the cached hash so a re-creation
  is treated as first sighting.

So the DDL story is already 80 % covered: schema mutations propagate
through the existing DML pipeline within one watchdog tick.

## Decision

**Active DDL hooks (e.g. `on_alter_table:`, `on_drop_table:`) are out
of scope.** GISPulse keeps the design where DDL is detected
*passively* by the schema-drift watchdog, never actively trapped at
the SQLite trigger level.

Concretely:

1. The watchdog detection that ships in B-13 stays as the canonical
   "GISPulse noticed a schema change" mechanism.
2. We do not introduce SQLite `CREATE TRIGGER ... ON SCHEMA` (which
   does not exist) nor a sniffer that intercepts `ALTER TABLE`
   statements at the engine layer.
3. We do not add `on_alter_table:` / `on_drop_table:` keys to
   `triggers.yaml`.
4. If a future Pro feature needs DDL hooks (e.g. compliance audit), it
   ships as a sidecar service that diffs the watchdog's hash log — not
   as a core DSL extension.

## Why not active DDL hooks

1. **No SQLite primitive.** SQLite has no DDL trigger surface.
   Implementing one would require parsing `ALTER`/`CREATE`/`DROP`
   statements at the connection layer and intercepting them. This
   crosses every code path that touches the GPKG (CLI, HTTP, plugin),
   and breaks the user's right to mutate the file in QGIS without
   GISPulse running.
2. **Use-case narrow.** The most common GIS schema change — ALTER
   TABLE ADD COLUMN — is already invisible to the user with B-13:
   triggers rebuild and the new column appears in subsequent events.
3. **Active hooks would lie.** A QGIS user editing the file while
   GISPulse is offline would later open it with GISPulse and see
   `on_alter_table` *not fire* — because we never observed the DDL,
   only the resulting state. Passive detection is honest about this:
   "I notice the schema differs from last poll, here is the new
   shape."
4. **Composition with cascade.** Active DDL hooks would fight the
   bounded fixed-point cascade design (cf. ADR 0002) — DDL changes
   trigger structural drift, which is fundamentally different from
   row-level cascading. Mixing them is a footgun.

## Alternatives considered

- **(a) Active hooks via SQL parsing.** Rejected: implementation
  invasive, breaks offline-edits-in-QGIS story.
- **(b) `gispulse track ddl` subcommand to diff schemas on demand.**
  Deferred to Pro-tier audit features; the watchdog already exposes
  the change via runtime events, no new CLI needed.
- **(c) Passive detection only, DDL hooks documented as
  out-of-scope (chosen).** Honest, low-risk, ships today.

## Consequences

### Positive

- No new code surface to maintain.
- B-13 watchdog gets called out as the canonical "schema drift"
  mechanism — discoverable via the doctor (`gispulse track doctor`)
  and observability.
- Aligns with [ADR 0001][adr0001] (DuckDB-spatial dialect contract)
  and [ADR 0003][adr0003] (changelog as poll log) — the rule writer
  has a small, predictable contract surface.

### Negative

- Compliance use cases that need a "DDL audit log" must wait for a
  future Pro feature.
- A user who drops a tracked layer and never reads the watcher logs
  may not notice that tracking silently went dormant — the watcher
  keeps polling but gets no rows. Mitigation: `gispulse track doctor`
  surfaces this state (cf. `_check_pragma`, `_changelog_table_exists`
  in `gispulse/cli_track.py`).

## Status of related work

- B-13 (#103, v1.5.3) — schema-drift watchdog already shipped and
  exercised in tests.
- `gispulse track doctor` (#6) reports stale unprocessed rows + WAL
  + busy_timeout, but does not currently flag dropped tables. A small
  doctor enhancement (file an issue if needed) would close the
  remaining gap.
- Issue #144 closed by this ADR.

## See also

- [`persistence/change_log_watcher.py`][watcher] — `_schema_drift_check_interval_s`
- [`docs-site/guide/architecture.md`][architecture] — passive
  detection in the architecture overview
- ADR [0001][adr0001] — DSL SQL dialect
- ADR [0002][adr0002] — Cascade semantics
- ADR [0003][adr0003] — Changelog scope

[watcher]: ../../persistence/change_log_watcher.py
[architecture]: ../../docs-site/guide/architecture.md
[adr0001]: ./0001-dsl-sql-dialect.md
[adr0002]: ./0002-trigger-cascade-semantics.md
[adr0003]: ./0003-changelog-replay-out-of-scope.md

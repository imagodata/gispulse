---
title: Walkthrough — Audit
description: Trace every modification of a tracked GeoPackage and export a timestamped CSV — for compliance, rollback, or simply understanding who touched what.
---

# Walkthrough — Audit

> **Promise**: every INSERT/UPDATE/DELETE on the GeoPackage is recorded
> with timestamp + OS user + per-attribute diff, exportable as CSV. No
> GIS-client plugin needed.

## What you'll see

A `log_event` rule consumes each row of the SQLite change-log and
appends it to a dedicated audit table (`_gispulse_audit_log`). That
table reads like any other QGIS table, and `gispulse audit export`
produces the matching CSV.

| Before | After a few saves |
|---|---|
| No trace of edits inside the GeoPackage | `_gispulse_audit_log` has one row per DML, attribute by attribute |

## Prerequisites

- QGIS ≥ 3.28
- `gispulse` ≥ 1.5.1 (`pipx install gispulse`)
- The demo pack: `gispulse examples fetch audit`

## Setup (~1 min)

```bash
gispulse track install ~/.gispulse/examples/audit/parcels.gpkg

gispulse triggers watch \
  --rules ~/.gispulse/examples/audit/triggers.yaml \
  --dataset ~/.gispulse/examples/audit/parcels.gpkg
```

The demo pack already ships with the `_gispulse_audit_log` layer and
its schema (`timestamp`, `op_type`, `layer`, `fid`, `user`, `before`,
`after`) — the `log_event` rule only fills it.

## The scenario in 3 steps

### 1. Make a few edits

Open `parcels` in QGIS, toggle edit mode, change two or three
attributes (e.g. `zonage_plu` on two parcels, delete a third), then
**save** (`Ctrl+S`).

### 2. The trigger logs each DML

```text
[info] dml.changed parcels fid=12 op=update
[info] rule:log_event triggered
[info]   → audit row +1 (op=update zonage_plu: UA → AU)
[info] dml.changed parcels fid=34 op=update
[info]   → audit row +1 (op=update zonage_plu: N → AU)
[info] dml.changed parcels fid=58 op=delete
[info]   → audit row +1 (op=delete fid=58 snapshot saved)
```

### 3. Inspect the trace

In QGIS, open the `_gispulse_audit_log` layer and sort by `timestamp
desc`. Each edit shows up with:

| timestamp | op_type | layer | fid | user | before | after |
|---|---|---|---|---|---|---|
| 2026-05-02T14:32:11Z | update | parcels | 12 | simon | `{"zonage_plu":"UA"}` | `{"zonage_plu":"AU"}` |
| 2026-05-02T14:32:11Z | update | parcels | 34 | simon | `{"zonage_plu":"N"}` | `{"zonage_plu":"AU"}` |
| 2026-05-02T14:32:12Z | delete | parcels | 58 | simon | `{...full snapshot...}` | `null` |

### 4. Export the CSV

```bash
gispulse audit export ~/.gispulse/examples/audit/parcels.gpkg \
  --since "2026-05-02T00:00:00Z" \
  --out audit-2026-05-02.csv
```

The CSV can be attached to a council-meeting record, plugged into a PLU
validation workflow, or archived for traceability.

## See the same scenario online

> 🔗 [Try it on `try.gispulse.dev/audit`](https://try.gispulse.dev/audit)

In the portal, every change you make on the map is logged live in the
**Events** panel. The **Download CSV** button exports the same data as
`gispulse audit export`.

## Expected portal output

**Events** panel:

```text
2026-05-02T14:32:11Z  log_event  parcels#12  ok 24ms
2026-05-02T14:32:11Z  log_event  parcels#34  ok 22ms
2026-05-02T14:32:12Z  log_event  parcels#58  ok 31ms
```

**Audit** panel: same table as the one in QGIS, filterable by
`op_type`, `user`, date range.

## Common use cases

- **Rollback**: the `before` column carries the full snapshot for
  `delete` and the diff for `update` — enough to manually replay an
  earlier state.
- **PLU compliance**: exporting the CSV at the time of a permit review
  proves that the zoning version in use matches the GPKG state on that
  exact date.
- **Regression detection**: paired with `gispulse track diff`, the log
  reveals which edit broke a downstream rule.

## What's next?

- [Parcels](/en/guide/walkthroughs/parcels) — sample business rule
  reclassifying features in response to a DML.
- [Isochrone](/en/guide/walkthroughs/isochrone) — heavier rule (network
  computation) fired by the same change-log.
- The [CLI ↔ Portal matrix](/en/guide/symmetry) confirms that `audit
  export` on the CLI side and the **Audit** panel on the portal side
  consume the same table.

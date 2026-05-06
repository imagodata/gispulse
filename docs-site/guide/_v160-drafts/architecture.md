---
title: Architecture
description: How GISPulse reacts to QGIS edits across 10+ GIS formats — DuckDB spatial as the universal compute substrate, PostGIS on-demand for teams.
---

# Architecture

GISPulse is a **declarative spatial CDC engine** that reacts to edits on any GIS format you can open in QGIS — GeoPackage, Shapefile, GeoJSON, FlatGeobuf, KML, MapInfo TAB, GeoParquet, and more — and applies rules defined in a single versionable `triggers.yaml`.

This page explains the moving parts.

## The big picture

```
┌──────────────────────────────────────────────────────────────────┐
│  CLIENT                                                          │
│  QGIS · ogr2ogr · raw SQL · FME · API call                       │
│                                                                   │
│  Edits any of: GPKG · SHP · GeoJSON · KML · FGB · TAB ·          │
│                CSV+WKT · GeoParquet · SpatiaLite · DXF · ...      │
└────────────────────────┬─────────────────────────────────────────┘
                         │ DML detected by per-format adapter
                         ▼
┌──────────────────────────────────────────────────────────────────┐
│  GISPULSE RUNTIME (CLI · API · plugin QGIS · portal)             │
│                                                                   │
│  ┌─ Adapters ────────────────────────────────────────────────┐   │
│  │  SQLite-family (GPKG / SpatiaLite)                        │   │
│  │    → AFTER triggers + _gispulse_change_log table          │   │
│  │  File-blob (SHP / GeoJSON / FGB / KML / TAB / CSV)        │   │
│  │    → mtime watcher + DuckDB diff snapshot                 │   │
│  │  PostGIS (remote, optional Pro)                           │   │
│  │    → LISTEN/NOTIFY + PL/pgSQL triggers                    │   │
│  └────────────────────────────────────────────────────────────┘   │
│                            │                                      │
│                            ▼                                      │
│  ┌─ Compute (DuckDB spatial) ─────────────────────────────────┐   │
│  │  Predicate evaluation · Aggregations · Geometric ops      │   │
│  │  Cross-source spatial joins · Vector tile generation      │   │
│  │  ~140 ST_* functions · 50+ formats via ST_Read · GIST     │   │
│  └────────────────────────────────────────────────────────────┘   │
│                            │                                      │
│                            ▼                                      │
│  ┌─ Trigger engine (DSL evaluator) ───────────────────────────┐   │
│  │  triggers.yaml → AST → push-down DuckDB SQL                │   │
│  │  Actions: SET_FIELD · RUN_SQL · WEBHOOK · validate · ...   │   │
│  │  Mutations write back through the native adapter           │   │
│  └────────────────────────────────────────────────────────────┘   │
│                            │                                      │
└────────────────────────────┼──────────────────────────────────────┘
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│  OUTPUT                                                          │
│  Webhooks · audit log · row mutations · MVT tiles · dashboards   │
└──────────────────────────────────────────────────────────────────┘
```

## Two-layer principle

GISPulse is built on a deliberate **two-layer split**:

- **DuckDB spatial = the brain.** All compute (predicates, aggregations, geometric functions, cross-source joins, vector tile generation) runs in DuckDB. It reads any of 50+ formats via `ST_Read`, uses an embedded GDAL/GEOS/PROJ stack with no runtime dependencies, and routinely outperforms PostGIS by 2× on vector workloads thanks to its `SPATIAL_JOIN` operator and Hilbert-packed R-tree.
- **Native adapters = the hands.** DuckDB doesn't write back into your `.gpkg`, `.shp`, or `.geojson` source — that goes through `pyogrio`, `sqlite3`, or `asyncpg` depending on the format. This keeps your source files in their canonical format, byte-compatible with QGIS and other tools.

This separation is what lets GISPulse stay format-agnostic without forcing you to migrate data into a custom store.

## The DML adapter layer

How GISPulse detects an edit depends on the format.

### SQLite-family (GeoPackage, SpatiaLite)

GeoPackage is, at its core, a SQLite database. When you call `gispulse track install <file.gpkg> --layer <name>`, GISPulse installs `AFTER INSERT`, `AFTER UPDATE`, `AFTER DELETE` triggers on the layer table that write a row into a hidden `_gispulse_change_log` table inside the same `.gpkg`. Any client editing the file — QGIS, `ogr2ogr`, raw `sqlite3`, FME, custom Python — fires those triggers atomically inside the SQLite transaction. The runtime drains the change-log on each tick.

- **Latency:** ~200 ms post-commit
- **Sync block-save:** not currently exposed (planned for v1.7+ via `BEFORE` trigger + plugin v1.5 `commitErrors` hook)
- **Bulk handling:** configurable threshold collapses N consecutive changes into one event

### File-blob (Shapefile, GeoJSON, FlatGeobuf, KML, MapInfo TAB, CSV+WKT)

Formats that can't host triggers natively use a **mtime watcher + DuckDB diff snapshot**. GISPulse keeps a `_gispulse_snapshot.duckdb` cache next to the source file. On each save:

1. Watcher detects the mtime change.
2. Runtime reads the current state via `ST_Read('file.shp')`.
3. DuckDB diffs current vs. snapshot by primary key (or composite signature if no PK), classifying each row as insert / update-attr / update-geom / delete.
4. The diff is pushed to the same change-log abstraction as GeoPackage; rules apply identically.

- **Latency:** 2–5 s post-save (polling-bound)
- **Sync block-save:** unavailable by design — file blobs commit before we see them
- **Encoding caveats:** auto-detect for DBF (cp1252 vs. UTF-8 vs. LATIN1) via `.cpg` sidecar

### PostGIS (remote, optional Pro)

When you point a `triggers.yaml` at a PostGIS connection, GISPulse uses native PostgreSQL triggers (`BEFORE`/`AFTER`/`DEFERRABLE`) and `LISTEN/NOTIFY` to receive change events synchronously. This unlocks:

- **True sync block-save:** a `BEFORE` trigger raising `RAISE EXCEPTION 'gispulse:invalid'` rejects the commit; the QGIS plugin v1.5 surfaces the message in the dock.
- **Multi-user concurrent edits** with full MVCC.
- **Multi-tenant RLS** for SaaS deployments.
- **Topology cross-feature live** (overlap/gap/sliver detection) backed by GIST indexes.

PostGIS isn't required to use GISPulse — your `triggers.yaml` is identical whether you target a local `.gpkg` or a remote PostGIS instance. The upgrade path is one line of YAML.

## The compute substrate: DuckDB spatial

GISPulse uses **DuckDB spatial** as its in-process compute engine. Why DuckDB:

- **Zero infrastructure.** A single Python wheel installs the runtime; the spatial extension auto-loads on first use (~50 MB download, no `apt install gdal-bin` or Postgres setup needed).
- **OGR-everything.** `ST_Read` opens 50+ vector formats via embedded GDAL — including all the formats GISPulse adapters care about, plus GeoParquet natively.
- **Performance.** Independent benchmarks (Foursquare 105M POI, NYC taxi 58M lines) show DuckDB spatial 2–3× faster than PostGIS on common spatial-join and aggregation workloads thanks to the v1.3+ `SPATIAL_JOIN` operator and Hilbert-packed R-tree indexes.
- **Federation.** `ATTACH 'postgres://...'` makes a remote PostGIS database addressable as if local; you can spatial-join a Shapefile with a PostGIS table in a single query.

Inside GISPulse, DuckDB powers four things:

1. **DSL geometric functions** (`geom_area_m2()`, `geom_within(layer, ...)`, `layer_lookup(...)`) — pushed down to `ST_*` calls.
2. **Predicate evaluation** for trigger filters.
3. **Aggregations** for declarative `aggregate:` rules (count-in-zone, sum-in-zone, density).
4. **Vector tile generation** for the portal map view via `ST_AsMVT`.

### Where DuckDB doesn't go

DuckDB spatial covers the 80% case for vector GIS workloads, but it's not a full PostGIS replacement. Gaps you may hit:

- No raster, topology PostGIS, pgRouting, or `geography` type.
- Some advanced operations (`ST_Subdivide`, `ST_VoronoiPolygons`, `ST_ClusterDBSCAN`) are missing or emulated.
- KNN queries (`<->` operator) are slower than PostGIS on large datasets.
- Some EPSG transformations require PROJ grid files that aren't bundled — fallback to `pyogrio.transform` is automatic, with explicit warnings logged.

For these cases, the **PostGIS Pro mode** is the recommended path.

## The trigger engine

Your `triggers.yaml` is parsed into an AST and evaluated against the change stream. A trigger has four parts:

```yaml
- name: enrich_batiment_from_zonage
  table: batiments               # which layer to watch
  when: [INSERT, UPDATE_GEOM]    # which DML events
  predicate: "constructible == true"  # row-level filter (optional)
  actions:
    - type: set_field
      field: zone_plu
      value: "layer_lookup(layer='plu_zones', match='spatial_within', take='code_zone')"
```

**Action types in v1.6:**

| Type | Effect |
|---|---|
| `set_field` | Write a value back into the source file (via native adapter) |
| `validate` | Tag the row or warn if a predicate fails (no block by default — use PostGIS Pro for synchronous block) |
| `run_sql` | Escape hatch for arbitrary SQL on the target engine |
| `webhook` | POST a JSON payload (with `new_values` / `old_values` / `geom_changed`) to an external URL |
| `aggregate` | Maintain a derived count/sum/density on a target layer (rebuild on tick or on event) |
| `cascade` | Trigger downstream actions on related rows in another layer |

The DSL parser is hand-rolled — no `eval`, no third-party expression evaluator. The surface area is intentionally small (8–10 geometric functions, 5 comparison operators, 3 boolean combinators) and pushed down to DuckDB SQL where possible.

## Three front-ends, one runtime

GISPulse exposes the same `triggers.yaml` through three interfaces:

- **CLI** (`gispulse triggers run`, `gispulse watch`, `gispulse track`) — for scripts, CI/CD, cron, systemd.
- **Portal** (gispulse-portal SPA) — visual rule editor, dryrun preview, dashboard.
- **QGIS plugin** — attach a layer, stream events into a dock, refresh the canvas after triggers fire.

The runtime under all three is identical: same parser, same evaluator, same DuckDB substrate. Anything you can do in the CLI you can do in the portal, and vice versa. We call this the **CLI–portal symmetry axiom** and we treat any asymmetry as UX debt.

## Three operating modes

| Mode | Best for | Latency | Sync block-save |
|---|---|---|---|
| **GeoPackage / SpatiaLite (Community)** | Solo data steward, offline, mobile field work | ~200 ms | Coming v1.7+ |
| **File-blob (Community, degraded)** | Shapefile / GeoJSON legacy, no migration cost | 2–5 s | Unavailable |
| **PostGIS (Pro)** | 5+ user teams, multi-tenant SaaS, sync validation | < 50 ms | ✅ |

You can mix modes: a single `triggers.yaml` can target multiple datasets across different formats and engines, and DuckDB federates queries across all of them.

## See also

- [Choose your engine](./engines.md) — decision tree for GeoPackage vs. PostGIS, format × latency × write-back matrix
- [DSL geometric functions](./dsl-geom-functions.md) — reference for the 7 push-down geom fcts
- Symmetry axiom — why CLI and portal must stay in lockstep ([guide/symmetry](../symmetry.md))
- Walkthroughs — three end-to-end QGIS save → trigger fire → action scenarios ([guide/walkthroughs](../walkthroughs/))

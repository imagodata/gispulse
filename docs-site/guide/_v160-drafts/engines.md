---
title: Choose your engine
description: Format × engine × latency × write-back matrix for GISPulse v1.6 — when to use GeoPackage, when to upgrade to PostGIS Pro.
---

# Choose your engine

GISPulse v1.6 supports four engines, automatically inferred from the dataset URI. This page is the decision tree.

## TL;DR — engine inference

The `engine:` clause in your `triggers.yaml` is **optional**. GISPulse infers it from the dataset URI:

| URI pattern | Inferred engine | Stage |
|---|---|---|
| `*.gpkg` | `gpkg` (SQLite triggers) | v1.0 stable |
| `*.sqlite` (no `gpkg_geometry_columns`) | `spatialite` | v1.6.1 |
| `postgresql://...` / `postgis://...` | `postgis` | v1.0 stable |
| `*.geojson` | `duckdb_diff` (file-blob CDC) | v1.6.1 |
| `*.fgb` | `duckdb_diff` | v1.6.1 |
| `*.shp` (+ companion files) | `duckdb_diff` | v1.6.2 |
| `*.kml` / `*.kmz` | `duckdb_diff` | v1.6.2 |
| `*.tab` (MapInfo) | `duckdb_diff` | v1.6.2 |
| `*.csv` (with WKT or lat/lon) | `duckdb_diff` | v1.6.2 |
| `*.dxf` | `duckdb_diff` (read-only) | v1.6.2 |

Override is one line:

```yaml
datasets:
  parcels:
    uri: ./data/parcels.gpkg
    engine: gpkg   # explicit override (default: inferred)
```

Mismatch between extension and engine raises a `DatasetEngineConflict` error at load time.

## Full matrix — format × properties

| Format | Engine | Latency | Sync block-save | Write-back | DuckDB CDC | Status |
|---|---|---|---|---|---|---|
| **GeoPackage** | `gpkg` | ~200 ms | v1.7+ | ✅ pyogrio + sqlite3 | native | v1.0 |
| **PostgreSQL/PostGIS** | `postgis` | < 50 ms | ✅ | ✅ asyncpg | federated via ATTACH | v1.0 |
| **SpatiaLite** | `spatialite` | ~200 ms | v1.7+ | ✅ pyogrio | native | v1.6.1 |
| **GeoJSON** | `duckdb_diff` | 2–5 s | ❌ | ✅ pyogrio | mtime + ST_Read | v1.6.1 |
| **FlatGeobuf** | `duckdb_diff` | 2–3 s | ❌ | ✅ pyogrio | append-aware | v1.6.1 |
| **Shapefile** | `duckdb_diff` | 3–5 s | ❌ | ✅ pyogrio | companion files | v1.6.2 |
| **KML / KMZ** | `duckdb_diff` | 3–5 s | ❌ | ✅ pyogrio | OGR-mediated | v1.6.2 |
| **MapInfo TAB** | `duckdb_diff` | 3–5 s | ❌ | ✅ pyogrio | companion files | v1.6.2 |
| **CSV + WKT** | `duckdb_diff` | 2–3 s | ❌ | ✅ pyogrio | native read_csv | v1.6.2 |
| **DXF** | `duckdb_diff` | 3–5 s | ❌ | ❌ read-only | OGR-mediated | v1.6.2 |
| **GeoParquet** | `duckdb` | < 1 s | ❌ | ✅ pyarrow | native columnar | v1.0 |

Legend:
- **Latency** — typical end-to-end edit-to-trigger-fire time on commodity hardware.
- **Sync block-save** — can a `validate` rule reject the commit before it lands ? Only PostGIS supports this in v1.6 ; GPKG / SpatiaLite are planned for v1.7+ via SQLite `BEFORE` triggers.
- **Write-back** — can `set_field` actions persist into the source file ?
- **DuckDB CDC** — how DuckDB participates in change detection. `native` = ingestion straight from the source file ; `federated via ATTACH` = remote DB attached read-only ; `mtime + ST_Read` = file-blob diff strategy.

## Decision tree

```
Start: what does my source look like ?
│
├─ A single GeoPackage on disk
│   └─ → gpkg (Community)        latency: 200ms, write-back: ✅
│
├─ A SpatiaLite legacy QGIS project
│   └─ → spatialite (Community)  latency: 200ms, write-back: ✅
│
├─ A Shapefile / GeoJSON / FlatGeobuf I cannot migrate
│   └─ → duckdb_diff (Community degraded)
│       latency: 2–5s, write-back: ✅, sync block-save: ❌
│
├─ A team of 5+ users editing concurrently
│   └─ → postgis (Pro)            latency: <50ms, write-back: ✅, sync block-save: ✅
│
├─ A multi-tenant SaaS deployment
│   └─ → postgis (Pro) with RLS  per-tenant isolation, audit log baked in
│
└─ A one-off batch job on GeoParquet
    └─ → duckdb (read-only)       latency: <1s, no triggers, no write-back
```

## When to upgrade to PostGIS Pro

Stay in Community (GPKG / SpatiaLite / file-blob) as long as:

- Single-user editing, or 2–3 users coordinating manually.
- Acceptable latency: 200 ms (SQLite) or 2–5 s (file-blob).
- No need to **block** an invalid commit synchronously (you can still tag the row post-commit).

Upgrade to PostGIS Pro when any of these become true:

- **5+ concurrent editors.** SQLite locks degrade past 3-4 writers ; PostgreSQL MVCC handles dozens.
- **You need to reject invalid commits at save time** (the QGIS user gets a dialog, the data never lands). Only PostGIS triggers can `RAISE EXCEPTION` synchronously.
- **You manage 50k+ features per layer.** SQLite VFS and pyogrio start showing latency past this point ; PostGIS GIST indexes scale to billions.
- **You need multi-tenant isolation** (SaaS deployments). PostGIS RLS is industry-standard ; GPKG cannot do this.
- **Audit trail is a compliance requirement.** PostGIS supports trigger-driven audit tables with full MVCC visibility ; GPKG's `_gispulse_change_log` is best-effort.

## Federation — mix and match

DuckDB lets you spatial-join data across engines in a single query :

```sql
-- (run via gispulse query or SET_FIELD layer_lookup)
ATTACH 'postgresql://prod/communes' AS pg;
SELECT p.id, c.code_insee
FROM parcels p, pg.communes c
WHERE ST_Within(p.geom, c.geom);
```

A single `triggers.yaml` can target a local GPKG and a remote PostGIS in the same rule :

```yaml
datasets:
  parcels:
    uri: ./data/parcels.gpkg              # gpkg engine
  zoning:
    uri: postgresql://prod/zoning_schema  # postgis engine

triggers:
  - table: parcels
    when: [INSERT, UPDATE_GEOM]
    actions:
      - type: set_field
        field: zone_plu
        value: "layer_lookup(layer='zoning', match='spatial_within', take='code_zone')"
```

The `layer_lookup` is push-down to DuckDB, which federates the query across both engines transparently.

## Performance notes

- **GPKG** : `_gispulse_change_log` adds ~5 µs per INSERT (negligible). The watcher tick polls every 200 ms by default — tune via `--poll-interval` if you need lower latency.
- **PostGIS** : `LISTEN/NOTIFY` is push-based, so latency is bounded by network round-trip (typically <50 ms). Triggers add ~50 µs per row (vs ~5 µs for the SQLite case) but this is rarely the bottleneck.
- **File-blob** : the diff is `O(n)` over the full file. For 100k+ feature files, prefer FlatGeobuf (append-only) or migrate to GPKG.

## See also

- [Architecture](./architecture.md) — the two-layer principle behind why these engines coexist
- [DSL geometric functions](./dsl-geom-functions.md) — what runs in the DuckDB compute substrate

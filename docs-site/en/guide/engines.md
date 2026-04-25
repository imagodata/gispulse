---
title: DuckDB / PostGIS / Hybrid Engines
description: Understanding the three GISPulse execution modes — local Python/DuckDB, persistent PostGIS, hybrid mode.
---

# Execution Engines

GISPulse supports three execution modes. The engine can be configured per rule, per session, or globally.

## Overview

| Engine | Tier | Usage | Volumes |
|--------|------|-------|---------|
| GPKG (GeoPandas) | Community | **Default**, portable mode | < 50k features |
| DuckDB | Community | Local acceleration | 50k – 10M features |
| PostGIS | Pro | Persistence, triggers, multi-user | Unlimited |
| Hybrid | Pro | DuckDB for computation + PostGIS for storage | Unlimited |

## GPKG mode (GeoPandas) — default

Default engine since v1.0.2. Uses GeoPandas + Shapely with native GPKG as portable storage format.

```bash
gispulse run input.gpkg --rules rules.json -o output.gpkg --engine gpkg
```

**When to use:**
- Datasets < 50,000 features
- No server-side persistence needed
- Offline / portable mode
- Environment without PostGIS

**Limitations:**
- Everything in RAM
- No persistence between sessions
- Less performant on large volumes

## DuckDB mode

Vectorized acceleration via DuckDB + the spatial extension. Automatically enabled by certain capabilities on large volumes.

```bash
gispulse run input.gpkg --rules rules.json -o output.gpkg --engine duckdb
```

**When to use:**
- Datasets from 50,000 to several million features
- Intensive attribute computations (aggregations, joins)
- Offline / serverless mode
- GeoParquet input (native DuckDB)

**Advantages:**
- Automatic SIMD vectorization
- Native GeoParquet reading (columnar format)
- Automatic multi-threading
- No server required

**Limitations:**
- Spatial operations less complete than PostGIS
- No server-side persistence
- No triggers or pg_notify

### Automatic DuckDB selection

Certain capabilities automatically switch to DuckDB when the volume exceeds 50,000 features and the `duckdb` engine is active:

- `buffer` — DuckDB spatial ST_Buffer
- `area_length` — vectorized calculations

## PostGIS mode (Pro)

Delegates processing to a PostgreSQL/PostGIS server. Provides persistence, triggers, multi-user, and advanced SQL operations.

**Prerequisites:**
- `pip install "gispulse[postgis]"`
- `GISPULSE_DSN=postgresql://user:pass@host:5432/db` in `.env`
- PostgreSQL 14+ with PostGIS 3.x extension

**Configuration:**

```bash
# .env
GISPULSE_DSN=postgresql://gispulse:secret@localhost:5432/gispulse
```

**Advantages:**
- Server-side dataset persistence
- Triggers via `pg_notify` (real-time reactivity)
- Advanced SQL operations (full ST_* support)
- Multi-user with RBAC (Team tier)
- Very large volume support (server-side spatial indexing)
- Cron pipelines

**When to use:**
- Data shared between multiple users
- Need for triggers or cron
- Volumes > several million features
- Production environment

### Getting started with PostGIS in Docker

```bash
docker run -d \
  --name gispulse-postgres \
  -e POSTGRES_USER=gispulse \
  -e POSTGRES_PASSWORD=secret \
  -e POSTGRES_DB=gispulse \
  -p 5432:5432 \
  postgis/postgis:16-3.4
```

```bash
GISPULSE_DSN=postgresql://gispulse:secret@localhost:5432/gispulse
gispulse portal
```

## Hybrid mode (Pro)

Combines DuckDB for intensive local computations and PostGIS for persistence and shared data.

```
Local data → DuckDB (fast computation) → PostGIS (storage)
                                       ← PostGIS (reference)
```

**Configuration:**

```json
{
  "capability": "spatial_join",
  "config": {
    "ref_layer": "municipalities",
    "engine": "hybrid"
  }
}
```

Hybrid mode is managed automatically by the `SessionManager` — it chooses DuckDB for operations on local data and PostGIS for lookups on persistent tables.

## Recommendations by use case

### Solo GIS analyst, local data

```bash
# Python mode — simple and direct
gispulse run data.gpkg --rules rules.json -o output.gpkg
```

### Batch pipeline on large volume

```bash
# DuckDB — maximum performance
gispulse run data_10M.gpkg --rules rules.json -o output.gpkg --engine duckdb
```

### Team / SaaS deployment

```bash
# PostGIS — persistence and multi-user
GISPULSE_DSN=postgresql://... gispulse portal --host 0.0.0.0
```

### Automated CI/CD

```yaml
- run: gispulse run data.gpkg --rules rules.json -o output.gpkg --engine duckdb
```

## Diagnosing the engine in use

The `run` command always indicates the engine actually used in the output:

```
2 rule(s) applied [engine: duckdb]
```

For more details, enable verbose mode:

```bash
gispulse run ... --verbose
```

Structured logs (JSON) indicate which strategy was selected for each capability.

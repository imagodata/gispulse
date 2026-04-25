---
title: Architecture & Concepts
description: GISPulse architecture overview — core concepts, execution flow, engine abstraction, plugin system, rules pipeline.
---

# Architecture & Concepts

GISPulse is a modular geospatial engine built around a clear separation of concerns. This page explains the key abstractions, how they interact, and how the system processes spatial data.

## High-Level Architecture

```
┌─────────────────────────────────────────────────────┐
│                    Entry Points                      │
│   CLI  │  REST API  │  Python SDK  │  QGIS Plugin   │
└────────┬────────────┬──────────────┬────────────────┘
         │            │              │
         ▼            ▼              ▼
┌─────────────────────────────────────────────────────┐
│                  Orchestration                        │
│   JobRunner  │  DAG Executor  │  Trigger Evaluator   │
└────────────────────────┬────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────┐
│                   Rules Engine                        │
│   Rule Parser  │  Capability Registry  │  Validator   │
└────────────────────────┬────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────┐
│                  Core Engine                          │
│   SessionManager  │  ExecutionStrategy  │  I/O Layer  │
└────────┬──────────────────────────────┬─────────────┘
         │                              │
         ▼                              ▼
┌─────────────────┐          ┌─────────────────────┐
│  DuckDB Engine  │          │   PostGIS Engine     │
│  (portable)     │          │   (persistent)       │
└─────────────────┘          └─────────────────────┘
```

The architecture is layered: entry points delegate to orchestration, which invokes the rules engine, which delegates to the core engine. The core engine abstracts the underlying spatial database through the `ExecutionStrategy` pattern.

---

## Core Concepts

### Dataset

A **Dataset** represents a spatial data source. It can be a local file (GPKG, GeoJSON, Shapefile), a remote service (WFS, OGC API Features), or a PostGIS table. Datasets are registered in the catalog and tracked by the session manager.

```python
@dataclass
class Dataset:
    id: str
    name: str
    path: str          # file path, URL, or table name
    format: str        # gpkg, geojson, postgis, etc.
    crs: str           # EPSG code
    layers: list[Layer]
```

### Layer

A **Layer** is a single feature collection within a dataset. A GPKG file may contain multiple layers. Each layer has a geometry type, attribute schema, and feature count.

### Job

A **Job** represents a single execution of a rules pipeline. Jobs have a lifecycle: `pending` -> `running` -> `completed` | `failed`. The `JobRunner` manages async execution and status tracking.

```python
@dataclass
class Job:
    id: str
    status: JobStatus   # pending, running, completed, failed
    rules: list[Rule]
    engine: str          # duckdb or postgis
    created_at: datetime
    completed_at: Optional[datetime]
    result: Optional[JobResult]
```

### Rule

A **Rule** is a declarative instruction to apply a capability with specific parameters. Rules are defined in JSON and executed sequentially within a pipeline.

```json
{
  "name": "buffered_parcels",
  "capability": "buffer",
  "params": {
    "input": "parcelles.gpkg",
    "distance": 100
  }
}
```

### Capability

A **Capability** is a reusable spatial operation. GISPulse ships with 17 built-in capabilities (buffer, clip, spatial_join, etc.). Each capability implements the `BaseCapability` interface and is registered in the capability registry.

Capabilities are engine-agnostic: the same `buffer` capability generates DuckDB SQL or PostGIS SQL depending on the active engine.

### Engine

An **Engine** is a concrete implementation of the `ExecutionStrategy` interface. GISPulse includes two engines:

- **DuckDB** — embedded, serverless, in-memory. No installation required.
- **PostGIS** — persistent, server-based. Supports triggers, scheduling, and multi-user access.

### Artifact

An **Artifact** is the output of a job. It can be a file (GPKG, GeoJSON), a PostGIS table, or a computed statistic. Artifacts are tracked by the session manager and can serve as inputs to subsequent rules.

---

## Execution Flow

When you run `gispulse run rules.json --engine duckdb`, the following sequence executes:

1. **CLI parses** the command and loads the rules file
2. **Rule Parser** validates the JSON structure and resolves capability references
3. **JobRunner** creates a new Job with `pending` status
4. **SessionManager** initializes a session with the selected engine
5. **I/O Layer** imports input datasets into the engine (DuckDB in-memory tables or PostGIS tables)
6. **For each rule**, the engine:
   - Resolves the capability from the registry
   - Executes it with the provided parameters
   - Stores the result as an intermediate layer
7. **I/O Layer** exports the final result in the requested format
8. **JobRunner** updates the job status to `completed`

```
rules.json → parse → validate → create job → init session
    → import data → execute rules (1..N) → export result → done
```

---

## Engine Abstraction

The `ExecutionStrategy` pattern allows GISPulse to support multiple spatial engines behind a unified interface:

```python
class ExecutionStrategy(Protocol):
    def execute_sql(self, sql: str, params: dict) -> Any: ...
    def import_layer(self, path: str, layer_name: str) -> str: ...
    def export_layer(self, layer_name: str, path: str, format: str) -> None: ...
    def list_layers(self) -> list[str]: ...
```

Each engine implements this protocol:

- **DuckDBStrategy** — uses DuckDB's spatial extension with in-memory tables
- **PostGISStrategy** — connects to a PostgreSQL/PostGIS instance via psycopg2

Switching engines is a runtime decision: `--engine duckdb` or `--engine postgis`. The rules file is identical.

---

## Plugin System

GISPulse supports external entry points:

- **QGIS Plugin** — dock widgets for datasets, jobs, scenarios, and triggers. Uses QThread workers for background execution.
- **ArcGIS Add-in** — dockpanes with geoprocessing tools that call the GISPulse API.
- **Tauri Desktop** — standalone React + MapLibre GL JS application.

All plugins communicate with GISPulse through the REST API or the Python SDK. They never access the engine directly.

---

## Rules Pipeline

Rules execute sequentially within a job. Each rule's output can be referenced as input by subsequent rules:

```json
[
  {
    "name": "step_1",
    "capability": "buffer",
    "params": { "input": "roads.gpkg", "distance": 50 }
  },
  {
    "name": "step_2",
    "capability": "clip",
    "params": { "input": "buildings.gpkg", "ref_layer": "step_1" }
  }
]
```

In this example, `step_2` references `step_1` by name. The engine resolves intermediate results automatically.

### DAG Execution (Pro)

The Pro tier includes a DAG executor that analyzes rule dependencies and can parallelize independent steps. The visual node editor in the portal provides a graphical interface for building complex pipelines.

---

## Error Handling

GISPulse uses structured error responses across all interfaces:

```json
{
  "error": {
    "code": "CAPABILITY_NOT_FOUND",
    "message": "Capability 'unknown_cap' is not registered",
    "detail": "Available capabilities: buffer, clip, union, ..."
  }
}
```

Job failures are recorded with full context:

- The rule that failed
- The error type and message
- The engine state at the time of failure

The REST API returns consistent HTTP status codes: 400 for validation errors, 404 for missing resources, 422 for processing errors, 500 for internal errors.

---

## Further Reading

- [Rules Guide](/guide/rules) — writing and validating rules
- [Engines Guide](/guide/engines) — choosing and configuring engines
- [Formats Guide](/guide/formats) — supported data formats
- [API Reference](/api/sdk) — REST API and Python SDK documentation

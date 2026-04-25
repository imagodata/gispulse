---
title: Python SDK
description: GISPulse Python SDK documentation — installation, synchronous/asynchronous client, WebSocket streaming.
---

# Python SDK

The `gispulse-sdk` Python SDK is a type-safe HTTP client for the GISPulse REST API. It supports synchronous and asynchronous modes, SSE streaming, and WebSocket.

## Installation

```bash
pip install gispulse-sdk

# With WebSocket support
pip install "gispulse-sdk[ws]"
```

**Requirements:** Python 3.9+, `httpx`, `pydantic>=2.0`

## Synchronous Client

```python
from gispulse_sdk import GISPulseClient

# Local connection (no auth)
client = GISPulseClient("http://localhost:8001")

# Remote connection with API key
client = GISPulseClient(
    "https://gispulse.example.com",
    api_key="sk-gp-your-key",
)
```

### Usage as a context manager

```python
with GISPulseClient("http://localhost:8001") as client:
    datasets = client.datasets.list()
    print(datasets)
# Connection closed automatically
```

## Asynchronous Client

```python
from gispulse_sdk import AsyncGISPulseClient

async def main():
    async with AsyncGISPulseClient("http://localhost:8001") as client:
        datasets = await client.datasets.list()
        print(datasets)

import asyncio
asyncio.run(main())
```

## Available Endpoints

| `client.X` | Description |
|-------------|-------------|
| `client.datasets` | Dataset management |
| `client.jobs` | Job execution and monitoring |
| `client.rules` | Rule CRUD |
| `client.capabilities` | List capabilities |
| `client.triggers` | Trigger management (Pro) |
| `client.scenarios` | Scenario management (Pro) |
| `client.sessions` | PostGIS sessions (Pro) |
| `client.projects` | Projects (Pro) |
| `client.catalog` | OGC Catalog |
| `client.ogc` | OGC Features API |

---

## Datasets

### Upload a file

```python
dataset = client.datasets.upload("data/parcelles.gpkg")
print(dataset.id)       # Dataset UUID
print(dataset.name)     # "parcelles.gpkg"
print(dataset.format)   # "GPKG"
print(dataset.crs)      # "EPSG:2154"
```

### List datasets

```python
datasets = client.datasets.list(limit=50, offset=0)
for ds in datasets:
    print(f"{ds.id} — {ds.name} ({ds.format})")
```

### Retrieve features

```python
fc = client.datasets.features(
    dataset_id=dataset.id,
    layer="parcelles",
    limit=1000,
    bbox=(2.3, 48.8, 2.4, 48.9),  # minx, miny, maxx, maxy
)
# fc is a GeoJSON FeatureCollection dict
```

### Export a dataset

```python
path = client.datasets.export(
    dataset_id=dataset.id,
    format="geojson",
    output_path="output/export.geojson",
)
print(f"Exported to {path}")
```

### SQL query

```python
result = client.datasets.sql(
    "SELECT code_dept, COUNT(*) as nb_parcelles FROM parcelles GROUP BY 1 ORDER BY 2 DESC"
)
print(result["rows"])
```

### Register an OGC service

```python
from gispulse_sdk.models import OGCDatasetCreate

dataset = client.datasets.upload_ogc(OGCDatasetCreate(
    url="https://wxs.ign.fr/parcellaire/geoportail/wfs",
    service_type="WFS",
    layer_name="BDPARCELLAIRE_VECTEUR:parcelle",
    name="IGN Parcels",
))
```

---

## Jobs

### Create and wait for a job

```python
from gispulse_sdk.models import JobCreate

job = client.jobs.create(JobCreate(
    name="buffer_parcelles",
    dataset_id=dataset.id,
    parameters={"rule_ids": [str(rule.id)]},
))

# Wait for completion (polling)
import time
while True:
    job = client.jobs.get(job.id)
    if job.status in ("COMPLETED", "FAILED"):
        break
    time.sleep(1)

print(f"Job finished: {job.status}")
```

### SSE streaming (async)

```python
async with AsyncGISPulseClient("http://localhost:8001") as client:
    async for event in client.jobs.stream(job_id):
        print(event)
```

---

## Rules

### Full CRUD

```python
from gispulse_sdk.models import RuleCreate

# Create
rule = client.rules.create(RuleCreate(
    name="buffer_50m",
    capability="buffer",
    config={"distance": 50},
    enabled=True,
))

# List
rules = client.rules.list()

# Update
rule = client.rules.update(rule.id, {"config": {"distance": 100}})

# Delete
client.rules.delete(rule.id)
```

### Validate rules (dry-run)

```python
results = client.rules.validate([
    {"capability": "buffer", "config": {"distance": 100}},
    {"capability": "reproject", "config": {"crs": "EPSG:2154"}},
])
for r in results:
    print(f"{r['name']}: {'OK' if r['valid'] else 'FAIL'}")
```

---

## Capabilities

```python
caps = client.capabilities()
for cap in caps:
    print(f"{cap.name}: {cap.description}")
```

---

## WebSocket Streaming

Available with `pip install "gispulse-sdk[ws]"`.

```python
from gispulse_sdk import AsyncGISPulseClient

async def watch_job(job_id: str):
    async with AsyncGISPulseClient("http://localhost:8001") as client:
        async for message in client.streaming.watch_job(job_id):
            print(f"[{message['type']}] {message.get('message', '')}")

asyncio.run(watch_job("job-uuid"))
```

---

## Error Handling

The SDK raises typed exceptions:

```python
from gispulse_sdk.exceptions import (
    GISPulseError,       # Base
    NotFoundError,       # 404
    AuthError,           # 401/403
    ValidationError,     # 422
    RateLimitError,      # 429
    ServerError,         # 500
)

try:
    dataset = client.datasets.get("nonexistent-uuid")
except NotFoundError:
    print("Dataset not found")
except AuthError:
    print("Invalid API key")
```

---

## Complete Example — Automated Pipeline

```python
from gispulse_sdk import GISPulseClient
from gispulse_sdk.models import RuleCreate, JobCreate
import time

with GISPulseClient("http://localhost:8001") as client:
    # 1. Upload file
    print("Uploading...")
    ds = client.datasets.upload("data/communes_bretagne.gpkg")

    # 2. Create rules
    rules = [
        client.rules.create(RuleCreate(
            name="buffer_2km",
            capability="buffer",
            config={"distance": 2000, "order": 0},
        )),
        client.rules.create(RuleCreate(
            name="area_calc",
            capability="area_length",
            config={"area_column": "surface_buffer_m2", "order": 1},
        )),
    ]

    # 3. Launch the job
    print("Running...")
    job = client.jobs.create(JobCreate(
        name="analyse_communes",
        dataset_id=ds.id,
        parameters={"rule_ids": [str(r.id) for r in rules]},
    ))

    # 4. Wait for completion
    while True:
        job = client.jobs.get(job.id)
        if job.status in ("COMPLETED", "FAILED"):
            break
        time.sleep(0.5)

    if job.status == "COMPLETED":
        print("Success!")
        # 5. Export the result
        out = client.datasets.export(ds.id, format="geojson", output_path="output/result.geojson")
        print(f"Result: {out}")
    else:
        print(f"Failed: {job.status}")
```

---

## Model Reference

<!-- TODO: document all Pydantic models from the SDK (DatasetResponse, JobResponse, etc.) -->

Pydantic models are in `gispulse_sdk/models.py`. They are all exported from `gispulse_sdk`:

```python
from gispulse_sdk.models import (
    DatasetResponse,
    JobResponse,
    RuleResponse,
    CapabilityInfo,
    HealthResponse,
    OGCDatasetCreate,
    RuleCreate,
    JobCreate,
)
```

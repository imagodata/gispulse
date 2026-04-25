---
title: REST API — Reference
description: Complete GISPulse REST API documentation — datasets, jobs, rules, capabilities, OGC, streaming.
---

# REST API — Reference

The GISPulse REST API is a FastAPI application available when the Portal is running (`gispulse portal`).

**Base URL:** `http://localhost:8001`

**Interactive documentation:** `http://localhost:8001/docs` (Swagger UI)

## Authentication

In local Community mode, authentication is optional. In production, configure an API key:

```bash
GISPULSE_API_KEY=sk-gp-your-key
```

All authenticated requests must include:

```http
Authorization: Bearer sk-gp-your-key
```

## Health

### `GET /health`

Server and component status.

**Response:**
```json
{
  "status": "ok",
  "version": "0.1.0",
  "engine": "python",
  "postgis": false
}
```

---

## Datasets

### `POST /datasets/upload`

Upload a spatial file. Returns the metadata of the created dataset.

**Content-Type:** `multipart/form-data`

```bash
curl -X POST http://localhost:8001/datasets/upload \
  -F "file=@data/parcelles.gpkg"
```

**Response:**
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "name": "parcelles.gpkg",
  "format": "GPKG",
  "crs": "EPSG:2154",
  "layers": [
    {
      "name": "parcelles",
      "geometry_type": "Polygon",
      "feature_count": 8420
    }
  ]
}
```

### `GET /datasets`

List all registered datasets.

**Query params:** `limit` (int, default 100), `offset` (int, default 0)

### `GET /datasets/{id}`

Retrieve a dataset by UUID.

### `DELETE /api/portal/datasets/{id}`

Delete a dataset.

### `POST /datasets/ogc`

Register a remote OGC service as a dataset (lazy — no download).

```json
{
  "url": "https://wxs.ign.fr/parcellaire/geoportail/wfs",
  "service_type": "WFS",
  "layer_name": "BDPARCELLAIRE_VECTEUR:parcelle",
  "name": "IGN Cadastral Parcels"
}
```

---

## Features / Data

### `GET /api/portal/datasets/{id}/layers/{layer}/features`

Retrieve layer features as GeoJSON.

**Query params:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `limit` | int | 100 | Number of features |
| `offset` | int | 0 | Pagination |
| `bbox` | string | — | Spatial filter: `minx,miny,maxx,maxy` |

**Response:** GeoJSON FeatureCollection

```json
{
  "type": "FeatureCollection",
  "features": [...],
  "total": 8420,
  "limit": 100,
  "offset": 0
}
```

### `POST /api/portal/sql/execute`

Execute a SQL query on loaded datasets (DuckDB).

```json
{ "query": "SELECT code_commune, COUNT(*) as nb FROM parcelles GROUP BY 1" }
```

### `POST /api/portal/datasets/export`

Export a dataset to a target format.

```json
{
  "dataset_id": "550e8400-...",
  "format": "geojson"
}
```

Available formats: `gpkg`, `geojson`, `fgb`, `parquet`, `shp`

---

## Jobs

### `POST /jobs`

Create and execute a processing job.

```json
{
  "name": "buffer_parcelles",
  "dataset_id": "550e8400-...",
  "parameters": {
    "rule_ids": ["rule-uuid-1", "rule-uuid-2"]
  }
}
```

**Response:**
```json
{
  "id": "job-uuid",
  "status": "PENDING",
  "created_at": "2026-04-06T10:00:00Z"
}
```

### `GET /jobs/{id}`

Retrieve the status of a job.

```json
{
  "id": "job-uuid",
  "status": "COMPLETED",
  "started_at": "2026-04-06T10:00:01Z",
  "completed_at": "2026-04-06T10:00:05Z"
}
```

Statuses: `PENDING`, `RUNNING`, `COMPLETED`, `FAILED`

### `GET /jobs/{id}/stream` (SSE)

Stream Server-Sent Events of real-time execution logs.

```javascript
const es = new EventSource('/jobs/job-uuid/stream')
es.onmessage = (e) => console.log(JSON.parse(e.data))
```

---

## Rules

### `GET /rules`

List all rules.

### `POST /rules`

Create a rule.

```json
{
  "name": "buffer_50m",
  "capability": "buffer",
  "config": { "distance": 50 },
  "enabled": true
}
```

### `PUT /rules/{id}`

Update a rule.

### `DELETE /rules/{id}`

Delete a rule.

### `POST /rules/validate`

Validate a batch of rules (dry-run).

```json
[
  { "capability": "buffer", "config": { "distance": 100 } },
  { "capability": "reproject", "config": { "crs": "EPSG:2154" } }
]
```

---

## Capabilities

### `GET /capabilities`

List all available capabilities.

```json
[
  {
    "name": "buffer",
    "description": "Buffer geometries by a fixed distance",
    "schema": {
      "type": "object",
      "properties": {
        "distance": { "type": "number" }
      }
    }
  }
]
```

---

## OGC Features API

The OGC API is available under `/ogc/`.

### `GET /ogc/collections`

List available collections (standard OGC Features API).

### `GET /ogc/collections/{id}/items`

Retrieve collection items as GeoJSON.

**OGC-compliant query params:** `limit`, `offset`, `bbox`, `datetime`

### `GET /ogc/collections/{id}/tiles/{z}/{x}/{y}.mvt`

Vector tiles (MVT) for high-performance map rendering.

---

## Triggers (Pro)

### `POST /triggers`

Create a trigger associated with a rule.

```json
{
  "name": "buffer_on_data_changed",
  "event": "DATA_CHANGED",
  "trigger_type": "DML",
  "rule_id": "rule-uuid",
  "enabled": true
}
```

### `GET /triggers`

List all triggers.

---

## Streaming / SSE

### `GET /rules/eval-stream`

SSE stream of real-time rule evaluation. Used by the Portal for visual execution feedback.

### `GET /jobs/{id}/stream`

SSE stream of logs for a specific job.

---

## Error Codes

| Code | Meaning |
|------|---------|
| `400` | Bad request (missing or malformed parameters) |
| `401` | Unauthenticated (missing API key) |
| `403` | Unauthorized (invalid API key or insufficient permissions) |
| `404` | Resource not found |
| `409` | Conflict (duplicate detected) |
| `422` | Validation error (invalid request body) |
| `429` | Too many requests (rate limit) |
| `500` | Internal server error |

---

## Python SDK

Prefer the [Python SDK](/api/sdk) over the raw REST API for Python scripts and integrations.

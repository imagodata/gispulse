---
title: Developing a Plugin / Capability
description: Guide for creating custom GISPulse capabilities and integrating GISPulse into third-party clients.
---

# Developing a Plugin or Capability

GISPulse is designed to be extensible at two levels:

1. **Capabilities** -- new spatial operations registered in the engine
2. **Client plugins** -- integrations into third-party GIS software (QGIS, ArcGIS, etc.)

## Creating a Capability

A capability is a Python class that inherits from `Capability` and is decorated with `@register`.

### Minimal Structure

```python
# capabilities/my_cap.py
from __future__ import annotations

import geopandas as gpd
from capabilities.base import Capability
from capabilities.registry import register


@register
class MyCapability(Capability):
    name = "my_cap"
    description = "Short description of what the capability does"
    schema = {
        "type": "object",
        "properties": {
            "my_parameter": {
                "type": "number",
                "default": 1.0,
                "description": "A numeric parameter",
            }
        },
        "required": [],
    }

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        config: dict,
        **kwargs,
    ) -> gpd.GeoDataFrame:
        my_parameter = config.get("my_parameter", 1.0)
        # Your logic here
        result = gdf.copy()
        result["my_column"] = my_parameter
        return result
```

### Required Attributes

| Attribute | Type | Description |
|-----------|------|-------------|
| `name` | `str` | Unique identifier, used in JSON rules (`"capability": "my_cap"`) |
| `description` | `str` | Displayed in `gispulse capabilities` and the API |
| `schema` | `dict` | JSON Schema of the configuration parameters |

### Auto-registration

The capability is automatically available as soon as the module is imported. GISPulse discovers capabilities by importing modules in `capabilities/`.

For a capability in an external package, import the module at startup:

```python
# your_package/__init__.py
import your_package.capabilities  # triggers @register
```

### Multi-strategy Support (Python + DuckDB)

For performance on large volumes, implement two strategies:

```python
from capabilities.strategy import ExecutionStrategy, ExecutionContext, StrategyMode


class _MyCapPythonStrategy(ExecutionStrategy):
    mode = StrategyMode.PYTHON
    priority = 10  # low priority = fallback

    def can_execute(self, ctx: ExecutionContext) -> bool:
        return True  # always available

    def execute(self, gdf: gpd.GeoDataFrame, ctx: ExecutionContext) -> gpd.GeoDataFrame:
        # GeoPandas implementation
        return gdf


class _MyCapDuckDBStrategy(ExecutionStrategy):
    mode = StrategyMode.DUCKDB
    priority = 100  # high priority = preferred when applicable

    def can_execute(self, ctx: ExecutionContext) -> bool:
        return ctx.engine.backend_name == "duckdb" and ctx.feature_count > 10_000

    def execute(self, gdf: gpd.GeoDataFrame, ctx: ExecutionContext) -> gpd.GeoDataFrame:
        # DuckDB SQL implementation
        ctx.engine.register("_input", gdf)
        return ctx.engine.sql_to_gdf("SELECT *, ... FROM _input")


@register
class MyCapability(Capability):
    name = "my_cap"
    description = "..."
    schema = {...}
    strategies = [_MyCapPythonStrategy, _MyCapDuckDBStrategy]
```

### Testing a Capability

```python
# tests/test_my_cap.py
import geopandas as gpd
from shapely.geometry import Point
from capabilities.registry import get


def test_my_cap_basic():
    gdf = gpd.GeoDataFrame(
        {"geometry": [Point(0, 0), Point(1, 1)]},
        crs="EPSG:4326",
    )
    cap = get("my_cap")
    result = cap.execute(gdf, config={"my_parameter": 2.0})
    assert len(result) == 2
    assert "my_column" in result.columns
    assert result["my_column"].iloc[0] == 2.0
```

### Using in JSON Rules

```json
{
  "name": "apply_my_cap",
  "capability": "my_cap",
  "config": {
    "my_parameter": 42.0,
    "order": 0
  },
  "enabled": true
}
```

---

## Developing a GIS Client (Third-party Plugin)

Any software capable of making HTTP requests can integrate with GISPulse via the REST API.

### Resources

- [REST API -- Reference](/api/rest) -- complete endpoints
- [Python SDK](/api/sdk) -- if your client is written in Python
- OGC API Features -- standard for loading layers in any compatible GIS software

### Basic Pattern (HTTP)

```python
import httpx

BASE_URL = "http://localhost:8001"

# 1. List datasets
resp = httpx.get(f"{BASE_URL}/datasets")
datasets = resp.json()

# 2. Load features from a dataset
features = httpx.get(
    f"{BASE_URL}/api/portal/datasets/{dataset_id}/layers/default/features",
    params={"limit": 1000}
).json()

# 3. Execute a job
job = httpx.post(f"{BASE_URL}/jobs", json={
    "name": "test",
    "dataset_id": dataset_id,
    "parameters": {"rule_ids": [rule_id]},
}).json()
```

### QGIS Plugin -- Reference Architecture

The QGIS plugin `clients/qgis/gispulse_qgis/` is the reference implementation:

| Module | Role |
|--------|------|
| `plugin.py` | QGIS entry point, panel initialization |
| `api_bridge.py` | HTTP client for the GISPulse API |
| `dock_datasets.py` | Dataset management panel |
| `dock_jobs.py` | Job monitoring panel |
| `layer_factories.py` | QGIS layer creation (OGC, MVT, PostGIS) |
| `connection_dialog.py` | Connection configuration dialog |

### SSE Streaming for Real-time Monitoring

To display job progress in real time:

```javascript
// JavaScript
const eventSource = new EventSource(`${BASE_URL}/jobs/${jobId}/stream`)
eventSource.onmessage = (event) => {
  const data = JSON.parse(event.data)
  console.log(data.message)
}
eventSource.addEventListener('done', () => {
  eventSource.close()
})
```

### Loading Vector Tiles (MVT)

The MVT API is compatible with MapLibre GL JS, Mapbox GL JS, and any vector tile client:

```javascript
// MapLibre GL JS
map.addSource('gispulse', {
  type: 'vector',
  tiles: [`${BASE_URL}/ogc/collections/${datasetId}/tiles/{z}/{x}/{y}.mvt`],
  minzoom: 0,
  maxzoom: 14,
})
map.addLayer({
  id: 'features',
  type: 'fill',
  source: 'gispulse',
  'source-layer': 'default',
  paint: { 'fill-color': '#2d5016', 'fill-opacity': 0.6 },
})
```

---

## Contributing

Capabilities and plugins are welcome as PRs. See [CONTRIBUTING.md](https://github.com/gispulse/gispulse/blob/main/CONTRIBUTING.md) for conventions.

Enterprise capabilities (paid, redistribution): contact [contact@gispulse.dev](mailto:contact@gispulse.dev).

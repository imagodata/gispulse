---
title: Writing Rules
description: GISPulse JSON rule format, options, pipeline composition, and best practices.
---

# Writing Rules

GISPulse rules are declarative JSON files. They define which spatial operations to apply, in what order, and with which parameters.

## Rule structure

```json
{
  "name": "buffer_100m",
  "description": "100 m buffer around buildings",
  "capability": "buffer",
  "config": {
    "distance": 100,
    "order": 0
  },
  "enabled": true
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | yes | Unique rule identifier within the file |
| `description` | string | no | Human-readable documentation |
| `capability` | string | yes | Name of the capability to invoke |
| `config` | object | yes | Parameters passed to the capability |
| `config.order` | int | recommended | Execution order (ascending) |
| `enabled` | bool | no | `true` by default. `false` = rule is skipped |

## Pipeline — complete rules file

A rules file is a JSON array of ordered rules:

```json
[
  {
    "name": "filter_active",
    "description": "Keep only active buildings",
    "capability": "filter",
    "config": {
      "expression": "statut == 'ACTIF'",
      "order": 0
    },
    "enabled": true
  },
  {
    "name": "reproject_l93",
    "description": "Reproject to Lambert-93 for metric calculations",
    "capability": "reproject",
    "config": {
      "crs": "EPSG:2154",
      "order": 1
    },
    "enabled": true
  },
  {
    "name": "buffer_protection",
    "description": "50 m protection zone",
    "capability": "buffer",
    "config": {
      "distance": 50,
      "order": 2
    },
    "enabled": true
  },
  {
    "name": "add_area",
    "description": "Calculate the area of each buffer zone",
    "capability": "area_length",
    "config": {
      "order": 3
    },
    "enabled": true
  }
]
```

Rules are executed in ascending `config.order`. In case of a tie, the order in the array is respected.

## Reference by capability

### `buffer`

Applies a metric buffer around geometries.

```json
{
  "capability": "buffer",
  "config": {
    "distance": 100,
    "crs_meters": "EPSG:3857",
    "order": 0
  }
}
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `distance` | float | `0.0` | Distance in meters |
| `crs_meters` | string | `EPSG:3857` | Intermediate metric CRS for projection |

### `filter`

Filters features by a Python-like expression on attributes.

```json
{
  "capability": "filter",
  "config": {
    "expression": "population > 10000 and region == 'Bretagne'",
    "order": 0
  }
}
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `expression` | string | Python expression evaluated on each feature (column access by name) |

::: warning Security
Expressions are evaluated with `eval()`. In production, use the REST API with authentication to prevent injection.
:::

### `reproject`

Reprojects geometries to another CRS.

```json
{
  "capability": "reproject",
  "config": {
    "crs": "EPSG:4326",
    "order": 1
  }
}
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `crs` | string | Target CRS (e.g. `EPSG:2154`, `EPSG:4326`) |

### `clip`

Clips features to the extent of a reference layer.

```json
{
  "capability": "clip",
  "config": {
    "ref_layer": "municipality",
    "order": 2
  }
}
```

The reference layer must be provided via `--ref-source` in the CLI:

```bash
gispulse run input.gpkg --rules rules.json -o output.gpkg \
  --ref-source municipality:data/municipalities.gpkg
```

### `intersects`

Keeps only features that intersect the reference layer.

```json
{
  "capability": "intersects",
  "config": {
    "ref_layer": "flood_zones",
    "order": 1
  }
}
```

### `spatial_join`

Spatial join between the processed layer and a reference layer.

```json
{
  "capability": "spatial_join",
  "config": {
    "ref_layer": "municipalities",
    "how": "left",
    "op": "intersects",
    "order": 2
  }
}
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `ref_layer` | string | required | Reference layer name |
| `how` | string | `left` | Join type: `left`, `inner`, `right` |
| `op` | string | `intersects` | Spatial predicate: `intersects`, `contains`, `within` |

### `centroid`

Replaces geometries with their centroid.

```json
{
  "capability": "centroid",
  "config": { "order": 3 }
}
```

### `area_length`

Calculates the area (polygons) and/or length (lines) of each feature, added as attribute columns.

```json
{
  "capability": "area_length",
  "config": {
    "area_column": "area_m2",
    "length_column": "perimeter_m",
    "order": 4
  }
}
```

### `dissolve`

Dissolves features grouped by an attribute value.

```json
{
  "capability": "dissolve",
  "config": {
    "by": "municipality_code",
    "order": 5
  }
}
```

### `union`

Merges all features into a single geometry.

```json
{
  "capability": "union",
  "config": { "order": 6 }
}
```

## Temporarily disabling rules

```json
{
  "name": "optional_buffer",
  "capability": "buffer",
  "config": { "distance": 50, "order": 2 },
  "enabled": false
}
```

The rule is skipped without removing it from the file.

## Best practices

- **Explicit order**: always define `config.order` even for a single rule.
- **Readable names**: prefer `filter_active_buildings` over `rule_1`.
- **CI validation**: integrate `gispulse validate rules.json` into your CI pipeline.
- **Version your rules**: `.json` files are configuration artifacts — commit them with your data.
- **One rule = one responsibility**: avoid overly complex filter expressions, break them down.

## Validation

```bash
gispulse validate rules/my_pipeline.json
```

Returns exit code `0` if all rules are valid, `1` otherwise. Integrable into CI/CD.

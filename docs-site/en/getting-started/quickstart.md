---
title: Quickstart — First job in 5 minutes
description: Create and run your first GISPulse pipeline in 5 minutes.
---

# Quickstart

You will have a working spatial pipeline in under 5 minutes.

## Prerequisites

- Python 3.10+ installed
- `pip install gispulse` completed

## Step 1 — Initialize a project

```bash
mkdir demo-gispulse && cd demo-gispulse
gispulse init --name demo
```

You get:

```
Initialized GISPulse project: demo
  rules/rules.json  — rule template
  data/             — put your data here
  output/           — results go here

Next steps:
  1. Copy your spatial file to data/
  2. Edit rules/rules.json
  3. gispulse run data/myfile.gpkg --rules rules/rules.json -o output/result.gpkg
```

## Step 2 — Prepare data

Use any vector file. For this example, let's download a public GeoJSON:

```bash
curl -L "https://raw.githubusercontent.com/datasets/geo-countries/master/data/countries.geojson" \
  -o data/countries.geojson
```

Inspect the file:

```bash
gispulse info data/countries.geojson
```

```
File:     data/countries.geojson
Format:   GeoJSON
Size:     0.24 MB
CRS:      EPSG:4326
Category: vector

1 layer(s):
  - countries: 177 features, MultiPolygon, EPSG:4326
```

## Step 3 — Write rules

Edit `rules/rules.json`:

```json
[
  {
    "name": "filter_europe",
    "description": "Keep only European countries",
    "capability": "filter",
    "config": {
      "expression": "CONTINENT == 'Europe'",
      "order": 0
    },
    "enabled": true
  },
  {
    "name": "buffer_50km",
    "description": "50 km buffer around each country",
    "capability": "buffer",
    "config": {
      "distance": 50000,
      "order": 1
    },
    "enabled": true
  }
]
```

Validate the rules without executing:

```bash
gispulse validate rules/rules.json
```

```
  OK  filter_europe
  OK  buffer_50km

2 rule(s) valid.
```

## Step 4 — Run the pipeline

```bash
gispulse run data/countries.geojson \
  --rules rules/rules.json \
  -o output/europe_buffered.gpkg
```

```
Loading data/countries.geojson (GeoJSON) [engine: python] ...
  [filter] filter_europe
  [buffer] buffer_50km
  44 features in -> 44 features out
  2 rule(s) applied [engine: python]
Output written to output/europe_buffered.gpkg (.gpkg)
```

## Step 5 — Visualize the result

```bash
gispulse serve output/europe_buffered.gpkg
```

```
Viewer at http://127.0.0.1:8765
```

Open your browser at `http://127.0.0.1:8765` to inspect the result in the embedded viewer.

## Going further

| Goal | Command |
|------|---------|
| DuckDB acceleration | `gispulse run ... --engine duckdb` |
| All layers in a GPKG | `gispulse run ... --all-layers` |
| Full Portal | `gispulse portal` |
| List capabilities | `gispulse capabilities` |
| List formats | `gispulse formats` |

- [Full CLI reference](/guide/cli)
- [All capabilities](/guide/capabilities)
- [Writing advanced rules](/guide/rules)

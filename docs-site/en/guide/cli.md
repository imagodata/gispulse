---
title: CLI — Full Reference
description: Documentation for all GISPulse CLI commands, their options, and usage examples.
---

# CLI — Full Reference

GISPulse installs as the `gispulse` command. All commands are headless and scriptable.

```bash
gispulse --help
```

## Available commands

| Command | Description |
|---------|-------------|
| `init` | Scaffold a new project |
| `run` | Execute a rule pipeline on a spatial file |
| `validate` | Validate a rules file without executing |
| `info` | Inspect spatial file metadata |
| `layers` | List layers in a spatial file |
| `formats` | List supported I/O formats |
| `capabilities` | List available capabilities |
| `serve` | Launch the embedded viewer for a file |
| `portal` | Launch the web Portal (visual editor) |
| `engine` | Launch the full engine (API + Portal + Viewer) as a single process |
| `doctor` | Diagnose the environment |
| `update` | Check for and apply updates (binary) |
| `jobs` | Manage jobs (list / status / cancel) |
| `marketplace` | Capability marketplace (list / search / install) |
| `template` | Project templates (list / use) |
| `telemetry` | Opt-in anonymous telemetry (status / enable / disable) |

---

## `gispulse init`

Scaffolds a new GISPulse project with rule templates and a Makefile.

```bash
gispulse init [DIRECTORY] [OPTIONS]
```

**Arguments:**

| Argument | Default | Description |
|----------|---------|-------------|
| `DIRECTORY` | `.` | Directory to initialize |

**Options:**

| Option | Description |
|--------|-------------|
| `--name`, `-n TEXT` | Project name (default: directory name) |

**Example:**

```bash
gispulse init ./my-project --name "My GIS project"
```

**Result:**

```
my-project/
├── rules/rules.json    # rule template
├── data/               # your spatial files
├── output/             # results
└── Makefile            # make run / make validate / make view
```

---

## `gispulse run`

Executes a rule pipeline on a spatial file. This is the main command.

```bash
gispulse run INPUT_FILE --rules RULES_FILE --output OUTPUT_FILE [OPTIONS]
```

**Required arguments:**

| Argument/Option | Description |
|-----------------|-------------|
| `INPUT_FILE` | Input spatial file (GPKG, GeoJSON, Shapefile, FlatGeobuf, CSV, Parquet, ...) |
| `--rules`, `-r` | JSON rules file |
| `--output`, `-o` | Output file (format detected from extension) |

**Options:**

| Option | Description |
|--------|-------------|
| `--layer`, `-l TEXT` | Layer name to process (default: first layer) |
| `--output-layer TEXT` | Layer name in the output file |
| `--all-layers`, `-A` | Process all layers (multi-layer formats, e.g. GPKG) |
| `--crs TEXT` | Force input CRS (e.g. `EPSG:4326`) if missing from file |
| `--ref-source TEXT` | External reference layer as `NAME:PATH` (repeatable) |
| `--engine`, `-e TEXT` | Engine: `python` (default) or `duckdb` |
| `--verbose`, `-v` | Enable DEBUG logs |

**Examples:**

```bash
# Simple pipeline
gispulse run data/parcels.gpkg \
  --rules rules/filtering.json \
  -o output/result.gpkg

# DuckDB engine (faster for large volumes)
gispulse run data/municipalities.gpkg \
  --rules rules/rules.json \
  -o output/result.gpkg \
  --engine duckdb

# All layers in a GPKG (styles copied automatically)
gispulse run data/project.gpkg \
  --rules rules/global.json \
  -o output/enriched_project.gpkg \
  --all-layers

# Specific layer with forced CRS
gispulse run data/points.csv \
  --rules rules/geocoding.json \
  -o output/points.gpkg \
  --layer points \
  --crs EPSG:2154

# With reference layers (for spatial join, clip, etc.)
gispulse run data/buildings.gpkg \
  --rules rules/analysis.json \
  -o output/enriched_buildings.gpkg \
  --ref-source municipalities:data/municipalities.gpkg \
  --ref-source zones:data/zoning.gpkg
```

**Output:**

```
Loading data/parcels.gpkg (GPKG) [engine: python] ...
  [filter] filter_agricultural
  [buffer] buffer_10m
  [reproject] to_wgs84
  1247 features in -> 892 features out
  3 rule(s) applied [engine: python]
Output written to output/result.gpkg (.gpkg)
```

---

## `gispulse validate`

Validates a JSON rules file without executing any processing. Useful in CI/CD.

```bash
gispulse validate RULES_FILE
```

```bash
gispulse validate rules/rules.json
```

```
  OK    filter_agricultural
  OK    buffer_10m
  FAIL  reproject_to_wgs84
        - [config.crs] Invalid CRS 'EPSG:9999'

Validation failed.
```

Returns exit code `1` if a rule is invalid — integrable into a CI pipeline.

---

## `gispulse info`

Inspects spatial file metadata: format, CRS, layers, feature count, styles.

```bash
gispulse info INPUT_FILE
```

```bash
gispulse info data/project.gpkg
```

```
File:     data/project.gpkg
Format:   GPKG
Size:     12.43 MB
CRS:      EPSG:2154
Category: vector

3 layer(s):
  - parcels: 8420 features, Polygon, EPSG:2154
  - buildings: 12841 features, MultiPolygon, EPSG:2154
  - roads: 3201 features, LineString, EPSG:2154

2 style(s):
  - parcels/parcels_style (QML + SLD)
  - buildings/buildings_style (QML)
```

---

## `gispulse layers`

Lists only the layer names in a spatial file.

```bash
gispulse layers INPUT_FILE
```

```bash
gispulse layers data/project.gpkg
```

```
3 layer(s):
  - parcels
  - buildings
  - roads
```

---

## `gispulse formats`

Lists all supported input/output formats.

```bash
gispulse formats
```

```
Supported formats:

  Extension    Driver               Read  Write
  ──────────── ──────────────────── ───── ─────
  .csv         CSV                   yes    yes
  .dxf         DXF                   yes    yes
  .fgb         FlatGeobuf            yes    yes
  .geojson     GeoJSON               yes    yes
  .gml         GML                   yes    yes
  .gpkg        GPKG                  yes    yes
  .kml         KML                   yes     no
  .parquet     GeoParquet            yes    yes
  .shp         ESRI Shapefile        yes    yes
  ...
```

See [I/O Formats](/guide/formats) for full documentation.

---

## `gispulse capabilities`

Lists all available capabilities with their parameters.

```bash
gispulse capabilities
```

```
10 capability(ies):
  - buffer (distance, cap_style): Buffer geometries by a fixed distance
  - union: Dissolve all features into a single geometry
  - reproject (crs): Reproject to a target CRS
  - filter (expression): Filter features by attribute expression
  - clip (ref_layer): Clip features to a reference layer extent
  - intersects (ref_layer): Keep only features intersecting reference layer
  - spatial_join (ref_layer, how, op): Spatial join with reference layer
  - centroid: Replace geometries with their centroid
  - area_length: Calculate area and/or length, add as attribute
  - dissolve (by): Dissolve features grouped by attribute
```

See [Capabilities](/guide/capabilities) for documentation of each capability.

---

## `gispulse serve`

Launches the embedded spatial viewer for a spatial file (read-only).

```bash
gispulse serve INPUT_FILE [OPTIONS]
```

**Options:**

| Option | Default | Description |
|--------|---------|-------------|
| `--port`, `-p` | `8765` | Listening port |
| `--host` | `127.0.0.1` | Host |
| `--dev` | `false` | Dev mode: API only, no static files |

```bash
gispulse serve output/result.gpkg --port 9000
# Viewer at http://127.0.0.1:9000
```

---

## `gispulse portal`

Launches the GISPulse Portal — visual pipeline editor and dataset manager.

```bash
gispulse portal [OPTIONS]
```

**Options:**

| Option | Default | Description |
|--------|---------|-------------|
| `--port`, `-p` | `8001` | Listening port |
| `--host` | `0.0.0.0` | Host (0.0.0.0 = LAN accessible) |
| `--data-dir`, `-d` | `~/.gispulse/data` | Directory for uploaded datasets |
| `--dev` | `false` | Dev mode: API only |

```bash
# Standard startup
gispulse portal

# GISPulse Portal at http://127.0.0.1:8001
```

---

## `gispulse doctor`

Full environment diagnostics: Python, GDAL, DuckDB, PostGIS, optional dependencies.

```bash
gispulse doctor
```

```
✓ GISPulse    v1.1.1
✓ Python      v3.12.3
✓ GDAL        v3.8.4
✓ DuckDB      v1.1.3 + spatial OK
✓ GeoPandas   v0.14.3
✓ PyOGRIO     v0.9.0
⚠ PostGIS     not configured (set GISPULSE_DSN)
⚠ Rasterio    not installed (pip install "gispulse[raster]")
✓ API         FastAPI 0.111.x
```

---

## `gispulse update`

Checks for and applies updates (available only for the PyInstaller binary).

```bash
# Check without installing
gispulse update --check

# Apply update
gispulse update --force
```

---

## `gispulse engine`

Launches the full GISPulse stack (API + Portal + Viewer) as a single process — convenient for local or single-server deployments.

```bash
gispulse engine --host 0.0.0.0 --port 8000
```

| Option | Default | Description |
|--------|---------|-------------|
| `--host` | `127.0.0.1` | Bind address |
| `--port` | `8000` | HTTP port |
| `--mode` | `portal` | `portal` (visual editor) or `api` (headless) |
| `--reload` | off | Enable uvicorn auto-reload (dev) |

---

## `gispulse jobs`

Manages jobs persisted by the API (Phase 2 / Phase 3 modes).

```bash
gispulse jobs list                # recent jobs
gispulse jobs status <JOB_ID>     # one-shot status
gispulse jobs cancel <JOB_ID>     # request cancellation
```

---

## `gispulse marketplace`

Capability marketplace — discover, inspect and install community or Pro capabilities distributed via Python entry points.

```bash
gispulse marketplace list                 # installed + available
gispulse marketplace search <keyword>
gispulse marketplace install <package>
```

---

## `gispulse template`

Project template management.

```bash
gispulse template list
gispulse template use TEMPLATE [--output-dir DIR]
```

Built-in templates:
- `environmental_monitoring` — NDVI / STAC pipeline
- `ftth_network_analysis` — FTTH network analysis
- `validation_plu_cnig` — CNIG-compliant PLU validation

---

## `gispulse telemetry`

Manages **opt-in** anonymous telemetry. No data is sent until `--enable` has been run. Project identifiers and paths are excluded by design.

```bash
gispulse telemetry --status
gispulse telemetry --enable
gispulse telemetry --disable
```

| Option | Description |
|--------|-------------|
| `--status`, `-s` | Show current status (enabled / disabled / flag path) |
| `--enable` | Enable telemetry (creates `~/.config/gispulse/telemetry.enabled`) |
| `--disable` | Disable telemetry |

Equivalent to `GISPULSE_TELEMETRY=1` / `GISPULSE_TELEMETRY=0` environment variables for scripted setups.

---

## CI/CD usage

```yaml
# .github/workflows/validate.yml
- name: Validate GISPulse rules
  run: |
    pip install gispulse
    gispulse validate rules/rules.json
```

Exit codes: `0` = success, `1` = error (invalid rule, missing file, etc.).

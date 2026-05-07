---
title: Walkthrough — GeoJSON CDC
description: Watch a GeoJSON file, fire a webhook on every edit. No GeoPackage required, no GIS-client plugin. Added in v1.6.2 (Format Frontier).
---

# Walkthrough — GeoJSON CDC

> **Promise**: edit `places.geojson` (in QGIS, vim, a Python script,
> anything) → GISPulse detects the diff within a second → webhook
> POST to your endpoint of choice.

This walkthrough exercises the new **DuckDBDiffEngine** infrastructure
shipped in v1.6.2. No SQLite triggers, no companion files — just
mtime watch + DuckDB `ST_Read` snapshot diff. The same pipeline works
for FlatGeobuf, Shapefile, KML, CSV+WKT and MapInfo TAB.

## What you'll see

A **GeoJSON** layer of points of interest. Every add / remove /
modify fires a webhook that logs the payload to your console.

| Before | After save |
|---|---|
| File edited in QGIS / vim, watcher idle | `dml.changed` event broadcast → webhook POST with `dataset_id`, `op`, `feature_id`, `geom_changed` |

## Prerequisites

- `gispulse` ≥ 1.6.2 (`pipx install gispulse`)
- DuckDB spatial extension (auto-installed on first use, or
  `gispulse doctor --install-spatial`)
- An HTTP endpoint to receive the webhooks. Quick options:
  [webhook.site](https://webhook.site/),
  [requestbin.com](https://requestbin.com/), or a local Python
  mini-server.

## Setup (~30 seconds)

### 1. Create a demo GeoJSON

```bash
mkdir -p ~/gispulse-demo && cd ~/gispulse-demo

cat > places.geojson <<'EOF'
{
  "type": "FeatureCollection",
  "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::4326"}},
  "features": [
    {"type": "Feature", "properties": {"name": "Paris", "population": 2140000},
     "geometry": {"type": "Point", "coordinates": [2.35, 48.85]}},
    {"type": "Feature", "properties": {"name": "Lyon", "population": 513000},
     "geometry": {"type": "Point", "coordinates": [4.83, 45.75]}},
    {"type": "Feature", "properties": {"name": "Marseille", "population": 868000},
     "geometry": {"type": "Point", "coordinates": [5.37, 43.30]}}
  ]
}
EOF
```

### 2. Write the rules

```yaml
# triggers.yaml
gpkg: ./places.geojson    # the runtime routes ``.geojson`` to ``duckdb_diff`` automatically

triggers:
  - name: notify_changes
    table: places          # name = file stem
    when: [INSERT, DELETE]  # see "set-diff semantics" below
    actions:
      - type: webhook
        url: https://webhook.site/YOUR-UNIQUE-ID
```

> **Set-diff semantics — important**: a GeoJSON has no stable PK. A
> **modification** of a feature surfaces as `DELETE` (old hash) +
> `INSERT` (new hash). The trigger therefore needs `when: [INSERT,
> DELETE]` to react on either side. See [Supported formats](../formats.md)
> for the full picture.

### 3. Start the watcher

```bash
gispulse triggers watch --rules triggers.yaml --dataset places.geojson
```

The terminal prints:

```text
[info] watching places.geojson via duckdb_diff engine
[info] first poll: 3 INSERT events emitted (initial baseline)
```

At this point the webhook has already received 3 POSTs (one per
feature in the baseline). If you want to start "empty" without a
baseline, delete the sidecar `places.geojson.gispulse-snapshot.duckdb`
after the first tick — the watcher restarts from zero.

## Test the edit (~1 min)

### Edit from a script

```bash
# Append Toulouse to the feature list
python -c "
import json
data = json.load(open('places.geojson'))
data['features'].append({
    'type': 'Feature',
    'properties': {'name': 'Toulouse', 'population': 493000},
    'geometry': {'type': 'Point', 'coordinates': [1.44, 43.60]}
})
json.dump(data, open('places.geojson', 'w'), indent=2)
"
```

The watcher detects the change within a second:

```text
[info] mtime changed, re-reading via DuckDB ST_Read
[info] diff: 1 INSERT (toulouse hash), 0 DELETE
[info] webhook POSTed: 200 OK
```

The webhook receives:

```json
{
  "dataset_id": "places",
  "table": "places",
  "op": "INSERT",
  "fid": "<32-hex-hash>",
  "change_id": 4,
  "ts": "2026-05-07T22:13:01.234Z",
  "geom_changed": true
}
```

### Edit from QGIS

Open `places.geojson` in QGIS, toggle the layer to edit mode, move
one feature's coordinates, save (`Ctrl+S`). The watcher notices:

```text
[info] diff: 1 INSERT (lyon hash with new coords), 1 DELETE (lyon old hash)
```

Two webhooks POSTed (`DELETE` + `INSERT`) because the format has no
stable PK. The webhook consumer can correlate them via timestamp or
ignore the `DELETE` if the following `INSERT` carries a different
`feature_id`.

## How it works under the hood

```
edit places.geojson (any tool)
        │
        ▼
mtime tick (default 200ms)
        │
        ▼
FileBlobChangeDetector.poll()
        │
        ▼
DuckDB ST_Read('places.geojson')  ← reads the file natively
        │
        ▼
hash = md5(ST_AsWKB(geom) || json_object(props))   ← excludes OGC_FID
        │
        ▼
diff vs sidecar `.gispulse-snapshot.duckdb`
        │
        ▼
ChangeRecord{INSERT|DELETE} → webhook + WS broadcast
```

**Sidecar snapshot**: a `.gispulse-snapshot.duckdb` file lives next
to the GeoJSON. It holds the last-known state for the diff. Don't
commit it to git (add `*.gispulse-snapshot.duckdb` to your
`.gitignore`).

## Honest limitations

- **No UPDATE detected** — set-diff. An edit surfaces as
  DELETE+INSERT. See above.
- **Polling at 200ms by default** — not inotify. Configurable via
  `--poll-interval`. Sub-second is fine, sub-millisecond is not the
  goal.
- **One layer per file** — a FeatureCollection = one layer.
  Multi-layer = GeoPackage pipeline.
- **No `execute_sql` against the GeoJSON** — `DuckDBDiffEngine` is a
  CDC adapter, not a query engine. For ad-hoc SQL use `gispulse run`
  with the standalone DuckDB engine.

## Variants

The same `triggers.yaml` (only the file extension changes) works on:

| Format | Engine | Notes |
|---|---|---|
| `.fgb` | `duckdb_diff` | Single-file mtime, ultra-fast |
| `.shp` | `duckdb_diff` | Watches all 5 companions (`.shp / .dbf / .shx / .prj / .cpg`) |
| `.kml` | `duckdb_diff` | Single-file mtime |
| `.csv` | `duckdb_diff` | Geometry as a WKT column (write with `GEOMETRY=AS_WKT`) |
| `.tab` | `duckdb_diff` (pyogrio fallback) | Watches the 4 companions; routes through pyogrio because DuckDB GDAL ships without the MapInfo driver |
| `.gpkg` | `gpkg` (SQLite triggers) | Native mode, exact deltas, transactional |
| `.sqlite`, `.db` | `spatialite` (SQLite triggers) | Like GPKG without the GPKG marker |

The hash contract is identical between the DuckDB path and the
pyogrio path (cf. `_PYOGRIO_FALLBACK_SUFFIXES` in
`persistence/file_blob_cdc.py`) — the same file produces the same
events regardless of which read path serves it.

## See also

- [Supported I/O formats](../formats.md) — "CDC file-blob" section
- [ADR 0001 — DuckDB-spatial = contract dialect](https://github.com/imagodata/gispulse/blob/main/docs/adr/0001-dsl-sql-dialect.md)
- [Walkthrough Parcels](./parcels.md) — native GPKG equivalent (SQLite triggers, exact deltas)
- [Walkthrough Audit](./audit.md) — different pattern: `validate:` rules vs triggers

# Data loading — `gispulse.load` & `gispulse.publish`

GISPulse reads **any supported source** behind a single verb and returns a
plain `GeoDataFrame`, so every capability runs on the result unchanged:

```python
import gispulse

gdf = gispulse.load("data/parcels.parquet")
out = gispulse.apply("buffer", gdf, distance=25.0)
```

`load` reconciles the three reading paths the engine already owned —
local files, remote protocol sources, and lazy DuckDB scans — and
normalises their heterogeneous results into one GeoDataFrame.

## Sources

| Form | Example | Resolves to |
|------|---------|-------------|
| Local file | `load("parcels.gpkg")`, `load("x.parquet")` | `read_vector` (GPKG, GeoJSON, GeoParquet w/ bbox, Shapefile, FlatGeobuf, CSV, XLSX, KMZ, …) |
| Remote table | `load("s3://bucket/x.parquet")`, `load("https://h/x.fgb")` | `remote-table` fetcher (DuckDB scan) |
| Remote file | `load("https://h/export.geojson")` | `download` fetcher |
| OGC / WFS / STAC | `load("ogc-features://h/collections/x")`, `load("wfs://h/wfs")` | the matching protocol fetcher |
| Datamart | `load("datamart://ref/parcels")` | a curated table (see below) |
| GeoNode | `load("geonode://prod/roads")` | the instance's GeoServer WFS (see below) |
| Descriptor | `load({"protocol": "wfs", "endpoint": "https://h/wfs", "params": {...}})` | explicit `AccessSpec` |

Common options: `bbox=(minx, miny, maxx, maxy)` (pushed down to the
reader/fetcher), `layer=` (multi-layer files), `crs=` (forced when the
source declares none), `lazy=True` (remote sources → a `LazyDataset`).

### Lazy sources

`load(src, lazy=True)` on a remote source returns a `LazyDataset` wrapping
a DuckDB scan — no bytes move until you materialise:

```python
handle = gispulse.load("s3://bucket/huge.parquet", lazy=True)
gdf = handle.to_geodataframe(bbox=(2.2, 48.8, 2.5, 48.9))  # only the rows in the bbox
gispulse.apply("cluster_dbscan", gdf, eps=100.0)
```

## Datamarts

A **datamart** is a named collection of curated Parquet/GeoParquet tables
(local, S3 or HTTP) scanned by DuckDB. It adds a naming indirection so
pipelines reference stable logical names instead of storage paths:

```python
from gispulse.persistence.datamart import DATAMARTS, Datamart

DATAMARTS.register(Datamart(name="ref", location="s3://bucket/marts/ref"))
gispulse.load("datamart://ref/parcels")          # → s3://bucket/marts/ref/parcels.parquet
```

Or declare them with `GISPULSE_DATAMARTS` (JSON):

```bash
export GISPULSE_DATAMARTS='{"ref": {"location": "s3://bucket/marts/ref"}}'
```

### DuckDB-file marts

A mart with `kind="duckdb"` points its `location` at a single DuckDB
database file; a table is a relation *inside* that file (no per-table
files). It is attached **read-only** (so concurrent readers don't contend
on a write lock) and selected; bbox push-down still applies via an
`ST_Intersects` predicate on a `geom` column.

```python
DATAMARTS.register(
    Datamart(name="warehouse", location="/data/warehouse.duckdb", kind="duckdb")
)
gispulse.load("datamart://warehouse/parcels")        # → SELECT * FROM parcels
gispulse.load("datamart://warehouse/parcels", bbox=(...))  # bbox pushed down
```

```bash
export GISPULSE_DATAMARTS='{"warehouse": {"location": "/data/warehouse.duckdb", "kind": "duckdb"}}'
```

## GeoNode (read + write)

GeoNode datasets are addressed as `geonode://<instance>/<dataset>`.

**Read** resolves to the instance's GeoServer WFS endpoint:

```python
from gispulse.persistence.geonode import GEONODES, GeoNode

GEONODES.register(GeoNode(name="prod", host="https://geo.example.org"))
gispulse.load("geonode://prod/roads", bbox=(2.2, 48.8, 2.5, 48.9))
```

An unregistered authority is treated as a bare host with default paths, so
`load("geonode://geo.example.org/roads")` works without prior declaration.
Instances can also be declared via `GISPULSE_GEONODE` (JSON).

**Write** packages a GeoDataFrame as a GeoPackage and uploads it through
the GeoNode REST API v2:

```python
gispulse.publish(result, "geonode://prod/roads", auth="<token>")
```

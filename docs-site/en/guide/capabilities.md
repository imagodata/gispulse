---
title: Available Capabilities
description: Full reference of the 117 GISPulse capabilities — vector, attributes, validation, classification, spatial statistics, topology, temporal, 3D pointcloud, raster, network, and PostGIS SQL.
---

# Available Capabilities

Capabilities are the unit operations GISPulse can apply to spatial datasets. They are invoked inside JSON rules via the `capability` field.

```bash
# List all capabilities
gispulse capabilities
```

Each rule calls a capability and passes it a `config` block. The parameters below are the keys of that block.

```json
{
  "capability": "<name>",
  "ref_layer": "<optional>",
  "config": { "...": "..." }
}
```

::: tip Source of truth
The code is authoritative. For the exact schema of a capability (parameters, types, defaults):

```python
from capabilities.registry import REGISTRY
REGISTRY.get("buffer")().get_schema()
```
:::

**Total registered capabilities: 117** — across 18 categories.

[[toc]]

---

## Vector — selection & join (Community)

Available in every tier. Capabilities tagged `DuckDB` / `PostGIS` go through the multi-backend Strategy pattern (dispatch by input size).

### `buffer`

Metric buffer around geometries. Automatically reprojects to a metric CRS when needed.

**Engines:** Python (GeoPandas), DuckDB (`ST_Buffer`, > 50,000 features), PostGIS (server-side).

```json
{
  "capability": "buffer",
  "config": { "distance": 100, "crs_meters": "EPSG:3857" }
}
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `distance` | float | `0.0` | Distance in meters |
| `crs_meters` | string | `EPSG:3857` | Intermediate metric projection |
| `cap_style` | string | `round` | `round`, `flat`, `square` |
| `join_style` | string | `round` | `round`, `mitre`, `bevel` |

### `filter`

Filters features by attribute expression and/or spatial predicate. Supports cross-layer references.

**Engines:** Python (`gdf.query` + Shapely), DuckDB (SQL), PostGIS.

```json
{
  "capability": "filter",
  "config": {
    "expression": "surface > 500 and usage == 'residential'",
    "spatial_predicate": "intersects",
    "ref_layer": "flood_zones",
    "buffer_distance": 50
  }
}
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `expression` | string | Python expression evaluated against columns |
| `spatial_predicate` | string | `intersects`, `within`, `contains`, `crosses`, `overlaps`, `touches`, `dwithin` |
| `ref_layer` | string | Reference layer for the spatial predicate |
| `ref_wkt` / `ref_geojson` | string / object | Explicit WKT or GeoJSON reference geometry |
| `buffer_distance` | float | Buffer (meters) applied to the reference geometry |

### `intersects`

Keeps features intersecting a reference geometry or layer (shortcut for a spatial `filter`).

```json
{ "capability": "intersects", "config": { "ref_layer": "flood_zones" } }
```

### `clip`

Clips features to the extent of a reference layer.

```json
{ "capability": "clip", "config": { "ref_layer": "study_area" } }
```

### `spatial_join`

Spatial join between the processed layer and a reference layer. Attaches reference attributes to matching features.

```json
{
  "capability": "spatial_join",
  "config": { "ref_layer": "municipalities", "how": "left", "op": "intersects" }
}
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `ref_layer` | string | *required* | Reference layer |
| `how` | string | `left` | `left`, `inner`, `right` |
| `op` | string | `intersects` | `intersects`, `contains`, `within` |

### `nearest_neighbor`

Joins attributes from the `k` nearest features of a reference layer, with optional `max_distance`.

```json
{
  "capability": "nearest_neighbor",
  "config": { "ref_layer": "schools", "k": 1, "max_distance": 2000, "distance_col": "dist_school_m" }
}
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `ref_layer` | string | *required* | Reference layer |
| `k` | int | `1` | Number of neighbours |
| `max_distance` | float | *none* | Max distance in meters |
| `distance_col` | string | `distance` | Name of the emitted distance column |

### `spatial_aggregate`

Aggregates values from a reference layer via a spatial predicate (`count`, `sum`, `mean`, `min`, `max`).

```json
{
  "capability": "spatial_aggregate",
  "config": {
    "ref_layer": "households",
    "aggregations": { "population": "sum" },
    "predicate": "contains"
  }
}
```

### `reproject`

Reproject to a target CRS.

```json
{ "capability": "reproject", "config": { "target_crs": "EPSG:2154" } }
```

---

## Vector — overlay & combine (Community)

Combine multiple layers into one — either geometrically (FME / QGIS-style overlay) or by concatenation.

| Capability | Description | Key parameters |
|-----------|-------------|----------------|
| `overlay_intersection` | Geometric intersection of two layers — attributes inherited from both sides. | `ref_layer`, `keep_geom_type`, `suffix_left`, `suffix_right` |
| `overlay_union` | Geometric union — keeps A-only, B-only and A∩B fragments. | `ref_layer`, `keep_geom_type`, `suffix_left`, `suffix_right` |
| `erase` | Geometric difference — removes from A whatever is covered by B (A attributes preserved). | `ref_layer`, `keep_geom_type` |
| `merge_layers` | Concatenates the primary layer with a list of `ref_layers`. | `ref_layers` (array) |
| `classify_by_ring` | Classifies every feature by the smallest containing ring (concentric isochrones / buffers) — emits `value` / `class` / `color`. | `ref_layers`, `ring_field`, `outside_value`, `palette` |

```json
{
  "capability": "overlay_intersection",
  "config": { "ref_layer": "zoning_plu", "suffix_right": "_plu" }
}
```

```json
{
  "capability": "classify_by_ring",
  "config": {
    "ref_layers": ["iso_500", "iso_1000", "iso_1500"],
    "ring_field": "cost_budget",
    "palette": "YlGnBu"
  }
}
```

---

## Vector — derived geometry (Community)

| Capability | Description | Key parameters |
|-----------|-------------|----------------|
| `centroid` | Replaces each geometry with its centroid. | — |
| `envelope` | Axis-aligned bounding box. | — |
| `oriented_bbox` | Minimum rotated rectangle (buildings, parcels). | — |
| `min_bounding_circle` | Minimum bounding circle (Shapely ≥ 2.1). | — |
| `convex_hull` | Convex hull. Supports `by_group` / `dissolve`. | `by_group`, `dissolve` |
| `concave_hull` | k-nearest concave hull. | `k`, `ratio` |
| `alpha_shape` | Alpha-shape (generalised concave hull). | `alpha` |
| `delaunay_triangulation` | Delaunay triangulation of vertices. | — |
| `voronoi_polygons` | Voronoi tessellation of input points. | `bounds` |
| `polygonize` | Builds polygons from a noded line network. | `snap_tolerance` |

```json
{ "capability": "convex_hull", "config": { "by_group": "municipality" } }
```

---

## Vector — editing & metrology (Community)

| Capability | Description | Key parameters |
|-----------|-------------|----------------|
| `area_length` | Adds `area_m2` / `length_m` columns. | `area_column`, `length_column` |
| `calculate` | Evaluates an expression to build a new column (sum, avg, min, max, median, std, ...). | `expression`, `output_field` |
| `dissolve` | Dissolves features by attribute (GROUP BY + ST_Union). | `by` |
| `union` | Merges every feature into a single geometry. | — |
| `symmetric_difference` | XOR between each feature and the union of a reference layer. | `ref_layer` |
| `vector_diff` | Compares two layers by id and flags `added` / `removed` / `modified` / `unchanged`. | `ref_layer`, `id_col`, `geometry_tolerance` |
| `make_valid` | Repairs invalid geometries (self-intersections, duplicate rings). | — |
| `simplify` | Simplification (Douglas-Peucker, Visvalingam-Whyatt, topology-preserving). | `tolerance`, `algorithm` |
| `chaikin_smooth` | Iterative corner-cutting smoother. | `iterations` |
| `densify_vertices` | Inserts vertices at a target spacing. | `max_distance`, `n_segments` |
| `snap_to_grid` | Snaps vertex coordinates onto a grid. | `grid_size` |
| `offset_curve` | Parallel line at a signed distance. | `distance` |
| `line_merge` | Merges touching line segments. | — |
| `line_substring` | Line substring between two measures. | `start_measure`, `end_measure` |
| `line_locate_point` | Projects points onto the nearest reference line + computes the measure. | `ref_layer` |
| `extract_vertices` | Emits one `Point` per vertex with indices. | — |
| `extract_segments` | Splits each line / polygon boundary into 2-point segments. | — |

```json
{ "capability": "simplify", "config": { "tolerance": 5.0, "algorithm": "vw" } }
```

```json
{
  "capability": "calculate",
  "config": { "output_field": "price_m2", "expression": "price / surface_m2" }
}
```

---

## Vector — attribute manipulation (Community)

Non-spatial schema operations — add, drop, rename, join, lookup table, null fallback.

| Capability | Description | Key parameters |
|-----------|-------------|----------------|
| `add_field` | Adds one or more columns with a default value. | `fields`, `overwrite` |
| `drop_field` | Drops columns (geometry is protected). | `fields`, `ignore_missing` |
| `select_columns` | Keeps only the listed columns (geometry always preserved). | `fields` |
| `rename_field` | Renames columns via `{old: new}` mapping. | `mapping`, `ignore_missing` |
| `cast_field` | Casts columns to a target dtype. | `casts`, `errors` |
| `attribute_join` | Non-spatial join on a key column (left / right / inner / outer). | `ref_layer`, `left_on`, `right_on`, `how`, `columns`, `prefix`, `suffix` |
| `lookup_table` | Maps a column through a dict with default fallback. | `source_col`, `target_col`, `mapping`, `default` |
| `coalesce_fields` | First non-null value across a list of columns. | `sources`, `target_col` |
| `case_when` | SQL-style conditional column. | `target_col`, `cases`, `else_` |

```json
{
  "capability": "case_when",
  "config": {
    "target_col": "tier",
    "cases": [
      { "when": "population > 100000", "value": "A" },
      { "when": "population > 10000", "value": "B" }
    ],
    "else_": "C"
  }
}
```

```json
{
  "capability": "attribute_join",
  "config": { "ref_layer": "communes_insee", "left_on": "code_insee", "how": "left" }
}
```

---

## Vector — pivot / unpivot (Community)

Long ↔ wide reshape (pandas semantics), geometry preserved.

| Capability | Description | Key parameters |
|-----------|-------------|----------------|
| `pivot` | Long → wide: `columns` becomes new columns, `values` populates the cells. | `index`, `columns`, `values`, `aggfunc`, `fill_value`, `geom_strategy` |
| `unpivot` | Wide → long: emits `(variable, value)` pairs from `value_vars`. | `id_vars`, `value_vars`, `var_name`, `value_name` |

```json
{
  "capability": "pivot",
  "config": {
    "index": "municipality_id",
    "columns": "year",
    "values": "population",
    "aggfunc": "sum",
    "geom_strategy": "first"
  }
}
```

`geom_strategy` (`first`, `union`, `centroid`) controls how geometries collapse when several rows in the same group carry different geometries.

---

## Vector — ordered selection & sampling (Community)

Sort, deduplicate, random sample, top-N — purely attribute operations.

| Capability | Description | Key parameters |
|-----------|-------------|----------------|
| `sort` | Orders by one or more columns. | `by`, `ascending`, `na_position` |
| `deduplicate` | Drops duplicates by attribute key (compare with `duplicate_geometry` which dedupes on geometry). | `keys`, `keep`, `order_by`, `ascending` |
| `random_sample` | Random sample (`n` or `fraction`, reproducible via `seed`). | `n`, `fraction`, `seed`, `replace` |
| `top_n` | Keeps the top-N features ordered by a column. | `n`, `by`, `ascending` |

```json
{ "capability": "top_n", "config": { "n": 100, "by": "rdm_m2", "ascending": false } }
```

---

## Vector — multipart & Z/M dimensions (Community)

Multipart ↔ singlepart conversion and Z (elevation) / M (measure) dimension management.

| Capability | Description | Key parameters |
|-----------|-------------|----------------|
| `multipart_to_singleparts` | Explodes `Multi*` features — one row per part, attributes duplicated. | `reset_index`, `drop_empty` |
| `singleparts_to_multipart` | Collects single-parts into `Multi*`, optionally grouped by attributes. | `by`, `agg` |
| `add_z` | Adds a Z dimension (constant or `from_column`). | `z`, `from_column` |
| `drop_z` | Strips the Z dimension → 2D layer. | — |
| `add_m` | Adds an M dimension (constant or column). | `m`, `from_column` |
| `drop_m` | Strips the M dimension. | — |

```json
{ "capability": "add_z", "config": { "from_column": "ground_elevation" } }
```

---

## Vector — geometric transforms (Community)

Affine transforms, axis swap, line reversal — fast operations that don't touch the CRS.

| Capability | Description | Key parameters |
|-----------|-------------|----------------|
| `affine_transform` | Translate / rotate / scale / skew (declared order, configurable origin). | `translate`, `rotate`, `scale`, `skew`, `origin` |
| `swap_xy` | Swaps X and Y on every vertex (rescue mis-exported lat/lon files). | — |
| `reverse_lines` | Reverses vertex order for `LineString` / `MultiLineString`. | `ignore_non_lines` |

```json
{
  "capability": "affine_transform",
  "config": { "rotate": 30, "origin": "centroid" }
}
```

---

## Vector — boundary & projection (Community)

Boundary extraction, hole handling, geometry-type coercion, CRS declaration without transform.

| Capability | Description | Key parameters |
|-----------|-------------|----------------|
| `boundary` | Topological boundary of every geometry. | `drop_empty` |
| `extract_holes` | Extracts interior rings (holes) as polygon features. | `parent_id_col`, `hole_index_col` |
| `force_geometry_type` | Coerces to a target type (Multi → single via explode, etc.). | `target`, `on_multi`, `on_invalid` |
| `assign_projection` | Sets the layer CRS **without** reprojecting (use `reproject` to transform). | `crs`, `allow_override` |

```json
{ "capability": "force_geometry_type", "config": { "target": "Polygon", "on_multi": "explode" } }
```

::: warning `assign_projection` ≠ `reproject`
`assign_projection` only changes the CRS metadata — it does not touch the coordinates. Use it to fix a layer with a wrong or missing CRS.
:::

---

## Temporal (Community)

Filters and joins on datetime columns.

| Capability | Description | Key parameters |
|-----------|-------------|----------------|
| `temporal_filter` | Filters by a `[start, end]` window on a datetime column. Inversion supported. | `time_col`, `start`, `end`, `include_start`, `include_end`, `invert` |
| `temporal_join` | Exact or as-of join (`backward` / `forward` / `nearest`) with tolerance. | `ref_layer`, `left_on`, `right_on`, `strategy`, `by`, `tolerance`, `columns` |

```json
{
  "capability": "temporal_join",
  "config": {
    "ref_layer": "weather_hourly",
    "left_on": "ts_observation",
    "strategy": "nearest",
    "tolerance": "30min",
    "by": "station_id"
  }
}
```

---

## Validation (Community)

Data quality controls. Validation capabilities return a **violations layer** (one row per issue detected).

| Capability | Description |
|-----------|-------------|
| `topology_check` | Invalid geometries, self-intersections, overlapping polygons. |
| `duplicate_geometry` | Exact or tolerance-based duplicate detection. |
| `attribute_validation` | Types, nullability, value ranges, regex, uniqueness. |
| `completeness_check` | Null ratio per column, spatial coverage relative to a reference extent. |

```json
{
  "capability": "topology_check",
  "config": { "check_overlaps": true, "check_self_intersections": true }
}
```

```json
{
  "capability": "attribute_validation",
  "config": {
    "schema": [
      { "field": "population", "type": "integer", "min": 0 },
      { "field": "code_insee", "unique": true, "pattern": "^[0-9]{5}$" }
    ]
  }
}
```

---

## Classification & styling (Community)

Prepares data for thematic cartography: bucketing, colour ramps, style generation.

| Capability | Description | Key parameters |
|-----------|-------------|----------------|
| `classify` | Buckets a numeric column into N classes (`quantile` / `equal_interval` / `manual` / `jenks` / `pretty` / `std_dev`). | `column`, `method`, `k`, `palette` |
| `classify_categorical` | Classification by unique values with optional `other` bucket. | `column`, `palette`, `other_threshold` |
| `head_tail_breaks` | Head/Tail breaks (Jiang 2013) — number of classes driven by the distribution. | `column` |
| `normalize` | Normalisation (`minmax` / `zscore` / `log` / `log1p` / `rank` / `percent`). | `column`, `method`, `denom` |
| `continuous_ramp` | Continuous colour gradient (no classification). | `column`, `palette` |
| `graduated_size` | Proportional symbol size driven by a numeric column. | `column`, `min_size`, `max_size` |
| `choropleth` | Classification + `LayerStyleDef` + legend (QML/SLD export). | `column`, `method`, `k`, `palette` |
| `bivariate_choropleth` | Bivariate choropleth (two columns × palette grid). | `column_x`, `column_y`, `n`, `palette` |

```json
{
  "capability": "choropleth",
  "config": {
    "column": "pop_density",
    "method": "jenks",
    "k": 5,
    "palette": "YlOrRd"
  }
}
```

---

## Spatial statistics (Community)

Cluster detection, autocorrelation, hot / cold spot analysis.

| Capability | Description | Key parameters |
|-----------|-------------|----------------|
| `spatial_weights` | Builds spatial weights (queen / rook / k-NN / distance band) and adds `n_neighbours`. | `method`, `k`, `threshold` |
| `morans_i` | Global Moran's I with permutation-based pseudo p-value. Returns a summary row. | `column`, `weights`, `n_permutations` |
| `getis_ord_g` | Getis-Ord Gi* z-score per feature (hot / cold spots). | `column`, `weights` |

```json
{
  "capability": "morans_i",
  "config": { "column": "price_m2", "weights": { "method": "queen" }, "n_permutations": 999 }
}
```

---

## Density & tessellation (Community)

Grid builders and density surfaces.

| Capability | Description | Key parameters |
|-----------|-------------|----------------|
| `grid_create` | Regular square fishnet over a ref_layer or explicit bounds. | `cell_size`, `bounds`, `ref_layer` |
| `hexgrid_create` | Flat-top hexagonal grid. | `cell_size`, `bounds`, `ref_layer` |
| `kde_heatmap` | KDE sampled on a regular grid → points with `density`. | `bandwidth`, `cell_size`, `bounds` |

```json
{
  "capability": "hexgrid_create",
  "config": { "cell_size": 500, "ref_layer": "study_area" }
}
```

---

## Clustering (Community — optional scikit-learn)

Install via `pip install "gispulse[cluster]"`. Each capability adds a `cluster` column (-1 = noise for DBSCAN / HDBSCAN).

| Capability | Description | Key parameters |
|-----------|-------------|----------------|
| `cluster_kmeans` | K-Means on geometry centroids. | `k`, `random_state` |
| `cluster_dbscan` | Density-based DBSCAN. | `eps`, `min_samples` |
| `cluster_hdbscan` | Hierarchical HDBSCAN, varying densities. | `min_cluster_size`, `min_samples` |

```json
{ "capability": "cluster_dbscan", "config": { "eps": 200, "min_samples": 5 } }
```

---

## Line-network topology (Community)

Cleans a line network before running network analysis.

| Capability | Description | Key parameters |
|-----------|-------------|----------------|
| `network_snap_endpoints` | Snaps near-coincident endpoints together. | `tolerance` |
| `network_extend_dangles` | Extends dangling endpoints to the closest line. | `tolerance` |
| `network_node_lines` | Splits each line at every intersection → planar graph. | — |
| `network_remove_duplicates` | Drops geometric duplicates (direction-agnostic). | `tolerance` |
| `network_remove_pseudo_nodes` | Merges segments meeting at degree-2 nodes. | — |

```json
{ "capability": "network_snap_endpoints", "config": { "tolerance": 0.5 } }
```

---

## Polygon topology (Community)

Repairs a polygon coverage (gaps, overlaps, slivers).

| Capability | Description | Key parameters |
|-----------|-------------|----------------|
| `polygon_fix_gaps` | Detects gaps below `max_area` and merges them into the neighbour with the longest shared border. | `max_area` |
| `polygon_fix_overlaps` | Removes overlaps following a rule (`smallest`, `largest`, `first`). | `rule` |
| `polygon_remove_slivers` | Removes long / thin polygons (`min_area` or `max_shape_index`). | `min_area`, `max_shape_index` |
| `polygon_snap_borders` | Snaps vertices onto a grid → adjacent borders land on the same coordinates. | `grid_size` |

```json
{ "capability": "polygon_fix_gaps", "config": { "max_area": 10.0 } }
```

---

## 3D Pointcloud (Community — `pip install "gispulse[pointcloud]"`)

Loading and processing of LAS / LAZ pointclouds (ASPRS). Requires `laspy` (and `lazrs` for LAZ).

| Capability | Description | Key parameters |
|-----------|-------------|----------------|
| `pointcloud_load_las` | Loads a `.las` / `.laz` file into a `GeoDataFrame` of `Point Z`. | `path`, `crs`, `max_points`, `classifications` |
| `pointcloud_filter_classification` | Filters points by ASPRS classification codes (keep / drop). | `keep`, `drop`, `col` |
| `pointcloud_zonal_height` | Per-polygon Z statistics (building heights, canopy). | `ref_layer`, `stats`, `prefix`, `ground_col` |
| `pointcloud_grid_summary` | Bins Z values into a regular grid → polygons with per-cell stats. | `cell_size`, `stats`, `drop_empty` |

```json
{
  "capability": "pointcloud_load_las",
  "config": {
    "path": "data/lidar/tile_1234.laz",
    "crs": "EPSG:2154",
    "classifications": [2, 6]
  }
}
```

```json
{
  "capability": "pointcloud_zonal_height",
  "config": {
    "ref_layer": "lidar_points",
    "stats": ["max", "mean", "p95"],
    "ground_col": "ground_elevation",
    "prefix": "h_"
  }
}
```

::: tip Common ASPRS class codes
`2` = ground, `5` = high vegetation, `6` = building, `9` = water. See the [LAS 1.4 spec](https://www.asprs.org/wp-content/uploads/2010/12/LAS_1_4_r13.pdf).
:::

---

## Raster (Pro — `pip install "gispulse[raster]"`)

Requires `rasterio` + `rasterstats` and a Pro license (`GISPULSE_TIER=pro`).

::: info Pro tier
Raster capabilities call `check_tier("pro")` at runtime.
:::

| Capability | Description | Key parameters |
|-----------|-------------|----------------|
| `zonal_stats` | Raster statistics (min/max/mean/std/sum/count) per polygon. | `raster_path`, `stats` |
| `raster_clip` | Clips a raster to a vector extent. | `raster_path`, `output_path` |
| `ndvi` | NDVI = (NIR − RED) / (NIR + RED) from a multi-band raster. | `raster_path`, `nir_band`, `red_band` |
| `raster_reproject` | Raster reprojection. | `raster_path`, `target_crs`, `output_path` |
| `raster_merge` | Merges multiple rasters into one. | `raster_paths`, `output_path` |
| `change_detection` | Polygonises changed areas between two rasters. | `raster_before`, `raster_after`, `threshold` |

```json
{
  "capability": "zonal_stats",
  "config": {
    "raster_path": "data/ndvi.tif",
    "stats": ["min", "max", "mean", "std", "sum", "count"]
  }
}
```

---

## Network analysis (Pro — `pip install "gispulse[network]"`)

Requires `networkx` and a Pro license.

| Capability | Description | Key parameters |
|-----------|-------------|----------------|
| `shortest_path` | Dijkstra shortest path between two points. | `origin`, `destination`, `weight_col` |
| `isochrone` | Reachable area within a budget (distance / time). | `origin`, `max_distance`, `weight_col` |
| `od_matrix` | Origin-destination matrix in long format. | `origins_layer`, `destinations_layer`, `weight_col` |
| `mst` | Minimum spanning tree of a line network. | `weight_col` |
| `network_allocation` | Allocates demand points to the nearest supply node on the network. | `supply_layer`, `weight_col`, `max_cost` |
| `connectivity_check` | Ensures the network forms a connected graph and returns connected components. | — |

```json
{
  "capability": "isochrone",
  "config": { "origin": [2.3522, 48.8566], "max_distance": 1000, "weight_col": "length_m" }
}
```

---

## PostGIS SQL (Pro)

### `postgis_sql`

Runs a parameterised SQL query directly on PostGIS for advanced operations not covered by the other capabilities.

```json
{
  "capability": "postgis_sql",
  "config": {
    "sql": "SELECT *, ST_Area(geom::geography) AS area_geo FROM {input_table} WHERE ST_IsValid(geom)",
    "geom_col": "geom",
    "params": {}
  }
}
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `sql` | string | Query using `{input_table}` as placeholder |
| `params` | object | Named parameters (`$name`) for the query |
| `geom_col` | string | Geometry column (default: `geom`) |
| `input_table` | string | Input table (auto-created if missing) |

::: warning Security
The DSN is never read from `config` — it is resolved from `GISPULSE_POSTGIS_DSN`. SQL identifiers are validated (`operation_executor.py`). Do not expose this capability to untrusted users.
:::

---

## Tier summary

| Category | Caps | Tier | Optional extra |
|----------|------|------|----------------|
| Vector — selection & join | 8 | Community | — |
| Vector — overlay & combine | 5 | Community | — |
| Vector — derived geometry | 10 | Community | — |
| Vector — editing & metrology | 17 | Community | — |
| Vector — attribute manipulation | 9 | Community | — |
| Vector — pivot / unpivot | 2 | Community | — |
| Vector — ordered selection & sampling | 4 | Community | — |
| Vector — multipart & Z/M dimensions | 6 | Community | — |
| Vector — geometric transforms | 3 | Community | — |
| Vector — boundary & projection | 4 | Community | — |
| Temporal | 2 | Community | — |
| Validation | 4 | Community | — |
| Classification & styling | 8 | Community | `mapclassify` (for jenks) |
| Spatial statistics | 3 | Community | — |
| Density & tessellation | 3 | Community | — |
| Clustering | 3 | Community | `gispulse[cluster]` (`scikit-learn`, `hdbscan`) |
| Line-network topology | 5 | Community | — |
| Polygon topology | 4 | Community | — |
| 3D Pointcloud | 4 | Community | `gispulse[pointcloud]` (`laspy`, `lazrs`) |
| Raster | 6 | **Pro** | `gispulse[raster]` (`rasterio`, `rasterstats`) |
| Network analysis | 6 | **Pro** | `gispulse[network]` (`networkx`) |
| PostGIS SQL | 1 | **Pro** | `gispulse[postgis]` + DSN |
| **Total** | **117** | | |

---

## Build your own capability

See [Developing a plugin](/plugins/developing) for the entry-point publication flow.

```python
from capabilities.base import Capability
from capabilities.registry import register

@register
class MyCapability(Capability):
    name = "my_cap"
    description = "Short description of my capability."

    def execute(self, gdf, my_parameter=1.0, **_):
        # business logic here
        return gdf

    def get_schema(self):
        return {
            "type": "object",
            "properties": {
                "my_parameter": {"type": "number", "default": 1.0}
            },
            "required": ["my_parameter"],
        }
```

A distributed capability can self-register via the `gispulse.capabilities` entry-point group (see the `pyproject.toml` in [sdk/](https://github.com/sducournau/gispulse/tree/main/sdk)).

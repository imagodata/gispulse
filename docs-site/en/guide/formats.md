---
title: Supported I/O Formats
description: Complete list of spatial file formats supported for reading and writing by GISPulse.
---

# Supported I/O Formats

GISPulse uses [PyOGRIO](https://pyogrio.readthedocs.io/) for reading and writing. Format detection is automatic based on the file extension.

```bash
# List available formats on your installation
gispulse formats
```

## Vector formats

### Recommended formats

| Extension | Format | Read | Write | Notes |
|-----------|--------|:----:|:-----:|-------|
| `.gpkg` | GeoPackage | yes | yes | Recommended — multi-layer, styles, performant |
| `.fgb` | FlatGeobuf | yes | yes | Ultra-fast for large volumes |
| `.parquet` | GeoParquet | yes | yes | Optimal for wide tabular data |
| `.geojson` | GeoJSON | yes | yes | Web standard, interoperable |
| `.geojsonl` | GeoJSON Lines | yes | yes | Streaming, large volumes |

### Common formats

| Extension | Format | Read | Write | Notes |
|-----------|--------|:----:|:-----:|-------|
| `.shp` | ESRI Shapefile | yes | yes | Legacy — prefer GPKG |
| `.csv` | CSV (with lat/lon columns) | yes | yes | No native geometry |
| `.dxf` | AutoCAD DXF | yes | yes | CAD |
| `.kml` | KML / KMZ | yes | no | Google Earth |
| `.gml` | GML | yes | yes | OGC standard |
| `.gpx` | GPX | yes | no | GPS tracks |

### Database formats

| Format | Read | Write | Notes |
|--------|:----:|:-----:|-------|
| PostGIS | yes | yes | Via `GISPULSE_DSN` (Pro) |
| SpatiaLite | yes | yes | Portable mode |
| ESRI GeoDatabase (.gdb) | yes | no | Read-only |
| OGC WFS | yes | no | Via WFS URL |

### Raster formats (with `gispulse[raster]`)

| Extension | Format | Read | Write |
|-----------|--------|:----:|:-----:|
| `.tif`, `.tiff` | GeoTIFF | yes | yes |
| `.vrt` | GDAL VRT | yes | no |
| `.img` | ERDAS Imagine | yes | no |
| `.nc` | NetCDF | yes | no |

## Automatic detection

GISPulse detects the format from the extension:

```bash
# Format detected automatically
gispulse run input.fgb --rules rules.json -o output.gpkg
gispulse run input.geojson --rules rules.json -o output.fgb
gispulse run input.shp --rules rules.json -o output.parquet
```

If the file has no recognized extension, force it with `--layer` and `--crs`.

## Format recommendations

### For large volumes (> 100,000 features)

1. **FlatGeobuf** (`.fgb`) — fastest read/write, spatially indexed
2. **GeoParquet** (`.parquet`) — excellent when you have dozens of attribute columns
3. **GPKG** — versatile, supports styles

### For GIS desktop interoperability

- **GeoPackage** (`.gpkg`) — supports QGIS styles (QML) and SLD, multi-layer
- GISPulse automatically copies styles during an `--all-layers` pipeline

### For web / API

- **GeoJSON** — universal standard, human-readable
- **FlatGeobuf** — performant streaming for large client-side datasets

### For "modern" spatial data

- **GeoParquet** — compatible with DuckDB, Pandas, Arrow, cloud-native

## Multi-layer (GPKG)

GeoPackage supports multiple layers in a single file:

```bash
# Process a specific layer
gispulse run project.gpkg --rules rules.json -o output.gpkg --layer buildings

# Process all layers (styles copied)
gispulse run project.gpkg --rules rules.json -o output.gpkg --all-layers
```

```bash
# Inspect layers in a GPKG
gispulse layers project.gpkg

3 layer(s):
  - parcels
  - buildings
  - roads
```

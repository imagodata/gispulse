---
title: ArcGIS Pro Add-in
description: Install and use the GISPulse ArcGIS Pro add-in — datasets, jobs, OGC layers.
---

# ArcGIS Pro Add-in

The GISPulse ArcGIS Pro add-in connects ArcGIS Pro to a GISPulse backend to browse datasets, execute jobs, and load OGC layers.

**Compatibility:** ArcGIS Pro 3.0+

## Installation

### From the .esriaddin file

1. Download `gispulse_arcgis.esriaddin` from the [GitHub releases](https://github.com/gispulse/gispulse/releases)
2. Double-click the `.esriaddin` file
3. ArcGIS Pro prompts for installation confirmation
4. Restart ArcGIS Pro

### Manual installation (development)

```bash
cd clients/arcgis
python makeaddin.py     # generates gispulse_arcgis.esriaddin
```

## Configuration

1. In ArcGIS Pro: **GISPulse** tab in the ribbon
2. Click **Connection Settings**
3. Enter the server URL and API key
4. **Test** then **OK**

## Available Panels

### Datasets Dockpane

Browse and upload datasets. Load as OGC FeatureLayer or PostGIS layer.

### Jobs Dockpane

Create jobs, monitor execution, download results.

### Sessions Dockpane (Pro)

Manage active PostGIS sessions.

## Geoprocessing Tools

Three tools are available in the **GISPulse Toolbox**:

<!-- TODO: document gp_tools -->

| Tool | Description |
|------|-------------|
| `Upload Dataset` | Upload the selected layer to GISPulse |
| `Run Rules` | Execute rules on a GISPulse dataset |
| `Load OGC Layer` | Load a GISPulse dataset as an OGC FeatureLayer |

## Loading Layers

### Via OGC API Features

The plugin loads GISPulse datasets as FeatureLayers via the OGC endpoint:

```
http://localhost:8001/ogc/collections/{dataset_id}/items
```

### Via PostGIS (Pro)

Direct connection to the underlying PostGIS table for persistent datasets.

## Source Code

`clients/arcgis/gispulse_arcgis/`

<!-- TODO: document advanced options and the config.daml manifest -->

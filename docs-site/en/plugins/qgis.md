---
title: QGIS Plugin
description: Install and use the GISPulse QGIS plugin — backend connection, dataset browser, jobs, OGC, MVT.
---

# QGIS Plugin

The GISPulse QGIS plugin connects QGIS to a GISPulse backend (local or remote) to browse datasets, execute rules, and load layers via OGC API Features, PostGIS, and MVT.

**Compatibility:** QGIS 3.28 -- 4.x

## Installation

### From the QGIS Plugin Manager

1. In QGIS: **Plugins -> Manage and Install Plugins**
2. Search for **GISPulse**
3. Click **Install**

### Manual installation (development)

```bash
# Clone the repository
git clone https://github.com/gispulse/gispulse
cd gispulse/clients/qgis

# Build the plugin (creates gispulse_qgis.zip)
python build_plugin.py

# Install in QGIS
cp -r gispulse_qgis ~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/
```

Enable the plugin in **Plugins -> Manage and Install Plugins -> Installed**.

## Configuration

On first launch, the connection dialog opens:

1. **Server URL**: URL of your GISPulse backend (e.g., `http://localhost:8001` or `https://gispulse.example.com`)
2. **API Key**: API key if your server is secured (leave empty for a local server without auth)
3. Click **Test Connection** to verify
4. **OK** to confirm

Settings are saved in QGIS settings (encrypted if a key is defined).

## Available Panels

The plugin adds four panels accessible via **View -> Panels**:

### Datasets Panel

- Lists datasets registered on the GISPulse server
- Upload a spatial file directly from QGIS
- Load a layer with a single click:
  - **OGC Features** -- vector layer via the OGC API (automatic pagination)
  - **MVT** -- vector tiles for large volumes
  - **PostGIS** -- direct connection if a PostGIS session is active (Pro)
- Metadata preview (CRS, feature count, format)

### Jobs Panel

- Create and execute jobs on a selected dataset
- Real-time execution monitoring (progress bar)
- Download the result as a new QGIS layer
- Recent job history

### Triggers Panel (Pro)

- View and manage active triggers on the server
- Enable/disable a trigger
- View recent events

### Scenarios Panel (Pro)

- Browse and execute defined scenarios
- Monitor DAG execution

## Loading Data from GISPulse

### Via OGC API Features

```
Datasets Panel -> select a dataset -> Load (OGC)
```

The layer is loaded in QGIS as a standard WFS vector layer. Supports bbox and attribute filters.

### Via MVT (vector tiles)

```
Datasets Panel -> select a dataset -> Load (MVT)
```

Ideal for datasets with > 100,000 features. The layer is loaded as a MapLibre vector tile layer.

MVT endpoint URL:
```
http://localhost:8001/ogc/collections/{dataset_id}/tiles/{z}/{x}/{y}.mvt
```

### Via direct PostGIS (Pro)

When a PostGIS session is active, the plugin can load PostGIS tables directly via QGIS's PostgreSQL connection. Layers stay synchronized with the backend.

## Running a Job from QGIS

1. Select a dataset in the Datasets panel
2. Go to the Jobs panel
3. Choose the rules to apply (or a scenario)
4. Click **Run**
5. Monitor the progress
6. Once completed: **Load Result** to add the layer to QGIS

## Geoprocessing Tools

The plugin exposes 3 tools in the **QGIS Processing Toolbox**:

<!-- TODO: document the exposed geoprocessing tools -->

| Tool | Description |
|------|-------------|
| `GISPulse — Run Rules` | Execute rules on the active layer |
| `GISPulse — Upload Dataset` | Upload the active layer to GISPulse |
| `GISPulse — Export Result` | Download the result of a job |

## Troubleshooting

### "Connection refused"
Make sure `gispulse portal` is running on the specified port.

### "Unauthorized"
Check the API key in the plugin settings (**Plugins -> GISPulse -> Settings**).

### Empty MVT layers
The MVT endpoint requires the dataset to be indexed on the server side. Try loading via OGC first.

## Plugin Development

See [Developing a Plugin](/plugins/developing) to contribute or create your own GISPulse plugin.

Source code: `clients/qgis/gispulse_qgis/`

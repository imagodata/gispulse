---
title: Quickstart — First triggers in 5 minutes
description: Install GISPulse, attach DML triggers to a GeoPackage, and run the headless runtime.
---

# Quickstart

You will have a GeoPackage that reacts to every INSERT/UPDATE/DELETE in under 5 minutes — no server, no QGIS plugin, just the CLI.

## Prerequisites

- Python 3.10+
- `pipx` ([install guide](https://pipx.pypa.io/stable/installation/)) — recommended to isolate the CLI from the system Python

## Step 1 — Install the CLI

```bash
pipx install gispulse
```

::: tip Why `pipx`?
`pipx` installs GISPulse in an isolated environment: no system-Python pollution, no clash with `geopandas` / `pyproj` from other projects. On macOS Sonoma+ or recent Debian, a global `pip install gispulse` fails with `error: externally-managed-environment` (PEP 668).

If you must use `pip`: `pip install --user gispulse`, or activate a virtualenv first.
:::

Verify:

```bash
gispulse --help
```

## Step 2 — Grab a sample GeoPackage

For this demo, download a cadastral parcels sample (~7 MB):

```bash
mkdir -p demo && cd demo
curl -L "https://raw.githubusercontent.com/imagodata/gispulse/main/examples/datasets/muret_parcels.gpkg" \
  -o parcels.gpkg

gispulse info parcels.gpkg
```

```
File:     parcels.gpkg
Format:   GPKG
Size:     6.91 MB
CRS:      EPSG:4326
Category: vector

1 layer(s):
  - parcels: 17212 features, Polygon, EPSG:4326
```

## Step 3 — Install change-tracking

GISPulse installs SQLite `AFTER INSERT/UPDATE/DELETE` triggers on the target layer. Any modification — by QGIS, `ogr2ogr`, FME, or any SQL client — gets captured into an internal `_gispulse_change_log` table.

```bash
gispulse track install parcels.gpkg --layer parcels
```

```
gpkg_project_bootstrapped: 11 internal tables (schema v2)
change_tracking_installed: parcels (pk=fid, cols=9, geom=geom)
✓ Installed change tracking on 1 layer(s): parcels
```

Verify the install:

```bash
gispulse track list parcels.gpkg
```

```
             Change tracking — parcels.gpkg
┏━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━┓
┃ Layer   ┃ Tracked ┃ Ops                  ┃ Pending ┃
┡━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━┩
│ parcels │    ✓    │ delete,insert,update │       0 │
└─────────┴─────────┴──────────────────────┴─────────┘
```

## Step 4 — Write a YAML trigger

Create `triggers.yaml` next to the GPKG:

```yaml
version: 1
gpkg: ./parcels.gpkg

triggers:
  - name: tag_high_value_parcels
    table: parcels
    pk_col: fid
    when: [INSERT, UPDATE]
    predicate: "surface_cadastrale > 10000"
    actions:
      - type: set_field
        field: owner
        value: AUDIT_REQUIRED

runtime:
  poll_interval_ms: 1000
  max_batch: 200
```

This trigger tags every parcel larger than 1 hectare with `owner = "AUDIT_REQUIRED"` whenever it is created or updated. The predicate DSL supports `==` `!=` `>` `<` `AND` `OR` `IN` — no `eval`, no third-party dependency (see [`docs/TRIGGERS_GUIDE.md`](https://github.com/imagodata/gispulse/blob/main/docs/TRIGGERS_GUIDE.md)).

Validate the config (syntax + schema + layer references):

```bash
gispulse triggers validate --config triggers.yaml
```

```
OK 1 trigger(s) valid against parcels.gpkg.
```

## Step 5 — Edit the GPKG from your usual client

Change-tracking captures changes made by any client. Open `parcels.gpkg` in **QGIS** (right-click → Toggle Editing), edit a few attributes, save. Or via CLI with `ogr2ogr`:

```bash
ogr2ogr -f GPKG parcels.gpkg parcels.gpkg parcels \
  -dialect SQLite -sql "UPDATE parcels SET owner='test' WHERE fid IN (1, 2, 3)" \
  -update
```

Check the pending changes:

```bash
gispulse track list parcels.gpkg
```

```
             Change tracking — parcels.gpkg
┏━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━┓
┃ Layer   ┃ Tracked ┃ Ops                  ┃ Pending ┃
┡━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━┩
│ parcels │    ✓    │ delete,insert,update │       3 │
└─────────┴─────────┴──────────────────────┴─────────┘
```

## Step 6 — Run the runtime

**`--once`** mode: drain the change-log once, run the actions, exit. Ideal for cron, AWS Lambda, CI hooks:

```bash
gispulse triggers run --config triggers.yaml --once
```

```
{"event":"runtime_starting","gpkg":"./parcels.gpkg","triggers":1,"mode":"once"}
{"event":"tick_done","processed":3}
OK one tick processed 3 change-log row(s) on parcels.gpkg.
```

**`--watch`** mode: a daemon that polls continuously, hot-reloads the YAML on mtime change, shuts down cleanly on SIGINT/SIGTERM (2 s drain):

```bash
gispulse watch parcels.gpkg --rules triggers.yaml
```

::: tip In production
For long-running deployments, use `packaging/systemd/gispulse-watch@.service` or `packaging/docker/Dockerfile.watch`. See [`docs/INTEGRATION_MATRIX.md`](https://github.com/imagodata/gispulse/blob/main/docs/INTEGRATION_MATRIX.md).
:::

## Going further

| Goal | Command |
|------|---------|
| Diagnose trigger drift | `gispulse track doctor parcels.gpkg --auto-fix` |
| List installed actions | `gispulse triggers list --gpkg parcels.gpkg` |
| View latest change-log rows | `gispulse track tail parcels.gpkg` |
| Uninstall change-tracking | `gispulse track uninstall parcels.gpkg --layer parcels` |
| Launch the visual portal | `gispulse portal` |
| List capabilities | `gispulse capabilities` |

- [Full triggers guide](https://github.com/imagodata/gispulse/blob/main/docs/TRIGGERS_GUIDE.md) — predicate DSL, action types, SQL guardrails, payload v2
- [CLI reference](/en/guide/cli)
- [All capabilities](/en/guide/capabilities)
- Integrations: [QGIS](/integrations/qgis) · [ArcGIS](/integrations/arcgis) · [MapLibre](/integrations/maplibre)

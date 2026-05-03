---
title: Walkthrough — Parcels
description: Classify the buildings of a parcel by isochrone ring just by saving the GeoPackage. No QGIS plugin required.
---

# Walkthrough — Parcels

> **Promise**: edit an attribute in QGIS → `Ctrl+S` → the GISPulse rule
> fires and reclassifies the parcel's buildings. No GIS-client plugin
> needed.

## What you'll see

A **cadastral parcels** layer plus a **walking isochrone** layer
(500/750/1000 m). On every change to a parcel, GISPulse re-evaluates
which buildings fall in which accessibility ring and writes the result
into the `accessibility_tier` attribute.

| Before | After save |
|---|---|
| `accessibility_tier` empty or stale on the buildings of the parcel you just edited | All buildings up to date: `tier_500m`, `tier_750m`, `tier_1000m`, or `out_of_range` |

## Prerequisites

- QGIS ≥ 3.28
- `gispulse` ≥ 1.5.1 (`pipx install gispulse`)
- The demo pack: `gispulse examples fetch parcels`

## Setup (~1 min)

```bash
# 1. Install the change-log + SQLite triggers on the demo GPKG
gispulse track install ~/.gispulse/examples/parcels/parcels.gpkg

# 2. Start the watch loop (leave it running in a terminal)
gispulse triggers watch \
  --rules ~/.gispulse/examples/parcels/triggers.yaml \
  --dataset ~/.gispulse/examples/parcels/parcels.gpkg
```

The terminal continuously prints:

```text
[info] watching parcels.gpkg for change-log entries
[info] 0 pending events
```

## The scenario in 3 steps

### 1. Open the layer in QGIS

```text
Layer → Add Layer → Vector → ~/.gispulse/examples/parcels/parcels.gpkg
```

Pick **`parcels`**, then also load **`buildings`** and **`isochrones`**
from the same GeoPackage.

### 2. Edit a parcel

Toggle edit mode (`Ctrl+E`), redraw the boundary of a parcel sitting
near an isochrone ring, then **save** (`Ctrl+S`). That's it.

### 3. The trigger fires

The terminal immediately shows:

```text
[info] dml.changed parcels fid=42
[info] rule:classify_buildings_in_isochrones triggered
[info]   → 6 buildings reclassified
[info]   → 2 moved tier_750m → tier_500m
[info]   → 4 unchanged
[info] commit ok in 87 ms
```

Re-open the buildings attribute table in QGIS (`F6`) — the
`accessibility_tier` column is up to date.

## See the same scenario online

The demo portal runs the very same rule on the same dataset, with
nothing to install:

> 🔗 [Try it on `try.gispulse.dev/parcels`](https://try.gispulse.dev/parcels)

Pick the isochrone radius (500/750/1000 m), click **Run trigger**, and
the portal shows the reclassified buildings on the map. Same rule, same
dataset, same engine.

## Expected portal output

**Events** panel (`/explorer`):

```text
2026-05-02T14:32:11Z  classify_buildings_in_isochrones  parcels#42  ok 87ms
2026-05-02T14:32:11Z  dml.changed                       parcels    fid=42
```

## What's next?

- The sister walkthrough [Isochrone](/en/guide/walkthroughs/isochrone)
  shows how to recompute **the rings** when the parcel itself changes
  shape.
- [Audit](/en/guide/walkthroughs/audit) traces **every** modification
  and exports a CSV for compliance.
- The [CLI ↔ Portal matrix](/en/guide/symmetry) lists every public
  capability exposed on both sides of the same source of truth.

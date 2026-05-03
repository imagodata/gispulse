---
title: Walkthrough — Isochrone
description: When a parcel's geometry changes, recompute its 500/750/1000 m walking isochrones via the road network — no QGIS plugin required.
---

# Walkthrough — Isochrone

> **Promise**: redraw a parcel's boundary in QGIS → `Ctrl+S` → its
> **accessibility rings** are recomputed from the new centroid. No
> GIS-client plugin needed.

## What you'll see

When a parcel's geometry moves (merge, split, cadastral correction), its
**walking isochrones** must follow. This rule recomputes the 3
concentric rings via the OSM road network on every parcel save.

| Before | After save |
|---|---|
| Rings frozen on the old parcel centroid | 3 isochrone polygons recomputed from the new centroid |

## Prerequisites

- QGIS ≥ 3.28
- `gispulse` ≥ 1.5.1 (`pipx install gispulse`)
- The demo pack: `gispulse examples fetch isochrone`

## Setup (~1 min)

```bash
gispulse track install ~/.gispulse/examples/isochrone/parcels.gpkg

gispulse triggers watch \
  --rules ~/.gispulse/examples/isochrone/triggers.yaml \
  --dataset ~/.gispulse/examples/isochrone/parcels.gpkg
```

The rule uses the road network bundled with the demo pack (`network.gpkg`
under `~/.gispulse/examples/isochrone/`) — no OSRM or Valhalla calls,
everything stays local.

## The scenario in 3 steps

### 1. Pick a parcel to edit

Open `parcels` and `isochrones` side by side in QGIS. Pick a parcel
near a ring boundary — the visual effect will be more striking.

### 2. Redraw its boundary

Toggle edit mode (`Ctrl+E`), nudge a few vertices to shift the centroid
by tens of meters, then **save** (`Ctrl+S`).

### 3. The trigger re-isochrones

The terminal shows:

```text
[info] dml.changed parcels fid=87
[info] rule:recompute_isochrones triggered
[info]   → 3 rings recomputed (500m, 750m, 1000m)
[info]   → routing graph cache hit
[info] commit ok in 312 ms
```

Refresh the `isochrones` layer in QGIS (`F5`) — the 3 polygons follow
the new parcel centroid.

## See the same scenario online

> 🔗 [Try it on `try.gispulse.dev/isochrone`](https://try.gispulse.dev/isochrone)

In the portal you can **drag-drop** the parcel boundary directly on the
map. The rule runs in `dryrun` mode (actions are captured but not
committed) so you see the result without touching the demo dataset.

## Expected portal output

**Events** panel (`/explorer`):

```text
2026-05-02T14:32:11Z  recompute_isochrones  parcels#87  ok 312ms
2026-05-02T14:32:11Z  dml.changed           parcels    fid=87
```

**Map** panel: the 3 rings reshape live as you drag the parcel.

## Cost and limits

- In-memory routing graph cache: the first recompute after startup
  takes ~800 ms; subsequent ones ~300 ms.
- `gispulse triggers watch` ceiling: 50 triggers per second — plenty
  for the demo (manual edits).
- For batches >1k modified parcels, prefer `gispulse triggers run
  --once --bulk-threshold 100`, which disables watch and runs
  vectorised in one pass.

## What's next?

- [Parcels](/en/guide/walkthroughs/parcels) shows the inverse effect:
  reclassifying a parcel's **buildings** when the parcel itself changes.
- [Audit](/en/guide/walkthroughs/audit) traces **every** recompute for
  later review.
- The [CLI ↔ Portal matrix](/en/guide/symmetry) lists every entry point
  available on both sides.

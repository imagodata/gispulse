# S5: Park Accessibility per Building — Versailles

<span class="gp-difficulty-badge" style="background: var(--gp-orange)">Intermediate</span> <span class="gp-mode-badge">CLI + Map</span>

**Capabilities**: `area_length` `filter` `nearest_neighbor` `classify`

## Use case

An urban-planning team wants an operational answer: **which homes lack access to a nearby park?** Not "what vegetation types exist" — BD TOPO already answers that — but: **how many residents walk more than 300 m to reach a park ≥ 1 ha?**

The pipeline computes, for every residential building, the distance to the nearest ≥ 1 ha park, then classifies the result against three institutional thresholds (WHO 300 m, SCoT IdF 600 m, ADEME 1000 m). Output: **a building-level choropleth** that reads directly — green = well served, red = underserved.

::: info Key numbers (BD TOPO Versailles, full commune)
**509 vegetation zones** → **92 parks ≥ 1 ha** (including Forêt de Fausses-Reposes, 411 ha) → **7,709 residential buildings** scored → **59.8 % within 300 m** of a park, **0 % in deficit** (> 1000 m, max measured distance 768 m).

Versailles is structurally well served — the forests of Fausses-Reposes, Versailles and Marly saturate the periphery. Running the same pipeline on a deficit-prone city (Pantin, Aubervilliers…) would expose a visible *Deficit* class.
:::

## IGN BD TOPO V3 data

| Layer | Content | Features (commune) | Fields used | Source |
|-------|---------|--------------------|-------------|--------|
| `vegetation` | Vegetation zones | 509 | `nature`, `cleabs` | [data.geopf.fr](https://data.geopf.fr) — BDTOPO_V3:zone_de_vegetation |
| `batiments` | Building footprints | 9,741 (7,709 residential) | `usage_1`, `hauteur` | [data.geopf.fr](https://data.geopf.fr) — BDTOPO_V3:batiment |

```bash
python examples/prepare_playground_data.py --city versailles
gispulse info examples/datasets/versailles_bdtopo.gpkg --layer batiments
```

## Pipeline (5 steps, 2 branches)

```
vegetation ──► area_length → area_m2                (crs_meters = EPSG:2154)
                  │
                  ▼
              filter (area_m2 >= 10000)               # parks ≥ 1 ha (SCoT IdF)
                  │                                     → 92 parks
                  ▼
               parks_1ha  ───────────────────┐
                                             │  ref_layer
batiments  ──► filter (usage_1 == 'Résidentiel')
                  │                           │       → 7,709 residential
                  ▼                           │
              nearest_neighbor ◄──────────────┘
                  k=1
                  distance_col = park_distance_m
                  columns = [cleabs, nature, area_m2]
                  crs_meters = EPSG:2154
                  │
                  ▼
              classify (field: park_distance_m)
                  method = manual
                  breaks = [0, 300, 600, 1000, 99999]
                  palette = [#1a9850, #a6d96a, #fdae61, #d7191c]
                  → access_class, access_color
```

::: tip Why `nearest_neighbor` and not `spatial_join`?
`spatial_join` answers "does this building *intersect* a park" — too strict here, no building overlaps a park. `nearest_neighbor` answers "how far is the closest park" — exactly the accessibility question. It reprojects internally to `crs_meters` (Lambert93) so the distance is in physical meters, not degrees.
:::

::: details Why these thresholds (all urbanism-sourced)?
| Threshold | Source | Meaning |
|-----------|--------|---------|
| **1 ha** (vegetation filter) | SCoT Île-de-France, *large green spaces* | Below that it's a hedge or a thicket — no park role |
| **300 m** | WHO — *minimum walking distance to urban green space* | European public-health baseline (≈ 4 min walk) |
| **600 m** | SCoT IdF — *acceptable pedestrian reach* | ≈ 8 min walk, standard walkability threshold |
| **1000 m** | ADEME — *beyond, motorised mode required* | Defines the actual *deficit* in nearby green space |

Breaks are **manual** (not quantile, not Jenks) — otherwise thresholds would "drift" from one city to another and the result would lose its institutional meaning. The criterion must be absolute, not relative.
:::

## Rules

```json
{
  "version": 2,
  "name": "park_access_score",
  "ref_layers": { "vegetation": "vegetation", "batiments": "batiments" },
  "steps": [
    {
      "id": "compute_veg_area",
      "capability": "area_length",
      "params": { "area_col": "area_m2", "crs_meters": "EPSG:2154", "compute_length": false }
    },
    {
      "id": "filter_parks_1ha",
      "capability": "filter",
      "params": { "expression": "area_m2 >= 10000" },
      "input": "compute_veg_area"
    },
    {
      "id": "filter_residential",
      "capability": "filter",
      "params": { "expression": "usage_1 == 'Résidentiel'" },
      "input": "batiments"
    },
    {
      "id": "nearest_park",
      "capability": "nearest_neighbor",
      "params": {
        "ref_layer": "filter_parks_1ha",
        "k": 1,
        "distance_col": "park_distance_m",
        "columns": ["cleabs", "nature", "area_m2"],
        "crs_meters": "EPSG:2154"
      },
      "input": "filter_residential"
    },
    {
      "id": "classify_access",
      "capability": "classify",
      "params": {
        "field": "park_distance_m",
        "method": "manual",
        "bins": 4,
        "breaks": [0, 300, 600, 1000, 99999],
        "class_col": "access_class",
        "color_col": "access_color",
        "palette": ["#1a9850", "#a6d96a", "#fdae61", "#d7191c"]
      },
      "input": "nearest_park"
    }
  ],
  "triggers": [
    { "on": "schedule:0 6 * * 1", "then": "run_pipeline" }
  ]
}
```

::: tip Download
[scenario-5-rules.json](./scenario-5-rules.json) — cron embedded in `rules.triggers`, no separate trigger file.
:::

## Weekly trigger (embedded in the pipeline)

The `triggers` section carries `0 6 * * 1` (every Monday 06:00, Europe/Paris) with action `run_pipeline`. The GISPulse scheduler replays the full sequence on each BD TOPO refresh — distances are recomputed for every building, new constructions land in the right class without manual intervention.

## Run

```bash
gispulse run examples/datasets/versailles_bdtopo.gpkg \
  --rules playground/scenario-5-rules.json \
  -o output/park_access.gpkg

gispulse serve output/park_access.gpkg
```

## Expected output

::: details Output schema (residential buildings)
| Column | Type | From | Description |
|--------|------|------|-------------|
| `geometry` | MultiPolygon | source | Building footprint (BD TOPO) |
| `usage_1` | string | source | Always `"Résidentiel"` (filtered in step 3) |
| `hauteur` | float | source | IGN height (m) |
| `cleabs` | string | step 4 (`nearest_neighbor`) | IGN id of the nearest park |
| `nature` | string | step 4 | BD TOPO type of the nearest park |
| `area_m2` | float | step 4 | Area of the nearest park (m²) |
| `park_distance_m` | float | step 4 | Distance to the nearest park (m, Lambert93) |
| `access_class` | int (1..4) | step 5 (`classify`) | 1=Excellent, 2=Correct, 3=Far, 4=Deficit |
| `access_color` | string | step 5 | Hex palette `#1a9850` → `#d7191c` |
:::

::: info Accessibility classes (Versailles, after filters)
| Class | Color | Interval | Share | Urbanism reading |
|-------|-------|----------|-------|------------------|
| **Excellent** | <span style="color:#1a9850">■</span> `#1a9850` | < 300 m | **59.8 %** | WHO baseline met |
| **Correct** | <span style="color:#a6d96a">■</span> `#a6d96a` | 300–600 m | **32.8 %** | Walkable per SCoT IdF |
| **Far** | <span style="color:#fdae61">■</span> `#fdae61` | 600–1000 m | **7.4 %** | ADEME pedestrian limit |
| **Deficit** | <span style="color:#d7191c">■</span> `#d7191c` | > 1000 m | **0 %** | Motorised mode required |

The *Deficit* class is empty in Versailles — that's information in itself. The same pipeline on a dense city without peripheral forests (Pantin, Aubervilliers, Bagnolet) would light up red zones.
:::

## Interactive playground

Live 5-step pipeline with two branches (vegetation + buildings), Lambert93 distance, manual choropleth (requires the demo backend).

<ClientOnly>
  <DualMapView scenario="green-spaces" :showPipeline="true" :showTriggers="true" />
</ClientOnly>

**Reference preparation**

1. **`compute_veg_area`** <span style="color: var(--gp-orange)">(orange)</span> — Lambert93 area (EPSG:2154) → `area_m2`.
2. **`filter_parks_1ha`** <span style="color: var(--gp-orange)">(orange)</span> — keep zones ≥ 10,000 m² → **92 parks** used as reference.
3. **`filter_residential`** <span style="color: var(--gp-orange)">(orange)</span> — filter `usage_1 == 'Résidentiel'` on buildings → **7,709 features**.

**Spatial join + classification**

4. **`nearest_park`** <span style="color: var(--gp-violet)">(violet)</span> — `nearest_neighbor` k=1: for every residential, distance to the nearest park ≥ 1 ha (meters) plus a join on `cleabs`, `nature`, `area_m2` of that park.
5. **`classify_access`** <span style="color: var(--gp-red)">(red)</span> — `classify` manual breaks [0, 300, 600, 1000, ∞] + reversed RdYlGn palette → `access_color` per building.

Residential popup: `hauteur`, `park_distance_m`, `access_class`, `nature` and `area_m2` of the nearest park.

## Try it live

<TryItLive endpoint="/capabilities" description="lists the demo backend capabilities (area_length, filter, nearest_neighbor, classify used by this pipeline)" />

<TryItLive endpoint="/datasets" description="lists demo datasets, including versailles_bdtopo with the vegetation and batiments layers wired for this scenario" />

<TryItLive endpoint="/health" description="GISPulse demo backend status." />

## Going further

- [S3: Health accessibility via isochrones](/en/playground/road-buffer-poi) — same amenity-access question, but via network isochrones (not Euclidean distance).
- [S6: Price per m² map (DVF)](/en/playground/real-estate) — another Versailles choropleth, on land transactions.
- [Vector capabilities](/en/guide/capabilities#vector) — `filter`, `area_length`, `nearest_neighbor`, `classify`.

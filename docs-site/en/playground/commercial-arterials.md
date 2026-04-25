# S2: Commercial Buildings along Arterial Roads — Toulouse

<span class="gp-difficulty-badge" style="background: var(--gp-green)">Beginner</span> <span class="gp-mode-badge">CLI + Map</span>

**Capabilities**: `filter`

## Use case

An urban planning team wants to **identify Toulouse commercial buildings within 50 m of an arterial road** (IGN importance 2, 3 or 4 — national, departmental and main urban arteries) to map the retail and service offer along high-traffic corridors.

The pipeline chains three steps (each one produces a layer that shows up on the map):
- **`filter_routes_arterials`** — keeps only road segments with `importance in ['2','3','4']`. Visible network layer.
- **`filter_near_arterials`** — on the `batiments` layer, spatial `intersects` against `filter_routes_arterials` with `buffer_distance: 50` (Lambert93): a true 50 m metric ring around the arterials, kept buildings intersect it.
- **`filter_commercial`** — attribute filter: `usage_1` **or** `usage_2` == `Commercial et services` (BD TOPO exposes two usage columns: a shop can be encoded as primary or secondary use).

## IGN BD TOPO V3 Data

| Layer | Content | Features | Source |
|-------|---------|----------|--------|
| `batiments` | IGN Toulouse buildings (usage, height, floors, dwellings) | ~31,000 | [data.geopf.fr](https://data.geopf.fr) — BDTOPO_V3:batiment |
| `routes` | IGN Toulouse road segments (importance 1-5, nature, nom_voie, width) | ~6,000 | [data.geopf.fr](https://data.geopf.fr) — BDTOPO_V3:troncon_de_route |

**Importance distribution** (Toulouse): 1 (motorways): ~70 · **2 (national): ~250** · **3 (departmental): 987** · **4 (main urban): 410** · 5 (local/communal streets): 3,497 · 6 (paths/accesses): 1,112.

```bash
python examples/prepare_playground_data.py --city toulouse
gispulse info examples/datasets/toulouse_bdtopo.gpkg
```

## Pipeline (3 steps)

```
routes  ──► filter_routes_arterials                 # step 1: arterial road network
              (importance in ['2','3','4'])         #          → ~1,600 segments (visible layer)
                │
                ▼ (used as ref_layer)
batiments ──► filter_near_arterials                 # step 2: buildings along the network
              (intersects ref_layer, buffer 50 m,   #          → spatial cohort (visible layer)
               Lambert93)
                │
                ▼
            filter_commercial                       # step 3: commercial
              (usage_1 == 'Commercial et services'  #          → final cohort (visible layer)
               or usage_2 == 'Commercial et services')
```

Each step exposes its result as a GeoJSON layer, so the interactive map
can visualize **both the filtered road network and the building cohorts**
at every stage.

::: tip Why a separate step for the roads?
With an inline `ref_filter` (previous version), the retained sub-network was never
materialized as a layer — you saw the building results but not WHICH segments drove
the calculation. The explicit `filter_routes_arterials` step produces the arterials
as an intermediate artifact, making the diagnostic auditable.
:::

::: warning Metric CRS
IGN data is stored in **EPSG:4326** (WGS84). The 50 m buffer is computed in
**EPSG:2154 (Lambert93)** to guarantee real-world 50 m on the ground. Without `crs_meters`,
the default `EPSG:3857` (Web Mercator) would give ~36 m at Toulouse's latitude.
:::

::: info Two usage columns
BD TOPO encodes a primary usage (`usage_1`) and a secondary usage (`usage_2`).
A mixed-use block (shops + housing) may be tagged `Résidentiel` / `Commercial et services`
or the reverse. The `or` pandas expression catches both cases.
:::

## Rules

```json
{
  "version": 2,
  "name": "toulouse_commercial_buildings_near_arterials",
  "ref_layers": {
    "routes": "routes",
    "batiments": "batiments"
  },
  "steps": [
    {
      "id": "filter_routes_arterials",
      "type": "capability",
      "capability": "filter",
      "params": {
        "expression": "importance in ['2', '3', '4']"
      },
      "input": "routes"
    },
    {
      "id": "filter_near_arterials",
      "type": "capability",
      "capability": "filter",
      "params": {
        "spatial_predicate": "intersects",
        "ref_layer": "filter_routes_arterials",
        "buffer_distance": 50,
        "crs_meters": "EPSG:2154"
      },
      "input": "batiments"
    },
    {
      "id": "filter_commercial",
      "type": "capability",
      "capability": "filter",
      "params": {
        "expression": "usage_1 == 'Commercial et services' or usage_2 == 'Commercial et services'"
      },
      "input": "filter_near_arterials"
    }
  ]
}
```

::: tip Download
[scenario-2-rules.json](/gispulse/playground/scenario-2-rules.json)
:::

## Execution

```bash
gispulse run examples/datasets/toulouse_bdtopo.gpkg \
  --layer batiments \
  --rules playground/scenario-2-rules.json \
  -o output/toulouse_commerces_near_arterials.gpkg \
  --ref-source routes:examples/datasets/toulouse_bdtopo.gpkg:routes
```

## Expected Result

::: details Output schema
| Column | Type | Source | Description |
|--------|------|--------|-------------|
| `geometry` | MultiPolygon | source | Building geometry |
| `usage_1` | string | source | Primary usage |
| `usage_2` | string | source | Secondary usage (may be null) |
| `hauteur` | float | source | Height (m) |
| `nombre_de_logements` | int | source | Dwelling count |
:::

On Toulouse, the pipeline keeps **commercial-use buildings** sitting along the arterial network (national + departmental + main urban roads). Order of magnitude: 1,000–2,000 buildings tagged purely or mixed `Commercial et services`, concentrated on transit corridors (av. de Grande-Bretagne, av. de Toulouse, route de Narbonne, etc.).

## Full interactive playground

Live 3-step pipeline (requires the demo backend):

<ClientOnly>
  <DualMapView scenario="data-quality" :showPipeline="true" :showTriggers="false" />
</ClientOnly>

**Step by step:**
1. `filter_routes_arterials` (blue) — importance 2-4 sub-network (~1,600 segments), shown as a layer
2. `filter_near_arterials` (orange) — buildings intersecting the 50 m buffer around that sub-network (Lambert93)
3. `filter_commercial` (purple) — buildings whose `usage_1` or `usage_2` equals `Commercial et services`

**Interactions:**
- Building popup: usage_1, usage_2, hauteur, nombre_de_logements
- Street popup: nom_voie, importance, largeur_de_chaussee — useful on the step-1 layer to inspect the BD TOPO category

## Try it live

<TryItLive endpoint="/capabilities" description="Check filter + ref_filter" />
<TryItLive endpoint="/datasets" description="Dataset toulouse_bdtopo available" />

## Further reading

- [S1: Flood Risk Diagnostic](/en/playground/urban-flood-risk) — same city, 4-step building filter + metric buffer + altitude filter
- [S4: Road Network + Urban Setback](/en/playground/road-setback) — complementary, structural axes in Clermont-Ferrand
- [Vector capabilities](/en/guide/capabilities#vector) — filter with ref_filter, spatial_join, area_length

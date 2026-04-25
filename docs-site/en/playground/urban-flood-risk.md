# S1: Flood Risk Assessment — Toulouse

<span class="gp-difficulty-badge" style="background: var(--gp-orange)">Intermediate</span> <span class="gp-mode-badge">CLI + Map</span>

**Capabilities**: `filter`

## Use Case

A city planner needs a flood risk diagnostic along the Garonne in Toulouse: isolate the low-rise buildings (≤ 15 m) inside the 250 m corridor of the Garonne AND whose ground sits 0–15 m above water level. All in a single GISPulse pipeline.

## IGN BD TOPO V3 Data

| Layer | Content | Features | Source |
|-------|---------|----------|--------|
| `batiments` | IGN buildings (height, floors, dwellings, usage) | ~31,000 | [data.geopf.fr](https://data.geopf.fr) — BDTOPO_V3:batiment |
| `surfaces_eau` | Hydrographic surfaces (Garonne, canals, basins) | 43 | [data.geopf.fr](https://data.geopf.fr) — BDTOPO_V3:surface_hydrographique |
| `cours_eau` | Waterways (Garonne, canals, tributaries) | 14 | [data.geopf.fr](https://data.geopf.fr) — BDTOPO_V3:cours_d_eau |

```bash
# Download BD TOPO data for Toulouse
python examples/prepare_playground_data.py --city toulouse

# Inspect the dataset
gispulse info examples/datasets/toulouse_bdtopo.gpkg
```

## Pipeline (4 steps)

```
cours_eau ──► filter_hydro (toponyme in ['la Garonne','Bras Inférieur Garonne'])   # narrow watercourses to the Garonne corridor

batiments ──► filter_in_flood_zone (intersects filter_hydro, buffer 250 m, L93)    # 250 m corridor
                │
                ▼
            filter_in_flood_altitude (altitude_minimale_sol in [134, 149] m IGN69) # ground 0-15 m above Garonne level
                │
                ▼
            filter_low_buildings (hauteur in ]0, 15] m)                            # low/mid-rise stock — most vulnerable
```

::: warning Metric CRS
IGN data is stored in **EPSG:4326** (WGS84 degrees). For the 250 m buffer in
`filter_in_flood_zone` we reproject to **EPSG:2154 (Lambert93)** — the default
`EPSG:3857` (Web Mercator) distorts distances by **~38 %** at Toulouse's latitude
(factor 1/cos(43.6°)).
:::

::: tip Why a dedicated `filter_hydro` step?
BD TOPO `cours_eau` holds 14 lines: the Garonne and its lower branch, but also water-supply
canals (Saint-Martory, Canal du Midi, Canal Latéral, Canal de Brienne) and tributaries
(Hers Mort, Girou, Sausse, Riou Gras) that don't carry the same flood risk. A 250 m buffer
around all of them flagged buildings west of the city near a supply canal (not floodable).
The `filter_hydro` step narrows `cours_eau` to the Garonne corridor first, then
`filter_in_flood_zone` uses that subset as its `ref_layer`.
:::

::: tip Altitude: no external DTM required
BD TOPO V3 already carries per-building Z values:
`altitude_minimale_sol`, `altitude_maximale_sol`, `altitude_minimale_toit`,
`altitude_maximale_toit` (meters, IGN69 datum). No need to drape an external DEM
(RGEALTI 1 m) over the footprints for this layer.

**Garonne reference in Toulouse**: ~134 m IGN69 at Pont-Neuf. The historical 1875
flood peaked around 142 m (~8 m above normal level). The filter
`altitude_minimale_sol BETWEEN 134 AND 149` keeps buildings whose ground sits
0–15 m above water level — the upper bound is intentionally generous to cover a
centennial flood plus margin.

For a finer diagnostic (upstream/downstream gradient), replace the constant 134
with a spatial join on `surfaces_eau` Z, or sample a DEM raster (`raster_sample`
capability).
:::

::: tip Why cap building height at ≤ 15 m?
Above 15 m (~5 floors) occupants have refuge floors; low buildings (R+0 to R+3)
concentrate human and material exposure during a major flood. Capping at 15 m
isolates the most vulnerable cohort.
:::

## Rules

```json
{
  "version": 2,
  "name": "flood_risk_diagnostic",
  "ref_layers": {
    "cours_eau": "cours_eau",
    "surfaces_eau": "surfaces_eau",
    "batiments": "batiments"
  },
  "steps": [
    {
      "id": "filter_hydro",
      "type": "capability",
      "capability": "filter",
      "params": {
        "expression": "toponyme in ['la Garonne', 'Bras Inférieur Garonne']",
        "crs_meters": "EPSG:2154"
      },
      "input": "cours_eau"
    },
    {
      "id": "filter_in_flood_zone",
      "type": "capability",
      "capability": "filter",
      "params": {
        "spatial_predicate": "intersects",
        "ref_layer": "filter_hydro",
        "buffer_distance": 250,
        "crs_meters": "EPSG:2154"
      }
    },
    {
      "id": "filter_in_flood_altitude",
      "type": "capability",
      "capability": "filter",
      "params": {
        "expression": "altitude_minimale_sol >= 134 and altitude_minimale_sol <= 149"
      },
      "input": "filter_in_flood_zone"
    },
    {
      "id": "filter_low_buildings",
      "type": "capability",
      "capability": "filter",
      "params": {
        "expression": "hauteur > 0 and hauteur <= 15"
      },
      "input": "filter_in_flood_altitude"
    }
  ]
}
```

::: tip Download
[scenario-1-rules.json](/gispulse/playground/scenario-1-rules.json)
:::

## Execution

```bash
gispulse run examples/datasets/toulouse_bdtopo.gpkg \
  --layer batiments \
  --rules playground/scenario-1-rules.json \
  -o output/flood_diagnostic.gpkg \
  --ref-source cours_eau:examples/datasets/toulouse_bdtopo.gpkg:cours_eau

# View result on map
gispulse serve output/flood_diagnostic.gpkg
```

## Expected Result

::: details Output schema
| Column | Type | Source | Description |
|--------|------|--------|-------------|
| `geometry` | MultiPolygon | source | Building geometry |
| `usage_1` | string | source | Main usage (Residential, Industrial...) |
| `hauteur` | float | source | Building height (m) — filtered to ]0, 15] m |
| `altitude_minimale_sol` | float | source | Min ground altitude (m IGN69) — filtered to [134, 149] m |
| `altitude_maximale_toit` | float | source | Max roof altitude (m IGN69) |
| `nombre_d_etages` | int | source | Number of floors |
| `nombre_de_logements` | int | source | Number of dwellings |
:::

From the ~31,000 Toulouse buildings, the pipeline retains those matching the three criteria: (1) within 250 m of the Garonne, (2) ground 0–15 m above Garonne level, (3) low/mid-rise (≤ 15 m). This is the cohort most exposed to a major 1875-class flood. To compute the footprint, chain an `area_length` step on the output of `filter_low_buildings`.

## Full interactive playground

Live 4-step pipeline (requires the demo backend):

<ClientOnly>
  <DualMapView scenario="flood-risk" :showPipeline="true" :showTriggers="false" />
</ClientOnly>

**Step by step:**
1. `filter_hydro` (blue) — narrows `cours_eau` to the Garonne corridor
2. `filter_in_flood_zone` (orange) — buildings within 250 m, Lambert93
3. `filter_in_flood_altitude` (yellow) — ground between 134 and 149 m IGN69
4. `filter_low_buildings` (red) — height ≤ 15 m, most vulnerable cohort

**Interactions:**
- Building popup: usage, height, ground altitude, dwellings, floors
- Each step colours the cohort reached after its filter

## Try it live

<TryItLive endpoint="/health" description="Demo server status" />
<TryItLive endpoint="/datasets" description="Available datasets" />

## Next Steps

- [S2: Commercial Buildings along Arterials](/en/playground/commercial-arterials) — same city, 2-step filter + ref_filter pattern
- [S6: Residential Real Estate](/en/playground/real-estate) — per-dwelling metrics in Versailles
- [Vector capabilities](/en/guide/capabilities#vector) — filter with ref_layer, ref_filter, buffer_distance

# S6: Price-per-m² Map — Versailles (DVF)

<span class="gp-difficulty-badge" style="background: var(--gp-orange)">Intermediate</span> <span class="gp-mode-badge">CLI + Map</span>

**Capabilities**: `filter` `calculate` `classify` `grid_create` `spatial_aggregate`

## Use Case

A real-estate analyst wants to visualize the spatial structure of price per square meter in Versailles. We start from DVF mutations (Demandes de Valeurs Foncières, Etalab open data), filter to residential sales, compute `price_per_m2 = valeur_fonciere / surface_reelle_bati`, drop outliers (parking spots, garages, data-entry mistakes), then classify the remaining transactions into **quintiles** with a **YlOrRd** palette (pale yellow → dark red, ColorBrewer 5 classes).

We then lay a **regular 100 m × 100 m fishnet** over the sales extent (`grid_create`, Lambert93 EPSG:2154), aggregate each tile's mean €/m² from the DVF points it contains (`spatial_aggregate`, predicate `contains`), and classify the cells into quintiles with the same YlOrRd ramp to paint a **clean heatmap** — contiguous square tiles that read like a thematic continuous surface, much easier than scattered point dots.

## Source: DVF Etalab

| Source | Content | Features (Versailles 2022-2024) | Key attributes |
|--------|---------|---------------------------------|----------------|
| [geo-dvf Etalab](https://files.data.gouv.fr/geo-dvf/latest/csv/) | Geolocated real-estate transactions | ~7,000 raw across 8 communes (Versailles + Le Chesnay-Rocquencourt + Viroflay + Vélizy + Jouy + Buc + Saint-Cyr + Bailly, 2022-2024) → ~5,100 residential after filter | `valeur_fonciere`, `surface_reelle_bati`, `type_local`, `nature_mutation`, `date_mutation` |

CSV files are published per year + commune:
`https://files.data.gouv.fr/geo-dvf/latest/csv/{year}/communes/{dept}/{insee}.csv`

Versailles = INSEE **78646**, département **78**.

```bash
python examples/prepare_playground_data.py --city versailles
gispulse info examples/datasets/versailles_bdtopo.gpkg --layer dvf_ventes
```

The script concats 2022+2023+2024, builds Point geometry from `longitude`/`latitude`, drops rows without coordinates or prices, then writes the `dvf_ventes` layer into the GPKG.

## Pipeline (8 steps)

```
dvf_ventes ──► filter (nature_mutation=='Vente' AND type_local in ['Maison','Appartement'])
                │                                                       # residential sales
                ▼
            calculate → price_per_m2 = valeur_fonciere / surface_reelle_bati
                │
                ▼
            filter (1500 <= price_per_m2 <= 25000)                      # DVF outlier trim
                │
                ▼
            classify → price_class (1..5) + price_color (YlOrRd)        # quintiles (points)
                         method: quantile, bins: 5
                │
                ▼
            grid_create → regular 100 m × 100 m fishnet                  # square tiles
              ref_layer: drop_price_outliers   (envelope of sales)
              cell_size: 100                    (metres)
              crs_meters: EPSG:2154             (Lambert93)
              clip_to_extent: true              (drop tiles outside sales)
                │
                ▼
            spatial_aggregate                                             # spatial attribute
              ref_layer: drop_price_outliers                              # tile ⊇ DVF points
              predicate: contains
              agg: mean_price_per_m2, max_price_per_m2, tx_count
                │
                ▼
            filter (tx_count > 0)                                         # keep tiles ≥1 sale
                │
                ▼
            classify → tile_class (1..5) + tile_color (YlOrRd)            # heatmap choropleth
                         field: mean_price_per_m2, method: quantile, bins: 5
```

**Steps 1–4 (points)** — ColorBrewer `YlOrRd` 5-class palette (`#ffffb2`, `#fecc5c`, `#fd8d3c`, `#f03b20`, `#bd0026`) attached per feature in `price_color`; each quintile holds ~20% of mutations.

**Steps 5–8 (tiles)** — `grid_create` emits a 100 m × 100 m fishnet in Lambert93 (exact metric) over the filtered-sales extent (~950 non-empty tiles); `spatial_aggregate` computes `mean_price_per_m2`, `max_price_per_m2`, `tx_count` per tile from the DVF points it contains; empty tiles are dropped; the final `classify` paints the **choropleth** as a high-resolution heatmap — fine-grained 100 m cells, easy thematic read, print-ready for QGIS export. Note: at 100 m on the wider S5 extent ~25 % of tiles carry a single transaction (vs ~35 % at the previous 50 m mesh on Versailles centre alone), so quintiles are more statistically stable while still keeping a fine-grained thematic read.

## Rules

```json
{
  "version": 2,
  "ref_layers": { "dvf_ventes": "dvf_ventes" },
  "steps": [
    {
      "id": "filter_residential_sales",
      "capability": "filter",
      "params": {
        "expression": "nature_mutation == 'Vente' and type_local in ['Maison', 'Appartement']"
      }
    },
    {
      "id": "compute_price_per_m2",
      "capability": "calculate",
      "params": { "expressions": { "price_per_m2": "valeur_fonciere / surface_reelle_bati" } },
      "input": "filter_residential_sales"
    },
    {
      "id": "drop_price_outliers",
      "capability": "filter",
      "params": { "expression": "price_per_m2 >= 1500 and price_per_m2 <= 25000" },
      "input": "compute_price_per_m2"
    },
    {
      "id": "classify_price_quintiles",
      "capability": "classify",
      "params": {
        "field": "price_per_m2",
        "method": "quantile",
        "bins": 5,
        "class_col": "price_class",
        "color_col": "price_color",
        "palette": ["#ffffb2", "#fecc5c", "#fd8d3c", "#f03b20", "#bd0026"]
      },
      "input": "drop_price_outliers"
    },
    {
      "id": "create_price_grid",
      "capability": "grid_create",
      "params": {
        "ref_layer": "drop_price_outliers",
        "cell_size": 100,
        "crs_meters": "EPSG:2154",
        "clip_to_extent": true
      },
      "input": "drop_price_outliers"
    },
    {
      "id": "aggregate_price_to_grid",
      "capability": "spatial_aggregate",
      "params": {
        "ref_layer": "drop_price_outliers",
        "predicate": "contains",
        "agg": {
          "mean_price_per_m2": ["price_per_m2", "mean"],
          "max_price_per_m2": ["price_per_m2", "max"],
          "tx_count": ["price_per_m2", "count"]
        }
      },
      "input": "create_price_grid"
    },
    {
      "id": "keep_cells_with_sales",
      "capability": "filter",
      "params": { "expression": "tx_count > 0" },
      "input": "aggregate_price_to_grid"
    },
    {
      "id": "classify_grid_choropleth",
      "capability": "classify",
      "params": {
        "field": "mean_price_per_m2",
        "method": "quantile",
        "bins": 5,
        "class_col": "tile_class",
        "color_col": "tile_color",
        "palette": ["#ffffb2", "#fecc5c", "#fd8d3c", "#f03b20", "#bd0026"]
      },
      "input": "keep_cells_with_sales"
    }
  ]
}
```

::: tip Download
[scenario-6-rules.json](/gispulse/playground/scenario-6-rules.json)
:::

## Execution

```bash
gispulse run examples/datasets/versailles_bdtopo.gpkg \
  --layer dvf_ventes \
  --rules playground/scenario-6-rules.json \
  -o output/versailles_price_map.gpkg

gispulse serve output/versailles_price_map.gpkg
```

## Expected Result

::: details Output schema — point layer (step 4)
| Column | Type | Source | Description |
|--------|------|--------|-------------|
| `geometry` | Point | source | DVF parcel centroid |
| `date_mutation` | date | source | Sale date |
| `nature_mutation` | string | source | "Vente" after filter |
| `type_local` | string | source | "Maison" or "Appartement" |
| `valeur_fonciere` | float | source | Sale price (€) |
| `surface_reelle_bati` | float | source | Built surface (m²) |
| `price_per_m2` | float | step 2 (`calculate`) | Price per square meter |
| `price_class` | int | step 4 (`classify`) | Quintile 1..5 |
| `price_color` | string | step 4 (`classify`) | Hex color (YlOrRd palette) |
:::

::: details Output schema — 50 m tile choropleth (step 8)
| Column | Type | Source | Description |
|--------|------|--------|-------------|
| `geometry` | Polygon | step 5 (`grid_create`) | 100 m × 100 m square tile in Lambert93 |
| `row` | int | step 5 (`grid_create`) | Fishnet row index |
| `col` | int | step 5 (`grid_create`) | Fishnet column index |
| `mean_price_per_m2` | float | step 6 (`spatial_aggregate`) | Mean €/m² of contained DVF points |
| `max_price_per_m2` | float | step 6 (`spatial_aggregate`) | Max €/m² observed in this tile |
| `tx_count` | int | step 6 (`spatial_aggregate`) | Number of mutations in tile |
| `tile_class` | int | step 8 (`classify`) | Quintile 1..5 of mean price |
| `tile_color` | string | step 8 (`classify`) | Hex color of the choropleth |
:::

::: info Versailles 2022-2024 quintile edges (typical, after outlier trim)
- **Q1** (< ~€5,200/m²): pale yellow `#ffffb2` — peripheral segments, atypical units
- **Q2** (€5,200 → €6,400/m²): light orange `#fecc5c`
- **Q3** (€6,400 → €7,300/m²): orange `#fd8d3c` — market median
- **Q4** (€7,300 → €8,500/m²): red-orange `#f03b20`
- **Q5** (> ~€8,500/m²): dark red `#bd0026` — Notre-Dame, Château district

Quintile edges are recomputed dynamically: bins shift if you change the period or the spatial filter.
:::

## Full interactive playground

Live 8-step pipeline (requires demo backend).

<ClientOnly><DualMapView scenario="real-estate" :showPipeline="true" :showTriggers="false" /></ClientOnly>

**Points (DVF) — per-mutation gradient**

1. `filter_residential_sales` (orange) — keep only Maison / Appartement sales
2. `compute_price_per_m2` (cyan) — ratio `valeur_fonciere / surface_reelle_bati`
3. `drop_price_outliers` (orange) — `1500 ≤ price/m² ≤ 25000 €`
4. `classify_price_quintiles` (red) — quintiles + `YlOrRd` palette → **color gradient** on points

**Choropleth (tiles) — 50 m heatmap**

5. `create_price_grid` (teal) — 100 m × 100 m fishnet in Lambert93 clipped to the DVF extent (~950 non-empty tiles)
6. `aggregate_price_to_grid` (purple) — `spatial_aggregate`: per tile, mean `price_per_m2` of contained DVF points (+ max, + count)
7. `keep_cells_with_sales` (orange) — drop empty tiles (`tx_count > 0`)
8. `classify_grid_choropleth` (red) — quintiles on `mean_price_per_m2` + `YlOrRd` → **heatmap choropleth**

DVF popup: date, type_local, valeur_fonciere, surface_reelle_bati, price_per_m2, price_class.
Tile popup: row, col, mean_price_per_m2, max_price_per_m2, tx_count, tile_class.
Legend: each quintile ~20% (points, then tiles), same palette, continuous thematic read.

## Try it live

<TryItLive endpoint="/capabilities" description="lists available capabilities (filter, calculate, classify) used by the S6 pipeline" />

<TryItLive endpoint="/datasets" description="lists demo datasets, including versailles_bdtopo with the dvf_ventes layer loaded for this scenario" />

## Next steps

- [S5: Green Spaces](/en/playground/green-spaces) — another Versailles workflow
- [Vector capabilities](/en/guide/capabilities#vector) — `classify`, `filter`, `calculate`
- [DVF Etalab](https://files.data.gouv.fr/geo-dvf/latest/) — 2014-2025, updated twice per year, all communes

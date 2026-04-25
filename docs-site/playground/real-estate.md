# S6 : Carte du prix au m² — Versailles (DVF)

<span class="gp-difficulty-badge" style="background: var(--gp-orange)">Intermediaire</span> <span class="gp-mode-badge">CLI + Map</span>

**Capabilities** : `filter` `calculate` `classify` `grid_create` `spatial_aggregate`

## Cas d'usage

Un analyste immobilier veut visualiser la structure spatiale des prix au m² a Versailles. On part des mutations DVF (Demandes de Valeurs Foncieres, Etalab open data), on filtre aux ventes residentielles, on calcule `price_per_m2 = valeur_fonciere / surface_reelle_bati`, on retire les outliers (parkings, garages, erreurs de saisie), puis on classe les transactions restantes en **quintiles** avec une palette **YlOrRd** (jaune pale → rouge sombre, ColorBrewer 5 classes).

On construit ensuite un **quadrillage regulier 50 m × 50 m** (`grid_create`, CRS Lambert93 EPSG:2154, clippe a l'emprise des ventes). Chaque tuile agrege le prix moyen / m² des mutations qu'elle contient (`spatial_aggregate`, predicate `contains`) et la derniere `classify` peint un **heatmap lisible** : des tuiles carrees contigues, meme gradient YlOrRd que les points, mais en lecture thematique continue plutot qu'en points eparpilles.

## Source : DVF Etalab

| Source | Contenu | Features (Versailles 2022-2024) | Attributs cles |
|--------|---------|---------------------------------|----------------|
| [geo-dvf Etalab](https://files.data.gouv.fr/geo-dvf/latest/csv/) | Mutations immobilieres geolocalisees | ~8 500 brutes → ~2 500 residentielles apres filtrage | `valeur_fonciere`, `surface_reelle_bati`, `type_local`, `nature_mutation`, `date_mutation` |

Les CSV sont publies par annee et par commune :
`https://files.data.gouv.fr/geo-dvf/latest/csv/{year}/communes/{dept}/{insee}.csv`

Versailles = INSEE **78646**, departement **78**.

```bash
python examples/prepare_playground_data.py --city versailles
gispulse info examples/datasets/versailles_bdtopo.gpkg --layer dvf_ventes
```

Le script concat 2022+2023+2024, construit la geometrie Point depuis `longitude`/`latitude`, supprime les lignes sans coordonnees ou sans prix, puis ecrit la couche `dvf_ventes` dans le GPKG.

## Pipeline (8 etapes)

```
dvf_ventes ──► filter (nature_mutation=='Vente' AND type_local in ['Maison','Appartement'])
                │                                                       # ventes residentielles
                ▼
            calculate → price_per_m2 = valeur_fonciere / surface_reelle_bati
                │
                ▼
            filter (1500 <= price_per_m2 <= 25000)                      # retire outliers DVF
                │
                ▼
            classify → price_class (1..5) + price_color (YlOrRd)        # quintiles points
                         method: quantile, bins: 5
                │
                ▼
            grid_create → fishnet regulier 50 m × 50 m                # tuiles carrees
              ref_layer: drop_price_outliers   (emprise des ventes)
              cell_size: 50                     (metres)
              crs_meters: EPSG:2154             (Lambert93)
              clip_to_extent: true              (drop tuiles hors DVF)
                │
                ▼
            spatial_aggregate                                             # jointure attributaire
              ref_layer: drop_price_outliers                              # tuile ⊇ points DVF
              predicate: contains
              agg: mean_price_per_m2, max_price_per_m2, tx_count
                │
                ▼
            filter (tx_count > 0)                                         # garde tuiles ≥1 vente
                │
                ▼
            classify → tile_class (1..5) + tile_color (YlOrRd)            # choroplethe heatmap
                         field: mean_price_per_m2, method: quantile, bins: 5
```

**Etapes 1-4 (points)** — palette ColorBrewer `YlOrRd` 5 classes (`#ffffb2`, `#fecc5c`, `#fd8d3c`, `#f03b20`, `#bd0026`) ecrite feature par feature dans `price_color` ; chaque quintile contient ~20 % des mutations.

**Etapes 5-8 (tuiles)** — `grid_create` genere un fishnet 50 m × 50 m en Lambert93 (metrique exact) sur l'emprise des ventes filtrees (~826 tuiles non vides) ; `spatial_aggregate` calcule `mean_price_per_m2`, `max_price_per_m2` et `tx_count` par tuile en comptant les points DVF contenus ; les tuiles vides sont droppees ; la derniere `classify` peint la **choroplethe finale** — heatmap 50 m haute resolution, lecture thematique fine, pret pour export QGIS / cartographie print. Note : a cette resolution ~35 % des tuiles portent une seule transaction, donc le quintile de ces cellules reflete l'observation brute plutot qu'une statistique stable — c'est l'arbitrage resolution/stabilite classique du quadrillage fin.

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
        "cell_size": 50,
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

::: tip Telecharger
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

## Resultat attendu

::: details Schema de sortie — couche points (step 4)
| Colonne | Type | Origine | Description |
|---------|------|---------|-------------|
| `geometry` | Point | source | Centroide parcelle DVF |
| `date_mutation` | date | source | Date de la vente |
| `nature_mutation` | string | source | "Vente" apres filtre |
| `type_local` | string | source | "Maison" ou "Appartement" |
| `valeur_fonciere` | float | source | Prix de vente (€) |
| `surface_reelle_bati` | float | source | Surface bati (m²) |
| `price_per_m2` | float | step 2 (`calculate`) | Prix au m² (€ / m²) |
| `price_class` | int | step 4 (`classify`) | Quintile 1..5 |
| `price_color` | string | step 4 (`classify`) | Couleur hex (palette YlOrRd) |
:::

::: details Schema de sortie — choroplethe tuiles 50 m (step 8)
| Colonne | Type | Origine | Description |
|---------|------|---------|-------------|
| `geometry` | Polygon | step 5 (`grid_create`) | Tuile carree 50 m × 50 m en Lambert93 |
| `row` | int | step 5 (`grid_create`) | Indice ligne du fishnet |
| `col` | int | step 5 (`grid_create`) | Indice colonne du fishnet |
| `mean_price_per_m2` | float | step 6 (`spatial_aggregate`) | Prix moyen / m² des DVF contenus |
| `max_price_per_m2` | float | step 6 (`spatial_aggregate`) | Prix max / m² observe sur la tuile |
| `tx_count` | int | step 6 (`spatial_aggregate`) | Nombre de mutations dans la tuile |
| `tile_class` | int | step 8 (`classify`) | Quintile 1..5 du prix moyen |
| `tile_color` | string | step 8 (`classify`) | Couleur hex de la choroplethe |
:::

::: info Quintiles Versailles 2022-2024 (valeurs typiques, apres filtre outliers)
- **Q1** (< ~5 200 €/m²) : pale yellow `#ffffb2` — segments peripheriques, biens atypiques
- **Q2** (5 200 → 6 400 €/m²) : light orange `#fecc5c`
- **Q3** (6 400 → 7 300 €/m²) : orange `#fd8d3c` — mediane marche
- **Q4** (7 300 → 8 500 €/m²) : red-orange `#f03b20`
- **Q5** (> ~8 500 €/m²) : dark red `#bd0026` — Notre-Dame, quartier Chateau

Les bornes sont recalculees dynamiquement : les quintiles s'ajustent si on change la periode ou le filtre geographique.
:::

## Playground interactif complet

Pipeline live 8 etapes (necessite backend demo).

<ClientOnly><DualMapView scenario="real-estate" :showPipeline="true" :showTriggers="false" /></ClientOnly>

**Points (DVF) — gradient par mutation**

1. `filter_residential_sales` (orange) — ne garde que les ventes Maison / Appartement
2. `compute_price_per_m2` (cyan) — ratio `valeur_fonciere / surface_reelle_bati`
3. `drop_price_outliers` (orange) — filtre `1500 ≤ price/m² ≤ 25000 €`
4. `classify_price_quintiles` (rouge) — quintiles + palette `YlOrRd` → **gradient de couleur** sur les points

**Choroplethe (tuiles) — heatmap 50 m**

5. `create_price_grid` (turquoise) — fishnet 50 m × 50 m en Lambert93 sur l'emprise DVF (~826 tuiles non vides)
6. `aggregate_price_to_grid` (violet) — `spatial_aggregate` : par tuile, moyenne des `price_per_m2` DVF contenus (+ max, + count)
7. `keep_cells_with_sales` (orange) — filtre `tx_count > 0` pour drop tuiles orphelines
8. `classify_grid_choropleth` (rouge) — quintiles sur `mean_price_per_m2` + palette `YlOrRd` → **choroplethe heatmap**

Popup DVF : date, type_local, valeur_fonciere, surface_reelle_bati, price_per_m2, price_class.
Popup tuile : row, col, mean_price_per_m2, max_price_per_m2, tx_count, tile_class.
Legende : chaque quintile ~20 % (points puis tuiles), meme palette, lecture thematique continue.

## Essayer en live

<TryItLive endpoint="/capabilities" description="liste les capabilities disponibles (filter, calculate, classify) utilisees par le pipeline S6" />

<TryItLive endpoint="/datasets" description="liste les datasets demo, dont versailles_bdtopo avec la couche dvf_ventes chargee pour ce scenario" />

## Pour aller plus loin

- [S5 : Espaces Verts](/playground/green-spaces) — autre workflow Versailles
- [Capabilities vecteur](/guide/capabilities#vector) — `classify`, `filter`, `calculate`
- [DVF Etalab](https://files.data.gouv.fr/geo-dvf/latest/) — 2014-2025, mise a jour 2x/an, toutes communes

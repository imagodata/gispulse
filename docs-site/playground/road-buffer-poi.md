# S3 : Accessibilite Sante — Clermont-Ferrand

<span class="gp-difficulty-badge" style="background: var(--gp-red)">Avance</span> <span class="gp-mode-badge">CLI + Map</span>

**Capabilities** : `filter` `isochrone` `classify_by_ring`

## Cas d'usage

Un planificateur sante analyse la couverture des etablissements de sante a Clermont-Ferrand avec **quatre anneaux isochrones concentriques** calcules sur le reseau routier reel BD TOPO — **500 m (~5 min), 750 m (~7.5 min), 1 km (~10 min), 1.5 km (~15 min)** de marche depuis chaque POI `categorie == 'Santé'`. Les quatre anneaux sont emis en **une seule passe Dijkstra multi-sources** (parametre `cost_budgets`, CRS metrique EPSG:2154) puis `classify_by_ring` attribue a chaque batiment l'anneau le plus interne qui le contient. Palette : **vert** (dans la zone 500 m, servi) -> **jaune** (500-750 m, +2.5 min) -> **orange** (750 m-1 km, +5 min) -> **rouge** (1-1.5 km, +10 min) -> **rouge fonce** (au-dela de 1.5 km).

## Donnees IGN BD TOPO V3 + OSM

| Couche | Contenu | Features | Source |
|--------|---------|----------|--------|
| `routes` | Troncons de route (nature, nb voies, largeur, vitesse) | 2 272 | [data.geopf.fr](https://data.geopf.fr) — BDTOPO_V3:troncon_de_route |
| `equipements` | POI santé OSM (Overpass) — exhaustif : pharmacies, médecins généralistes et spécialistes, hôpitaux, cliniques, dentistes, laboratoires, kinés, maisons de retraite, centres médico-sociaux, vétérinaires... | **223** | [overpass-api.de](https://overpass-api.de) — `amenity~hospital|clinic|doctors|dentist|pharmacy|nursing_home|social_facility|veterinary` + `healthcare=*` |
| `batiments` | Batiments a rattacher | ~77 000 | [data.geopf.fr](https://data.geopf.fr) — BDTOPO_V3:batiment |

::: tip Pourquoi OSM et pas la BD TOPO `equipements` ?
La BD TOPO V3 ne liste que **47 établissements "Santé"** dans la bbox Clermont — hôpitaux, cliniques, maisons de retraite, thermes. Elle ignore les médecins individuels, pharmacies, dentistes, laboratoires. OSM remonte **223 POIs santé** — cache Overpass généré par [`scripts/fetch_health_pois_osm.py`](https://github.com/imagodata/gispulse/blob/main/scripts/fetch_health_pois_osm.py) et committé dans `examples/datasets/clermont_ferrand_health_osm.geojson`.
:::

```bash
python examples/prepare_playground_data.py --city clermont-ferrand
gispulse info examples/datasets/clermont_ferrand_bdtopo.gpkg
```

## Pipeline (3 etapes)

```
equipements (223 OSM POIs) ──► filter categorie == 'Santé'
                              │  → 223 POIs santé (pharmacies, médecins, labos, EHPAD, hôpitaux, dentistes, kinés...)
                              │
                              ▼
                     isochrone cost_budgets=[500, 750, 1000, 1500]
                              │  Dijkstra multi-sources, CRS EPSG:2154, 1 passe -> 4 anneaux
                              │  → GeoDataFrame de 4 polygones { cost_budget, geometry }
                              │
batiments ─────────────────► classify_by_ring ref_layers=[isochrone_rings]
                              │  → access_ring (500|750|1000|1500|99999)
                              │  → access_class (1..5)
                              │  → access_color (vert -> rouge fonce)
                              ▼
                     TOUS les batiments cartographies par anneau d'appartenance
```

## Rules

```json
{
  "version": 2,
  "name": "health_accessibility",
  "ref_layers": {
    "routes": "routes",
    "batiments": "batiments"
  },
  "steps": [
    {
      "id": "filter_sante",
      "type": "capability",
      "capability": "filter",
      "params": { "expression": "categorie == 'Santé'" }
    },
    {
      "id": "isochrone_rings",
      "type": "capability",
      "capability": "isochrone",
      "params": {
        "ref_layer": "routes",
        "cost_budgets": [500, 750, 1000, 1500],
        "crs_meters": "EPSG:2154",
        "edge_buffer_m": 200,
        "dissolve": true
      },
      "input": "filter_sante"
    },
    {
      "id": "classify_by_ring",
      "type": "capability",
      "capability": "classify_by_ring",
      "params": {
        "ref_layers": ["isochrone_rings"],
        "ring_field": "cost_budget",
        "class_col": "access_class",
        "color_col": "access_color",
        "value_col": "access_ring",
        "palette": ["#1a9850", "#fee08b", "#fdae61", "#f46d43", "#a50026"],
        "use_centroid": true,
        "ring_simplify_tolerance": 10.0
      },
      "input": "batiments"
    }
  ]
}
```

::: info Mode multi-budget (`cost_budgets`)
Une seule passe Dijkstra avec `cutoff = max(cost_budgets)`, puis filtrage des noeuds atteignables par budget pour buffer+dissolve chacun. Cost de N anneaux ≈ cost d'un seul. Chaque anneau est une zone **pleine** (pas un anneau evidé) — `classify_by_ring` choisit l'anneau le plus interne qui contient un feature. Cote rendu, le playground attribue automatiquement une couleur par budget (vert -> rouge) et inverse l'ordre de dessin pour que l'anneau le plus petit soit affiche au-dessus.
:::

::: info Perf — `use_centroid` + `ring_simplify_tolerance`
Sur ~50 000 batiments × 4 anneaux issus de la BD TOPO, le `sjoin` polygone-vs-polygone exact prend > 100 s : chaque anneau de 1.5 km est un polygone unaire de plusieurs dizaines de milliers de sommets, et `intersects` paye le cout des bords contre chaque empreinte de batiment. Deux leviers cumulables :

- `use_centroid: true` — passe en `within` sur le centroide de chaque batiment. Les empreintes etant petites (~10-30 m de cote), la classe attribuee reste identique sauf pour les rares batiments qui chevauchent vraiment une frontiere d'anneau.
- `ring_simplify_tolerance: 10.0` — simplifie les anneaux a 10 m avant le join, ce qui retire l'essentiel des sommets sans deplacer visiblement les frontieres a l'echelle ville.

Effet typique : **138 s -> ~3 s** sur le scenario S3 sans changer les classes affichees.
:::

::: info CRS metrique — pourquoi EPSG:2154
Les donnees sources sont en EPSG:4326 (degres). Un `cost_budget: 500` sans reprojection serait interprete en degres (~55 km). Le parametre `crs_meters: "EPSG:2154"` reprojette le reseau en Lambert-93 le temps du routing, pour que les budgets soient exprimes en metres reels ; le resultat est ensuite reprojete vers la CRS d'origine.
:::

::: tip Telecharger
[scenario-3-rules.json](/gispulse/playground/scenario-3-rules.json)
:::

## Execution

```bash
gispulse run examples/datasets/clermont_ferrand_bdtopo.gpkg \
  --layer equipements \
  --rules playground/scenario-3-rules.json \
  -o output/health_coverage.gpkg

gispulse serve output/health_coverage.gpkg
```

## Resultat attendu

::: details Schema de sortie
| Colonne | Type | Description |
|---------|------|-------------|
| `access_ring` | float | Valeur `cost_budget` de l'anneau le plus interne qui contient le batiment, ou 99999 hors de toute zone |
| `access_class` | int | Indice 1-5 de l'anneau (1 = dans la zone 500 m, 5 = au-dela de 1.5 km) |
| `access_color` | string | Hex de la palette RdYlGn inversee (vert -> rouge fonce) |
:::

## Playground interactif complet

Pipeline live en 3 etapes (necessite le backend de demo).

<ClientOnly>
  <DualMapView scenario="accessibility" :showPipeline="true" :showTriggers="false" />
</ClientOnly>

Etape par etape :

1. **`filter_sante`** (orange) — conserve les features ou `categorie == 'Santé'` (hopitaux, cliniques, pharmacies, cabinets...).
2. **`isochrone_rings`** (degradé violet → bleu) — Dijkstra multi-sources sur `routes` en CRS metrique EPSG:2154, une passe pour les 4 budgets `[500, 750, 1000, 1500]`. Output : 4 polygones dissolus empiles, un par budget.
3. **`classify_by_ring`** (multicolore) — pour chaque batiment, recupere le plus petit `cost_budget` des anneaux qui le contiennent, mappe a un indice 1..5 (5 = hors de tout anneau) et colore via la palette.

Interactions :
- Popup sur les marqueurs d'etablissements de sante (categorie, nature).
- Le polygone d'isochrone suit le reseau routier reel (ce n'est pas un buffer circulaire).
- **Clique sur la carte** pour ajouter une nouvelle source : l'isochrone est recalculee en direct.

## Essayer en live

<TryItLive endpoint="/capabilities" description="Liste les capabilities disponibles (filter, isochrone, classify_by_ring, etc.)." />

<TryItLive endpoint="/datasets" description="Liste les datasets exposes par la demo, dont clermont_ferrand_bdtopo." />

## Pour aller plus loin

- [S4 : Reseau Routier + Recul Urbanisme](/playground/road-setback) — buffer 50 m et trigger DML a Clermont-Ferrand
- [Capabilities reseau](/guide/capabilities#network) — isochrone, shortest_path, connectivity_check
- [Cross-layer references](/guide/rules#cross-layer) — syntaxe `--ref-source`

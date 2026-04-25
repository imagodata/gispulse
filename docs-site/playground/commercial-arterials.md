# S2 : Commerces le long des Axes Structurants — Toulouse

<span class="gp-difficulty-badge" style="background: var(--gp-green)">Debutant</span> <span class="gp-mode-badge">CLI + Map</span>

**Capabilities** : `filter`

## Cas d'usage

Un service urbanisme veut **identifier les batiments a usage commercial de Toulouse situes a moins de 50 m d'un axe structurant** (routes IGN d'importance 2, 3 ou 4 — nationales, departementales, voies principales urbaines) pour cartographier l'offre commerciale le long des corridors a fort trafic.

Le pipeline enchaine trois etapes (chacune produit un layer visible sur la carte) :
- **`filter_routes_arterials`** — sous-ensemble du reseau routier : on garde uniquement les troncons d'`importance in ['2','3','4']`. Layer reseau visible.
- **`filter_near_arterials`** — sur la couche `batiments`, predicat spatial `intersects` contre `filter_routes_arterials` avec un `buffer_distance: 50` (Lambert93) : tampon de 50 m metriques autour des arteres, on garde les batiments qui l'intersectent.
- **`filter_commercial`** — filtre attributaire : `usage_1` **ou** `usage_2` == `Commercial et services` (la BD TOPO expose deux colonnes d'usage ; un commerce peut etre encode en usage principal ou secondaire).

## Donnees IGN BD TOPO V3

| Couche | Contenu | Features | Source |
|--------|---------|----------|--------|
| `batiments` | Batiments IGN Toulouse (usage, hauteur, etages, logements) | ~31 000 | [data.geopf.fr](https://data.geopf.fr) — BDTOPO_V3:batiment |
| `routes` | Troncons routiers IGN Toulouse (importance 1-5, nature, nom_voie, largeur_de_chaussee) | ~6 000 | [data.geopf.fr](https://data.geopf.fr) — BDTOPO_V3:troncon_de_route |

**Distribution des importances** (Toulouse) : 1 (autoroutes): ~70 · **2 (nationales): ~250** · **3 (departementales): 987** · **4 (voies principales): 410** · 5 (voies communales/rues): 3 497 · 6 (chemins/acces): 1 112.

```bash
python examples/prepare_playground_data.py --city toulouse
gispulse info examples/datasets/toulouse_bdtopo.gpkg
```

## Pipeline (3 etapes)

```
routes  ──► filter_routes_arterials                 # step 1 : axes structurants
              (importance in ['2','3','4'])         #          → ~1 600 troncons (layer visible)
                │
                ▼ (utilise comme ref_layer)
batiments ──► filter_near_arterials                 # step 2 : batiments le long du reseau
              (intersects ref_layer, buffer 50 m,   #          → cohorte spatiale (layer visible)
               Lambert93)
                │
                ▼
            filter_commercial                       # step 3 : commerces
              (usage_1 == 'Commercial et services'  #          → cohorte finale (layer visible)
               or usage_2 == 'Commercial et services')
```

Chaque step expose son resultat sous forme de layer GeoJSON, ce qui permet a la
carte interactive de visualiser **a la fois le reseau filtre et les cohortes batiments**
a chaque etape.

::: tip Pourquoi un step separe pour les routes ?
Avec un `ref_filter` inline (ancienne version), le sous-reseau retenu n'etait jamais
materialise comme layer : on voyait les batiments resultats mais pas QUELS axes
avaient servi au calcul. Le step explicite `filter_routes_arterials` produit la couche
des arteres comme artefact intermediaire, ce qui rend le diagnostic auditable.
:::

::: warning CRS metriques
Les donnees IGN sont stockees en **EPSG:4326** (WGS84). Le buffer 50 m est calcule en
**EPSG:2154 (Lambert93)** pour garantir 50 m au sol. Sans `crs_meters`, le defaut
`EPSG:3857` (Web Mercator) donnerait ~36 m reels a la latitude de Toulouse.
:::

::: info Deux colonnes d'usage
La BD TOPO encode un usage principal (`usage_1`) et un usage secondaire (`usage_2`).
Un immeuble mixte commerce + logement peut etre tagge `Résidentiel` / `Commercial et services`
ou l'inverse. Le `or` pandas garantit qu'on capte les deux cas.
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

::: tip Telecharger
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

## Resultat attendu

::: details Schema de sortie
| Colonne | Type | Origine | Description |
|---------|------|---------|-------------|
| `geometry` | MultiPolygon | source | Geometrie du batiment |
| `usage_1` | string | source | Usage principal |
| `usage_2` | string | source | Usage secondaire (peut etre null) |
| `hauteur` | float | source | Hauteur (m) |
| `nombre_de_logements` | int | source | Nombre de logements |
:::

Sur Toulouse, le pipeline retient **les batiments a vocation commerciale** situes
le long des axes structurants (nationales, departementales et voies principales urbaines).
Cohorte attendue : ordre de grandeur 1 000-2 000 batiments en `Commercial et services`
pur ou mixte, concentres sur les corridors de transit (av. de Grande-Bretagne,
av. de Toulouse, route de Narbonne, etc.).

## Playground interactif complet

Pipeline live en 3 etapes (necessite le backend demo) :

<ClientOnly>
  <DualMapView scenario="data-quality" :showPipeline="true" :showTriggers="false" />
</ClientOnly>

**Etape par etape :**
1. `filter_routes_arterials` (bleu) — sous-reseau d'importance 2-4 (~1 600 troncons), affiche comme layer
2. `filter_near_arterials` (orange) — batiments intersectant le buffer 50 m autour de ce sous-reseau (Lambert93)
3. `filter_commercial` (violet) — batiments dont `usage_1` ou `usage_2` vaut `Commercial et services`

**Interactions :**
- Popup batiment : usage_1, usage_2, hauteur, logements
- Popup voie : nom_voie, importance, largeur_de_chaussee — utile sur le layer step 1 pour verifier la categorie BD TOPO

## Essayer en live

<TryItLive endpoint="/capabilities" description="Verifier filter + ref_filter" />
<TryItLive endpoint="/health" description="Statut du serveur demo" />

## Pour aller plus loin

- [S1 : Risque Inondation](/playground/urban-flood-risk) — meme ville, 4 etapes filtre bati + buffer metrique + altitude
- [S4 : Reseau Routier + Recul Urbanisme](/playground/road-setback) — complement, axes structurants a Clermont-Ferrand
- [Capabilities vecteur](/guide/capabilities#vecteur) — filter avec ref_filter, spatial_join, area_length

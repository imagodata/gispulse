# S5 : Accessibilité parcs par bâtiment — Versailles

<span class="gp-difficulty-badge" style="background: var(--gp-orange)">Intermediaire</span> <span class="gp-mode-badge">CLI + Map</span>

**Capabilities** : `area_length` `filter` `nearest_neighbor` `classify`

## Cas d'usage

Un service urbanisme veut répondre à une question opérationnelle : **quels logements sont en déficit d'accès à un parc de proximité ?** Pas "quels types de végétation existent" — les fiches BD TOPO le disent déjà — mais : **combien de résidents marchent plus de 300 m pour atteindre un parc ≥ 1 ha** ?

Le pipeline calcule la distance de chaque bâtiment résidentiel au parc le plus proche, puis classe le résultat contre trois seuils institutionnels (OMS 300 m, SCoT IdF 600 m, ADEME 1000 m). Résultat : **une choroplèthe bâtiments** directement lisible — vert = bien desservi, rouge = carence.

::: info Chiffres-cles (BD TOPO Versailles, commune entière)
**509 zones** de végétation → **92 parcs ≥ 1 ha** (dont Forêt de Fausses-Reposes 411 ha) → **7 709 bâtiments résidentiels** mesurés → **59,8 % à < 300 m** d'un parc, **0 % en carence** (>1000 m, distance max mesurée 768 m).

Versailles est structurellement bien dotée — les forêts de Fausses-Reposes, Versailles et Marly saturent la périphérie. Le même pipeline sur une ville en déficit (Pantin, Aubervilliers…) ferait apparaître la classe *Carence*.
:::

## Données IGN BD TOPO V3

| Couche | Contenu | Features (commune) | Champs utilisés | Source |
|--------|---------|--------------------|-----------------|--------|
| `vegetation` | Zones de végétation | 509 | `nature`, `cleabs` | [data.geopf.fr](https://data.geopf.fr) — BDTOPO_V3:zone_de_vegetation |
| `batiments` | Empreintes bâti | 9 741 (dont 7 709 résidentiels) | `usage_1`, `hauteur` | [data.geopf.fr](https://data.geopf.fr) — BDTOPO_V3:batiment |

```bash
python examples/prepare_playground_data.py --city versailles
gispulse info examples/datasets/versailles_bdtopo.gpkg --layer batiments
```

## Pipeline (5 étapes, 2 branches)

```
vegetation ──► area_length → area_m2                (crs_meters = EPSG:2154)
                  │
                  ▼
              filter (area_m2 >= 10000)               # parcs ≥ 1 ha (SCoT IdF)
                  │                                     → 92 parcs
                  ▼
               parks_1ha  ───────────────────┐
                                             │  ref_layer
batiments  ──► filter (usage_1 == 'Résidentiel')
                  │                           │       → 7 709 résidentiels
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

::: tip Pourquoi `nearest_neighbor` et pas `spatial_join` ?
`spatial_join` répond à "ce bâtiment *intersecte*-t-il un parc" — trop strict ici, aucun bâtiment ne recouvre un parc. `nearest_neighbor` répond à "à quelle distance est le parc le plus proche" — exactement la question d'accessibilité. Il reprojette internement en `crs_meters` (Lambert93) pour que la distance soit en mètres physiques, pas en degrés.
:::

::: details Pourquoi ces seuils (tous urbanisme) ?
| Seuil | Source | Signification |
|-------|--------|---------------|
| **1 ha** (filtre végétation) | SCoT Île-de-France, *grands espaces verts* | En-dessous c'est une haie ou un bosquet — aucun rôle de parc |
| **300 m** | OMS — *minimum walking distance to urban green space* | Standard santé publique européen (≈ 4 min à pied) |
| **600 m** | SCoT IdF — *accessibilité piétonne acceptable* | ≈ 8 min à pied, seuil usuel de marchabilité |
| **1000 m** | ADEME — *au-delà, mode motorisé requis* | Définit la vraie *carence* d'espace vert de proximité |

Les breaks sont **manuels** (pas de quantile, pas de Jenks) — sinon les seuils "dériveraient" d'une ville à l'autre et le résultat perdrait toute interprétation institutionnelle. Le critère doit être absolu, pas relatif.
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

::: tip Télécharger
[scenario-5-rules.json](./scenario-5-rules.json) — cron intégré à `rules.triggers`, pas de fichier trigger séparé.
:::

## Trigger hebdomadaire (intégré au pipeline)

La section `triggers` porte `0 6 * * 1` (tous les lundis 6h00, Europe/Paris) avec action `run_pipeline`. L'ordonnanceur GISPulse rejoue la séquence complète à la prochaine mise à jour de la BD TOPO — la distance est recalculée pour chaque bâtiment, les nouvelles constructions apparaissent dans leur bonne classe sans intervention.

## Exécution

```bash
gispulse run examples/datasets/versailles_bdtopo.gpkg \
  --rules playground/scenario-5-rules.json \
  -o output/park_access.gpkg

gispulse serve output/park_access.gpkg
```

## Résultat attendu

::: details Schéma de sortie (bâtiments résidentiels)
| Colonne | Type | Origine | Description |
|---------|------|---------|-------------|
| `geometry` | MultiPolygon | source | Empreinte bâtiment BD TOPO |
| `usage_1` | string | source | Toujours `"Résidentiel"` (filtré step 3) |
| `hauteur` | float | source | Hauteur IGN (m) |
| `cleabs` | string | step 4 (`nearest_neighbor`) | Identifiant IGN du parc le plus proche |
| `nature` | string | step 4 | Type BD TOPO du parc le plus proche |
| `area_m2` | float | step 4 | Surface du parc le plus proche (m²) |
| `park_distance_m` | float | step 4 | Distance au parc le plus proche (m, Lambert93) |
| `access_class` | int (1..4) | step 5 (`classify`) | 1=Excellent, 2=Correct, 3=Éloigné, 4=Carence |
| `access_color` | string | step 5 | Hex palette `#1a9850` → `#d7191c` |
:::

::: info Classes d'accessibilité (Versailles, après filtres)
| Classe | Couleur | Intervalle | Part | Interprétation urbanisme |
|--------|---------|-----------|------|--------------------------|
| **Excellent** | <span style="color:#1a9850">■</span> `#1a9850` | < 300 m | **59,8 %** | Standard OMS atteint |
| **Correct** | <span style="color:#a6d96a">■</span> `#a6d96a` | 300–600 m | **32,8 %** | Marchable SCoT IdF |
| **Éloigné** | <span style="color:#fdae61">■</span> `#fdae61` | 600–1000 m | **7,4 %** | Limite piétonne ADEME |
| **Carence** | <span style="color:#d7191c">■</span> `#d7191c` | > 1000 m | **0 %** | Mode motorisé requis |

La classe *Carence* est vide sur Versailles — c'est une information en soi. Le même pipeline sur une ville dense sans forêt périphérique (Pantin, Aubervilliers, Bagnolet) ferait apparaître des zones rouges.
:::

## Playground interactif complet

Pipeline live à 5 étapes, 2 branches (végétation + bâtiments), distance Lambert93 et choroplèthe manuelle (nécessite le backend demo).

<ClientOnly>
  <DualMapView scenario="green-spaces" :showPipeline="true" :showTriggers="true" />
</ClientOnly>

**Préparation des références**

1. **`compute_veg_area`** <span style="color: var(--gp-orange)">(orange)</span> — surface Lambert93 (EPSG:2154) → `area_m2`.
2. **`filter_parks_1ha`** <span style="color: var(--gp-orange)">(orange)</span> — garde les zones ≥ 10 000 m² → **92 parcs** servent de référence.
3. **`filter_residential`** <span style="color: var(--gp-orange)">(orange)</span> — filtre `usage_1 == 'Résidentiel'` sur bâtiments → **7 709 features**.

**Jointure spatiale + classification**

4. **`nearest_park`** <span style="color: var(--gp-violet)">(violet)</span> — `nearest_neighbor` k=1 : pour chaque résidentiel, distance au parc ≥ 1 ha le plus proche (en mètres) + jointure de `cleabs`, `nature`, `area_m2` du parc.
5. **`classify_access`** <span style="color: var(--gp-red)">(rouge)</span> — `classify` manual breaks [0, 300, 600, 1000, ∞] + palette RdYlGn inversée → `access_color` par bâtiment.

Popup d'un bâtiment résidentiel : `hauteur`, `park_distance_m`, `access_class`, `nature` et `area_m2` du parc le plus proche.

## Essayer en live

<TryItLive endpoint="/capabilities" description="liste les capabilities du backend demo (area_length, filter, nearest_neighbor, classify utilisées par ce pipeline)" />

<TryItLive endpoint="/datasets" description="liste les datasets demo, dont versailles_bdtopo avec les couches vegetation et batiments chargées pour ce scénario" />

<TryItLive endpoint="/health" description="Etat du backend demo GISPulse." />

## Pour aller plus loin

- [S3 : Accessibilité santé par isochrones](/playground/road-buffer-poi) — même question d'accès à une aménité, mais via isochrones réseau (pas de distance euclidienne).
- [S6 : Carte du prix au m² (DVF)](/playground/real-estate) — autre choroplèthe Versailles, sur les mutations foncières.
- [Capabilities vecteur](/guide/capabilities#vector) — `filter`, `area_length`, `nearest_neighbor`, `classify`.

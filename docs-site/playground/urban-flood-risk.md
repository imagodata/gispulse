# S1 : Diagnostic Risque Inondation — Toulouse

<span class="gp-difficulty-badge" style="background: var(--gp-orange)">Intermediaire</span> <span class="gp-mode-badge">CLI + Map</span>

**Capabilities** : `filter`

## Cas d'usage

Un urbaniste doit produire un diagnostic du risque inondation le long de la Garonne a Toulouse : isoler les batiments bas (<= 15 m) situes dans le corridor 250 m de la Garonne ET dont le sol est 0-15 m au-dessus du fil de l'eau. Le tout en un seul pipeline GISPulse.

## Donnees IGN BD TOPO V3

| Couche | Contenu | Features | Source |
|--------|---------|----------|--------|
| `batiments` | Batiments IGN (hauteur, etages, logements, usage) | ~31 000 | [data.geopf.fr](https://data.geopf.fr) — BDTOPO_V3:batiment |
| `surfaces_eau` | Surfaces hydrographiques (Garonne, canaux, bassins) | 43 | [data.geopf.fr](https://data.geopf.fr) — BDTOPO_V3:surface_hydrographique |
| `cours_eau` | Cours d'eau lineaires (Garonne, canaux, affluents) | 14 | [data.geopf.fr](https://data.geopf.fr) — BDTOPO_V3:cours_d_eau |

```bash
# Telecharger les donnees BD TOPO Toulouse
python examples/prepare_playground_data.py --city toulouse

# Inspecter le dataset
gispulse info examples/datasets/toulouse_bdtopo.gpkg
```

## Pipeline (4 etapes)

```
cours_eau ──► filter_hydro (toponyme in ['la Garonne','Bras Inferieur Garonne'])   # restreint les cours d'eau au seul corridor Garonne

batiments ──► filter_in_flood_zone (intersects filter_hydro, buffer 250 m, L93)    # corridor 250 m
                │
                ▼
            filter_in_flood_altitude (altitude_minimale_sol in [134, 149] m IGN69) # sol 0-15 m au-dessus du niveau Garonne
                │
                ▼
            filter_low_buildings (hauteur in ]0, 15] m)                            # bati bas/moyen, plus vulnerable
```

::: warning CRS metriques
Les donnees IGN sont stockees en **EPSG:4326** (WGS84). Pour le buffer 250 m du
`filter_in_flood_zone`, on reprojette en **EPSG:2154 (Lambert93)** : le defaut
`EPSG:3857` (Web Mercator) deforme les distances de **~38 %** a la latitude de
Toulouse (facteur 1/cos(43.6°)).
:::

::: tip Pourquoi un step `filter_hydro` separe ?
`cours_eau` BD TOPO contient 14 lignes : la Garonne et son bras inferieur, mais aussi des
canaux d'adduction (Canal de Saint-Martory, Canal du Midi, Canal Lateral, Canal de Brienne)
et des affluents (Hers Mort, Girou, Sausse, Riou Gras) qui ne portent pas le meme risque de
crue. Un buffer 250 m autour de tout ca flagguait des batiments a l'ouest de la ville pres
d'un canal d'adduction (pas inondable). Le step `filter_hydro` restreint d'abord
`cours_eau` au seul corridor Garonne, puis `filter_in_flood_zone` utilise ce sous-ensemble
comme `ref_layer`.
:::

::: tip Altitude : pas besoin de DTM externe
BD TOPO V3 transporte deja la cote du sol par batiment :
`altitude_minimale_sol`, `altitude_maximale_sol`, `altitude_minimale_toit`,
`altitude_maximale_toit` (en metres IGN69). Pas besoin de plaquer un MNT (RGEALTI 1 m)
sur les empreintes pour cette couche.

**Reference Garonne a Toulouse** : ~134 m IGN69 a hauteur de Pont-Neuf. La crue
historique de 1875 a culmine vers 142 m (~8 m au-dessus du niveau normal). Le filtre
`altitude_minimale_sol BETWEEN 134 AND 149` retient les batiments dont le sol est
0-15 m au-dessus du fil de l'eau — borne haute volontairement large pour couvrir
une crue centennale + marge.

Pour un diagnostic plus fin (gradient amont/aval), remplacer la constante 134 par un
join spatial sur `surfaces_eau` Z ou un sample d'un raster MNT (capability `raster_sample`).
:::

::: tip Filtre hauteur batiment : pourquoi ≤ 15 m ?
Au-dela de 15 m (~R+4), les occupants disposent d'etages refuge ; les batiments
bas (R+0 a R+3) concentrent l'expo aux dommages humains et materiels en cas de crue.
Pour ne flagguer que la couche la plus vulnerable, on coupe au-dessus de 15 m.
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

::: tip Telecharger
[scenario-1-rules.json](/gispulse/playground/scenario-1-rules.json)
:::

## Execution

```bash
gispulse run examples/datasets/toulouse_bdtopo.gpkg \
  --layer batiments \
  --rules playground/scenario-1-rules.json \
  -o output/flood_diagnostic.gpkg \
  --ref-source cours_eau:examples/datasets/toulouse_bdtopo.gpkg:cours_eau

# Visualiser le resultat sur carte
gispulse serve output/flood_diagnostic.gpkg
```

## Resultat attendu

::: details Schema de sortie
| Colonne | Type | Origine | Description |
|---------|------|---------|-------------|
| `geometry` | MultiPolygon | source | Geometrie du batiment |
| `usage_1` | string | source | Usage principal (Residentiel, Indifferencie...) |
| `hauteur` | float | source | Hauteur du batiment (m) — filtree en ]0, 15] m |
| `altitude_minimale_sol` | float | source | Altitude min du sol (m IGN69) — filtree en [134, 149] m |
| `altitude_maximale_toit` | float | source | Altitude max du toit (m IGN69) |
| `nombre_d_etages` | int | source | Nombre d'etages |
| `nombre_de_logements` | int | source | Nombre de logements |
:::

Sur les ~31 000 batiments de Toulouse, le pipeline retient ceux qui cochent les trois criteres : (1) corridor 250 m de la Garonne, (2) sol 0-15 m au-dessus du niveau Garonne, (3) batiment bas/moyen (<= 15 m). C'est le sous-ensemble le plus expose a une crue majeure type 1875. Pour calculer l'emprise au sol, chainer une etape `area_length` (capability disponible) sur la sortie de `filter_low_buildings`.

## Playground interactif complet

Pipeline live en 4 etapes (necessite le backend demo) :

<ClientOnly>
  <DualMapView scenario="flood-risk" :showPipeline="true" :showTriggers="false" />
</ClientOnly>

**Etape par etape :**
1. `filter_hydro` (bleu) — restreint `cours_eau` au corridor Garonne
2. `filter_in_flood_zone` (orange) — batiments a 250 m du corridor, Lambert93
3. `filter_in_flood_altitude` (jaune) — sol entre 134 et 149 m IGN69
4. `filter_low_buildings` (rouge) — hauteur ≤ 15 m, cohorte la plus exposee

**Interactions :**
- Popup batiment : usage, hauteur, altitude sol, logements, nombre d'etages
- Chaque step colore la cohorte atteinte apres son filtre

## Essayer en live

<TryItLive endpoint="/health" description="Statut du serveur demo" />
<TryItLive endpoint="/datasets" description="Datasets disponibles" />

## Pour aller plus loin

- [S2 : Commerces / Axes Structurants](/playground/commercial-arterials) — meme ville, 2 etapes, pattern filtre + ref_filter
- [S6 : Densite Batie](/playground/real-estate) — analyser la densite batie a Versailles
- [Capabilities vecteur](/guide/capabilities#vecteur) — filter avec ref_layer, ref_filter, buffer_distance

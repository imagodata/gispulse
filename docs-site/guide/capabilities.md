---
title: Capabilities disponibles
description: Référence complète des 118 capabilities GISPulse — vecteur, attributs, validation, classification, statistiques spatiales, topologie, temporel, 3D pointcloud, raster, réseau et PostGIS SQL.
---

# Capabilities disponibles

Les capabilities sont les opérations unitaires que GISPulse peut appliquer sur des datasets spatiaux. Elles s'utilisent dans les règles JSON via le champ `capability`.

```bash
# Lister toutes les capabilities
gispulse capabilities
```

Chaque règle appelle une capability et lui passe un bloc `config`. Les paramètres ci-dessous correspondent aux clés de ce bloc.

```json
{
  "capability": "<name>",
  "ref_layer": "<optionnel>",
  "config": { "...": "..." }
}
```

::: tip Catalogue vivant
Le code fait foi. Pour le schéma exact d'une capability (paramètres, types, valeurs par défaut) :

```python
from capabilities.registry import REGISTRY
REGISTRY.get("buffer")().get_schema()
```
:::

**Nombre total de capabilities enregistrées : 117** — réparties en 18 catégories.

[[toc]]

---

## Vecteur — sélection & jointure (Community)

Disponibles dans tous les tiers. Les caps marquées `DuckDB` / `PostGIS` bénéficient du pattern Strategy multi-backend (dispatch automatique selon la taille).

### `buffer`

Buffer métrique autour des géométries. Reprojette automatiquement dans un CRS métrique si nécessaire.

**Moteurs :** Python (GeoPandas), DuckDB (`ST_Buffer`, > 50 000 features), PostGIS (server-side).

```json
{
  "capability": "buffer",
  "config": { "distance": 100, "crs_meters": "EPSG:3857" }
}
```

| Paramètre | Type | Défaut | Description |
|-----------|------|--------|-------------|
| `distance` | float | `0.0` | Distance en mètres |
| `crs_meters` | string | `EPSG:3857` | CRS de projection métrique intermédiaire |
| `cap_style` | string | `round` | `round`, `flat`, `square` |
| `join_style` | string | `round` | `round`, `mitre`, `bevel` |

### `filter`

Filtre les features selon une expression attributaire et/ou un prédicat spatial. Supporte les références cross-layer.

**Moteurs :** Python (`gdf.query` + Shapely), DuckDB (SQL), PostGIS.

```json
{
  "capability": "filter",
  "config": {
    "expression": "surface > 500 and usage == 'résidentiel'",
    "spatial_predicate": "intersects",
    "ref_layer": "zones_inondables",
    "buffer_distance": 50
  }
}
```

| Paramètre | Type | Description |
|-----------|------|-------------|
| `expression` | string | Expression Python évaluée sur les colonnes |
| `spatial_predicate` | string | `intersects`, `within`, `contains`, `crosses`, `overlaps`, `touches`, `dwithin` |
| `ref_layer` | string | Layer de référence pour le prédicat spatial |
| `ref_wkt` / `ref_geojson` | string / object | Géométrie WKT ou GeoJSON explicite |
| `buffer_distance` | float | Buffer en mètres appliqué à la géométrie de référence |

### `intersects`

Garde les features qui intersectent une géométrie ou layer de référence (raccourci de `filter` spatial).

```json
{ "capability": "intersects", "config": { "ref_layer": "zones_inondables" } }
```

### `clip`

Découpe les features selon l'emprise d'une layer de référence.

```json
{ "capability": "clip", "config": { "ref_layer": "zone_etude" } }
```

### `spatial_join`

Jointure spatiale entre la layer traitée et une layer de référence. Ajoute les attributs de la layer de référence sur les features correspondantes.

```json
{
  "capability": "spatial_join",
  "config": { "ref_layer": "communes", "how": "left", "op": "intersects" }
}
```

| Paramètre | Type | Défaut | Description |
|-----------|------|--------|-------------|
| `ref_layer` | string | *requis* | Layer de référence |
| `how` | string | `left` | `left`, `inner`, `right` |
| `op` | string | `intersects` | `intersects`, `contains`, `within` |

### `nearest_neighbor`

Joint les attributs des `k` features les plus proches d'une layer de référence, avec filtre optionnel `max_distance`.

```json
{
  "capability": "nearest_neighbor",
  "config": { "ref_layer": "ecoles", "k": 1, "max_distance": 2000, "distance_col": "dist_ecole_m" }
}
```

| Paramètre | Type | Défaut | Description |
|-----------|------|--------|-------------|
| `ref_layer` | string | *requis* | Layer de référence |
| `k` | int | `1` | Nombre de voisins |
| `max_distance` | float | *aucun* | Distance maximale en mètres |
| `distance_col` | string | `distance` | Nom de la colonne de distance ajoutée |

### `spatial_aggregate`

Agrège des valeurs d'une layer de référence par prédicat spatial (`count`, `sum`, `mean`, `min`, `max` par feature).

```json
{
  "capability": "spatial_aggregate",
  "config": {
    "ref_layer": "habitations",
    "aggregations": { "population": "sum" },
    "predicate": "contains"
  }
}
```

### `reproject`

Reprojection vers un CRS cible.

```json
{ "capability": "reproject", "config": { "target_crs": "EPSG:2154" } }
```

---

## Vecteur — overlay & combinaison (Community)

Combine plusieurs layers en une, soit géométriquement (overlay façon FME / QGIS) soit par concaténation.

| Capability | Description | Paramètres clés |
|-----------|-------------|-----------------|
| `overlay_intersection` | Intersection géométrique de deux layers — attributs hérités des deux côtés. | `ref_layer`, `keep_geom_type`, `suffix_left`, `suffix_right` |
| `overlay_union` | Union géométrique — garde les parts A-only, B-only, A∩B. | `ref_layer`, `keep_geom_type`, `suffix_left`, `suffix_right` |
| `erase` | Différence géométrique — supprime de A ce qui est couvert par B (attributs A préservés). | `ref_layer`, `keep_geom_type` |
| `merge_layers` | Concatène la layer primaire avec une liste de `ref_layers`. | `ref_layers` (array) |
| `classify_by_ring` | Classe chaque feature par le plus petit anneau qui la contient (isochrones, buffers concentriques) — ajoute `value` / `class` / `color`. | `ref_layers`, `ring_field`, `outside_value`, `palette` |

```json
{
  "capability": "overlay_intersection",
  "config": { "ref_layer": "zonage_plu", "suffix_right": "_plu" }
}
```

```json
{
  "capability": "classify_by_ring",
  "config": {
    "ref_layers": ["iso_500", "iso_1000", "iso_1500"],
    "ring_field": "cost_budget",
    "palette": "YlGnBu"
  }
}
```

---

## Vecteur — géométrie dérivée (Community)

| Capability | Description | Paramètres clés |
|-----------|-------------|-----------------|
| `centroid` | Remplace chaque géométrie par son centroïde. | — |
| `envelope` | Bounding box axis-aligned. | — |
| `oriented_bbox` | Rectangle minimum orienté (bâtiments, parcelles). | — |
| `min_bounding_circle` | Plus petit cercle englobant (Shapely ≥ 2.1). | — |
| `convex_hull` | Enveloppe convexe. Supporte `by_group` / `dissolve`. | `by_group`, `dissolve` |
| `concave_hull` | Enveloppe concave k-nearest. | `k`, `ratio` |
| `alpha_shape` | Alpha-shape (concave hull généralisé). | `alpha` |
| `delaunay_triangulation` | Triangulation de Delaunay des vertex. | — |
| `voronoi_polygons` | Diagramme de Voronoï des points. | `bounds` |
| `polygonize` | Construit des polygones depuis un réseau de lignes noded. | `snap_tolerance` |

```json
{ "capability": "convex_hull", "config": { "by_group": "commune" } }
```

---

## Vecteur — édition & métrologie (Community)

| Capability | Description | Paramètres clés |
|-----------|-------------|-----------------|
| `area_length` | Ajoute des colonnes `area_m2` / `length_m`. | `area_column`, `length_column` |
| `calculate` | Évalue une expression pour créer une nouvelle colonne (sum, avg, min, max, median, std, etc.). | `expression`, `output_field` |
| `dissolve` | Dissout les features par attribut (GROUP BY + ST_Union). | `by` |
| `union` | Fusionne toutes les features en une seule géométrie. | — |
| `symmetric_difference` | XOR entre chaque feature et l'union d'une layer de référence. | `ref_layer` |
| `vector_diff` | Compare deux layers par id et étiquette `added` / `removed` / `modified` / `unchanged`. | `ref_layer`, `id_col`, `geometry_tolerance` |
| `make_valid` | Répare les géométries invalides (auto-intersections, anneaux dupliqués). | — |
| `simplify` | Simplification (Douglas-Peucker, Visvalingam-Whyatt, topology-preserving). | `tolerance`, `algorithm` |
| `chaikin_smooth` | Lissage par coin-cutting itératif. | `iterations` |
| `densify_vertices` | Insère des vertex à intervalle régulier. | `max_distance`, `n_segments` |
| `snap_to_grid` | Aligne les coordonnées sur une grille. | `grid_size` |
| `offset_curve` | Ligne parallèle à distance signée. | `distance` |
| `line_merge` | Fusionne les segments adjacents. | — |
| `line_substring` | Sous-section d'une ligne entre deux mesures. | `start_measure`, `end_measure` |
| `line_locate_point` | Projette des points sur la ligne la plus proche et calcule la mesure. | `ref_layer` |
| `extract_vertices` | Extrait un `Point` par vertex avec index. | — |
| `extract_segments` | Découpe chaque ligne/bord en segments 2 points. | — |

```json
{ "capability": "simplify", "config": { "tolerance": 5.0, "algorithm": "vw" } }
```

```json
{
  "capability": "calculate",
  "config": { "output_field": "prix_m2", "expression": "prix / surface_m2" }
}
```

---

## Vecteur — manipulation d'attributs (Community)

Opérations non spatiales sur le schéma d'attributs : ajout, suppression, renommage, jointure, table de lookup, fallback null.

| Capability | Description | Paramètres clés |
|-----------|-------------|-----------------|
| `add_field` | Ajoute une ou plusieurs colonnes avec valeur par défaut. | `fields`, `overwrite` |
| `drop_field` | Supprime des colonnes (la géométrie est protégée). | `fields`, `ignore_missing` |
| `select_columns` | Conserve uniquement la liste de colonnes (géométrie toujours conservée). | `fields` |
| `rename_field` | Renomme via mapping `{old: new}`. | `mapping`, `ignore_missing` |
| `cast_field` | Cast de colonnes vers un dtype cible. | `casts`, `errors` |
| `attribute_join` | Jointure non-spatiale sur clé (left / right / inner / outer). | `ref_layer`, `left_on`, `right_on`, `how`, `columns`, `prefix`, `suffix` |
| `lookup_table` | Mappe une colonne via dict avec valeur par défaut. | `source_col`, `target_col`, `mapping`, `default` |
| `coalesce_fields` | Première valeur non-null parmi une liste de colonnes. | `sources`, `target_col` |
| `case_when` | Calcul conditionnel SQL CASE WHEN. | `target_col`, `cases`, `else_` |
| `describe` | Rapport d'introspection (dtype, nulls, unique, geom_type, bounds) — stocké dans `gdf.attrs["__schema_describe__"]`, layer renvoyé inchangé. | `sample_size`, `include_geometry` |

```json
{
  "capability": "case_when",
  "config": {
    "target_col": "tier",
    "cases": [
      { "when": "population > 100000", "value": "A" },
      { "when": "population > 10000", "value": "B" }
    ],
    "else_": "C"
  }
}
```

```json
{
  "capability": "attribute_join",
  "config": { "ref_layer": "communes_insee", "left_on": "code_insee", "how": "left" }
}
```

---

## Vecteur — pivot / unpivot (Community)

Reshape long ↔ wide façon pandas, géométrie préservée.

| Capability | Description | Paramètres clés |
|-----------|-------------|-----------------|
| `pivot` | Long → wide : `columns` éclate en colonnes, `values` peuple les cellules. | `index`, `columns`, `values`, `aggfunc`, `fill_value`, `geom_strategy` |
| `unpivot` | Wide → long : produit `(variable, value)` à partir de `value_vars`. | `id_vars`, `value_vars`, `var_name`, `value_name` |

```json
{
  "capability": "pivot",
  "config": {
    "index": "commune_id",
    "columns": "annee",
    "values": "population",
    "aggfunc": "sum",
    "geom_strategy": "first"
  }
}
```

`geom_strategy` (`first`, `union`, `centroid`) résout le conflit quand plusieurs lignes du même groupe ont des géométries différentes.

---

## Vecteur — sélection ordonnée & échantillonnage (Community)

Tri, dédup, échantillon aléatoire, top-N — opérations purement attributaires.

| Capability | Description | Paramètres clés |
|-----------|-------------|-----------------|
| `sort` | Trie par une ou plusieurs colonnes. | `by`, `ascending`, `na_position` |
| `deduplicate` | Supprime les doublons sur clé(s) attributaires (différent de `duplicate_geometry` qui dédup sur géométrie). | `keys`, `keep`, `order_by`, `ascending` |
| `random_sample` | Échantillon aléatoire (`n` ou `fraction`, `seed` reproductible). | `n`, `fraction`, `seed`, `replace` |
| `top_n` | Conserve les N premières features ordonnées par une colonne. | `n`, `by`, `ascending` |

```json
{ "capability": "top_n", "config": { "n": 100, "by": "rdm_m2", "ascending": false } }
```

---

## Vecteur — multipart & dimensions Z/M (Community)

Conversion multipart ↔ singlepart et gestion des dimensions Z (élévation) / M (mesure).

| Capability | Description | Paramètres clés |
|-----------|-------------|-----------------|
| `multipart_to_singleparts` | Éclate les `Multi*` — une ligne par part, attributs dupliqués. | `reset_index`, `drop_empty` |
| `singleparts_to_multipart` | Regroupe en `Multi*`, optionnellement par attribut. | `by`, `agg` |
| `add_z` | Ajoute Z (constante ou colonne `from_column`). | `z`, `from_column` |
| `drop_z` | Strip de la dimension Z → 2D. | — |
| `add_m` | Ajoute M (constante ou colonne). | `m`, `from_column` |
| `drop_m` | Strip de la dimension M. | — |

```json
{ "capability": "add_z", "config": { "from_column": "altitude_minimale_sol" } }
```

---

## Vecteur — transformations géométriques (Community)

Transformations affines, inversion d'axes, reverse de lignes — opérations rapides ne touchant pas le CRS.

| Capability | Description | Paramètres clés |
|-----------|-------------|-----------------|
| `affine_transform` | Translate / rotate / scale / skew (ordre déclaré, origine paramétrable). | `translate`, `rotate`, `scale`, `skew`, `origin` |
| `swap_xy` | Inverse X et Y de chaque vertex (utile pour fichiers lat/lon mal exportés). | — |
| `reverse_lines` | Inverse l'ordre des vertex pour `LineString` / `MultiLineString`. | `ignore_non_lines` |

```json
{
  "capability": "affine_transform",
  "config": { "rotate": 30, "origin": "centroid" }
}
```

---

## Vecteur — frontière & projection (Community)

Extraction de frontière, gestion de trous, coercition de type, déclaration de CRS sans transformation.

| Capability | Description | Paramètres clés |
|-----------|-------------|-----------------|
| `boundary` | Frontière topologique de chaque géométrie. | `drop_empty` |
| `extract_holes` | Extrait les anneaux intérieurs (trous) comme polygones. | `parent_id_col`, `hole_index_col` |
| `force_geometry_type` | Coerce vers un type cible (Multi → single via explode, etc.). | `target`, `on_multi`, `on_invalid` |
| `assign_projection` | Définit le CRS de la layer **sans** reprojection (utiliser `reproject` pour transformer). | `crs`, `allow_override` |

```json
{ "capability": "force_geometry_type", "config": { "target": "Polygon", "on_multi": "explode" } }
```

::: warning `assign_projection` ≠ `reproject`
`assign_projection` change l'étiquette CRS sans toucher aux coordonnées. À utiliser uniquement pour réparer une layer dont le CRS est faux ou absent.
:::

---

## Temporel (Community)

Filtres et jointures sur colonnes datetime.

| Capability | Description | Paramètres clés |
|-----------|-------------|-----------------|
| `temporal_filter` | Filtre par fenêtre `[start, end]` sur colonne datetime. Inversion possible. | `time_col`, `start`, `end`, `include_start`, `include_end`, `invert` |
| `temporal_join` | Jointure exacte ou as-of (`backward` / `forward` / `nearest`) avec tolérance. | `ref_layer`, `left_on`, `right_on`, `strategy`, `by`, `tolerance`, `columns` |

```json
{
  "capability": "temporal_join",
  "config": {
    "ref_layer": "meteo_horaire",
    "left_on": "ts_observation",
    "strategy": "nearest",
    "tolerance": "30min",
    "by": "station_id"
  }
}
```

---

## Validation (Community)

Contrôle qualité des données spatiales. Les caps de validation retournent une layer de **violations** (une ligne par problème détecté).

| Capability | Description |
|-----------|-------------|
| `topology_check` | Géométries invalides, auto-intersections, polygones qui se chevauchent. |
| `duplicate_geometry` | Géométries dupliquées exactes ou à tolérance. |
| `attribute_validation` | Types, nullabilité, plages, regex, unicité. |
| `completeness_check` | Ratio de valeurs nulles par colonne, couverture spatiale relative. |

```json
{
  "capability": "topology_check",
  "config": { "check_overlaps": true, "check_self_intersections": true }
}
```

```json
{
  "capability": "attribute_validation",
  "config": {
    "schema": [
      { "field": "population", "type": "integer", "min": 0 },
      { "field": "code_insee", "unique": true, "pattern": "^[0-9]{5}$" }
    ]
  }
}
```

---

## Classification & styling (Community)

Prépare les données pour la cartographie thématique : bucketing, rampe de couleurs, styles.

| Capability | Description | Paramètres clés |
|-----------|-------------|-----------------|
| `classify` | Bucket numérique en N classes (`quantile` / `equal_interval` / `manual` / `jenks` / `pretty` / `std_dev`). | `column`, `method`, `k`, `palette` |
| `classify_categorical` | Classification par valeurs uniques, avec bucket `other` optionnel. | `column`, `palette`, `other_threshold` |
| `head_tail_breaks` | Classification Head/Tail (Jiang 2013) — nb de classes auto. | `column` |
| `normalize` | Normalisation (`minmax` / `zscore` / `log` / `log1p` / `rank` / `percent`). | `column`, `method`, `denom` |
| `continuous_ramp` | Rampe de couleur continue sans classification. | `column`, `palette` |
| `graduated_size` | Taille de symbole proportionnelle à une colonne numérique. | `column`, `min_size`, `max_size` |
| `choropleth` | Classification + `LayerStyleDef` + légende (export QML/SLD). | `column`, `method`, `k`, `palette` |
| `bivariate_choropleth` | Choroplèthe bivariée (deux colonnes × grille de palette). | `column_x`, `column_y`, `n`, `palette` |

```json
{
  "capability": "choropleth",
  "config": {
    "column": "densite_pop",
    "method": "jenks",
    "k": 5,
    "palette": "YlOrRd"
  }
}
```

---

## Statistiques spatiales (Community)

Détection de clusters, d'autocorrélation et de points chauds / froids.

| Capability | Description | Paramètres clés |
|-----------|-------------|-----------------|
| `spatial_weights` | Calcule les poids spatiaux (queen / rook / k-NN / distance band) et ajoute `n_neighbours`. | `method`, `k`, `threshold` |
| `morans_i` | Moran's I global avec p-value par permutations. Retourne une ligne résumé. | `column`, `weights`, `n_permutations` |
| `getis_ord_g` | Getis-Ord Gi* z-score par feature (hot / cold spots). | `column`, `weights` |

```json
{
  "capability": "morans_i",
  "config": { "column": "rdm_m2", "weights": { "method": "queen" }, "n_permutations": 999 }
}
```

---

## Densité & tessellation (Community)

Construction de grilles et estimation de densité.

| Capability | Description | Paramètres clés |
|-----------|-------------|-----------------|
| `grid_create` | Grille régulière carrée sur un ref_layer ou bounds. | `cell_size`, `bounds`, `ref_layer` |
| `hexgrid_create` | Grille hexagonale flat-top. | `cell_size`, `bounds`, `ref_layer` |
| `kde_heatmap` | KDE sur grille régulière → points avec `density`. | `bandwidth`, `cell_size`, `bounds` |

```json
{
  "capability": "hexgrid_create",
  "config": { "cell_size": 500, "ref_layer": "zone_etude" }
}
```

---

## Clustering (Community — scikit-learn optionnel)

Installable via `pip install "gispulse[cluster]"`. Chaque cap ajoute une colonne `cluster` (-1 = noise pour DBSCAN / HDBSCAN).

| Capability | Description | Paramètres clés |
|-----------|-------------|-----------------|
| `cluster_kmeans` | K-Means sur les centroïdes. | `k`, `random_state` |
| `cluster_dbscan` | DBSCAN density-based. | `eps`, `min_samples` |
| `cluster_hdbscan` | HDBSCAN hiérarchique, densités variables. | `min_cluster_size`, `min_samples` |

```json
{ "capability": "cluster_dbscan", "config": { "eps": 200, "min_samples": 5 } }
```

---

## Topologie — lignes (Community)

Nettoie un réseau linéaire avant analyse réseau.

| Capability | Description | Paramètres clés |
|-----------|-------------|-----------------|
| `network_snap_endpoints` | Snap des endpoints proches. | `tolerance` |
| `network_extend_dangles` | Étend les endpoints dangling jusqu'à la ligne la plus proche. | `tolerance` |
| `network_node_lines` | Split de chaque ligne à chaque intersection → graphe planaire. | — |
| `network_remove_duplicates` | Supprime les doublons géométriques (direction ignorée). | `tolerance` |
| `network_remove_pseudo_nodes` | Fusionne les segments aux nœuds de degré 2. | — |

```json
{ "capability": "network_snap_endpoints", "config": { "tolerance": 0.5 } }
```

---

## Topologie — polygones (Community)

Répare une couverture polygonale (gaps, overlaps, slivers).

| Capability | Description | Paramètres clés |
|-----------|-------------|-----------------|
| `polygon_fix_gaps` | Détecte les trous sous `max_area` et les rattache au voisin partageant la plus grande frontière. | `max_area` |
| `polygon_fix_overlaps` | Supprime les chevauchements selon une règle (`smallest`, `largest`, `first`). | `rule` |
| `polygon_remove_slivers` | Supprime les polygones longs/fins (`min_area` ou `max_shape_index`). | `min_area`, `max_shape_index` |
| `polygon_snap_borders` | Snap des vertex sur grille → frontières adjacentes alignées. | `grid_size` |

```json
{ "capability": "polygon_fix_gaps", "config": { "max_area": 10.0 } }
```

---

## 3D Pointcloud (Community — `pip install "gispulse[pointcloud]"`)

Chargement et exploitation de nuages de points LAS / LAZ (ASPRS). Nécessite `laspy` (et `lazrs` pour LAZ).

| Capability | Description | Paramètres clés |
|-----------|-------------|-----------------|
| `pointcloud_load_las` | Charge un fichier `.las` / `.laz` en `GeoDataFrame` de `Point Z`. | `path`, `crs`, `max_points`, `classifications` |
| `pointcloud_filter_classification` | Filtre par codes de classification ASPRS (keep / drop). | `keep`, `drop`, `col` |
| `pointcloud_zonal_height` | Statistiques Z par polygone (hauteur de bâti, canopée). | `ref_layer`, `stats`, `prefix`, `ground_col` |
| `pointcloud_grid_summary` | Bin Z dans une grille régulière → polygones avec stats par cellule. | `cell_size`, `stats`, `drop_empty` |

```json
{
  "capability": "pointcloud_load_las",
  "config": {
    "path": "data/lidar/dalle_1234.laz",
    "crs": "EPSG:2154",
    "classifications": [2, 6]
  }
}
```

```json
{
  "capability": "pointcloud_zonal_height",
  "config": {
    "ref_layer": "lidar_points",
    "stats": ["max", "mean", "p95"],
    "ground_col": "altitude_minimale_sol",
    "prefix": "h_"
  }
}
```

::: tip Codes ASPRS courants
`2` = sol nu, `5` = haute végétation, `6` = bâtiment, `9` = eau. Voir la [spec LAS 1.4](https://www.asprs.org/wp-content/uploads/2010/12/LAS_1_4_r13.pdf).
:::

---

## Raster (Pro — `pip install "gispulse[raster]"`)

Nécessite `rasterio` + `rasterstats` et une licence Pro (`GISPULSE_TIER=pro`).

::: info Disponible en Pro
Les capabilities raster appellent `check_tier("pro")` à l'exécution.
:::

| Capability | Description | Paramètres clés |
|-----------|-------------|-----------------|
| `zonal_stats` | Statistiques raster (min/max/mean/std/sum/count) par polygone. | `raster_path`, `stats` |
| `raster_clip` | Découpe un raster par l'emprise d'un vecteur. | `raster_path`, `output_path` |
| `ndvi` | NDVI = (NIR − RED) / (NIR + RED) depuis un raster multi-bandes. | `raster_path`, `nir_band`, `red_band` |
| `raster_reproject` | Reprojection raster. | `raster_path`, `target_crs`, `output_path` |
| `raster_merge` | Fusionne plusieurs rasters. | `raster_paths`, `output_path` |
| `change_detection` | Détecte les zones changées entre deux rasters → polygones. | `raster_before`, `raster_after`, `threshold` |

```json
{
  "capability": "zonal_stats",
  "config": {
    "raster_path": "data/ndvi.tif",
    "stats": ["min", "max", "mean", "std", "sum", "count"]
  }
}
```

---

## Réseau — analyse (Pro — `pip install "gispulse[network]"`)

Nécessite `networkx` et une licence Pro.

| Capability | Description | Paramètres clés |
|-----------|-------------|-----------------|
| `shortest_path` | Plus court chemin (Dijkstra) entre deux points. | `origin`, `destination`, `weight_col` |
| `isochrone` | Zone atteignable dans un budget (distance / temps). | `origin`, `max_distance`, `weight_col` |
| `od_matrix` | Matrice origines × destinations en format long. | `origins_layer`, `destinations_layer`, `weight_col` |
| `mst` | Arbre couvrant minimum d'un réseau linéaire. | `weight_col` |
| `network_allocation` | Alloue points de demande ↔ offre les plus proches sur le réseau. | `supply_layer`, `weight_col`, `max_cost` |
| `connectivity_check` | Vérifie que le réseau forme un graphe connecté et retourne les composantes. | — |

```json
{
  "capability": "isochrone",
  "config": { "origin": [2.3522, 48.8566], "max_distance": 1000, "weight_col": "length_m" }
}
```

---

## PostGIS SQL (Pro)

### `postgis_sql`

Exécute une requête SQL paramétrée directement sur PostGIS pour les opérations avancées non disponibles ailleurs.

```json
{
  "capability": "postgis_sql",
  "config": {
    "sql": "SELECT *, ST_Area(geom::geography) AS area_geo FROM {input_table} WHERE ST_IsValid(geom)",
    "geom_col": "geom",
    "params": {}
  }
}
```

| Paramètre | Type | Description |
|-----------|------|-------------|
| `sql` | string | Requête avec `{input_table}` comme placeholder |
| `params` | object | Paramètres nommés (`$name`) pour la requête |
| `geom_col` | string | Colonne géométrie (défaut : `geom`) |
| `input_table` | string | Table d'entrée (auto-créée si absente) |

::: warning Sécurité
Le DSN n'est jamais lu depuis `config` — il est résolu depuis `GISPULSE_POSTGIS_DSN`. Les identifiants SQL sont validés (`operation_executor.py`). N'exposez pas cette capability à des utilisateurs non authentifiés.
:::

---

## Résumé par tier

| Catégorie | Caps | Tier | Dépendance optionnelle |
|-----------|------|------|------------------------|
| Vecteur — sélection & jointure | 8 | Community | — |
| Vecteur — overlay & combinaison | 5 | Community | — |
| Vecteur — géométrie dérivée | 10 | Community | — |
| Vecteur — édition & métrologie | 17 | Community | — |
| Vecteur — manipulation d'attributs | 9 | Community | — |
| Vecteur — pivot / unpivot | 2 | Community | — |
| Vecteur — sélection ordonnée & échantillonnage | 4 | Community | — |
| Vecteur — multipart & dimensions Z/M | 6 | Community | — |
| Vecteur — transformations géométriques | 3 | Community | — |
| Vecteur — frontière & projection | 4 | Community | — |
| Temporel | 2 | Community | — |
| Validation | 4 | Community | — |
| Classification & styling | 8 | Community | `mapclassify` (pour jenks) |
| Statistiques spatiales | 3 | Community | — |
| Densité & tessellation | 3 | Community | — |
| Clustering | 3 | Community | `gispulse[cluster]` (`scikit-learn`, `hdbscan`) |
| Topologie — lignes | 5 | Community | — |
| Topologie — polygones | 4 | Community | — |
| 3D Pointcloud | 4 | Community | `gispulse[pointcloud]` (`laspy`, `lazrs`) |
| Raster | 6 | **Pro** | `gispulse[raster]` (`rasterio`, `rasterstats`) |
| Réseau — analyse | 6 | **Pro** | `gispulse[network]` (`networkx`) |
| PostGIS SQL | 1 | **Pro** | `gispulse[postgis]` + DSN |
| **Total** | **117** | | |

---

## Créer une capability personnalisée

Voir [Développer un plugin](/plugins/developing) pour la publication via entry-points.

```python
from capabilities.base import Capability
from capabilities.registry import register

@register
class MaCapabilite(Capability):
    name = "ma_cap"
    description = "Description courte de ma capability."

    def execute(self, gdf, mon_parametre=1.0, **_):
        # logique métier ici
        return gdf

    def get_schema(self):
        return {
            "type": "object",
            "properties": {
                "mon_parametre": {"type": "number", "default": 1.0}
            },
            "required": ["mon_parametre"],
        }
```

Une capability distribuée en package peut s'auto-enregistrer via le groupe d'entry-points `gispulse.capabilities` (voir `pyproject.toml` dans [sdk/](https://github.com/sducournau/gispulse/tree/main/sdk)).

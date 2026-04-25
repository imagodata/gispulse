/**
 * Static overlay pulled from the precomputed bundle
 * (docs-site/public/playground/data/<slug>/<layer>.geojson.gz).
 * Used when a scenario needs a visual "zone" on top of backend-served layers
 * — e.g. the road-setback 50 m buffer that makes the DML trigger readable
 * before the user draws anything.
 */
export interface StaticOverlay {
  /** Slug under `public/playground/data/` — usually matches the scenario key. */
  staticSlug: string
  /** Layer name as declared in the manifest. */
  layer: string
  /** Key used in the store (defaults to `layer`). */
  storeName?: string
  color: string
  opacity?: number
  /**
   * Per-feature color attribute (e.g. `_style_color` for the road-setback
   * 5-ring overlay where each annulus is paint-encoded at build time). Falls
   * back to `color` for features missing the attribute.
   */
  colorField?: string
}

export interface ScenarioConfig {
  datasetName: string
  layers: string[]
  center: [number, number] // [lng, lat]
  zoom: number
  pipelineRules: string[]
  /**
   * Per-step input overrides for non-linear pipelines. Keys are rule/step ids,
   * values are either a dataset layer name, another step id, or the sentinel
   * "input" (primary layer). Steps absent from this map inherit the upstream
   * step's output (linear carry-over).
   */
  stepInputs?: Record<string, string>
  triggerNames: string[]
  bbox?: string
  description: string
  capabilities: string[]
  difficulty: 'debutant' | 'intermediaire' | 'avance'
  mode: 'CLI' | 'CLI + Map' | 'Docker' | 'Raster'
  drawMode?: 'none' | 'polygon' | 'point'
  drawAction?: string
  /** Layer that receives drawn features (defaults to `layers[0]`). */
  drawTargetLayer?: string
  /** Default properties stamped on drawn features — used to satisfy attribute
   *  predicates of the attached DML trigger. */
  drawProperties?: Record<string, unknown>
  /** Extra static overlays (precomputed zones, thematic masks) loaded on top
   *  of backend-served base layers. */
  staticOverlays?: StaticOverlay[]
  /**
   * Zone used for in-browser DML trigger evaluation. The drawn polygon's
   * centroid is tested against this overlay; a hit (plus matching
   * drawProperties) fires the trigger's action cascade. The overlay must be
   * one of `staticOverlays[].storeName` or `layer`.
   */
  triggerEvalZone?: string
  /**
   * When true, draws are handled client-side: the polygon is appended to a
   * local "drawn_batiments" layer (red inside `triggerEvalZone`, green
   * outside) and the trigger cascade is evaluated in-browser. No backend
   * round-trip, so the page stays usable whether or not the demo API is up.
   * Requires `triggerEvalZone`.
   */
  clientSideDraw?: boolean
  /**
   * Path to a static trigger definition (JSON from `public/playground/...`)
   * used as the source of truth for the in-browser evaluator when the live
   * backend does not expose the matching trigger. Loaded at scenario init
   * and pushed into the trigger list verbatim.
   */
  clientTriggerUrl?: string
}

export const scenarios: Record<string, ScenarioConfig> = {
  // S1 — Toulouse : Risque Inondation Garonne (BD TOPO)
  'flood-risk': {
    datasetName: 'toulouse_bdtopo',
    layers: ['batiments', 'surfaces_eau', 'cours_eau'],
    center: [1.445, 43.605],
    zoom: 14,
    bbox: '1.430,43.595,1.460,43.615',
    pipelineRules: [
      'filter_hydro',
      'filter_in_flood_zone',
      'filter_in_flood_altitude',
      'filter_low_buildings',
    ],
    stepInputs: {
      filter_hydro: 'cours_eau',
      filter_in_flood_zone: 'input',
      filter_in_flood_altitude: 'filter_in_flood_zone',
      filter_low_buildings: 'filter_in_flood_altitude',
    },
    triggerNames: [],
    description:
      'Diagnostic risque inondation Toulouse — corridor 250 m Garonne + altitude sol 134-149 m IGN69 + bati <=15 m',
    capabilities: [
      'filter',
    ],
    difficulty: 'intermediaire',
    mode: 'CLI + Map',
  },

  // S2 — Toulouse : Commerces le long des axes structurants (BD TOPO)
  'data-quality': {
    datasetName: 'toulouse_bdtopo',
    layers: ['batiments', 'routes'],
    center: [1.445, 43.605],
    zoom: 14,
    bbox: '1.430,43.595,1.460,43.615',
    pipelineRules: [
      'filter_routes_arterials',
      'filter_near_arterials',
      'filter_commercial',
    ],
    stepInputs: {
      filter_routes_arterials: 'routes',
      filter_near_arterials: 'input',
      filter_commercial: 'filter_near_arterials',
    },
    triggerNames: [],
    description:
      'Commerces Toulouse (usage_1 ou usage_2 == "Commercial et services") a moins de 50 m d\'un axe structurant IGN (importance 2, 3 ou 4 — nationales, departementales, voies principales) — 3 etapes : pre-filtre routes, buffer 50 m Lambert93, filtre attributaire commerces',
    capabilities: [
      'filter',
    ],
    difficulty: 'debutant',
    mode: 'CLI + Map',
  },

  // S3 — Clermont Auvergne Metropole : Accessibilite Urbaine (BD TOPO)
  'accessibility': {
    datasetName: 'clermont_ferrand_bdtopo',
    layers: ['equipements', 'routes', 'batiments'],
    center: [3.100, 45.785],
    zoom: 12,
    bbox: '3.020,45.740,3.180,45.830',
    pipelineRules: [
      'filter_sante',
      'isochrone_rings',
      'classify_by_ring',
    ],
    stepInputs: {
      classify_by_ring: 'batiments',
    },
    triggerNames: [],
    description:
      'Accessibilite Sante Clermont Auvergne Metropole — quatre anneaux isochrones concentriques (500 / 750 / 1000 / 1500 m a pied, ~5 / 7.5 / 10 / 15 min) emis en UN seul passage Dijkstra sur le reseau BD TOPO (CRS metrique EPSG:2154) via cost_budgets. classify_by_ring attribue ensuite a chaque batiment l\'anneau le plus interne qui le contient et le colore sur 5 bandes : vert = servi (500 m), jaune / orange / rouge pour les anneaux 750 / 1000 / 1500 m, rouge fonce au-dela de 1.5 km.',
    capabilities: [
      'filter',
      'isochrone',
      'classify_by_ring',
    ],
    difficulty: 'avance',
    mode: 'CLI + Map',
    drawMode: 'point',
    drawAction: 'Placez un point → calcul accessibilite en temps reel',
  },

  // S4 — Clermont Auvergne Metropole : Reseau Routier + recul urbanisme (BD TOPO)
  'road-setback': {
    datasetName: 'clermont_ferrand_bdtopo',
    layers: ['routes', 'batiments'],
    center: [3.100, 45.785],
    // zoom 12 (~6 km span): the importance 1-2 network is sparse, so we zoom
    // out to keep both the autoroute ring and the central nationale ribbons
    // visible without panning. At zoom 13 most of the inner city showed no
    // overlay because the structural axes hug the periphery.
    zoom: 12,
    bbox: '3.020,45.740,3.180,45.830',
    pipelineRules: [
      'filter_major_roads',
    ],
    triggerNames: ['alert_road_setback_violation'],
    description:
      'Reseau routier structurant Clermont Auvergne Metropole — filtrage importance 1-2 (autoroutes + nationales). Trigger DML : alerte recul < 250 m axe structurant a la creation d\'un bati residentiel, gradient 5 anneaux (rouge <= 200 m, orange 200-250 m, vert > 250 m). Dessin polygone OU point.',
    capabilities: [
      'filter',
    ],
    difficulty: 'debutant',
    mode: 'CLI + Map',
    drawMode: 'polygon',
    drawAction: 'Dessinez un batiment residentiel (polygone ou point) — rouge si <= 200 m d\'un axe (autoroute / nationale), orange entre 200 et 250 m, vert au-dela. Cascade du trigger DML declenchee dans la zone d\'alerte.',
    drawTargetLayer: 'batiments',
    // usage_1 = Residentiel satisfies the attr predicate of the DML trigger
    // (see scenario-4-trigger.json). Without this, a drawn polygon would
    // always miss the trigger regardless of its position.
    drawProperties: { usage_1: 'Residentiel' },
    staticOverlays: [
      {
        staticSlug: 'road-setback',
        layer: 'setback_zone',
        storeName: 'zone_recul_tiers',
        // Per-feature `_style_color` (deep red -> orange) is baked at build
        // time, one paint per annulus — see scripts/build_playground_data.py.
        color: '#E53935',
        opacity: 0.45,
        colorField: '_style_color',
      },
    ],
    triggerEvalZone: 'zone_recul_tiers',
    clientSideDraw: true,
    clientTriggerUrl: 'playground/scenario-4-trigger.json',
  },

  // S5 — Versailles : Accessibilité parcs par bâtiment — nearest_neighbor + classify manual
  'green-spaces': {
    datasetName: 'versailles_bdtopo',
    layers: ['vegetation', 'batiments'],
    center: [2.095, 48.82],
    zoom: 12,
    bbox: '1.960,48.770,2.170,48.870',
    pipelineRules: [
      'compute_veg_area',
      'filter_parks_1ha',
      'filter_residential',
      'nearest_park',
      'classify_access',
    ],
    stepInputs: {
      compute_veg_area: 'vegetation',
      filter_parks_1ha: 'compute_veg_area',
      filter_residential: 'batiments',
      nearest_park: 'filter_residential',
      classify_access: 'nearest_park',
    },
    triggerNames: [],
    description:
      "Accessibilité parcs à Versailles — calcule la surface des zones BD TOPO en Lambert93, garde les parcs ≥ 1 ha (seuil SCoT IdF), filtre les bâtiments résidentiels, mesure pour chacun la distance au parc le plus proche via nearest_neighbor (EPSG:2154) puis classe en 4 bandes urbanistiques (< 300 m OMS / < 600 m marche SCoT / < 1000 m ADEME / carence) avec palette RdYlGn inversée. Cron hebdomadaire (lundis 06:00) dans rules.triggers.",
    capabilities: [
      'area_length',
      'filter',
      'nearest_neighbor',
      'classify',
    ],
    difficulty: 'intermediaire',
    mode: 'CLI + Map',
  },

  // S6 — Versailles : Carte du prix au m² (DVF Etalab) + choroplèthe tuiles 100 m
  // Emprise alignée sur S5 green-spaces (Versailles élargi : ~21 × 11 km) afin
  // de couvrir l'ensemble du tissu urbain — Le Chesnay, Viroflay, Buc, Jouy.
  'real-estate': {
    datasetName: 'versailles_bdtopo',
    layers: ['dvf_ventes'],
    center: [2.095, 48.82],
    zoom: 12,
    bbox: '1.960,48.770,2.170,48.870',
    pipelineRules: [
      'filter_residential_sales',
      'compute_price_per_m2',
      'drop_price_outliers',
      'classify_price_quintiles',
      'create_price_grid',
      'aggregate_price_to_grid',
      'keep_cells_with_sales',
      'classify_grid_choropleth',
    ],
    stepInputs: {
      create_price_grid: 'drop_price_outliers',
      aggregate_price_to_grid: 'create_price_grid',
      keep_cells_with_sales: 'aggregate_price_to_grid',
      classify_grid_choropleth: 'keep_cells_with_sales',
    },
    triggerNames: [],
    description:
      'Carte du prix au m² Versailles élargi — DVF (Etalab) 2022-2024 sur le même cadre que S5 (Versailles + Le Chesnay + Viroflay + Buc + Jouy-en-Josas, ~21 × 11 km) : filtre ventes Maison/Appartement, price_per_m2 = valeur_fonciere / surface_reelle_bati, suppression des outliers 1500-25000 €, classification en quintiles avec palette YlOrRd. Puis quadrillage régulier 100 m × 100 m (grid_create, CRS Lambert93), agrégation du prix moyen / m² par tuile (spatial_aggregate, predicate=contains) et choroplèthe final en heatmap lisible.',
    capabilities: [
      'filter',
      'calculate',
      'classify',
      'grid_create',
      'spatial_aggregate',
    ],
    difficulty: 'intermediaire',
    mode: 'CLI + Map',
  },
}

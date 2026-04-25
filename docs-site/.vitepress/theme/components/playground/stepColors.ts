/** Shared capability → color mapping for pipeline steps.
 *  Used by PipelinePanel, DualMapView legend, and map layers.
 */
export const STEP_COLORS: Record<string, string> = {
  buffer: '#1E88E5',
  clip: '#E53935',
  spatial_join: '#43A047',
  filter: '#FB8C00',
  area_length: '#8E24AA',
  calculate: '#00ACC1',
  reproject: '#6D4C41',
  dissolve: '#FFB300',
  spatial_aggregate: '#5E35B1',
  topology_check: '#D81B60',
  intersects: '#00897B',
  duplicate_geometry: '#F06292',
  attribute_validation: '#26A69A',
  completeness_check: '#7E57C2',
  centroid: '#EF6C00',
  connectivity_check: '#5C6BC0',
  shortest_path: '#00838F',
  network_allocation: '#4E342E',
  isochrone: '#AD1457',
  union: '#558B2F',
  classify: '#C62828',
  classify_by_ring: '#1A9850',
  choropleth: '#AD1457',
  classify_categorical: '#D81B60',
  normalize: '#6A1B9A',
  head_tail_breaks: '#C62828',
  continuous_ramp: '#00695C',
  graduated_size: '#EF6C00',
  bivariate_choropleth: '#283593',
}

export function stepColor(capability: string): string {
  return STEP_COLORS[capability] || '#78909C'
}

/** Short human-friendly description per capability (FR). Shown in step preview. */
export const CAPABILITY_DESC: Record<string, { label: string; desc: string }> = {
  buffer: { label: 'Buffer', desc: 'Cree une zone tampon autour de chaque geometrie.' },
  clip: { label: 'Clip', desc: 'Decoupe les geometries selon une couche de reference.' },
  spatial_join: { label: 'Jointure spatiale', desc: 'Attache les attributs d\'une couche voisine par predicat spatial.' },
  filter: { label: 'Filtre', desc: 'Ne conserve que les features respectant une expression.' },
  area_length: { label: 'Aire / longueur', desc: 'Calcule aire ou longueur en unites metriques.' },
  calculate: { label: 'Calcul', desc: 'Ajoute une colonne derivee a partir d\'une expression.' },
  reproject: { label: 'Reprojection', desc: 'Change le systeme de coordonnees de la couche.' },
  dissolve: { label: 'Dissolve', desc: 'Fusionne les geometries partageant une meme valeur.' },
  spatial_aggregate: { label: 'Agregation spatiale', desc: 'Agrege des valeurs par zone (sum, mean, count...).' },
  topology_check: { label: 'Topologie', desc: 'Detecte les erreurs topologiques (auto-intersection...).' },
  intersects: { label: 'Intersection', desc: 'Filtre les features qui intersectent une couche.' },
  duplicate_geometry: { label: 'Doublons', desc: 'Detecte les geometries dupliquees.' },
  attribute_validation: { label: 'Validation attributs', desc: 'Verifie les contraintes metier sur les colonnes.' },
  completeness_check: { label: 'Completude', desc: 'Mesure le taux de remplissage des colonnes.' },
  centroid: { label: 'Centroide', desc: 'Remplace chaque geometrie par son centre.' },
  connectivity_check: { label: 'Connectivite', desc: 'Detecte les composantes connexes du reseau.' },
  shortest_path: { label: 'Plus court chemin', desc: 'Calcule le plus court chemin entre deux noeuds.' },
  network_allocation: { label: 'Allocation reseau', desc: 'Rattache des features au troncon le plus proche.' },
  isochrone: { label: 'Isochrone', desc: 'Zone accessible en X minutes depuis un point.' },
  union: { label: 'Union', desc: 'Fusionne toutes les geometries en une seule.' },
  classify: { label: 'Classification', desc: 'Decoupe une colonne numerique en N classes (quantile / interval / jenks / pretty / std_dev / manuel) avec palette.' },
  classify_by_ring: { label: 'Classification par anneau', desc: 'Attribue a chaque feature l\'anneau le plus interne qui le contient et applique la palette correspondante.' },
  choropleth: { label: 'Choroplethe', desc: 'Classification + palette + LayerStyleDef + legende — pret pour export QML/SLD.' },
  classify_categorical: { label: 'Categories', desc: 'Classification par valeurs uniques (string/discrete) avec palette qualitative.' },
  normalize: { label: 'Normalisation', desc: 'Normalise une colonne (minmax / zscore / log / log1p / rank / percent) avant classification.' },
  head_tail_breaks: { label: 'Head/Tail breaks', desc: 'Classification adaptee aux distributions heavy-tail (Jiang 2013) — nombre de classes determine par la donnee.' },
  continuous_ramp: { label: 'Gradient continu', desc: 'Interpolation de couleur per-feature sans classes (densites, rasters).' },
  graduated_size: { label: 'Taille graduee', desc: 'Mappe une colonne numerique vers une taille de symbole (points/lignes).' },
  bivariate_choropleth: { label: 'Choropleth bivariee', desc: 'Deux variables sur une grille de couleurs NxN (trade-offs, comparaisons).' },
}

export function capabilityInfo(capability: string): { label: string; desc: string } {
  return CAPABILITY_DESC[capability] || { label: capability, desc: 'Capability GISPulse.' }
}

/** Matches Python ViewerLayerSummary */
export interface LayerSummary {
  name: string;
  geometry_type: string | null;
  feature_count: number;
  bbox: [number, number, number, number];
  crs: string;
}

/** Matches Python LayerFieldInfo */
export interface LayerField {
  name: string;
  type: string;
}

/** Matches Python LayerDetailResponse */
export interface LayerDetail extends LayerSummary {
  fields: LayerField[];
}

/** Matches Python LayerListResponse */
export interface LayerListResponse {
  file: string;
  layers: LayerSummary[];
}

/** Standard GeoJSON types */
export interface GeoJSONFeature {
  type: "Feature";
  geometry: {
    type: string;
    coordinates: number[] | number[][] | number[][][] | number[][][][];
  };
  properties: Record<string, unknown>;
}

export interface GeoJSONFeatureCollection {
  type: "FeatureCollection";
  features: GeoJSONFeature[];
  total_count: number;
}

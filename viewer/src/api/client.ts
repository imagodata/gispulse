import type {
  LayerListResponse,
  LayerDetail,
  GeoJSONFeature,
  GeoJSONFeatureCollection,
} from "./types";

const BASE = "/v1/viewer";

async function fetchJSON<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) {
    throw new Error(`API error ${res.status}: ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

export async function fetchLayers(): Promise<LayerListResponse> {
  return fetchJSON<LayerListResponse>(`${BASE}/layers`);
}

export async function fetchLayerDetail(name: string): Promise<LayerDetail> {
  return fetchJSON<LayerDetail>(`${BASE}/layers/${encodeURIComponent(name)}`);
}

export async function fetchFeatures(
  name: string,
  opts?: {
    bbox?: [number, number, number, number];
    limit?: number;
    offset?: number;
    simplify?: number;
  }
): Promise<GeoJSONFeatureCollection> {
  const params = new URLSearchParams();
  if (opts?.bbox) params.set("bbox", opts.bbox.join(","));
  if (opts?.limit) params.set("limit", String(opts.limit));
  if (opts?.offset) params.set("offset", String(opts.offset));
  if (opts?.simplify) params.set("simplify", String(opts.simplify));

  const qs = params.toString();
  const url = `${BASE}/layers/${encodeURIComponent(name)}/features${qs ? `?${qs}` : ""}`;
  return fetchJSON<GeoJSONFeatureCollection>(url);
}

export async function fetchBbox(
  name: string
): Promise<{ bbox: [number, number, number, number] }> {
  return fetchJSON(`${BASE}/layers/${encodeURIComponent(name)}/bbox`);
}

// ---------------------------------------------------------------------------
// Feature editing (Phase 2)
// ---------------------------------------------------------------------------

export async function createFeature(
  layerName: string,
  geometry: GeoJSONFeature["geometry"],
  properties: Record<string, unknown> = {}
): Promise<{ fid: number; status: string }> {
  const res = await fetch(
    `${BASE}/layers/${encodeURIComponent(layerName)}/features`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ type: "Feature", geometry, properties }),
    }
  );
  if (!res.ok) throw new Error(`Create failed: ${res.status}`);
  return res.json();
}

export async function updateFeature(
  layerName: string,
  fid: number,
  update: {
    geometry?: GeoJSONFeature["geometry"];
    properties?: Record<string, unknown>;
  }
): Promise<{ fid: number; status: string }> {
  const res = await fetch(
    `${BASE}/layers/${encodeURIComponent(layerName)}/features/${fid}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(update),
    }
  );
  if (!res.ok) throw new Error(`Update failed: ${res.status}`);
  return res.json();
}

export async function deleteFeature(
  layerName: string,
  fid: number
): Promise<{ fid: number; status: string }> {
  const res = await fetch(
    `${BASE}/layers/${encodeURIComponent(layerName)}/features/${fid}`,
    { method: "DELETE" }
  );
  if (!res.ok) throw new Error(`Delete failed: ${res.status}`);
  return res.json();
}

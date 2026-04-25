/**
 * Load pre-computed playground datasets from the static build
 * (docs-site/public/playground/data/). Zero backend, gzip-aware,
 * freeze-safe (fetch + DecompressionStream in a single async call).
 *
 * Layout:
 *   public/playground/data/manifest.json
 *   public/playground/data/<scenario>/<layer>.geojson.gz
 */

import { withBase } from 'vitepress'

export interface LayerEntry {
  features: number
  size_bytes: number
  file: string | null
}

export interface ScenarioManifest {
  slug: string
  title: string
  center: [number, number]
  zoom: number
  bbox: [number, number, number, number]
  layers: Record<string, LayerEntry>
  total_size_bytes: number
}

export interface Manifest {
  generated_by: string
  crs: string
  scenarios: ScenarioManifest[]
}

const DATA_ROOT = 'playground/data'

let manifestPromise: Promise<Manifest> | null = null
const layerCache = new Map<string, Promise<any>>()

/** Fetch and decompress a possibly-gzipped GeoJSON file. */
async function fetchGeoJSON(url: string): Promise<any> {
  const res = await fetch(url)
  if (!res.ok) throw new Error(`[static-playground] ${res.status} on ${url}`)

  // Browsers serving .gz from GitHub Pages send raw bytes with no auto-decompress.
  if (url.endsWith('.gz') && 'DecompressionStream' in globalThis) {
    const ds = new (globalThis as any).DecompressionStream('gzip')
    const stream = res.body!.pipeThrough(ds)
    const text = await new Response(stream).text()
    return JSON.parse(text)
  }
  if (url.endsWith('.gz')) {
    // Fallback: no DecompressionStream (very old browser) — pull uncompressed sibling.
    const alt = url.slice(0, -3)
    const r2 = await fetch(alt)
    if (!r2.ok) throw new Error(`[static-playground] no fallback at ${alt}`)
    return r2.json()
  }
  return res.json()
}

export function useStaticPlayground() {
  async function loadManifest(): Promise<Manifest> {
    if (!manifestPromise) {
      manifestPromise = fetchGeoJSON(withBase(`/${DATA_ROOT}/manifest.json`))
    }
    return manifestPromise
  }

  async function loadScenario(slug: string): Promise<ScenarioManifest> {
    const m = await loadManifest()
    const sc = m.scenarios.find((s) => s.slug === slug)
    if (!sc) throw new Error(`[static-playground] unknown scenario "${slug}"`)
    return sc
  }

  async function loadLayer(slug: string, layerName: string): Promise<any> {
    const cacheKey = `${slug}::${layerName}`
    if (!layerCache.has(cacheKey)) {
      const promise = (async () => {
        const sc = await loadScenario(slug)
        const entry = sc.layers[layerName]
        if (!entry?.file) throw new Error(`[static-playground] no file for ${cacheKey}`)
        return fetchGeoJSON(withBase(`/${DATA_ROOT}/${entry.file}`))
      })()
      layerCache.set(cacheKey, promise)
    }
    return layerCache.get(cacheKey)!
  }

  /** Pre-load all layers of a scenario in parallel (used when the map mounts). */
  async function loadAllLayers(slug: string): Promise<Record<string, any>> {
    const sc = await loadScenario(slug)
    const names = Object.keys(sc.layers).filter((n) => sc.layers[n].file)
    const results = await Promise.all(names.map((n) => loadLayer(slug, n)))
    return Object.fromEntries(names.map((n, i) => [n, results[i]]))
  }

  return { loadManifest, loadScenario, loadLayer, loadAllLayers }
}

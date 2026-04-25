const DEFAULT_API = 'https://demo.gispulse.dev'
const API_KEY = 'demo-playground-key'

/** Hard cap per request. The demo API is fronted by Caddy + Cloud Run; cold
 *  starts can stretch past 5 s, so 8 s leaves slack without making the user
 *  stare at a frozen page on a real outage. */
const REQUEST_TIMEOUT_MS = 8000
/** Backoff before a single retry on transient network/timeout failures. */
const RETRY_DELAY_MS = 600

let _baseUrl = DEFAULT_API

export function setApiBase(url: string) {
  _baseUrl = url
}

function getBaseUrl(): string {
  return _baseUrl
}

/**
 * Fetch wrapper hardened for the public demo:
 *   - 8 s AbortController timeout — prevents a stalled `demo.gispulse.dev`
 *     from leaving the page on a never-resolving spinner.
 *   - 1 retry with backoff on network / timeout errors only. 4xx/5xx with a
 *     body resolve to a thrown "API NNN" — those are deterministic and not
 *     worth retrying.
 *   - Mutations (non-GET) are NEVER retried. Re-running an `executePipeline`
 *     or `createFeature` could double-execute a side-effect.
 */
async function apiFetch(path: string, opts: RequestInit = {}, retries = 1): Promise<any> {
  const base = getBaseUrl()
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)
  const isMutation = (opts.method || 'GET').toUpperCase() !== 'GET'

  try {
    const res = await fetch(`${base}${path}`, {
      ...opts,
      signal: controller.signal,
      headers: {
        'Content-Type': 'application/json',
        'X-API-Key': API_KEY,
        ...(opts.headers || {}),
      },
    })
    if (!res.ok) throw new Error(`API ${res.status}: ${res.statusText}`)
    return await res.json()
  } catch (e: any) {
    const isTransient = e?.name === 'AbortError' || e?.name === 'TypeError'
    if (retries > 0 && !isMutation && isTransient) {
      await new Promise((r) => setTimeout(r, RETRY_DELAY_MS))
      return apiFetch(path, opts, retries - 1)
    }
    throw e
  } finally {
    clearTimeout(timer)
  }
}

export function useGispulseApi() {
  return {
    getBaseUrl,

    async listDatasets() {
      const data = await apiFetch('/datasets')
      return data.items || data
    },

    async getDataset(id: string) {
      return apiFetch(`/datasets/${id}`)
    },

    async getFeatures(datasetId: string, layerName: string, opts?: {
      limit?: number
      simplify?: number
      bbox?: string
    }) {
      const params = new URLSearchParams()
      if (opts?.limit) params.set('limit', String(opts.limit))
      if (opts?.simplify) params.set('simplify', String(opts.simplify))
      if (opts?.bbox) params.set('bbox', opts.bbox)
      const qs = params.toString() ? `?${params}` : ''
      return apiFetch(`/api/portal/datasets/${datasetId}/layers/${layerName}/features${qs}`)
    },

    async createFeature(datasetId: string, layerName: string, geometry: any, properties?: Record<string, unknown>) {
      return apiFetch(`/api/portal/features/${layerName}`, {
        method: 'POST',
        body: JSON.stringify({ geometry, properties }),
      })
    },

    async listRules() {
      const data = await apiFetch('/rules')
      return data.items || data
    },

    async createJob(payload: { name: string; dataset_id: string; layer: string; rules: string[] }) {
      return apiFetch('/jobs', {
        method: 'POST',
        body: JSON.stringify({
          name: payload.name,
          dataset_id: payload.dataset_id,
          parameters: {
            rule_ids: payload.rules,
            layer: payload.layer,
          },
        }),
      })
    },

    async getJob(id: string) {
      return apiFetch(`/jobs/${id}`)
    },

    async pollJob(id: string, onProgress?: (job: any) => void, intervalMs = 2000, maxAttempts = 30): Promise<any> {
      for (let i = 0; i < maxAttempts; i++) {
        const job = await apiFetch(`/jobs/${id}`)
        onProgress?.(job)
        const s = (job.status || '').toUpperCase()
        if (s === 'COMPLETED' || s === 'FAILED') return job
        await new Promise(r => setTimeout(r, intervalMs))
      }
      throw new Error('Job polling timeout')
    },

    async listTriggers() {
      const data = await apiFetch('/triggers')
      return data.items || data
    },

    async listCapabilities() {
      return apiFetch('/capabilities')
    },

    async getJobFeatures(jobId: string, opts?: { limit?: number; simplify?: number }) {
      const params = new URLSearchParams()
      if (opts?.limit) params.set('limit', String(opts.limit))
      if (opts?.simplify) params.set('simplify', String(opts.simplify))
      const qs = params.toString() ? `?${params}` : ''
      return apiFetch(`/jobs/${jobId}/features${qs}`)
    },

    /**
     * Execute a pipeline and get GeoJSON for each intermediate step.
     * Single API call, no polling needed.
     */
    async executePipelineSteps(payload: {
      dataset_id: string
      layer: string
      steps: { id: string; capability: string; params: Record<string, any>; input?: string }[]
      ref_layers?: Record<string, string>
      simplify?: number
      limit?: number
    }) {
      const qs = new URLSearchParams()
      if (payload.simplify) qs.set('simplify', String(payload.simplify))
      if (payload.limit) qs.set('limit', String(payload.limit))
      const qsStr = qs.toString() ? `?${qs}` : ''
      return apiFetch(`/pipelines/execute-steps${qsStr}`, {
        method: 'POST',
        body: JSON.stringify({
          dataset_id: payload.dataset_id,
          layer: payload.layer,
          steps: payload.steps,
          ref_layers: payload.ref_layers || {},
        }),
      })
    },
  }
}

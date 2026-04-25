import { reactive, toRefs } from 'vue'

export interface LayerData {
  name: string
  geojson: any
  visible: boolean
  color: string
  type: 'fill' | 'line' | 'circle'
  opacity: number
  /**
   * If set, the map reads per-feature color from `feature.properties[colorField]`
   * instead of using the flat `color` string. Used by the `classify` capability
   * output (price-per-m² map, choropleths…). The `color` field is still used as
   * a fallback for features where the attribute is missing.
   */
  colorField?: string
}

export interface PlaygroundState {
  datasets: any[]
  activeDatasetId: string | null
  layers: Map<string, LayerData>
  selectedFeatureId: string | null
  selectedFeatureProps: Record<string, unknown> | null
  selectedLayerName: string | null

  // Pipeline
  rules: any[]
  selectedRuleIds: string[]
  jobStatus: 'idle' | 'running' | 'completed' | 'failed'
  jobMessage: string
  beforeSnapshot: Map<string, any> | null
  showingBefore: boolean

  // Triggers
  triggers: any[]
  firedResults: any[]
  /**
   * Outcome of the last client-side DML trigger evaluation.
   * - `null` when no draw has been evaluated yet,
   * - `true` when predicates matched (→ `firedResults` carries the cascade),
   * - `false` when they didn't (→ `firedResults` is empty, UI shows "compliant").
   */
  firedMatched: boolean | null
  firedSummary: string

  // Draw
  drawMode: 'none' | 'polygon' | 'point'
  drawnCoords: [number, number][]
}

const state = reactive<PlaygroundState>({
  datasets: [],
  activeDatasetId: null,
  layers: new Map(),
  selectedFeatureId: null,
  selectedFeatureProps: null,
  selectedLayerName: null,

  rules: [],
  selectedRuleIds: [],
  jobStatus: 'idle',
  jobMessage: '',
  beforeSnapshot: null,
  showingBefore: false,

  triggers: [],
  firedResults: [],
  firedMatched: null,
  firedSummary: '',

  drawMode: 'none',
  drawnCoords: [],
})

export function usePlaygroundStore() {
  /** Flush pending mutations — create new Map ref to trigger watchers */
  let _flushPending = 0
  function _flush() {
    if (!_flushPending) {
      _flushPending = requestAnimationFrame(() => {
        state.layers = new Map(state.layers)
        _flushPending = 0
      })
    }
  }
  /** Immediate flush (for operations that need sync reactivity) */
  function _flushNow() {
    if (_flushPending) { cancelAnimationFrame(_flushPending); _flushPending = 0 }
    state.layers = new Map(state.layers)
  }

  function setLayer(name: string, geojson: any, opts?: { color?: string; type?: LayerData['type']; opacity?: number; colorField?: string }) {
    const geomType = detectGeometryType(geojson)
    const existing = state.layers.get(name)
    state.layers.set(name, {
      name,
      geojson,
      visible: existing?.visible ?? true,
      color: opts?.color || existing?.color || autoColor(name),
      type: opts?.type || geomType,
      opacity: opts?.opacity ?? existing?.opacity ?? 1,
      colorField: opts?.colorField ?? existing?.colorField,
    })
    _flush()
  }

  function setLayerOpacity(name: string, opacity: number) {
    const layer = state.layers.get(name)
    if (layer && layer.opacity !== opacity) {
      layer.opacity = opacity
      // Don't flush immediately — caller may batch
    }
  }

  function clearLayers() {
    state.layers = new Map()
  }

  function selectFeature(id: string | null, props: Record<string, unknown> | null) {
    state.selectedFeatureId = id
    state.selectedLayerName = null
    state.selectedFeatureProps = props
  }

  function snapshotBefore() {
    // Shallow snapshot — only store geojson refs (they are immutable from API)
    state.beforeSnapshot = new Map()
    for (const [k, v] of state.layers) {
      state.beforeSnapshot.set(k, v.geojson)
    }
  }

  function toggleBeforeAfter() {
    state.showingBefore = !state.showingBefore
  }

  function resetDraw() {
    state.drawMode = 'none'
    state.drawnCoords = []
  }

  function toggleLayerVisibility(name: string) {
    const layer = state.layers.get(name)
    if (layer) {
      layer.visible = !layer.visible
      _flush()
    }
  }

  function setLayerVisibility(name: string, visible: boolean) {
    const layer = state.layers.get(name)
    if (layer && layer.visible !== visible) {
      layer.visible = visible
      // Don't flush immediately — caller may batch multiple calls
    }
  }

  /** Batch visibility changes: mutate in-place then flush once */
  function isolateLayer(name: string) {
    for (const [key, layer] of state.layers) {
      layer.visible = key === name
      if (key === name) layer.opacity = 1
    }
    _flushNow()
  }

  function showAllLayers() {
    for (const [, layer] of state.layers) {
      layer.visible = true
      layer.opacity = 1
    }
    _flushNow()
  }

  /** Flush any pending batched mutations (call after a series of setLayerVisibility) */
  function commitVisibility() {
    _flushNow()
  }

  function removePipelineLayers() {
    for (const key of [...state.layers.keys()]) {
      if (key.startsWith('step_')) {
        state.layers.delete(key)
      }
    }
    _flushNow()
  }

  /**
   * Wipe all per-scenario state so the next scenario starts from a blank slate.
   * Keeps the datasets directory (used to resolve ids) but clears layers,
   * pipeline results, snapshots, selection, draw mode and triggers. Callers
   * that switch scenarios MUST invoke this before loading the new config.
   */
  function reset() {
    if (_flushPending) { cancelAnimationFrame(_flushPending); _flushPending = 0 }
    state.layers = new Map()
    state.selectedFeatureId = null
    state.selectedFeatureProps = null
    state.selectedLayerName = null
    state.activeDatasetId = null
    state.selectedRuleIds = []
    state.jobStatus = 'idle'
    state.jobMessage = ''
    state.beforeSnapshot = null
    state.showingBefore = false
    state.triggers = []
    state.firedResults = []
    state.firedMatched = null
    state.firedSummary = ''
    state.drawMode = 'none'
    state.drawnCoords = []
    // Keep state.rules and state.datasets — global, reused across scenarios.
  }

  return {
    state,
    setLayer,
    clearLayers,
    selectFeature,
    snapshotBefore,
    toggleBeforeAfter,
    resetDraw,
    toggleLayerVisibility,
    setLayerVisibility,
    setLayerOpacity,
    isolateLayer,
    showAllLayers,
    commitVisibility,
    removePipelineLayers,
    reset,
  }
}

function detectGeometryType(geojson: any): LayerData['type'] {
  const features = geojson?.features || []
  if (!features.length) return 'fill'
  const t = features[0]?.geometry?.type || ''
  if (t.includes('Point')) return 'circle'
  if (t.includes('Line')) return 'line'
  return 'fill'
}

const COLORS = ['#2196F3', '#F44336', '#4CAF50', '#FF9800', '#9C27B0', '#00BCD4', '#795548', '#607D8B']
let colorIdx = 0

function autoColor(name: string): string {
  const map: Record<string, string> = {
    // Buildings — warm coral, visible on both light/dark basemaps
    batiments: '#E65100',
    // Water
    surfaces_eau: '#1565C0',
    cours_eau: '#2196F3',
    // Roads & transport
    routes: '#455A64',
    roads: '#607D8B',
    // Vegetation
    vegetation: '#2E7D32',
    parcelles_agricoles: '#8BC34A',
    // Points of interest
    equipements: '#7B1FA2',
    pois: '#4CAF50',
    schools: '#9C27B0',
    // Generic
    parcels: '#1976D2',
    flood_zones: '#F44336',
  }
  return map[name] || COLORS[colorIdx++ % COLORS.length]
}

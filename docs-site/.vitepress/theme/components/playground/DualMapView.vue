<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import { withBase } from 'vitepress'
import { useGispulseApi } from '../../composables/useGispulseApi'
import { usePlaygroundStore } from '../../composables/usePlaygroundStore'
import { useStaticPlayground } from '../../composables/useStaticPlayground'
import { scenarios, type ScenarioConfig } from './scenarioConfig'
import { polygonIntersectsGeoJSON } from './geo'
import type { DrawMeasure } from './geo'

/** Palette for client-side drawn polygons. Three tiers for the road-setback
 *  scenario (alert / warning / compliant) plus the legacy IN/OUT pair kept as
 *  fallback aliases for any future binary-zone scenario. Picked to read on
 *  top of the translucent ring overlay without bleeding into it. */
const DRAWN_COLOR_ALERT = '#7F0000'   // intersects the <=200 m alert zone
const DRAWN_COLOR_WARN = '#E65100'    // 200-250 m warning band
const DRAWN_COLOR_OK = '#2E7D32'      // > 250 m, compliant
const DRAWN_COLOR_IN = DRAWN_COLOR_ALERT
const DRAWN_COLOR_OUT = DRAWN_COLOR_OK
import PlaygroundMap from './PlaygroundMap.vue'
import DrawToolbar from './DrawToolbar.vue'
import PipelinePanel from './PipelinePanel.vue'
import TriggerPanel from './TriggerPanel.vue'
import { stepColor } from './stepColors'

const props = defineProps<{
  scenario: string
  showPipeline?: boolean
  showTriggers?: boolean
}>()

const api = useGispulseApi()
const staticApi = useStaticPlayground()
const store = usePlaygroundStore()
const maplibreRef = ref<any>(null)
const leafletRef = ref<any>(null)
const pipelineRef = ref<any>(null)
const layout = ref<"split" | "tabs">("split")
const activeTab = ref<'maplibre' | 'leaflet'>('maplibre')
const loading = ref(true)
const error = ref('')
const datasetId = ref('')

/**
 * Defer Leaflet mount so MapLibre wins LCP and the page becomes interactive
 * faster. Sticky once true (we never tear Leaflet down again, even on tab
 * switch back to MapLibre — the sync watch keeps both viewports aligned).
 *
 * - `tabs` layout: mounted as soon as the user opens the Leaflet tab.
 * - `split` layout: mounted after `requestIdleCallback` (~ MapLibre's first
 *   paint), so the side-by-side comparison still appears within ~1 s on a
 *   fresh load but doesn't fight MapLibre for main-thread time during the
 *   crucial first frames.
 */
const leafletMounted = ref(false)
function ensureLeaflet() {
  if (!leafletMounted.value) leafletMounted.value = true
}

/**
 * Latest scenario bbox from the static manifest. Stored at component scope so
 * a late-mounting Leaflet (idle callback or tab switch after scenario load)
 * can catch up to MapLibre's fitBounds without waiting for the next reload.
 */
const currentBbox = ref<[number, number, number, number] | null>(null)

// Drawing state surfaced to the toolbar. Mirrors whichever map is actively
// receiving clicks — the other map emits empty state, so "last non-zero wins"
// would get stale; we refresh on every emit (even 0) to stay in sync with the
// currently active canvas.
const drawVertexCount = ref(0)
const drawMeasure = ref<DrawMeasure | null>(null)

function onDrawState(state: { vertexCount: number; measure: DrawMeasure | null }) {
  drawVertexCount.value = state.vertexCount
  drawMeasure.value = state.measure
}

// Keyboard shortcuts are only meaningful while a draw mode is active. We
// register on `window` because the map canvas doesn't receive key events by
// default (no tabindex). Guarded by `drawMode !== 'none'` to stay out of the
// way when the user is just reading the page.
function onKeydown(e: KeyboardEvent) {
  if (store.state.drawMode === 'none') return
  const target = e.target as HTMLElement | null
  // Respect typing into inputs — never hijack keys while editing a form field.
  if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable)) {
    return
  }

  if (e.key === 'Escape') {
    store.state.drawMode = 'none'
    maplibreRef.value?.cancelDraw?.()
    leafletRef.value?.cancelDraw?.()
    e.preventDefault()
  } else if (e.key === 'Enter') {
    maplibreRef.value?.requestFinish?.()
    leafletRef.value?.requestFinish?.()
    e.preventDefault()
  } else if ((e.key === 'z' || e.key === 'Z') && !e.metaKey && !e.ctrlKey) {
    maplibreRef.value?.undoDrawPoint?.()
    leafletRef.value?.undoDrawPoint?.()
    e.preventDefault()
  }
}

function onToolbarUndo() {
  maplibreRef.value?.undoDrawPoint?.()
  leafletRef.value?.undoDrawPoint?.()
}

function onToolbarFinish() {
  maplibreRef.value?.requestFinish?.()
  leafletRef.value?.requestFinish?.()
}

onMounted(() => window.addEventListener('keydown', onKeydown))
onUnmounted(() => window.removeEventListener('keydown', onKeydown))

// Lazy-mount Leaflet after MapLibre's first paint in split layout.
// `requestIdleCallback` falls back to a 1 s timer on Safari (no native impl).
let leafletIdleHandle: number | null = null
function scheduleLeafletMount() {
  if (leafletMounted.value || layout.value !== 'split') return
  const ric = (globalThis as any).requestIdleCallback as
    | ((cb: () => void, opts?: { timeout: number }) => number)
    | undefined
  if (ric) {
    leafletIdleHandle = ric(() => ensureLeaflet(), { timeout: 1500 })
  } else {
    leafletIdleHandle = window.setTimeout(ensureLeaflet, 1000)
  }
}
onMounted(scheduleLeafletMount)
onUnmounted(() => {
  if (leafletIdleHandle === null) return
  const cic = (globalThis as any).cancelIdleCallback as ((h: number) => void) | undefined
  if (cic) cic(leafletIdleHandle)
  else window.clearTimeout(leafletIdleHandle)
})

// Tab switch / layout change → mount Leaflet immediately so the user never
// waits on the idle scheduler when they explicitly ask to see it.
watch(activeTab, (tab) => { if (tab === 'leaflet') ensureLeaflet() })
watch(layout, (l) => { if (l === 'split') ensureLeaflet() })

// When Leaflet mounts AFTER the scenario was already loaded (idle callback
// or tab switch on a populated page), it would otherwise sit on the config
// center/zoom and wait for the user to pan MapLibre before catching up.
// Re-apply the cached bbox once its ref is wired.
watch(leafletMounted, async (mounted) => {
  if (!mounted) return
  await nextTick()
  if (currentBbox.value) leafletRef.value?.fitBounds?.(currentBbox.value)
})

const config = computed<ScenarioConfig | undefined>(() => scenarios[props.scenario])

const displayedLayers = computed(() => {
  if (store.state.showingBefore && store.state.beforeSnapshot) {
    const before = new Map<string, any>()
    for (const [k, v] of store.state.layers) {
      // Hide intermediate step layers in "Before" mode — they didn't exist yet
      if (k.startsWith('step_')) continue
      const snap = store.state.beforeSnapshot.get(k)
      if (snap) {
        before.set(k, { ...v, geojson: snap })
      } else {
        before.set(k, v)
      }
    }
    return before
  }
  return store.state.layers
})

watch(activeTab, async () => {
  await nextTick()
  if (activeTab.value === "leaflet") {
    leafletRef.value?.invalidateSize()
  }
})

/**
 * Load the scenario pointed to by `config`: resolve the dataset id, fetch the
 * configured base layers, then rules and (optionally) triggers. Safe to call
 * repeatedly; callers must invoke `resetView()` first when switching scenarios.
 *
 * Strategy: static-first. Every scenario ships a precomputed bundle under
 * `public/playground/data/<slug>/` that is faster (~50 ms gzip-decompress vs
 * ~500-3000 ms API round-trip + simplify), works offline, and survives demo
 * API outages. The live API is still queried for rules / pipeline / triggers
 * because those mutate / compute server-side. If the static bundle is missing
 * or partial, we fall back to fetching base layers from the live API.
 */
async function loadScenario() {
  const cfg = config.value
  if (!cfg) {
    error.value = `Unknown scenario: ${props.scenario}`
    loading.value = false
    return
  }

  loading.value = true
  error.value = ''

  try {
    const slug = props.scenario

    // 1. Try the static bundle first for base layers — fast and offline-safe.
    let staticOk = false
    let manifestBbox: [number, number, number, number] | null = null
    try {
      const sm = await staticApi.loadScenario(slug)
      manifestBbox = (sm.bbox?.length === 4 ? sm.bbox : null) as
        | [number, number, number, number]
        | null
      const wanted = cfg.layers.filter((n) => sm.layers[n]?.file)
      if (wanted.length === cfg.layers.length) {
        const geos = await Promise.all(wanted.map((n) => staticApi.loadLayer(slug, n)))
        // Insert in reverse scenario order so the first-listed layer paints on top.
        for (let i = wanted.length - 1; i >= 0; i--) {
          store.setLayer(wanted[i], geos[i])
        }
        staticOk = true
      }
    } catch {
      // No bundle for this scenario — silent fall-through to live API.
    }

    // 2. Resolve datasetId for pipeline / mutations / triggers. This is the
    //    only call that *requires* the demo API to be reachable; if it fails
    //    while the static bundle succeeded, we still render base layers and
    //    only disable the pipeline button.
    const needLiveApi = props.showPipeline || (props.showTriggers && !cfg.clientTriggerUrl)
    let liveApiReachable = false
    try {
      const datasets = store.state.datasets.length
        ? store.state.datasets
        : await api.listDatasets()
      const matches = datasets.filter((d: any) => d.name === cfg.datasetName)
      const ds = matches.length > 1
        ? [...matches].sort((a, b) => String(b.created_at ?? '').localeCompare(String(a.created_at ?? '')))[0]
        : matches[0]
      if (ds) {
        datasetId.value = ds.id
        store.state.activeDatasetId = ds.id
        store.state.datasets = datasets
        liveApiReachable = true
      } else if (!staticOk) {
        error.value = `Dataset "${cfg.datasetName}" not found on demo server`
        loading.value = false
        return
      }
    } catch (e: any) {
      if (needLiveApi && !staticOk) {
        error.value = `API GISPulse injoignable et aucun bundle statique pour "${slug}": ${e.message}`
        loading.value = false
        return
      }
      // Static bundle saved us — pipeline panel will display its own error.
    }

    // 3. Fall back to the live API for base layers when the static bundle is
    //    missing or incomplete (one or more required layers absent).
    if (!staticOk) {
      if (!datasetId.value) {
        error.value = `Aucun bundle statique pour "${slug}" et dataset live indisponible`
        loading.value = false
        return
      }
      const geojsons = await Promise.all(
        cfg.layers.map(layerName =>
          api.getFeatures(datasetId.value, layerName, { limit: 100000, bbox: cfg.bbox, simplify: 0.00001 }),
        ),
      )
      for (let i = cfg.layers.length - 1; i >= 0; i--) {
        store.setLayer(cfg.layers[i], geojsons[i])
      }
    }

    if (liveApiReachable && !store.state.rules.length) store.state.rules = await api.listRules()
    if (props.showTriggers) {
      // When the scenario ships its own trigger JSON (e.g. road-setback, whose
      // S4 DML trigger isn't registered on the public demo API), use it as the
      // source of truth. Otherwise fall back to whatever the backend exposes.
      if (cfg.clientTriggerUrl) {
        try {
          const res = await fetch(withBase(`/${cfg.clientTriggerUrl}`))
          if (res.ok) {
            const trig = await res.json()
            store.state.triggers = [trig]
          } else if (liveApiReachable) {
            store.state.triggers = await api.listTriggers()
          }
        } catch (e) {
          console.warn('[playground] clientTriggerUrl load failed, falling back to API', e)
          if (liveApiReachable) store.state.triggers = await api.listTriggers()
        }
      } else if (liveApiReachable) {
        store.state.triggers = await api.listTriggers()
      }
    }

    // Static overlays — precomputed "zones" shipped with the docs bundle
    // (e.g. road-setback's 50 m buffer around importance 1-2 routes). These
    // sit on top of backend-served base layers and are the target of the
    // client-side DML evaluation.
    if (cfg.staticOverlays?.length) {
      await Promise.all(
        cfg.staticOverlays.map(async (ov) => {
          try {
            const gj = await staticApi.loadLayer(ov.staticSlug, ov.layer)
            const key = ov.storeName ?? ov.layer
            store.setLayer(key, gj, {
              color: ov.color,
              opacity: ov.opacity ?? 0.3,
              colorField: ov.colorField,
            })
          } catch (e) {
            // Non-fatal: page still works without the overlay.
            console.warn(`[playground] static overlay ${ov.staticSlug}/${ov.layer} failed`, e)
          }
        }),
      )
    }

    // Cadre la vue sur le bbox réel des données chargées plutôt que sur le
    // center/zoom du config — utile quand le bundle statique a été clippé sur
    // un bbox plus serré que celui de scenarioConfig.
    currentBbox.value = manifestBbox
    if (manifestBbox) {
      await nextTick()
      maplibreRef.value?.fitBounds?.(manifestBbox)
      leafletRef.value?.fitBounds?.(manifestBbox)
    }

    loading.value = false
  } catch (e: any) {
    error.value = e.message
    loading.value = false
  }
}

/** Tear down every per-scenario artefact before loading a new scenario. */
function resetView() {
  pipelineRef.value?.resetPipeline?.()
  maplibreRef.value?.clearCache?.()
  leafletRef.value?.clearCache?.()
  store.reset()
  datasetId.value = ''
}

// The playground store is a module-level singleton, so it survives when
// VitePress unmounts DualMapView on a route change. Reset on mount too,
// otherwise the previous scenario's base + step_* layers leak into the
// newly mounted scenario (setLayer only overwrites matching keys).
async function reload() {
  resetView()
  await nextTick()
  await loadScenario()
  if (props.showPipeline && config.value?.pipelineRules.length) {
    // Pre-execute the pipeline silently so the step layers are cached in the
    // browser before the user clicks Play. The panel keeps viewStepIndex at -1
    // (only base layers visible) until the user starts the guided tour, then
    // autoplay walks through pre-loaded results with no extra latency.
    await nextTick()
    pipelineRef.value?.runPipeline?.()
  }
}

onMounted(reload)
watch(() => props.scenario, reload)


async function handleFeatureDrawn(geometry: any) {
  const cfg = config.value
  if (!cfg) return

  const drawnProps: Record<string, unknown> = {
    name: 'Playground feature',
    source: 'playground-draw',
    ...(cfg.drawProperties ?? {}),
  }

  // Client-side path: used when the scenario flags `clientSideDraw` (e.g.
  // road-setback). The polygon is appended to a local layer styled red/green
  // by centroid-vs-zone membership; no backend mutation. This keeps the page
  // functional whether the demo API is up or not, and — more importantly —
  // gives an instant, readable visual signal of the trigger outcome.
  if (cfg.clientSideDraw) {
    if (props.showTriggers) evaluateDrawAgainstTriggers(geometry, cfg, drawnProps)
    appendDrawnFeature(geometry, cfg, drawnProps)
    store.resetDraw()
    return
  }

  // Backend path (legacy — scenarios that mutate a real dataset layer).
  if (!datasetId.value) return
  const targetLayer = cfg.drawTargetLayer ?? cfg.layers[0]

  try {
    await api.createFeature(datasetId.value, targetLayer, geometry, drawnProps)

    const geojson = await api.getFeatures(datasetId.value, targetLayer, {
      limit: 100000,
      bbox: cfg.bbox,
      simplify: 0.00001,
    })
    store.setLayer(targetLayer, geojson)
    store.resetDraw()

    if (props.showTriggers && store.state.triggers.length) {
      evaluateDrawAgainstTriggers(geometry, cfg, drawnProps)
    }
  } catch (e: any) {
    error.value = `Draw failed: ${e.message}`
  }
}

/**
 * Distance tier returned by the ring overlay for a drawn polygon.
 *
 * The road-setback overlay ships 5 concentric annuli tagged with `distance_m`
 * (50, 100, 150, 200, 250 m). We intersect the polygon against each ring and
 * keep the smallest distance whose ring overlaps — that is the closest road
 * the drawn footprint reaches. `null` means the polygon sits entirely beyond
 * the outermost ring (compliant). For legacy single-feature zones (no
 * `distance_m`), the helper falls back to a binary intersect and returns 0
 * (alert) or null (compliant).
 */
function closestRingDistance(geometry: any, zoneGeo: any): number | null {
  if (!geometry || !zoneGeo) return null
  const features: any[] = zoneGeo.features ?? []
  if (!features.length) return null
  let best: number | null = null
  let sawDistance = false
  for (const f of features) {
    const d = f?.properties?.distance_m
    if (typeof d === 'number') {
      sawDistance = true
      if (best !== null && d >= best) continue
      if (polygonIntersectsGeoJSON(geometry, { type: 'FeatureCollection', features: [f] })) {
        best = d
      }
    }
  }
  if (sawDistance) return best
  // Legacy single-feature zone (no per-feature distance_m): binary fallback.
  return polygonIntersectsGeoJSON(geometry, zoneGeo) ? 0 : null
}

/**
 * Append a user-drawn geometry (Point or Polygon) to the matching
 * `drawn_batiments_*` store layer with a per-feature `_style_color`.
 * Three tiers (road-setback):
 *   distance <= 200 m → dark red (alert)
 *   200 < distance <= 250 m → orange (warning)
 *   no ring intersected (or > 250 m) → green (compliant)
 *
 * Points and polygons go to separate store layers because the store infers
 * one render type per layer from the first feature — mixing types in a
 * single layer would silently drop one geometry kind. Naming polygons
 * `drawn_batiments_polys` reuses MapLibreMap's "building" fill-opacity path
 * (0.55 vs 0.35) so the shape reads over the translucent ring overlay.
 */
function appendDrawnFeature(
  geometry: any,
  cfg: ScenarioConfig,
  drawnProps: Record<string, unknown>,
) {
  const zoneName = cfg.triggerEvalZone
  const zoneLayer = zoneName ? store.state.layers.get(zoneName) : null
  const zoneGeo = zoneLayer?.geojson
  const distance = closestRingDistance(geometry, zoneGeo)

  let color: string
  let inZone: boolean
  if (distance === null) {
    color = DRAWN_COLOR_OK
    inZone = false
  } else if (distance <= 200) {
    color = DRAWN_COLOR_ALERT
    inZone = true
  } else {
    color = DRAWN_COLOR_WARN
    inZone = true
  }

  const isPoint = geometry?.type === 'Point' || geometry?.type === 'MultiPoint'
  const layerKey = isPoint ? 'drawn_batiments_pts' : 'drawn_batiments_polys'
  const existing = store.state.layers.get(layerKey)?.geojson
  const features: any[] = existing?.features ? [...existing.features] : []
  features.push({
    type: 'Feature',
    geometry,
    properties: {
      ...drawnProps,
      _style_color: color,
      _in_zone: inZone,
      _distance_tier_m: distance,
      _index: features.length + 1,
    },
  })
  const geojson = { type: 'FeatureCollection', features }
  store.setLayer(layerKey, geojson, {
    color: DRAWN_COLOR_ALERT,
    colorField: '_style_color',
    opacity: 1,
  })
}

/**
 * Client-side stand-in for the server's TriggerEvaluator: we can't send
 * geometry through `POST /triggers/{id}/evaluate` (ChangeRecordIn only
 * carries old_values/new_values, not WKT), so the demo runs the spatial +
 * attribute predicates locally against the pre-loaded zone.
 *
 * Logic for the road-setback 5-ring overlay:
 *   1. find the closest annulus the polygon intersects (50/100/.../250 m)
 *   2. distance <= 200 m  → ALERT tier, geom predicate OK (cascade fires)
 *      200 < distance <= 250 m → WARNING tier, geom predicate OK (cascade fires
 *           because the backend trigger is a single 250 m disc; the gradient
 *           is a UX signal of severity, not a separate rule)
 *      no intersection → geom predicate KO (compliant)
 *   3. drawnProps satisfies every `attr` predicate (AND) → attr OK
 *   4. geom + attr both required → fire cascade, else compliant
 *
 * Single zone, single trigger, AND logic. Enough for the S4 demo; richer
 * scenarios should move this to the backend once the API accepts geometry.
 */
function evaluateDrawAgainstTriggers(
  geometry: any,
  cfg: ScenarioConfig,
  drawnProps: Record<string, unknown>,
) {
  // The public demo API doesn't always expose the scenario-specific trigger
  // (e.g. road-setback ships its own JSON via `clientTriggerUrl`), and even
  // when it does the order of `listTriggers()` is not guaranteed. Pick by
  // name first — `triggers[0]` would otherwise run the wrong predicate set.
  const wantName = cfg.triggerNames[0]
  const trigger = wantName
    ? store.state.triggers.find((t: any) => t.name === wantName) ?? store.state.triggers[0]
    : store.state.triggers[0]
  if (!trigger) return
  const actions = (trigger.actions || []).map((a: any) => ({
    action_type: a.action_type,
    config: a.config,
  }))

  const zoneName = cfg.triggerEvalZone
  const zoneLayer = zoneName ? store.state.layers.get(zoneName) : null
  const zoneGeo = zoneLayer?.geojson
  const distance = closestRingDistance(geometry, zoneGeo)
  const inZone = distance !== null
  const tier: 'alert' | 'warn' | 'ok' =
    distance === null ? 'ok' : distance <= 200 ? 'alert' : 'warn'

  const attrPredicates = (trigger.predicates || []).filter((p: any) => p.type === 'attr')
  const attrOk = attrPredicates.every((p: any) => {
    const v = drawnProps[p.field]
    if (p.op === 'eq') return v === p.value
    if (p.op === 'neq') return v !== p.value
    if (p.op === 'in' && Array.isArray(p.value)) return p.value.includes(v)
    return false
  })

  const matched = !!(inZone && attrOk)
  store.state.firedMatched = matched
  store.state.firedResults = matched ? actions : []
  const tierLabel =
    tier === 'alert'
      ? `ALERTE recul <= 200 m (anneau ${distance} m)`
      : tier === 'warn'
        ? `WARNING bande 200-250 m (anneau ${distance} m)`
        : 'hors zone (> 250 m)'
  store.state.firedSummary = matched
    ? `MATCH : ${tierLabel} + ${attrPredicates.length} predicat(s) attributaire(s) OK`
    : inZone
      ? `NO MATCH : ${tierLabel}, predicats attributaires non satisfaits`
      : 'NO MATCH : polygone hors de la zone de recul (> 250 m)'
}

/** Key builder for step layers */
function stepKey(idx: number, name: string) {
  return `step_${idx}_${name}`
}

/** Register step layer in store (hidden by default — handleStepView controls visibility). */
function handleStepResult(
  stepIndex: number,
  geojson: any,
  meta: { name: string; capability: string; colorField?: string; opacity?: number },
) {
  if (!config.value) return
  const layerKey = stepKey(stepIndex, meta.name)
  const color = stepColor(meta.capability)
  store.setLayer(layerKey, geojson, { color, opacity: meta.opacity ?? 1, colorField: meta.colorField })
  // Start hidden — view logic will reveal per step
  store.setLayerVisibility(layerKey, false)
  store.commitVisibility()
  // Race fix: if the user has already advanced past this index (rare, but
  // possible if results arrive out of order or autoplay started during
  // prefetch), re-apply the view so the new layer immediately shows up.
  const curView = pipelineRef.value?.activeStepIndex?.value ?? -1
  if (curView === stepIndex) handleStepView(curView)
}

/**
 * Apply the visibility/opacity rules for a given viewStepIndex.
 *
 * -1 : base layers @ 100%, no step layers — initial state
 *  N : current step @ 100%; latest prior step with a DIFFERENT geometry type
 *      shown as a 0.4 ghost (e.g. when viewing a building cohort, the routes
 *      that drove the spatial filter remain visible). Base layers that are
 *      ancestors (via stepInputs) of the current or ghost step are hidden —
 *      otherwise a 30K-feature base + a 1K-feature subset overlay at 0.15 vs
 *      1.0 reads as visual noise. Other base layers stay at 0.15 as faint
 *      context (e.g. batiments while viewing compute_veg_area in S5).
 */
function handleStepView(viewStepIndex: number) {
  // Collect step keys in pipeline order
  const stepKeys: string[] = []
  for (const [key] of store.state.layers) {
    if (key.startsWith('step_')) stepKeys.push(key)
  }
  stepKeys.sort((a, b) => {
    const ai = parseInt(a.match(/^step_(\d+)_/)?.[1] || '0', 10)
    const bi = parseInt(b.match(/^step_(\d+)_/)?.[1] || '0', 10)
    return ai - bi
  })

  if (viewStepIndex < 0) {
    // Initial: show all base layers full opacity, hide all step layers
    for (const [key] of store.state.layers) {
      if (key.startsWith('step_')) {
        store.setLayerVisibility(key, false)
      } else {
        store.setLayerVisibility(key, true)
        store.setLayerOpacity(key, 1)
      }
    }
    store.commitVisibility()
    return
  }

  const currentKey = stepKeys[viewStepIndex]
  const currentLayer = store.state.layers.get(currentKey)
  const currentType = currentLayer?.type

  // Solo mode for the final step: choropleth/heatmap scenarios (e.g. S6
  // real-estate) opt into hiding every other layer once the painted tiles
  // cover the area, since any ghost or base context only adds visual noise.
  const isFinalStep = viewStepIndex === stepKeys.length - 1
  const soloFinalStep = !!config.value?.soloFinalStep && isFinalStep
  if (soloFinalStep) {
    for (const [key] of store.state.layers) {
      if (key === currentKey) {
        store.setLayerVisibility(key, true)
        store.setLayerOpacity(key, 1)
      } else {
        store.setLayerVisibility(key, false)
      }
    }
    store.commitVisibility()
    return
  }

  // Ghost = most recent prior step layer of a DIFFERENT geometry type.
  // Same-type ghost is suppressed — it's just a larger superset of the current
  // step (e.g. filter_in_flood_zone vs filter_in_flood_altitude both polygons).
  let ghostKey: string | null = null
  for (let i = viewStepIndex - 1; i >= 0; i--) {
    const k = stepKeys[i]
    const l = store.state.layers.get(k)
    if (l && l.type !== currentType) { ghostKey = k; break }
  }

  // Trace each active step back to its base-layer ancestor(s) through
  // stepInputs. Only those bases are actual supersets of the step layer and
  // should be hidden; unrelated bases of the same geometry type (e.g.
  // batiments while viewing compute_veg_area on vegetation — both polygons)
  // must stay visible so the user keeps their spatial reference.
  const cfg = config.value
  const baseNames = new Set(cfg?.layers ?? [])
  const stepInputs = cfg?.stepInputs
  const primaryLayer = cfg?.layers?.[0]
  const hiddenBases = new Set<string>()
  const addAncestor = (key: string | null) => {
    if (!key) return
    let cur = key.match(/^step_\d+_(.+)$/)?.[1]
    const seen = new Set<string>()
    while (cur && !seen.has(cur)) {
      seen.add(cur)
      let input = stepInputs?.[cur]
      // PipelinePanel normalizes 'input' to the primary base (config.layers[0])
      // before dispatch — mirror that here so ancestry resolves in both cases.
      if (input === 'input' && primaryLayer) input = primaryLayer
      if (!input) {
        // Implicit primary input (no stepInputs entry) — treat as primary base.
        if (primaryLayer) hiddenBases.add(primaryLayer)
        break
      }
      if (baseNames.has(input)) { hiddenBases.add(input); break }
      cur = input
    }
  }
  addAncestor(currentKey)
  addAncestor(ghostKey)

  for (const [key, layer] of store.state.layers) {
    if (key.startsWith('step_')) {
      if (key === currentKey) {
        store.setLayerVisibility(key, true)
        store.setLayerOpacity(key, 1)
      } else if (key === ghostKey) {
        store.setLayerVisibility(key, true)
        store.setLayerOpacity(key, 0.4)
      } else {
        store.setLayerVisibility(key, false)
      }
    } else {
      if (hiddenBases.has(key)) {
        // Base layer has been promoted into an active step layer — hide the
        // superset so the subset reads cleanly.
        store.setLayerVisibility(key, false)
      } else {
        // Non-ancestor base: keep as readable context (e.g. batiments while
        // viewing compute_veg_area in S5). 0.4 reads clearly on MapLibre
        // without competing with the step layer at 1.0.
        store.setLayerVisibility(key, true)
        store.setLayerOpacity(key, 0.4)
      }
      // Silence unused-variable warning: layer.type is intentionally not
      // consulted anymore (ancestry > geometry type for hide decisions).
      void layer
    }
  }
  store.commitVisibility()
}

function handlePipelineReset() {
  for (const [key] of store.state.layers) {
    if (!key.startsWith('step_')) {
      store.setLayerVisibility(key, true)
      store.setLayerOpacity(key, 1)
    }
  }
  store.removePipelineLayers()
}

/** Parse step layer key → step index, or -1 if not a step layer */
function stepIndexFromKey(key: string): number {
  const m = key.match(/^step_(\d+)_/)
  return m ? parseInt(m[1], 10) : -1
}

function onLegendClick(name: string) {
  const idx = stepIndexFromKey(name)
  if (idx >= 0 && pipelineRef.value) {
    // Step layer — delegate to PipelinePanel focus (toggle isolation on map)
    pipelineRef.value.focusStep(idx)
  } else {
    // Base layer — simple visibility toggle
    store.toggleLayerVisibility(name)
  }
}

function onLegendDblClick(name: string) {
  // Isolate this single layer (step or base)
  const visibleLayers = [...store.state.layers.entries()].filter(([, l]) => l.visible)
  if (visibleLayers.length === 1 && visibleLayers[0][0] === name) {
    store.showAllLayers()
  } else {
    store.isolateLayer(name)
  }
}

// Split-mode view sync: mirror the user's pan/zoom from one engine to the
// other. `setView` on the peer guards its own emit, so there's no A→B→A loop.
function onMaplibreView(v: { center: [number, number]; zoom: number }) {
  leafletRef.value?.setView?.(v.center, v.zoom)
}
function onLeafletView(v: { center: [number, number]; zoom: number }) {
  maplibreRef.value?.setView?.(v.center, v.zoom)
}
</script>

<template>
  <div class="gp-dual-map">
    <!-- Banner -->
    <div class="gp-dual-map-banner">
      Same API, Different Clients — GISPulse is Client-Agnostic
    </div>

    <!-- Header -->
    <div class="gp-dual-map-header">
      <span class="gp-dual-map-desc">{{ config?.description }}</span>
      <div class="gp-dual-map-controls">
        <div class="gp-layout-toggle">
          <button :class="{ active: layout === 'split' }" @click="layout = 'split'">Split</button>
          <button :class="{ active: layout === 'tabs' }" @click="layout = 'tabs'">Tabs</button>
        </div>
        <DrawToolbar
          v-model="store.state.drawMode"
          :vertex-count="drawVertexCount"
          :measure="drawMeasure"
          @undo="onToolbarUndo"
          @finish="onToolbarFinish"
        />
      </div>
    </div>

    <!-- Loading / Error -->
    <div v-if="loading" class="gp-dual-map-loading">
      Chargement des donnees depuis l'API GISPulse...
    </div>
    <div v-else-if="error" class="gp-dual-map-error">{{ error }}</div>

    <!-- Maps -->
    <template v-else>
      <!-- Tab buttons (tabs mode) -->
      <div v-if="layout === 'tabs'" class="gp-tab-bar">
        <button :class="{ active: activeTab === 'maplibre' }" @click="activeTab = 'maplibre'">
          MapLibre GL
        </button>
        <button :class="{ active: activeTab === 'leaflet' }" @click="activeTab = 'leaflet'">
          Leaflet
        </button>
      </div>

      <div class="gp-dual-map-body" :class="layout">
        <div class="gp-map-panel" v-show="layout === 'split' || activeTab === 'maplibre'">
          <div class="gp-map-label gp-label-maplibre">MapLibre GL</div>
          <PlaygroundMap
            ref="maplibreRef"
            engine="maplibre"
            :layers="displayedLayers"
            :center="config?.center || [0, 0]"
            :zoom="config?.zoom || 10"
            :draw-mode="store.state.drawMode"
            :selected-feature-id="store.state.selectedFeatureId"
            @feature-drawn="handleFeatureDrawn"
            @view-change="onMaplibreView"
            @draw-state="onDrawState"
          />
        </div>
        <div
          v-if="leafletMounted"
          class="gp-map-panel"
          v-show="layout === 'split' || activeTab === 'leaflet'"
        >
          <div class="gp-map-label gp-label-leaflet">Leaflet</div>
          <PlaygroundMap
            ref="leafletRef"
            engine="leaflet"
            :layers="displayedLayers"
            :center="config?.center || [0, 0]"
            :zoom="config?.zoom || 10"
            :draw-mode="store.state.drawMode"
            :selected-feature-id="store.state.selectedFeatureId"
            @feature-drawn="handleFeatureDrawn"
            @view-change="onLeafletView"
            @draw-state="onDrawState"
          />
        </div>
        <div
          v-else
          class="gp-map-panel gp-map-panel-pending"
          v-show="layout === 'split' || activeTab === 'leaflet'"
          @click="ensureLeaflet"
        >
          <div class="gp-map-label gp-label-leaflet">Leaflet</div>
          <div class="gp-map-pending-msg">Chargement Leaflet…</div>
        </div>
      </div>

      <!-- Pipeline -->
      <PipelinePanel
        v-if="showPipeline && config?.pipelineRules.length"
        ref="pipelineRef"
        :rule-names="config.pipelineRules"
        :dataset-id="datasetId"
        :layer-name="config.layers[0]"
        :step-inputs="config.stepInputs"
        :static-pipeline-results="config.staticPipelineResults"
        @step-result="handleStepResult"
        @step-view="handleStepView"
        @pipeline-reset="handlePipelineReset"
      />

      <!-- Triggers -->
      <TriggerPanel v-if="showTriggers" />

      <!-- Legend -->
      <div class="gp-map-legend">
        <!-- Base layers -->
        <div
          v-for="[name, layer] in [...store.state.layers].filter(([k]) => !k.startsWith('step_'))"
          :key="name"
          class="gp-legend-item"
          :class="{ 'gp-legend-hidden': !layer.visible }"
          @click="onLegendClick(name)"
          @dblclick.prevent="onLegendDblClick(name)"
          :title="layer.visible ? 'Click: toggle · Double-click: isolate' : 'Click: show'"
        >
          <span class="gp-legend-eye">{{ layer.visible ? '&#9679;' : '&#9675;' }}</span>
          <span class="gp-legend-swatch" :style="{ background: layer.visible ? layer.color : '#ccc' }" />
          <span class="gp-legend-label">{{ name }}</span>
          <span v-if="layer.geojson?.features" class="gp-legend-count">{{ layer.geojson.features.length }}</span>
        </div>
        <!-- Step layers (shown after pipeline runs) -->
        <template v-if="[...store.state.layers].some(([k]) => k.startsWith('step_'))">
          <span class="gp-legend-sep">|</span>
          <div
            v-for="[name, layer] in [...store.state.layers].filter(([k]) => k.startsWith('step_'))"
            :key="name"
            class="gp-legend-item gp-legend-step"
            :class="{
              'gp-legend-hidden': !layer.visible,
              'gp-legend-active': pipelineRef?.activeStepIndex === stepIndexFromKey(name)
            }"
            @click="onLegendClick(name)"
            @dblclick.prevent="onLegendDblClick(name)"
            :title="'Click: show on map'"
          >
            <span class="gp-legend-eye">{{ layer.visible ? '&#9679;' : '&#9675;' }}</span>
            <span class="gp-legend-swatch" :style="{ background: layer.visible ? layer.color : '#ccc' }" />
            <span class="gp-legend-label">{{ name.replace(/^step_\d+_/, '') }}</span>
            <span v-if="layer.geojson?.features" class="gp-legend-count">{{ layer.geojson.features.length }}</span>
          </div>
        </template>
      </div>
    </template>
  </div>
</template>

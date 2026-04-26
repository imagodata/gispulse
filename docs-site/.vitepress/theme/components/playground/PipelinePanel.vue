<script setup lang="ts">
import { ref, computed, watch, onUnmounted } from 'vue'
import { useData, withBase } from 'vitepress'
import { useGispulseApi } from '../../composables/useGispulseApi'
import { usePlaygroundStore } from '../../composables/usePlaygroundStore'
import { stepColor, capabilityInfo } from './stepColors'

const I18N = {
  fr: {
    duration: 'Duree',
    pipelineReady: 'Pipeline pret — naviguez avec Next',
    startGuided: 'Demarrer la visite guidee',
    runAllFast: 'Run All (rapide)',
    running: 'Execution en cours...',
    prevStep: 'Etape precedente',
    nextStep: 'Etape suivante',
    pause: 'Pause',
    autoPlay: 'Lecture auto',
  },
  en: {
    duration: 'Duration',
    pipelineReady: 'Pipeline ready — navigate with Next',
    startGuided: 'Start guided tour',
    runAllFast: 'Run all (fast)',
    running: 'Running...',
    prevStep: 'Previous step',
    nextStep: 'Next step',
    pause: 'Pause',
    autoPlay: 'Auto-play',
  },
} as const

const { lang } = useData()
const t = computed(() => I18N[(lang.value === 'en' ? 'en' : 'fr')])

interface StepState {
  name: string
  capability: string
  ruleId: string
  config: Record<string, any>
  status: 'pending' | 'running' | 'completed' | 'failed' | 'skipped'
  featureCount: number | null
  featuresIn: number | null
  featuresDelta: number | null
  columnsAdded: string[]
  columnsRemoved: string[]
  bbox: number[] | null
  duration: number | null
  error: string | null
  geojson: any | null
}

const props = defineProps<{
  ruleNames: string[]
  datasetId: string
  layerName: string
  stepInputs?: Record<string, string>
  /** When set, each rule name with a matching key is loaded from the URL
   *  (relative to the docs `public/playground/` root) instead of being
   *  computed via the live API. Used by S3 accessibility to bypass the
   *  ~86 s isochrone run on the demo Cloud Run. */
  staticPipelineResults?: Record<string, string>
}>()

const emit = defineEmits<{
  'step-result': [
    stepIndex: number,
    geojson: any,
    meta: { name: string; capability: string; colorField?: string; opacity?: number },
  ]
  'step-view': [viewStepIndex: number]
  'pipeline-reset': []
}>()

const api = useGispulseApi()
const store = usePlaygroundStore()

const running = ref(false)
const globalError = ref('')
const steps = ref<StepState[]>([])
const missingRules = ref<string[]>([])
const initialFeatureCount = ref<number | null>(null)

/** Currently viewed step index. -1 = initial (just base layers). */
const viewStepIndex = ref(-1)

/** Expanded detail panel index. */
const expandedStep = ref(-1)

/** Playback mode for guided tour. */
const playing = ref(false)
const autoplayDelayMs = 4500
let autoplayTimer: ReturnType<typeof setTimeout> | null = null

const matchedRules = computed(() =>
  store.state.rules.filter(r => props.ruleNames.includes(r.name))
)

/** Capability lookup for ruleNames when the live API rules aren't loaded
 *  (offline / static-replay scenarios). Keys mirror rules in
 *  `docs-site/public/playground/scenario-*-rules.json`; only listed here
 *  because the static-replay path can't ask the server. */
const STATIC_RULE_CAPABILITIES: Record<string, string> = {
  filter_sante: 'filter',
  isochrone_rings: 'isochrone',
  classify_by_ring: 'classify_by_ring',
}

function emptyStep(name: string, capability: string): StepState {
  return {
    name,
    capability,
    ruleId: name,
    config: {},
    status: 'pending',
    featureCount: null,
    featuresIn: null,
    featuresDelta: null,
    columnsAdded: [],
    columnsRemoved: [],
    bbox: null,
    duration: null,
    error: null,
    geojson: null,
  }
}

/** Rebuild the step list whenever the scenario (ruleNames) or the loaded
 *  rules change. Unlike the previous guard-on-length version, this always
 *  reflects the current scenario — required when the user navigates between
 *  scenario pages without unmounting the panel. */
watch(
  [matchedRules, () => props.ruleNames, () => store.state.rules.length, () => props.staticPipelineResults],
  ([rules]) => {
    // Static-replay scenarios (S3) don't depend on the live API rule
    // catalogue — synthesize the step list directly from ruleNames so the
    // panel renders even when the demo backend is unreachable.
    if (props.staticPipelineResults) {
      steps.value = props.ruleNames.map((name) =>
        emptyStep(name, STATIC_RULE_CAPABILITIES[name] ?? 'filter'),
      )
      missingRules.value = []
      return
    }

    if (!rules.length) {
      steps.value = []
      // Only flag missing rules once the backend rule list has loaded —
      // otherwise we'd light up the warning for one frame on every mount.
      missingRules.value = store.state.rules.length ? [...props.ruleNames] : []
      return
    }
    steps.value = props.ruleNames
      .map(name => {
        const rule = rules.find(r => r.name === name)
        if (!rule) return null
        return {
          name: rule.name,
          capability: rule.capability,
          ruleId: rule.id,
          config: rule.config || {},
          status: 'pending' as const,
          featureCount: null,
          featuresIn: null,
          featuresDelta: null,
          columnsAdded: [],
          columnsRemoved: [],
          bbox: null,
          duration: null,
          error: null,
          geojson: null,
        }
      })
      .filter(Boolean) as StepState[]
    missingRules.value = props.ruleNames.filter(
      name => !rules.find(r => r.name === name),
    )
  },
  { immediate: true },
)

// Reset the cached "initial count" when the main layer changes (scenario switch).
watch(() => props.layerName, () => { initialFeatureCount.value = null })

watch(() => store.state.layers, (layers) => {
  if (initialFeatureCount.value !== null) return
  const layer = layers.get(props.layerName)
  if (layer?.geojson?.features) {
    initialFeatureCount.value = layer.geojson.features.length
  }
}, { immediate: true, deep: true })

onUnmounted(() => {
  if (autoplayTimer) clearTimeout(autoplayTimer)
})

function resetPipeline() {
  stopAutoplay()
  for (const step of steps.value) {
    step.status = 'pending'
    step.featureCount = null
    step.featuresIn = null
    step.featuresDelta = null
    step.columnsAdded = []
    step.columnsRemoved = []
    step.bbox = null
    step.duration = null
    step.error = null
    step.geojson = null
  }
  viewStepIndex.value = -1
  expandedStep.value = -1
  running.value = false
  globalError.value = ''
  store.state.jobStatus = 'idle'
  store.state.jobMessage = ''
  store.state.showingBefore = false

  if (store.state.beforeSnapshot) {
    for (const [k, geojson] of store.state.beforeSnapshot) {
      store.setLayer(k, geojson)
    }
    store.state.beforeSnapshot = null
  }
  emit('pipeline-reset')
}

/** Capabilities that emit a per-feature color column (default field name in
 *  parens). The pipeline uses `step.config.color_col` first, then falls back
 *  to this default. Add new ramp-emitting capabilities here so the map paints
 *  per-feature instead of falling back to the flat capability tint. */
const COLOR_COL_DEFAULTS: Record<string, string> = {
  classify: 'color',
  classify_by_ring: 'ring_color',
  classify_categorical: 'color',
  choropleth: 'color',
  continuous_ramp: 'color',
  head_tail_breaks: 'color',
  bivariate_choropleth: 'color',
}

/** Default ring palette for `isochrone` with `cost_budgets`, aligned with the
 *  classify_by_ring palette used in S3 (green = closest). Up to 5 rings, then
 *  cycles. Index 0 = smallest budget. */
const ISOCHRONE_RING_PALETTE = ['#1a9850', '#fee08b', '#fdae61', '#f46d43', '#a50026']

/**
 * Augment a step's GeoJSON before it hits the store:
 * - capabilities in COLOR_COL_DEFAULTS keep the column the backend wrote and
 *   tell the map which one to read;
 * - multi-budget `isochrone` (no per-feature color from the backend) gets a
 *   synthetic `_style_color` mapped from `cost_budget`, and features are
 *   sorted DESC by cost_budget so the smallest ring is drawn last (on top).
 *
 * Returns a possibly-new FeatureCollection plus the colorField the map
 * should read. `colorField` is undefined when no per-feature paint applies.
 */
function decorateStepGeojson(
  step: StepState,
  geojson: any,
): { geojson: any; colorField: string | undefined; opacity: number | undefined } {
  const cap = step.capability
  const declaredCol = COLOR_COL_DEFAULTS[cap]
  if (declaredCol) {
    return { geojson, colorField: step.config?.color_col ?? declaredCol, opacity: undefined }
  }

  if (cap === 'isochrone' && geojson?.features?.length) {
    const features = geojson.features as any[]
    const budgets = features
      .map(f => Number(f?.properties?.cost_budget))
      .filter((v: number) => Number.isFinite(v))
    if (budgets.length) {
      const sorted = [...new Set(budgets)].sort((a, b) => a - b)
      const colorOf = new Map<number, string>()
      for (let k = 0; k < sorted.length; k++) {
        colorOf.set(sorted[k], ISOCHRONE_RING_PALETTE[k % ISOCHRONE_RING_PALETTE.length])
      }
      const painted = features.map(f => {
        const b = Number(f?.properties?.cost_budget)
        const color = colorOf.get(b)
        if (!color) return f
        return {
          ...f,
          properties: { ...(f.properties ?? {}), _style_color: color },
        }
      })
      // Larger ring first → smaller ring drawn last → smaller ring on top.
      // Combined with a higher opacity, each annulus reads as its own band:
      // the outer red disc shows where neither orange/yellow/green overlaps,
      // and the innermost green dominates the centre instead of blending into
      // a muddy stack of 4 translucent layers.
      painted.sort((a, b) => Number(b.properties?.cost_budget ?? 0) - Number(a.properties?.cost_budget ?? 0))
      return {
        geojson: { ...geojson, features: painted },
        colorField: '_style_color',
        opacity: 2.2,
      }
    }
  }

  return { geojson, colorField: undefined, opacity: undefined }
}

/** Fetch + gunzip a precomputed step output shipped under
 *  `docs-site/public/playground/`. Mirrors useStaticPlayground.fetchGeoJSON
 *  (kept inline to avoid widening that composable's surface for one caller). */
async function fetchStaticStepGeoJSON(relPath: string): Promise<any> {
  const url = withBase(`/playground/${relPath}`)
  const res = await fetch(url)
  if (!res.ok) throw new Error(`static step ${relPath}: ${res.status}`)
  if (relPath.endsWith('.gz') && 'DecompressionStream' in globalThis) {
    const ds = new (globalThis as any).DecompressionStream('gzip')
    const text = await new Response(res.body!.pipeThrough(ds)).text()
    return JSON.parse(text)
  }
  return res.json()
}

/** Replay the pipeline from precomputed step files instead of hitting the
 *  live API. Same UX as runPipeline (status updates, decorate, emit) so the
 *  PipelinePanel UI stays interactive — only the source of step results
 *  differs. Returns true when every mapped step loaded successfully. */
async function runPipelineFromStatic(): Promise<boolean> {
  if (running.value || !steps.value.length) return false
  const sources = props.staticPipelineResults
  if (!sources) return false

  running.value = true
  globalError.value = ''
  store.snapshotBefore()
  store.state.jobStatus = 'running'
  for (const s of steps.value) { s.status = 'running' }

  const startTime = Date.now()
  try {
    let prevCount = initialFeatureCount.value ?? null
    for (let i = 0; i < steps.value.length; i++) {
      const step = steps.value[i]
      const url = sources[step.name]
      if (!url) {
        // Unmapped step — mark skipped so the UI shows it greyed out rather
        // than spinning forever. Caller could fall back to live API here, but
        // S3 maps every step so this is currently dead code in practice.
        step.status = 'skipped'
        continue
      }
      const tStep = Date.now()
      const geojson = await fetchStaticStepGeoJSON(url)
      const count = geojson?.features?.length ?? 0
      step.featureCount = count
      step.featuresIn = prevCount
      step.featuresDelta = prevCount === null ? null : count - prevCount
      step.columnsAdded = []
      step.columnsRemoved = []
      step.bbox = null
      step.duration = (Date.now() - tStep) / 1000
      step.status = 'completed'
      step.geojson = geojson

      const { geojson: paintedGeojson, colorField, opacity } = decorateStepGeojson(step, geojson)
      step.geojson = paintedGeojson
      emit('step-result', i, paintedGeojson, {
        name: step.name,
        capability: step.capability,
        colorField,
        opacity,
      })
      prevCount = count
    }
    const totalMs = Date.now() - startTime
    const lastStep = steps.value[steps.value.length - 1]
    store.state.jobStatus = 'completed'
    store.state.jobMessage = `${lastStep?.featureCount ?? 0} features (${(totalMs / 1000).toFixed(1)}s, statique)`
    return true
  } catch (e: any) {
    for (const s of steps.value) {
      if (s.status === 'running') s.status = 'failed'
    }
    globalError.value = e.message
    store.state.jobStatus = 'failed'
    return false
  } finally {
    running.value = false
  }
}

/** Execute the full pipeline in a single backend call. Caches geojson per step. */
async function runPipeline(): Promise<boolean> {
  // Static-replay path: scenarios that ship precomputed step outputs (S3
  // accessibility) skip the API entirely. See ScenarioConfig.staticPipelineResults
  // for the reasoning. When set, the panel stays usable even if the demo API
  // is down — and the heavy classify_by_ring overlay (~3 MB gzipped) lands
  // in a couple of seconds rather than a 60 s round-trip that may 500.
  if (props.staticPipelineResults) {
    return runPipelineFromStatic()
  }

  if (running.value || !matchedRules.value.length || !props.datasetId) return false

  running.value = true
  globalError.value = ''
  store.snapshotBefore()
  store.state.jobStatus = 'running'

  for (const s of steps.value) { s.status = 'running' }

  const startTime = Date.now()

  try {
    const orderedRules = props.ruleNames
      .map(name => matchedRules.value.find(r => r.name === name))
      .filter(Boolean)

    const pipelineSteps = orderedRules.map((rule: any) => {
      const { order, ...params } = rule.config || {}
      const step: { id: string; type: string; capability: string; params: any; input?: string } = {
        id: rule.name,
        type: 'capability',
        capability: rule.capability,
        params,
      }
      const explicitInput = props.stepInputs?.[rule.name]
      if (explicitInput) {
        // Normalize the "input" sentinel to the real main layer name so the
        // payload validates against both current and older backend versions.
        step.input = explicitInput === 'input' ? props.layerName : explicitInput
      }
      return step
    })

    const stepIds = new Set(pipelineSteps.map(s => s.id))
    const refLayers: Record<string, string> = {}
    const addRef = (name: string | undefined) => {
      if (!name || stepIds.has(name) || name === 'input' || refLayers[name]) return
      refLayers[name] = name
    }
    for (const step of pipelineSteps) {
      addRef(step.params?.ref_layer)
      addRef(step.input)
    }

    const response = await api.executePipelineSteps({
      dataset_id: props.datasetId,
      layer: props.layerName,
      steps: pipelineSteps,
      ref_layers: refLayers,
      simplify: 0.00001,
      // Cap per-step GeoJSON return at 15 000 features. S3 accessibility's
      // classify_by_ring on the full 77k Clermont batiments would emit
      // ~62 MB of GeoJSON (props-dominated; simplify barely helps on 4-vertex
      // building polygons), well over Cloud Run's 32 MB per-request cap.
      // Even at 30k (~28 MB) the browser saw intermittent HTTP 500 — likely
      // the proxied path applies a tighter cap than the curl HTTP/2 measurement
      // suggests. 15k holds the response around 14 MB with comfortable margin
      // and still ships ~1.9× more classified features than the old 8k cap.
      // Backend uses the full source internally; above this cap it falls
      // back to a deterministic random sample (see pipelines_router truncate
      // path). The 77k base batiments still load full from the static
      // bundle, so the user sees every building — only the coloured overlay
      // is sampled.
      limit: 15000,
    })

    for (let i = 0; i < response.steps.length; i++) {
      const stepResult = response.steps[i]
      const step = steps.value[i]
      if (!step) continue

      step.featureCount = stepResult.features_count
      step.featuresIn = stepResult.features_in ?? null
      step.featuresDelta = stepResult.features_delta ?? null
      step.columnsAdded = stepResult.columns_added ?? []
      step.columnsRemoved = stepResult.columns_removed ?? []
      step.bbox = stepResult.bbox ?? null
      step.duration = (stepResult.duration_ms ?? 0) / 1000
      step.status = 'completed'
      step.geojson = stepResult.geojson

      // Register layer in store (invisible initially — DualMapView manages visibility per view)
      // Capabilities that emit a per-feature color column are read by the map
      // via `colorField`. For multi-budget isochrone we synthesise the color
      // here (one shade per ring) and reorder so the smallest ring renders on
      // top of the larger discs.
      const { geojson: paintedGeojson, colorField, opacity } = decorateStepGeojson(step, stepResult.geojson)
      step.geojson = paintedGeojson
      emit('step-result', i, paintedGeojson, {
        name: step.name,
        capability: step.capability,
        colorField,
        opacity,
      })
    }

    for (let i = response.steps.length; i < steps.value.length; i++) {
      steps.value[i].status = 'skipped'
    }

    const totalMs = response.total_duration_ms ?? (Date.now() - startTime)
    const lastStep = steps.value[response.steps.length - 1] || steps.value[steps.value.length - 1]
    store.state.jobStatus = 'completed'
    store.state.jobMessage = `${lastStep?.featureCount ?? 0} features (${(totalMs / 1000).toFixed(1)}s)`
    return true
  } catch (e: any) {
    for (const s of steps.value) {
      if (s.status === 'running') s.status = 'failed'
    }
    globalError.value = e.message
    store.state.jobStatus = 'failed'
    return false
  } finally {
    running.value = false
  }
}

/** Instant mode: run + jump to final step */
async function runInstant() {
  const ok = await runPipeline()
  if (ok) {
    const lastIdx = steps.value.filter(s => s.status === 'completed').length - 1
    setView(lastIdx)
  }
}

/** Guided mode: run + stay at view=-1 (initial), let user navigate */
async function runGuided() {
  const ok = await runPipeline()
  if (ok) setView(0)
}

function setView(idx: number) {
  const maxIdx = steps.value.filter(s => s.status === 'completed').length - 1
  const clamped = Math.max(-1, Math.min(idx, maxIdx))
  viewStepIndex.value = clamped
  expandedStep.value = clamped
  emit('step-view', clamped)
}

function nextStep() {
  const maxIdx = steps.value.filter(s => s.status === 'completed').length - 1
  if (viewStepIndex.value < maxIdx) setView(viewStepIndex.value + 1)
  else stopAutoplay()
}

function prevStep() {
  if (viewStepIndex.value > -1) setView(viewStepIndex.value - 1)
}

function startAutoplay() {
  if (playing.value) return
  const maxIdx = steps.value.filter(s => s.status === 'completed').length - 1
  if (viewStepIndex.value >= maxIdx) setView(-1)  // restart from beginning
  playing.value = true
  tick()
}

function tick() {
  if (!playing.value) return
  autoplayTimer = setTimeout(() => {
    const maxIdx = steps.value.filter(s => s.status === 'completed').length - 1
    if (viewStepIndex.value < maxIdx) {
      nextStep()
      tick()
    } else {
      stopAutoplay()
    }
  }, autoplayDelayMs)
}

function stopAutoplay() {
  playing.value = false
  if (autoplayTimer) { clearTimeout(autoplayTimer); autoplayTimer = null }
}

function togglePlayPause() {
  if (playing.value) stopAutoplay()
  else startAutoplay()
}

function onStepClick(idx: number) {
  const step = steps.value[idx]
  if (!step || step.status !== 'completed') return
  stopAutoplay()
  setView(idx)
}

const completedCount = computed(() => steps.value.filter(s => s.status === 'completed').length)
const allDone = computed(() => steps.value.length > 0 && steps.value.every(s => s.status !== 'pending'))
const hasStarted = computed(() => steps.value.some(s => s.status !== 'pending'))
const hasFailed = computed(() => steps.value.some(s => s.status === 'failed'))
const totalDuration = computed(() =>
  steps.value.reduce((sum, s) => sum + (s.duration || 0), 0)
)

const currentStepInfo = computed(() => {
  if (viewStepIndex.value < 0) return null
  const step = steps.value[viewStepIndex.value]
  if (!step) return null
  return { step, info: capabilityInfo(step.capability) }
})

const atLast = computed(() => {
  const maxIdx = completedCount.value - 1
  return viewStepIndex.value >= maxIdx
})

const atFirst = computed(() => viewStepIndex.value <= -1)

/** External focus support (legend click) — kept for compat with DualMapView */
function focusStep(idx: number) {
  onStepClick(idx)
}

defineExpose({ focusStep, resetPipeline, steps, activeStepIndex: viewStepIndex, runPipeline, hasStarted, running })
</script>

<template>
  <div class="gp-step-pipeline">
    <!-- Header -->
    <div class="gp-step-header">
      <span class="gp-step-title">Pipeline</span>
      <span class="gp-step-progress" v-if="hasStarted && !hasFailed">
        <template v-if="viewStepIndex >= 0">
          Vue: {{ viewStepIndex + 1 }}/{{ completedCount }}
        </template>
        <template v-else>
          {{ completedCount }}/{{ steps.length }}
        </template>
      </span>
      <span class="gp-step-count" v-else-if="!hasFailed">
        {{ steps.length }} steps
      </span>
    </div>

    <!-- Input -->
    <div class="gp-step-initial" v-if="initialFeatureCount !== null">
      <span class="gp-step-dot gp-dot-start" />
      <span class="gp-step-label">{{ layerName }}</span>
      <span class="gp-step-feat">{{ initialFeatureCount?.toLocaleString() }} feat.</span>
    </div>

    <!-- Steps list -->
    <div class="gp-step-list">
      <div
        v-for="(step, idx) in steps"
        :key="step.name"
        class="gp-step-item"
        :class="[
          `gp-step-${step.status}`,
          { 'gp-step-current': idx === viewStepIndex && step.status === 'completed' },
          { 'gp-step-clickable': step.status === 'completed' },
        ]"
        :role="step.status === 'completed' ? 'button' : undefined"
        :tabindex="step.status === 'completed' ? 0 : -1"
        :aria-current="idx === viewStepIndex && step.status === 'completed' ? 'step' : undefined"
        :aria-label="`${step.name} — ${step.capability} (${step.status})`"
        @click="onStepClick(idx)"
        @keydown.enter.prevent="step.status === 'completed' && onStepClick(idx)"
        @keydown.space.prevent="step.status === 'completed' && onStepClick(idx)"
      >
        <div class="gp-step-connector" />
        <div class="gp-step-indicator">
          <span v-if="step.status === 'pending'" class="gp-step-num" aria-hidden="true">{{ idx + 1 }}</span>
          <span v-else-if="step.status === 'running'" class="gp-step-spinner" aria-hidden="true" />
          <span v-else-if="step.status === 'completed'" class="gp-step-check" aria-hidden="true">&#10003;</span>
          <span v-else-if="step.status === 'failed'" class="gp-step-cross" aria-hidden="true">&#10007;</span>
          <span v-else class="gp-step-skip" aria-hidden="true">-</span>
        </div>
        <div class="gp-step-body">
          <div class="gp-step-name">
            <span
              class="gp-step-color-dot"
              :style="{ background: stepColor(step.capability) }"
            />
            {{ step.name }}
          </div>
          <div class="gp-step-cap">{{ step.capability }}</div>
          <div v-if="step.status === 'completed' && step.featureCount !== null" class="gp-step-result">
            {{ step.featureCount.toLocaleString() }} feat.
            <span v-if="step.featuresDelta !== null && step.featuresDelta !== 0" class="gp-step-delta" :class="step.featuresDelta > 0 ? 'gp-delta-up' : 'gp-delta-down'">
              {{ step.featuresDelta > 0 ? '+' : '' }}{{ step.featuresDelta.toLocaleString() }}
            </span>
            <span v-if="step.duration" class="gp-step-dur">({{ (step.duration * 1000).toFixed(0) }}ms)</span>
          </div>
          <div v-if="step.status === 'completed' && step.featureCount === null" class="gp-step-result">
            done <span v-if="step.duration" class="gp-step-dur">({{ (step.duration * 1000).toFixed(0) }}ms)</span>
          </div>
          <div v-if="step.status === 'completed' && (step.columnsAdded.length || step.columnsRemoved.length)" class="gp-step-cols">
            <span v-if="step.columnsAdded.length" class="gp-col-added" :title="step.columnsAdded.join(', ')">+{{ step.columnsAdded.length }} col</span>
            <span v-if="step.columnsRemoved.length" class="gp-col-removed" :title="step.columnsRemoved.join(', ')">-{{ step.columnsRemoved.length }} col</span>
          </div>
          <div v-if="step.error" class="gp-step-error">{{ step.error }}</div>
        </div>
      </div>
    </div>

    <!-- Guided step preview (shown when a step is active) -->
    <transition name="gp-expand">
      <div v-if="currentStepInfo" class="gp-step-preview">
        <div class="gp-preview-head">
          <span
            class="gp-preview-dot"
            :style="{ background: stepColor(currentStepInfo.step.capability) }"
          />
          <span class="gp-preview-title">{{ currentStepInfo.info.label }}</span>
          <span class="gp-preview-idx">Step {{ viewStepIndex + 1 }}/{{ completedCount }}</span>
        </div>
        <div class="gp-preview-desc">{{ currentStepInfo.info.desc }}</div>
        <div class="gp-preview-stats">
          <div class="gp-stat">
            <span class="gp-stat-label">Input</span>
            <span class="gp-stat-value">{{ currentStepInfo.step.featuresIn?.toLocaleString() ?? '?' }}</span>
          </div>
          <div class="gp-stat">
            <span class="gp-stat-label">Output</span>
            <span class="gp-stat-value">{{ currentStepInfo.step.featureCount?.toLocaleString() ?? '?' }}</span>
          </div>
          <div v-if="currentStepInfo.step.featuresDelta !== null && currentStepInfo.step.featuresDelta !== 0" class="gp-stat">
            <span class="gp-stat-label">Delta</span>
            <span
              class="gp-stat-value"
              :class="currentStepInfo.step.featuresDelta > 0 ? 'gp-delta-up' : 'gp-delta-down'"
            >
              {{ currentStepInfo.step.featuresDelta > 0 ? '+' : '' }}{{ currentStepInfo.step.featuresDelta.toLocaleString() }}
            </span>
          </div>
          <div class="gp-stat">
            <span class="gp-stat-label">{{ t.duration }}</span>
            <span class="gp-stat-value">{{ currentStepInfo.step.duration ? (currentStepInfo.step.duration * 1000).toFixed(0) + 'ms' : '?' }}</span>
          </div>
        </div>
        <div v-if="currentStepInfo.step.columnsAdded.length" class="gp-preview-cols">
          <span class="gp-col-added">+ {{ currentStepInfo.step.columnsAdded.join(', ') }}</span>
        </div>
        <div v-if="currentStepInfo.step.columnsRemoved.length" class="gp-preview-cols">
          <span class="gp-col-removed">- {{ currentStepInfo.step.columnsRemoved.join(', ') }}</span>
        </div>
      </div>
    </transition>

    <!-- Output summary -->
    <div v-if="allDone && store.state.jobStatus === 'completed' && viewStepIndex < 0" class="gp-step-final">
      <span class="gp-step-dot gp-dot-end" />
      <span class="gp-step-label">{{ t.pipelineReady }}</span>
      <span class="gp-step-feat">
        {{ steps[steps.length - 1]?.featureCount?.toLocaleString() ?? '?' }} feat. final
        <span class="gp-step-dur" v-if="totalDuration">({{ (totalDuration * 1000).toFixed(0) }}ms)</span>
      </span>
    </div>

    <!-- Global error -->
    <div v-if="globalError" class="gp-step-global-error">
      {{ globalError }}
    </div>

    <!-- Missing rules warning (scenario expected N rules, backend has M<N) -->
    <div v-if="missingRules.length" class="gp-step-missing-warning">
      <strong>Pipeline incomplete:</strong>
      {{ missingRules.length }} rule{{ missingRules.length > 1 ? 's' : '' }}
      not found on the demo API ({{ missingRules.join(', ') }}).
      The pipeline will run on the remaining {{ steps.length }} step(s) only.
    </div>

    <!-- Actions -->
    <div class="gp-step-actions">
      <template v-if="!hasStarted">
        <button class="gp-btn gp-btn-primary" :disabled="running || !steps.length" @click="runGuided">
          {{ t.startGuided }}
        </button>
        <button class="gp-btn gp-btn-ghost" :disabled="running || !steps.length" @click="runInstant">
          {{ t.runAllFast }}
        </button>
      </template>
      <template v-else-if="!allDone && !hasFailed">
        <button class="gp-btn gp-btn-primary" :disabled="true">
          {{ t.running }}
        </button>
      </template>
      <template v-else-if="hasFailed">
        <button class="gp-btn gp-btn-primary" :disabled="running" @click="resetPipeline(); runGuided()">
          Retry
        </button>
        <button class="gp-btn gp-btn-ghost" :disabled="running" @click="resetPipeline">
          Reset
        </button>
      </template>
      <template v-else>
        <!-- Guided navigation controls -->
        <div class="gp-nav-controls">
          <button
            class="gp-nav-btn"
            :disabled="atFirst || playing"
            @click="prevStep"
            :title="t.prevStep"
            :aria-label="t.prevStep"
          >&lt; Prev</button>
          <button
            class="gp-nav-btn gp-nav-play"
            :class="{ 'gp-nav-playing': playing }"
            @click="togglePlayPause"
            :title="playing ? t.pause : t.autoPlay"
            :aria-label="playing ? t.pause : t.autoPlay"
          >
            <span v-if="playing" aria-hidden="true">&#9616;&#9616; {{ t.pause }}</span>
            <span v-else aria-hidden="true">&#9654; Play</span>
          </button>
          <button
            class="gp-nav-btn"
            :disabled="atLast || playing"
            @click="nextStep"
            :title="t.nextStep"
            :aria-label="t.nextStep"
          >Next &gt;</button>
        </div>
        <button class="gp-btn gp-btn-ghost" @click="resetPipeline">
          Reset
        </button>
        <button
          v-if="store.state.beforeSnapshot"
          class="gp-btn gp-btn-ghost"
          @click="store.toggleBeforeAfter()"
        >
          {{ store.state.showingBefore ? 'After' : 'Before' }}
        </button>
      </template>
    </div>
  </div>
</template>

<style scoped>
.gp-step-preview {
  margin: 12px 0;
  padding: 12px 14px;
  background: var(--vp-c-bg-soft);
  border: 1px solid var(--vp-c-divider);
  border-left: 3px solid var(--vp-c-brand-1);
  border-radius: 6px;
}
.gp-preview-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}
.gp-preview-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  flex-shrink: 0;
}
.gp-preview-title {
  font-weight: 600;
  font-size: 13px;
}
.gp-preview-idx {
  margin-left: auto;
  font-size: 11px;
  color: var(--vp-c-text-2);
  font-variant-numeric: tabular-nums;
}
.gp-preview-desc {
  font-size: 12px;
  color: var(--vp-c-text-2);
  line-height: 1.5;
  margin-bottom: 8px;
}
.gp-preview-stats {
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
}
.gp-stat {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.gp-stat-label {
  font-size: 10px;
  color: var(--vp-c-text-3);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
.gp-stat-value {
  font-size: 13px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.gp-delta-up { color: #2e7d32; }
.gp-delta-down { color: #c62828; }
.gp-preview-cols {
  margin-top: 6px;
  font-size: 11px;
  font-family: ui-monospace, monospace;
}
.gp-col-added { color: #2e7d32; }
.gp-col-removed { color: #c62828; }

.gp-nav-controls {
  display: inline-flex;
  gap: 4px;
  margin-right: 8px;
}
.gp-nav-btn {
  padding: 6px 10px;
  font-size: 12px;
  border: 1px solid var(--vp-c-divider);
  background: var(--vp-c-bg);
  border-radius: 4px;
  cursor: pointer;
  font-weight: 500;
}
.gp-nav-btn:hover:not(:disabled) {
  background: var(--vp-c-bg-soft);
  border-color: var(--vp-c-brand-1);
}
.gp-nav-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
.gp-nav-play {
  background: var(--vp-c-brand-1);
  color: white;
  border-color: var(--vp-c-brand-1);
}
.gp-nav-play:hover:not(:disabled) {
  background: var(--vp-c-brand-2);
  border-color: var(--vp-c-brand-2);
  color: white;
}
.gp-nav-playing {
  background: #c62828;
  border-color: #c62828;
}
.gp-nav-playing:hover:not(:disabled) {
  background: #b71c1c;
  border-color: #b71c1c;
}

.gp-step-current {
  background: var(--vp-c-bg-soft);
  border-left-width: 3px !important;
}

.gp-step-clickable {
  cursor: pointer;
}

.gp-step-clickable:focus-visible,
.gp-nav-btn:focus-visible,
.gp-btn:focus-visible {
  outline: 2px solid var(--vp-c-brand-1);
  outline-offset: 2px;
}

.gp-step-item:not(.gp-step-clickable) {
  cursor: default;
}

.gp-step-missing-warning {
  margin: 8px 0;
  padding: 8px 12px;
  border-radius: 4px;
  background: #fff4e5;
  border-left: 3px solid #e67e22;
  color: #7a4a00;
  font-size: 12px;
  line-height: 1.5;
}
.dark .gp-step-missing-warning {
  background: rgba(230, 126, 34, 0.12);
  color: #f0b77a;
}
</style>

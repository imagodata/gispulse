<script setup lang="ts">
/**
 * MiniDemo — static, lazy-mounted, backend-free scenario preview.
 *
 * Loads pre-computed GeoJSON from public/playground/data/<slug>/ via
 * useStaticPlayground. The MapLibre instance is only created when the
 * component scrolls into view (IntersectionObserver), and GeoJSON layers
 * are inserted in a single frame after decompression to avoid jank.
 *
 * Each dataset bundle is < 100 kB gzipped (see scripts/build_playground_data.py),
 * so even large BD TOPO extracts stay freeze-free on GitHub Pages.
 */
import { ref, onMounted, onUnmounted, shallowRef, computed } from 'vue'
import { useStaticPlayground, type ScenarioManifest } from '../../composables/useStaticPlayground'
import { featurePopupHtml } from './featurePopup'

const props = defineProps<{
  scenario: string
  height?: string
  /** Layers to show — default: all from manifest */
  layers?: string[]
  /** Hide attribution and controls for compact embedding */
  compact?: boolean
}>()

const container = ref<HTMLDivElement>()
const status = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
const errorMsg = ref('')
const manifest = shallowRef<ScenarioManifest | null>(null)

let map: any = null
let maplibregl: any = null
let currentPopup: any = null
let observer: IntersectionObserver | null = null

const COLORS: Record<string, string> = {
  batiments: '#E65100',
  routes: '#455A64',
  surfaces_eau: '#1565C0',
  cours_eau: '#2196F3',
  vegetation: '#2E7D32',
  equipements: '#7B1FA2',
}

const layerList = computed(() => {
  if (!manifest.value) return []
  const keys = Object.keys(manifest.value.layers).filter(
    (n) => manifest.value!.layers[n].file,
  )
  return props.layers ? keys.filter((n) => props.layers!.includes(n)) : keys
})

const totalKb = computed(() => {
  if (!manifest.value) return 0
  return Math.round(manifest.value.total_size_bytes / 1024)
})

const featureCount = computed(() => {
  if (!manifest.value) return 0
  return Object.values(manifest.value.layers).reduce((n, l) => n + l.features, 0)
})

async function initMap() {
  if (map || status.value === 'loading' || status.value === 'ready') return
  status.value = 'loading'
  try {
    const api = useStaticPlayground()
    manifest.value = await api.loadScenario(props.scenario)

    maplibregl = await import('maplibre-gl')
    await import('maplibre-gl/dist/maplibre-gl.css')

    if (!container.value) return

    const isDark = document.documentElement.classList.contains('dark')
    const tileUrl = isDark
      ? 'https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png'
      : 'https://a.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}@2x.png'

    map = new maplibregl.Map({
      container: container.value,
      style: {
        version: 8,
        sources: {
          carto: {
            type: 'raster',
            tiles: [tileUrl],
            tileSize: 256,
            attribution:
              '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> &copy; <a href="https://carto.com/">CARTO</a>',
          },
        },
        layers: [{ id: 'carto', type: 'raster', source: 'carto' }],
      },
      center: manifest.value.center,
      zoom: manifest.value.zoom,
      attributionControl: !props.compact,
      interactive: true,
    })

    if (!props.compact) {
      map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right')
    }

    await new Promise<void>((resolve) => {
      if (map.loaded()) resolve()
      else map.once('load', () => resolve())
    })

    const geojsons = await api.loadAllLayers(props.scenario)

    for (const name of layerList.value) {
      const gj = geojsons[name]
      if (!gj) continue
      addLayer(name, gj)
    }

    attachClick()
    status.value = 'ready'
  } catch (err: any) {
    errorMsg.value = err?.message || String(err)
    status.value = 'error'
  }
}

function addLayer(name: string, geojson: any) {
  const srcId = `mini-${name}`
  const color = COLORS[name] || '#9E9E9E'
  map.addSource(srcId, { type: 'geojson', data: geojson })

  const first = geojson.features?.[0]?.geometry?.type || ''
  if (first.includes('Point')) {
    map.addLayer({
      id: `${srcId}-circle`,
      type: 'circle',
      source: srcId,
      paint: {
        'circle-color': color,
        'circle-radius': 5,
        'circle-stroke-width': 1.5,
        'circle-stroke-color': '#fff',
      },
    })
  } else if (first.includes('Line')) {
    map.addLayer({
      id: `${srcId}-line`,
      type: 'line',
      source: srcId,
      paint: { 'line-color': color, 'line-width': 2 },
    })
  } else {
    map.addLayer({
      id: `${srcId}-fill`,
      type: 'fill',
      source: srcId,
      paint: { 'fill-color': color, 'fill-opacity': 0.55, 'fill-outline-color': color },
    })
    map.addLayer({
      id: `${srcId}-outline`,
      type: 'line',
      source: srcId,
      paint: { 'line-color': color, 'line-width': 1 },
    })
  }
}

function attachClick() {
  map.on('click', (e: any) => {
    const features = map.queryRenderedFeatures(e.point)
    const hit = features.find((f: any) => f.source.startsWith('mini-'))
    if (!hit) return
    const layerName = hit.source.replace('mini-', '')
    currentPopup?.remove()
    currentPopup = new maplibregl.Popup({ closeButton: true, maxWidth: '300px' })
      .setLngLat(e.lngLat)
      .setHTML(featurePopupHtml(layerName, hit.properties || {}))
      .addTo(map)
  })
  map.on('mouseenter', () => (map.getCanvas().style.cursor = 'pointer'))
  map.on('mouseleave', () => (map.getCanvas().style.cursor = ''))
}

onMounted(() => {
  if (!container.value) return

  // Lazy-mount: only create MapLibre when the card scrolls near the viewport.
  observer = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting) {
          observer?.disconnect()
          observer = null
          initMap()
        }
      }
    },
    { rootMargin: '200px' },
  )
  observer.observe(container.value)
})

onUnmounted(() => {
  observer?.disconnect()
  currentPopup?.remove()
  map?.remove()
  map = null
})
</script>

<template>
  <div class="gp-mini-demo">
    <div
      ref="container"
      class="gp-mini-demo__map"
      :style="{ height: props.height || '320px' }"
    >
      <div v-if="status === 'idle'" class="gp-mini-demo__placeholder">
        <span>Carte : chargement au defilement...</span>
      </div>
      <div v-if="status === 'loading'" class="gp-mini-demo__loading">
        <span>Chargement des donnees...</span>
      </div>
      <div v-if="status === 'error'" class="gp-mini-demo__error">
        <strong>Erreur :</strong> {{ errorMsg }}
      </div>
    </div>
    <div v-if="manifest && !props.compact" class="gp-mini-demo__footer">
      <span>{{ featureCount }} features</span>
      <span class="gp-mini-demo__sep">·</span>
      <span>{{ totalKb }} kB gzip</span>
      <span class="gp-mini-demo__sep">·</span>
      <span>{{ layerList.join(', ') }}</span>
    </div>
  </div>
</template>

<style scoped>
.gp-mini-demo {
  margin: 1.25rem 0;
  border: 1px solid var(--vp-c-divider);
  border-radius: 8px;
  overflow: hidden;
  background: var(--vp-c-bg-soft);
}
.gp-mini-demo__map {
  position: relative;
  width: 100%;
  background: var(--vp-c-bg-alt);
}
.gp-mini-demo__placeholder,
.gp-mini-demo__loading,
.gp-mini-demo__error {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 0.85rem;
  color: var(--vp-c-text-2);
  text-align: center;
  padding: 0 1rem;
}
.gp-mini-demo__error {
  color: var(--vp-c-danger-1);
}
.gp-mini-demo__footer {
  padding: 0.5rem 0.8rem;
  font-size: 0.75rem;
  color: var(--vp-c-text-2);
  border-top: 1px solid var(--vp-c-divider);
  display: flex;
  gap: 0.4rem;
  flex-wrap: wrap;
  align-items: center;
}
.gp-mini-demo__sep {
  opacity: 0.5;
}
</style>

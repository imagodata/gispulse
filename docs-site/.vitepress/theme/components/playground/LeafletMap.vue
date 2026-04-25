<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch } from 'vue'
import type { LayerData } from '../../composables/usePlaygroundStore'
import { featurePopupHtml } from './featurePopup'
import {
  polygonAreaM2,
  polylineLengthM,
  formatArea,
  formatLength,
} from './geo'
import type { DrawMeasure } from './geo'

const props = defineProps<{
  layers: Map<string, LayerData>
  center: [number, number]
  zoom: number
  drawMode: 'none' | 'polygon' | 'point'
  selectedFeatureId: string | null
}>()

const emit = defineEmits<{
  'feature-click': [feature: any]
  'feature-drawn': [geojson: any]
  'view-change': [view: { center: [number, number]; zoom: number }]
  'draw-state': [state: { vertexCount: number; measure: DrawMeasure | null }]
}>()

const container = ref<HTMLDivElement>()
let map: any = null
let L: any = null

/** Live cursor position while drawing — drives the rubber-band + close-snap. */
let cursorLngLat: [number, number] | null = null
/** True when the cursor is within {@link SNAP_CLOSE_PX} of the first vertex
 *  (polygon with >= 3 pts). Click in this state closes the polygon instead
 *  of appending a new sommet. */
let nearClose = false
const SNAP_CLOSE_PX = 18

// Guards the `move` handler while `setView` applies a programmatic pose from
// the peer map, preventing A→B→A feedback in split-mode sync.
let suppressViewEmit = false

/** Cached layer groups and their state for diffing */
const layerCache = new Map<string, {
  group: any
  dataRef: any
  color: string
  visible: boolean
  opacity: number
}>()

const drawPoints: [number, number][] = []
let drawLayer: any = null
let drawMarkers: any[] = []
/** Separate rubber-band layer so it can be redrawn on every mousemove without
 *  touching the committed vertex markers / edge layer. */
let guideLayer: any = null

onMounted(async () => {
  L = (await import('leaflet')).default || await import('leaflet')
  await import('leaflet/dist/leaflet.css')

  delete (L.Icon.Default.prototype as any)._getIconUrl
  L.Icon.Default.mergeOptions({
    iconRetinaUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png',
    iconUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png',
    shadowUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png',
  })

  if (!container.value) return

  map = L.map(container.value, {
    center: [props.center[1], props.center[0]],
    zoom: props.zoom,
    doubleClickZoom: false,
    // Canvas is ~10x faster than SVG on multi-thousand-feature layers
    // (e.g. filter_near_arterials returns 10K+ polygons).
    preferCanvas: true,
    // Allow fractional zoom so split-mode sync from MapLibre (which emits
    // fractional zoom during wheel/pinch) doesn't snap to integers.
    zoomSnap: 0,
  })

  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '&copy; OpenStreetMap contributors',
    maxZoom: 19,
  }).addTo(map)

  map.on('click', (e: any) => {
    if (props.drawMode !== 'none') {
      handleDrawClick(e.latlng)
    }
  })

  map.on('dblclick', () => {
    if (props.drawMode === 'polygon' && drawPoints.length >= 3) {
      finishDraw()
    }
  })

  // Mousemove feeds rubber band + close-snap hit test. Suppressed when not
  // drawing — we piggyback on the one listener rather than registering on
  // every drawMode change.
  map.on('mousemove', (e: any) => {
    if (props.drawMode === 'none') return
    cursorLngLat = [e.latlng.lng, e.latlng.lat]
    updateCloseSnap()
    updateDrawPreview()
  })

  // Leaving the canvas should clear the rubber band so we don't leave a
  // dangling segment anchored at the last known cursor position.
  map.getContainer().addEventListener('mouseleave', () => {
    if (props.drawMode === 'none') return
    cursorLngLat = null
    nearClose = false
    updateDrawPreview()
  })

  // `zoomanim` fires once at the *start* of Leaflet's CSS zoom animation with
  // the target zoom/center — use it so the peer map jumps immediately instead
  // of waiting for `zoom`/`moveend` at animation end (~250ms lag). `move` and
  // `zoom` still cover non-animated changes and pan.
  map.on('move zoom zoomanim', (e: any) => {
    if (suppressViewEmit) return
    const c = e?.center ?? map.getCenter()
    const z = e?.zoom ?? map.getZoom()
    emit('view-change', { center: [c.lng, c.lat], zoom: z })
  })

  syncLayers()
})

onUnmounted(() => {
  map?.remove()
})

// Shallow watch — store always creates new Map ref on change
watch(() => props.layers, syncLayers)

// Recenter when the scenario swaps: props.center/zoom come from the active
// scenarioConfig, so a page switch with the component kept alive would
// otherwise leave us framed on the previous city.
watch(
  () => [props.center[0], props.center[1], props.zoom] as const,
  ([lng, lat, zoom]) => {
    if (!map) return
    map.setView([lat, lng], zoom)
  },
)

watch(() => props.drawMode, (mode) => {
  drawPoints.length = 0
  cursorLngLat = null
  nearClose = false
  clearDrawPreview()
  emitDrawState()
  if (map) {
    const el = map.getContainer()
    el.style.cursor = mode !== 'none' ? 'crosshair' : ''
  }
})

/** Resolve per-feature color when the layer has a `colorField` (classify output);
 *  otherwise fall back to the flat base color. */
function featureColor(feature: any, layer: LayerData, baseColor: string): string {
  if (!layer.colorField) return baseColor
  const c = feature?.properties?.[layer.colorField]
  return typeof c === 'string' && c.startsWith('#') ? c : baseColor
}

function createGeoLayer(name: string, layer: LayerData, baseColor: string) {
  const op = layer.opacity ?? 1
  return L.geoJSON(layer.geojson, {
    style: (feature: any) => {
      const c = featureColor(feature, layer, baseColor)
      return {
        color: c,
        weight: layer.type === 'line' ? 3 : 1.5,
        fillColor: c,
        fillOpacity: layer.type === 'fill' ? 0.35 * op : 0,
        opacity: 0.8 * op,
      }
    },
    pointToLayer: (feature: any, latlng: any) => {
      const c = featureColor(feature, layer, baseColor)
      return L.circleMarker(latlng, {
        radius: 5, fillColor: c,
        color: '#fff', weight: 1, fillOpacity: 0.8 * op,
        opacity: 0.8 * op,
      })
    },
    onEachFeature: (feature: any, featureLayer: any) => {
      featureLayer.on('click', () => {
        emit('feature-click', {
          id: feature.id || feature.properties?.id,
          properties: feature.properties,
          geometry: feature.geometry,
        })
      })
      const fProps = feature.properties || {}
      if (Object.keys(fProps).length) {
        featureLayer.bindPopup(featurePopupHtml(name, fProps), { maxWidth: 340, className: 'gp-leaflet-popup' })
      }
    },
  })
}

function syncLayers() {
  if (!map || !L) return

  const currentKeys = new Set(props.layers.keys())

  // Remove layers no longer in store
  for (const [name, cached] of layerCache) {
    if (!currentKeys.has(name)) {
      if (map.hasLayer(cached.group)) map.removeLayer(cached.group)
      layerCache.delete(name)
    }
  }

  for (const [name, layer] of props.layers) {
    const baseColor = layer.color.slice(0, 7)
    const cached = layerCache.get(name)

    const op = layer.opacity ?? 1

    if (cached) {
      // --- Existing layer: diff ---

      // Visibility changed?
      if (cached.visible !== layer.visible) {
        if (layer.visible && !map.hasLayer(cached.group)) {
          cached.group.addTo(map)
        } else if (!layer.visible && map.hasLayer(cached.group)) {
          map.removeLayer(cached.group)
        }
        cached.visible = layer.visible
      }

      // Data changed? Must recreate L.geoJSON (no setData in Leaflet)
      if (cached.dataRef !== layer.geojson) {
        if (map.hasLayer(cached.group)) map.removeLayer(cached.group)
        const newGroup = createGeoLayer(name, layer, baseColor)
        if (layer.visible) newGroup.addTo(map)
        cached.group = newGroup
        cached.dataRef = layer.geojson
        cached.color = layer.color
        cached.opacity = op
      }
      // Color or opacity changed but data same? Update style in-place
      else if (cached.color !== layer.color || cached.opacity !== op) {
        if (layer.colorField) {
          // Per-feature colors: re-evaluate via style function so each feature
          // keeps its own color from the `colorField` attribute.
          cached.group.setStyle((feature: any) => {
            const c = featureColor(feature, layer, baseColor)
            return {
              color: c,
              fillColor: c,
              fillOpacity: layer.type === 'fill' ? 0.35 * op : 0,
              opacity: 0.8 * op,
            }
          })
        } else {
          cached.group.setStyle({
            color: baseColor,
            fillColor: baseColor,
            fillOpacity: layer.type === 'fill' ? 0.35 * op : 0,
            opacity: 0.8 * op,
          })
        }
        cached.color = layer.color
        cached.opacity = op
      }
    } else {
      // --- New layer ---
      const geoLayer = createGeoLayer(name, layer, baseColor)
      if (layer.visible) geoLayer.addTo(map)

      layerCache.set(name, {
        group: geoLayer,
        dataRef: layer.geojson,
        color: layer.color,
        visible: layer.visible,
        opacity: op,
      })
    }
  }
}

function handleDrawClick(latlng: { lat: number; lng: number }) {
  if (props.drawMode === 'point') {
    emit('feature-drawn', { type: 'Point', coordinates: [latlng.lng, latlng.lat] })
    return
  }
  if (nearClose && drawPoints.length >= 3) {
    finishDraw()
    return
  }
  drawPoints.push([latlng.lng, latlng.lat])
  updateDrawPreview()
  emitDrawState()
}

function finishDraw() {
  if (drawPoints.length < 3) return
  const coords = [...drawPoints, drawPoints[0]]
  emit('feature-drawn', { type: 'Polygon', coordinates: [coords] })
  drawPoints.length = 0
  cursorLngLat = null
  nearClose = false
  clearDrawPreview()
  emitDrawState()
}

/** Recompute nearClose for current cursor — runs on every mousemove while
 *  drawing. Uses Leaflet's containerPoint projection so the threshold is
 *  measured in CSS pixels like on MapLibre. */
function updateCloseSnap() {
  if (!map || !cursorLngLat || props.drawMode !== 'polygon' || drawPoints.length < 3) {
    nearClose = false
    return
  }
  const first = drawPoints[0]
  const a = map.latLngToContainerPoint([first[1], first[0]])
  const b = map.latLngToContainerPoint([cursorLngLat[1], cursorLngLat[0]])
  const dx = a.x - b.x
  const dy = a.y - b.y
  nearClose = dx * dx + dy * dy <= SNAP_CLOSE_PX * SNAP_CLOSE_PX
  const el = map.getContainer()
  el.style.cursor = nearClose ? 'pointer' : 'crosshair'
}

function updateDrawPreview() {
  if (!map || !L) return
  clearDrawPreview()

  // Vertex markers. First vertex gets a bigger / green-tinted style once the
  // ring has 3+ pts so the user can target it to close the polygon.
  for (let i = 0; i < drawPoints.length; i++) {
    const [lng, lat] = drawPoints[i]
    const isFirst = i === 0 && drawPoints.length >= 3
    const fill = isFirst && nearClose ? '#22c55e' : '#FF5722'
    const m = L.circleMarker([lat, lng], {
      radius: isFirst ? (nearClose ? 9 : 6) : 4,
      fillColor: fill,
      color: '#fff',
      weight: 2,
      fillOpacity: 1,
    }).addTo(map)
    drawMarkers.push(m)
  }

  // Committed edges — polygon once 3+ pts, open line for the 2-pt draft.
  if (drawPoints.length >= 2) {
    const latLngs = drawPoints.map(([lng, lat]) => [lat, lng])
    if (drawPoints.length >= 3) {
      drawLayer = L.polygon(latLngs as any, {
        color: '#FF5722', weight: 2, dashArray: '5,5', fillColor: '#FF5722', fillOpacity: 0.2,
      }).addTo(map)
    } else {
      drawLayer = L.polyline(latLngs as any, {
        color: '#FF5722', weight: 2, dashArray: '5,5',
      }).addTo(map)
    }
  }

  // Rubber band: segment from last vertex to the current cursor. Hidden when
  // snap-to-close is armed — the first-vertex highlight carries that signal.
  if (
    cursorLngLat
    && drawPoints.length >= 1
    && props.drawMode === 'polygon'
    && !nearClose
  ) {
    const last = drawPoints[drawPoints.length - 1]
    guideLayer = L.polyline(
      [[last[1], last[0]], [cursorLngLat[1], cursorLngLat[0]]] as any,
      { color: '#FF5722', weight: 2, dashArray: '2,4', opacity: 0.7 },
    ).addTo(map)
  }
}

function clearDrawPreview() {
  if (drawLayer) { map?.removeLayer(drawLayer); drawLayer = null }
  if (guideLayer) { map?.removeLayer(guideLayer); guideLayer = null }
  for (const m of drawMarkers) map?.removeLayer(m)
  drawMarkers = []
}

function emitDrawState() {
  const vertexCount = drawPoints.length
  let measure: DrawMeasure | null = null
  if (props.drawMode === 'polygon') {
    if (vertexCount >= 3) {
      const m2 = polygonAreaM2(drawPoints)
      if (m2 > 0) measure = { type: 'area', text: formatArea(m2) }
    } else if (vertexCount === 2) {
      const m = polylineLengthM(drawPoints)
      if (m > 0) measure = { type: 'length', text: formatLength(m) }
    }
  }
  emit('draw-state', { vertexCount, measure })
}

function undoDrawPoint() {
  if (props.drawMode !== 'polygon' || drawPoints.length === 0) return
  drawPoints.pop()
  updateCloseSnap()
  updateDrawPreview()
  emitDrawState()
}

function cancelDraw() {
  if (drawPoints.length === 0) return
  drawPoints.length = 0
  cursorLngLat = null
  nearClose = false
  clearDrawPreview()
  emitDrawState()
}

/** Close the polygon if it has enough vertices. No-op otherwise — used by
 *  keyboard / toolbar handlers that don't know local state. */
function requestFinish() {
  if (props.drawMode === 'polygon' && drawPoints.length >= 3) finishDraw()
}

function invalidateSize() {
  map?.invalidateSize()
}

function fitBounds(bbox: [number, number, number, number]) {
  map?.fitBounds([[bbox[1], bbox[0]], [bbox[3], bbox[2]]])
}

/**
 * Apply a peer map's view without re-emitting `view-change`. Called by the
 * host (DualMapView) to keep split-mode canvases aligned.
 */
function setView(center: [number, number], zoom: number) {
  if (!map) return
  suppressViewEmit = true
  try {
    map.setView([center[1], center[0]], zoom, { animate: false })
  } finally {
    requestAnimationFrame(() => { suppressViewEmit = false })
  }
}

/**
 * Drop every cached GeoJSON group and forget diff state. Keeps the map
 * instance + tile layer + draw preview so the host can repopulate layers
 * without remount. Call on scenario switch.
 */
function clearCache() {
  if (map) {
    for (const [, cached] of layerCache) {
      if (map.hasLayer(cached.group)) map.removeLayer(cached.group)
    }
  }
  layerCache.clear()
  drawPoints.length = 0
  cursorLngLat = null
  nearClose = false
  clearDrawPreview()
  emitDrawState()
}

defineExpose({
  fitBounds,
  invalidateSize,
  clearCache,
  setView,
  undoDrawPoint,
  cancelDraw,
  requestFinish,
})
</script>

<template>
  <div ref="container" class="gp-map-container" />
</template>

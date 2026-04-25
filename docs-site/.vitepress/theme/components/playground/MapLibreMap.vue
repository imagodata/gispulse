<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch } from 'vue'
import type { LayerData } from '../../composables/usePlaygroundStore'
import { featurePopupHtml } from './featurePopup'
import { polygonAreaM2, polylineLengthM, formatArea, formatLength } from './geo'
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
let maplibregl: any = null
let currentPopup: any = null
const drawPoints: [number, number][] = []

/** Live cursor while drawing — drives the rubber-band guide + close-snap hit. */
let cursorLngLat: [number, number] | null = null
/** True when the cursor is within {@link SNAP_CLOSE_PX} of the first vertex
 *  (only meaningful for polygons with >= 3 vertices). A click in this state
 *  closes the polygon instead of appending a new vertex. */
let nearClose = false
/** Pixel threshold for snap-to-close; roughly the size of the first-vertex
 *  marker so pointing at it reliably triggers the close affordance. */
const SNAP_CLOSE_PX = 18

/**
 * Set true once the basemap style has finished loading. Replaces the previous
 * `map.isStyleLoaded()` gate, which can return false transiently after the
 * initial load (theme switch, basemap tile reload, etc.) and silently dropped
 * sync calls — leaving step layers missing on the map after a playback step.
 */
let mapReady = false

// Guards the `move` handler while `setView` applies a programmatic pose from
// the peer map, preventing A→B→A feedback in split-mode sync.
let suppressViewEmit = false

/**
 * Leaflet-minus-MapLibre zoom offset: MapLibre's Transform uses a 512-px
 * internal tileSize, Leaflet's default CRS uses 256 — so at the same numeric
 * zoom MapLibre's worldSize is 2× Leaflet's and the view is one level more
 * zoomed in. We treat every external zoom (props, sync events) as Leaflet-
 * convention and subtract 1 before touching MapLibre's API.
 */
const LEAFLET_ZOOM_OFFSET = 1
const toInternalZoom = (z: number) => z - LEAFLET_ZOOM_OFFSET
const toExternalZoom = (z: number) => z + LEAFLET_ZOOM_OFFSET

/** Track previous layer state for diffing */
let prevState = new Map<string, { dataRef: any; color: string; visible: boolean; opacity: number; colorField?: string }>()

/** Build MapLibre paint value: solid color, or `coalesce(['get', field], fallback)`
 *  when the layer has a per-feature color attribute (classify output). */
function colorExpr(color: string, colorField?: string): any {
  return colorField ? ['coalesce', ['get', colorField], color] : color
}

onMounted(async () => {
  maplibregl = await import('maplibre-gl')
  await import('maplibre-gl/dist/maplibre-gl.css')

  if (!container.value) return

  const isDark = document.documentElement.classList.contains('dark')
  const basemapUrl = isDark
    ? 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png'
    : 'https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}@2x.png'

  map = new maplibregl.Map({
    container: container.value,
    style: {
      version: 8,
      sources: {
        carto: {
          type: 'raster',
          tiles: [
            basemapUrl.replace('{s}', 'a'),
            basemapUrl.replace('{s}', 'b'),
            basemapUrl.replace('{s}', 'c'),
          ],
          tileSize: 256,
          attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/">CARTO</a>',
        },
      },
      layers: [{ id: 'carto', type: 'raster', source: 'carto' }],
    },
    center: props.center,
    zoom: toInternalZoom(props.zoom),
    minZoom: -1,
    attributionControl: true,
  })

  map.addControl(new maplibregl.NavigationControl({ showCompass: true }), 'top-right')
  map.addControl(new maplibregl.ScaleControl({ maxWidth: 150, unit: 'metric' }), 'bottom-left')

  map.on('move', () => {
    if (suppressViewEmit) return
    const c = map.getCenter()
    emit('view-change', { center: [c.lng, c.lat], zoom: toExternalZoom(map.getZoom()) })
  })

  map.on('load', () => {
    mapReady = true
    let hoveredSource: string | null = null
    map.on('mousemove', (ev: any) => {
      if (props.drawMode !== 'none') {
        // While drawing: feed rubber band + snap-to-close; skip feature hover.
        cursorLngLat = [ev.lngLat.lng, ev.lngLat.lat]
        updateCloseSnap()
        updateDrawSource()
        return
      }
      const fs = map.queryRenderedFeatures(ev.point)
      const hit = fs.find((f: any) => f.source.startsWith('gp-') && f.source !== 'gp-draw' && f.source !== 'gp-draw-guide')

      if (hoveredSource && map.getLayer(`${hoveredSource}-highlight`)) {
        map.setFilter(`${hoveredSource}-highlight`, ['==', ['id'], ''])
      }

      if (hit) {
        map.getCanvas().style.cursor = 'pointer'
        hoveredSource = hit.source
        if (map.getLayer(`${hoveredSource}-highlight`)) {
          map.setFilter(`${hoveredSource}-highlight`, ['==', ['id'], hit.id ?? ''])
        }
      } else {
        map.getCanvas().style.cursor = ''
        hoveredSource = null
      }
    })

    // Clear rubber band when the mouse leaves the canvas — otherwise the guide
    // stays anchored at the last known position even after focus moves away.
    map.on('mouseout', () => {
      if (props.drawMode === 'none') return
      cursorLngLat = null
      nearClose = false
      updateDrawSource()
    })

    syncLayers()
    addDrawLayer()
  })

  map.on('click', (e: any) => {
    if (props.drawMode !== 'none') {
      handleDrawClick(e.lngLat)
      return
    }

    const features = map.queryRenderedFeatures(e.point)
    const geoFeature = features.find((f: any) => f.source.startsWith('gp-'))
    if (geoFeature) {
      const layerName = geoFeature.source.replace('gp-', '')
      const featureProps = geoFeature.properties || {}

      if (currentPopup) currentPopup.remove()
      currentPopup = new maplibregl.Popup({ closeButton: true, maxWidth: '340px', className: 'gp-maplibre-popup' })
        .setLngLat(e.lngLat)
        .setHTML(featurePopupHtml(layerName, featureProps))
        .addTo(map)

      emit('feature-click', {
        id: geoFeature.id,
        properties: featureProps,
        geometry: geoFeature.geometry,
      })
    }
  })

  map.on('dblclick', () => {
    if (props.drawMode === 'polygon' && drawPoints.length >= 3) {
      finishDraw()
    }
  })
})

onUnmounted(() => {
  if (currentPopup) currentPopup.remove()
  map?.remove()
  mapReady = false
})

// Shallow watch — only fires when the Map reference changes (store always creates new Map)
watch(() => props.layers, syncLayers)

// Recenter when the scenario swaps: props.center/zoom come from the active
// scenarioConfig, so a page switch with the component kept alive would
// otherwise leave us framed on the previous city.
watch(
  () => [props.center[0], props.center[1], props.zoom] as const,
  ([lng, lat, zoom]) => {
    if (!map) return
    map.jumpTo({ center: [lng, lat], zoom: toInternalZoom(zoom) })
  },
)

watch(() => props.drawMode, (mode) => {
  drawPoints.length = 0
  cursorLngLat = null
  nearClose = false
  updateDrawSource()
  emitDrawState()
  if (currentPopup) { currentPopup.remove(); currentPopup = null }
  if (map) {
    map.getCanvas().style.cursor = mode !== 'none' ? 'crosshair' : ''
  }
})

/** Layer suffix list for cleanup */
const LAYER_SUFFIXES = ['-fill', '-outline', '-highlight', '-line', '-casing', '-circle']

function syncLayers() {
  if (!map || !mapReady) return

  const currentKeys = new Set(props.layers.keys())

  // Remove sources no longer in store
  for (const [srcKey] of prevState) {
    if (!currentKeys.has(srcKey)) {
      const srcId = `gp-${srcKey}`
      for (const suffix of LAYER_SUFFIXES) {
        if (map.getLayer(srcId + suffix)) map.removeLayer(srcId + suffix)
      }
      if (map.getSource(srcId)) map.removeSource(srcId)
      prevState.delete(srcKey)
    }
  }

  for (const [name, layer] of props.layers) {
    const srcId = `gp-${name}`
    const prev = prevState.get(name)
    const vis = layer.visible ? 'visible' : 'none'
    const op = layer.opacity ?? 1
    const isBuilding = name.includes('batiment')

    const paintFor = (kind: 'fill' | 'outline' | 'line' | 'casing' | 'circle') => {
      switch (kind) {
        case 'fill': return (isBuilding ? 0.55 : 0.35) * op
        case 'outline': return 0.9 * op
        case 'line': return 0.85 * op
        case 'casing': return 0.4 * op
        case 'circle': return 0.9 * op
      }
    }

    if (prev) {
      // --- Existing layer: diff and update only what changed ---

      // Data changed?
      if (prev.dataRef !== layer.geojson) {
        map.getSource(srcId)?.setData(layer.geojson)
      }

      // Visibility changed?
      if (prev.visible !== layer.visible) {
        for (const suffix of LAYER_SUFFIXES) {
          if (map.getLayer(srcId + suffix))
            map.setLayoutProperty(srcId + suffix, 'visibility', vis)
        }
      }

      // Color or color-field changed?
      if (prev.color !== layer.color || prev.colorField !== layer.colorField) {
        const expr = colorExpr(layer.color, layer.colorField)
        if (map.getLayer(`${srcId}-fill`)) {
          map.setPaintProperty(`${srcId}-fill`, 'fill-color', expr)
          map.setPaintProperty(`${srcId}-outline`, 'line-color', expr)
        }
        if (map.getLayer(`${srcId}-line`))
          map.setPaintProperty(`${srcId}-line`, 'line-color', expr)
        if (map.getLayer(`${srcId}-circle`))
          map.setPaintProperty(`${srcId}-circle`, 'circle-color', expr)
      }

      // Opacity changed?
      if (prev.opacity !== op) {
        if (map.getLayer(`${srcId}-fill`)) {
          map.setPaintProperty(`${srcId}-fill`, 'fill-opacity', paintFor('fill'))
          map.setPaintProperty(`${srcId}-outline`, 'line-opacity', paintFor('outline'))
        }
        if (map.getLayer(`${srcId}-casing`))
          map.setPaintProperty(`${srcId}-casing`, 'line-opacity', paintFor('casing'))
        if (map.getLayer(`${srcId}-line`))
          map.setPaintProperty(`${srcId}-line`, 'line-opacity', paintFor('line'))
        if (map.getLayer(`${srcId}-circle`))
          map.setPaintProperty(`${srcId}-circle`, 'circle-opacity', paintFor('circle'))
      }
    } else {
      // --- New layer: create source + layers ---
      map.addSource(srcId, { type: 'geojson', data: layer.geojson })

      const fillExpr = colorExpr(layer.color, layer.colorField)
      if (layer.type === 'fill') {
        map.addLayer({
          id: `${srcId}-fill`, type: 'fill', source: srcId,
          layout: { visibility: vis },
          paint: {
            'fill-color': fillExpr,
            'fill-opacity': paintFor('fill'),
            'fill-outline-color': fillExpr,
          },
        })
        map.addLayer({
          id: `${srcId}-outline`, type: 'line', source: srcId,
          layout: { visibility: vis },
          paint: {
            'line-color': fillExpr,
            'line-width': isBuilding ? 1.2 : 1.8,
            'line-opacity': paintFor('outline'),
          },
        })
        map.addLayer({
          id: `${srcId}-highlight`, type: 'fill', source: srcId,
          layout: { visibility: vis },
          paint: { 'fill-color': fillExpr, 'fill-opacity': 0.6 },
          filter: ['==', ['id'], ''],
        })
      } else if (layer.type === 'line') {
        map.addLayer({
          id: `${srcId}-casing`, type: 'line', source: srcId,
          layout: { visibility: vis },
          paint: { 'line-color': '#fff', 'line-width': 5, 'line-opacity': paintFor('casing') },
        })
        map.addLayer({
          id: `${srcId}-line`, type: 'line', source: srcId,
          layout: { visibility: vis },
          paint: { 'line-color': fillExpr, 'line-width': 3, 'line-opacity': paintFor('line') },
        })
      } else if (layer.type === 'circle') {
        map.addLayer({
          id: `${srcId}-circle`, type: 'circle', source: srcId,
          layout: { visibility: vis },
          paint: {
            'circle-color': fillExpr,
            'circle-radius': 6,
            'circle-stroke-width': 2,
            'circle-stroke-color': '#fff',
            'circle-opacity': paintFor('circle'),
          },
        })
      }
    }

    // Update prev state
    prevState.set(name, { dataRef: layer.geojson, color: layer.color, visible: layer.visible, opacity: op, colorField: layer.colorField })
  }
}

function addDrawLayer() {
  if (!map) return
  map.addSource('gp-draw', {
    type: 'geojson',
    data: { type: 'FeatureCollection', features: [] },
  })
  // Rubber-band source: a single LineString from the last committed vertex to
  // the current cursor position. Kept separate from `gp-draw` so we can style
  // it independently (dashed, lighter) and skip it in feature queries.
  map.addSource('gp-draw-guide', {
    type: 'geojson',
    data: { type: 'FeatureCollection', features: [] },
  })
  map.addLayer({
    id: 'gp-draw-fill', type: 'fill', source: 'gp-draw',
    filter: ['==', ['geometry-type'], 'Polygon'],
    paint: { 'fill-color': '#FF5722', 'fill-opacity': 0.2 },
  })
  map.addLayer({
    id: 'gp-draw-line', type: 'line', source: 'gp-draw',
    filter: ['!=', ['geometry-type'], 'Point'],
    paint: { 'line-color': '#FF5722', 'line-width': 2.5, 'line-dasharray': [3, 2] },
  })
  map.addLayer({
    id: 'gp-draw-guide-line', type: 'line', source: 'gp-draw-guide',
    paint: {
      'line-color': '#FF5722',
      'line-width': 2,
      'line-dasharray': [2, 2],
      'line-opacity': 0.7,
    },
  })
  // Non-first vertices — small solid markers.
  map.addLayer({
    id: 'gp-draw-points', type: 'circle', source: 'gp-draw',
    filter: ['all', ['==', ['geometry-type'], 'Point'], ['!=', ['get', 'role'], 'first']],
    paint: {
      'circle-color': '#FF5722', 'circle-radius': 4,
      'circle-stroke-width': 2, 'circle-stroke-color': '#fff',
    },
  })
  // First vertex — bigger marker that turns green when snap-to-close is
  // armed, so the user knows a click will close the polygon instead of
  // appending a new sommet.
  map.addLayer({
    id: 'gp-draw-first', type: 'circle', source: 'gp-draw',
    filter: ['all', ['==', ['geometry-type'], 'Point'], ['==', ['get', 'role'], 'first']],
    paint: {
      'circle-color': ['case', ['>', ['coalesce', ['get', 'nearClose'], 0], 0], '#22c55e', '#FF5722'],
      'circle-radius': ['case', ['>', ['coalesce', ['get', 'nearClose'], 0], 0], 9, 6],
      'circle-stroke-width': 2,
      'circle-stroke-color': '#fff',
    },
  })
}

function handleDrawClick(lngLat: { lng: number; lat: number }) {
  if (props.drawMode === 'point') {
    emit('feature-drawn', { type: 'Point', coordinates: [lngLat.lng, lngLat.lat] })
    return
  }
  // Snap-to-close: clicking near the first vertex with 3+ pts closes the ring.
  if (nearClose && drawPoints.length >= 3) {
    finishDraw()
    return
  }
  drawPoints.push([lngLat.lng, lngLat.lat])
  updateDrawSource()
  emitDrawState()
}

function finishDraw() {
  if (drawPoints.length < 3) return
  const coords = [...drawPoints, drawPoints[0]]
  emit('feature-drawn', { type: 'Polygon', coordinates: [coords] })
  drawPoints.length = 0
  cursorLngLat = null
  nearClose = false
  updateDrawSource()
  emitDrawState()
}

/** Recompute `nearClose` flag for the current cursor position. Cheap — runs
 *  on every `mousemove` while drawing. */
function updateCloseSnap() {
  if (!map || !cursorLngLat || props.drawMode !== 'polygon' || drawPoints.length < 3) {
    nearClose = false
    return
  }
  const a = map.project(drawPoints[0] as any)
  const b = map.project(cursorLngLat as any)
  const dx = a.x - b.x
  const dy = a.y - b.y
  nearClose = dx * dx + dy * dy <= SNAP_CLOSE_PX * SNAP_CLOSE_PX
  map.getCanvas().style.cursor = nearClose ? 'pointer' : 'crosshair'
}

function updateDrawSource() {
  if (!map) return
  const src = map.getSource('gp-draw')
  const guideSrc = map.getSource('gp-draw-guide')
  if (!src) return

  const features: any[] = []
  for (let i = 0; i < drawPoints.length; i++) {
    features.push({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: drawPoints[i] },
      properties: {
        role: i === 0 && drawPoints.length >= 3 ? 'first' : 'vertex',
        nearClose: i === 0 && nearClose ? 1 : 0,
      },
    })
  }
  if (drawPoints.length >= 2) {
    features.push({
      type: 'Feature',
      geometry: {
        type: drawPoints.length >= 3 ? 'Polygon' : 'LineString',
        coordinates: drawPoints.length >= 3 ? [[...drawPoints, drawPoints[0]]] : drawPoints,
      },
      properties: {},
    })
  }
  src.setData({ type: 'FeatureCollection', features })

  // Rubber band: segment from last vertex (or first, if snapping to close) to
  // the current cursor. Suppressed when snap-to-close is armed — the user sees
  // the first-vertex marker highlight instead, which communicates the close.
  if (guideSrc) {
    const showGuide = cursorLngLat
      && drawPoints.length >= 1
      && props.drawMode === 'polygon'
      && !nearClose
    guideSrc.setData({
      type: 'FeatureCollection',
      features: showGuide
        ? [{
            type: 'Feature',
            geometry: {
              type: 'LineString',
              coordinates: [drawPoints[drawPoints.length - 1], cursorLngLat!],
            },
            properties: {},
          }]
        : [],
    })
  }
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
  updateDrawSource()
  emitDrawState()
}

function cancelDraw() {
  if (drawPoints.length === 0) return
  drawPoints.length = 0
  cursorLngLat = null
  nearClose = false
  updateDrawSource()
  emitDrawState()
}

/** Finish the current polygon if it has enough vertices. No-op otherwise —
 *  used by keyboard / toolbar handlers that don't know the local state. */
function requestFinish() {
  if (props.drawMode === 'polygon' && drawPoints.length >= 3) finishDraw()
}

function fitBounds(bbox: [number, number, number, number]) {
  map?.fitBounds(bbox, { padding: 40 })
}

/**
 * Drop every data layer/source created by this component and forget the
 * diff cache. Keeps the map instance + basemap + draw source so the host
 * can repopulate layers without remount. Call on scenario switch.
 */
function clearCache() {
  if (currentPopup) { currentPopup.remove(); currentPopup = null }
  if (map && mapReady) {
    for (const [key] of prevState) {
      const srcId = `gp-${key}`
      for (const suffix of LAYER_SUFFIXES) {
        if (map.getLayer(srcId + suffix)) map.removeLayer(srcId + suffix)
      }
      if (map.getSource(srcId)) map.removeSource(srcId)
    }
  }
  prevState.clear()
  drawPoints.length = 0
  cursorLngLat = null
  nearClose = false
  updateDrawSource()
  emitDrawState()
}

function invalidateSize() {
  map?.resize()
}

/**
 * Apply a peer map's view without re-emitting `view-change`. Called by the
 * host (DualMapView) to keep split-mode canvases aligned.
 */
function setView(center: [number, number], zoom: number) {
  if (!map) return
  suppressViewEmit = true
  try {
    map.jumpTo({ center, zoom: toInternalZoom(zoom) })
  } finally {
    // MapLibre fires `move` synchronously inside jumpTo; clear on next frame
    // so any async straggler is still guarded.
    requestAnimationFrame(() => { suppressViewEmit = false })
  }
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

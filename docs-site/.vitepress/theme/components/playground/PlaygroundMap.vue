<script setup lang="ts">
import { defineAsyncComponent, shallowRef } from 'vue'
import type { LayerData } from '../../composables/usePlaygroundStore'
import type { DrawMeasure } from './geo'

const props = defineProps<{
  engine: 'maplibre' | 'leaflet'
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

const mapRef = shallowRef<any>(null)

const MapLibreMap = defineAsyncComponent(() => import('./MapLibreMap.vue'))
const LeafletMap = defineAsyncComponent(() => import('./LeafletMap.vue'))

function fitBounds(bbox: [number, number, number, number]) {
  mapRef.value?.fitBounds(bbox)
}

function invalidateSize() {
  mapRef.value?.invalidateSize()
}

function clearCache() {
  mapRef.value?.clearCache?.()
}

function setView(center: [number, number], zoom: number) {
  mapRef.value?.setView?.(center, zoom)
}

function undoDrawPoint() {
  mapRef.value?.undoDrawPoint?.()
}

function cancelDraw() {
  mapRef.value?.cancelDraw?.()
}

function requestFinish() {
  mapRef.value?.requestFinish?.()
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
  <div class="gp-map-wrapper">
    <component
      :is="engine === 'maplibre' ? MapLibreMap : LeafletMap"
      ref="mapRef"
      :layers="props.layers"
      :center="props.center"
      :zoom="props.zoom"
      :draw-mode="props.drawMode"
      :selected-feature-id="props.selectedFeatureId"
      @feature-click="(f: any) => emit('feature-click', f)"
      @feature-drawn="(g: any) => emit('feature-drawn', g)"
      @view-change="(v: any) => emit('view-change', v)"
      @draw-state="(s: any) => emit('draw-state', s)"
    />
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { DrawMeasure } from './geo'

type DrawMode = 'none' | 'polygon' | 'point'

const props = defineProps<{
  modelValue: DrawMode
  /** Number of vertices placed so far (0 when idle / in point mode). */
  vertexCount?: number
  /** Live measurement label shown while drawing (e.g. "2.35 ha"). */
  measure?: DrawMeasure | null
}>()

const emit = defineEmits<{
  'update:modelValue': [mode: DrawMode]
  'undo': []
  'finish': []
}>()

const count = computed(() => props.vertexCount ?? 0)
const canUndo = computed(() => props.modelValue === 'polygon' && count.value > 0)
const canFinish = computed(() => props.modelValue === 'polygon' && count.value >= 3)

function toggle(mode: Exclude<DrawMode, 'none'>) {
  emit('update:modelValue', props.modelValue === mode ? 'none' : mode)
}
</script>

<template>
  <div class="gp-draw-toolbar">
    <div class="gp-draw-row">
      <button
        :class="['gp-draw-btn', { active: modelValue === 'polygon' }]"
        title="Dessiner un polygone — Entree pour terminer, Z pour annuler le dernier point, Echap pour annuler"
        @click="toggle('polygon')"
      >
        Polygon
      </button>
      <button
        :class="['gp-draw-btn', { active: modelValue === 'point' }]"
        title="Placer un point"
        @click="toggle('point')"
      >
        Point
      </button>
      <button
        v-if="canUndo"
        class="gp-draw-btn undo"
        title="Retirer le dernier sommet (Z)"
        @click="emit('undo')"
      >
        Undo
      </button>
      <button
        v-if="canFinish"
        class="gp-draw-btn finish"
        title="Terminer le polygone (Entree ou double-clic)"
        @click="emit('finish')"
      >
        Terminer
      </button>
      <button
        v-if="modelValue !== 'none'"
        class="gp-draw-btn cancel"
        title="Annuler (Echap)"
        @click="emit('update:modelValue', 'none')"
      >
        Annuler
      </button>
    </div>

    <div
      v-if="modelValue === 'polygon' && count > 0"
      class="gp-draw-hint"
    >
      <span class="gp-draw-count">{{ count }} sommet{{ count > 1 ? 's' : '' }}</span>
      <span v-if="measure" class="gp-draw-measure">
        {{ measure.type === 'area' ? 'aire' : 'longueur' }} : {{ measure.text }}
      </span>
      <span class="gp-draw-keys">
        <kbd>Entree</kbd> terminer
        <kbd>Z</kbd> undo
        <kbd>Echap</kbd> annuler
      </span>
    </div>
  </div>
</template>

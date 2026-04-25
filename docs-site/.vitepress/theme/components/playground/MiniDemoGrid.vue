<script setup lang="ts">
/**
 * MiniDemoGrid — renders a responsive grid of MiniDemo cards.
 *
 * Accepts a list of scenario slugs. Each card is lazy-mounted independently
 * via MiniDemo's IntersectionObserver, so even 6 cards on a page stay
 * cheap: MapLibre instances are created one-by-one as the user scrolls.
 *
 * Usage:
 *   <MiniDemoGrid :slugs="['flood-risk', 'data-quality', 'accessibility']" />
 *   <MiniDemoGrid />   <!-- auto-load every scenario from manifest.json -->
 */
import { ref, onMounted, computed } from 'vue'
import MiniDemo from './MiniDemo.vue'
import { useStaticPlayground } from '../../composables/useStaticPlayground'

const props = withDefaults(
  defineProps<{
    slugs?: string[]
    height?: string
    compact?: boolean
    minWidth?: string
  }>(),
  {
    height: '240px',
    compact: true,
    minWidth: '280px',
  },
)

const allSlugs = ref<string[]>([])

onMounted(async () => {
  if (props.slugs && props.slugs.length) return
  try {
    const api = useStaticPlayground()
    const manifest = await api.loadManifest()
    allSlugs.value = manifest.scenarios.map((s) => s.slug)
  } catch (err) {
    console.warn('[MiniDemoGrid] manifest load failed', err)
  }
})

const resolved = computed(() =>
  props.slugs && props.slugs.length ? props.slugs : allSlugs.value,
)
</script>

<template>
  <div
    class="gp-mini-grid"
    :style="{ '--mini-min': minWidth } as any"
  >
    <MiniDemo
      v-for="slug in resolved"
      :key="slug"
      :scenario="slug"
      :height="height"
      :compact="compact"
    />
  </div>
</template>

<style scoped>
.gp-mini-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(var(--mini-min, 280px), 1fr));
  gap: 0.8rem;
  margin: 1rem 0 1.5rem;
}
</style>

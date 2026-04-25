<script setup lang="ts">
defineProps<{
  title: string
  difficulty: 'debutant' | 'intermediaire' | 'avance'
  description: string
  capabilities: string[]
  link: string
  mode?: string
}>()

const badgeColor: Record<string, string> = {
  debutant: 'var(--gp-green)',
  intermediaire: 'var(--gp-orange)',
  avance: 'var(--gp-red)',
}

const badgeLabel: Record<string, string> = {
  debutant: 'Debutant',
  intermediaire: 'Intermediaire',
  avance: 'Avance',
}
</script>

<template>
  <a :href="link" class="gp-scenario-card">
    <div class="gp-scenario-card-header">
      <span class="gp-difficulty-badge" :style="{ background: badgeColor[difficulty] }">
        {{ badgeLabel[difficulty] }}
      </span>
      <span v-if="mode" class="gp-mode-badge">{{ mode }}</span>
    </div>
    <h3>{{ title }}</h3>
    <p>{{ description }}</p>
    <div class="gp-scenario-caps">
      <code v-for="cap in capabilities" :key="cap">{{ cap }}</code>
    </div>
  </a>
</template>

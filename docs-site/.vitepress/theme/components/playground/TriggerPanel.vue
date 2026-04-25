<script setup lang="ts">
import { usePlaygroundStore } from '../../composables/usePlaygroundStore'

const store = usePlaygroundStore()
</script>

<template>
  <div class="gp-trigger-panel">
    <div class="gp-trigger-header">
      <span class="gp-trigger-title">Triggers actifs</span>
      <span class="gp-trigger-count">{{ store.state.triggers.length }}</span>
    </div>

    <div v-if="!store.state.triggers.length" class="gp-trigger-empty">
      Aucun trigger charge
    </div>

    <div v-for="t in store.state.triggers" :key="t.id" class="gp-trigger-item">
      <div class="gp-trigger-name">{{ t.name }}</div>
      <div class="gp-trigger-meta">
        <span class="gp-trigger-type-badge">{{ t.trigger_type }}</span>
        <span class="gp-trigger-event">{{ t.event }}</span>
      </div>
    </div>

    <div
      v-if="store.state.firedMatched !== null"
      class="gp-trigger-outcome"
      :class="store.state.firedMatched ? 'gp-outcome-match' : 'gp-outcome-nomatch'"
    >
      <span class="gp-outcome-badge">
        {{ store.state.firedMatched ? 'MATCH' : 'NO MATCH' }}
      </span>
      <span class="gp-outcome-summary">{{ store.state.firedSummary }}</span>
    </div>

    <div v-if="store.state.firedResults.length" class="gp-trigger-fired">
      <div class="gp-trigger-fired-title">Cascade declenchee</div>
      <div v-for="(r, i) in store.state.firedResults" :key="i" class="gp-trigger-action">
        <span
          class="gp-action-badge"
          :class="`gp-action-${r.action_type?.toLowerCase()}`"
        >
          {{ r.action_type }}
        </span>
        <span class="gp-action-detail">{{ r.config?.message || r.config?.field || '' }}</span>
      </div>
    </div>

    <div v-if="store.state.drawMode !== 'none'" class="gp-trigger-hint">
      Dessinez un feature — prediccats geom + attr evalues localement, cascade affichee sur MATCH.
    </div>
  </div>
</template>

<style scoped>
.gp-trigger-outcome {
  margin-top: 0.6rem;
  padding: 0.5rem 0.7rem;
  border-radius: 6px;
  display: flex;
  gap: 0.5rem;
  align-items: center;
  font-size: 0.8rem;
  border: 1px solid transparent;
}
.gp-outcome-match {
  background: rgba(229, 57, 53, 0.12);
  border-color: rgba(229, 57, 53, 0.4);
  color: #b71c1c;
}
.gp-outcome-nomatch {
  background: rgba(46, 125, 50, 0.12);
  border-color: rgba(46, 125, 50, 0.4);
  color: #1b5e20;
}
.gp-outcome-badge {
  font-weight: 700;
  letter-spacing: 0.03em;
}
.gp-outcome-summary {
  flex: 1;
  opacity: 0.9;
}
</style>

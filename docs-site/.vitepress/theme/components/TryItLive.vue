<script setup lang="ts">
import { ref, computed } from 'vue'

const props = defineProps<{
  endpoint: string
  method?: string
  body?: string
  description: string
  apiBase?: string
}>()

const DEMO_API = props.apiBase || 'https://demo.gispulse.dev'
const DEMO_KEY = 'demo-playground-key'

const response = ref<string>('')
const loading = ref(false)
const error = ref('')
const executed = ref(false)
const statusCode = ref(0)

const fullUrl = computed(() => `${DEMO_API}${props.endpoint}`)

async function execute() {
  loading.value = true
  error.value = ''
  response.value = ''
  executed.value = true
  statusCode.value = 0

  try {
    const opts: RequestInit = {
      method: props.method || 'GET',
      headers: {
        'Content-Type': 'application/json',
        'X-API-Key': DEMO_KEY,
      },
    }
    if (props.body && props.method !== 'GET') {
      opts.body = props.body
    }

    const res = await fetch(fullUrl.value, opts)
    statusCode.value = res.status
    const data = await res.json()
    response.value = JSON.stringify(data, null, 2)
  } catch (e: any) {
    error.value = e.message || 'Connection failed — demo server may be offline'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="gp-tryit">
    <div class="gp-tryit-header">
      <span class="gp-tryit-badge">Live Demo</span>
      <span class="gp-tryit-desc">{{ description }}</span>
    </div>
    <div class="gp-tryit-request">
      <code class="gp-tryit-method">{{ method || 'GET' }}</code>
      <code class="gp-tryit-url">{{ endpoint }}</code>
      <button class="gp-tryit-btn" :disabled="loading" @click="execute">
        {{ loading ? 'Loading...' : 'Execute' }}
      </button>
    </div>
    <div v-if="body && method !== 'GET'" class="gp-tryit-body">
      <details>
        <summary>Request body</summary>
        <pre><code>{{ body }}</code></pre>
      </details>
    </div>
    <div v-if="executed" class="gp-tryit-response">
      <div v-if="error" class="gp-tryit-error">{{ error }}</div>
      <template v-else-if="response">
        <div class="gp-tryit-status" :class="statusCode < 400 ? 'gp-tryit-status-ok' : 'gp-tryit-status-err'">
          HTTP {{ statusCode }}
        </div>
        <pre><code>{{ response }}</code></pre>
      </template>
      <div v-else-if="loading" class="gp-tryit-loading">Waiting for response...</div>
    </div>
  </div>
</template>

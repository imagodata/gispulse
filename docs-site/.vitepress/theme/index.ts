import { defineAsyncComponent } from 'vue'
import DefaultTheme from 'vitepress/theme'
import './custom.css'
import ScenarioCard from './components/ScenarioCard.vue'
import TryItLive from './components/TryItLive.vue'
import TemplatesGallery from './components/TemplatesGallery.vue'

// Heavy playground components are loaded only when a page actually uses them.
// DualMapView pulls MapLibre GL + Leaflet (~1.5 MB combined) — eager mounting
// taxed every docs page (API ref, blog, guides) that never renders a map.
// MiniDemo* lean on the same map pipeline. defineAsyncComponent keeps them
// out of the main chunk; VitePress wraps the consumer in <ClientOnly> via
// the playground markdown pages, which is enough to avoid SSR mismatches.
const DualMapView = defineAsyncComponent(
  () => import('./components/playground/DualMapView.vue'),
)
const MiniDemo = defineAsyncComponent(
  () => import('./components/playground/MiniDemo.vue'),
)
const MiniDemoGrid = defineAsyncComponent(
  () => import('./components/playground/MiniDemoGrid.vue'),
)

export default {
  extends: DefaultTheme,
  enhanceApp({ app }) {
    app.component('ScenarioCard', ScenarioCard)
    app.component('TryItLive', TryItLive)
    app.component('DualMapView', DualMapView)
    app.component('MiniDemo', MiniDemo)
    app.component('MiniDemoGrid', MiniDemoGrid)
    app.component('TemplatesGallery', TemplatesGallery)
  },
}

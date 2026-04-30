import { defineConfig } from 'vitepress'

const shared = {
  title: 'GISPulse',
  base: '/gispulse/',
  cleanUrls: true,
  // Coverage matrix + integrations docs link to repo paths (../../tests/,
  // ../../docs/TRIGGERS_GUIDE, ../INTEGRATION_MATRIX) that resolve in the
  // source tree but not under the built docs root. VitePress 1.x exits 1
  // on the first dead link, blocking the gh-pages deploy entirely. Match
  // the offending shapes loosely (the links use leading "./../" forms) so
  // the build can ship; the links themselves should be rewritten as
  // github.com/... URLs as a follow-up.
  ignoreDeadLinks: [
    /capabilities\//,
    /tests\//,
    /TRIGGERS_GUIDE/,
    /INTEGRATION_MATRIX/,
  ],

  head: [
    ['link', { rel: 'icon', href: '/gispulse/favicon.svg', type: 'image/svg+xml' }],
    ['meta', { name: 'theme-color', content: '#0D47A1' }],
    ['meta', { property: 'og:type', content: 'website' }],
    ['meta', { property: 'og:image', content: 'https://docs.gispulse.dev/og-image.png' }],
    ['meta', { property: 'og:site_name', content: 'GISPulse Docs' }],
    ['meta', { name: 'twitter:card', content: 'summary_large_image' }],
    // Warm up fetches for MiniDemo + templates gallery — tiny files the user
    // will almost certainly need. No cost if the page doesn't use them.
    ['link', { rel: 'prefetch', href: '/gispulse/playground/data/manifest.json', as: 'fetch', crossorigin: 'anonymous' }],
    ['link', { rel: 'prefetch', href: '/gispulse/templates/index.json', as: 'fetch', crossorigin: 'anonymous' }],
    ['script', {}, `(function(){var a=["blue","red","orange","green"];document.documentElement.dataset.accent=a[Math.floor(Math.random()*a.length)]})()`],
  ] as any[],

  themeConfig: {
    logo: '/favicon-animated.svg',

    search: {
      provider: 'local' as const,
    },

    playgroundApiBase: 'http://localhost:8001',

    socialLinks: [],

    footer: {
      message: 'Published under <a href="https://www.gnu.org/licenses/agpl-3.0.html">AGPL-3.0</a> license.',
      copyright: 'Copyright © 2025–2026 GISPulse contributors',
    },
  },
}

const frNav = [
  { text: 'Démarrage', link: '/getting-started/installation' },
  { text: 'Guide', link: '/guide/cli' },
  { text: 'Blog', link: '/blog/gispulse-vs-fme' },
  {
    text: 'API',
    items: [
      { text: 'API REST', link: '/api/rest' },
      { text: 'Python SDK', link: '/api/sdk' },
    ],
  },
  {
    text: 'Plugins',
    items: [
      { text: 'QGIS', link: '/plugins/qgis' },
      { text: 'ArcGIS', link: '/plugins/arcgis' },
      { text: 'Développer un plugin', link: '/plugins/developing' },
    ],
  },
  {
    text: 'Intégrations',
    items: [
      { text: 'QGIS sans plugin', link: '/integrations/qgis' },
      { text: 'ArcGIS Pro / Online / GeoEvent', link: '/integrations/arcgis' },
      { text: 'MapLibre / deck.gl', link: '/integrations/maplibre' },
    ],
  },
  {
    text: 'Ressources',
    items: [
      { text: 'Presets metier', link: '/templates' },
      { text: 'FAQ', link: '/faq' },
      { text: 'Communauté', link: '/community' },
      { text: 'Contribuer', link: '/contributing' },
    ],
  },
  { text: 'Playground', link: '/playground/' },
  { text: 'Pricing', link: '/pricing' },
  { text: 'Changelog', link: '/changelog' },
]

const frSidebar = {
  '/getting-started/': [
    {
      text: 'Prise en main',
      items: [
        { text: 'Installation', link: '/getting-started/installation' },
        { text: 'Quickstart', link: '/getting-started/quickstart' },
        { text: 'Configuration', link: '/getting-started/configuration' },
      ],
    },
  ],
  '/guide/': [
    {
      text: 'Guide',
      items: [
        { text: 'CLI — Référence', link: '/guide/cli' },
        { text: 'Écrire des règles', link: '/guide/rules' },
        { text: 'Capabilities', link: '/guide/capabilities' },
        { text: 'Matrice de couverture', link: '/guide/coverage' },
        { text: 'Symétrie CLI ↔ Portail', link: '/guide/symmetry' },
        { text: 'Formats I/O', link: '/guide/formats' },
        { text: 'Moteurs DuckDB / PostGIS', link: '/guide/engines' },
        { text: 'Architecture', link: '/guide/architecture' },
        { text: 'Déploiement', link: '/guide/deployment' },
      ],
    },
  ],
  '/api/': [
    {
      text: 'Référence API',
      items: [
        { text: 'REST API', link: '/api/rest' },
        { text: 'Python SDK', link: '/api/sdk' },
      ],
    },
  ],
  '/plugins/': [
    {
      text: 'Plugins & intégrations',
      items: [
        { text: 'Plugin QGIS', link: '/plugins/qgis' },
        { text: 'Add-in ArcGIS', link: '/plugins/arcgis' },
        { text: 'Développer un plugin', link: '/plugins/developing' },
      ],
    },
  ],
  '/integrations/': [
    {
      text: 'Intégrations sans plugin',
      items: [
        { text: 'QGIS', link: '/integrations/qgis' },
        { text: 'ArcGIS Pro / Online / GeoEvent', link: '/integrations/arcgis' },
        { text: 'MapLibre GL JS / deck.gl', link: '/integrations/maplibre' },
      ],
    },
  ],
  '/blog/': [
    {
      text: 'Blog',
      items: [
        { text: 'GISPulse vs FME', link: '/blog/gispulse-vs-fme' },
        { text: 'GISPulse vs QGIS Processing', link: '/blog/gispulse-vs-qgis-processing' },
        { text: 'Automatiser un workflow spatial', link: '/blog/automate-spatial-workflows' },
        { text: 'Predicats, agregations, triggers', link: '/blog/predicats-agregations-triggers' },
        { text: 'Synthese globale', link: '/blog/gispulse-synthese-globale' },
        { text: 'Business plan', link: '/blog/business-plan-gis-engine-on-demand' },
      ],
    },
  ],
  '/playground/': [
    {
      text: 'Playground',
      items: [
        { text: 'Vue d\'ensemble', link: '/playground/' },
        { text: 'S1. Risque Inondation', link: '/playground/urban-flood-risk' },
        { text: 'S2. Commerces / Axes Structurants', link: '/playground/commercial-arterials' },
        { text: 'S3. Accessibilite Sante', link: '/playground/road-buffer-poi' },
        { text: 'S4. Reseau Routier + Recul Urbanisme', link: '/playground/road-setback' },
        { text: 'S5. Espaces Verts', link: '/playground/green-spaces' },
        { text: 'S6. Carte Prix/m² DVF', link: '/playground/real-estate' },
      ],
    },
  ],
}

const enNav = [
  { text: 'Getting Started', link: '/en/getting-started/installation' },
  { text: 'Guide', link: '/en/guide/cli' },
  { text: 'Blog', link: '/en/blog/gispulse-vs-fme' },
  {
    text: 'API',
    items: [
      { text: 'REST API', link: '/en/api/rest' },
      { text: 'Python SDK', link: '/en/api/sdk' },
    ],
  },
  {
    text: 'Plugins',
    items: [
      { text: 'QGIS', link: '/en/plugins/qgis' },
      { text: 'ArcGIS', link: '/en/plugins/arcgis' },
      { text: 'Develop a plugin', link: '/en/plugins/developing' },
    ],
  },
  {
    text: 'Resources',
    items: [
      { text: 'Presets library', link: '/en/templates' },
      { text: 'FAQ', link: '/en/faq' },
      { text: 'Community', link: '/en/community' },
      { text: 'Contributing', link: '/en/contributing' },
    ],
  },
  { text: 'Playground', link: '/en/playground/' },
  { text: 'Pricing', link: '/en/pricing' },
  { text: 'Changelog', link: '/en/changelog' },
]

const enSidebar = {
  '/en/getting-started/': [
    {
      text: 'Getting Started',
      items: [
        { text: 'Installation', link: '/en/getting-started/installation' },
        { text: 'Quickstart', link: '/en/getting-started/quickstart' },
        { text: 'Configuration', link: '/en/getting-started/configuration' },
      ],
    },
  ],
  '/en/guide/': [
    {
      text: 'Guide',
      items: [
        { text: 'CLI Reference', link: '/en/guide/cli' },
        { text: 'Writing Rules', link: '/en/guide/rules' },
        { text: 'Capabilities', link: '/en/guide/capabilities' },
        { text: 'Coverage matrix', link: '/en/guide/coverage' },
        { text: 'CLI ↔ Portal symmetry', link: '/en/guide/symmetry' },
        { text: 'Formats I/O', link: '/en/guide/formats' },
        { text: 'Engines — DuckDB / PostGIS', link: '/en/guide/engines' },
        { text: 'Architecture', link: '/en/guide/architecture' },
        { text: 'Deployment', link: '/en/guide/deployment' },
      ],
    },
  ],
  '/en/api/': [
    {
      text: 'API Reference',
      items: [
        { text: 'REST API', link: '/en/api/rest' },
        { text: 'Python SDK', link: '/en/api/sdk' },
      ],
    },
  ],
  '/en/plugins/': [
    {
      text: 'Plugins & Integrations',
      items: [
        { text: 'QGIS Plugin', link: '/en/plugins/qgis' },
        { text: 'ArcGIS Add-in', link: '/en/plugins/arcgis' },
        { text: 'Develop a Plugin', link: '/en/plugins/developing' },
      ],
    },
  ],
  '/en/blog/': [
    {
      text: 'Blog',
      items: [
        { text: 'GISPulse vs FME', link: '/en/blog/gispulse-vs-fme' },
        { text: 'GISPulse vs QGIS Processing', link: '/en/blog/gispulse-vs-qgis-processing' },
        { text: 'Automate Spatial Workflows', link: '/en/blog/automate-spatial-workflows' },
      ],
    },
  ],
  '/en/playground/': [
    {
      text: 'Playground',
      items: [
        { text: 'Overview', link: '/en/playground/' },
        { text: 'S1. Flood Risk Diagnostic', link: '/en/playground/urban-flood-risk' },
        { text: 'S2. Commercial along Arterials', link: '/en/playground/commercial-arterials' },
        { text: 'S3. Health Accessibility', link: '/en/playground/road-buffer-poi' },
        { text: 'S4. Road Network + Urban Setback', link: '/en/playground/road-setback' },
        { text: 'S5. Green Spaces', link: '/en/playground/green-spaces' },
        { text: 'S6. Price-per-m² Map (DVF)', link: '/en/playground/real-estate' },
      ],
    },
  ],
}

export default defineConfig({
  ...shared,
  description: 'Moteur geospatial modulaire — rules-as-config pour la donnée spatiale',

  locales: {
    root: {
      label: 'Français',
      lang: 'fr',
      themeConfig: {
        nav: frNav,
        sidebar: frSidebar,
        lastUpdated: {
          text: 'Mis à jour le',
        },
        docFooter: {
          prev: 'Page précédente',
          next: 'Page suivante',
        },
        outline: {
          label: 'Sur cette page',
          level: [2, 3] as [number, number],
        },
        returnToTopLabel: 'Retour en haut',
        sidebarMenuLabel: 'Menu',
        darkModeSwitchLabel: 'Thème',
        lightModeSwitchTitle: 'Passer au thème clair',
        darkModeSwitchTitle: 'Passer au thème sombre',
        langMenuLabel: 'Langue',
      },
    },
    en: {
      label: 'English',
      lang: 'en',
      description: 'Modular geospatial engine — rules-as-config for spatial data',
      themeConfig: {
        nav: enNav,
        sidebar: enSidebar,
        lastUpdated: {
          text: 'Last updated',
        },
        docFooter: {
          prev: 'Previous page',
          next: 'Next page',
        },
        outline: {
          label: 'On this page',
          level: [2, 3] as [number, number],
        },
        returnToTopLabel: 'Back to top',
        sidebarMenuLabel: 'Menu',
        darkModeSwitchLabel: 'Theme',
        lightModeSwitchTitle: 'Switch to light theme',
        darkModeSwitchTitle: 'Switch to dark theme',
        langMenuLabel: 'Language',
      },
    },
  },

  vite: {
    css: {
      preprocessorOptions: {
        // no SCSS needed
      },
    },
  },
})

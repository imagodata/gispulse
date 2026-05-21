---
title: Intégrer MapLibre GL JS / deck.gl
description: 4 scénarios pour brancher GISPulse à un viewer web (MapLibre GL JS, deck.gl) — tuiles MVT, GeoJSON OGC, live WS, auth, CORS. Code HTML autonome 100 LOC inclus.
---

# Intégrer MapLibre GL JS / deck.gl

GISPulse expose toutes les surfaces nécessaires à un client web standard (MVT, GeoJSON OGC, WebSocket) — pas besoin de SDK. Le SDK npm `@gispulse/sdk-core` arrive v1.3+ ([INTEGRATION_MATRIX](https://github.com/imagodata/gispulse/blob/main/docs/INTEGRATION_MATRIX.md)).

## Setup

```bash
npm install maplibre-gl
# ou pour deck.gl :
# npm install deck.gl @deck.gl/layers @deck.gl/mapbox
```

Pas d'install GISPulse côté client. Tout passe par `fetch()` / `WebSocket`.

## Scénario A — Carte de base avec MVT

```js
import maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'

const GISPULSE = 'http://localhost:8001'

// Récupérer le doc TileJSON pour bounds + minzoom/maxzoom auto
const tilejson = await fetch(`${GISPULSE}/tiles/parcels/tilejson.json`).then(r => r.json())

const map = new maplibregl.Map({
  container: 'map',
  style: {
    version: 8,
    sources: {
      basemap: {
        type: 'raster',
        tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
        tileSize: 256,
      },
      parcels: {
        type: 'vector',
        tiles: tilejson.tiles,
        minzoom: tilejson.minzoom,
        maxzoom: tilejson.maxzoom,
      },
    },
    layers: [
      { id: 'basemap', type: 'raster', source: 'basemap' },
      {
        id: 'parcels-fill',
        type: 'fill',
        source: 'parcels',
        'source-layer': 'parcels',
        paint: { 'fill-color': '#0D47A1', 'fill-opacity': 0.4 },
      },
      {
        id: 'parcels-line',
        type: 'line',
        source: 'parcels',
        'source-layer': 'parcels',
        paint: { 'line-color': '#0D47A1', 'line-width': 1 },
      },
    ],
  },
  bounds: tilejson.bounds,
})
```

## Scénario B — GeoJSON via OGC API Features

Pour des layers <50 k features ou des vues détaillées, charger un GeoJSON complet est plus simple que MVT.

```js
const GISPULSE = 'http://localhost:8001'

// Charger toutes les features dans la bbox courante
async function loadOGC(map, collectionId) {
  const b = map.getBounds()
  const bbox = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()].join(',')
  const url = `${GISPULSE}/ogc/features/collections/${collectionId}/items?bbox=${bbox}&limit=10000`
  const fc = await fetch(url).then(r => r.json())

  const sourceId = `ogc-${collectionId}`
  if (map.getSource(sourceId)) {
    map.getSource(sourceId).setData(fc)
  } else {
    map.addSource(sourceId, { type: 'geojson', data: fc })
    map.addLayer({
      id: `${sourceId}-fill`,
      type: 'fill',
      source: sourceId,
      paint: { 'fill-color': '#FF6F00', 'fill-opacity': 0.5 },
    })
  }
}

map.on('moveend', () => loadOGC(map, 'parcels'))
```

## Scénario C — Live update via WebSocket

Combiner avec un trigger GISPulse pour recharger la couche quand un INSERT/UPDATE matche un prédicat. Le WS supporte le **filtrage par topic / trigger_id / table** dans l'URL.

```js
const ws = new WebSocket(
  `ws://localhost:8001/ws/events?topics=trigger.fired,dml.changed&tables=parcels`
)

ws.onopen = () => console.log('WS connected')
ws.onerror = (e) => console.error('WS error', e)
ws.onmessage = (msg) => {
  const evt = JSON.parse(msg.data)
  if (evt.type === 'dml.changed' && evt.data.table === 'parcels') {
    // Recharger la source GeoJSON / invalider le tile cache MVT
    loadOGC(map, 'parcels')
  }
  if (evt.type === 'trigger.fired') {
    console.log('Trigger fired:', evt.data.trigger_id, evt.data.actions)
  }
}

// Reconnect simple sur close
ws.onclose = () => setTimeout(() => location.reload(), 2000)
```

::: tip Filtrage WS
Les paramètres `?topics=`, `?trigger_ids=`, `?tables=` filtrent **côté client** (post-broadcast) sur OSS — voir [TRIGGERS_GUIDE → Limites OSS §6](https://github.com/imagodata/gispulse/blob/main/docs/TRIGGERS_GUIDE.md#limites-oss-community-tier). Pour du routage server-side (Pro), une issue v1.3+ est ouverte.
:::

## Scénario D — Auth API key

Sur instance protégée, ajouter `X-API-Key` à toutes les requêtes :

```js
const API_KEY = 'sk_live_...'  // jamais commiter !

const tilejson = await fetch(`${GISPULSE}/tiles/parcels/tilejson.json`, {
  headers: { 'X-API-Key': API_KEY },
}).then(r => r.json())

// Pour les MVT : MapLibre supporte transformRequest
const map = new maplibregl.Map({
  container: 'map',
  style: { /* ... */ },
  transformRequest: (url, resourceType) => {
    if (resourceType === 'Tile' && url.startsWith(GISPULSE)) {
      return { url, headers: { 'X-API-Key': API_KEY } }
    }
    return { url }
  },
})

// Pour le WS : query string (les browsers n'envoient pas d'en-têtes custom au WS upgrade)
const ws = new WebSocket(`ws://localhost:8001/ws/events?api_key=${API_KEY}&topics=...`)
```

::: warning API key dans le browser
Une clé exposée côté browser est lisible par n'importe qui. Pour un site public, utiliser une clé read-only avec rate-limit, ou un proxy server-side qui ajoute la clé.
:::

## CORS

Si le browser bloque les requêtes (`Access-Control-Allow-Origin`), configurer côté GISPulse :

```bash
# Variable d'environnement
export GISPULSE_CORS_ORIGINS="https://app.example.com,http://localhost:5173"
gispulse serve
```

Wildcard `*` accepté en dev mais déconseillé en prod (incompatible avec `credentials: include`).

## Code complet — viewer autonome 100 LOC

Fichier `viewer.html` à ouvrir directement dans un browser (les fetches se feront vers le GISPulse local) :

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>GISPulse MapLibre demo</title>
  <link href="https://unpkg.com/maplibre-gl@4/dist/maplibre-gl.css" rel="stylesheet">
  <style>
    body, html { margin: 0; padding: 0; height: 100%; font-family: system-ui; }
    #map { position: absolute; inset: 0; }
    #log { position: absolute; bottom: 0; left: 0; right: 0; max-height: 30vh;
           overflow: auto; background: rgba(0,0,0,0.85); color: #0f0; padding: 8px;
           font-family: monospace; font-size: 12px; }
  </style>
</head>
<body>
  <div id="map"></div>
  <pre id="log"></pre>

  <script type="module">
    import maplibregl from 'https://unpkg.com/maplibre-gl@4/dist/maplibre-gl.js?module'

    const GISPULSE = 'http://localhost:8001'
    const COLLECTION = 'parcels'
    const log = (...args) => {
      const el = document.getElementById('log')
      el.textContent += args.join(' ') + '\n'
      el.scrollTop = el.scrollHeight
    }

    const map = new maplibregl.Map({
      container: 'map',
      style: {
        version: 8,
        sources: {
          osm: { type: 'raster', tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'], tileSize: 256 },
        },
        layers: [{ id: 'osm', type: 'raster', source: 'osm' }],
      },
      center: [2.13, 48.80],
      zoom: 11,
    })

    async function refresh() {
      const b = map.getBounds()
      const bbox = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()].join(',')
      const url = `${GISPULSE}/ogc/features/collections/${COLLECTION}/items?bbox=${bbox}&limit=5000`
      try {
        const fc = await fetch(url).then(r => r.json())
        if (map.getSource('parcels')) {
          map.getSource('parcels').setData(fc)
        } else {
          map.addSource('parcels', { type: 'geojson', data: fc })
          map.addLayer({
            id: 'parcels-fill', type: 'fill', source: 'parcels',
            paint: { 'fill-color': '#0D47A1', 'fill-opacity': 0.4 },
          })
          map.addLayer({
            id: 'parcels-line', type: 'line', source: 'parcels',
            paint: { 'line-color': '#0D47A1', 'line-width': 1 },
          })
        }
        log(`✓ ${fc.features?.length ?? 0} features loaded`)
      } catch (e) { log('✗ fetch error:', e.message) }
    }

    map.on('load', refresh)
    map.on('moveend', refresh)

    // Live updates
    const ws = new WebSocket(`${GISPULSE.replace('http', 'ws')}/ws/events?topics=dml.changed&tables=${COLLECTION}`)
    ws.onmessage = (msg) => {
      const evt = JSON.parse(msg.data)
      log(`▶ ${evt.type}`, JSON.stringify(evt.data))
      if (evt.type === 'dml.changed') refresh()
    }
    ws.onerror = () => log('✗ WS disconnected')
  </script>
</body>
</html>
```

Lancer : `python -m http.server 5173`, ouvrir `http://localhost:5173/viewer.html`. Toute INSERT/UPDATE sur la table `parcels` côté GISPulse rafraîchit la carte en quasi-temps réel.

## Voir aussi

- [Integration matrix](https://github.com/imagodata/gispulse/blob/main/docs/INTEGRATION_MATRIX.md)
- [Triggers Guide → WebSocket post-broadcast filter](https://github.com/imagodata/gispulse/blob/main/docs/TRIGGERS_GUIDE.md#limites-oss-community-tier)
- [REST API reference](../api/rest.md)

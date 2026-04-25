# Integration Matrix

How GISPulse exposes data and events to common GIS clients, and what's planned for upcoming versions.

Legend: ✅ supported · ⚠️ workaround required · ❌ not supported · — not applicable

| Target | Mode | v1.2 | v1.3+ | Notes |
|---|---|---|---|---|
| **QGIS** | GPKG drag-drop | ✅ | — | Output any pipeline to GPKG, open in QGIS |
| QGIS | WFS / OGC API Features | ✅ | CQL2 filter pushdown | GISPulse acts as the OGC server |
| QGIS | MVT (PostGIS backend) | ✅ | MVT for DuckDB | `GET /tiles/{id}/{z}/{x}/{y}.mvt` |
| QGIS | TileJSON discovery | ✅ | — | `GET /tiles/{id}/tilejson.json` returns TileJSON 3.0 |
| QGIS | Native plugin | ❌ | ✅ | Dataset browser, jobs, rule runner — planned v1.3 |
| **ArcGIS Pro** | OGC API Features | ✅ | — | "Add Data → OGC API Features" |
| ArcGIS Pro | FileGDB export | ✅ | QML/SLD sidecars | Output rule pipeline to `.gdb` |
| **ArcGIS Online** | MVT + TileJSON | ✅ | — | Tile service URL is the TileJSON `tiles[]` entry |
| **ArcGIS GeoEvent** | Webhook out | ✅ (v1.2.x) | — | `ActionDispatcher` POSTs to configured URL on trigger fire |
| ArcGIS GeoEvent | Webhook in | ⚠️ via `/triggers/{id}/evaluate` | dedicated `/webhooks/arcgis` | Manual evaluation today |
| **ArcGIS REST API** | Native client | ❌ | ✅ | Read/write feature services from rules — planned |
| **MapLibre GL JS** | MVT + GeoJSON | ✅ | — | Use the TileJSON `tiles[]` URL as a vector source |
| MapLibre / deck.gl | Live events (WebSocket) | ✅ | — | `wss://server/ws/events` with topic / trigger_id / table filtering |
| **Custom JavaScript** | Public npm SDK | ❌ | ✅ | `@gispulse/sdk-core` — typed jobs, rules, triggers, events |
| **Zapier / n8n** | Webhook out | ✅ (v1.2.x) | — | Same dispatcher as ArcGIS GeoEvent |
| **Python** | Public SDK | ✅ | — | `pip install gispulse` exposes the `gispulse-sdk` client |

## Webhook payload

When a trigger fires, the configured webhook receives:

```json
{
  "trigger_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "trigger_name": "on_dataset_load",
  "event": "dataset.loaded",
  "category": "integration",
  "severity": "info",
  "fired_at": "2026-04-25T14:32:00Z",
  "context": {"dataset_id": "...", "layer": "..."}
}
```

Delivery is fire-and-forget on v1.2 — no retry, no DLQ. ESB-tier triggers (Pro / Enterprise) add retry, dead-letter queue, and circuit breaker.

## OSS limits to know

- **Single writer** — SpatiaLite/GPKG serialize concurrent writes (see `README.md`). Use PostGIS for multi-writer workloads.
- **Trigger polling** — local triggers poll the GPKG change-log every 100 ms. Acceptable latency for UI scenarios; ESB triggers are sub-50 ms.
- **No retry** — webhook actions are best-effort on Community tier.
- **Cascade depth ≤ 3** — a trigger that fires another trigger that fires another trigger stops at depth 3 to prevent loops.
- **Predicate AST is interpreted** — not pre-compiled. Pro tier compiles the AST for hot paths.
- **WebSocket filter is post-broadcast** — the hub fans out then the subscription filter drops non-matching events. Bandwidth saving is real, server-side CPU saving is marginal.

## See also

- [README — What you can do today](../README.md#what-you-can-do-today-v12)
- [Triggers spec](../docs-site/guide/rules.md)
- API reference: [`docs-site/api/rest.md`](../docs-site/api/rest.md)

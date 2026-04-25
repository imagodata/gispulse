---
title: API REST — Référence
description: Documentation complète de l'API REST GISPulse — datasets, jobs, règles, capabilities, OGC, triggers, sessions, streaming.
---

# API REST — Référence

L'API REST GISPulse est une API FastAPI disponible quand le Portal ou le moteur est démarré (`gispulse portal` ou `gispulse engine`).

**Base URL :** `http://localhost:8001`

**Documentation interactive :** `http://localhost:8001/docs` (Swagger UI)

## Authentification

GISPulse supporte trois modes d'authentification :

### 1. Clés API (Pro)

```bash
# .env
GISPULSE_API_KEYS=sk-gp-cle-1,sk-gp-cle-2
```

```http
X-API-Key: sk-gp-cle-1
# ou
Authorization: Bearer sk-gp-cle-1
```

Validation timing-safe pour prévenir les attaques par timing.

### 2. OIDC / SSO (Enterprise)

```bash
GISPULSE_OIDC_ISSUER=https://auth.example.com
GISPULSE_OIDC_CLIENT_ID=gispulse
GISPULSE_OIDC_CLIENT_SECRET=secret
```

Session cookie après le flow OIDC. JWT signé côté serveur.

### 3. Sans auth (développement)

Si `GISPULSE_API_KEYS` n'est pas défini, l'API est ouverte (mode Community local).

---

## Health

### `GET /health`

État du serveur et des composants (DB, Redis, disque).

**Réponse :**
```json
{
  "status": "ok",
  "version": "1.0.0",
  "engine": "duckdb",
  "components": {
    "database": "ok",
    "redis": "unavailable",
    "disk": "ok"
  }
}
```

---

## Datasets

### `POST /datasets/upload`

Upload d'un fichier spatial. Retourne les métadonnées du dataset créé.

**Content-Type :** `multipart/form-data`

Protection SSRF pour les URLs distantes. Validation du nom de fichier (`Path(file.filename).name`).

```bash
curl -X POST http://localhost:8001/datasets/upload \
  -F "file=@data/parcelles.gpkg"
```

**Réponse :**
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "name": "parcelles.gpkg",
  "format": "GPKG",
  "crs": "EPSG:2154",
  "layers": [
    {
      "name": "parcelles",
      "geometry_type": "Polygon",
      "feature_count": 8420
    }
  ]
}
```

### `GET /datasets`

Liste tous les datasets enregistrés.

**Query params :** `limit` (int, défaut 100), `offset` (int, défaut 0)

### `GET /datasets/{id}`

Récupère un dataset par UUID.

### `POST /datasets/ogc`

Enregistre un service OGC distant comme dataset (lazy — pas de téléchargement).

```json
{
  "url": "https://wxs.ign.fr/parcellaire/geoportail/wfs",
  "service_type": "WFS",
  "layer_name": "BDPARCELLAIRE_VECTEUR:parcelle",
  "name": "Parcelles cadastrales IGN"
}
```

---

## Features / Données

### `GET /api/portal/datasets/{id}/layers/{layer}/features`

Récupère les features d'une layer en GeoJSON.

**Query params :**

| Param | Type | Défaut | Description |
|-------|------|--------|-------------|
| `limit` | int | 100 | Nombre de features |
| `offset` | int | 0 | Pagination |
| `bbox` | string | — | Filtre spatial : `minx,miny,maxx,maxy` |

**Réponse :** GeoJSON FeatureCollection

### `POST /api/portal/sql/execute`

Exécute une requête SQL sur les datasets chargés (session DuckDB). Protégé par `X-Admin-Key` en production.

```json
{ "query": "SELECT code_commune, COUNT(*) as nb FROM parcelles GROUP BY 1" }
```

### `POST /api/portal/sql/export`

Exporte le résultat d'une requête SQL dans un format cible. Protégé par `X-Admin-Key` en production.

### `POST /api/portal/datasets/export`

Exporte un dataset dans un format cible.

```json
{
  "dataset_id": "550e8400-...",
  "format": "geojson"
}
```

Formats disponibles : `gpkg`, `geojson`, `fgb`, `parquet`, `shp`

---

## Jobs

### `POST /jobs`

Crée et exécute un job de traitement. Support retry automatique (configurable via `max_retries`), timeout (défaut 300s).

```json
{
  "name": "buffer_parcelles",
  "dataset_id": "550e8400-...",
  "parameters": {
    "rule_ids": ["rule-uuid-1", "rule-uuid-2"]
  }
}
```

**Réponse :**
```json
{
  "id": "job-uuid",
  "status": "PENDING",
  "created_at": "2026-04-06T10:00:00Z"
}
```

### `GET /jobs/{id}`

Récupère le statut d'un job.

Statuts : `PENDING`, `RUNNING`, `COMPLETED`, `FAILED`

### `GET /jobs/{id}/stream` (SSE)

Stream Server-Sent Events des logs d'exécution en temps réel.

```javascript
const es = new EventSource('/jobs/job-uuid/stream')
es.onmessage = (e) => console.log(JSON.parse(e.data))
```

### `GET /jobs/{id}/download`

Télécharge l'artifact résultat d'un job complété.

### `POST /jobs/{id}/cancel`

Annule un job en cours d'exécution.

---

## Règles

### `GET /rules`

Liste toutes les règles.

### `POST /rules`

Crée une règle.

```json
{
  "name": "buffer_50m",
  "capability": "buffer",
  "config": { "distance": 50 },
  "scope": "global",
  "enabled": true
}
```

### `PUT /rules/{id}`

Met à jour une règle.

### `DELETE /rules/{id}`

Supprime une règle.

### `POST /rules/validate`

Valide un batch de règles (dry-run). Vérifie les capabilities, les schémas JSON et les types.

### `POST /rules/to-node`

Convertit une règle en template de nœud pour le graph editor.

### `POST /rules/from-node`

Crée une règle depuis un nœud du graph editor.

---

## Pipelines v2

::: tip Nouveau en 1.0.1
L'API Pipelines v2 utilise le format `PipelineSpec` déclaratif avec support DAG, steps conditionnels et triggers inline.
:::

### `POST /api/pipelines/execute`

Exécute un pipeline v2 complet. Accepte un `PipelineSpec` JSON.

```json
{
  "name": "analyse_risque",
  "steps": [
    {
      "id": "filtrage",
      "capability": "filter",
      "params": { "expression": "surface > 100" }
    },
    {
      "id": "buffer_zone",
      "capability": "buffer",
      "input": "filtrage",
      "params": { "distance": 50 }
    }
  ]
}
```

**Réponse :**
```json
{
  "job_id": "job-uuid",
  "status": "COMPLETED",
  "steps_executed": 2,
  "result_path": "/tmp/gispulse/result.gpkg"
}
```

### `POST /api/pipelines/validate`

Validation dry-run d'un pipeline v2. Vérifie la structure, les capabilities, les référencements de steps et les prédicats.

### `GET /api/pipelines/examples`

Retourne des exemples de pipelines v2 pour chaque cas d'usage courant.

---

## Capabilities

### `GET /capabilities`

Liste toutes les capabilities disponibles avec nom, description et schéma JSON.

### `GET /capabilities/{name}`

Détails d'une capability spécifique.

### `GET /capabilities/{name}/sql-preview`

Prévisualisation de la requête SQL générée pour une capability (mode DuckDB/PostGIS).

---

## Scenarios

### `GET /scenarios`

Liste tous les scénarios.

### `POST /scenarios`

Crée un scénario (pipeline multi-jobs).

### `POST /scenarios/{id}/run`

Exécute un scénario (mode séquentiel ou indépendant).

### `POST /scenarios/{id}/run-node`

Exécute un nœud spécifique du graphe d'un scénario (Phase 3A).

---

## Triggers (Pro)

### `POST /triggers`

Crée un trigger associé à une règle avec prédicats et actions.

```json
{
  "name": "alerte_zone_inondable",
  "event": "FEATURE_CREATED",
  "trigger_type": "DML",
  "predicates": [
    { "type": "geom", "op": "intersects", "ref_table": "zones_inondables" }
  ],
  "actions": [
    { "action_type": "NOTIFY", "config": { "message": "Alerte inondation" } }
  ],
  "enabled": true
}
```

### `GET /triggers`

Liste tous les triggers.

### `POST /triggers/validate`

Valide la configuration d'un trigger.

### `POST /triggers/test`

Test de tir d'un trigger (dry-run).

### `POST /triggers/evaluate`

Évalue les prédicats d'un trigger contre des données.

### `GET /triggers/{id}/operations`

Liste les opérations spatiales attachées à un trigger.

### `POST /triggers/{id}/operations`

Ajoute une opération spatiale à un trigger.

```json
{
  "operation_type": "BEFORE",
  "capability": "buffer",
  "params": { "distance": 100 },
  "order": 1
}
```

### `PUT /triggers/{id}/operations/{op_id}`

Met à jour une opération de trigger.

### `DELETE /triggers/{id}/operations/{op_id}`

Supprime une opération de trigger.

---

## Sessions (Pro)

### `POST /sessions`

Crée une session spatiale éphémère (DuckDB ou SpatiaLite).

### `GET /sessions`

Liste les sessions actives.

### `DELETE /sessions/{id}`

Supprime une session éphémère.

---

## Projects (Pro)

### `GET /projects`

Liste les projets (namespaces PostGIS).

### `POST /projects`

Crée un projet avec schéma PostGIS dédié.

### `GET /projects/{id}/layers`

Liste les layers d'un projet.

---

## OGC Features API

L'API OGC est disponible sous `/ogc/`. Conforme à la spécification OGC API Features.

### `GET /ogc/collections`

Liste les collections disponibles.

### `GET /ogc/collections/{id}/items`

Récupère les items d'une collection en GeoJSON.

**Query params conformes OGC :** `limit`, `offset`, `bbox`, `datetime`

### `GET /ogc/collections/{id}/tiles/{z}/{x}/{y}.mvt`

Tuiles vectorielles MVT pour l'affichage cartographique haute performance.

---

## Schedules (Pro)

### `GET /schedules`

Liste les pipelines planifiés (CRON).

### `POST /schedules`

Crée un schedule CRON pour un pipeline.

```json
{
  "name": "daily_update",
  "cron": "0 2 * * *",
  "pipeline_id": "scenario-uuid"
}
```

---

## Auth & Admin

### `POST /auth/login`

Authentification (API key ou OIDC).

### `GET /auth/session`

Vérifie la session courante.

### `GET /admin/users`

Liste les utilisateurs (RBAC).

### `POST /admin/license`

Informations de licence.

---

## Billing (Pro)

### `GET /billing/usage`

Métriques d'utilisation (features traitées, temps d'exécution, stockage).

### `POST /billing/subscribe`

Gestion d'abonnement Stripe.

---

## Marketplace

### `GET /marketplace/capabilities`

Liste les capabilities disponibles dans le marketplace.

### `POST /marketplace/install`

Installe une capability depuis le marketplace.

---

## Catalog

### `GET /catalog/basemaps`

Liste les fonds de carte disponibles (OSM, IGN, Esri, CARTO, etc.).

### `GET /catalog/projections`

Liste les projections EPSG courantes.

### `GET /catalog/providers`

Liste les fournisseurs de données (IGN, data.gouv.fr, STAC, etc.).

---

## WebSocket

### `GET /ws/events`

WebSocket pour les événements temps réel (notifications de triggers, mises à jour de jobs, collaboration).

---

## Streaming / SSE

### `GET /rules/eval-stream`

Stream SSE de l'évaluation des règles en temps réel.

### `GET /jobs/{id}/stream`

Stream SSE des logs d'un job spécifique.

---

## Rate Limiting

L'API applique un rate limiting configurable (défaut : 300 requêtes/minute) :

```bash
# Redis-backed (production)
GISPULSE_REDIS_URL=redis://localhost:6379
GISPULSE_RATE_LIMIT_STORAGE=redis://localhost:6379

# In-memory (développement)
GISPULSE_RATE_LIMIT_STORAGE=memory://
```

---

## Codes d'erreur

| Code | Signification |
|------|---------------|
| `400` | Requête invalide (paramètres manquants ou mal formés) |
| `401` | Non authentifié (clé API manquante) |
| `403` | Non autorisé (clé API invalide ou droits insuffisants) |
| `404` | Ressource non trouvée |
| `409` | Conflit (doublon détecté) |
| `422` | Erreur de validation (corps de requête invalide) |
| `429` | Trop de requêtes (rate limit) |
| `500` | Erreur interne |

---

## SDK Python

Préférez le [SDK Python](/api/sdk) à l'API REST brute pour les scripts et intégrations Python.

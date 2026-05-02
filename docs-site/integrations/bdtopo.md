---
title: Utiliser GISPulse sur la BD TOPO IGN
description: Guide complet pour traiter un extrait BD TOPO (GeoPackage Lambert-93) avec GISPulse — inspection, pipelines de règles, triggers DML.
---

# Utiliser GISPulse sur la BD TOPO IGN

La [BD TOPO](https://geoservices.ign.fr/bdtopo) est livrée par l'IGN au format **GeoPackage** en Lambert-93 (EPSG:2154). C'est le format de prédilection de GISPulse : règles déclaratives, triggers DML natifs, change-tracking SQL — tout fonctionne sans conversion.

Ce guide couvre trois usages :

1. **Pipelines de règles** (one-shot) — filtres, joins spatiaux, isochrones
2. **Triggers DML** — réagir aux INSERT/UPDATE/DELETE sur le GPKG
3. **Patterns avancés** — déclencher un pipeline depuis un trigger, push PostGIS

## Pré-requis

```bash
pip install gispulse
gispulse --version    # >= 1.5.1 recommandé
```

Télécharger un extrait BD TOPO départemental (.7z) sur [geoservices.ign.fr](https://geoservices.ign.fr/bdtopo) puis décompresser pour obtenir le `.gpkg`.

## 1. Inspecter le fichier

```bash
gispulse info BDTOPO_3-3_GPKG_LAMB93_D075.gpkg
gispulse layers BDTOPO_3-3_GPKG_LAMB93_D075.gpkg
```

Couches BD TOPO 3.x typiques : `batiment`, `troncon_de_route`, `commune`, `cours_d_eau`, `zone_d_activite_ou_d_interet`, `pai_*` (POI), `parcelle_cadastrale`.

## 2. Pipelines de règles

### 2.1 Initialiser un projet

```bash
gispulse init bdtopo-paris
cd bdtopo-paris
```

Layout scaffold :

```
bdtopo-paris/
├── rules/rules.json    ← template de règles
├── data/               ← y placer le .gpkg BD TOPO
└── output/             ← résultats émis ici
```

Lister les opérateurs disponibles :

```bash
gispulse capabilities    # 118+ capabilities
```

### 2.2 Bâtiments à moins de 50 m d'une route principale

`rules/batiments_pres_routes.json` :

```json
[
  {
    "name": "routes_principales",
    "capability": "filter",
    "config": {
      "expression": "nature in ('Type autoroutier','Route à 2 chaussées','Route à 1 chaussée')",
      "order": 0
    }
  },
  {
    "name": "batiments_a_proximite",
    "capability": "filter",
    "config": {
      "spatial_predicate": "intersects",
      "ref_layer": "batiment",
      "buffer_distance": 50,
      "crs_meters": "EPSG:2154",
      "order": 1
    }
  }
]
```

```bash
gispulse run data/BDTOPO_xxx.gpkg \
  -r rules/batiments_pres_routes.json \
  -o output/batiments_pres_routes.gpkg \
  -l troncon_de_route \
  --ref-source batiment:data/BDTOPO_xxx.gpkg \
  --engine duckdb
```

### 2.3 Enrichir chaque bâtiment avec sa commune

`rules/batiments_avec_commune.json` :

```json
[
  {
    "name": "join_commune",
    "capability": "spatial_join",
    "config": {
      "ref_layer": "commune",
      "how": "left",
      "predicate": "intersects",
      "columns": ["nom_officiel", "code_insee", "population"],
      "order": 0
    }
  }
]
```

```bash
gispulse run data/BDTOPO_xxx.gpkg \
  -r rules/batiments_avec_commune.json \
  -o output/batiments_enrichis.gpkg \
  -l batiment \
  --ref-source commune:data/BDTOPO_xxx.gpkg
```

### 2.4 Isochrones piéton 5 / 10 / 15 min autour d'un POI

À 5 km/h : 5 min ≈ 417 m, 10 min ≈ 833 m, 15 min ≈ 1250 m.

`rules/isochrone_pieton.json` :

```json
[
  {
    "name": "iso_5_10_15min",
    "capability": "isochrone",
    "config": {
      "start_x": 651000,
      "start_y": 6862000,
      "cost_budgets": [417, 833, 1250],
      "dissolve": true,
      "crs_meters": "EPSG:2154",
      "edge_buffer_m": 25,
      "order": 0
    }
  }
]
```

```bash
gispulse run data/BDTOPO_xxx.gpkg \
  -r rules/isochrone_pieton.json \
  -o output/iso_pieton.gpkg \
  -l troncon_de_route
```

Coordonnées en Lambert-93. Les trois zones concentriques sont émises en sortie.

### 2.5 Bonnes pratiques pipelines

- **Toujours valider d'abord** : `gispulse validate -r rules/monfichier.json`
- **`--engine duckdb`** sur les couches lourdes (BD TOPO `batiment` d'un département = >500k features)
- **`crs_meters: "EPSG:2154"`** dans les ops à distance — la BD TOPO est déjà en Lambert-93, ça évite toute reprojection implicite
- **Découvrir visuellement** : `gispulse portal`, drag-and-drop le GPKG, construire le pipeline graphiquement, exporter le `rules.json`

## 3. Triggers DML

GISPulse peut réagir aux modifications du GPKG (INSERT/UPDATE/DELETE) en exécutant des actions configurées en YAML.

### 3.1 Activer le change-tracking

Une seule fois par couche :

```bash
gispulse track enable BDTOPO_xxx.gpkg --layer batiment
```

Pose les triggers SQL internes (`_gispulse_changelog`).

### 3.2 Trigger de base — `triggers.yaml`

```yaml
version: 1

gpkg: ./BDTOPO_3-3_GPKG_LAMB93_D075.gpkg

triggers:

  # 1. Notifier un système externe à chaque nouveau bâtiment
  - name: notify_new_building
    table: batiment
    pk_col: cleabs
    when: [INSERT]
    actions:
      - type: webhook
        url: https://mon-sig.example.com/hooks/batiment-new

  # 2. Marquer automatiquement les bâtiments industriels de grande hauteur
  - name: flag_industriel_haut
    table: batiment
    pk_col: cleabs
    when: [INSERT, UPDATE]
    predicate: "usage_1 == 'Industriel' AND hauteur > 20"
    actions:
      - type: set_field
        field: date_audit
        value: 2026-05-02
      - type: webhook
        url: https://mon-sig.example.com/hooks/batiment-sensible

  # 3. Audit log sur toute suppression
  - name: audit_batiment_deletes
    table: batiment
    pk_col: cleabs
    when: [DELETE]
    actions:
      - type: run_sql
        expression: "INSERT INTO audit_log (cleabs, op, ts) VALUES (OLD.cleabs, 'DELETE', CURRENT_TIMESTAMP)"

security:
  webhook_allowlist:
    - mon-sig.example.com

runtime:
  poll_interval_ms: 1000
  max_batch: 200
```

### 3.3 Lancer

```bash
gispulse triggers validate --config triggers.yaml

# One-shot : draine la file et sort
gispulse triggers run --config triggers.yaml --once

# Daemon : reste en écoute
gispulse triggers run --config triggers.yaml
```

Alternative légère pour le dev :

```bash
gispulse watch BDTOPO_xxx.gpkg --rules triggers.yaml
```

### 3.4 Spécificités BD TOPO

- **`pk_col: cleabs`** — l'identifiant stable IGN. Ne pas utiliser `fid` (peut changer entre millésimes).
- **`OLD.*` / `NEW.*`** — les attributs nus (`usage_1`, `hauteur`) résolvent à `new.*` par défaut. Préfixer par `OLD.` pour la valeur avant modif.
- **DSL prédicat** : `==`, `!=`, `<`, `>`, `AND`, `OR`, `in (...)`. Détaillé dans [TRIGGERS_GUIDE.md](https://github.com/imagodata/gispulse/blob/main/docs/TRIGGERS_GUIDE.md).
- **Pas de prédicat spatial** dans le DSL (`ST_Contains` etc.) — il est attribut-only. Pour du spatial, déclencher un pipeline de règles via webhook (voir §4.2).

## 4. Patterns avancés

### 4.1 Multi-actions chaînées

Les actions s'exécutent dans l'ordre déclaré.

```yaml
- name: onboard_new_batiment
  table: batiment
  pk_col: cleabs
  when: [INSERT]
  actions:
    - type: set_field
      field: date_creation_audit
      value: 2026-05-02

    - type: run_sql
      expression: >
        INSERT INTO audit_log (cleabs, op, ts, source)
        VALUES (NEW.cleabs, 'INSERT', CURRENT_TIMESTAMP, 'gispulse-trigger')

    - type: notify
      channel: gispulse_batiment_events
      payload_template: |
        {"cleabs": "{{ NEW.cleabs }}",
         "commune": "{{ NEW.code_insee }}",
         "hauteur": {{ NEW.hauteur }} }
```

### 4.2 Déclencher un pipeline complet via webhook → API

Le YAML CLI ne mappe pas directement `RUN_JOB`. Pour lancer un pipeline depuis un trigger, on passe par l'API :

```yaml
- name: enrich_pipeline_on_new_batiment
  table: batiment
  pk_col: cleabs
  when: [INSERT, UPDATE]
  predicate: "usage_1 == 'Industriel'"
  actions:
    - type: webhook
      url: http://localhost:8000/jobs
```

Démarrer l'API à côté :

```bash
gispulse engine    # API + portail dans un seul process
```

Le payload du change-log (`{table, pk, op, old, new, ts}`) est posté ; le worker lance le pipeline. Ne pas oublier d'ajouter le host dans `webhook_allowlist`.

### 4.3 Push PostGIS sur INSERT

Deux approches :

**Webhook + relai HTTP** (simple, retry intégré) :

```yaml
- name: sync_batiment_to_postgis
  table: batiment
  pk_col: cleabs
  when: [INSERT, UPDATE, DELETE]
  actions:
    - type: webhook
      url: http://postgis-sync.internal:9000/sync/batiment
```

Le service `postgis-sync` (FastAPI ~30 lignes) reçoit `{op, old, new}` et fait l'UPSERT/DELETE.

**Outbox + cron** (zéro serveur, asynchrone) :

```yaml
- name: outbox_batiment_insert
  table: batiment
  pk_col: cleabs
  when: [INSERT]
  actions:
    - type: run_sql
      expression: >
        INSERT INTO outbox_postgis (cleabs, op, ts)
        VALUES (NEW.cleabs, 'INSERT', CURRENT_TIMESTAMP)
```

Cron toutes les 5 min :

```bash
ogr2ogr -f PostgreSQL "PG:host=... dbname=ign" \
  BDTOPO.gpkg outbox_postgis -append
```

### 4.4 Garde-fou métier (refus + alerte)

Empêche l'altération d'un bâtiment classé monument historique :

```yaml
- name: protect_mh
  table: batiment
  pk_col: cleabs
  when: [UPDATE]
  predicate: "OLD.usage_1 == 'Monument historique'"
  actions:
    - type: run_sql
      expression: >
        UPDATE batiment SET usage_1 = OLD.usage_1, hauteur = OLD.hauteur
        WHERE cleabs = OLD.cleabs
    - type: webhook
      url: https://admin-sig.example.com/alerts/mh-tampering
    - type: log_event
```

## 5. Référence — actions disponibles

### Mode CLI (YAML headless)

| Action | Config | Cas BD TOPO |
|---|---|---|
| `webhook` | `url` | Sync externe, déclencher pipeline via API |
| `set_field` | `field`, `value` | Stamp d'audit |
| `run_sql` | `expression` | Audit log, outbox, refus métier |
| `notify` | `channel`, `payload_template` | Worker custom écoute |
| `log_event` | — | Observabilité |

### Mode API/portail uniquement

| Action | Pourquoi pas en YAML |
|---|---|
| `RUN_JOB` / `RUN_GRAPH` | Nécessite contexte job_repo + dataset_repo (non-instanciable en Mode 1 headless) |
| `ENQUEUE` | Backend de queue requis |
| `UPDATE_AGGREGATE` | Métriques temps-réel côté backend |

→ Pour ces actions, soit passer par `webhook`, soit lancer `gispulse engine` et configurer depuis le portail.

## 6. Limites à connaître

- **`run_sql` est sandboxé** : pas de DDL, tables `gpkg_*` / `_gispulse_*` protégées, multi-statements refusés. Créer les tables annexes (`audit_log`, `outbox_postgis`) **avant** le premier run.
- **Polling 1000 ms** par défaut → latence ~1 s. Baisse à 250 ms si besoin réactif (`runtime.poll_interval_ms: 250`).
- **`max_batch: 200`** : un import massif déclenche au compte-goutte. Soit augmenter, soit faire l'import **avant** d'activer le tracking.
- **Webhook allowlist obligatoire** (défense SSRF) — déclarer chaque host explicitement dans `security.webhook_allowlist`.

## Voir aussi

- [TRIGGERS_GUIDE.md](https://github.com/imagodata/gispulse/blob/main/docs/TRIGGERS_GUIDE.md) — DSL prédicats complet, contrat webhook, modèle de sécurité
- [Capabilities](/guide/capabilities) — les 118 opérateurs disponibles
- [QGIS sans plugin](./qgis) — consommer les sorties depuis QGIS
- [Engine modes](/guide/engines) — `python` vs `duckdb`

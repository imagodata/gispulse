---
title: Écrire des règles
description: Format JSON des règles GISPulse, options, composition de pipelines, triggers, prédicats et bonnes pratiques.
---

# Écrire des règles

Les règles GISPulse sont des fichiers JSON déclaratifs. Ils définissent quelles opérations spatiales appliquer, dans quel ordre, avec quels paramètres.

## Structure d'une règle

```json
{
  "name": "buffer_100m",
  "description": "Buffer de 100 m autour des bâtiments",
  "capability": "buffer",
  "config": {
    "distance": 100,
    "order": 0
  },
  "enabled": true,
  "scope": "global"
}
```

| Champ | Type | Requis | Description |
|-------|------|--------|-------------|
| `name` | string | oui | Identifiant unique de la règle dans le fichier |
| `description` | string | non | Documentation humaine |
| `capability` | string | oui | Nom de la capability à invoquer |
| `config` | object | oui | Paramètres passés à la capability |
| `config.order` | int | recommandé | Ordre d'exécution (croissant) |
| `enabled` | bool | non | `true` par défaut. `false` = règle ignorée |
| `scope` | string | non | Portée : `global`, `plan`, `user`, `project`, `dataset` |

## Pipeline — fichier de règles complet

Un fichier de règles est un tableau JSON de règles ordonnées :

```json
[
  {
    "name": "filter_actif",
    "description": "Ne garder que les bâtiments actifs",
    "capability": "filter",
    "config": {
      "expression": "statut == 'ACTIF'",
      "order": 0
    }
  },
  {
    "name": "reproject_l93",
    "description": "Passer en Lambert-93 pour les calculs métriques",
    "capability": "reproject",
    "config": {
      "target_crs": "EPSG:2154",
      "order": 1
    }
  },
  {
    "name": "buffer_protection",
    "description": "Zone de protection de 50 m",
    "capability": "buffer",
    "config": {
      "distance": 50,
      "order": 2
    }
  },
  {
    "name": "ajouter_surface",
    "description": "Calculer la surface de chaque zone tampon",
    "capability": "area_length",
    "config": {
      "order": 3
    }
  }
]
```

Les règles sont exécutées en ordre croissant de `config.order`. En cas d'égalité, l'ordre dans le tableau est respecté.

## Référence par capability

### `buffer`

Applique un buffer métrique autour des géométries.

```json
{
  "capability": "buffer",
  "config": {
    "distance": 100,
    "crs_meters": "EPSG:3857",
    "order": 0
  }
}
```

| Paramètre | Type | Défaut | Description |
|-----------|------|--------|-------------|
| `distance` | float | `0.0` | Distance en mètres |
| `crs_meters` | string | `EPSG:3857` | CRS métrique de projection intermédiaire |

### `filter`

Filtre les features par expression attributaire et/ou prédicat spatial.

```json
{
  "capability": "filter",
  "config": {
    "expression": "population > 10000 and region == 'Bretagne'",
    "order": 0
  }
}
```

**Avec prédicat spatial :**

```json
{
  "capability": "filter",
  "config": {
    "spatial_predicate": "intersects",
    "ref_layer": "zones_inondables",
    "buffer_distance": 100,
    "order": 0
  }
}
```

| Paramètre | Type | Description |
|-----------|------|-------------|
| `expression` | string | Expression Python évaluée via `gdf.query()` |
| `spatial_predicate` | string | `intersects`, `within`, `contains`, `crosses`, `overlaps`, `touches`, `dwithin` |
| `ref_layer` | string | Layer de référence (via `--ref-source`) |
| `buffer_distance` | float | Buffer en mètres sur la géométrie de référence |

### `reproject`

Reprojette les géométries dans un autre CRS.

```json
{
  "capability": "reproject",
  "config": { "target_crs": "EPSG:4326", "order": 1 }
}
```

### `clip`

Découpe les features selon l'emprise d'une layer de référence.

```json
{
  "capability": "clip",
  "config": { "ref_layer": "commune", "order": 2 }
}
```

La layer de référence doit être fournie via `--ref-source` en CLI :

```bash
gispulse run input.gpkg --rules rules.json -o output.gpkg \
  --ref-source commune:data/communes.gpkg
```

### `intersects`

Garde uniquement les features qui intersectent la layer de référence.

```json
{
  "capability": "intersects",
  "config": { "ref_layer": "zones_inondables", "order": 1 }
}
```

### `spatial_join`

Jointure spatiale entre la layer traitée et une layer de référence.

```json
{
  "capability": "spatial_join",
  "config": {
    "ref_layer": "communes",
    "how": "left",
    "op": "intersects",
    "order": 2
  }
}
```

| Paramètre | Type | Défaut | Description |
|-----------|------|--------|-------------|
| `ref_layer` | string | requis | Nom de la layer de référence |
| `how` | string | `left` | Type de jointure : `left`, `inner`, `right` |
| `op` | string | `intersects` | Prédicat spatial : `intersects`, `contains`, `within` |

### `centroid`

Remplace les géométries par leur centroïde.

```json
{ "capability": "centroid", "config": { "order": 3 } }
```

### `area_length`

Calcule la surface et/ou la longueur de chaque feature.

```json
{
  "capability": "area_length",
  "config": {
    "area_column": "surface_m2",
    "length_column": "perimetre_m",
    "order": 4
  }
}
```

### `dissolve`

Dissout les features regroupées par valeur d'un attribut.

```json
{
  "capability": "dissolve",
  "config": { "by": "code_commune", "order": 5 }
}
```

### `union`

Fusionne toutes les features en une géométrie unique.

```json
{ "capability": "union", "config": { "order": 6 } }
```

### `calculate`

Évalue une expression calculée sur les attributs.

```json
{
  "capability": "calculate",
  "config": {
    "expression": "prix_m2 = prix / surface_m2",
    "output_field": "prix_m2",
    "order": 7
  }
}
```

### `spatial_aggregate`

Agrégation spatiale avec GROUP BY.

```json
{
  "capability": "spatial_aggregate",
  "config": {
    "group_by": "code_commune",
    "aggregations": { "population": "sum", "geometry": "union" },
    "order": 8
  }
}
```

---

## Prédicats (Triggers)

Les prédicats sont utilisés dans les triggers pour évaluer des conditions sur les données. Trois types de prédicats sont supportés :

### Prédicat attributaire

```json
{
  "type": "attr",
  "field": "population",
  "op": "gt",
  "value": 10000
}
```

Opérateurs : `eq`, `neq`, `gt`, `lt`, `gte`, `lte`, `in`, `like`, `is_null`, `not_null`

### Prédicat géométrique

```json
{
  "type": "geom",
  "op": "intersects",
  "ref_table": "zones_protegees",
  "ref_filter": "type = 'ZNIEFF'",
  "buffer_m": 100
}
```

Opérateurs : `intersects`, `within`, `contains`, `crosses`, `overlaps`, `touches`, `distance_lt`, `distance_gt`

### Prédicat composé

```json
{
  "type": "compound",
  "logic": "AND",
  "predicates": [
    { "type": "attr", "field": "surface", "op": "gt", "value": 1000 },
    { "type": "geom", "op": "within", "ref_table": "commune", "ref_filter": "nom = 'Paris'" }
  ]
}
```

Logiques : `AND`, `OR`, `NOT`

---

## Triggers

Les triggers réagissent aux événements sur les données et exécutent des actions.

```json
{
  "name": "alerte_zone_inondable",
  "event": "FEATURE_CREATED",
  "trigger_type": "DML",
  "category": "DATA",
  "predicates": [
    { "type": "geom", "op": "intersects", "ref_table": "zones_inondables" }
  ],
  "predicate_logic": "AND",
  "actions": [
    { "action_type": "NOTIFY", "config": { "message": "Nouvelle feature en zone inondable" } },
    { "action_type": "RUN_JOB", "config": { "job_name": "recalcul_risque" } }
  ],
  "enabled": true
}
```

**Événements** : `DATA_CHANGED`, `GEOMETRY_CHANGED`, `FEATURE_CREATED`, `FEATURE_UPDATED`, `FEATURE_DELETED`, `THRESHOLD_CROSSED`, `JOB_COMPLETED`, `JOB_FAILED`, etc.

**Types d'actions** : `NOTIFY`, `SET_FIELD`, `UPDATE_AGGREGATE`, `RUN_JOB`, `RUN_GRAPH`, `WEBHOOK`, `ENQUEUE`, `LOG_EVENT`, `SEND_EMAIL`, `RUN_SQL`, etc.

::: tip Cascade
Les triggers peuvent déclencher d'autres triggers. La profondeur maximale de cascade est limitée à 3 niveaux pour éviter les boucles infinies.
:::

### Webhook actions (Zapier, ArcGIS GeoEvent, Make, n8n, …)

Une action `WEBHOOK` envoie le payload du trigger en POST JSON à une URL externe. Câbler le client une seule fois à l'instanciation du dispatcher :

```python
from gispulse.adapters.webhooks import HttpWebhookClient
from gispulse.adapters.esb.action_dispatcher import ActionDispatcher

dispatcher = ActionDispatcher(
    webhook_client=HttpWebhookClient().post,
    # ... autres callables (sql_executor, event_hub, …)
)
```

Configuration de l'action côté trigger :

```json
{
  "action_type": "WEBHOOK",
  "config": { "url": "https://hooks.zapier.com/…/abcd" }
}
```

**Format du payload** (contrat public, stable v1.2+) :

```json
{
  "event_type": "trigger_fired",
  "trigger_id": "3fa85f64-…",
  "trigger_name": "alerte_zone_inondable",
  "table": "parcels",
  "operation": "INSERT",
  "row_id": "…",
  "matched": true,
  "transition": "ENTER",
  "timestamp": "2026-04-26T14:32:11.123+00:00",
  "custom": { /* sortie de payload_template, le cas échéant */ }
}
```

**Sécurité** :

- Schémas autorisés : `http`, `https` (rien d'autre).
- Blocklist SSRF : RFC1918 + loopback + link-local (`169.254/16`, dont les *cloud metadata*) + multicast + reserved. Mettre `allow_private_ips=True` à l'init pour atteindre une cible interne (CI, dev fixture).
- Signature optionnelle HMAC-SHA256 dans l'en-tête `X-GISPulse-Signature` quand la variable d'environnement `GISPULSE_WEBHOOK_SIGNING_SECRET` est définie. Le receveur doit recalculer `sha256=hex(hmac(secret, body))`.
- Retry borné : 2 tentatives sur 5xx + connect/read timeout (back-off 1 s, 3 s). 4xx jamais retenté.

---

## Désactiver des règles temporairement

```json
{
  "name": "buffer_optionnel",
  "capability": "buffer",
  "config": { "distance": 50, "order": 2 },
  "enabled": false
}
```

La règle est ignorée sans la supprimer du fichier.

## Bonnes pratiques

- **Ordre explicite** : toujours définir `config.order` même pour une règle unique.
- **Noms lisibles** : préférez `filter_batiments_actifs` à `rule_1`.
- **Validation CI** : intégrez `gispulse validate rules.json` dans votre pipeline CI.
- **Versionner les règles** : les fichiers `.json` sont des artefacts de configuration — commitez-les avec vos données.
- **Une règle = une responsabilité** : évitez les expressions de filtre trop complexes, décomposez.
- **Scope** : utilisez `scope` pour contrôler la portée des règles (`global`, `project`, `dataset`).

## Validation

```bash
gispulse validate rules/mon_pipeline.json
```

La validation vérifie :
- La capability existe dans le registre (`REGISTRY`)
- Les paramètres respectent le schéma JSON de la capability
- Les types sont corrects (vérification récursive)
- Les prédicats de triggers sont structurellement valides

Retourne code `0` si toutes les règles sont valides, `1` sinon. Intégrable en CI/CD.

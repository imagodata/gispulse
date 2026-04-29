---
title: Quickstart — Premiers triggers en 5 minutes
description: Installer GISPulse, attacher des triggers DML à un GeoPackage et exécuter le runtime headless.
---

# Quickstart

Vous aurez un GeoPackage qui réagit à chaque INSERT/UPDATE/DELETE en moins de 5 minutes — sans serveur, sans plugin QGIS, juste la CLI.

## Prérequis

- Python 3.10+
- `pipx` ([guide officiel](https://pipx.pypa.io/stable/installation/)) — recommandé pour isoler la CLI du Python système

## Étape 1 — Installer la CLI

```bash
pipx install gispulse
```

::: tip Pourquoi `pipx` ?
`pipx` installe GISPulse dans un environnement isolé : pas de pollution du Python système, pas de conflit avec `geopandas` / `pyproj` d'autres projets. Sur macOS Sonoma+ ou Debian récent, un `pip install gispulse` global échoue avec `error: externally-managed-environment` (PEP 668).

Si vous tenez à `pip` : `pip install --user gispulse` ou activez d'abord un virtualenv.
:::

Vérifiez :

```bash
gispulse --help
```

## Étape 2 — Récupérer un GeoPackage de test

Pour cette démo, on télécharge un échantillon de parcelles cadastrales (~7 MB) :

```bash
mkdir -p demo && cd demo
curl -L "https://raw.githubusercontent.com/imagodata/gispulse/main/examples/datasets/muret_parcels.gpkg" \
  -o parcels.gpkg

gispulse info parcels.gpkg
```

```
File:     parcels.gpkg
Format:   GPKG
Size:     6.91 MB
CRS:      EPSG:4326
Category: vector

1 layer(s):
  - parcels: 17212 features, Polygon, EPSG:4326
```

## Étape 3 — Installer le change-tracking

GISPulse pose des triggers SQLite `AFTER INSERT/UPDATE/DELETE` sur la couche cible. Toute modification — par QGIS, `ogr2ogr`, FME, ou n'importe quel client SQL — sera capturée dans une table interne `_gispulse_change_log`.

```bash
gispulse track install parcels.gpkg --layer parcels
```

```
gpkg_project_bootstrapped: 11 internal tables (schema v2)
change_tracking_installed: parcels (pk=fid, cols=9, geom=geom)
✓ Installed change tracking on 1 layer(s): parcels
```

Vérifiez l'installation :

```bash
gispulse track list parcels.gpkg
```

```
             Change tracking — parcels.gpkg
┏━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━┓
┃ Layer   ┃ Tracked ┃ Ops                  ┃ Pending ┃
┡━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━┩
│ parcels │    ✓    │ delete,insert,update │       0 │
└─────────┴─────────┴──────────────────────┴─────────┘
```

## Étape 4 — Écrire un trigger YAML

Créez `triggers.yaml` à côté du GPKG :

```yaml
version: 1
gpkg: ./parcels.gpkg

triggers:
  - name: tag_high_value_parcels
    table: parcels
    pk_col: fid
    when: [INSERT, UPDATE]
    predicate: "surface_cadastrale > 10000"
    actions:
      - type: set_field
        field: owner
        value: AUDIT_REQUIRED

runtime:
  poll_interval_ms: 1000
  max_batch: 200
```

Ce trigger tague chaque parcelle de plus d'1 hectare avec `owner = "AUDIT_REQUIRED"` quand elle est créée ou modifiée. Le DSL prédicat supporte `==` `!=` `>` `<` `AND` `OR` `IN` — sans `eval` ni dépendance externe (cf. [`docs/TRIGGERS_GUIDE.md`](https://github.com/imagodata/gispulse/blob/main/docs/TRIGGERS_GUIDE.md)).

Validez la config (syntaxe + schéma + références aux couches) :

```bash
gispulse triggers validate --config triggers.yaml
```

```
OK 1 trigger(s) valid against parcels.gpkg.
```

## Étape 5 — Modifier le GPKG depuis votre client habituel

Le change-tracking capte les modifications faites par n'importe quel client. Ouvrez `parcels.gpkg` dans **QGIS** (clic droit → Toggle Editing), modifiez quelques attributs, sauvegardez. Ou en CLI avec `ogr2ogr` :

```bash
ogr2ogr -f GPKG parcels.gpkg parcels.gpkg parcels \
  -dialect SQLite -sql "UPDATE parcels SET owner='test' WHERE fid IN (1, 2, 3)" \
  -update
```

Vérifiez les changements en attente :

```bash
gispulse track list parcels.gpkg
```

```
             Change tracking — parcels.gpkg
┏━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━┓
┃ Layer   ┃ Tracked ┃ Ops                  ┃ Pending ┃
┡━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━┩
│ parcels │    ✓    │ delete,insert,update │       3 │
└─────────┴─────────┴──────────────────────┴─────────┘
```

## Étape 6 — Exécuter le runtime

Mode **`--once`** : draine le change-log une fois, exécute les actions, sort. Idéal pour cron, AWS Lambda, hooks CI :

```bash
gispulse triggers run --config triggers.yaml --once
```

```
{"event":"runtime_starting","gpkg":"./parcels.gpkg","triggers":1,"mode":"once"}
{"event":"tick_done","processed":3}
OK one tick processed 3 change-log row(s) on parcels.gpkg.
```

Mode **`--watch`** : démon qui poll en continu, recharge la config sur changement de mtime, propre sur SIGINT/SIGTERM (drain de 2 s) :

```bash
gispulse watch parcels.gpkg --rules triggers.yaml
```

::: tip En production
Pour un déploiement durable, utilisez `packaging/systemd/gispulse-watch@.service` ou `packaging/docker/Dockerfile.watch`. Voir [`docs/INTEGRATION_MATRIX.md`](https://github.com/imagodata/gispulse/blob/main/docs/INTEGRATION_MATRIX.md).
:::

## Aller plus loin

| Objectif | Commande |
|----------|----------|
| Diagnostiquer une dérive de triggers | `gispulse track doctor parcels.gpkg --auto-fix` |
| Lister les actions installées | `gispulse triggers list --gpkg parcels.gpkg` |
| Voir les dernières lignes du change-log | `gispulse track tail parcels.gpkg` |
| Désinstaller le change-tracking | `gispulse track uninstall parcels.gpkg --layer parcels` |
| Lancer le portail visuel | `gispulse portal` |
| Liste des capabilities | `gispulse capabilities` |

- [Guide complet des triggers](https://github.com/imagodata/gispulse/blob/main/docs/TRIGGERS_GUIDE.md) — DSL prédicat, types d'actions, garde-fous SQL, payload v2
- [Référence CLI](/guide/cli)
- [Toutes les capabilities](/guide/capabilities)
- Intégrations : [QGIS](/integrations/qgis) · [ArcGIS](/integrations/arcgis) · [MapLibre](/integrations/maplibre)

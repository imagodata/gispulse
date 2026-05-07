---
title: Walkthrough — GeoJSON CDC
description: Surveiller un fichier GeoJSON, déclencher un webhook à chaque édition. Aucun GeoPackage requis, aucun plugin GIS-client. Ajouté en v1.6.2 (Format Frontier).
---

# Walkthrough — GeoJSON CDC

> **Promesse** : éditer `places.geojson` (dans QGIS, vim, un script Python, n'importe quoi) → GISPulse détecte le diff dans la seconde → webhook POST sur l'URL de ton choix.

Ce walkthrough utilise la nouvelle infrastructure **DuckDBDiffEngine** (v1.6.2). Pas de SQLite triggers, pas de companion files — juste mtime watch + DuckDB `ST_Read` snapshot diff. Le même pipeline marche avec FlatGeobuf, Shapefile, KML, CSV+WKT et MapInfo TAB.

## Ce que vous allez voir

Une couche **GeoJSON** de points d'intérêt. Chaque ajout / suppression / modification déclenche un webhook qui logge le payload dans la console.

| Avant | Après save |
|---|---|
| Fichier édité dans QGIS / vim, watcher inactif | `dml.changed` event broadcast → webhook POST avec `dataset_id`, `op`, `feature_id`, `geom_changed` |

## Prérequis

- `gispulse` ≥ 1.6.2 (`pipx install gispulse`)
- DuckDB spatial extension (auto-installée à la première utilisation, cf. `gispulse doctor --install-spatial`)
- Un endpoint HTTP pour recevoir les webhooks. Exemples : [webhook.site](https://webhook.site/) (test rapide), [requestbin.com](https://requestbin.com/), ou un mini-server Python local

## Setup (≈ 30 secondes)

### 1. Créer un GeoJSON de démo

```bash
mkdir -p ~/gispulse-demo && cd ~/gispulse-demo

cat > places.geojson <<'EOF'
{
  "type": "FeatureCollection",
  "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::4326"}},
  "features": [
    {"type": "Feature", "properties": {"name": "Paris", "population": 2140000},
     "geometry": {"type": "Point", "coordinates": [2.35, 48.85]}},
    {"type": "Feature", "properties": {"name": "Lyon", "population": 513000},
     "geometry": {"type": "Point", "coordinates": [4.83, 45.75]}},
    {"type": "Feature", "properties": {"name": "Marseille", "population": 868000},
     "geometry": {"type": "Point", "coordinates": [5.37, 43.30]}}
  ]
}
EOF
```

### 2. Écrire les règles

```yaml
# triggers.yaml
gpkg: ./places.geojson    # le runtime route ``.geojson`` vers ``duckdb_diff`` automatiquement

triggers:
  - name: notify_changes
    table: places          # nom = file stem
    when: [INSERT, DELETE]  # voir « semantics set-diff » plus bas
    actions:
      - type: webhook
        url: https://webhook.site/YOUR-UNIQUE-ID
```

> **Set-diff semantics — important** : un GeoJSON n'a pas de PK stable. Une **modification** d'une feature surface comme `DELETE` (ancien hash) + `INSERT` (nouveau hash). Le trigger doit donc déclarer `when: [INSERT, DELETE]` pour réagir aux deux côtés. Voir [Formats supportés](../formats.md) pour les détails.

### 3. Lancer la boucle de surveillance

```bash
gispulse triggers watch --rules triggers.yaml --dataset places.geojson
```

Le terminal affiche :

```text
[info] watching places.geojson via duckdb_diff engine
[info] first poll: 3 INSERT events emitted (initial baseline)
```

À ce point, le webhook a déjà reçu 3 POSTs (un par feature de la baseline). Si vous voulez démarrer "vide" sans baseline, supprimez le sidecar `places.geojson.gispulse-snapshot.duckdb` après le premier tick — le watcher repartira de zéro.

## Tester l'édition (≈ 1 min)

### Édition au clavier

```bash
# Ajouter Toulouse à la fin de la liste
python -c "
import json
data = json.load(open('places.geojson'))
data['features'].append({
    'type': 'Feature',
    'properties': {'name': 'Toulouse', 'population': 493000},
    'geometry': {'type': 'Point', 'coordinates': [1.44, 43.60]}
})
json.dump(data, open('places.geojson', 'w'), indent=2)
"
```

Le watcher détecte la modification dans la seconde :

```text
[info] mtime changed, re-reading via DuckDB ST_Read
[info] diff: 1 INSERT (toulouse hash), 0 DELETE
[info] webhook POSTed: 200 OK
```

Le webhook reçoit un payload :

```json
{
  "dataset_id": "places",
  "table": "places",
  "op": "INSERT",
  "fid": "<32-hex-hash>",
  "change_id": 4,
  "ts": "2026-05-07T22:13:01.234Z",
  "geom_changed": true
}
```

### Édition dans QGIS

Ouvrir `places.geojson` dans QGIS, basculer la couche en édition, modifier la coordonnée d'une feature, enregistrer (`Ctrl+S`). Le watcher remarque le changement :

```text
[info] diff: 1 INSERT (lyon hash with new coords), 1 DELETE (lyon old hash)
```

Deux webhooks POSTés (`DELETE` + `INSERT`) car le format n'a pas de PK stable. Le consommateur du webhook peut les corréler via le timestamp ou ignorer le `DELETE` si l'`INSERT` qui suit a un `feature_id` différent.

## Comment ça marche sous le capot

```
edit places.geojson (any tool)
        │
        ▼
mtime tick (default 200ms)
        │
        ▼
FileBlobChangeDetector.poll()
        │
        ▼
DuckDB ST_Read('places.geojson')  ← lit le fichier nativement
        │
        ▼
hash = md5(ST_AsWKB(geom) || json_object(props))   ← exclut OGC_FID
        │
        ▼
diff vs sidecar `.gispulse-snapshot.duckdb`
        │
        ▼
ChangeRecord{INSERT|DELETE} → webhook + WS broadcast
```

**Sidecar snapshot** : un fichier `.gispulse-snapshot.duckdb` est créé à côté du GeoJSON. Il contient le dernier état connu pour le diff. À ne pas commit dans git (ajouter `*.gispulse-snapshot.duckdb` au `.gitignore`).

## Limitations honnêtes

- **Pas de UPDATE détecté** — set-diff. Un edit surface comme DELETE+INSERT. Voir explication ci-dessus.
- **Polling 200ms par défaut** — pas inotify. Configurable via `--poll-interval`. Sub-seconde est ok, sub-milliseconde non.
- **Single-layer par fichier** — un FeatureCollection = un layer. Multi-layer = pipeline GeoPackage.
- **Pas d'`execute_sql` contre le GeoJSON** — `DuckDBDiffEngine` est un adapter CDC, pas un query engine. Pour ad-hoc SQL utilisez `gispulse run` avec engine `duckdb` standalone.

## Variantes

Le même `triggers.yaml` (juste l'extension de fichier change) marche avec :

| Format | Engine | Notes |
|---|---|---|
| `.fgb` | `duckdb_diff` | Single-file mtime, ultra-rapide |
| `.shp` | `duckdb_diff` | Watch les 5 companions (`.shp / .dbf / .shx / .prj / .cpg`) |
| `.kml` | `duckdb_diff` | Single-file mtime |
| `.csv` | `duckdb_diff` | Géométrie en colonne WKT (`GEOMETRY=AS_WKT` à l'écriture) |
| `.tab` | `duckdb_diff` (pyogrio fallback) | Watch les 4 companions ; route via pyogrio car DuckDB GDAL n'a pas le driver MapInfo |
| `.gpkg` | `gpkg` (SQLite triggers) | Mode natif, exact deltas, transactionnel |
| `.sqlite`, `.db` | `spatialite` (SQLite triggers) | Comme GPKG mais sans le marqueur GPKG |

Le contrat de hash est identique entre le path DuckDB et le path pyogrio (cf. `_PYOGRIO_FALLBACK_SUFFIXES` dans `persistence/file_blob_cdc.py`) — un même fichier produit les mêmes événements peu importe le chemin de lecture.

## Voir aussi

- [Formats I/O supportés](../formats.md) — section "CDC file-blob"
- [ADR 0001 — DuckDB-spatial = dialecte contrat](https://github.com/imagodata/gispulse/blob/main/docs/adr/0001-dsl-sql-dialect.md)
- [Walkthrough Parcelles](./parcels.md) — équivalent GPKG natif (SQLite triggers, exact deltas)
- [Walkthrough Audit](./audit.md) — pattern différent : `validate:` rules vs triggers

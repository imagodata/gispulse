---
title: Formats I/O supportés
description: Liste complète des formats de fichiers spatiaux supportés en lecture et écriture par GISPulse.
---

# Formats I/O supportés

GISPulse utilise [PyOGRIO](https://pyogrio.readthedocs.io/) pour la lecture et l'écriture vectorielle et [Rasterio](https://rasterio.readthedocs.io/) pour le raster. La détection du format est automatique basée sur l'extension du fichier.

```bash
# Lister les formats disponibles sur votre installation
gispulse formats
```

## Formats vectoriels

### Formats recommandés

| Extension | Format | Lecture | Écriture | Notes |
|-----------|--------|:-------:|:--------:|-------|
| `.gpkg` | GeoPackage | oui | oui | Recommandé — multi-layers, styles, performant |
| `.fgb` | FlatGeobuf | oui | oui | Ultra-rapide pour gros volumes, indexé spatialement |
| `.parquet` | GeoParquet | oui | oui | Optimal pour données tabulaires larges, natif DuckDB |
| `.geojson` | GeoJSON | oui | oui | Standard web, interopérable. **CDC v1.6.1** : engine `duckdb_diff` détecte INSERT/DELETE par mtime + DuckDB snapshot diff (UPDATE non détectable, voir notes ci-dessous). |
| `.fgb` | FlatGeobuf | oui | oui | Ultra-rapide, indexé spatialement. **CDC v1.6.1** : engine `duckdb_diff` (zero-code-change vs GeoJSON, single-file mtime). |
| `.geojsonl` | GeoJSON Lines | oui | oui | Streaming, gros volumes |

### Formats courants

| Extension | Format | Lecture | Écriture | Notes |
|-----------|--------|:-------:|:--------:|-------|
| `.shp` | ESRI Shapefile | oui | oui | Héritage — préférer GPKG. **CDC v1.6.1** : engine `duckdb_diff` watche les 5 companions (`.shp`/`.dbf`/`.shx`/`.prj`/`.cpg`) en `max(mtime)` — un edit attributaire-seulement (qui ne touche que `.dbf`) est détecté. |
| `.csv` | CSV (lat/lon ou WKT) | oui | oui | Détection auto colonnes géométrie |
| `.dxf` | AutoCAD DXF | oui | oui | CAD |
| `.kml` | KML / KMZ | oui | non | Google Earth |
| `.gml` | GML | oui | oui | OGC standard |
| `.gpx` | GPX | oui | non | GPS tracks |

### Formats bases de données

| Format | Lecture | Écriture | Notes |
|--------|:-------:|:--------:|-------|
| PostGIS | oui | oui | Via `GISPULSE_DSN` (Pro) |
| SpatiaLite (`.sqlite`, `.db`) | oui | oui | Engine `spatialite` v1.6.1 — DML triggers AFTER INSERT/UPDATE/DELETE comme GPKG, write-back via pyogrio (`SQLite + SPATIALITE=YES`). Auto-détection : `.sqlite`/`.db` route automatiquement vers cet engine ; un `.gpkg` reste route GPKG. |
| GeoDatabase ESRI (.gdb) | oui | non | Read-only |
| OGC WFS | oui | non | Via `OGCLayerLoader` (lazy loading) |
| OGC API Features | oui | non | Standard OGC moderne |

### CDC file-blob (v1.6.1)

L'engine `duckdb_diff` apporte la détection DML aux formats sans triggers natifs. Activé automatiquement pour `.geojson` (et progressivement `.fgb`, `.shp`, `.kml`, `.csv`, `.tab`, `.dxf`) — auto-routing depuis l'URI dans `triggers.yaml`.

**Mécanisme** : `mtime` watch + DuckDB `ST_Read` snapshot diff. Au premier poll chaque feature emerge en `INSERT`. À chaque édition (QGIS, vim, script tiers), le moteur compare le hash (`md5(WKB || properties)`) de chaque ligne contre le snapshot persistant en sidecar `.gispulse-snapshot.duckdb` à côté du fichier.

**Multi-file formats** : Shapefile (`.shp`) ne touche pas toujours `.shp` lors d'un edit (un changement attributaire ne touche que `.dbf`). Le détecteur watche donc `max(mtime)` sur les 5 companions `.shp`/`.dbf`/`.shx`/`.prj`/`.cpg` pour ne pas manquer ce cas. Single-file formats (GeoJSON/FGB/KML/CSV) restent en single-file mtime.

**Limitations connues v1.6.1** :
- `UPDATE` est **indétectable** (pas de PK stable dans le format) — un edit produit `DELETE` (vieux hash) + `INSERT` (nouveau hash). Déclarer `when: [INSERT, DELETE]` dans le trigger pour réagir aux deux côtés.
- Polling uniquement (pas d'inotify) — l'intervalle est fixé par le watcher loop.
- Single-layer par fichier (multi-layers = pipeline GPKG).
- Pas d'exécution SQL contre le fichier (`execute_sql` lève `NotImplementedError`) — pour ad-hoc SQL utilisez l'engine `duckdb` standalone via `gispulse run`.

### Lecture par lots (chunked)

Pour les fichiers volumineux, `read_vector_chunked()` permet une lecture par lots de 50 000 features, évitant de charger tout en mémoire.

### Formats raster (avec `gispulse[raster]`)

| Extension | Format | Lecture | Écriture |
|-----------|--------|:-------:|:--------:|
| `.tif`, `.tiff` | GeoTIFF | oui | oui |
| `.tif` (COG) | Cloud-Optimized GeoTIFF | oui | oui |
| `.jp2` | JPEG2000 | oui | non |
| `.asc` | ASCII Grid | oui | non |
| `.vrt` | GDAL VRT | oui | non |
| `.img` | ERDAS Imagine | oui | non |
| `.nc` | NetCDF | oui | non |
| `.hdf`, `.h5` | HDF5 | oui | non |
| `.ecw` | ECW | oui | non |
| `.sid` | MrSID | oui | non |
| `.png` | PNG (géoréférencé) | oui | non |

::: info Raster
Les formats raster nécessitent `pip install "gispulse[raster]"` (dépendance `rasterio`).
:::

## Détection automatique

GISPulse détecte le format à partir de l'extension :

```bash
# Format détecté automatiquement
gispulse run input.fgb --rules rules.json -o output.gpkg
gispulse run input.geojson --rules rules.json -o output.fgb
gispulse run input.shp --rules rules.json -o output.parquet
```

Si le fichier n'a pas d'extension reconnue, forcer avec `--layer` et `--crs`.

## Conseils de format

### Pour de gros volumes (> 100 000 features)

1. **FlatGeobuf** (`.fgb`) — lecture/écriture la plus rapide, indexé spatialement
2. **GeoParquet** (`.parquet`) — excellent pour les dizaines de colonnes attributaires, natif DuckDB
3. **GPKG** — polyvalent, supporte les styles

### Pour l'interopérabilité GIS desktop

- **GeoPackage** (`.gpkg`) — supporte les styles QGIS (QML) et SLD, multi-layers
- GISPulse copie automatiquement les styles lors d'un pipeline `--all-layers`

### Pour le web / API

- **GeoJSON** — standard universel, lisible humainement
- **FlatGeobuf** — streaming performant pour les grandes datasets côté client
- **MVT** — tuiles vectorielles via l'endpoint `/ogc/collections/{id}/tiles/`

### Pour la data spatiale "moderne"

- **GeoParquet** — compatible DuckDB, Pandas, Arrow, cloud-native
- **COG** — Cloud-Optimized GeoTIFF pour le raster

## Multi-layers (GPKG)

GeoPackage supporte plusieurs layers dans un seul fichier :

```bash
# Traiter une layer spécifique
gispulse run projet.gpkg --rules rules.json -o output.gpkg --layer batiments

# Traiter toutes les layers (styles copiés)
gispulse run projet.gpkg --rules rules.json -o output.gpkg --all-layers
```

```bash
# Inspecter les layers d'un GPKG
gispulse layers projet.gpkg

3 layer(s):
  - parcelles
  - batiments
  - routes
```

---
title: CLI — Référence complète
description: Documentation de toutes les commandes GISPulse CLI, leurs options et exemples d'utilisation.
---

# CLI — Référence complète

GISPulse s'installe comme commande `gispulse`. Toutes les commandes sont headless et scriptables.

```bash
gispulse --help
```

## Commandes disponibles

| Commande | Description |
|----------|-------------|
| `init` | Scaffolde un nouveau projet |
| `run` | Exécute un pipeline de règles sur un fichier spatial |
| `validate` | Valide un fichier de règles sans exécuter |
| `info` | Inspecte les métadonnées d'un fichier spatial |
| `layers` | Liste les layers d'un fichier spatial |
| `formats` | Liste les formats I/O supportés |
| `capabilities` | Liste les capabilities disponibles |
| `serve` | Lance le viewer embarqué pour un fichier |
| `portal` | Lance le Portal web (éditeur visuel) |
| `engine` | Lance le moteur complet (API + Portal + Viewer) |
| `doctor` | Diagnostique l'environnement |
| `update` | Vérifie et applique les mises à jour |
| `jobs` | Gestion des jobs (list, status, cancel) |
| `marketplace` | Marketplace de capabilities (list, search, install) |
| `template` | Templates de projets (list, use) |
| `telemetry` | Télémétrie anonyme opt-in (status / enable / disable) |

---

## `gispulse init`

Scaffolde un nouveau projet GISPulse avec des templates de règles et un Makefile.

```bash
gispulse init [DIRECTORY] [OPTIONS]
```

**Options :**

| Option | Description |
|--------|-------------|
| `DIRECTORY` | Répertoire à initialiser (défaut : `.`) |
| `--name`, `-n TEXT` | Nom du projet (défaut : nom du répertoire) |

**Résultat :**

```
mon-projet/
├── rules/rules.json    # template de règles
├── data/               # vos fichiers spatiaux
├── output/             # résultats
└── Makefile            # make run / make validate / make view
```

---

## `gispulse run`

Exécute un pipeline de règles sur un fichier spatial. C'est la commande principale.

```bash
gispulse run INPUT_FILE --rules RULES_FILE --output OUTPUT_FILE [OPTIONS]
```

**Arguments requis :**

| Argument/Option | Description |
|-----------------|-------------|
| `INPUT_FILE` | Fichier spatial d'entrée (16+ formats supportés) |
| `--rules`, `-r` | Fichier de règles JSON |
| `--output`, `-o` | Fichier de sortie (format détecté depuis l'extension) |

**Options :**

| Option | Description |
|--------|-------------|
| `--layer`, `-l TEXT` | Nom de la layer à traiter (défaut : première layer) |
| `--output-layer TEXT` | Nom de la layer dans le fichier de sortie |
| `--all-layers`, `-A` | Traiter toutes les layers (formats multi-layer, ex: GPKG) |
| `--crs TEXT` | Forcer le CRS d'entrée (ex: `EPSG:4326`) si absent du fichier |
| `--ref-source TEXT` | Layer de référence externe au format `NOM:CHEMIN` (répétable) |
| `--engine`, `-e TEXT` | Moteur : `python` (défaut) ou `duckdb` |
| `--verbose`, `-v` | Activer les logs DEBUG |

**Exemples :**

```bash
# Pipeline simple
gispulse run data/parcelles.gpkg \
  --rules rules/filtrage.json \
  -o output/resultat.gpkg

# Moteur DuckDB (plus rapide sur gros volumes)
gispulse run data/communes.gpkg \
  --rules rules/rules.json \
  -o output/result.gpkg \
  --engine duckdb

# Toutes les layers d'un GPKG (styles copiés automatiquement)
gispulse run data/projet.gpkg \
  --rules rules/global.json \
  -o output/projet_enrichi.gpkg \
  --all-layers

# Avec layer de référence (pour spatial join, clip, etc.)
gispulse run data/batiments.gpkg \
  --rules rules/analyse.json \
  -o output/batiments_enrichis.gpkg \
  --ref-source communes:data/communes.gpkg \
  --ref-source zones:data/zonage.gpkg
```

**Sortie :**

```
Loading data/parcelles.gpkg (GPKG) [engine: python] ...
  [filter] filter_agricole
  [buffer] buffer_10m
  [reproject] vers_wgs84
  1247 features in -> 892 features out
  3 rule(s) applied [engine: python]
Output written to output/resultat.gpkg (.gpkg)
```

---

## `gispulse validate`

Valide un fichier de règles JSON sans exécuter de traitement. Utile en CI/CD.

```bash
gispulse validate RULES_FILE
```

```
  OK    filter_agricole
  OK    buffer_10m
  FAIL  reproject_vers_wgs84
        - [config.crs] CRS 'EPSG:9999' invalide

Validation failed.
```

Retourne code de sortie `1` si une règle est invalide — intégrable dans un pipeline CI.

---

## `gispulse info`

Inspecte les métadonnées d'un fichier spatial : format, CRS, layers, feature count, styles.

```bash
gispulse info INPUT_FILE
```

```
File:     data/projet.gpkg
Format:   GPKG
Size:     12.43 MB
CRS:      EPSG:2154
Category: vector

3 layer(s):
  - parcelles: 8420 features, Polygon, EPSG:2154
  - batiments: 12841 features, MultiPolygon, EPSG:2154
  - routes: 3201 features, LineString, EPSG:2154

2 style(s):
  - parcelles/parcelles_style (QML + SLD)
  - batiments/batiments_style (QML)
```

---

## `gispulse layers`

Liste les noms des layers dans un fichier spatial.

```bash
gispulse layers INPUT_FILE
```

```
3 layer(s):
  - parcelles
  - batiments
  - routes
```

---

## `gispulse formats`

Liste tous les formats d'entrée/sortie supportés.

```bash
gispulse formats
```

---

## `gispulse capabilities`

Liste toutes les capabilities disponibles avec leurs paramètres.

```bash
gispulse capabilities
```

```
27 capability(ies):
  Vector (Community):
    buffer, filter, reproject, clip, intersects, spatial_join,
    centroid, area_length, dissolve, union, calculate, spatial_aggregate
  Validation (Community):
    topology_check, duplicate_geometry, attribute_validation, completeness_check
  Raster (Pro):
    zonal_stats, raster_clip, ndvi, raster_reproject, raster_merge, change_detection
  Network (Pro):
    shortest_path, isochrone, network_allocation, connectivity_check
  SQL (Pro):
    postgis_sql
```

---

## `gispulse serve`

Lance le viewer spatial embarqué pour un fichier spatial (lecture seule).

```bash
gispulse serve INPUT_FILE [OPTIONS]
```

| Option | Défaut | Description |
|--------|--------|-------------|
| `--port`, `-p` | `8765` | Port d'écoute |
| `--host` | `127.0.0.1` | Hôte |
| `--dev` | `false` | Mode dev : API seulement, pas de fichiers statiques |

```bash
gispulse serve output/result.gpkg --port 9000
# Viewer at http://127.0.0.1:9000
```

---

## `gispulse portal`

Lance le Portal GISPulse — workbench visuel (canvas de noeuds, registre de capabilities, gestionnaire de datasets) servi par le moteur local. Nécessite le package optionnel `gispulse-portal`. Référence détaillée : [Lancer le portail localement](/guide/portal-local).

```bash
gispulse portal [OPTIONS]
```

| Option | Défaut | Description |
|--------|--------|-------------|
| `--port`, `-p` | `8001` | Port d'écoute (mode local). |
| `--host` | `127.0.0.1` | Hôte (mode local). |
| `--data-dir`, `-d` | `~/.gispulse/data` | Répertoire pour les datasets uploadés. |
| `--backend URL` | — | Mode remote : ouvre le portail GH Pages pointé sur un moteur distant. |
| `--no-browser` | `false` | Ne pas ouvrir le navigateur. |
| `--dev` | `false` | Autorise le fallback sur `portal/dist/` du checkout (workflow contributeur). |

```bash
# Local (par défaut)
gispulse portal
# GISPulse Portal at http://127.0.0.1:8001/portal/

# Remote (pas de moteur local)
gispulse portal --backend=https://api.example.com
```

---

## `gispulse engine`

Lance le moteur GISPulse en headless (API REST + WebSocket, **sans SPA**). Utilisé par le sidecar Tauri, les déploiements serveur et les intégrations tierces. Pour un workbench visuel local, voir [`gispulse portal`](#gispulse-portal) et le guide [Lancer le portail localement](/guide/portal-local).

```bash
gispulse engine [OPTIONS]
```

| Option | Défaut | Description |
|--------|--------|-------------|
| `--port`, `-p` | `0` (auto) | Port d'écoute (`0` = port libre auto-détecté pour Tauri) |
| `--host` | `127.0.0.1` | Hôte |
| `--engine`, `-e` | `duckdb` | Backend spatial (`duckdb`, `postgis`, `hybrid`) |
| `--data-dir`, `-d` | `~/.gispulse/data` | Répertoire des datasets |
| `--no-browser` | `false` | Ne pas ouvrir le navigateur |

Émet un JSON de démarrage sur stdout pour le sidecar Tauri :

```
GISPULSE_READY:{"port": 8001, "host": "127.0.0.1", "engine": "duckdb", "pid": 12345}
```

Référence détaillée : [Lancer le moteur](/guide/engine).

---

## `gispulse doctor`

Diagnostique complet de l'environnement : Python, GDAL, DuckDB, PostGIS, dépendances optionnelles, espace disque, OIDC.

```bash
gispulse doctor
```

```
✓ GISPulse    v1.1.1
✓ Python      v3.12.3
✓ GDAL        v3.8.4
✓ DuckDB      v1.1.3 + spatial OK
✓ GeoPandas   v0.14.3
✓ PyOGRIO     v0.9.0
⚠ PostGIS     not configured (set GISPULSE_DSN)
⚠ Rasterio    not installed (pip install "gispulse[raster]")
✓ API         FastAPI 0.111.x
✓ Disk        42.3 GB free
```

---

## `gispulse update`

Vérifie et applique les mises à jour. Détecte automatiquement le mode d'installation (pip/conda/snap).

```bash
# Vérifier sans installer
gispulse update --check

# Mettre à jour
gispulse update --force
```

Cache le résultat de la vérification pendant 5 minutes.

---

## `gispulse jobs`

Sous-groupe de gestion des jobs distants (requiert un serveur API en cours d'exécution).

```bash
# Lister les jobs
gispulse jobs list [--host HOST] [--api-key KEY]

# Statut d'un job
gispulse jobs status JOB_ID [--host HOST] [--api-key KEY]

# Annuler un job
gispulse jobs cancel JOB_ID [--host HOST] [--api-key KEY]
```

---

## `gispulse marketplace`

Marketplace de capabilities — recherche, installation et gestion des plugins.

```bash
# Lister les capabilities (installées + disponibles)
gispulse marketplace list [QUERY]

# Rechercher dans le marketplace
gispulse marketplace search QUERY

# Installer un plugin
gispulse marketplace install NAME

# Désinstaller
gispulse marketplace uninstall NAME

# Détails d'un plugin
gispulse marketplace info NAME
```

---

## `gispulse template`

Gestion des templates de projets pour le scaffolding.

```bash
# Lister les templates disponibles
gispulse template list

# Créer un projet depuis un template
gispulse template use TEMPLATE [--output-dir DIR]
```

Templates inclus :
- `environmental_monitoring` — Pipeline de monitoring environnemental (NDVI, STAC)
- `ftth_network_analysis` — Analyse réseau FTTH
- `validation_plu_cnig` — Validation PLU conforme CNIG

---

## `gispulse telemetry`

Gère la télémétrie anonyme **opt-in**. Aucune donnée n'est transmise tant que `--enable` n'a pas été exécuté. Les identifiants de projet et chemins sont exclus par construction.

```bash
# Voir le statut courant
gispulse telemetry --status

# Activer
gispulse telemetry --enable

# Désactiver
gispulse telemetry --disable
```

| Option | Description |
|--------|-------------|
| `--status`, `-s` | Affiche le statut courant (enabled / disabled / path du flag) |
| `--enable` | Active la télémétrie (crée `~/.config/gispulse/telemetry.enabled`) |
| `--disable` | Désactive la télémétrie |

Équivaut aux variables d'environnement `GISPULSE_TELEMETRY=1` / `GISPULSE_TELEMETRY=0` pour les environnements scriptés.

---

## Utilisation en CI/CD

```yaml
# .github/workflows/validate.yml
- name: Validate GISPulse rules
  run: |
    pip install gispulse
    gispulse validate rules/rules.json

- name: Run spatial pipeline
  run: |
    gispulse run data/input.gpkg \
      --rules rules/pipeline.json \
      -o output/result.gpkg \
      --engine duckdb
```

Codes de sortie : `0` = succès, `1` = erreur (règle invalide, fichier manquant, etc.).

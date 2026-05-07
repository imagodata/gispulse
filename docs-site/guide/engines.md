---
title: Moteurs DuckDB / PostGIS / Hybrid
description: Comprendre les trois modes d'exécution GISPulse — Python/DuckDB local, PostGIS persistant, mode hybride.
---

# Moteurs d'exécution

GISPulse supporte trois modes d'exécution. Le moteur se configure par variable d'environnement, par ligne de commande ou par règle.

> **À lire avant** : [Contrat de dialecte SQL](./dsl-sql-dialect.md). Le DSL `triggers.yaml` est écrit en dialecte DuckDB-spatial par défaut. Les règles `run_sql` et les fonctions `geom_*()` ne sont pas portables sur PostGIS sans déclarer explicitement `engine:`.

## Vue d'ensemble

| Moteur | Tier | Usage | Volumes |
|--------|------|-------|---------|
| GPKG (GeoPandas) | Community | **Défaut**, mode portable | < 50k features |
| DuckDB | Community | Accélération locale | 50k – 10M features |
| PostGIS | Pro | Persistance, triggers, multi-user | Illimité |
| Hybride | Pro | DuckDB pour calcul + PostGIS pour stockage | Illimité |

## Interface SpatialEngine

Tous les moteurs implémentent l'interface commune `SpatialEngine` :

```python
class SpatialEngine(ABC):
    def open(self) -> None: ...
    def close(self) -> None: ...
    def load_layer(self, source, layer_name=None) -> GeoDataFrame: ...
    def write_layer(self, gdf, target, layer_name=None) -> None: ...
    def list_layers(self) -> list[str]: ...
    def execute_sql(self, sql, params=None) -> Any: ...
    def sql_to_gdf(self, sql, params=None, geom_col="geom") -> GeoDataFrame: ...
    def register(self, name, gdf) -> None: ...

    @property
    def backend_name(self) -> str: ...   # "duckdb", "postgis", "hybrid"
    @property
    def is_persistent(self) -> bool: ... # False pour DuckDB, True pour PostGIS
```

Un wrapper `AsyncSpatialEngine` est disponible pour l'intégration FastAPI.

## Mode GPKG (GeoPandas) — défaut

Moteur par défaut depuis v1.0.2. Utilise GeoPandas + Shapely avec GPKG natif comme format de stockage portable.

```bash
gispulse run input.gpkg --rules rules.json -o output.gpkg --engine gpkg
```

**Quand l'utiliser :**
- Datasets < 50 000 features
- Pas besoin de persistance serveur
- Mode offline / portable
- Environnement sans PostGIS

**Limites :**
- Tout en mémoire RAM
- Pas de persistance entre sessions
- Moins performant sur gros volumes

## Mode DuckDB

Accélération vectorisée via DuckDB + l'extension spatial. `DuckDBSession` gère le cycle de vie des sessions in-memory.

```bash
gispulse run input.gpkg --rules rules.json -o output.gpkg --engine duckdb
```

**Quand l'utiliser :**
- Datasets de 50 000 à plusieurs millions de features
- Calculs attributaires intensifs (agrégations, jointures)
- Mode offline / sans serveur
- GeoParquet en entrée (natif DuckDB)

**Avantages :**
- Vectorisation SIMD automatique
- Lecture GeoParquet native (format columnar)
- Multi-threading automatique
- Lecture GPKG native via `ST_Read`
- Sérialisation WKB automatique

**Limites :**
- Opérations spatiales moins complètes qu'avec PostGIS
- Pas de persistance côté serveur
- Pas de triggers ni pg_notify

### Sélection automatique DuckDB

Certaines capabilities basculent automatiquement sur DuckDB quand le volume dépasse 50 000 features et que le moteur `duckdb` est actif :

- `buffer` — `_BufferDuckDBStrategy` (priorité 80)
- `filter` — `_FilterDuckDBStrategy` avec `ExpressionConverter`
- `area_length` — calculs vectorisés

## Mode PostGIS (Pro)

Délègue les traitements à un serveur PostgreSQL/PostGIS via `PostGISConnection`. Fournit persistance, triggers, multi-user et opérations SQL avancées.

**Prérequis :**
- `pip install "gispulse[postgis]"`
- PostgreSQL 14+ avec extension PostGIS 3.x
- Variable d'environnement `GISPULSE_DSN`

**Configuration :**

```bash
# .env
GISPULSE_ENGINE=postgis
GISPULSE_DSN=postgresql://gispulse:secret@localhost:5432/gispulse
```

**Avantages :**
- Persistance des datasets côté serveur
- Connection pooling (pool_size=20, max_overflow=30)
- Triggers via `pg_notify` (réactivité temps réel)
- Opérations SQL avancées (ST_* complet)
- Multi-user avec RBAC (tier Team)
- Index spatial automatique sur écriture
- Pipelines cron

**Quand l'utiliser :**
- Données partagées entre plusieurs utilisateurs
- Besoin de triggers ou de cron
- Volumes > quelques millions de features
- Environnement production

### Démarrer avec PostGIS en Docker

```bash
docker run -d \
  --name gispulse-postgres \
  -e POSTGRES_USER=gispulse \
  -e POSTGRES_PASSWORD=secret \
  -e POSTGRES_DB=gispulse \
  -p 5432:5432 \
  postgis/postgis:16-3.4
```

```bash
GISPULSE_ENGINE=postgis \
GISPULSE_DSN=postgresql://gispulse:secret@localhost:5432/gispulse \
gispulse portal
```

## Mode hybride (Pro)

Combine DuckDB pour les calculs intensifs locaux et PostGIS pour la persistance et les données partagées. Géré par `HybridEngine` + `DuckDBPostGISBridge`.

```
Données locales → DuckDB (calcul rapide) → PostGIS (stockage)
                                         ← PostGIS (référence)
```

**Configuration :**

```bash
GISPULSE_ENGINE=hybrid
GISPULSE_DSN=postgresql://gispulse:secret@localhost:5432/gispulse
```

Le mode hybride est géré automatiquement par le `SessionManager` — il choisit DuckDB pour les opérations sur les données locales et PostGIS pour les lookups sur les tables persistantes.

## Pattern Strategy

Chaque capability peut déclarer plusieurs stratégies d'exécution backend-aware :

```python
class ExecutionStrategy(ABC):
    mode: StrategyMode      # PYTHON (10), DUCKDB (80), POSTGIS (100)
    def can_execute(self, ctx: ExecutionContext) -> bool: ...
    def execute(self, gdf, ctx) -> GeoDataFrame: ...
```

Le `select_strategy()` choisit la stratégie éligible de plus haute priorité. Fallback Python toujours disponible.

| Stratégie | Priorité | Condition |
|-----------|----------|-----------|
| Python (GeoPandas) | 10 | Toujours éligible |
| DuckDB | 80 | Backend DuckDB actif + > 50k features |
| PostGIS | 100 | Backend PostGIS actif |

## Engine Factory

Le moteur est instancié via `create_spatial_engine()` :

```python
from persistence.engine_factory import create_spatial_engine

engine = create_spatial_engine(
    backend="postgis",        # ou "duckdb", "hybrid"
    dsn="postgresql://...",
    duckdb_path=":memory:"
)
```

Ou automatiquement via la variable d'environnement `GISPULSE_ENGINE`.

## Recommandations par cas d'usage

### Analyste GIS solo, données locales

```bash
# Mode Python — simple et direct
gispulse run data.gpkg --rules rules.json -o output.gpkg
```

### Pipeline batch sur gros volume

```bash
# DuckDB — performance maximale
gispulse run data_10M.gpkg --rules rules.json -o output.gpkg --engine duckdb
```

### Déploiement équipe / SaaS

```bash
# PostGIS — persistance et multi-user
GISPULSE_ENGINE=postgis \
GISPULSE_DSN=postgresql://... \
gispulse portal --host 0.0.0.0
```

### CI/CD automatisé

```yaml
- run: gispulse run data.gpkg --rules rules.json -o output.gpkg --engine duckdb
```

## Diagnostiquer le moteur utilisé

La commande `run` indique toujours le moteur effectivement utilisé dans la sortie :

```
2 rule(s) applied [engine: duckdb]
```

Pour plus de détails, activez le mode verbose :

```bash
gispulse run ... --verbose
```

Les logs structurés (JSON) indiquent pour chaque capability quelle stratégie a été sélectionnée.

## v1.6.0 — DuckDB Spatial Inside

À partir de la v1.6.0, GISPulse traite **DuckDB Spatial** comme moteur compute
universel sous-jacent à tous les engines vecteur. Le DSL `triggers.yaml`
appelle des fonctions géométriques (`geom_area_m2`, `geom_within`, …)
qui se compilent en SQL DuckDB push-down ; les write-back continuent de
passer par l'adapter natif (pyogrio pour les fichiers, asyncpg pour PostGIS).

### Lazy install de l'extension spatial

L'extension `duckdb-spatial` n'est plus chargée au démarrage. Le premier appel
à une fonction geom du DSL déclenche `INSTALL spatial; LOAD spatial;` (≈10 s
sur réseau standard, puis cached). Le résultat est mis en cache pour la durée
du processus.

Pour pré-installer explicitement (CI, environnements air-gapped) :

```bash
gispulse doctor --install-spatial
```

La commande probe également quelques EPSG critiques (4326, 3857, 2154, 27572)
et flague les transformations qui dévient au-delà de la tolérance —
indicateur classique d'une grille de datum-shift manquante (NTF→RGF93 par ex.).

### Inférence du moteur depuis l'URI

En v1.6+, vous pouvez omettre `engine:` et laisser GISPulse inférer depuis l'URI :

| URI | Engine inféré |
|---|---|
| `*.gpkg` | `gpkg` |
| `*.sqlite`, `*.db` | `spatialite` |
| `postgresql://…`, `postgres://…`, `postgis://…` | `postgis` |
| `*.shp`, `*.geojson`, `*.fgb`, `*.kml`, `*.tab`, `*.csv` | `duckdb_diff` (file-blob CDC, v1.6.1+) |

Override possible via `engine:` dans le YAML — la combinaison URI / override est
validée à la lecture du config et lève une erreur si elle est incompatible
(ex. `engine: postgis` sur un fichier `.gpkg`).

### Verbes DML granulaires

Les triggers acceptent maintenant `UPDATE_GEOM` et `UPDATE_ATTR` en plus de
`UPDATE` (qui reste un alias catch-all). Le watcher résout chaque ligne du
change log en variant fin via le flag `geom_changed` capturé au moment de
l'AFTER UPDATE trigger SQLite. Voir
[Migrating from ESRI](./migration-from-esri.md#triggering-events-granular-update).

### Bench R1 — write-back DuckDB vs pyogrio

Mesuré 2026-05-06 sur 1 M polygones EPSG:2154 :

| Scénario | pyogrio (s) | DuckDB COPY (s) | Ratio | RSS pyogrio | RSS DuckDB |
|---|---:|---:|---:|---:|---:|
| Append +100k | 8.19 | **3.63** | 2.26× | 950 MB | **273 MB** |
| Update attr | 6.94 | **2.75** | 2.52× | 839 MB | **255 MB** |
| Update geom | 8.87 | **2.47** | 3.59× | 843 MB | **275 MB** |

DuckDB `COPY (FORMAT GDAL, DRIVER 'GPKG', SRS 'EPSG:2154')` est **plus rapide
que pyogrio sur les bulk writes ≤ 5 M lignes**. La doctrine "pyogrio-only
write-back" v1.5.x est officiellement sub-optimale ; l'engine GPKG bascule
sur DuckDB COPY pour les bulk imports en v1.6+, avec fallback pyogrio
forcé sur les datasets >5 M lignes ou les GPKG portant des triggers / vues
custom (write GDAL ne préserve pas tout).


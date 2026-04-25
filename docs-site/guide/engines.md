---
title: Moteurs DuckDB / PostGIS / Hybrid
description: Comprendre les trois modes d'exécution GISPulse — Python/DuckDB local, PostGIS persistant, mode hybride.
---

# Moteurs d'exécution

GISPulse supporte trois modes d'exécution. Le moteur se configure par variable d'environnement, par ligne de commande ou par règle.

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

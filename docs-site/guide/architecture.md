---
title: Architecture & concepts
description: Architecture interne de GISPulse — concepts fondamentaux, flux d'execution, moteurs, pipeline de regles et modele d'erreurs.
---

# Architecture & concepts

Ce guide explique comment GISPulse fonctionne sous le capot : les concepts fondamentaux, le flux d'exécution et l'abstraction des moteurs.

---

## Architecture globale

```
┌──────────────────────────────────────────────────────────┐
│                       ADAPTERS                           │
│  CLI   REST API   SDK Python   MCP   QGIS   ArcGIS      │
└──────────────┬───────────────────────────────────────────┘
               │
┌──────────────▼───────────────────────────────────────────┐
│                     ORCHESTRATION                        │
│  PipelineExecutor   JobRunner   GraphExecutor   Scheduler │
└──────────────┬───────────────────────────────────────────┘
               │
┌──────────────▼───────────────────────────────────────────┐
│                        RULES                             │
│  RuleEngine   Loader   Validator   PredicateEvaluator    │
└──────────────┬───────────────────────────────────────────┘
               │
┌──────────────▼───────────────────────────────────────────┐
│                     CAPABILITIES                         │
│  Vector   Raster   Network   Validation   PostGIS SQL    │
└──────────────┬───────────────────────────────────────────┘
               │
┌──────────────▼───────────────────────────────────────────┐
│                     PERSISTENCE                          │
│  DuckDB   PostGIS   Hybrid   GPKG I/O   Raster I/O      │
└──────────────┬───────────────────────────────────────────┘
               │
┌──────────────▼───────────────────────────────────────────┐
│                    ESB / TRIGGERS                        │
│  TriggerManager   pg_notify   EventRouter   ActionDispatcher │
└──────────────┬───────────────────────────────────────────┘
               │
┌──────────────▼───────────────────────────────────────────┐
│                        CORE                              │
│  Dataset  Layer  Job  Rule  Trigger  Scenario  Project   │
└──────────────────────────────────────────────────────────┘
```

Les couches sont organisées de l'extérieur vers l'intérieur. Chaque couche ne dépend que des couches situées en dessous. Les **adapters** n'importent jamais directement le **core** — ils passent par l'orchestration et les rules.

---

## Concepts fondamentaux

### Organisation des modèles

Les types centraux sont répartis dans 6 modules thématiques sous `core/` :

| Module | Contenu |
|--------|---------|
| `core/models.py` | `Dataset`, `Layer`, `Job`, `Rule`, `Trigger`, `Scenario`, `Project` |
| `core/enums.py` | `JobStatus`, `TriggerEvent`, `TriggerType`, `DataCategory`, etc. |
| `core/conditions.py` | `AttrPredicate`, `GeomPredicate`, `CompoundPredicate` |
| `core/predicates.py` | Évaluation des prédicats sur GeoDataFrame |
| `core/graph.py` | `NodeDef`, `EdgeDef`, `GraphSpec` |
| `core/relations.py` | `TableRelation`, `RelationType` |
| `core/pipeline.py` | `PipelineSpec`, `StepSpec`, `TriggerSpec` |
| `core/config.py` | Configuration centralisée Pydantic Settings (13 groupes, proxy lazy) |
| `core/capability_params.py` | TypedDict pour 10 capabilities (`FilterParams`, `BufferParams`, etc.) |

### Dataset

Unité de données source. Un dataset pointe vers un fichier local (GPKG, GeoJSON, Shapefile, GeoParquet, FlatGeobuf, etc.), une table PostGIS ou un service OGC distant.

```python
@dataclass
class Dataset:
    id: str              # UUID
    name: str            # Identifiant humain
    source_path: str     # Chemin fichier ou URI
    metadata: dict       # Métadonnées arbitraires
    created_at: datetime
    data_category: DataCategory  # VECTOR, RASTER, NETWORK, ...
    crs: str | None      # CRS source (détecté si absent)
    format: str | None   # "gpkg", "geojson", "parquet", ...
    ogc_source: OGCSourceConfig | None  # WFS/OGC API Features
```

**Catégories de données** : `VECTOR`, `RASTER`, `POINT_CLOUD`, `MESH_3D`, `NETWORK`, `TABULAR_GEO`, `SPATIO_TEMPORAL`

### Layer

Représentation logique d'une couche spatiale à l'intérieur d'un dataset. Un GPKG peut contenir plusieurs layers.

```python
@dataclass
class Layer:
    id: str
    dataset_id: str
    name: str
    geometry_type: str    # Polygon, LineString, Point, ...
    srid: int
    layer_type: str
    has_z: bool
    has_m: bool
    feature_count: int
```

### Job

Unité d'exécution. Un job encapsule l'application d'un pipeline de règles sur un dataset, avec son résultat et ses métadonnées.

```python
@dataclass
class Job:
    id: str
    name: str
    status: JobStatus     # PENDING, RUNNING, COMPLETED, FAILED
    dataset_id: str
    parameters: dict      # Inclut rule_ids, options
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    result_path: str | None
    error_message: str | None
    attempts: int         # Nombre de tentatives
    max_retries: int      # Retries automatiques
```

### Rule

Déclaration d'une opération spatiale à appliquer. C'est l'unité atomique du système rules-as-config.

```python
@dataclass
class Rule:
    id: str
    name: str
    description: str
    scope: str           # "global", "plan", "user", "project", "dataset"
    capability: str      # Nom de la capability
    config: dict         # Paramètres + order
    enabled: bool
```

### Trigger

Déclencheur réactif qui évalue des conditions et exécute des actions quand un événement se produit.

```python
@dataclass
class Trigger:
    id: str
    name: str
    event: TriggerEvent      # DATA_CHANGED, FEATURE_CREATED, ...
    trigger_type: TriggerType # DML, THRESHOLD, COMPOSITE, SCHEDULE, ...
    category: TriggerCategory # DATA, TEMPORAL, BUSINESS_RULE, ...
    rule_id: str | None
    predicates: list          # GeomPredicate, AttrPredicate, CompoundPredicate
    predicate_logic: str      # "AND" | "OR"
    actions: list[ActionDef]  # NOTIFY, SET_FIELD, RUN_JOB, WEBHOOK, ...
    enabled: bool
    auto_eval: bool
```

**14 types d'événements** : `DATA_CHANGED`, `GEOMETRY_CHANGED`, `FEATURE_CREATED`, `FEATURE_UPDATED`, `FEATURE_DELETED`, `LAYER_ADDED`, `THRESHOLD_CROSSED`, `JOB_COMPLETED`, etc.

**14 types d'actions** : `NOTIFY`, `SET_FIELD`, `UPDATE_AGGREGATE`, `RUN_JOB`, `RUN_GRAPH`, `WEBHOOK`, `ENQUEUE`, `LOG_EVENT`, `SEND_EMAIL`, `RUN_SQL`, etc.

#### Décisions de scope (ADRs)

GISPulse documente les semantics de triggers via les ADRs :

- **[ADR 0001](https://github.com/imagodata/gispulse/blob/main/docs/adr/0001-dsl-sql-dialect.md)** — DuckDB-spatial est le dialecte contrat du DSL (`set_field`, `validate:`, `run_sql`). `engine:` override autorisé pour PostGIS / SpatiaLite.
- **[ADR 0002](https://github.com/imagodata/gispulse/blob/main/docs/adr/0002-trigger-cascade-semantics.md)** — Cascade = bounded fixed-point (max 3) avec origin-tagging au niveau SQLite. Community capé à 1, Pro jusqu'à 3, fail-fast au-delà.
- **[ADR 0003](https://github.com/imagodata/gispulse/blob/main/docs/adr/0003-changelog-replay-out-of-scope.md)** — `_gispulse_change_log` reste un poll log, pas un event store. Replay / time-travel / mirror reportés à v1.7+ via table d'extension.
- **[ADR 0004](https://github.com/imagodata/gispulse/blob/main/docs/adr/0004-ddl-hooks-out-of-scope.md)** — Pas de hooks `on_alter_table` / `on_drop_table`. La détection schema-drift passive (B-13, watchdog) couvre le cas commun ALTER TABLE ADD COLUMN.

### Scenario

Pipeline multi-jobs composable. Supporte l'exécution séquentielle, indépendante et le mode graphe (DAG).

```python
@dataclass
class Scenario:
    id: str
    name: str
    dataset_id: str
    jobs: list[str]       # IDs des jobs
    rules: list[str]      # IDs des règles
    graph: dict | None    # DAG de nodes/edges (Phase 3A)
    version: int
    locked_by: str | None # Verrouillage collaboratif
```

### Capability

Opération spatiale concrète (buffer, filter, clip, etc.). Chaque capability est auto-enregistrée et possède un schéma JSON de validation.

### Artifact

Résultat d'un job : fichier de sortie (GPKG, GeoJSON), table PostGIS, ou rapport de validation.

### Project (mode persistant)

Namespace pour regrouper datasets, règles et triggers dans un schéma PostGIS dédié.

```python
@dataclass
class Project:
    id: str
    name: str
    schema_name: str
    engine_backend: str   # "duckdb" | "postgis"
    dsn: str | None
    datasets: list[str]
    rules: list[str]
    triggers: list[str]
```

---

## Grammaire déclarative v2 — PipelineSpec

::: tip Nouveau en 1.0.1
La grammaire v2 unifie rules, triggers et graph dans un seul format `PipelineSpec`. Les pipelines v1 (flat rule lists) restent compatibles et sont auto-convertis.
:::

Le `PipelineSpec` est la structure centrale qui décrit un pipeline complet :

```python
@dataclass
class PipelineSpec:
    name: str
    steps: list[StepSpec]          # Étapes séquentielles ou DAG
    triggers: list[TriggerSpec]    # Triggers inline (on/when/then)
    version: int = 2

@dataclass
class StepSpec:
    id: str
    capability: str
    params: dict
    input: str | None = None       # Référence à un step précédent (DAG)
    when: Predicate | None = None  # Condition d'exécution
    enabled: bool = True

@dataclass
class TriggerSpec:
    on: str                        # Événement
    when: list[Predicate]          # Conditions
    then: list[ActionDef]          # Actions
```

**Exemple pipeline v2 avec DAG :**

```json
{
  "name": "analyse_risque_inondation",
  "version": 2,
  "steps": [
    { "id": "load", "capability": "filter", "params": { "expression": "type == 'habitation'" } },
    { "id": "buffer", "capability": "buffer", "input": "load", "params": { "distance": 50 } },
    { "id": "join", "capability": "spatial_join", "input": "buffer", "params": { "ref_layer": "zones_inondables" } },
    { "id": "stats", "capability": "spatial_aggregate", "input": "join",
      "when": { "type": "attr", "field": "niveau_risque", "op": "is_not_null" },
      "params": { "group_by": "code_commune", "aggregations": { "population": "sum" } } }
  ]
}
```

---

## Flux d'exécution

Quand vous lancez `gispulse run`, voici ce qui se passe :

```
1. LOAD        Charger le dataset source (multi-format via PyOGRIO)
                 ↓
2. PARSE       Convertir en PipelineSpec v2 (auto-upgrade si v1)
                 ↓
3. VALIDATE    Valider les steps (capabilities, paramètres, refs)
                 ↓
4. PLAN        Résoudre le DAG ou ordonner séquentiellement
                 ↓
5. EXECUTE     PipelineExecutor — mode linéaire ou DAG
                 ↓
6. EXPORT      Écrire le résultat dans le format de sortie
```

### 1. Load

Le `SessionManager` détecte le format source et charge les données :

- **GPKG / GeoJSON / SHP / FlatGeobuf / KML / DXF / GML** : charge via PyOGRIO (`read_vector()`)
- **GeoParquet** : lecture columnar native
- **CSV** : avec détection automatique des colonnes lat/lon ou WKT
- **PostGIS** : charge via requête SQL
- **WFS / OGC API Features** : lazy loading via `OGCLayerLoader`

Le CRS source est détecté automatiquement. Si absent, une erreur est levée.

Pour les gros fichiers, `read_vector_chunked()` permet une lecture par lots de 50 000 features.

### 2. Parse

Le pipeline est converti en `PipelineSpec` v2 :
- Si le JSON est un tableau de règles (v1), chaque règle est convertie en `StepSpec`
- Si c'est déjà un objet avec `version: 2`, il est parsé nativement

### 3. Validate

Le `RuleValidator` vérifie chaque step :

- La capability existe dans le registre (`REGISTRY`)
- Les paramètres respectent le schéma JSON de la capability (TypedDict pour 10 capabilities)
- Les `ref_layer` référencés sont fournis
- Les références `input` pointent vers des steps existants (DAG)
- Les types JSON correspondent (validation récursive)

```bash
# Validation standalone
gispulse validate rules.json
```

### 4. Plan

Le `PipelineExecutor` analyse le pipeline :

- **Mode linéaire** : si aucun step n'a de référence `input`, exécution séquentielle
- **Mode DAG** : tri topologique avec détection de cycles, délégation au `GraphExecutor`
- Les `ref_layer` sont chargés depuis les sources fournies (`--ref-source`)
- Le moteur est sélectionné : explicite (`--engine`), ou automatique selon le volume

### 5. Execute

Le `PipelineExecutor` exécute les steps :

```
# Mode linéaire
GeoDataFrame → step_0 → GeoDataFrame → step_1 → ... → GeoDataFrame final

# Mode DAG
step_0 ──► step_1 ──► step_3
                  ╲
step_2 ──────────► step_4
```

Les steps conditionnels (`when`) sont évalués sur le GeoDataFrame courant. Si la condition est fausse, le step est sauté.

Chaque capability reçoit un `GeoDataFrame` et retourne un `GeoDataFrame` modifié. Le `ExecutionContext` sélectionne la meilleure stratégie (Python, DuckDB ou PostGIS).

::: tip Performance
Le pattern Strategy sélectionne automatiquement le backend DuckDB pour les capabilities qui le supportent (buffer, filter) quand le volume dépasse 50 000 features.
:::

### 6. Export

Le résultat est écrit dans le format de sortie via `write_vector()` :

- 16+ formats vectoriels supportés en écriture
- Export PostGIS via SQLAlchemy
- GeoParquet natif

---

## Abstraction des moteurs — SpatialEngine

L'interface `SpatialEngine` définit le contrat que chaque moteur implémente :

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
    def backend_name(self) -> str: ...

    @property
    def is_persistent(self) -> bool: ...
```

Un `AsyncSpatialEngine` wrapper est disponible pour l'intégration FastAPI (via `asyncio.to_thread()`).

### Python Engine (GeoPandas)

- **Charge** : `gpd.read_file()` via PyOGRIO
- **Exécute** : méthodes GeoPandas/Shapely
- **Exporte** : `gdf.to_file()`
- **Avantage** : simplicité, disponible partout
- **Limite** : tout en RAM, lent > 50k features

### DuckDB Engine

- **Charge** : `DuckDBSession` avec extension spatial
- **Exécute** : SQL spatial DuckDB (`ST_Buffer`, `ST_Area`, etc.)
- **Exporte** : `to_gpkg()` avec sérialisation WKB
- **Avantage** : vectorisation SIMD, multi-thread, GeoParquet natif
- **Limite** : opérations spatiales moins complètes que PostGIS

### PostGIS Engine

- **Charge** : `PostGISConnection` via SQLAlchemy (pool_size=20)
- **Exécute** : SQL PostGIS complet (`ST_*`)
- **Exporte** : INSERT avec index spatial automatique
- **Avantage** : opérations SQL complètes, persistance, triggers, multi-user
- **Limite** : nécessite un serveur PostgreSQL 14+ avec PostGIS 3.x

### Hybrid Engine

Combine DuckDB pour les calculs locaux et PostGIS pour le stockage :

```
Données locales → DuckDB (calcul rapide) → PostGIS (stockage)
                                         ← PostGIS (ref_layers)
```

Géré par `HybridEngine` + `DuckDBPostGISBridge`.

---

## Pattern Strategy (multi-backend)

Chaque capability peut déclarer plusieurs stratégies d'exécution :

```python
class ExecutionStrategy(ABC):
    mode: StrategyMode      # PYTHON (10), DUCKDB (80), POSTGIS (100)
    def can_execute(self, ctx: ExecutionContext) -> bool: ...
    def execute(self, gdf, ctx) -> GeoDataFrame: ...
```

Le `select_strategy()` filtre les stratégies éligibles et retourne celle de plus haute priorité. Fallback Python toujours disponible.

Exemple pour `buffer` :
- `_BufferPythonStrategy` (priorité 10) — toujours éligible
- `_BufferDuckDBStrategy` (priorité 80) — si backend DuckDB et > 50k features
- `_BufferPostGISStrategy` (priorité 100) — si backend PostGIS

---

## Système de plugins

Les capabilities sont auto-enregistrées via un décorateur :

```python
from capabilities.registry import register

@register
class BufferCapability(Capability):
    name = "buffer"
    description = "Creates a fixed-distance buffer around each geometry."
    _strategies = [_BufferPythonStrategy(), _BufferDuckDBStrategy(), _BufferPostGISStrategy()]

    def execute(self, gdf, distance=0.0, crs_meters="EPSG:3857", **_):
        # logique GeoPandas
        ...

    def get_schema(self):
        return {"type": "object", "properties": {...}, "required": ["distance"]}
```

Au démarrage, GISPulse charge les modules built-in (`vector`, `raster`, `network`, `validation`, `postgis_sql`) de façon lazy avec thread-safe lock. Les plugins externes ajoutent des capabilities via entry points :

```toml
# pyproject.toml du plugin
[project.entry-points."gispulse.capabilities"]
mon_plugin = "mon_plugin.capabilities"
```

---

## ESB / Architecture événementielle

Le bus d'événements (ESB) connecte les triggers à l'exécution :

```
PostgreSQL pg_notify
      ↓
PgNotifyListener (asyncpg)
      ↓
EventRouter
      ↓
TriggerEvaluator → PredicateEvaluator (PostGIS SQL ou Shapely)
      ↓
ActionDispatcher → [notify, set_field, run_job, webhook, ...]
```

**Fiabilité** :
- `CircuitBreaker` : prévient les cascades (CLOSED → OPEN → HALF_OPEN)
- `DeadLetterQueue` : récupération des messages échoués avec replay
- `MAX_CASCADE_DEPTH = 3` : limite la profondeur des triggers en cascade
- `WorkerPool` : pool de workers configurable pour le traitement parallèle

---

## Graph Executor (DAG)

Le `GraphExecutor` permet des pipelines complexes avec des nœuds typés :

| Type de nœud | Description |
|--------------|-------------|
| `DATASET` | Source de données |
| `CAPABILITY` | Opération spatiale |
| `RULE` | Règle métier |
| `TRIGGER` | Déclencheur |
| `CALCULATE` | Expression calculée |
| `AGGREGATE` | Agrégation spatiale |
| `LOOP` | Boucle sur sous-ensemble |
| `BRANCH` | Condition if/else |
| `PARALLEL` | Exécution parallèle |
| `ARTIFACT` | Résultat de sortie |

Les nœuds sont connectés par des `EdgeDef` avec des ports nommés. Le graphe est trié topologiquement avec détection de cycles.

---

## Pipeline de règles

### Ordre d'exécution

Les règles sont exécutées par ordre croissant de `config.order`. En cas d'égalité, l'ordre dans le tableau JSON est respecté.

```json
[
  { "capability": "filter",    "config": { "order": 0, ... } },
  { "capability": "reproject", "config": { "order": 1, ... } },
  { "capability": "buffer",    "config": { "order": 2, ... } },
  { "capability": "area_length", "config": { "order": 3, ... } }
]
```

### Règles désactivées

Une règle avec `"enabled": false` est ignorée sans supprimer le fichier. Utile pour le debug.

### Scénarios

Le `ScenarioRunner` supporte deux modes :

- **Séquentiel** (`run`) : chaque job reçoit le résultat du précédent. Arrêt au premier échec.
- **Indépendant** (`run_independent`) : chaque job reçoit le GeoDataFrame original. Continue malgré les échecs.

Checkpointing GeoParquet pour les résultats intermédiaires.

---

## Modèle d'erreurs

GISPulse distingue trois niveaux d'erreurs :

### Erreurs de validation

Détectées avant l'exécution par `gispulse validate` :

- Capability inconnue (absente du `REGISTRY`)
- Paramètre invalide (type, valeur hors bornes, vérification JSON Schema)
- `ref_layer` manquant
- Prédicat de trigger invalide (structure récursive vérifiée)

::: warning
Toujours lancer `gispulse validate` avant `gispulse run` en CI/CD.
:::

### Erreurs d'exécution

Surviennent pendant le traitement :

- Dataset source introuvable ou illisible
- Géométries invalides (auto-corrigées si possible via `make_valid`)
- Dépassement mémoire (basculer sur DuckDB ou PostGIS)
- Timeout (configurable, défaut 300s)
- `CascadeDepthExceeded` si les triggers s'enchaînent au-delà de 3 niveaux

### Erreurs de sortie

Surviennent à l'export :

- Permissions fichier insuffisantes
- PostGIS déconnecté
- Format de sortie incompatible avec les données

Toutes les erreurs sont tracées dans le `Job` et remontées au format structuré :

```json
{
  "job_id": "abc-123",
  "status": "FAILED",
  "error": {
    "type": "ExecutionError",
    "rule": "buffer_100m",
    "message": "CRS mismatch: source EPSG:4326, expected metric CRS",
    "suggestion": "Ajoutez une règle reproject avant le buffer"
  }
}
```

---

## Diagramme de séquence — `gispulse run`

```
CLI          Orchestration     Rules        Capabilities    Engine
 │                │              │               │            │
 │── run ────────►│              │               │            │
 │                │── parse ────►│               │            │
 │                │◄── rules ────│               │            │
 │                │── validate ─►│               │            │
 │                │◄── ok ───────│               │            │
 │                │── plan ─────►│               │            │
 │                │◄── ordered ──│               │            │
 │                │                              │            │
 │                │── execute(rule_0) ──────────►│            │
 │                │                              │── load ───►│
 │                │                              │◄── gdf ────│
 │                │                              │── exec ───►│
 │                │                              │◄── gdf ────│
 │                │◄── result ──────────────────│            │
 │                │                              │            │
 │                │── execute(rule_1) ──────────►│            │
 │                │   ...                        │            │
 │                │── export ───────────────────────────────►│
 │◄── artifact ──│              │               │            │
```

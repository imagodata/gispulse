# Manifeste ELT `version: 3`

Le manifeste `version: 3` est la **surface déclarative unifiée** de GISPulse pour les pipelines ELT. Il fusionne les trois formats hérités (`triggers.yaml` v1, pipeline JSON v2, listes de règles) en un seul schéma — sources, staging, modèles, triggers, sécurité, runtime — et **compile vers le moteur PipelineSpec existant**. Aucun nouveau DAG executor, aucune nouvelle couche de dispatch : c'est une couche déclarative au-dessus du moteur qui tourne déjà depuis la v1.

> **Référence d'architecture** : [ADR 0005 — Unified GISPulse manifest](../../docs/adr/0005-unified-manifest.md) cadre la décision. Le dialecte SQL contractuel est figé par [ADR 0001 — DuckDB-spatial is the contract dialect](../../docs/adr/0001-dsl-sql-dialect.md). La cascade DELETE descendante réutilise le fixed-point d'[ADR 0002 — Trigger cascade semantics](../../docs/adr/0002-trigger-cascade-semantics.md).

## TL;DR

```yaml
version: 3

sources:
  cadastre:
    uri: ./parcelles.gpkg
    layer: parcelles
    crs: EPSG:2154
  plu:
    uri: s3://bucket/plu.parquet

staging:
  engine: duckdb
  attach: true
  cdc: incremental

models:
  zones_u:
    select: plu
    transform:
      - filter: { expression: "zone == 'U'" }
    materialize: view

  parcelles_constructibles:
    select: cadastre
    transform:
      - spatial_join: { with: zones_u, predicate: intersects }
      - area_length: { compute_area: true, area_col: surface_m2 }
    materialize: incremental
    refresh: on_change
    assert:
      - not_null: [parcel_id]
      - unique: [parcel_id]
      - geometry_valid: geometry
      - expect_rows: { min: 1 }

triggers:
  - name: notify_new
    table: parcelles_constructibles
    on: [INSERT]
    actions: [{ type: webhook, url: https://example.com/hook }]

security: { webhook_allowlist: [example.com] }
runtime:  { poll_interval_ms: 1000, max_batch: 200 }
```

## Sections du manifeste

### `sources:` — entrées déclarées

Chaque source porte au minimum une `uri:`. Les champs additionnels — `layer`, `geometry`, `crs`, `format` — sont logiques (jamais l'encodage physique : voir [Q3 — geometry-agnostic DSL](../../docs/adr/0005-unified-manifest.md#decision)).

```yaml
sources:
  cadastre:
    uri: ./parcelles.gpkg
    layer: parcelles
    geometry: geom        # nom logique de la colonne géométrie
    crs: EPSG:2154
  remote_plu:
    uri: https://wfs.example.com/geoserver/wfs?service=WFS&typeName=plu
  s3_buildings:
    uri: s3://bucket/buildings.parquet
```

Le routage par schéma d'URI est automatique : fichier local → `LayerSourceConfigModel`, distant → registre `SOURCES` + `LazyFetcher` (réutilise la fondation v1.9.0).

### `staging:` — façade engine + CDC

```yaml
staging:
  engine: duckdb          # ou postgis, gpkg, spatialite
  attach: true            # lazy ATTACH via LayerRegistry
  cdc: off                # off | snapshot | incremental
```

`engine:` est **global, pas par-modèle** — décision ADR 0005 (la complexité par-modèle n'est pas justifiée pour le tier ciblé). `attach:` câble `LayerRegistry.install()`. `cdc:` câble les modules `persistence/` (`duckdb_diff_engine`, `change_log_watcher`).

### `models:` — le DAG déclaratif

Chaque modèle décrit **comment dériver une couche** à partir des sources ou d'autres modèles. La forme imbriquée est la **seule syntaxe d'auteur** ; la compilation produit `PipelineSpec.steps` côté moteur.

```yaml
models:
  <nom>:
    select: <source-ou-modèle>     # référence amont obligatoire
    transform:                      # chaîne de capabilities
      - <capability_name>: { <params> }
      - <capability_name>: { with: <ref>, <params> }
    materialize: view               # view | table | incremental
    refresh: manual                 # manual | on_change | schedule
    assert: [...]                   # data-quality gates
```

#### `select:` et `with:` — résolution des références

- `select: <source>` → la source est chargée et passée comme entrée principale du modèle.
- `select: <autre_modèle>` → le modèle aval lit le résultat matérialisé de l'amont.
- `with: <ref>` à l'intérieur d'un transform → entrée secondaire (e.g. `spatial_join`/`attribute_join`). La référence est routée via le paramètre `ref_layer` du moteur.

Les références sont **validées au load-time** : une référence non résolue ou un cycle inter-modèle lève `ManifestValidationError` avant exécution (voir `gispulse explain` ci-dessous).

#### `materialize:` — modes de matérialisation

| Mode | Sémantique | Quand l'utiliser |
|---|---|---|
| `view` (défaut) | Résultat gardé en mémoire ; recalculé à chaque run | Modèle intermédiaire, sortie volatile |
| `table` | En mémoire **et** enregistré sur le moteur (`engine.register`) sous `elt_<nom>` | Modèle référencé par un push-down SQL aval |
| `incremental` | Re-transforme uniquement les lignes changées ; premier run = snapshot | Modèles "lourds" sur sources CDC |

**Note 2026-05-20** : `incremental` requiert `staging.cdc: incremental` + une clé d'incrément. La sémantique est figée par ADR 0005 ; le câblage (delta CDC + cascade DELETE + refresh schedule) arrive dans la continuation de [#249](https://github.com/imagodata/gispulse/issues/249).

#### `refresh:` — stratégie de rafraîchissement

- `manual` (défaut) : recompute à chaque appel de `gispulse run` ou `GISPulseApp.run_manifest()`.
- `on_change` : recompute uniquement si une source amont a changé (court-circuit basé sur le CDC).
- `schedule` : à venir — câblage cron via `runtime.scheduler`.

#### `assert:` — data-quality gates

```yaml
models:
  parcelles_constructibles:
    select: cadastre
    transform: [...]
    assert:
      - not_null: [parcel_id]
      - unique: [parcel_id]
      - geometry_valid: geometry
      - expect_rows: { min: 1, max: 100000 }
      - { not_null: [code_insee], severity: warning }
```

Quatre kinds disponibles : `not_null`, `unique`, `geometry_valid`, `expect_rows`. Chaque assertion porte un `severity` optionnel (défaut `error`) :

- `severity: error` → si la vérification échoue, **lève `AssertionFailedError` immédiatement** après la matérialisation du modèle fautif (le run s'arrête avant que les modèles avals ne consomment une sortie corrompue).
- `severity: warning` → la failure est collectée sur `ManifestRunResult.assertion_warnings` mais ne lève pas.

Les assertions tournent **en Python sur le résultat matérialisé** — engine-agnostiques par construction (la donnée à vérifier est la même qu'elle vienne d'un push-down SQL ou d'un fallback Python).

### `triggers:` — déclencheurs réactifs

Les triggers v3 conservent la sémantique de [`docs-site/guide/rules`](./rules.md) — déclenchés sur événement DML / schedule / manual, ils dispatchent une action (`notify`, `webhook`, `run_sql`, …). Ils ne sont **pas** des étapes de DAG : `models:` et `triggers:` sont [deux sections distinctes du même schéma](../../docs/adr/0005-unified-manifest.md#settled-design-questions) (décision D — un schéma, deux sémantiques).

### `security:` et `runtime:`

Identiques à v1/v2 — `webhook_allowlist`, `poll_interval_ms`, `max_batch`, etc. Inchangés.

## Frontière ETL / ELT — `select_strategy()`

Chaque nœud du DAG passe par `capabilities/strategy.select_strategy()`. La stratégie retenue dépend :

1. **Du moteur configuré** (`staging.engine`) — PostGIS prioritaire (priorité 100), DuckDB ensuite (80), Python en fallback (10).
2. **Des paramètres du step** — un gate par-stratégie peut décliner (e.g. `feature_count > 50_000` pour `buffer DuckDB`, ou un `BufferStyle` non-default qui n'est pas exprimable en SQL DuckDB).
3. **De la couverture du capability** — certains capabilities (`vector_diff`) n'ont **pas de stratégie SQL** par conception (diff row-by-row Hausdorff, ETL-strict).

**Tu n'as pas à deviner ce qui va se passer** : `gispulse explain` rend le choix inspectable avant l'exécution.

## `gispulse explain` — inspecter le DAG avant de courir

```bash
gispulse explain manifest.yaml
```

Sortie type :

```
Manifest: parcelles_constructibles_demo
Engine:   duckdb
Order:    zones_u → parcelles_constructibles

model: zones_u
  select:      plu
  materialize: view
  refresh:     manual
  • zones_u — filter → duckdb@80  [✓ ]
      available: postgis@100(gated), duckdb@80, python@10

model: parcelles_constructibles
  select:      cadastre
  materialize: incremental
  depends_on:  zones_u
  • parcelles_constructibles__t0 — spatial_join → duckdb@80  [✓ ]
      available: postgis@100(gated), duckdb@80, python@10
  • parcelles_constructibles — area_length → duckdb@80  [✓ ]
      available: postgis@100(gated), duckdb@80, python@10
```

- `picked@priority` : la stratégie qui s'engagera réellement (mode + priorité).
- `available:` : toutes les stratégies déclarées par le capability, avec `(gated)` pour celles que le contexte recale.
- **`⚠ ETL-strict`** : tag sur les capabilities qui n'ont pas de stratégie SQL — points de re-matérialisation Python inévitables.

Options : `--engine duckdb|postgis` pour scorer contre un autre moteur, `--format json` pour scripting.

C'est l'argument-de-vente face à FME / ArcGIS ModelBuilder : **la prévisibilité est inspectable, pas devinée**.

## Validation au load-time

Le loader (`load_manifest_v3()` ou `gispulse run`) valide :

1. **Schéma JSON** — `SCHEMA_V3` (versionné, à côté de `SCHEMA_V1`/`V2`).
2. **Cycles inter-modèles** — Kahn topologique sur le graphe `select:` / `with:` (algorithme partagé avec `GraphExecutor`).
3. **Références non résolues** — chaque `select:` / `with:` doit pointer vers une source ou un modèle déclaré.
4. **Modèles orphelins** — warning (souvent les sorties terminales du pipeline, légitimes).

```python
from gispulse.core.manifest_v3 import load_manifest_v3, ManifestValidationError

try:
    manifest = load_manifest_v3("manifest.yaml")
except ManifestValidationError as exc:
    print(f"Validation failed: {exc.errors}")
```

## Exécuter un manifeste

### Via la CLI

```bash
gispulse run manifest.yaml
```

### Via Python

```python
from gispulse.app import GISPulseApp
from gispulse.persistence.duckdb_engine import DuckDBSession

with DuckDBSession() as engine:
    result = GISPulseApp().run_manifest("manifest.yaml", engine=engine)
    for name, mat in result.materialized.items():
        print(f"{name}: {len(mat.result)} rows ({mat.mode.value})")
    if result.assertion_warnings:
        for w in result.assertion_warnings:
            print(f"⚠ {w.model}.{w.kind}: {w.message}")
```

## Voir aussi

- [Migration v1 / v2 → v3](./elt-migration.md) — `gispulse migrate` et calendrier de dépréciation.
- [Écrire des règles](./rules.md) — sémantique des triggers (réutilisée par v3).
- [Capabilities](./capabilities.md) — catalogue des opérations, avec stratégies SQL/Python par-cap.
- [Moteurs DuckDB / PostGIS](./engines.md) — choix d'engine.
- [Dialecte SQL du DSL](./dsl-sql-dialect.md) — ADR 0001, le contrat de portabilité.

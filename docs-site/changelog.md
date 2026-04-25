---
title: Changelog
description: Historique des versions GISPulse.
---

# Changelog


Toutes les modifications notables sont documentées ici. Format basé sur [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/). Versionnement [Semantic Versioning](https://semver.org/lang/fr/).

## [Unreleased]

### Changements

- **Doc capabilities** — passe de **78 à 117** capabilities documentées (FR + EN), 11 → 18 catégories. Ajout des sections : overlay & combinaison, manipulation d'attributs, pivot/unpivot, sélection ordonnée & échantillonnage, multipart & dimensions Z/M, transformations géométriques, frontière & projection, temporel, 3D pointcloud.
- **Playground manifest** — régénération avec les **6 scénarios** réellement déployés (S1 flood-risk, S2 data-quality, S3 accessibility, S4 road-setback, S5 green-spaces, S6 real-estate). L'ancien manifeste ne listait que `road-setback`.
- **Build playground idempotent** — `scripts/build_playground_data.py` : nouvelle fonction `_entry_from_disk` qui émet une entrée manifest depuis les fichiers existants quand le GPKG source est absent.

### Corrections

- **CHANGELOG v1.1.0** — corrigé : S5 décrit comme « Park accessibility » (et non « Canopy typology »), mention du « S7 / seven-scenario index » retirée car jamais livrée.
- **Pages orphelines** — `playground/environmental-ndvi.md` (FR + EN) supprimées : redirections JS dépréciées, plus aucune référence source.

### Suppressions

- **Scénario S7 dvf-heavy-tail** — spec retirée du build script ; le dossier de données orphelin a été supprimé. La page n'avait jamais été créée côté docs.

---

## [1.1.1] — 2026-04-25

### Ajouts

- **`capabilities/vector/`** — le monolithe `vector.py` (4 359 LOC, 43 capabilities) a été éclaté en un package de 32 sous-modules par domaine. La surface publique est préservée via shim de re-export ; tous les `from capabilities.vector import ...` continuent de fonctionner.

### Changements

- **`gispulse/__init__.py`** — `__version__` fallback passe de `"1.0.0"` codé en dur à `"unknown"` quand `importlib.metadata` n'est pas disponible.
- **`portal/package.json`** + **`docs-site/package.json`** — versions synchronisées sur `1.1.1` pour matcher `pyproject.toml`.

### Corrections

- **Accessibilité** — navigation clavier sur `PipelinePanel`, imports portail unifiés sur les tokens du design system.

---

## [1.1.0] — 2026-04-25

### Ajouts

- **Playground scenarios** — S5 Accessibilité aux parcs (Versailles, BD TOPO végétation ≥ 1 ha + `nearest_neighbor` + `classify`, cron hebdomadaire) et S6 Carte du prix au m² DVF (8 étapes, fishnet 50 m, palette YlOrRd quintiles).
- **Capabilities — classification & stats** — `head_tail_breaks` (Jiang 2013), `normalize` (log1p / minmax / zscore), `grid_create`, `hexgrid_create`, `spatial_aggregate`, `classify_categorical`, `bivariate_choropleth`, `graduated_size`, `continuous_ramp`, `kde_heatmap`. Clustering : `cluster_kmeans`, `cluster_dbscan`, `cluster_hdbscan`, `morans_i`, `getis_ord_g`, `nearest_neighbor`, `od_matrix`, `spatial_weights`.
- **Capabilities — 3D pointcloud** — sprint LAS / LAZ : `pointcloud_load_las`, `pointcloud_filter_classification`, `pointcloud_zonal_height`, `pointcloud_grid_summary`.
- **Capabilities — manipulation de couches P0-P3** — overlay (`overlay_intersection`, `overlay_union`, `erase`), sélection (`sort`, `deduplicate`, `random_sample`, `top_n`), shape ops, transformations (`affine_transform`, `swap_xy`, `reverse_lines`), Z/M (`add_z`, `drop_z`, `add_m`, `drop_m`), pivot/unpivot, `classify_by_ring`, `merge_layers`, attribute logic (`add_field`, `drop_field`, `select_columns`, `rename_field`, `cast_field`, `attribute_join`, `lookup_table`, `coalesce_fields`, `case_when`), temporal (`temporal_filter`, `temporal_join`).
- **Playground UX** — dessin rubber band avec snap-to-close + raccourcis clavier + mesure live sur la carte ; styling intersection polygone côté client (S4 road-setback).
- **DVF Etalab 2022-2024** — dataset bundlé dans `examples/prepare_playground_data.py --city versailles` (couche `dvf_ventes`).
- **Style sidecars** — fichiers `.style.qml` / `.style.sld` / `.legend.json` émis à côté des sorties vecteur pour import direct dans QGIS / GeoServer.
- **SQL preview** — gate d'authentification explicite + blocklist de capabilities sur la capability PostGIS SQL.

### Changements

- **`core/config.py`** — centralisation de toutes les variables d'environnement dans un module Pydantic Settings unique (13 groupes : `engine`, `database`, `storage`, `s3`, `api`, `oidc`, `session`, `redis`, `logging`, `audit`, `stripe`, `telemetry`, `jobs`). Rétro-compatible avec tous les noms `GISPULSE_*` existants.
- **Moteur par défaut** — passe de `duckdb` à `gpkg` (mode portable GPKG / GeoPandas).
- **Suppression des `os.environ.get()` éparpillés** — routers, adapters, persistence : tout passe par `settings`.
- **Playground S5** réécrit en accessibilité aux parcs par bâtiment — végétation BD TOPO ≥ 1 ha (SCoT IdF), `nearest_neighbor` distance bâti → parc, classification contre les seuils OMS / SCoT / ADEME (300 / 600 / 1000 m). L'ancien trigger NDVI / canopée a été retiré.
- **Playground S6** étendu au fishnet 250 m puis resserré à 50 m pour une heatmap haute résolution.
- **Playground S3** — pipeline 6 étapes ramené à 3 via `cost_budgets` + `classify_by_ring` (4 isochrones concentriques 500 / 750 / 1000 / 1500 m).
- **`adapters/http`** — fork namespace résolu : arborescence legacy supprimée, entrypoints prod basculés sur `gispulse.adapters.http.app`.
- **Sécurité** — `MD5` remplacé par `BLAKE2b`, `eval` sandboxé pour `np`, `_ensure_valid` restauré.

### Corrections

- **Capabilities — 4 P0 fermés** : `force_geometry_type` (cible GeometryCollection), `attribute_join` sur DataFrame nu, NaN crash dans `add_z` / `add_m` chemin `from_column`, `singleparts_to_multipart` (perte silencieuse sur types geom mixtes).
- **Capabilities** — pointcloud grid 2D NaN, KDE grid blow-up, sandbox RCE de `Calculate`.
- **Tests** — 27 tests ressuscités après déblocage du CI, `__init__.py` shadow supprimé, `asyncio_mode = "auto"` activé, SyntaxError `workflows/ftth_network_analysis.py` corrigée. 3 600+ tests au vert.
- **Tests** — isolation des mutations `GISPULSE_ENGINE` ; conftest auth-disabled-by-default.
- **Billing** — `StripeSettings` par défaut + messages d'erreur actionnables quand les clés Stripe manquent.
- **Capabilities** — `clip` / `intersects` : évite la vérification truth-value sur `GeoDataFrame` ; `spatial_predicate` fallback rendu explicite.
- **Playground** — S6 `drop_price_outliers` renommé `drop_value_outliers` (filtre sur `valeur_fonciere` brut, pas le prix au m²).
- **i18n** — strings `PipelinePanel` ; alignement du moteur par défaut ; pipelines `ref_layers` plural.
- **Performance** — `DualMapView` lazy-loadé.
- **Rules router** — validation du payload avant persistance (400 avec erreurs structurées).

---

## [1.0.2] — Sprint S1→S6 (2026-04-12)

Six sprints d'audit et hardening : securite, architecture, tests, observabilite, couverture routers, metriques Prometheus.

### Ajouts

#### Architecture — Grammaire déclarative v2 (Sprint S1)
- **`PipelineSpec` / `StepSpec` / `TriggerSpec`** — grammaire unifiée remplaçant 3 DSLs divergents (rules, triggers, graph)
- **Support DAG** — les steps peuvent référencer d'autres steps via `step.input`
- **Steps conditionnels** — évaluation de prédicats `step.when` sur le GeoDataFrame courant
- **Triggers inline** — syntaxe `on/when/then` dans le pipeline
- **Rétro-compatible** — les pipelines v1 (flat rule lists) sont auto-convertis en v2
- **`PipelineExecutor`** — exécuteur unifié (mode linéaire et mode DAG via `GraphExecutor`), remplace le choix entre `SessionManager`/`JobRunner`/`ScenarioRunner`
- **`PluginRegistry[T]`** — registre générique thread-safe avec découverte par entry points
- **`BoundedLayerCache`** — cache LRU extrait de `app.py` vers `core/cache.py`
- **`ProductionAuthMiddleware`** — extrait de `create_app()` vers `middleware/production_auth.py`

#### Pipeline v2 API (Sprint S2)
- **`POST /api/pipelines/execute`** — exécution de pipelines v2 avec `PipelineSpec` JSON
- **`POST /api/pipelines/validate`** — validation dry-run d'un pipeline
- **`GET /api/pipelines/examples`** — exemples de pipelines v2
- **CRUD `/api/triggers/{id}/operations`** — persistance des opérations spatiales dans les triggers
- **`SessionManager.run_pipeline_v2()`** — délègue nativement au `PipelineExecutor`
- **TypedDict pour 10 capabilities** — `FilterParams`, `BufferParams`, etc. dans `core/capability_params.py`
- **PipelineEditor** — mode éditeur dans le Portal : import/export JSON v2, exécution via `/pipelines/execute`

#### Portal — Décomposition et WebSocket (Sprint S3)
- **`LayerItemButton`** (275L) et **`DatasetItem`** (150L) extraits de `LeftPanel.tsx` (1183→774 lignes)
- **WebSocket listener** remplace le polling `setInterval` dans `transformStore`
- **CI GitHub Actions** — workflow `ci.yml` avec backend (pytest, ruff) et frontend (tsc, vite build)

#### Documentation et outillage (Sprint S4)
- **`scripts/export_openapi.py`** — génère `docs/openapi.json` + `docs/API_REFERENCE.md` automatiquement, commande `make docs`
- **QUICKSTART.md**, **RULES_GUIDE.md**, **TRIGGERS_GUIDE.md**, **API_QUICKSTART.md** — 4 guides utilisateur
- **`docs/openapi.json`** — spécification OpenAPI 3.1 complète (88 endpoints)

### Changements

#### Modèles (Sprint S1)
- **`core/models.py` scindé** (795→280L) en 6 modules : `enums.py`, `conditions.py`, `predicates.py`, `graph.py`, `relations.py`, `session.py`
- **`Rule.order`** extrait du bag `config` vers un champ dédié
- Réexports backward-compatible — zéro changement d'import dans le code existant

#### Portal (Sprint S3)
- **Renommage types de prédicats** — suppression du suffixe `*Node` (`AttrPredicateNode` → `AttrPredicate`, etc.)
- **Forge operations connectées** — `OperationExecutor` → ESB : actions `RUN_SQL` exécutées end-to-end

### Supprimés
- **Stubs clients non fonctionnels** — `clients/qgis/`, `clients/arcgis/`, `clients/desktop/` (code conservé dans l'historique Git)
- **ESB `CircuitBreaker` et `DeadLetterQueue`** marqués `EXPERIMENTAL`, lazy-import uniquement

### Securite (Sprint S1)
- Patch de 13 vulnerabilites critiques (7 injections SQL, 2 RCE, 1 auth bypass)
- 114 tests de securite couvrant tous les vecteurs d'audit
- **`hmac.compare_digest()`** pour toutes les comparaisons d'authentification (timing-safe)
- **Headers de securite Nginx** — CSP, X-Frame-Options DENY, X-Content-Type-Options nosniff, Referrer-Policy
- **Rate limiting** sur `/api/filter/preview` (30/min) et `/api/filter/apply` (20/min)
- **`pip-audit`** bloque maintenant le CI sur les CVEs connues (suppression du `|| true`)
- **Validation GISPULSE_MAX_UPLOAD_MB** — gestion des valeurs invalides, cap a 5GB

### Architecture (Sprint S2)
- **Migration structlog** — remplacement de `print()` et `logging` par structlog dans ESB workers et pg_notify
- **Logging des exceptions silencieuses** — 6 handlers `except: pass` remplacés par `log.debug()`/`log.warning()`
- **Fix race condition jobs** — vérification d'annulation AVANT persistance des résultats
- **Timeout dataset loading** — 300s max pour éviter les blocages sur gros fichiers
- **Fix collision triggers** — utilisation de l'UUID du trigger comme suffixe (supporte plusieurs triggers par table)
- **Limite WebSocket** — 1MB max par message sortant

### Observabilite (Sprint S4 + S6)
- **`MetricsMiddleware`** — métriques HTTP automatiques : `gispulse_http_requests_total` (par method/path/status), `gispulse_http_request_duration_seconds`, `gispulse_http_requests_in_flight`
- **Normalisation de chemin** — remplacement des UUIDs et segments numériques pour réduire la cardinalité Prometheus
- **Trace ID correlation** — `_log.error("unhandled_exception", trace_id=...)` dans le error handler
- **Migration jobs_router** — stdlib `logging` remplacé par structlog avec keyword args
- **Docker non-root** — `USER appuser` (uid 1000) dans le Dockerfile
- **`.dockerignore`** — exclut .git, node_modules, tests, docs, .env, IDE files
- **`.pre-commit-config.yaml`** — ruff lint+format, trailing whitespace, YAML check, détection de clés privées

### Tests (Sprints S3 + S5)
- **2 439 tests** passent (contre 2 205 en v1.0.1), +234 tests ajoutés sur 6 sprints
- **106 fichiers de tests** (unitaires + intégration + sécurité)
- **Couverture routers : 85%** (23/27 routers testés, contre 33% avant)
- Nouveaux fichiers : `test_rules_router`, `test_triggers_router`, `test_jobs_router`, `test_datasets_router`, `test_cli`, `test_persistence_io`, `test_auth_router`, `test_admin_router`, `test_scenarios_router`, `test_schedules_router`, `test_catalog_router`, `test_relations_router`, `test_filter_router`, `test_portal_datasets_router`, `test_esb_router`, `test_tiles_router`
- **CI : mypy** (type checking core modules) + **ESLint/Vitest** (frontend lint + tests)
- 90 fichiers de tests (unitaires + intégration + sécurité)

---

## [1.0.0] — 2026-04-06

Release initiale publique. 27 capabilities, 1 836 tests, moteur multi-backend DuckDB/PostGIS.

---

## [0.1.0] — 2026-03-31

### Ajouts

#### Moteur central
- Moteur geospatial DuckDB avec modes portable SpatiaLite et persistant PostGIS
- `SessionManager` avec pipeline E2E, pattern `ExecutionStrategy`, support session SpatiaLite
- `JobRunner` avec exécution asynchrone et suivi de statut des jobs
- Opérations cross-layer : spatial join, système de layer de référence, support multi-layer
- Pagination, association datasets, CRUD projets
- Migration PyOGRIO pour I/O multi-format
- Robustification edge cases : zones shadow, centroïde, capabilities surface/longueur
- Support GeoParquet et serveur OGC avec serveur de tuiles MVT

#### CLI
- Entry point CLI Typer (`gispulse`)
- Commandes : `init`, `validate`, `info`, `layers`, `formats`, `capabilities`, `serve`, `portal`, `doctor`
- Acceptance multi-format via la couche I/O intégrée

#### Capabilities vectorielles (10)
- `buffer` — buffer métrique avec reprojection automatique
- `union` — fusion de toutes les features
- `reproject` — reprojection CRS
- `filter` — filtre attributaire
- `clip` — découpe par layer de référence
- `intersects` — filtre par intersection spatiale
- `spatial_join` — jointure spatiale
- `centroid` — extraction des centroïdes
- `area_length` — calcul surface et longueur
- `dissolve` — dissolution par attribut
- Registre de capabilities avec auto-découverte
- Injection de capabilities lifespan-managed

#### Règles
- Système rules-as-config avec définitions JSON
- Rule editor UI avec predicate builder
- Évaluation de règles basée sur triggers avec `auto_eval` et SSE eval-stream

#### Persistence
- Mode PostGIS persistant avec live sync et intégration pg_notify
- Mode SpatiaLite portable (session niveau 2, serverless)
- Export GPKG depuis le catalogue
- Scene manager avec snapshot et restore

#### API REST (FastAPI)
- API REST complète : projets, datasets, features, sessions, règles, triggers, scénarios
- 14 routeurs, 100+ endpoints
- Mise à jour de features, exécution SQL, endpoints relations
- Endpoints d'ingestion OGC Features
- Streaming SSE pour les résultats d'évaluation de triggers
- Configuration hot-reload Docker pour API et Portal dev servers
- Error handlers globaux `{"error": {"code", "message", "detail"}}` pour 400/404/422/500

#### Portal (React 19)
- Layout 5 workspaces : Explorer, Map, Workflows, Catalog, Data
- Layer tree avec groupes, color picker, légende et symbologie
- Layout de panneaux redimensionnables avec ActivityBar et Inspector
- Node editor (XyFlow/ReactFlow v12) avec 9 types de nœuds, NodePalette, inspector inline
- Trigger stepper, barre de scénarios, UI opérations spatiales
- Console SQL et inspecteur de features
- Workspace Catalog avec cartes, favoris, mini-map, filtrage domaine
- Dark mode avec tokens design OKLCH, police Geist, notifications toast
- Palette de commandes (Ctrl+K), raccourcis clavier (1–5, Ctrl+I/B/K/S/?)
- Upload drag-and-drop et import URL, export GPKG avec styles QML

#### Viewer
- Viewer spatial deck.gl embarqué servi via `gispulse serve`

#### ESB / Triggers
- Bus d'événements avec pg_notify, routage, circuit breaker, dead letter queue
- Trigger Builder UI avec composition de prédicats
- `SessionProvisioner` avec `TriggerEvaluator` et SSE eval-stream

#### Catalogue
- Catalogue de données GIS : projections, fonds de carte, flux WMS/WFS, sources open data

#### Tests
- 46 fichiers de tests : unitaires et intégration
- Tests d'intégration E2E SpatiaLite
- Configuration pytest avec support async

---

## Liens

- [Dépôt GitHub](https://github.com/gispulse/gispulse)
- [Signaler un bug](https://github.com/gispulse/gispulse/issues)
- [Roadmap](https://github.com/gispulse/gispulse/projects)

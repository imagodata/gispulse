---
title: Changelog
description: Historique des versions GISPulse.
---

# Changelog

Toutes les modifications notables sont documentées ici. Format basé sur [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/). Versionnement [Semantic Versioning](https://semver.org/lang/fr/).

La source de vérité de ce fichier est [`CHANGELOG.md`](https://github.com/imagodata/gispulse/blob/main/CHANGELOG.md) dans le dépôt — les entrées ci-dessous sont alignées à chaque release.

## [Unreleased]

---

## [2.0.0] — 2026-05-20

Première release **majeure**. Numériquement c'est un saut depuis `1.6.2`, mais en pratique la surface d'API regroupe ce qui était tagué en interne `1.7.0`, `1.8.0` et `1.9.0` — des fonctionnalités accumulées sur `main` sans jamais être publiées sur PyPI. On promeut toute la stack en un seul tag et on aligne la version publique sur l'histoire du produit.

Le chemin de mise à jour est dans [`Migration 2.0`](./migration-2.0). En résumé : **aucun changement de code applicatif n'est strictement nécessaire** — le shim meta-path `_compat.py` absorbe le déplacement des imports et l'alias `PluginHub = ExtensionHub` préserve les anciens imports jusqu'en 2.1.0.

Trois chantiers convergent ici :

1. **Foundations** (interne `v1.8.0`) — mono-package `gispulse.*`, `ExtensionHub` remplace `PluginHub`, façade `GISPulseApp`, serveur MCP complet, régime data-pack, routers CLI / HTTP / templates.
2. **Agrégateur mondial** (interne `v1.9.0`) — réseau de fetchers DuckDB paresseux couvrant 4 familles de protocoles (`GeoParquetS3`, `OGCFeatures`, `STAC`, `HttpFile`) et un `worldwide_catalog.yml` curaté.
3. **Rails data-pack** — les premiers data-packs *tiers* peuvent désormais ship sur PyPI : canal de découverte via l'entry-point `gispulse.data_packs`, contrôle de signature Ed25519 sur les manifests EXTERNAL, et un format de payload de licence unifié partagé avec la future licence SaaS tenant.

### Ajouts

- **Régime data-pack — canal de découverte PyPI (T5).** Troisième canal aux côtés des manifests OSS bundlés et de `GISPULSE_DATA_PACKS_DIR` : le groupe d'entry-point Python `gispulse.data_packs` permet à un package tiers d'enregistrer ses manifests à l'installation. Un pack défectueux n'empêche jamais les autres. (#269)
- **Régime data-pack — contrôle de signature Ed25519 (G1a).** `DataPackManifest` gagne un champ `signature` optionnel. Les manifests EXTERNAL portant une signature sont vérifiés contre `GISPULSE_DATA_PACK_PUBLIC_KEY` ; les manifests altérés sont rejetés avec des événements de log explicites. Les manifests INTERNAL (bundled) sont exemptés. Régler `GISPULSE_DATA_PACK_REQUIRE_SIGNATURE=true` pour refuser les packs EXTERNAL non signés. (#271)
- **Format de payload de licence Ed25519 unifié (L0).** Nouveau `gispulse.core.licence_format` définit le schéma de payload unique partagé par la clé de licence par-machine, la future licence SaaS tenant et la signature de manifest data-pack. Versionné via `schema_version`, forward-compat, JSON canonicalisé. (#266)
- **Client OGC haut-niveau pour data-packs (T1).** Nouveau `gispulse.core.fetchers.ogc_client.fetch_features(...)` — un one-liner au-dessus du transport consolidé, dispatch WFS vs OGC API Features, surface d'erreur réseau typée (`OGCEndpointUnreachable`, `OGCClientError`). (#267)
- **Normalisateur ZoningElement déclaratif (T2).** Nouveau `gispulse.core.zoning_normalizer` mappe des enregistrements hétérogènes vers un schéma commun à 8 champs inspiré d'INSPIRE PlannedLandUse. CRS obligatoire et explicite (`EPSG:XXXX`). (#268)
- **Type de contenu data-pack `regulatory-zoning` (T3).** Nouvelle valeur dans `DATA_PACK_CONTENTS`. Nouveau dataclass `RegulatoryZoningEntry` + validateur `from_dict()` : champs requis, aucun champ inconnu, code pays ISO-3166-1 alpha-2, protocole connu, CRS `EPSG:` explicite, bbox à 4 nombres. (#270)
- **Agrégateur mondial (EPIC #226).** 15 sous-issues livrant des fetchers DuckDB paresseux couvrant `GeoParquetS3`, `OGCFeatures`, `STAC`, `HttpFile`, plus un `worldwide_catalog.yml` curaté (France / UE / monde), endpoints HTTP (A10), onglet Worldwide du portail (A12). (#227-#241)
- **Serveur MCP v1.8.0 (EPIC #206).** 7 outils, mode dry-run, scoping FS. Launcher stdio via `gispulse mcp`. (#202-#205, PR #242)
- **Consolidation mono-package `gispulse.*` (Foundations A).** Arborescence flat à 8 packages → package unique `src/gispulse/` ; ~280 fichiers OSS déplacés avec shim meta-path `_compat.py` préservant chaque ancien racine d'import.
- **Façade `GISPulseApp` + 4 façades thin (Foundations B).** Couche application au-dessus des routers CLI / HTTP / MCP / templates.
- **Hub `ExtensionHub` à deux régimes (Foundations C).** Remplace `PluginHub`, sépare plugins code et data-packs ; `DataPackManifest` + `templates/manifest.yml` pour le régime data-pack.
- **Stack push-down ELT (EPIC #243).** Génération SQL dialect-aware (#244, Lot 1), push-down de schéma (#245, Lot 2), push-down par capability (Lots 3b-3e : geom multi-couche, dissolve/sjoin, nearest/overlay, temporel), manifest unifié v3 (Lot 4A), validation des cycles (Lot 4B), matérialisation (Lot 4C), inspection DAG `gispulse explain` (Lot 4E), portes data-quality `assert:` (Lot 4F), docs manifest v3 + cross-refs ADR (Lot 4G). 12 PRs mergées sur `main` le 2026-05-20. (#262, #264, #296-#305)

### Changements

- **`PluginHub` renommé `ExtensionHub`.** Même module (`gispulse.core.plugin_hub`) ; un alias `PluginHub = ExtensionHub` préserve les anciens imports. Suppression prévue en **2.1.0**.
- **Surface publique `gispulse.core.plugin_contracts` gelée via `__all__`.** Les 8 symboles réellement exportés par le wheel 1.6.2 sont gelés ; les types déplacés vers `plugin_model.py` n'étaient jamais dans `plugin_contracts` — pas de shim compat nécessaire.
- **Horizon de dépréciation `_compat.py` corrigé.** La docstring et le `DeprecationWarning` pointent désormais sur **2.1.0** au lieu de l'ancienne ligne « supprimé en 1.9.0 ».

### Corrections

- **Job `security-audit`** — silence deux advisories upstream contestés (`joblib` PYSEC-2024-277, `pyjwt` PYSEC-2025-183) via allowlist `--ignore-vuln` avec note de réévaluation. Aucun changement de code.

### Migration

Voir [`Migration 2.0`](./migration-2.0). En résumé :

- Les imports legacy racines (`core.*`, `capabilities.*`, `rules.*`, `orchestration.*`, `persistence.*`, `catalog.*`) continuent de fonctionner via le shim meta-path `_compat.py` avec un `DeprecationWarning` one-shot par racine.
- `PluginHub` continue de fonctionner via l'alias `PluginHub = ExtensionHub`.
- Les deux shims seront retirés en **2.1.0** — migrez vers `gispulse.*` / `ExtensionHub` quand vous voulez.

---

## [1.7.0] — interne

> **Note :** `1.7.0` n'a jamais été publié sur PyPI comme tag autonome — son scope est intégré dans [`2.0.0`](#200--2026-05-20). L'entrée ci-dessous documente ce que le tag *aurait* contenu pour les personnes qui suivent l'EPIC #175.

La release « Câbler la plateforme ETL ». L'EPIC #175 (PR #189) avait livré le modèle de plugin unifié en *squelette* ; v1.7.0 le rend bout-en-bout — une source de données peut être déclarée, fetchée sur le réseau via un registre de protocoles, et surveillée pour fraîcheur afin qu'une révision externe déclenche un trigger. GISPulse gagne une étape « Extract » aux côtés de ses triggers CDC locaux existants.

### Ajouts

- **Modèle de plugin unifié + `PluginHub`.** Cinq genres de plugin (`source`, `capability`, `sink`, `protocol`, `extension`), découverte par entry-point, et un cycle de vie `discover → resolve → gate → activate` avec gating tier/trust. (EPIC #175, PR #189)
- **Triggers `source_changed`.** Un trigger peut déclarer `on: {source_changed: <source>://<entry>, frequency: …}` et se déclencher quand une source externe publie une nouvelle révision. (#195)
- **`SourceWatcherRegistry` câblé dans `gispulse watch`.** Sonde le token `revision()` de chaque source surveillée à la cadence `frequency` et dispatche les événements `source.changed`. (#197)
- **Fetchers transport dans le `ProtocolRegistry`.** `WfsFetcher` + `OgcFeaturesFetcher` (#192, PR #209), `StacFetcher` + `RestGeoJsonFetcher` (#192, PR #211).
- **Plugins sources `gispulse-src-cadastre` et `gispulse-src-ign`.** Premiers pilotes `gispulse-src-*` — cadastre français (IGN Parcellaire Express) et données de référence IGN (BD TOPO + ADMIN EXPRESS). (#184, #194)
- **`gispulse mcp`.** Launcher CLI démarrant le serveur MCP GISPulse en stdio pour les agents LLM. (#201)
- **Scanner de dérive de dialecte PostGIS.** Avertissement au chargement quand un `run_sql` utilise des constructions PostGIS-only qui ne tourneront pas sur le dialecte DuckDB-spatial. (#146)
- **Documentation ETL.** Guide de rédaction de plugin source, walkthrough « surveiller une source externe » (FR + EN), section `source_changed` dans `TRIGGERS_GUIDE.md`. (#200)

### Changements

- **La découverte du catalogue consomme `PluginHub.records`.** `catalog/registry.py` ne lance plus son propre scan — le hub possède le scan unique. `/catalog/*` est fonctionnellement inchangé. (#193)
- **`gispulse-src-cadastre.revision()` est une vraie sonde.** Fraîcheur lue depuis `HTTP HEAD` `ETag` / `Last-Modified` contre le WFS Géoplateforme `GetCapabilities`. (#198)

### Corrections

- **Garde SSRF sur `ProtocolRegistry.dispatch_fetch()`.** Chaque endpoint de fetch est validé via le garde partagé `core.ssrf`. (#199)
- **Flake fichier-lock `test_p02`.** La race connue sqlite3 / pyogrio est marquée `flaky` et rejouée via `pytest-rerunfailures`. (#191)

---

## [1.6.2] — 2026-05-07

La release « Format Frontier » — DuckDB Spatial comme substrat CDC universel. Ajoute deux moteurs (`spatialite`, `duckdb_diff`), apporte la détection DML à sept formats fichier (GPKG, SpatiaLite, GeoJSON, FlatGeobuf, Shapefile, KML, CSV+WKT) — dont cinq n'avaient aucune surface de trigger native — et ferme l'EPIC #139 (ADRs sémantique DML + sécurité connexion WAL).

### Ajouts

- **Moteur SpatiaLite.** Nouveau `persistence.spatialite_engine.SpatiaLiteEngine` partage le DDL trigger SQLite de GPKG mais écrit via le driver pyogrio `SQLite + SPATIALITE=YES`. Auto-routé pour les URIs `*.sqlite` / `*.db`. (PR #151)
- **Helper de détection `is_spatialite_file(path)` + `bootstrap_spatialite_project(conn)`.** Sœur du bootstrap GPKG ; helper partagé `_bootstrap_gispulse_internals(conn)`. (PR #151)
- **`FileBlobChangeDetector`.** CDC réutilisable basé sur mtime + diff de snapshot `ST_Read` DuckDB. Hash `md5(ST_AsWKB(geom) || json_object(props))` excluant `OGC_FID`. Snapshot persisté comme `<blob>.gispulse-snapshot.duckdb`. Sémantique set-diff : INSERT / DELETE uniquement — UPDATE indétectable sans PK stable. (PR #152)
- **Surveillance des fichiers compagnons.** Shapefile + MapInfo TAB surveillés via `max(mtime)` sur tous les compagnons ; map `_COMPANION_EXTENSIONS` extensible. (PR #152)
- **`DuckDBDiffEngine`.** Implémentation `SpatialEngine` basée sur le détecteur file-blob. GeoJSON, FlatGeobuf, Shapefile, KML, CSV+WKT. Reprend la forme de `GeoPackageEngine.get_pending_changes` pour que `ChangeLogWatcher` itère uniformément. (PR #152, #153)
- **Entrées factory moteur.** `_spatialite_factory` et `_duckdb_diff_factory` enregistrés comme built-ins ; l'inférence URI mappe les suffixes automatiquement. (PRs #151, #152)
- **`persistence.gpkg_connection.connect_gpkg(path, …)`.** Point d'entrée unique appliquant WAL + `busy_timeout=5000` sur chaque `sqlite3.connect` GeoPackage. 8 call-sites migrés. (#141, PR #145)
- **ADRs 0001-0004.** DuckDB-spatial comme dialecte SQL contractuel (#140 / PR #147), cascade de triggers bounded fixed-point (#142 / PR #148), `_gispulse_change_log` comme poll log (#143 / PR #150), hooks DDL hors scope (#144 / PR #150).
- **CDC KML, CDC CSV+WKT, fichiers compagnons MapInfo TAB + fallback pyogrio.** (EPIC #106 slices 1+2, PR #153, #154)
- **`POST /datasets/{id}/enable_tracking` multi-moteurs.** La route n'est plus codée en dur sur `GeoPackageEngine` ; résout le moteur via suffixe URI. Famille SQLite installe les triggers AFTER ; `duckdb_diff` saute l'installation (snapshot sidecar au premier poll). (#157, PR #158)

### Changements

- **`bootstrap_gpkg_project` extrait un helper interne partagé** — test de régression épingle que le chemin GPKG produit toujours un GeoPackage valide avec `application_id = 0x47504B47`. (PR #151)

### Documentation

- **`docs/adr/0001 → 0004`** introduits sous `docs/adr/` ; cross-linkés depuis `architecture.md`.
- **`dsl-sql-dialect.md`** — référence utilisateur du contrat de dialecte SQL DSL.
- **Sous-section « comportement de cascade » dans `rules.md`** avec table de tiers, explication à deux couches, lien vers l'ADR 0002. (PR #148)
- **`formats.md`** — lignes SpatiaLite, GeoJSON, FlatGeobuf, Shapefile, KML, CSV+WKT, MapInfo TAB avec notes CDC ; nouvelle section « CDC file-blob ». (PRs #151-#154)
- **`walkthroughs/geojson-cdc.md` (FR + EN)** — quatrième walkthrough bout-en-bout. (PRs #155, #156)

---

## [1.6.1] — 2026-05-07

Suite same-day de v1.6.0. Ferme les 3 items différés du kickoff sprint v1.6.0 en un seul PR (#138) pour que la ligne v1.6.x livre toute sa surface promise — push-down cross-source, lookup scalaire, et auto-wire validate zéro-config.

### Ajouts

- **Fct DSL `layer_lookup(layer, match, take, layer_geom)`.** Lookup d'attribut scalaire contre une couche cross-source avec trois modes (`spatial_within`, `spatial_intersects`, raccourci égalité-attribut). Compile vers `(SELECT _L."<take>" FROM "<layer>" AS _L WHERE <pred> LIMIT 1)`. (#124)
- **Registre de couches cross-source.** `gispulse.runtime.layer_registry.LayerRegistry` ATTACHe des sources GeoPackage / Parquet / PostgreSQL externes en lecture seule et crée une vue DuckDB par couche déclarée. (#122)
- **Bloc top-level `layers:` dans `triggers.yaml`.** Refs cross-source déclaratives via `LayerSourceConfigModel`. Garde anti-doublon de nom au chargement. (#122)
- **Auto-wire validate de `build_runtime`.** Nouveaux kwargs `validate_rules`, `default_table`, `layer_sources`, `source_epsg` câblent un `ValidationRunner` directement sur le watcher du change-log.
- **`table:` par règle et `default_table:` top-level.** Ordre de résolution : `rule.table` > `default_table` > autodétection GPKG mono-table > `ValidationTableResolutionError`.

### Changements

- **`compile_validate_rules` accepte un callable `table_resolver`** — supporte la résolution par règle. Le paramètre legacy `table=` est préservé pour les appelants v1.6.0.

---

## [1.6.0] — 2026-05-07

La release « DuckDB Spatial Inside ». Ferme l'EPIC #104 — une cascade d'un jour de 7 PRs (#129 → #135) qui pose la fondation, la whitelist de fonctions geom DSL, les verbes DML granulaires, le bloc `validate:` déclaratif bout-en-bout, et le gap historique B-08 des prédicats DELETE.

DuckDB spatial passe d'« embarqué si vous le voulez » à **substrat de compute universel** : les nouvelles fcts geom DSL compilent en SQL DuckDB, le runner de validation évalue les règles via un ATTACH DuckDB sur le GeoPackage, et le bench Atlas R1 contre pyogrio justifie le pivot — DuckDB COPY est **2.3× à 3.6× plus rapide que pyogrio** sur 1M polygones EPSG:2154, RSS pic divisé par ~3.4×.

### Ajouts

- **Extension DuckDB spatial — install paresseuse à la première utilisation.** `gispulse.runtime.duckdb_engine.get_spatial_connection()` lance `INSTALL spatial; LOAD spatial;` au premier appel. `DuckDBSpatialUnavailable` remonte les échecs air-gapped explicitement. (#113, PR #129)
- **`gispulse doctor --install-spatial`.** Préinstalle l'extension spatial et sonde un set curaté de roundtrips EPSG (`EPSG:4326 / 3857 / 2154 / 27572`) contre une baseline `pyproj`. (#114, PR #129)
- **Inférence de moteur depuis l'URI du dataset.** `triggers.yaml` n'exige plus de `engine:` explicite : `*.gpkg` → `gpkg`, `postgresql://...` → `postgis`, `*.shp / *.geojson / *.fgb` → `duckdb_diff`. (#115, PR #129)
- **Fonctions geom DSL — première whitelist.** Sept fonctions push-down sûres : `geom_area_m2`, `geom_perimeter_m`, `geom_length_m`, `geom_centroid_x`, `geom_centroid_y`, `geom_npoints`, `geom_is_valid`. Auto-projection en `EPSG:2154` par défaut. (#116, #117)
- **Parser d'expressions DSL — safe-by-construction.** AST walked sous allowlist stricte (littéraux, refs colonnes, `+ - * / %`, parens). Le mode boolean ouvre `== != <= >= and or not` pour les règles `validate:`. (#118)
- **Verbes DML granulaires `when:`.** `INSERT`, `UPDATE_GEOM`, `UPDATE_ATTR`, `DELETE`, `BULK`. Le watcher résout un `UPDATE` grossier en variant granulaire via le flag `geom_changed` du change-log. (#119)
- **Flag `geom_changed` dans la charge `dml.changed`.** Les souscripteurs peuvent rendre les éditions de géométrie différemment des éditions d'attributs. (#120)
- **Bloc top-level `validate:` dans `triggers.yaml`.** Règles de validation déclaratives avec `mode: warn` ou `mode: tag`. Les règles compilent au chargement. (#121)
- **Action `tag_field:`.** Écrit un statut (et message optionnel) sur la ligne, auto-création des colonnes cibles via `PRAGMA table_info` + `ALTER TABLE ADD COLUMN`. Handler partagé entre actions YAML explicites et bridge `validate: mode: tag`. (#123)
- **Fcts DSL sous-requête cross-layer.** `geom_within(layer='communes', match='code_insee')` et `geom_overlaps_any(layer='self', exclude_self=True)`. Le compilateur émet `EXISTS (SELECT 1 FROM "<layer>" AS _L WHERE …)` avec validation stricte d'identifiant. (#122)
- **`ValidationRunner` + `make_gpkg_sql_evaluator(gpkg_path)`.** Composant runtime engine-agnostic qui compile chaque règle une fois au boot, évalue par ligne via un `sql_evaluator` injecté. Broadcaste `validation.failed` sur l'event hub. Isolation par règle : une seule règle défaillante n'avorte jamais le batch. (PRs #132-#133)
- **Hook validation `ChangeLogWatcher`.** Quand un `ValidationRunner` est injecté, chaque INSERT / UPDATE_GEOM / UPDATE_ATTR conduit `runner.evaluate(...)`. (PR #133)
- **Alias vocabulaire ESRI Attribute Rules.** `kind: constraint | calculation | validation` acceptés comme alias cosmétiques sur `triggers.yaml`. (#125)
- **Nouvelles pages docs.** `dsl-geom-functions.md`, `dsl-validation.md`, `migration-from-esri.md`, section v1.6.0 sur `engines.md`. (#126)

### Corrections

- **B-08 — Les prédicats DELETE peuvent enfin filtrer sur l'état pré-delete de la ligne.** Le trigger AFTER DELETE écrit `OLD.*` comme `json_object(NEW.*)` dans `old_values` depuis v1, mais la whitelist tail du lecteur changelog jetait la colonne. La whitelist inclut maintenant `old_values` ; le watcher hydrate `ChangeRecord.old_values` quand au moins un trigger actif porte un AST de prédicat. Aucune migration GPKG. (#120, PR #135)

### Sécurité

- **La charge broadcast `dml.changed` reste minimale sur DELETE.** Les attributs de ligne capturés par AFTER DELETE sont exposés uniquement à l'évaluateur de prédicats interne, jamais sur `/ws/events`. Le test `test_dml_changed_does_not_leak_old_values` épingle le contrat.
- **Le SQL des règles `validate:` n'est jamais splicé brut.** Validateur strict `[A-Za-z_][A-Za-z0-9_]{0,62}` sur chaque identifiant ; littéraux SQL-quotés ; parser AST qui refuse tout nœud hors allowlist.

### Performance

- **DuckDB COPY GDAL/GPKG est maintenant le fast path bulk write-back.** Bench Atlas R1 sur 1M polygones EPSG:2154 (médiane sur 3 runs) :

  | Scénario | pyogrio (s) | DuckDB COPY (s) | Speedup | RSS pyogrio | RSS DuckDB |
  |---|---:|---:|---:|---:|---:|
  | Append +100k | 8.19 | **3.63** | 2.26× | 950 MB | **273 MB** |
  | Update attribut | 6.94 | **2.75** | 2.52× | 839 MB | **255 MB** |
  | Update géométrie | 8.87 | **2.47** | 3.59× | 843 MB | **275 MB** |

  Fallback pyogrio reste forcé pour datasets > 5M lignes, GPKG avec triggers / vues custom, et sémantique append-in-place.

---

## [1.5.3] — 2026-05-05

Hotfix pour l'EPIC #103 — 4 bugs P0 identifiés par Beta sur les triggers DML v1.5.2 + workflow QGIS.

### Corrections

- **B-05 — Les noms de couches QGIS avec espaces, accents ou tirets sont acceptés.** Le validateur délègue maintenant à `core.sql_safety.validate_layer_name()` qui accepte tout caractère sûr dans un identifiant quoté ; seuls `"`, `'`, `;`, `\` et les caractères de contrôle sont refusés. Les noms d'objets trigger passent par `slug_identifier()`. (#107)
- **B-02 — Le trigger SET_FIELD ne boucle plus à l'infini.** Origin-tagging M1 : les couches trackées gagnent une colonne sentinelle `_gispulse_origin TEXT` (migration schéma v3, idempotent au re-bootstrap). Le trigger AFTER UPDATE gagne une clause WHEN supprimant les re-tirs quand la ligne porte un marqueur `trigger:<id>`. (#108)
- **B-01 — Bulk threshold Mode 3 (event WS bulk + éval trigger par ligne).** Nouveau paramètre constructeur `bulk_eval: Literal["skip", "per_row"] = "skip"`. `"per_row"` émet un seul `bulk.changed` summary ET évalue les triggers par ligne. (#109)
- **B-13 — Watchdog de dérive de schéma rebuild les triggers sur changements de colonnes.** Check de dérive throttlé à wall-clock (défaut 5 s) re-hash `PRAGMA table_info` ; sur diff, drop + ré-installe le change tracking et broadcaste `schema.changed`. Premier sighting silencieux. (#110)
- **CI — `_drop_rtree_triggers` et `_connect_with_retry` durcis.** Budget helper retry passé de 8×0.15 s à 20×0.25 s.

### Notes

- Bump schéma v2 → v3. Les GPKG v2 existants se mettent à niveau in-place au prochain appel `bootstrap_gpkg_project` (boot moteur), idempotent.
- `bulk_eval="per_row"` est opt-in sur le constructeur du watcher.
- Le watchdog de dérive de schéma tourne par défaut à 5 s ; régler `schema_drift_check_interval_s=0` pour désactiver.

---

## [1.5.2] — 2026-05-04

Release big-launch. Le runtime garde la surface v1.5 ; ajoute le plugin QGIS, trois walkthroughs bout-en-bout, bouche un trou critique de middleware en mode portail, et livre `/system/doctor`.

### Ajouts

- **Plugin QGIS (`qgis_plugin/`).** Dock widget léger qui shell-out la CLI `gispulse` système via `QProcess`. Version-gate (≥1.5.0), dialogue d'installation OS-specific, combo attach-trigger (couches vecteur uniquement), runner non-bloquant avec logs colorés streamés + Cancel, résumé post-run + reload auto + Restore 5 min. ~500 Ko unzippé, 99 tests, version lockstep avec le wheel. (#71, #73, #74, #76, #78, #80, #84)
- **Walkthroughs (FR + EN).** `classify_buildings_in_isochrones`, `recompute_isochrones`, `log_event`. (#89)
- **`POST /system/doctor`.** Endpoint backend santé miroir de `gispulse track doctor`. Closes #91. (#97)
- **CI — job `build-plugin-zip`** packageant et vérifiant le ZIP plugin à chaque tag. `release.yml` double-gated. (#79)

### Corrections

- **Sécurité — `ProductionAuthMiddleware` n'était jamais monté en mode portail.** L'install middleware via `PluginHub` était imbriquée dans la branche `is_portal=False` de `create_app`, donc le middleware d'auth enterprise (livré via l'entry-point `gispulse.middleware`) n'était jamais installé quand `gispulse portal` tournait. Les déploiements portail `GISPULSE_ENV=production` étaient NON-PROTÉGÉS sur `/filter/*`, `/ogc/*`, `/ws/*`. Boucle d'install `hub.middleware` hissée au-dessus de la branche `is_portal`. Closes part 2 of #87. (#96)
- **CI — flake `test_p02_enable_tracking_full_lifecycle` sur Python 3.10/3.12.** `sqlite3.connect()` enveloppé d'un retry à 3 tentatives. (#86, #57)
- **Docs — URL `git clone` morte dans le guide d'install du plugin QGIS.** Pointait vers `github.com/gispulse/gispulse` (404) ; le dépôt réel est `github.com/imagodata/gispulse`. Corrigé FR + EN. (#101)

### Changements

- `release.yml` — `github-release` attend à la fois `publish-pypi` et `build-plugin-zip`.

### Sécurité

- Bump dépendances : `docker/build-push-action` 6 → 7, `actions/upload-pages-artifact` 4 → 5, `actions/upload-artifact` 4 → 7. (#98-#100)

---

## [1.5.1] — 2026-04-30

Mode 2 portail Community : GISPulse ship maintenant un workbench visuel local. `pip install gispulse-portal` ajoute le SPA bundlé à votre install CLI ; `gispulse portal` ouvre `http://localhost:8001/portal` avec moteur same-origin.

### Ajouts

- **Commande CLI `gispulse portal`** montant le SPA `gispulse-portal` bundlé sur `/portal` via `StaticFiles` FastAPI. Flags `--port`, `--no-browser`, `--backend=URL`, `--dev`.
- **Mini-backend `/api/examples/*`** — registre read-only de fixtures GPKG bundlées (`muret-parcels`, `muret-flood-zones`, `toulouse-isochrones`, `bordeaux-rpg`) pour la démo publique « Try it ». Hard-cappé (timeout 5 s, 1000 enregistrements DML, 50 triggers, cache tuiles 50 Mo) ; `DryRunDispatcher` capture les actions mais n'exécute jamais d'effets de bord.
- **Docs — guides « Lancer le portail en local » + « Lancer le moteur »** (FR + EN).
- **Matrice de symétrie CLI ↔ Portail** (`guide/symmetry.md`) — 82 capabilities mappées ligne-par-ligne, 31 ⚠️ asymétries listées pour triage v1.6+.

### Release compagnon

- **`gispulse-portal 1.5.1` ship sur PyPI** pour la première fois. Le wheel bundle le SPA VitePress buildé pour que `gispulse portal` puisse le servir same-origin sur localhost.

### Corrections

- L'aide `engine -e/--engine` de `cli.py` mentionne maintenant `hybrid` à côté de `duckdb` et `postgis`.

---

## [1.5.0] — 2026-04-30

Release de styling QML-grade : charger, classer côté serveur, éditer et exporter des styles compatibles QGIS bout-en-bout.

### Ajouts

- **`POST /datasets/{id}/layers/{layer}/breaks`** — classification côté serveur (quantile, equal-interval, Jenks, std-dev, pretty) enveloppant `ClassifyCapability`.
- **`PUT /datasets/{id}/styles`** — persiste `LayerStyleDef` dans la table `layer_styles` du GPKG.
- **`POST /datasets/{id}/styles/import`** — upload multipart `.qml`, parsé via `persistence/style_converter.py` et persisté.
- **Suite intégration QML roundtrip** — 5 fixtures représentatives (single, categorized, graduated, rule-based, labels) testées en CI pour garder contre les cycles export/import lossy.

### Changements

- La classification de style passe côté serveur par défaut ; le client retombe en local pour les scénarios offline.
- `persistence/style_converter.py` (~608 LOC) devient la source de vérité QML ↔ `LayerStyleDef`. Bridge GeoStyler abandonné.

---

## [1.3.1] — 2026-04-29

Hotfix qui débloque la distribution v1.3.0 : `pipx install gispulse` ship maintenant un `triggers run` / `watch` fonctionnel, la stack Docker locale boote en tier community, le portail sert favicon/robots/manifest correctement, CI à nouveau vert.

### Corrections

- **Packaging — dépendance runtime core `httpx`** — déplacée des extras `[api]` / `[sso]` / `[dev]` vers base. `pipx install gispulse` produisait jusque-là une CLI fonctionnelle pour `track` / `info` / `run` mais `gispulse triggers run` et `gispulse watch` crashaient sur `ModuleNotFoundError: No module named 'httpx'`. Contournement pour 1.3.0 : `pipx install "gispulse[api]"`.
- **Packaging — dépendance runtime core `pyarrow`** — `pyarrow>=14,<22` déclarée en base. Sans elle, `gispulse run --output result.parquet`, le writer GeoParquet et tout pipeline DuckDB qui pose du GeoParquet via `COPY ... TO ... (FORMAT 'parquet')` crashaient sur `ImportError: Missing optional dependency 'pyarrow.parquet'`.
- **Runtime — `gispulse watch --bulk-threshold` crashait au démarrage** — `cli_watch.py` câblait `--bulk-threshold` directement dans `build_runtime(bulk_threshold=...)`, mais `build_runtime()` n'a jamais accepté le kwarg.
- **API — `/pipelines/execute-steps` 500 sur `ref_layer`** — la route résolvait les alias mais laissait les clés originales dans `params`. Corrigé via `dict.pop()` pour stripper les clés de plumbing avant l'appel capability.
- **API — stubs auth OSS + websockets** — `/api/auth/providers` et `/api/auth/me` shippent maintenant des stubs OSS retournant `[]` / `200 null`. Bascule de l'extra `[api]` vers `uvicorn[standard]` pour que les upgrades `/ws/events` cessent d'échouer sur `No supported WebSocket library detected`.
- **API — assets statiques racine SPA** — le fallback tente maintenant le dist root avant d'appliquer la whitelist de routes SPA + fallback index.html.
- **Compose — boot tier community** — `docker-compose.local.yml` ne hardcode plus `GISPULSE_ENGINE=postgis` ; PostGIS opt-in via `--profile postgis`.
- **Catalogue — entrées IGN Scan 25 mortes** — IGN Géoplateforme a déprécié `GEOGRAPHICALGRIDSYSTEMS.MAPS`. Drop de `basemap:ign-scan25` et `ign-scan25-wmts` ; `GEOGRAPHICALGRIDSYSTEMS.PLANIGNV2` exposée comme `basemap:ign-plan` / `ign-plan-wmts`.

### Changements

- **CI** — le job `test` installe `[dev,api,postgis,mcp,raster,network,classification,pointcloud,scheduling,sso]` au lieu de `[dev]` seul.
- **CI** — `pip-audit` ignore `CVE-2026-3219` (pip 26.x tar/ZIP confusion, pas de fix upstream encore ; réévaluation trimestrielle).
- **Docs** — README pipx quickstart aligné avec la surface CLI v1.3.

### Sécurité

- **Dépendances** — bump `fastmcp` `>=0.1,<2.0` → `>=2.14.2,<4.0` (CVE-2025-62800 / 62801 / 69196 / 64340 / 2026-27124 / GHSA-rcfx-77hg-w2wv).
- **Dev** — bump `pytest` `>=7.0,<9.0` → `>=9.0.3,<10.0` (CVE-2025-71176).

---

## [1.3.0] — 2026-04-27

La release CLI « no plugin required » — `gispulse track` + `gispulse watch` font de tout writer QGIS / ogr2ogr / FME / ArcGIS / DBeaver une source de trigger first-class.

### Ajouts

- **`gispulse track`** — sous-commande SQL de tracking de changements (`install` / `uninstall` / `list` / `tail` / `doctor [--auto-fix]`). Installe les triggers `_gispulse_change_log` sur un GPKG pour que n'importe quel client puisse écrire et que le daemon récupère les changements. (#4, #6)
- **`gispulse watch`** — daemon foreground top-level. Shutdown propre SIGINT/SIGTERM (drain 2 s), heartbeat structuré stderr toutes les 60 s, override repeatable `--webhook host` allowlist. Supporte mode daemon et drain `--once`. (#5, #11)
- **Charge trigger v2** — les triggers SQLite `_gispulse_change_log` baquent les colonnes JSON `new_values` / `old_values` + un flag `geom_changed`, capturés atomiquement dans le trigger SQLite via `json_object(NEW.*)`. Supprime le SELECT `_load_row_values()` post-commit. (#7)
- **Tick mode bulk** — `--bulk-threshold N` collapse les ticks avec `N+` lignes en un seul événement summary `bulk.changed` au lieu de broadcaster par ligne. (#8)
- **Packaging** — `packaging/systemd/gispulse-watch@.service` + `packaging/docker/Dockerfile.watch` + `docker-compose.watch.yml`. (#9)

### Notes

- Ferme le scope Mode 1 de #2 entièrement. Mode 2 (CRUD trigger portail) reste sur la roadmap.
- `gispulse triggers run --watch` et le nouveau top-level `gispulse watch` coexistent pour une release.
- Nettoyage baseline CI (#19) — flag `pip-audit --fix-auto=off` retiré, matrice de capabilities régénérée, dérive ruff nettoyée (514 → 0 erreurs), workflows alignés sur le split sibling-repo `gispulse-portal`.

---

## [1.2.1] — interne

> **Note :** `1.2.1` n'a jamais été publié sur PyPI comme tag autonome — son scope a été roulé dans [`1.3.0`](#130--2026-04-27). L'entrée ci-dessous documente ce que le tag *aurait* contenu.

### Ajouts

- **`gispulse triggers`** — nouveau groupe de sous-commandes CLI (`run` / `validate` / `list`) pour le runtime trigger autonome (Mode 1). YAML config → triggers DML GPKG, sans process FastAPI.
- **`gispulse/runtime/headless_runtime.py`** — `HeadlessRuntime` câble `ChangeLogWatcher` + `TriggerEvaluator` + `ActionDispatcher` contre un `NullEventHub` pour que le pipeline ESB tourne en dehors du lifespan FastAPI.
- **`gispulse/runtime/config_loader.py`** — schéma pydantic v2 strict (`extra="forbid"`, `yaml.safe_load` uniquement, garde anti-path-traversal).
- **`gispulse/runtime/predicate_dsl.py`** — parser LL(1) récursif descendant écrit à la main pour le champ `predicate:`. **Pas d'`eval`, pas de `simpleeval`, pas de dép tierce.** Opérateurs : `== != > >= < <= AND OR NOT IN NOT IN IS NULL IS NOT NULL`. `MAX_DEPTH=32`.
- **`gispulse/runtime/sqlite_retry.py`** — `RetryingSqlExecutor` enveloppe `GeoPackageEngine.execute()` avec backoff exponentiel sur `SQLITE_BUSY`. Cap à 5 retries / 30 s total.
- **`persistence/sql_guardrails.py`** — `enforce()` est la sandbox unique entre les actions YAML `run_sql` / `set_field` et SQLite. Allowlist `INSERT` / `UPDATE` / `DELETE` / `SELECT` uniquement. Hard-block `ATTACH` / `DETACH` / `PRAGMA` / `VACUUM` / `LOAD_EXTENSION` / `writable_schema` / `sqlite_master`. Charges multi-statement refusées.

---

## [1.2.0] — 2026-04-25

**Première release publique AGPL-3.0 sur PyPI comme `gispulse`.** Source : https://github.com/imagodata/gispulse.

### Ajouts

- **PluginHub + plugin contracts** — `core/plugin_hub.py` + `core/plugin_contracts.py` pour la découverte de plugins via entry-points Python, six groupes (`gispulse.routers`, `gispulse.middleware`, `gispulse.auth_provider`, `gispulse.billing_provider`, `gispulse.licence_provider`, `gispulse.connectors`).
- **Catalogue tarifaire** — `core/pricing_catalog.json` pour le catalogue tier→features (community / pro / team / enterprise) avec chaîne `inherits`.
- **Tier `team`** dans `persistence.tier.VALID_TIERS` et `core.config.EngineSettings`, entre `pro` et `enterprise`.
- **Gate multi-projets** sur `POST /projects` (community=1, pro=5, team+=∞).
- **Gate tier Pro** sur `triggers_router` (router-level) et `pipelines_router` (`/execute`, `/execute-steps`).

### Changements

- **Layout du dépôt** — les modules propriétaires (billing Stripe, SSO OIDC, admin RBAC, middleware production auth, sync licence Stripe) déplacés vers un package compagnon privé `gispulse-enterprise` distribué sous EULA commerciale. Le moteur OSS ne ship que des composants AGPL et découvre enterprise via entry-points au runtime.
- `gispulse/adapters/http/app.py` — mounting des routers billing, auth, admin maintenant piloté par la découverte `PluginHub` au lieu d'imports codés en dur ; dégrade proprement quand aucun plugin enterprise n'est installé.

### Suppressions

- `gispulse/adapters/billing/`, `gispulse/adapters/http/oidc.py`, `middleware/production_auth.py`, `routers/{auth,billing,admin}_router.py` — déplacés vers `gispulse-enterprise`.
- `pricing.yml` (montants EUR, conditions early-adopter) — déplacé vers `gispulse-enterprise/config/pricing_commercial.yml`. Le mapping technique tier→features reste ici comme `core/pricing_catalog.json`.
- Fichiers de test spécifiques aux modules enterprise.

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

- [Dépôt GitHub](https://github.com/imagodata/gispulse)
- [Signaler un bug](https://github.com/imagodata/gispulse/issues)
- [Roadmap](https://github.com/imagodata/gispulse/projects)

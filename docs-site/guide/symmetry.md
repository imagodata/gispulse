---
title: Symétrie CLI ↔ Portail
description: Matrice d'invariance — chaque capability publique de GISPulse a un point d'entrée CLI ET un point d'entrée portail (ou une dette UX explicitement loggée).
---

# Symétrie CLI ↔ Portail

GISPulse expose deux UIs équivalentes sur la même source de vérité (`triggers.yaml` + change-log SQLite/PostGIS) : un **CLI** terminal-first pour les power users et un **portail web** visual-first pour l'onboarding. **Cette page est le test d'invariance** : toute feature publique doit apparaître dans les deux colonnes — sinon la dette UX est loggée explicitement.

> Doctrine produit confirmée 2026-04-30. Aucun plugin GIS-client requis : QGIS save, ogr2ogr, ArcGIS Pro export, raw `sqlite3`, CLI ou portail web — tout DML fire les triggers via le change-log. Voir [Architecture](./architecture).

**Légende des statuts**

| Statut | Signification |
|---|---|
| ✅ | Symétrique : feature présente côté CLI **et** côté portail |
| ⚠️ | Asymétrique : présent d'un seul côté, dette UX loggée (voir issue) |
| ❌ | Reporté : pas implémenté ni côté CLI ni côté portail (voir milestone) |
| 🔧 | Surface "ops" volontairement CLI-only (pas de UI prévue) |

---

## 1. Rules — CRUD pipelines

Source de vérité : règles JSON / YAML chargées via `rules.loader`. API : [`rules_router.py`](https://github.com/imagodata/gispulse/blob/main/gispulse/adapters/http/routers/rules_router.py).

| Capability | CLI | Portail | Statut |
|---|---|---|---|
| Créer une règle | `gispulse template use <preset>` (scaffold) puis édition manuelle JSON | `RuleEditorModal` (drag-and-drop registry → schema-driven form) — `components/rules/RuleEditorModal.tsx`, `NodeEditor.tsx` | ✅ |
| Lister les règles d'un pipeline | `gispulse capabilities` (registry) + lecture du JSON | `NodeEditor` workspace — affiche le DAG du pipeline, registry-driven palette | ✅ |
| Éditer une règle | Édition manuelle du JSON + `gispulse validate` | `NodePropertyPanel` (form schema-driven) + validation live — `components/nodes/NodePropertyPanel.tsx` | ✅ |
| Supprimer une règle | Suppression manuelle JSON | Suppression node depuis `NodeEditor` (`Delete` key / context menu) | ✅ |
| Valider un pipeline | `gispulse validate <rules.json>` | Auto-validate au save dans `NodeEditor` (POST `/rules/{id}/validate`) | ✅ |
| Convertir règle ↔ node | _N/A_ (le CLI manipule du JSON brut) | GET `/rules/{id}/to-node` + POST `/rules/from-node` exposés à `NodeEditor` | ⚠️ |
| Exécuter un pipeline | `gispulse run <input> --rules <pipeline.json> -o <output>` | `WorkflowsView` → "Run" button (POST `/pipelines/execute`) | ✅ |
| Exporter le pipeline en YAML triggers | _N/A_ — le CLI consomme directement YAML | _N/A_ — le portail écrit du YAML pour le runtime | 🔧 |

**Asymétries loggées :**
- ⚠️ **rule ↔ node converter** : exposé seulement via API REST, pas de commande CLI dédiée. → suggérer issue `feat(cli): gispulse rules to-node / from-node` (v1.6+).

---

## 2. Triggers — Configuration et runtime

Source de vérité : YAML triggers + `_gispulse_change_log` table. API : [`triggers_router.py`](https://github.com/imagodata/gispulse/blob/main/gispulse/adapters/http/routers/triggers_router.py). Code CLI : [`cli_triggers.py`](https://github.com/imagodata/gispulse/blob/main/gispulse/cli_triggers.py), [`cli_watch.py`](https://github.com/imagodata/gispulse/blob/main/gispulse/cli_watch.py).

| Capability | CLI | Portail | Statut |
|---|---|---|---|
| Créer un trigger | Édition manuelle YAML | `TriggerBuilderInline` / `TriggerBuilderModal` — `PredicateBuilder` + `ActionEditor` + `CronBuilder` (POST `/triggers`) | ✅ |
| Lister les triggers | `gispulse triggers list --gpkg <path>` (triggers SQLite installés) | GET `/triggers` — `ScenariosPanel` / `TriggerHistoryPanel` | ✅ |
| Éditer un trigger | Édition manuelle YAML | `TriggerBuilderModal` (PUT `/triggers/{id}`) | ✅ |
| Supprimer un trigger | Édition manuelle YAML + `gispulse triggers validate` | DELETE `/triggers/{id}` depuis `ScenariosPanel` | ✅ |
| Activer / désactiver | _N/A_ (commenter dans YAML) | POST `/triggers/{id}/toggle` (UI switch dans `TriggerBuilderInline`) | ⚠️ |
| Valider un YAML triggers | `gispulse triggers validate --config <yaml> --gpkg <path>` | Validation live au save dans `TriggerBuilderModal` (réutilise `validate_against_gpkg`) | ✅ |
| Tick unique (run-once) | `gispulse triggers run --config <yaml> --once` | POST `/triggers/{id}/evaluate` — bouton "Test" dans `TriggerBuilderInline` | ✅ |
| Daemon long-running | `gispulse triggers run --config <yaml> --watch` ou `gispulse watch <gpkg> -r <rules>` | _N/A_ — le portail config un trigger, le **runtime** local (CLI ou daemon) l'exécute | 🔧 |
| Stream événements live | `gispulse triggers run --watch` (logs JSON stderr) | GET `/triggers/eval-stream` (SSE) consommé par `TriggerHistoryPanel` + `ActivityTimeline` | ✅ |
| Dryrun (preview actions) | _N/A_ — le mode `--once` exécute pour de vrai | POST `/examples/{id}/triggers/dryrun` — préview actions sans persister (Mode 2 Try-it) | ⚠️ |
| Inspecter operations d'un trigger | _N/A_ | GET `/triggers/{id}/operations` — historique d'exécution dans `TriggerHistoryPanel` | ⚠️ |

**Asymétries loggées :**
- ⚠️ **toggle CLI** : pas de `gispulse triggers enable/disable <id>`. → suggérer issue `feat(cli): gispulse triggers toggle <id> --enabled/--disabled` (v1.6+).
- ⚠️ **dryrun CLI** : pas d'équivalent CLI à `POST /examples/{id}/triggers/dryrun`. → suggérer issue `feat(cli): gispulse triggers run --dry-run` (v1.6+, déférable).
- ⚠️ **operations history CLI** : pas de `gispulse triggers history <id>`. → suggérer issue `feat(cli): gispulse triggers history <id>` (v1.6+).

---

## 3. Tracking — Change-log SQLite

Source de vérité : `_gispulse_change_log` table dans le GPKG. Code CLI : [`cli_track.py`](https://github.com/imagodata/gispulse/blob/main/gispulse/cli_track.py). API : `datasets_router.py` (`enable_tracking` / `disable_tracking` / `tracking_status`).

| Capability | CLI | Portail | Statut |
|---|---|---|---|
| Installer le tracking sur une layer | `gispulse track install <gpkg> --layer <name>` | POST `/datasets/{id}/enable_tracking` — bouton "Enable tracking" dans `DatasetCard` | ✅ |
| Installer sur toutes les layers | `gispulse track install <gpkg> --all-layers` | POST `/datasets/{id}/enable_tracking` (pas de toggle "all") | ⚠️ |
| Désinstaller le tracking | `gispulse track uninstall <gpkg> --layer <name>` | POST `/datasets/{id}/disable_tracking` — bouton dans `DatasetContextMenu` | ✅ |
| Lister layers tracked | `gispulse track list <gpkg>` (triggers + pending counts) | GET `/datasets/{id}/tracking_status` — affiché dans `DatasetCard` | ✅ |
| Tail des changements pending | `gispulse track tail <gpkg> --limit 50` | _N/A_ — `ActivityTimeline` consomme les events post-dispatch, pas le raw change-log | ⚠️ |
| Diagnostic + auto-fix | `gispulse track doctor <gpkg> [--auto-fix]` | _N/A_ | ⚠️ |
| Diagnostic global env | `gispulse doctor` | _N/A_ — surface "ops" CLI-only volontaire | 🔧 |

**Asymétries loggées :**
- ⚠️ **all-layers UI** : le bouton enable_tracking traite une seule layer à la fois. → suggérer issue `feat(portal): bulk enable tracking from DatasetCard` (v1.6+).
- ⚠️ **tail change-log** : utile en debug, pas de panel UI. → suggérer issue `feat(portal): raw change-log inspector panel` (v1.6+, déférable).
- ⚠️ **track doctor UI** : healthcheck triggers + auto-fix à exposer dans `DatasetCard`. → suggérer issue `feat(portal): tracking health badge + repair action` (v1.6+).

---

## 4. Datasets — Upload, listing, suppression

API : [`datasets_router.py`](https://github.com/imagodata/gispulse/blob/main/gispulse/adapters/http/routers/datasets_router.py), [`portal_upload_router.py`](https://github.com/imagodata/gispulse/blob/main/gispulse/adapters/http/routers/portal_upload_router.py).

| Capability | CLI | Portail | Statut |
|---|---|---|---|
| Uploader un dataset (local file) | `gispulse run` consomme directement un fichier local | `CatalogImportDialog` + `DragDropOverlay` (POST `/datasets/upload`) | ⚠️ |
| Uploader depuis URL | _N/A_ | POST `/datasets/import-url` — input dans `CatalogImportDialog` | ⚠️ |
| Importer depuis OGC API Features | _N/A_ | POST `/datasets/ogc` — `CatalogPanel` connector | ⚠️ |
| Lister datasets | `gispulse layers <file>` (single-file) ; `gispulse info <file>` | GET `/datasets` → `DatasetsView` + `DatasetCard` grid | ⚠️ |
| Inspecter métadonnées (CRS, layers, styles) | `gispulse info <file>` | GET `/datasets/{id}` → `InspectorPanel` + `DatasetSchemaGraph` | ✅ |
| Supprimer un dataset | _N/A_ — `rm <file>` à la main | DELETE `/datasets/{id}` — `DatasetContextMenu` | ⚠️ |
| Renommer un dataset | _N/A_ | PATCH `/datasets/{id}` → `RenameDialog` | ⚠️ |
| Exporter en GPKG | `gispulse run -o <output.gpkg>` (output d'un pipeline) | POST `/datasets/export-gpkg` — bouton "Export" dans `DatasetCard` | ✅ |
| Exporter (autres formats) | `gispulse run -o <output.{geojson,shp,parquet,fgb,...}>` | POST `/datasets/export` (16+ formats — voir [Formats I/O](./formats)) | ✅ |

**Asymétries loggées :**
- ⚠️ **dataset registry CLI-side** : les datasets sont implicites côté CLI (un fichier sur disque) vs explicites côté portail (registry persistant). → ce design gap est volontaire pour Mode 1, mais on pourrait exposer `gispulse datasets list/add/rm` qui pointe sur un registre local optionnel. À débattre v1.6+ — issue `feat(cli): optional dataset registry`.
- ⚠️ **import-url / OGC CLI** : pas de `gispulse import url <URL>` ni `gispulse import ogc <endpoint>`. → suggérer issue `feat(cli): gispulse import` (v1.6+).

---

## 5. Examples — Mode 2 portail "Try it"

API : [`examples_router.py`](https://github.com/imagodata/gispulse/blob/main/gispulse/adapters/http/routers/examples_router.py). Sprint v1.5.1, registry de datasets fixes read-only.

| Capability | CLI | Portail | Statut |
|---|---|---|---|
| Lister les exemples disponibles | _N/A_ — surface volontairement portail-only (Mode 2 Community demo) | GET `/examples` → `MarketplacePage` + landing | 🔧 |
| Détails d'un exemple | _N/A_ | GET `/examples/{id}` → preview card | 🔧 |
| Preview tile / MVT | _N/A_ — le viewer consomme directement le GPKG local | GET `/examples/{id}/preview` + `/examples/{id}/tiles/{z}/{x}/{y}.mvt` → `MapView` | 🔧 |
| Dryrun triggers sur exemple | `gispulse triggers run --once --config <yaml> --gpkg <example.gpkg>` (en local, après `pipx install gispulse`) | POST `/examples/{id}/triggers/dryrun` — `TriggerBuilderModal` "Test on this example" | ✅ |

**Surface "Try it" :** par construction le portail propose les exemples comme **on-ramp** vers `pipx install gispulse`. Les CLI users qui clonent le repo ont accès aux mêmes datasets via `examples/`. Pas de dette UX ici — c'est le funnel.

---

## 6. Styles — QML / SLD roundtrip

API : `portal_datasets_router.py` (styles import / export / breaks). Sprint v1.5.0.

| Capability | CLI | Portail | Statut |
|---|---|---|---|
| Importer un style QML | _N/A_ — QML déjà copié par `gispulse run --all-layers` | POST `/datasets/{id}/styles/import` — `LayerColorPicker` / `SchemaView` action | ⚠️ |
| Exporter un style QML | `gispulse run` copie automatiquement les styles depuis le GPKG d'entrée | GET `/datasets/{id}/styles` → bouton "Download QML" | ✅ |
| Mettre à jour le style | _N/A_ | PUT `/datasets/{id}/styles` — `LayerColorPicker` + `MapLegend` editing | ⚠️ |
| Calculer des breaks (Jenks / quantile / equal interval) | _N/A_ | POST `/datasets/{id}/layers/{layer}/breaks` — `LayerColorPicker` classification picker | ⚠️ |
| Lister les valeurs distinctes d'un champ | _N/A_ | GET `/datasets/{id}/layers/{layer}/distinct/{field}` | ⚠️ |
| Stats descriptives (min/max/mean/quantiles) | _N/A_ | GET `/datasets/{id}/layers/{layer}/stats/{field}` — `InspectorPanel` | ⚠️ |

**Asymétries loggées :**
- ⚠️ **styles CLI** : import / classify breaks / stats sont des opérations cartographiques **par essence visuelles**. CLI symétrique faible utilité. → loggable comme issue *non-prioritaire* `feat(cli): gispulse style classify --field <f> --method jenks --bins 5` pour CI / batch. v1.7+.

---

## 7. Run — Exécution pipelines

Source de vérité : `core.pipeline` + `orchestration.session_manager`. API : [`pipelines_router.py`](https://github.com/imagodata/gispulse/blob/main/gispulse/adapters/http/routers/pipelines_router.py), [`jobs_router.py`](https://github.com/imagodata/gispulse/blob/main/gispulse/adapters/http/routers/jobs_router.py).

| Capability | CLI | Portail | Statut |
|---|---|---|---|
| Exécuter un pipeline (sync) | `gispulse run <input> --rules <pipeline.json> -o <output>` | POST `/pipelines/execute` — `WorkflowsView` "Run" | ✅ |
| Exécuter étape par étape | _N/A_ (pas d'API CLI dédiée — l'engine exécute en bloc) | POST `/pipelines/execute-steps` — debug mode dans `NodeEditor` | ⚠️ |
| Valider un pipeline | `gispulse validate <pipeline.json>` | POST `/pipelines/validate` — auto-validate au save | ✅ |
| Lister les jobs | `gispulse jobs list [--host HOST] [--api-key KEY]` | GET `/jobs` → `JobTrackerCorner` (lazy panel) | ✅ |
| Statut d'un job | `gispulse jobs status <JOB_ID>` | GET `/jobs/{id}` → `JobTrackerCorner` détail | ✅ |
| Stream events d'un job | _N/A_ (le CLI exécute sync, pas de SSE) | GET `/jobs/{id}/events` (SSE) → progress dans `JobTrackerCorner` | ⚠️ |
| Annuler un job | `gispulse jobs cancel <JOB_ID>` | POST `/jobs/{id}/cancel` → `JobTrackerCorner` action | ✅ |
| Télécharger les features d'un job | _N/A_ — output déjà écrit en local par `gispulse run` | GET `/jobs/{id}/features` + `/jobs/{id}/download` | ⚠️ |
| Soumettre un job async | _N/A_ (`gispulse run` est synchrone) | POST `/jobs` — submit async via `WorkflowsView` | ⚠️ |
| Examples / presets pipelines | `gispulse template list` + `gispulse template use <name>` | GET `/pipelines/examples` → palette ou `WorkflowList` | ✅ |

**Asymétries loggées :**
- ⚠️ **execute-steps CLI** : utile pour debug step-by-step. → suggérer issue `feat(cli): gispulse run --step <id>` (v1.7+, déférable).
- ⚠️ **jobs SSE / async CLI** : `gispulse run` est synchrone par design (script-friendly). Le pattern async est portail-only, justifié pour un workflow long-running. Pas d'urgence.

---

## 8. Schedules — Cron jobs

API : [`schedules_router.py`](https://github.com/imagodata/gispulse/blob/main/gispulse/adapters/http/routers/schedules_router.py). Composant : `components/schedules/ScheduleForm.tsx`.

| Capability | CLI | Portail | Statut |
|---|---|---|---|
| Créer un schedule | _N/A_ — utiliser `cron` / `systemd timers` natifs OS pour wrapper `gispulse run` | POST `/schedules` → `ScheduleForm` (`CronBuilder` réutilisé depuis triggers) | ⚠️ |
| Lister schedules | _N/A_ | GET `/schedules` | ⚠️ |
| Détails / éditer schedule | _N/A_ | GET / PATCH `/schedules/{id}` | ⚠️ |
| Supprimer schedule | _N/A_ | DELETE `/schedules/{id}` | ⚠️ |
| Run-now manuel | `gispulse run` direct | POST `/schedules/{id}/run-now` | ⚠️ |

**Asymétries loggées :**
- ⚠️ **schedules CLI absent** : décision produit à confirmer — soit on assume "use cron" pour CLI users, soit on expose `gispulse schedules add/list/rm`. → suggérer issue `decision: gispulse schedules CLI subcommand` (v1.6+).

---

## 9. Marketplace — Plugins / capabilities tierces

API : [`marketplace_router.py`](https://github.com/imagodata/gispulse/blob/main/gispulse/adapters/http/routers/marketplace_router.py). Composants : `components/marketplace/`, `pages/MarketplacePage.tsx`.

| Capability | CLI | Portail | Statut |
|---|---|---|---|
| Lister plugins installés | `gispulse marketplace list [QUERY]` | GET `/marketplace/plugins` → `MarketplacePage` | ✅ |
| Rechercher dans le catalogue | `gispulse marketplace search QUERY` | GET `/marketplace/search` + `/marketplace/catalog` | ✅ |
| Détails d'un plugin | `gispulse marketplace info NAME` | GET `/marketplace/plugins/{name}` | ✅ |
| Installer un plugin | `gispulse marketplace install NAME` | POST `/marketplace/install` | ✅ |
| Désinstaller un plugin | `gispulse marketplace uninstall NAME` | DELETE `/marketplace/plugins/{name}` | ✅ |

✅ **Symétrie complète.** Surface marketplace alignée par construction depuis v1.1.0.

---

## 10. Templates — Scaffolding projets

API : `pipelines_router.py` `/examples`. CLI : `gispulse template`.

| Capability | CLI | Portail | Statut |
|---|---|---|---|
| Lister templates | `gispulse template list` | GET `/pipelines/examples` (preset library exposé dans `WorkflowList`) | ✅ |
| Scaffolder un projet depuis template | `gispulse template use <NAME> [--output-dir DIR]` | `OnboardingFlow` (premier lancement) + `SaveTemplateDialog` | ✅ |
| Créer un workflow depuis template | `gispulse template workflow` | `WorkflowList` → "From template" | ✅ |

✅ **Symétrie complète.**

---

## 11. Viewer / Portal / Engine — Lifecycle process

Surface "ops" — comment lancer GISPulse.

| Capability | CLI | Portail | Statut |
|---|---|---|---|
| Lancer le viewer (read-only) | `gispulse serve <file> [--port 8765]` | _N/A_ — le viewer est intégré dans le portail | 🔧 |
| Lancer le portail | `gispulse portal [--port 8001]` | _N/A_ — le portail **est** le portail (méta) | 🔧 |
| Lancer le moteur complet | `gispulse engine [--port 8001]` (Tauri sidecar JSON) | _N/A_ | 🔧 |
| Connect "My engine" depuis portail public | `gispulse portal --backend=<URL>` (Mode 2 — sprint v1.5.1) | `BackendStatusBanner` + `SettingsPanel` (input URL backend, persist localStorage) — **livré gispulse-portal #30** | ✅ |
| Diagnostiquer l'environnement | `gispulse doctor` | _N/A_ | 🔧 |
| Mettre à jour | `gispulse update [--check] [--force]` | _N/A_ — le portail web est self-updating, le CLI gère sa propre version | 🔧 |
| Initialiser un projet | `gispulse init [DIR] [--name NAME]` | `OnboardingFlow` (équivalent visuel pour la première session) | ✅ |
| Télémétrie opt-in | `gispulse telemetry --enable / --disable / --status` | _N/A_ — config CLI only (env var `GISPULSE_TELEMETRY=1` pour scripts) | 🔧 |

**Surface 🔧 volontaire :** le lifecycle process et la télémétrie sont CLI-only par design — le portail _est_ déjà lancé quand l'user clique. Pas de dette.

---

## 12. SQL Console — Édition / preview SQL

API : [`portal_sql_router.py`](https://github.com/imagodata/gispulse/blob/main/gispulse/adapters/http/routers/portal_sql_router.py). Composant : `components/sql/SQLConsole.tsx`.

| Capability | CLI | Portail | Statut |
|---|---|---|---|
| Exécuter une requête SQL | _N/A_ — `gispulse run` accepte le pipeline + capability `postgis_sql` | POST `/sql/execute` → `SQLConsole` | ⚠️ |
| Preview résultats SQL | _N/A_ | `SQLPreviewTable` (auth + blocklist côté backend, v1.1.0) | ⚠️ |
| Exporter résultats SQL | _N/A_ | POST `/sql/export` | ⚠️ |

**Asymétries loggées :**
- ⚠️ **SQL CLI** : fonctionnalité plutôt "exploration interactive" — déjà couvert pour batch via la capability `postgis_sql` dans un pipeline. Pas urgent. → issue déférable `feat(cli): gispulse sql --execute "SELECT ..."` (v1.7+).

---

## 13. Auth — SSO et identité

API : [`auth_router.py`](https://github.com/imagodata/gispulse/blob/main/gispulse/adapters/http/routers/auth_router.py). OSS : stub anonyme. Pro/Enterprise : OIDC (provider Google / Azure / Keycloak — voir `gispulse-enterprise`).

| Capability | CLI | Portail | Statut |
|---|---|---|---|
| Lister providers SSO | _N/A_ (pas d'auth CLI en OSS) | GET `/auth/providers` → `pages/auth/` | 🔧 |
| User info | _N/A_ | GET `/auth/me` → `UserMenu` + `AuthGuard` | 🔧 |

**Surface 🔧 :** OSS Mode 1 = single-user CLI sans auth. Mode 2 portail SaaS Pro v1.6+ ajoutera l'auth visuelle. CLI auth viendra avec `gispulse login` (issue v1.7+).

---

## Récapitulatif

| Domaine | ✅ Symétrique | ⚠️ Asymétrique | 🔧 Volontairement CLI/Portal-only | ❌ Reporté |
|---|---|---|---|---|
| Rules | 7 | 1 | 1 | 0 |
| Triggers | 6 | 4 | 1 | 0 |
| Tracking | 4 | 3 | 1 | 0 |
| Datasets | 3 | 6 | 0 | 0 |
| Examples | 1 | 0 | 3 | 0 |
| Styles | 1 | 5 | 0 | 0 |
| Run | 5 | 4 | 0 | 0 |
| Schedules | 0 | 5 | 0 | 0 |
| Marketplace | 5 | 0 | 0 | 0 |
| Templates | 3 | 0 | 0 | 0 |
| Lifecycle / Engine | 2 | 0 | 6 | 0 |
| SQL | 0 | 3 | 0 | 0 |
| Auth | 0 | 0 | 2 | 0 |
| **Total** | **37** | **31** | **14** | **0** |

**Lecture :** sur 82 capabilities publiques, 37 sont déjà symétriques, 14 sont CLI-only ou portail-only par design assumé, et **31 dettes UX sont identifiées et listées** ci-dessus avec leur issue suggérée. Aucune capability n'est silencieusement absente d'une des deux surfaces.

---

## Comment cette page reste à jour

Cette matrice est aujourd'hui **maintenue manuellement**. Toute nouvelle feature (CLI ou portail) doit être ajoutée à la ligne correspondante avec son statut. Une issue v1.6+ (`feat(scripts): generate symmetry.md from CLI ↔ portal mapping`) explore une génération automatique à partir d'un mapping déclaratif dans le code source — pour l'instant le contenu manuel reste l'autorité.

**Process pour toute nouvelle PR ajoutant une feature :**
1. Identifier la ligne à ajouter / mettre à jour dans cette matrice
2. Si la PR introduit une asymétrie, **logger l'issue de dette correspondante** dans la même session
3. Demander la review de Marco (gis-lead-dev) ou Jordan (jordan-po) pour valider le statut

**Voir aussi :**
- [Matrice de couverture des capabilities](./coverage) — pour les 100+ capabilities pipeline (test ✕ docs ✕ playground ✕ template)
- [CLI Référence](./cli)
- [Architecture](./architecture)
- [Doctrine `cli_portal_symmetry_axiom`](https://github.com/imagodata/gispulse/blob/main/docs/CLI_PORTAL_AXIOM.md) (memory)

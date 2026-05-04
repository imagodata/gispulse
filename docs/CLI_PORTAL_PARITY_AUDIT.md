# Audit de parité CLI ↔ Portal

**Date** : 2026-05-03 · **Version auditée** : v1.5.1 · **Doctrine de référence** : axiome de symétrie (CLI et Portal sont deux UIs équivalentes sur la même source de vérité `triggers.yaml` + change-log).

> **Errata 2026-05-03 (post-vérification)** — l'audit initial avait deux faux négatifs majeurs sur les endpoints existants. Cette version corrige le verdict : le portail seul couvre **plus que ~60 %** ; le bloquant "impossible d'activer le tracking depuis le portail" n'existe pas (l'endpoint `POST /datasets/{id}/enable_tracking` est shippé depuis v1.5.x avec auto-register `WatcherRegistry`). Trois P0 réels demeurent : changelog inspection, watcher dashboard UI, triggers.yaml import. Système doctor (P0-4) livré 2026-05-03.

> **Leçon** : les audits Explore agent peuvent hallucinate l'absence d'endpoints quand ils ne grep pas tous les routers. Toujours croiser avec [`docs-site/guide/symmetry.md`](../docs-site/guide/symmetry.md) qui est la matrice manuellement maintenue.

Réf. complémentaires : [`USAGE_MATRIX.md`](USAGE_MATRIX.md), [`INTEGRATION_MATRIX.md`](INTEGRATION_MATRIX.md), [`TRIGGERS_GUIDE.md`](TRIGGERS_GUIDE.md).

---

## 1. Surface auditée

**CLI (Python — Click/Typer)** :
- [`gispulse/cli.py`](../gispulse/cli.py) — verbes principaux (`init`, `run`, `layers`, `formats`, `serve`, `validate`, `capabilities`, `info`, `doctor`, `engine`, `update`, `jobs *`, `template *`, `marketplace *`, `telemetry`)
- [`gispulse/cli_triggers.py`](../gispulse/cli_triggers.py) — `triggers run --once|--watch`, `triggers validate`, `triggers list`
- [`gispulse/cli_track.py`](../gispulse/cli_track.py) — `track install|uninstall|list|tail|doctor`
- [`gispulse/cli_watch.py`](../gispulse/cli_watch.py) — `watch` (legacy daemon)
- [`gispulse/cli_portal.py`](../gispulse/cli_portal.py) — `portal` (lance la SPA bundlée + StaticFiles same-origin)

**Portal (React/TS — `gispulse-portal/`)** :
- Routes : `/explorer`, `/map`, `/workflows`, `/datasets`, `/catalog`, `/schema`, `/marketplace`, `/login`
- Pages : `ExplorerView`, `WorkflowsView`, `DatasetsView`, `MapView`, `CatalogWorkspace`, `SchemaView`, `MarketplacePage`
- API clients : `datasets.ts`, `pipelines.ts`, `catalog.ts`, `marketplace.ts`, `projects.ts`, `scenarios.ts`, `schedules.ts`, `styles.ts`

**Backend HTTP (consommé par les deux)** :
- [`gispulse/adapters/http/`](../gispulse/adapters/http/) — routers FastAPI (datasets, pipelines, triggers, jobs, runs, ws, capabilities)

---

## 2. Matrice de parité détaillée

Légende : ✅ disponible · ⚠️ partiel · ❌ absent

### Datasets

| Fonctionnalité | CLI | Portal | Notes |
|---|---|---|---|
| Browser / lister datasets serveur | ❌ | ✅ | CLI n'a que `layers FILE` (fichier local) |
| Uploader / importer dataset | ❌ | ✅ | upload, import-url |
| Supprimer dataset | ❌ | ✅ | |
| Inspecter schéma + CRS + métadata | ✅ `info` | ⚠️ | CLI plus riche (styles, drivers, formats) |
| Exporter (GPKG, GeoJSON, Parquet, CSV) | ⚠️ via `run` | ✅ | Portal : export multi-format direct |
| Éditer features (SQL execute / row CRUD) | ❌ | ✅ | Portal seul |
| Styling (QML / SLD / colors) | ❌ | ✅ StyleEditor | Portal seul |

### Pipelines / Rules

| Fonctionnalité | CLI | Portal | Notes |
|---|---|---|---|
| Édition pipeline v2 | ✅ fichier JSON | ✅ NodeEditor visuel | parité (vecteurs différents) |
| Édition règle v1 (legacy) | ❌ | ❌ | déprécation v1.5 acceptable |
| Validation pipeline | ✅ `validate` | ✅ `/pipelines/validate` SSE | parité ✅ |
| Exécution one-shot | ✅ `run` | ✅ `/pipelines/execute` | parité ✅ |
| Visualisation résultat | ⚠️ `serve` viewer simple | ✅ MapView complet | Portal mieux |
| Templates / presets browse | ✅ `template list/use` | ⚠️ via marketplace browse | parité partielle |
| Capabilities introspection | ✅ `capabilities` | ✅ fetch `/capabilities` | parité ✅ |

### Triggers DML

| Fonctionnalité | CLI | Portal | Notes |
|---|---|---|---|
| Créer / éditer un trigger | ⚠️ YAML manuel | ✅ TriggerBuilderModal | Portal mieux |
| Configurer une action / webhook | ⚠️ YAML manuel | ✅ ActionEditor | Portal mieux |
| Tester un trigger sur payload | ❌ | ✅ `/triggers/{id}/evaluate` SSE | Portal seul |
| Activer / désactiver un trigger | ⚠️ via éditer YAML | ✅ toggle UI | Portal mieux |
| Importer un `triggers.yaml` externe | ✅ `triggers validate --config` | ❌ | **Gap P0** |
| Lister triggers SQL natifs (`sqlite_master`) | ✅ `triggers list` | ❌ | gap moyen |

### Change-tracking (cœur du runtime DML)

| Fonctionnalité | CLI | Portal | Notes |
|---|---|---|---|
| Activer le change-log sur un GPKG | ✅ `track install` | ✅ `POST /datasets/{id}/enable_tracking` | **CORRIGÉ** — déjà shippé v1.5.x, audit initial faux négatif. Auto-register `WatcherRegistry`. |
| Désactiver le change-log | ✅ `track uninstall` | ✅ `POST /datasets/{id}/disable_tracking` | **CORRIGÉ** — déjà shippé. |
| Lister couches tracées | ✅ `track list` | ✅ `GET /datasets/{id}/tracking_status` | **CORRIGÉ** — déjà shippé. |
| Inspecter le change-log (tail rows) | ✅ `track tail` | **❌** | gap réel — debug aveugle depuis le portail |
| Stats change-log (pending par couche) | ✅ `track list` | ⚠️ partiel (`tracking_status` ne donne pas pending counts détaillés) | gap moyen |
| Diagnostic santé tracking | ✅ `track doctor [--auto-fix]` | **❌** | gap réel — debug |

### Watcher / runtime daemon

| Fonctionnalité | CLI | Portal | Notes |
|---|---|---|---|
| Lancer le watcher long-running | ✅ `triggers run --watch` | ✅ **AUTO** sur `enable_tracking` (WatcherRegistry) | **CORRIGÉ** — pas besoin d'endpoint dédié, le watcher démarre automatiquement à l'activation du tracking |
| Lister les watchers actifs | ✅ via logs CLI | ❌ pas d'endpoint `/watchers` ni dashboard UI | gap observabilité |
| Arrêter un watcher | ✅ Ctrl+C | ✅ via `disable_tracking` (registry.unregister) | parité fonctionnelle |
| Mode one-shot tick | ✅ `triggers run --once` | ⚠️ via `/triggers/eval-stream` | parité partielle |
| Voir les fires en stream | ✅ logs stderr structurés | ⚠️ SSE event stream | parité fonctionnelle |
| Voir le journal des runs (jobs) | ✅ `jobs list/status/cancel` | ✅ jobs view + SSE | parité ✅ |

### Système / opérations

| Fonctionnalité | CLI | Portal | Notes |
|---|---|---|---|
| Doctor (GDAL/DuckDB/PostGIS/OIDC) | ✅ `doctor` | ⚠️ portal assets seulement | gap |
| `gispulse update [--check]` | ✅ | ❌ | acceptable (touche pip/wheel) |
| Marketplace install / uninstall plugin | ✅ | ❌ (browse seul) | acceptable (touche pip) |
| Scaffold projet (`init`) | ✅ | ❌ | acceptable |
| Auth / login | ❌ (Bearer env) | ✅ OIDC | acceptable (cohérent) |
| Telemetry opt-in | ✅ | ❌ | acceptable (machine-side) |

---

## 3. Gaps prioritaires (corrigé après vérification 2026-05-03)

| # | État | Endpoint | Remplace | Sans ça, un user portal-only ne peut pas… |
|---|---|---|---|---|
| ~~P0-1~~ | ✅ déjà shippé v1.5.x | `POST /datasets/{id}/enable_tracking` + `disable_tracking` + `GET /tracking_status` | `gispulse track install/uninstall/list` | (déjà couvert — issue #92 à fermer) |
| **P0-2** | ❌ à livrer | `GET /datasets/{id}/changelog` (paginated tail) + `POST /changelog/doctor` | `gispulse track tail/doctor` | …débugger "pourquoi mon trigger n'a pas fire" |
| **P0-3** | ⚠️ partiel | `GET /watchers` (list) + UI "Background Tasks" + stats temps réel | `gispulse triggers run --watch` (visualisation seule, le daemon démarre auto sur `enable_tracking`) | …voir / superviser les watchers actifs depuis le navigateur |
| **P0-4** | ✅ livré 2026-05-03 | `POST /system/doctor` | `gispulse doctor` | (livré — issue #91, EPIC #90) |
| **P0-5** | ❌ à livrer | `POST /triggers/import` (validation + hydratation) | `gispulse triggers validate --config` | …reprendre dans le portail un projet édité en CLI |

**Sprint réduit** : 3 P0 réels demeurent (P0-2 + P0-3 dashboard + P0-5) ≈ **5-7 jours** dev (au lieu de 10-13 estimés initialement).

**Note d'architecture** : aucun des P0 restants ne casse l'axiome de symétrie. Ils HTTP-isent une opération déjà implémentée en Python (réutiliser les fonctions de `cli_track.py` et `cli_triggers.py` côté backend, ne pas réimplémenter en JS).

---

## 4. Doctrine front — vérifications

Recherche systématique de logique runtime qui devrait rester backend-only :

| Suspect inspecté | Verdict | Justification |
|---|---|---|
| `PredicateBuilder.tsx` (ATTR_OPS, GEOM_OPS, AGG_COMPARE_OPS) | ✅ acceptable | enums UI labels seulement, pas d'évaluation |
| `NodeEditor.tsx` (build PipelineSpec → executePipeline) | ✅ acceptable | sérialisation client puis POST `/pipelines/execute`, runtime côté backend |
| `TriggerBuilderModal` + `ActionEditor` | ✅ acceptable | forme JSON envoyée à `/triggers/{id}/evaluate` SSE — aucune éval locale |
| Capabilities catalog côté front | ✅ acceptable | fetch backend, pas de registry hardcodée |
| MarketplacePage filters | ✅ acceptable | UX state seul (catégories, search) |

**Conclusion** : le portail respecte la doctrine "sucre syntaxique". Toute la logique reste dans le runtime Python.

---

## 5. Asymétries acceptables (à NE PAS combler)

- **Auth/login portal-only** : la CLI est stateless avec Bearer token via env. Cohérent.
- **`marketplace install/uninstall` CLI-only** : touche `pip`, doit rester côté machine.
- **`gispulse update` CLI-only** : idem, met à jour le wheel.
- **`init` (scaffold projet) CLI-only** : crée fichiers locaux, sans intérêt portail.
- **Édition règle v1 absente partout** : déprécation v1.5 documentée.
- **Telemetry opt-in CLI-only** : configuration machine-side.

---

## 6. Plan de remédiation

Avec les 5 endpoints P0 + l'UI `Background Tasks` :

- Le portail passe de "design tool + viewer" à **complete headless workbench**
- La promesse big-launch v1.5.2 ("tout depuis le navigateur, optionnellement depuis le terminal") tient mécaniquement
- L'axiome de symétrie est respecté sans dette résiduelle bloquante

Estimation grossière : **1 sprint de 2 semaines** pour les 5 endpoints + UI (ils HTTP-isent du code existant, pas de nouveau runtime).

---

## Voir aussi

- [`PARITY_P0_SPEC.md`](PARITY_P0_SPEC.md) — spec technique des 5 endpoints P0 (contrats, réutilisation code existant, ordre de livraison)
- [`USAGE_MATRIX.md`](USAGE_MATRIX.md) — matrice persona × scénario × canal (replace "Portal" par "Portal + CLI" tant que les 5 P0 ne sont pas livrés)
- [`INTEGRATION_MATRIX.md`](INTEGRATION_MATRIX.md) — matrice clients GIS × modes d'échange
- [`TRIGGERS_GUIDE.md`](TRIGGERS_GUIDE.md) — sémantique des triggers DML / contrat webhook

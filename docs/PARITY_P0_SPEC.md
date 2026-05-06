# Spec — 5 endpoints P0 pour fermer la dette CLI ↔ Portal

> **Errata 2026-05-03** — vérification post-audit : P0-1 (enable/disable tracking) **est déjà shippé** depuis v1.5.x via `POST /datasets/{id}/enable_tracking,disable_tracking` + auto-register `WatcherRegistry`. Sa section ci-dessous (#P0-1) est **caduque**. P0-3 watcher endpoint a aussi été redimensionné : le watcher démarre automatiquement à l'activation du tracking, donc seul un dashboard d'observabilité (`GET /watchers`) reste à livrer. Sprint réduit à 3 P0 réels (P0-2, P0-3 dashboard, P0-5) ≈ 5-7 jours.
>
> **Update 2026-05-06** — P0-3 dashboard livré (issue #95) : `GET /watchers` + `GET /watchers/{dataset_id}` exposent les counters runtime (`tick_count`, `rows_processed`, `fire_count`, `error_count`, `last_*_at`, `last_error_msg`) + le snapshot config (poll_interval, batch_limit, bulk_threshold, bulk_eval, layers, gpkg_path). `WatcherRegistry` désormais initialisé en portal mode aussi pour que le dashboard renvoie 200 sur instance fraîche. EPIC #90 fermé.

**Contexte** : voir [`CLI_PORTAL_PARITY_AUDIT.md`](CLI_PORTAL_PARITY_AUDIT.md). Cinq endpoints HTTP qui HTTP-isent du code Python déjà existant (réutilisation, pas réimplémentation).

**Principe directeur** : chaque endpoint réutilise les fonctions de `cli_track.py` / `cli_triggers.py` / `gispulse.persistence.gpkg_schema`. Le portail ne réimplémente rien — il appelle. Doctrine de symétrie respectée.

**Auth** : tous les endpoints écrivent dans le filesystem ou pilotent des process → exigent un user authentifié avec rôle `editor` au minimum (cf. middleware existant). En portal-mode self-hosted local (`gispulse portal`), auth désactivée par défaut comme aujourd'hui.

---

## P0-1 — Activer / désactiver le change-tracking sur un dataset

**Remplace** : `gispulse track install/uninstall`.
**Sans ça** : un user portal-only ne peut pas du tout activer un trigger DML — rien ne fire jamais sur GPKG.

### Endpoints

```
POST   /datasets/{id}/tracking          → enable
DELETE /datasets/{id}/tracking          → disable (toutes couches)
```

### Body POST

```json
{
  "layers": ["parcelles", "permis"],   // ou null = --all-layers
  "primary_key": "fid"                  // optional, default = "fid"
}
```

### Réponse 200

```json
{
  "dataset_id": "…",
  "tracked_layers": [
    {"layer": "parcelles", "operations": ["insert", "update", "delete"], "pk": "fid"},
    {"layer": "permis",    "operations": ["insert", "update", "delete"], "pk": "fid"}
  ],
  "changelog_table": "_gispulse_changelog",
  "installed_at": "2026-05-03T14:32:11Z"
}
```

### Erreurs

| Code | Cas |
|---|---|
| 404 | dataset_id inconnu |
| 409 | tracking déjà installé sur la couche (idempotent : retourner 200 avec `already_installed: true` si flag `--force` absent) |
| 422 | couche n'existe pas dans le GPKG, PK invalide, GPKG non writable |

### Réutilisation

- `gispulse.persistence.gpkg_schema.install_change_tracking()` (déjà ligne 406)
- `gispulse.persistence.gpkg_schema.uninstall_change_tracking()` (déjà ligne 469)
- Validation identifier : `_validate_identifier()` (déjà ligne 41) — protège contre l'injection SQL

### Tests

- 200 happy path mono-couche
- 200 `--all-layers` équivalent (`layers: null`)
- 409 idempotence
- 422 PK manquante
- 422 GPKG read-only filesystem
- intégration : POST tracking → INSERT row via API portal → GET /changelog vérifie qu'une ligne arrive

### UI Portal

- Bouton `Enable change-tracking` dans `DatasetsView` → `/datasets/{id}` page détails
- État affiché : "tracked since YYYY-MM-DD on layers [...]"
- Bouton rouge `Disable tracking` avec confirmation modale

---

## P0-2 — Inspecter le change-log d'un dataset

**Remplace** : `gispulse track tail`, `track list`, `track doctor`.
**Sans ça** : impossible de débugger "pourquoi mon trigger n'a pas fire" depuis le portail.

### Endpoints

```
GET  /datasets/{id}/changelog                       → liste paginée
GET  /datasets/{id}/changelog/stats                 → résumé par couche
POST /datasets/{id}/changelog/doctor                → diagnostic santé (auto-fix optionnel)
```

### Query params (`/changelog`)

| Param | Type | Default | Notes |
|---|---|---|---|
| `layer` | string | (toutes) | filtre par couche tracée |
| `op` | enum | (toutes) | `insert` / `update` / `delete` |
| `since_id` | int | 0 | curseur (id du dernier event vu) |
| `limit` | int | 50 | max 500 |

### Réponse `/changelog`

```json
{
  "items": [
    {
      "id": 142,
      "layer": "parcelles",
      "op": "INSERT",
      "row_id": "f00…",
      "ts": "2026-05-03T14:33:01Z",
      "user": "alice@…"   // si configuré
    }
  ],
  "next_since_id": 142,
  "has_more": false
}
```

### Réponse `/changelog/stats`

```json
{
  "layers": [
    {"layer": "parcelles", "pending": 12, "last_event_at": "2026-05-03T14:33:01Z", "ops": {"insert": 5, "update": 6, "delete": 1}}
  ],
  "total_pending": 12
}
```

### Réponse `/changelog/doctor`

Schéma identique à la sortie JSON de `gispulse track doctor --json` (cf. `cli_track.py:464`). Champs `health_score`, `issues[]`, `fixed[]` (si `auto_fix=true`).

### Réutilisation

- Helpers `_installed_triggers()`, `_list_spatial_layers()` de `cli_track.py:61-100`
- Doctor : extraire la fonction sous-jacente de `cli_track.py:cmd_doctor` dans un module shared `gispulse.persistence.changelog_doctor` puis l'appeler depuis CLI ET endpoint (parité forte)

### UI Portal

- Onglet `Change-log` dans `DatasetsView` → table paginée live (curseur SSE optionnel)
- Bouton `Run doctor` → modal avec `health_score` + checklist `issues[]`

---

## P0-3 — Watcher / daemon long-running depuis le portail

**Remplace** : `gispulse triggers run --watch`.
**Sans ça** : impossible de démarrer un polling daemon depuis le navigateur.

### Endpoints

```
POST   /watchers                       → démarrer un watcher
GET    /watchers                       → lister watchers actifs
GET    /watchers/{id}                  → détail d'un watcher (état, stats, logs récents)
GET    /watchers/{id}/events           → SSE stream des fires
DELETE /watchers/{id}                  → arrêter un watcher
```

### Body POST

```json
{
  "config_source": "stored",           // "stored" (triggers de la base) | "yaml"
  "yaml_content": null,                // requis si config_source=yaml
  "dataset_id": "…",                   // dataset cible (GPKG path résolu côté backend)
  "poll_interval_ms": 100,
  "name": "preset-ppri-prod"
}
```

### Réponse 201

```json
{
  "id": "wtch_…",
  "status": "running",
  "started_at": "…",
  "config": { /* ... */ },
  "stats": {"ticks": 0, "fires": 0, "errors": 0}
}
```

### Modèle d'exécution

Watchers tournent dans le **process backend** (ASGI worker), pas en sous-process. Une `asyncio.Task` par watcher, supervisée par un `WatcherRegistry` singleton. Si le process redémarre, les watchers actifs sont restaurés depuis la table `watchers_state` (persistance).

**Limite Community OSS** : max 1 watcher concurrent par instance backend (raison : single-writer GPKG). Pro lift cette limite.

### Réutilisation

- `gispulse.runtime.build_runtime()` + `ChangeLogWatcher` existants (cf. `cli_triggers.py:107-292`)
- Wrapper `WatcherRegistry` à créer sous `gispulse/runtime/watcher_registry.py` — partagé CLI futur (`gispulse triggers run --watch` peut à terme écrire dans la table aussi pour visualisation portal)

### UI Portal

- Page `/runtime` (nouvelle) — section "Background Tasks" : liste des watchers, start/stop, vue détaillée avec SSE des fires
- Indicateur de statut watcher dans le header (badge "1 watcher running")

---

## P0-4 — Diagnostic système depuis le portail

**Remplace** : `gispulse doctor`.
**Sans ça** : impossible de troubleshoot une install cassée sans terminal.

### Endpoint

```
POST /system/doctor
```

### Body (optionnel)

```json
{
  "checks": ["python", "gdal", "duckdb", "spatialite", "postgis", "oidc", "assets"]
}
```

Si absent : tous les checks.

### Réponse 200

```json
{
  "summary": {"ok": 5, "warning": 1, "error": 0},
  "checks": [
    {"name": "python",     "status": "ok",      "detail": "3.12.4"},
    {"name": "gdal",       "status": "ok",      "detail": "3.9.0"},
    {"name": "duckdb",     "status": "ok",      "detail": "1.0.0 + spatial"},
    {"name": "spatialite", "status": "warning", "detail": "mod_spatialite not on LD_LIBRARY_PATH; fallback ok"},
    {"name": "postgis",    "status": "skipped", "detail": "no PG_DSN configured"},
    {"name": "oidc",       "status": "ok",      "detail": "issuer reachable"}
  ],
  "ran_at": "…"
}
```

### Réutilisation

- Refactor `cli.py:cmd_doctor` (lignes 491-648) → extraire dans `gispulse/diagnostics/system.py` avec une fonction pure `run_checks(names: list[str]) -> DoctorResult` callable depuis CLI ET endpoint
- CLI devient un consommateur de cette fonction → `gispulse doctor --json` peut sérialiser exactement le même schéma

### UI Portal

- Page `/system/health` (nouvelle, footer link) — bouton `Run diagnostic`, table des checks colorée, copy-to-clipboard du JSON pour issues GitHub

### Sécurité

- Endpoint **admin only** (rôle `admin`), même en portal-mode local désactiver pour `auth_disabled` impose un confirm prompt → exposition de versions = recon léger

---

## P0-5 — Importer / valider un `triggers.yaml` externe

**Remplace** : `gispulse triggers validate --config`.
**Sans ça** : un user qui édite en CLI ne peut pas reprendre dans le portail.

### Endpoints

```
POST /triggers/import              → valider et hydrater l'éditeur (dry-run)
POST /triggers/import?commit=true  → valider et persister (créer les triggers)
```

### Body

```yaml
# multipart upload OU body raw application/x-yaml
```

### Réponse 200 (dry-run)

```json
{
  "valid": true,
  "summary": {"triggers": 4, "actions": 6},
  "preview": [
    {
      "name": "alerte_ppri",
      "table": "permis",
      "operation": "INSERT",
      "predicate": "intersects(ppri_zone)",
      "actions": ["webhook:teams"]
    }
  ],
  "warnings": []
}
```

### Réponse 422 (invalide)

```json
{
  "valid": false,
  "errors": [
    {"path": "$.triggers[0].predicate", "message": "unknown function 'intersects_with'", "line": 12}
  ]
}
```

### Réutilisation

- Parser et validateur de `cli_triggers.py:cmd_validate` (lignes 294-346)
- Extraction recommandée : `gispulse/runtime/yaml_loader.py` partagé, callable depuis CLI ET endpoint

### UI Portal

- Bouton `Import YAML` dans `WorkflowsView` → file picker / textarea
- Modal preview avec diff (existing triggers vs imported)
- Bouton `Commit` après validation OK

---

## Synthèse par priorité

| # | Endpoint | Effort dev (j) | Débloque |
|---|---|---|---|
| **P0-1** | `POST/DELETE /datasets/{id}/tracking` | 2 | tout le mode DML portal-only |
| **P0-2** | `GET /datasets/{id}/changelog{,/stats,/doctor}` | 2-3 | debug change-log |
| **P0-3** | `POST/GET/DELETE /watchers` + UI | 4-5 | daemon depuis navigateur |
| **P0-4** | `POST /system/doctor` | 1 | diagnostic install |
| **P0-5** | `POST /triggers/import` | 1-2 | reprise CLI → portal |

**Total estimé** : 10-13 jours dev backend + UI = sprint v1.5.2 (2 semaines avec QA + docs).

**Ordre de livraison recommandé** : P0-4 (le moins risqué, refactor doctor vers shared module = template pour les autres extractions) → P0-1 (débloque mode DML) → P0-2 (suit immédiatement) → P0-5 (rapide) → P0-3 (le plus complexe, watcher registry).

---

## Voir aussi

- [`CLI_PORTAL_PARITY_AUDIT.md`](CLI_PORTAL_PARITY_AUDIT.md) — audit complet, contexte
- [`USAGE_MATRIX.md`](USAGE_MATRIX.md) — caveat de parité aligné
- `gispulse/cli_track.py` — code à HTTP-iser pour P0-1 / P0-2
- `gispulse/cli_triggers.py` — code à HTTP-iser pour P0-3 / P0-5
- `gispulse/cli.py:491-648` — `cmd_doctor` à refactorer pour P0-4

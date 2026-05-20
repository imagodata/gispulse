# Triggers Guide

GISPulse triggers react to DML events on tracked datasets and dispatch one or more actions (notify, set_field, webhook, run_sql, …). This page is the operator-oriented summary; the canonical reference for trigger / rule JSON shape lives in **[`docs-site/guide/rules.md`](../docs-site/guide/rules.md)** (rendered at <https://imagodata.github.io/gispulse/guide/rules>).

> **2026-05-20 — unified `version: 3` manifest (ADR 0005)** : triggers can now be declared inside the new unified manifest alongside `sources:` / `models:` / `staging:`. The semantics of each trigger are unchanged; only the surrounding file shape is new. See [`docs-site/guide/elt-manifest.md`](../docs-site/guide/elt-manifest.md) for the v3 reference and [`elt-migration.md`](../docs-site/guide/elt-migration.md) for `gispulse migrate`. v1 / v2 trigger files keep loading (deprecated at v1.10.1, removed at v2.0.0).

## Architecture in one diagram

```
DML write              ChangeLogWatcher           ActionDispatcher
   │                        │                          │
   ▼                        ▼                          ▼
GPKG / DuckDB ─poll(100ms)─▶ trigger.fired (WS)        actions:
   _change_log              │                            • notify (pg_notify + EventHub)
                            ▼                            • set_field / update_aggregate
                       TriggerEvaluator                  • run_job / run_graph
                            │                            • webhook (HttpWebhookClient, SSRF-safe)
                            ▼                            • enqueue / log_event
                       FiredTrigger ──────────────────▶  • run_sql (Forge operations)
```

The HTTP runtime wires `ChangeLogWatcher` + `ActionDispatcher` + `HttpWebhookClient` automatically in the FastAPI lifespan. Embedded users (worker, script) inject them manually — see the rules guide.

## Standalone CLI mode

Since v1.2.1, GISPulse ships a headless trigger runtime under `gispulse triggers`. It executes the same `ChangeLogWatcher` → `TriggerEvaluator` → `ActionDispatcher` pipeline against a local GeoPackage, with no FastAPI process required. Useful for cron jobs, on-prem ETL, QGIS sidecar workflows.

### Quick start

```bash
# v1.2.1 PyPI release upcoming — until then:
git clone https://github.com/imagodata/gispulse.git
cd gispulse && pip install -e .

# Once published:
pip install "gispulse>=1.2.1"
```

### YAML config

```yaml
version: 1
gpkg: ./parcels.gpkg          # overridable via --gpkg

triggers:
  - name: notify_high_value
    table: parcels
    pk_col: fid
    when: [INSERT, UPDATE]
    predicate: "valeur > 100000 AND commune == 'Lyon'"
    actions:
      - type: webhook
        url: https://example.com/hooks/parcels

  - name: stamp_enriched_at
    table: parcels
    when: [INSERT]
    actions:
      - type: set_field
        field: enriched_at
        value: 2026-04-27

  - name: audit_deletes
    table: parcels
    when: [DELETE]
    actions:
      - type: run_sql
        expression: "INSERT INTO audit_log (fid, op, ts) VALUES (OLD.fid, 'DELETE', CURRENT_TIMESTAMP)"

security:
  webhook_allowlist:
    - example.com

runtime:
  poll_interval_ms: 1000
  max_batch: 200
```

### Source-watched triggers (`source_changed`)

Every trigger above is a **DML trigger** — it declares a `table` and fires
on a local edit. v1.7.0 adds a second shape: the **source-watched
trigger**, which fires when an *external* data source publishes a new
revision (EPIC #175, issue #195).

```yaml
triggers:
  - name: refresh_on_new_cadastre
    on:
      source_changed: cadastre://parcelles   # <source>://<entry> URI
      frequency: mensuel                      # poll cadence (optional)
    actions:
      - type: log_event
      - type: webhook
        url: https://example.com/hooks/cadastre-updated
```

- A `source_changed` trigger declares **no `table`, `when` or
  `predicate`** — it watches a source, not a layer. (The config still
  needs the top-level `gpkg:` key — the project database.)
- `source_changed` is a `<source>://<entry>` URI, resolved against the
  `core.sources.SOURCES` registry. The source comes from an installed
  `gispulse-src-*` plugin — see the
  [Source Plugin Authoring Guide](./SOURCE_PLUGIN_GUIDE.md).
- `frequency` sets the `revision()` poll cadence. Recognised labels:
  `temps-reel`, `quotidien`, `hebdomadaire`, `mensuel`, `trimestriel`,
  `annuel`, `pluriannuel`; an unknown label falls back to the default
  interval.
- The `SourceWatcherRegistry` polls the source's `revision()` token — a
  cheap freshness probe, never a full fetch. The first tick seeds the
  baseline (no firing); a token change afterwards fires the trigger.

Run a source-watched config with the long-running watch loop:

```bash
gispulse watch ./project.gpkg --rules triggers.yaml
```

A full runnable example ships in
[`examples/triggers/source_changed_cadastre.yaml`](../examples/triggers/source_changed_cadastre.yaml);
a step-by-step walkthrough lives in
`docs-site/guide/walkthroughs/watch-external-source.md`.

### Commands

```bash
# Validate config + GPKG layer cross-check (no execution)
gispulse triggers validate --config triggers.yaml

# One-shot tick (process pending change-log entries, exit)
gispulse triggers run --config triggers.yaml --once

# Override the GPKG path from the config
gispulse triggers run --config triggers.yaml --gpkg /var/data/today.gpkg --once

# Daemon mode (SIGINT/SIGTERM clean shutdown, reload on config mtime change)
gispulse triggers run --config triggers.yaml --watch

# Inspect tracked tables on a GPKG
gispulse triggers list --gpkg parcels.gpkg
```

Human-friendly output goes to **stdout** (Rich-formatted). Structured per-tick JSON metrics (`fired`, `skipped_predicate`, `errors`, `duration_ms`, `sqlite_busy_retries`) go to **stderr** for log shippers. Exit codes: `0` success, `1` config / GPKG / fatal runtime error (incl. 10 consecutive failed ticks under `--watch`), `2` partial trigger failures.

### Row dict semantics

Predicates and `set_field` value templates resolve attributes against the row payload supplied by the watcher:

| Reference | Meaning |
|---|---|
| `new.col` | Value of `col` after the DML write (the row that triggered) |
| `old.col` | Value of `col` before the write (UPDATE / DELETE only) |
| `col` (bare) | Equivalent to `new.col` — the SQL-feel default |

For `INSERT` rows, `old.*` is `None` and any non-`IS NULL` comparison against it is false. For `DELETE` rows, `new.*` is `None`.

### Predicate DSL

```
predicate    := or_expr
or_expr      := and_expr ("OR" and_expr)*
and_expr     := not_expr ("AND" not_expr)*
not_expr     := "NOT" not_expr | comparison
comparison   := attr op literal
              | attr "IS" "NOT"? "NULL"
              | attr ("NOT" "IN" | "IN") list_literal
              | "(" or_expr ")"
attr         := identifier ("." identifier)*
op           := "==" | "!=" | ">" | ">=" | "<" | "<="
literal      := number | string | boolean | "null"
list_literal := "[" literal ("," literal)* "]"
```

Examples:

```yaml
predicate: "valeur > 100 AND commune == 'Lyon'"
predicate: "status IN ['pending', 'review'] AND NOT archived"
predicate: "old.zoning != new.zoning"               # value changed
predicate: "geometry_area IS NOT NULL AND area_m2 >= 500.0"
```

The parser is hand-written recursive descent — **no `eval`, no `simpleeval`, no third-party dep**. Operator alphabet is closed, identifiers match `[A-Za-z_][A-Za-z0-9_]*`, dunders are refused, max nesting depth is 32, NUL bytes and non-printable control characters are rejected before parsing. Errors carry `line` / `col` so `triggers validate` prints actionable diagnostics.

### SQL safety (run_sql / set_field)

Every SQL string flows through `persistence/sql_guardrails.py:enforce()` before SQLite sees it.

**Allowed leading keywords** (default, YAML actions): `INSERT`, `UPDATE`, `DELETE`, `SELECT`.
**Hard-blocked unconditionally**: `ATTACH`, `DETACH`, `PRAGMA`, `VACUUM`, `REINDEX`, `ANALYZE`, `BEGIN`, `COMMIT`, `ROLLBACK`, `SAVEPOINT`, `RELEASE`, `LOAD_EXTENSION`.
**Pattern-blocked anywhere in the statement**: `writable_schema`, `sqlite_master`, `sqlite_temp_master`, `attach database`, `detach database`, `load_extension`.
**Protected table prefixes** (no writes): `gpkg_*`, `rtree_*`, `sqlite_*`, `_gispulse_*`. Adding a layer goes through the regular write API, not a YAML `run_sql`.
**Multi-statement payloads refused**: any meaningful semicolon between statements (`INSERT …; DROP TABLE x`) is rejected — string literals are masked before scanning, so `'a;b'` does not count.
**Max paren depth**: 5 (CTEs / sub-queries beyond that are refused as DoS).

DDL (`CREATE` / `DROP` / `ALTER`) is gated by an internal `allow_ddl` flag used only for first-boot migrations; YAML actions never set it. A failing guardrail raises `SecurityError`, which the retry layer **never** retries — bad payloads fail fast.

### Concurrency with QGIS

GISPulse opens the GPKG with WAL journaling so a QGIS session and the trigger daemon can coexist on the same file most of the time. Two caveats:

- **`SQLITE_BUSY` retry**: The runtime wraps DML through `RetryingSqlExecutor`, which retries `SQLITE_BUSY` / `database is locked` errors with exponential backoff (5 attempts max, 30 s total cap). Sustained contention still surfaces as a tick failure.
- **Network filesystems**: SQLite explicitly does **not** support GPKG files on NFS / SMB / cloud-mounted shares. Lock semantics are unreliable and silent corruption is possible. Run `gispulse triggers` against a local-disk GPKG; replicate to the share after the tick.
- **Single writer**: Inherits the OSS limit at the top of this guide — concurrent writers serialize on the file lock, regardless of WAL.

### Known limitations

- **Mode 2 (portail UI for trigger CRUD)** — on the roadmap, not shipped in v1.2.1. Current path is YAML-only.
- **Post-commit row snapshot**: `_load_row_values()` reads the row from the GPKG **after** the DML commits. Under heavy concurrent writes, two rapid UPDATEs on the same row can cause the second tick to observe the third state instead of the second; predicates of the form `old.x != new.x` may produce false negatives in that race. Acceptable for batch / cron workloads, less so for sub-second writer mixes — switch to PostGIS triggers for those.
- **Reload-on-config-change latency**: the daemon polls the YAML mtime once per tick. Latency between save and reload = `poll_interval_ms` + the tick currently in flight. A slow webhook can therefore push the perceived reload by several seconds.
- **No DLQ on action failure**: `run_sql` / `set_field` failures are logged and counted (`errors` in the per-tick JSON) but **not** retried or queued. Webhook actions retain their own retry policy (#451).

### See also

- [`examples/cli/triggers.yaml`](../examples/cli/triggers.yaml) — runnable reference config

## Webhook actions

Format payload, sécurité (SSRF blocklist, HMAC), retries — see **[Integration Matrix → Webhook payload](INTEGRATION_MATRIX.md#webhook-payload)** and **[`docs-site/guide/rules.md` → Webhook actions](../docs-site/guide/rules.md#webhook-actions-zapier-arcgis-geoevent-make-n8n-)**.

## Limites OSS (Community tier)

GISPulse OSS prioritise la simplicité de déploiement (un seul fichier GPKG, zéro serveur) sur la performance multi-tenant. Les limites ci-dessous sont structurelles à l'architecture portable et **levées en tier ESB / Pro / Enterprise**.

### 1. Single writer (SpatiaLite / GPKG)

Le moteur SQLite sous-jacent sérialise les writes : un seul `INSERT` / `UPDATE` à la fois sur l'ensemble du fichier. Les rules concurrentes ou les triggers cascadés attendent le file-lock. Acceptable pour les sessions UI ou les batch nocturnes ; bloquant pour les workloads multi-utilisateur.

→ **Workaround** : passer en mode **Persistant (PostGIS)** — le repo Pro/Enterprise expose le même contrat de triggers sur PostgreSQL avec `pg_notify` (sub-50 ms latency, MVCC).

### 2. Polling 100 ms (vs `pg_notify` push)

Le `ChangeLogWatcher` poll le `_gispulse_change_log` à intervalle fixe (par défaut 200 ms, configurable). La latence p99 d'un trigger est donc bornée par cet intervalle. PostGIS triggers (Pro) propagent en `pg_notify` push avec latence sub-50 ms.

### 3. Pas de retry orchestré côté ESB

L'action `webhook` est résiliente par elle-même (2 retries 5xx + connect timeout, back-off `1s/3s`), mais les autres actions (`run_sql`, `set_field`, …) sont **fire-and-forget** : si l'exécution échoue (verrou DB, transaction rollback), le trigger est marqué dispatché sans réessai. La couche **DLQ + circuit breaker** vit dans `gispulse-enterprise` (issue #417 marquée experimental sur OSS).

### 4. Cascade depth ≤ 3

Pour éviter les boucles infinies (trigger A → write → trigger A → …), la profondeur de cascade est plafonnée à 3 niveaux dans `TriggerEvaluator`. Au-delà, l'évaluation lève `CascadeDepthExceeded` et abandonne la branche. Suffisant pour la plupart des workflows ; ESB tier permet 10+ niveaux avec instrumentation.

### 5. AST de prédicat interprété (pas pré-compilé)

`PredicateEvaluator` parcourt l'arbre des prédicats à chaque évaluation. Le coût est marginal pour <100 triggers actifs ; au-delà, la pré-compilation (Pro feature) divise le temps d'éval par ~3-5× sur hot path.

### 6. Filtre WebSocket post-broadcast

Le `EventHub` fait fan-out à tous les subscribers, et c'est le client WebSocket qui filtre via `topics=` / `trigger_id=` / `table=` dans son URL. Le serveur ne fait pas de routage sélectif. Bandwidth saving réel pour le client, gain CPU serveur marginal — un tier ESB v1.3+ ajoutera un routage server-side avec topic exchanges.

## Dépannage

| Symptôme | Cause probable | Remède |
|---|---|---|
| Webhook jamais reçu | `ActionDispatcher` non instancié dans le lifespan | Vérifier `change_log_watcher_started action_dispatcher_wired=true` dans les logs au boot |
| `webhook_4xx_no_retry` en boucle | URL cible rejette le payload (mauvais content-type côté receveur) | Vérifier que le serveur accepte `Content-Type: application/json` |
| `WebhookSecurityError: blocked` en dev local | URL cible résout vers RFC1918/loopback | Init `HttpWebhookClient(allow_private_ips=True)` (CI/dev uniquement) |
| Triggers s'évaluent mais aucune action ne s'exécute | Pas de bridge `ActionDispatcher` (avant #458) | Mettre à jour vers v1.2.0 ou plus récent |
| Latence > 1 s par tick | Webhook lent + dispatch inline | Réduire `timeout`/`max_retries` du `HttpWebhookClient` ou attendre v1.3 (background tasks) |

## Voir aussi

- **[Rules guide (canonical, FR)](../docs-site/guide/rules.md)** — JSON schema des rules, prédicats, actions, exemples
- **[Integration Matrix](INTEGRATION_MATRIX.md)** — quel client consomme quoi, par version
- **[REST API reference](../docs-site/api/rest.md)** — endpoints `/api/triggers`, `/api/rules`, `/ws/events`

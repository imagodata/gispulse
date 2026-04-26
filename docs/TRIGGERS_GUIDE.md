# Triggers Guide

GISPulse triggers react to DML events on tracked datasets and dispatch one or more actions (notify, set_field, webhook, run_sql, …). This page is the operator-oriented summary; the canonical reference for trigger / rule JSON shape lives in **[`docs-site/guide/rules.md`](../docs-site/guide/rules.md)** (rendered at <https://imagodata.github.io/gispulse/guide/rules>).

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

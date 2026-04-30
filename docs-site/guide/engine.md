---
title: Lancer le moteur
description: Faire tourner le moteur GISPulse en headless (API REST + WebSocket, sans SPA) avec `gispulse engine`. Cas d'usage serveur, sidecar Tauri, intégration tierce.
---

# Lancer le moteur (`gispulse engine`)

Le moteur GISPulse est un serveur FastAPI qui expose les pipelines de règles, les triggers ESB, les datasets et les WebSockets d'évènements. La commande `gispulse engine` le lance **en mode headless** — pas de SPA monté, juste l'API.

> **Quand utiliser `gispulse engine` plutôt que `gispulse portal` ?** Quand vous n'avez pas besoin du workbench visuel : pipelines automatisés, sidecar de l'app desktop Tauri, backend pour un portail hébergé séparément, intégration tierce qui consomme l'API REST. Pour un workflow d'édition visuelle sur poste, utilisez plutôt [`gispulse portal`](/guide/portal-local).

## Commande

```bash
gispulse engine [OPTIONS]
```

| Option | Défaut | Description |
|--------|--------|-------------|
| `--port`, `-p` | `0` (auto) | Port d'écoute. `0` = port libre auto-détecté (utile pour sidecar Tauri). |
| `--host` | `127.0.0.1` | Hôte d'écoute. Mettre `0.0.0.0` pour exposer en LAN ou derrière un reverse proxy. |
| `--engine`, `-e` | `duckdb` | Backend spatial : `duckdb` (local), `postgis` (serveur), `hybrid` (Pro, mix des deux). |
| `--data-dir`, `-d` | `~/.gispulse/data` | Répertoire des datasets uploadés. |
| `--no-browser` | `false` | Ne pas ouvrir le navigateur sur la racine de l'API. |

### Démarrage et mode "ready"

Au boot, `gispulse engine` émet une ligne JSON sur stdout pour permettre à un parent process (Tauri, supervisor, script) de récupérer port et PID :

```
GISPULSE_READY:{"port": 8001, "host": "127.0.0.1", "engine": "duckdb", "pid": 12345}
GISPulse at http://127.0.0.1:8001
```

Le format `GISPULSE_READY:` est stable — vous pouvez parser cette ligne dans un sidecar.

## Surface API

Le moteur monte les routeurs FastAPI suivants en mode `full` (par défaut). Référence complète : [REST API](/api/rest) et `/docs` Swagger UI sur l'instance live.

| Endpoint | Méthode | Description |
|---|---|---|
| `/health` | GET | Healthcheck — renvoie `{"status": "ok"}`. Utilisé par le healthcheck de `gispulse portal`. |
| `/metrics` | GET | Métriques Prometheus (text format). Activé en mode `full`. |
| `/datasets` | GET / POST / DELETE | Gestion des datasets (upload, list, delete). |
| `/projects` | GET / POST | Workspaces multi-rules. |
| `/scenarios` | GET / POST | Scénarios = projet + ruleset versionné. |
| `/rules` | CRUD | Règles (capabilities + paramètres). |
| `/triggers` | CRUD | Triggers ESB (DML watchers, webhooks). |
| `/relations` | CRUD | Relations spatiales / attributaires entre layers. |
| `/jobs` | GET | Jobs asynchrones (statut, résultat). |
| `/capabilities` | GET | Liste les capabilities disponibles + schémas de paramètres. |
| `/marketplace` | GET | Catalogue de capabilities tierces (Pro). |
| `/examples` | GET | Mode 2 portail Try-it — datasets de démo embarqués. |
| `/styles` | GET / PUT | QML / SLD / breaks de classification. |
| `/schedules` | CRUD | Pipelines cron (Pro). |
| `/pipelines` | POST | Exécution d'un pipeline. |
| `/sessions` | POST | Sessions DuckDB éphémères. |
| `/ws/events` | WebSocket | Stream live des évènements ESB. |

::: tip Documentation OpenAPI live
Une fois le moteur démarré, ouvrez `http://localhost:8001/docs` (Swagger UI) ou `http://localhost:8001/redoc` (ReDoc) pour explorer la surface API exacte de la version installée.
:::

## Authentification

| Tier | Authentification | Notes |
|---|---|---|
| **Community** | Aucune (localhost only par défaut) | OK pour usage personnel et CI. |
| **Pro / Enterprise** | API key (header `X-API-Key`) ou OIDC | Activée en mode `full` quand `GISPULSE_REQUIRE_AUTH=1`. |

Pour un déploiement serveur (host `0.0.0.0`), **toujours** activer l'auth :

```bash
GISPULSE_TIER=pro \
GISPULSE_REQUIRE_AUTH=1 \
GISPULSE_API_KEYS="key1,key2" \
gispulse engine --host 0.0.0.0 --port 8001
```

## Backend `duckdb` vs `postgis`

| Flag | Backend | Cas d'usage |
|---|---|---|
| `--engine duckdb` | DuckDB local + GPKG/GeoParquet | Mode portable, pas de DB externe, < ~10M features. |
| `--engine postgis` | PostgreSQL/PostGIS distant | Persistance, triggers `pg_notify`, multi-user. Nécessite `GISPULSE_DSN`. |
| `--engine hybrid` | DuckDB calcul + PostGIS stockage (Pro) | Volumes hétérogènes, perf maximale. |

Détails dans [Moteurs DuckDB / PostGIS / Hybrid](/guide/engines).

```bash
# Mode PostGIS sur un Postgres en Docker
GISPULSE_DSN=postgresql://gispulse:secret@localhost:5432/gispulse \
gispulse engine --engine postgis --port 8001
```

## Reverse proxy (Caddy / nginx)

Pour exposer le moteur en HTTPS sur un VPS, on le bind sur `127.0.0.1:8001` et on délègue le TLS au reverse proxy.

### Caddy (auto-TLS Let's Encrypt)

```caddy
api.example.com {
    reverse_proxy 127.0.0.1:8001
    encode gzip zstd

    # WebSocket (events bus) — Caddy gère ça nativement
    @ws {
        path /ws/*
    }
    reverse_proxy @ws 127.0.0.1:8001
}
```

### nginx (TLS manuel)

```nginx
server {
    listen 443 ssl http2;
    server_name api.example.com;

    ssl_certificate     /etc/letsencrypt/live/api.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/api.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8001;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-Proto https;

        # WebSocket upgrade
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 3600s;
    }
}
```

Voir [Déploiement](/guide/deployment) pour la stack complète (PostGIS + Caddy + Prometheus + backups).

## Cohabitation avec `gispulse portal`

Les deux commandes lancent le même `create_app()` FastAPI. La seule différence :

| Commande | Mount SPA | Auth par défaut | Cas d'usage |
|---|---|---|---|
| `gispulse engine` | non | activable via env | serveur, sidecar Tauri, intégration tierce |
| `gispulse portal` | oui (`/portal`) | désactivée (localhost) | poste de travail, onboarding visuel |

Vous pouvez tout à fait :

1. Lancer `gispulse engine --host 0.0.0.0` sur un VPS.
2. Lancer `gispulse portal --backend=https://your.engine.example.com` sur votre poste.

Le portail GH Pages se connecte alors au moteur distant — voir [Lancer le portail localement](/guide/portal-local#mode-remote-backend-url).

## Voir aussi

- [`gispulse portal`](/guide/portal-local) — version avec SPA bundlé.
- [CLI Référence](/guide/cli) — toutes les commandes.
- [Moteurs DuckDB / PostGIS / Hybrid](/guide/engines) — choisir le bon backend.
- [Déploiement](/guide/deployment) — production VPS, Docker Compose, Prometheus.
- [REST API](/api/rest) — référence complète des endpoints.

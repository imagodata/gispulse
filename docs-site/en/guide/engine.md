---
title: Running the engine
description: Run the GISPulse engine headlessly (REST API + WebSocket, no SPA) via `gispulse engine`. Server, Tauri sidecar, third-party integration use cases.
---

# Running the engine (`gispulse engine`)

The GISPulse engine is a FastAPI server that exposes rule pipelines, ESB triggers, datasets, and an event-bus WebSocket. The `gispulse engine` command runs it **headlessly** — no SPA mounted, just the API.

> **When should I use `gispulse engine` instead of `gispulse portal`?** When you don't need the visual workbench: automated pipelines, the Tauri desktop sidecar, a backend for a separately-hosted portal, third-party integrations consuming the REST API. For local visual editing on a workstation, use [`gispulse portal`](/en/guide/portal-local).

## Command

```bash
gispulse engine [OPTIONS]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--port`, `-p` | `0` (auto) | Listening port. `0` = auto-detect a free port (handy for the Tauri sidecar). |
| `--host` | `127.0.0.1` | Bind host. Use `0.0.0.0` to expose on the LAN or behind a reverse proxy. |
| `--engine`, `-e` | `duckdb` | Spatial backend: `duckdb` (local), `postgis` (server), `hybrid` (Pro, mix of both). |
| `--data-dir`, `-d` | `~/.gispulse/data` | Datasets upload directory. |
| `--no-browser` | `false` | Don't open the browser on the API root. |

### Boot and "ready" mode

On boot, `gispulse engine` emits a JSON line on stdout so a parent process (Tauri, supervisor, script) can read the port and PID:

```
GISPULSE_READY:{"port": 8001, "host": "127.0.0.1", "engine": "duckdb", "pid": 12345}
GISPulse at http://127.0.0.1:8001
```

The `GISPULSE_READY:` prefix is stable — you can parse this line in a sidecar.

## API surface

In `full` mode (the default), the engine mounts the following FastAPI routers. Full reference: [REST API](/en/api/rest) and the live `/docs` Swagger UI.

| Endpoint | Method | Description |
|---|---|---|
| `/health` | GET | Healthcheck — returns `{"status": "ok"}`. Used by the `gispulse portal` healthcheck. |
| `/metrics` | GET | Prometheus metrics (text format). Enabled in `full` mode. |
| `/datasets` | GET / POST / DELETE | Dataset management (upload, list, delete). |
| `/projects` | GET / POST | Multi-rule workspaces. |
| `/scenarios` | GET / POST | Scenario = project + versioned ruleset. |
| `/rules` | CRUD | Rules (capabilities + parameters). |
| `/triggers` | CRUD | ESB triggers (DML watchers, webhooks). |
| `/relations` | CRUD | Spatial / attribute relations between layers. |
| `/jobs` | GET | Async jobs (status, result). |
| `/capabilities` | GET | List available capabilities + parameter schemas. |
| `/marketplace` | GET | Third-party capabilities catalog (Pro). |
| `/examples` | GET | Mode 2 portal Try-it — embedded demo datasets. |
| `/styles` | GET / PUT | QML / SLD / classification breaks. |
| `/schedules` | CRUD | Cron pipelines (Pro). |
| `/pipelines` | POST | Pipeline execution. |
| `/sessions` | POST | Ephemeral DuckDB sessions. |
| `/ws/events` | WebSocket | Live ESB event stream. |

::: tip Live OpenAPI documentation
Once the engine is running, open `http://localhost:8001/docs` (Swagger UI) or `http://localhost:8001/redoc` (ReDoc) to explore the exact API surface of your installed version.
:::

## Authentication

| Tier | Authentication | Notes |
|---|---|---|
| **Community** | None (localhost only by default) | Fine for personal use and CI. |
| **Pro / Enterprise** | API key (`X-API-Key` header) or OIDC | Enabled in `full` mode when `GISPULSE_REQUIRE_AUTH=1`. |

For a server deployment (host `0.0.0.0`), **always** enable auth:

```bash
GISPULSE_TIER=pro \
GISPULSE_REQUIRE_AUTH=1 \
GISPULSE_API_KEYS="key1,key2" \
gispulse engine --host 0.0.0.0 --port 8001
```

## Backend `duckdb` vs `postgis`

| Flag | Backend | Use case |
|---|---|---|
| `--engine duckdb` | DuckDB local + GPKG/GeoParquet | Portable mode, no external DB, < ~10M features. |
| `--engine postgis` | PostgreSQL/PostGIS server | Persistence, `pg_notify` triggers, multi-user. Requires `GISPULSE_DSN`. |
| `--engine hybrid` | DuckDB compute + PostGIS storage (Pro) | Heterogeneous volumes, max throughput. |

Details in [Engines — DuckDB / PostGIS / Hybrid](/en/guide/engines).

```bash
# PostGIS mode against a Postgres in Docker
GISPULSE_DSN=postgresql://gispulse:secret@localhost:5432/gispulse \
gispulse engine --engine postgis --port 8001
```

## Reverse proxy (Caddy / nginx)

To expose the engine over HTTPS on a VPS, bind it to `127.0.0.1:8001` and let the reverse proxy terminate TLS.

### Caddy (auto-TLS via Let's Encrypt)

```caddy
api.example.com {
    reverse_proxy 127.0.0.1:8001
    encode gzip zstd

    # WebSocket (event bus) — Caddy handles this natively
    @ws {
        path /ws/*
    }
    reverse_proxy @ws 127.0.0.1:8001
}
```

### nginx (manual TLS)

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

See [Deployment](/en/guide/deployment) for the full stack (PostGIS + Caddy + Prometheus + backups).

## Cohabitation with `gispulse portal`

Both commands launch the same `create_app()` FastAPI app. The only difference:

| Command | Mounts SPA | Default auth | Use case |
|---|---|---|---|
| `gispulse engine` | no | env-controlled | server, Tauri sidecar, third-party integration |
| `gispulse portal` | yes (`/portal`) | disabled (localhost) | workstation, visual onboarding |

You can absolutely:

1. Run `gispulse engine --host 0.0.0.0` on a VPS.
2. Run `gispulse portal --backend=https://your.engine.example.com` on your laptop.

The GH-Pages portal then connects to the remote engine — see [Running the Portal locally](/en/guide/portal-local#remote-mode-backend-url).

## See also

- [`gispulse portal`](/en/guide/portal-local) — same engine plus the bundled SPA.
- [CLI Reference](/en/guide/cli) — all commands.
- [Engines — DuckDB / PostGIS / Hybrid](/en/guide/engines) — pick the right backend.
- [Deployment](/en/guide/deployment) — production VPS, Docker Compose, Prometheus.
- [REST API](/en/api/rest) — full endpoint reference.

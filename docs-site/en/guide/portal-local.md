---
title: Running the Portal locally
description: Step-by-step guide for running the GISPulse Portal workbench on your machine via `gispulse portal` — no HTTPS, no CORS, no GIS plugin required.
---

# Running the Portal locally

The **GISPulse Portal** is a visual workbench (node canvas, capability registry, dataset manager) served by the GISPulse engine. This page documents the `gispulse portal` command, which mounts the bundled SPA on your local engine and opens the browser.

> **Product axiom.** The CLI and the Portal are two equivalent UIs over the same source of truth (`triggers.yaml` + change-log). Anything you configure in the Portal can be edited via the CLI, and vice versa.

## Quick start (30 seconds)

```bash
# 1. Install the CLI plus the bundled SPA
pipx install gispulse-portal

# 2. Start the engine and open the browser at localhost:8001/portal
gispulse portal
```

That's it. The browser opens at `http://127.0.0.1:8001/portal/`, you edit your triggers, and `Ctrl+C` stops the server.

::: tip Why two packages?
`gispulse-portal` is a separate PyPI wheel that ships the built SPA (`dist/`). It depends on `gispulse`, so `pipx install gispulse-portal` installs both in one go. This split keeps `gispulse` lean (~3 MB) for CLI-only / CI users. See the [architecture note](/en/guide/architecture) for details.
:::

## The `gispulse portal` command

```bash
gispulse portal [OPTIONS]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--port`, `-p` | `8001` | Local engine listening port. |
| `--host` | `127.0.0.1` | Bind host. Keep `127.0.0.1` unless you need LAN access. |
| `--data-dir`, `-d` | `~/.gispulse/data` | Directory for datasets uploaded via the portal. |
| `--no-browser` | `false` | Don't open the browser (useful over SSH / headless). |
| `--backend URL` | — | Remote mode: open the GH-Pages portal pointed at a remote engine, **does not start** a local engine. |
| `--dev` | `false` | Allow falling back to `portal/dist/` from a local checkout (contributor workflow). |

### Behavior

1. **SPA bundle resolution.** The command looks up `gispulse_portal.PORTAL_DIST_PATH`. If absent and `--dev` is set, falls back to `<repo>/portal/dist/`.
2. **Same-origin mount.** The SPA is served by FastAPI under `/portal` via `StaticFiles`. The engine (REST API + WebSocket) lives on the root. No mixed-content, no CORS — everything goes through `localhost:8001`.
3. **Healthcheck plus browser open.** A daemon thread polls `GET /health` every 100 ms for 3 s, then calls `webbrowser.open()` on the portal URL. If the healthcheck times out, we open anyway — worst case you see the loader.
4. **Uvicorn run.** The engine runs in the foreground. `Ctrl+C` shuts down cleanly.

### Examples

```bash
# Custom port (port 8001 conflict)
gispulse portal --port 9000

# No browser (SSH deployment, container, CI)
gispulse portal --no-browser

# Project-local datasets directory
gispulse portal --data-dir ./my-project/data

# Contributor workflow (from a repo checkout)
gispulse portal --dev
```

## Without the `gispulse-portal` package

If you only installed `gispulse` (no SPA bundle), the command fails cleanly with an install hint:

```
$ gispulse portal
Error: gispulse-portal package is not installed.
Install it with:
  pip install gispulse-portal
Or, for a remote workbench without a local install:
  gispulse portal --backend=https://your-engine.example.com
```

This is intentional: CLI-only users (CI/CD, headless servers, terminal-first power users) don't pay the SPA cost. If you only need REST API + WebSocket without UI, use [`gispulse engine`](/en/guide/engine) instead.

## Remote mode: `--backend URL`

Instead of a local engine, you can point the GH-Pages portal (served over HTTPS) at **an engine deployed elsewhere**:

```bash
gispulse portal --backend=https://your.engine.example.com
```

The command URL-encodes the parameter and opens:

```
https://gispulse.dev/?backend=https%3A%2F%2Fyour.engine.example.com
```

**Use cases:**

- You have an engine running on a VPS (see [Deployment](/en/guide/deployment)) and want to drive it from your laptop.
- You're evaluating the hosted SaaS edition (v1.6+, `pro.gispulse.dev`).
- You're demoing the portal to a client without installing Python on their machine.

::: warning Mixed-content
The GH-Pages portal is served over HTTPS. The `backend` URL must also be HTTPS — `http://localhost:8001` won't work (the browser blocks it). For a local engine, use the default mode (no `--backend`), which serves the SPA same-origin and side-steps the issue.
:::

## Troubleshooting

### Port conflict (`EADDRINUSE`)

Port `8001` is already in use (for example, a previous engine that wasn't killed). Diagnose:

```bash
# Linux / macOS
lsof -i :8001

# Windows
netstat -ano | findstr :8001
```

Fix: `gispulse portal --port 9000`.

### Firewall (Windows / corporate)

On first launch, Windows Defender may prompt for permission for `python.exe` or `uvicorn`. Approve **Private networks** only — no need to expose the port publicly.

### Healthcheck timeout (~3 s)

If the engine takes more than 3 s to start (large datasets, slow GDAL, cold container), the browser opens anyway on a not-yet-ready endpoint. Refresh once. For consistently slow starts, open manually after a few seconds:

```bash
gispulse portal --no-browser
# wait until the engine logs "Application startup complete"
# then open http://127.0.0.1:8001/portal/ manually
```

### Browser doesn't open at all

`webbrowser.open()` fails silently in some environments (WSL without `wslview`, Docker containers, broken SSH X-forward). The URL is printed on stdout:

```
GISPulse Portal at http://127.0.0.1:8001/portal/
```

Copy-paste the URL into your browser.

### Managing the engine process

`gispulse portal` runs in the foreground. To run it in the background:

```bash
# Linux / macOS
gispulse portal --no-browser &
echo $!  # PID

# tmux / screen
tmux new -d -s gispulse 'gispulse portal --no-browser'

# systemd user unit (persistent local production)
# see /en/guide/deployment
```

## See also

- [`gispulse engine`](/en/guide/engine) — engine without the SPA (REST API + WebSocket only).
- [CLI Reference](/en/guide/cli) — all commands.
- [Architecture](/en/guide/architecture) — why two PyPI packages.
- [Deployment](/en/guide/deployment) — multi-user production (PostGIS, Caddy, Docker).

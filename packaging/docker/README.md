# Docker images for `gispulse watch`

Deployment templates for the v1.3.0 `gispulse watch` daemon.

## Files

| File | Purpose |
|---|---|
| `Dockerfile.watch` | Slim image (< 200 MB target) — Python + gispulse wheel + tini, no portal/viewer/GDAL |
| `docker-compose.watch.yml` | Single-service compose example with bind-mount, healthcheck, restart policy |

## Why a separate image

The repo's top-level `Dockerfile` builds the full stack: portal frontend, viewer frontend, GDAL, FastAPI server, multi-Gigabyte. That's the right image for hosting the API server and the playground UI.

For `gispulse watch`, you want the opposite: minimal surface area, zero web stack, fast cold start, easy to deploy in cron / Lambda / k8s. Hence `Dockerfile.watch`.

| Image | Size | Use when |
|---|---|---|
| `Dockerfile` (top-level) | ~1.5 GB | Hosting the FastAPI server + portal/viewer UI |
| `Dockerfile.watch` (here) | < 200 MB | Long-running daemon that fires rules on GPKG edits |

## Build

```bash
# From local source (default — for dev / CI)
docker build \
    -f packaging/docker/Dockerfile.watch \
    -t gispulse/watch:dev \
    .

# Pinned to a published PyPI version (for production)
docker build \
    -f packaging/docker/Dockerfile.watch \
    --build-arg GISPULSE_VERSION=1.3.0 \
    -t gispulse/watch:1.3.0 \
    .
```

## Run — single container

```bash
mkdir -p ./data
cp /path/to/your.gpkg     ./data/parcels.gpkg
cp /path/to/your.rules.yaml ./data/parcels.rules.yaml

# One-shot install of change-tracking on the layer(s)
docker run --rm -v ./data:/data --entrypoint gispulse \
    gispulse/watch:1.3.0 \
    track install /data/parcels.gpkg --all-layers

# Long-running watcher
docker run -d --name gispulse-watch \
    -v ./data:/data \
    --restart on-failure:5 \
    gispulse/watch:1.3.0 \
    /data/parcels.gpkg --rules /data/parcels.rules.yaml \
    --bulk-threshold 1000

# Inspect
docker logs -f gispulse-watch

# Stop (drains in-flight rows before exit, ≤ 2 s)
docker stop gispulse-watch
```

## Run — docker compose

```bash
cd packaging/docker
mkdir -p ./data
# edit data/parcels.rules.yaml + drop parcels.gpkg
docker compose -f docker-compose.watch.yml up -d
docker compose -f docker-compose.watch.yml logs -f
docker compose -f docker-compose.watch.yml down
```

## Run — `--once` (cron / Lambda)

The same image works for one-shot drains:

```bash
docker run --rm -v ./data:/data \
    gispulse/watch:1.3.0 \
    /data/parcels.gpkg --rules /data/parcels.rules.yaml \
    --once --exit-zero-if-empty
```

Wire into a cron entry on the host (or k8s CronJob spec):

```cron
*/5 * * * * docker run --rm -v /var/lib/gispulse:/data gispulse/watch:1.3.0 /data/parcels.gpkg --rules /data/parcels.rules.yaml --once --exit-zero-if-empty
```

## Signal handling

`docker stop` sends SIGTERM. tini (PID 1 in the image) forwards it to the
`gispulse watch` process, which catches it and runs the graceful-drain
path (`runtime.stop()` 2-s join + `runtime.close()` → exit 0). If the
watcher does not exit within `stop_grace_period` (10 s in the compose
file), Docker sends SIGKILL — that's fine, in-flight rows stay
`processed=0` and the next start picks them up idempotently.

## Multi-architecture

To publish multi-arch images (linux/amd64 + linux/arm64) for Apple
Silicon and Graviton hosts:

```bash
docker buildx create --use --name gispulse-builder
docker buildx build \
    -f packaging/docker/Dockerfile.watch \
    --platform linux/amd64,linux/arm64 \
    --build-arg GISPULSE_VERSION=1.3.0 \
    -t ghcr.io/imagodata/gispulse-watch:1.3.0 \
    --push \
    .
```

## See also

- `gispulse watch --help`
- [`packaging/systemd/README.md`](../systemd/README.md) — systemd alternative
- [TRIGGERS_GUIDE.md](../../docs/TRIGGERS_GUIDE.md)

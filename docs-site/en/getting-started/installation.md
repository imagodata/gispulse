---
title: Installation
description: Install GISPulse via pip, Docker, precompiled binary, or desktop application.
---

# Installation

GISPulse can be deployed in four ways depending on your context. Choose the mode that fits your use case.

## Mode 1 — pip (local, recommended for developers)

Requires Python 3.10+ and GDAL installed on the system.

::: warning Linux: install `mod_spatialite` for tracked GeoPackages
GISPulse installs SQLite triggers that call SpatiaLite functions (`ST_IsEmpty`, etc.) to detect geometry changes on tracked layers. On Linux, the loadable extension `mod_spatialite.so` is **not** included with `libspatialite` and must be installed separately, otherwise any DML on a tracked layer fails with `sqlite3.OperationalError: no such function: ST_IsEmpty`.

```bash
# Debian / Ubuntu
sudo apt install libsqlite3-mod-spatialite

# Fedora / RHEL
sudo dnf install libspatialite-devel

# Alpine
sudo apk add libspatialite-dev
```

macOS Homebrew (`spatialite-tools`), QGIS, FME and OSGeo4W ship `mod_spatialite` already — no extra step. `gispulse doctor` verifies the extension is loadable.
:::

```bash
# Community (free) — DuckDB + CLI
pip install gispulse

# Pro (all Pro features in one command)
pip install gispulse-pro

# Or install individual extras:
pip install "gispulse[postgis]"    # PostGIS engine
pip install "gispulse[api]"        # Embedded REST API
pip install "gispulse[raster]"     # Raster support (rasterio)
pip install "gispulse[all]"        # Everything included
```

::: tip Pro vs extras
`pip install gispulse-pro` is a shortcut for `pip install "gispulse[postgis,api,redis,s3,scheduling]"`. It installs all dependencies required for Pro features (PostGIS, S3, Redis, scheduling). Pro features additionally require `GISPULSE_TIER=pro` and `GISPULSE_LICENSE_KEY` to be activated.
:::

Verify the installation:

```bash
gispulse doctor
```

Expected output:

```
✓ GISPulse    v2.0.0
✓ Python      v3.12.x
✓ GDAL        v3.x.x
✓ DuckDB      v1.x.x + spatial OK
✓ GeoPandas   v1.x.x
```

## Mode 2 — Docker

No system dependencies required. Ideal for reproducible deployments or server environments.

```bash
# Official image (Community)
docker pull ghcr.io/gispulse/gispulse:latest

# Run a job from a local GPKG
docker run --rm \
  -v $(pwd)/data:/data \
  ghcr.io/gispulse/gispulse:latest \
  run /data/input.gpkg --rules /data/rules.json -o /data/output.gpkg
```

### Docker Compose with PostGIS

```yaml
# docker-compose.yml
services:
  gispulse:
    image: ghcr.io/gispulse/gispulse:latest
    ports:
      - "8001:8001"
    environment:
      - GISPULSE_DSN=postgresql://gispulse:secret@postgis:5432/gispulse
    command: portal --host 0.0.0.0
    depends_on:
      - postgis

  postgis:
    image: postgis/postgis:16-3.4
    environment:
      POSTGRES_USER: gispulse
      POSTGRES_PASSWORD: secret
      POSTGRES_DB: gispulse
    volumes:
      - pgdata:/var/lib/postgresql/data

volumes:
  pgdata:
```

```bash
docker compose up -d
# Portal available at http://localhost:8001
```

## Mode 3 — Precompiled binary (PyInstaller)

Distributed via GitHub releases. No Python required on the target machine.

```bash
# Linux x86_64
curl -L https://github.com/gispulse/gispulse/releases/latest/download/gispulse-linux-x86_64 \
  -o /usr/local/bin/gispulse
chmod +x /usr/local/bin/gispulse

# macOS arm64
curl -L https://github.com/gispulse/gispulse/releases/latest/download/gispulse-macos-arm64 \
  -o /usr/local/bin/gispulse
chmod +x /usr/local/bin/gispulse

gispulse --version
```

::: info Automatic updates
The binary includes a `gispulse update` command to check for and apply updates.
:::

## Mode 4 — Desktop Application (Tauri)

Standalone React + MapLibre GL JS application for non-technical users. Downloadable from GitHub releases (.dmg, .exe, .AppImage).

| Platform | Format |
|----------|--------|
| macOS    | .dmg   |
| Windows  | .exe (NSIS) |
| Linux    | .AppImage |

The desktop application embeds the GISPulse engine and does not require a Python installation.

## Initial setup

After installation, initialize a project:

```bash
mkdir my-project && cd my-project
gispulse init --name "my-project"
```

This creates:

```
my-project/
├── rules/
│   └── rules.json    # template rules to edit
├── data/             # put your files here
├── output/           # results
└── Makefile          # shortcuts: make run / make validate
```

See [Configuration](/getting-started/configuration) for environment variables and PostGIS connection setup.

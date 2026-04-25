---
title: Installation
description: Installer GISPulse en mode pip, Docker, binaire précompilé ou application desktop.
---

# Installation

GISPulse se déploie de quatre façons selon votre contexte. Choisissez le mode adapté à votre usage.

## Mode 1 — pip (local, recommandé pour développeurs)

Requiert Python 3.10+ et GDAL installé sur le système.

```bash
# Community (gratuit) — DuckDB + CLI
pip install gispulse

# Pro (toutes les features Pro en une commande)
pip install gispulse-pro

# Ou installez des extras individuellement :
pip install "gispulse[postgis]"    # PostGIS engine
pip install "gispulse[api]"        # API REST embarquée
pip install "gispulse[raster]"     # support raster (rasterio)
pip install "gispulse[all]"        # tout inclus
```

::: tip Pro vs extras
`pip install gispulse-pro` est un raccourci pour `pip install "gispulse[postgis,api,redis,s3,scheduling]"`. Il installe toutes les dependances necessaires aux features Pro (PostGIS, S3, Redis, scheduling). Les features Pro requierent en plus `GISPULSE_TIER=pro` et `GISPULSE_LICENSE_KEY` pour etre activees.
:::

Vérifiez l'installation :

```bash
gispulse doctor
```

Sortie attendue :

```
✓ GISPulse    v1.1.1
✓ Python      v3.12.x
✓ GDAL        v3.x.x
✓ DuckDB      v1.x.x + spatial OK
✓ GeoPandas   v1.x.x
```

## Mode 2 — Docker

Aucune dépendance système requise. Idéal pour un déploiement reproductible ou sur serveur.

```bash
# Image officielle (Community)
docker pull ghcr.io/gispulse/gispulse:latest

# Lancer un job depuis un GPKG local
docker run --rm \
  -v $(pwd)/data:/data \
  ghcr.io/gispulse/gispulse:latest \
  run /data/input.gpkg --rules /data/rules.json -o /data/output.gpkg
```

### Docker Compose avec PostGIS

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
# Portal accessible sur http://localhost:8001
```

## Mode 3 — Binaire précompilé (PyInstaller)

Distribué via les releases GitHub. Aucun Python requis sur la machine cible.

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

::: info Mise à jour automatique
Le binaire intègre une commande `gispulse update` pour vérifier et appliquer les mises à jour.
:::

## Mode 4 — Application Desktop (Tauri)

Application standalone React + MapLibre GL JS pour utilisateurs non-techniques. Téléchargeable depuis les releases GitHub (.dmg, .exe, .AppImage).

| Plateforme | Format |
|------------|--------|
| macOS      | .dmg   |
| Windows    | .exe (NSIS) |
| Linux      | .AppImage |

L'application desktop embarque le moteur GISPulse et ne nécessite pas d'installation Python.

## Configuration initiale

Après installation, initialisez un projet :

```bash
mkdir mon-projet && cd mon-projet
gispulse init --name "mon-projet"
```

Cela crée :

```
mon-projet/
├── rules/
│   └── rules.json    # règles template à éditer
├── data/             # mettez vos fichiers ici
├── output/           # résultats
└── Makefile          # raccourcis make run / make validate
```

Voir [Configuration](/getting-started/configuration) pour les variables d'environnement et la connexion PostGIS.

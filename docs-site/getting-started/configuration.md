---
title: Configuration
description: Fichier TOML, variables d'environnement, profils, Pydantic Settings, tiers Community/Pro et configuration avancee.
---

# Configuration

GISPulse se configure via trois mecanismes avec une chaine de precedence claire :

```
env vars  >  gispulse.{profil}.toml  >  gispulse.toml  >  defaults
```

::: tip Pydantic Settings (v1.0.2+)
Toute la configuration est centralisee dans un module Pydantic Settings unique (`core/config.py`). En Python :
```python
from core.config import settings

settings.engine.backend   # "gpkg"
settings.api.env          # "development"
settings.redis.url        # ""
```
:::

## Fichier TOML (recommande)

Creez un fichier `gispulse.toml` a la racine de votre projet :

```toml
[engine]
backend = "duckdb"              # duckdb | postgis | hybrid | gpkg
tier = "community"              # community | pro | enterprise

[database]
# dsn = "postgresql://gispulse:secret@localhost:5432/gispulse"
gpkg_path = "project.gpkg"

[storage]
mode = "sqlite"                 # sqlite | memory
data_dir = "~/.gispulse/data"

[api]
env = "development"             # development | production
cors_origins = ""               # origines CORS (virgule-separees)
max_upload_mb = 500

[logging]
level = "INFO"                  # DEBUG | INFO | WARNING | ERROR
format = "console"              # console | json

[session]
expiry = 28800                  # 8 heures

[jobs]
timeout = 3600
duckdb_threshold = 100000
```

Le fichier est cherche dans cet ordre :
1. `GISPULSE_CONFIG` (chemin explicite)
2. `./gispulse.toml` (repertoire courant)
3. `~/.gispulse/gispulse.toml` (niveau utilisateur)

::: info Secrets
Les secrets (cles API, DSN, client_secret OIDC) doivent rester en variables d'environnement, pas dans le fichier TOML.
:::

## Profils

Definissez `GISPULSE_PROFILE` pour charger un fichier de surcharge :

```bash
GISPULSE_PROFILE=prod
```

Cela charge `gispulse.prod.toml` en plus du fichier de base. Le profil ecrase les valeurs de la base (deep merge). Les variables d'environnement ecrasent tout.

Exemple `gispulse.prod.toml` :

```toml
[engine]
tier = "pro"

[logging]
level = "WARNING"
format = "json"

[api]
env = "production"
cors_origins = "https://gispulse.example.com"
```

## Schema de validation des pipelines

Les fichiers de regles (v1) et pipelines (v2) sont valides automatiquement au chargement via JSON Schema. Deux formats sont supportes :

**Format v1** (liste plate de regles) :
```json
[
  {"name": "buffer_50m", "capability": "buffer", "config": {"distance": 50}}
]
```

**Format v2** (pipeline DAG avec triggers) :
```json
{
  "version": 2,
  "name": "enrich_parcels",
  "steps": [
    {"id": "filter", "type": "capability", "capability": "filter",
     "params": {"expression": "area > 1000"}},
    {"id": "buffer", "capability": "buffer", "params": {"distance": 50},
     "input": "filter"}
  ],
  "triggers": [
    {"on": "dml:parcelles:INSERT", "then": "run_pipeline"}
  ]
}
```

La validation est activee par defaut dans `load_pipeline()` et `load_rules()`. Pour la desactiver : `load_pipeline(path, validate=False)`.

## Variables d'environnement

### Moteur et stockage

| Variable | Défaut | Description |
|----------|--------|-------------|
| `GISPULSE_ENGINE` | `gpkg` | Moteur d'exécution : `gpkg`, `duckdb`, `postgis`, `hybrid` |
| `GISPULSE_TIER` | `community` | Tier : `community`, `pro`, `enterprise` |
| `GISPULSE_LICENSE_KEY` | — | Clé de licence (requis pour pro/enterprise) |
| `GISPULSE_STORAGE` | `sqlite` | Stockage métadonnées : `sqlite`, `memory` |
| `GISPULSE_DB_PATH` | `~/.gispulse/gispulse.db` | Chemin de la base SQLite de métadonnées |
| `GISPULSE_DATA_DIR` | `~/.gispulse/data` | Répertoire de stockage des fichiers |
| `GISPULSE_ENV` | `development` | Environnement : `development`, `production` |

### Base de données

| Variable | Défaut | Description |
|----------|--------|-------------|
| `GISPULSE_DSN` | — | DSN PostgreSQL/PostGIS (mode postgis/hybrid) |
| `GISPULSE_DATABASE_URL` | — | Alternative à `GISPULSE_DSN` |
| `GISPULSE_GPKG_PATH` | `project.gpkg` | Chemin GPKG par défaut (mode portable) |
| `GISPULSE_POSTGIS_DSN` | — | DSN spécifique pour les requêtes SQL du portal |
| `GISPULSE_BASE_DSN` | — | DSN de base pour les connexions multi-schémas |

Exemple `GISPULSE_DSN` :

```bash
GISPULSE_DSN=postgresql://gispulse:secret@localhost:5432/gispulse
```

### API et sécurité

| Variable | Défaut | Description |
|----------|--------|-------------|
| `GISPULSE_API_KEYS` | — | Clés API (virgule-séparées). Vide = auth désactivée |
| `GISPULSE_API_KEY` | — | Clé API unique (legacy, ajoutée à `API_KEYS`) |
| `GISPULSE_CORS_ORIGINS` | — | Origines CORS autorisées (virgule-séparées) |
| `GISPULSE_RBAC` | `false` | Activer RBAC (rôles et permissions) |
| `GISPULSE_MAX_UPLOAD_MB` | `500` | Taille max d'upload en Mo (cap 5 Go) |
| `GISPULSE_METRICS_TOKEN` | — | Token d'accès à l'endpoint `/metrics` |
| `GISPULSE_SQL_ADMIN_KEY` | — | Clé d'administration pour les requêtes SQL portal |

### OIDC / SSO (Enterprise)

| Variable | Défaut | Description |
|----------|--------|-------------|
| `GISPULSE_OIDC_ISSUER` | — | URL de l'émetteur OIDC |
| `GISPULSE_OIDC_CLIENT_ID` | — | Client ID OIDC |
| `GISPULSE_OIDC_CLIENT_SECRET` | — | Client secret OIDC |
| `GISPULSE_OIDC_REDIRECT_URI` | — | URI de callback |
| `GISPULSE_OIDC_SCOPES` | `openid,profile,email` | Scopes OIDC (virgule-séparées) |
| `GISPULSE_OIDC_DEFAULT_ROLE` | `editor` | Rôle par défaut des nouveaux utilisateurs SSO |

### Session

| Variable | Défaut | Description |
|----------|--------|-------------|
| `GISPULSE_SESSION_SECRET` | — | Secret de session JWT (requis en production avec OIDC) |
| `GISPULSE_SESSION_EXPIRY` | `28800` | Durée de session en secondes (8h par défaut) |

### Redis et rate limiting

| Variable | Défaut | Description |
|----------|--------|-------------|
| `GISPULSE_REDIS_URL` | — | Redis pour job queue, rate limiting, metering |
| `GISPULSE_RATE_LIMIT_STORAGE` | — | Backend rate limit (fallback : Redis URL, puis `memory://`) |

### S3 / MinIO (Pro)

| Variable | Défaut | Description |
|----------|--------|-------------|
| `GISPULSE_S3_ENDPOINT` | — | URL endpoint S3/MinIO |
| `GISPULSE_S3_BUCKET` | `gispulse` | Nom du bucket |
| `GISPULSE_S3_ACCESS_KEY` | — | Clé d'accès S3 |
| `GISPULSE_S3_SECRET_KEY` | — | Clé secrète S3 |
| `GISPULSE_S3_REGION` | `us-east-1` | Région S3 |

### Stripe (mode SaaS)

| Variable | Défaut | Description |
|----------|--------|-------------|
| `GISPULSE_STRIPE_API_KEY` | — | Clé API Stripe |
| `GISPULSE_STRIPE_WEBHOOK_SECRET` | — | Secret du webhook Stripe |
| `GISPULSE_STRIPE_PRICE_PRO_MONTHLY` | — | Price ID Stripe Pro mensuel |
| `GISPULSE_STRIPE_PRICE_PRO_ANNUAL` | — | Price ID Stripe Pro annuel |
| `GISPULSE_STRIPE_PRICE_TEAM_MONTHLY` | — | Price ID Stripe Team mensuel |
| `GISPULSE_STRIPE_PRICE_TEAM_ANNUAL` | — | Price ID Stripe Team annuel |

### Logging et observabilité

| Variable | Défaut | Description |
|----------|--------|-------------|
| `GISPULSE_LOG_LEVEL` | `INFO` | Niveau de log : `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `GISPULSE_LOG_FORMAT` | `console` | Format de log : `console`, `json` |
| `GISPULSE_AUDIT` | `false` | Activer les logs d'audit (Pro) |
| `GISPULSE_AUDIT_RETENTION_DAYS` | `90` | Rétention des logs d'audit en jours |

### Jobs et exécution

| Variable | Défaut | Description |
|----------|--------|-------------|
| `GISPULSE_JOB_TIMEOUT` | `3600` | Timeout d'exécution des jobs en secondes |
| `GISPULSE_DUCKDB_THRESHOLD` | `100000` | Seuil de features pour basculer sur DuckDB |

### Télémétrie

| Variable | Défaut | Description |
|----------|--------|-------------|
| `GISPULSE_TELEMETRY` | — | `0` pour désactiver, `1` pour forcer |
| `GISPULSE_TELEMETRY_URL` | — | URL du collecteur de télémétrie |
| `GISPULSE_NO_UPDATE_CHECK` | — | `1` pour désactiver la vérification de mise à jour |

### Portal frontend

| Variable | Défaut | Description |
|----------|--------|-------------|
| `VITE_API_URL` | `http://localhost:8001` | URL API pour le frontend React |

## Fichier .env (alternative)

Si vous preferez les variables d'environnement au format `.env` :

```bash
# .env — GISPulse configuration
GISPULSE_ENGINE=gpkg
GISPULSE_STORAGE=sqlite
GISPULSE_GPKG_PATH=project.gpkg
GISPULSE_TIER=community

# PostGIS (optionnel, mode postgis/hybrid)
# GISPULSE_DSN=postgresql://gispulse:secret@localhost:5432/gispulse
```

::: warning Ne committez pas .env
Ajoutez `.env` a votre `.gitignore`. Il contient des secrets. Le fichier `gispulse.toml` peut etre committe car il ne doit pas contenir de secrets — utilisez les variables d'environnement pour les valeurs sensibles.
:::

## Tiers et fonctionnalités

### Community (gratuit, AGPL-3.0)

- Moteur GPKG/DuckDB complet
- CLI toutes commandes
- Portal single-user
- SDK Python
- Plugin QGIS
- Docker
- 104 capabilities Community : vecteur, attributs, overlay, classification & styling, statistiques spatiales, clustering, topologie, temporel, 3D pointcloud (LAS/LAZ)
- Datasets locaux illimités

### Pro (79 €/mois ou 790 €/an)

Toutes les features Community, plus :

- Connexion PostGIS persistante
- Mode hybride (DuckDB + PostGIS)
- Stockage S3/MinIO
- Triggers ESB (pg_notify + actions)
- Exécuteur DAG (graphe de nœuds)
- Visual editor (node editor)
- Monitoring / métriques (Prometheus)
- Pipelines cron
- Logs d'audit
- 6 capabilities raster (`zonal_stats`, `raster_clip`, `ndvi`, `raster_reproject`, `raster_merge`, `change_detection`)
- 6 capabilities réseau (`shortest_path`, `isochrone`, `od_matrix`, `mst`, `network_allocation`, `connectivity_check`)
- Capability `postgis_sql` (SQL paramétré)
- Jusqu'à 5 clés API, 50 datasets, 1 instance

Pour activer une licence Pro :

```bash
GISPULSE_TIER=pro
GISPULSE_LICENSE_KEY=gp-pro-xxxxxxxxxxxx
```

### Team (299 €/mois ou 2 990 €/an)

Toutes les features Pro, plus :

- RBAC (rôles et permissions)
- Multi-projets (schémas PostGIS isolés)
- Support 48h
- 2 instances, 20 clés API, datasets illimités

### Enterprise (à partir de 1 490 €/mois, sur devis)

- SSO SAML / OIDC
- Clustering
- White-label
- SLA 4h
- Capabilities custom
- Instances et clés API illimitées

Contactez [sales@gispulse.dev](mailto:sales@gispulse.dev) pour un devis.

### Early Adopter

- 49 €/mois (prix verrouillé 24 mois)
- Limité aux 50 premiers clients
- Essai gratuit 30 jours

## Vérification de la configuration

```bash
gispulse doctor
```

Affiche l'état de toutes les dépendances, la connexion PostGIS si configurée, l'espace disque et le niveau de licence détecté.

## Configuration avancée PostGIS

En mode PostGIS, le pool de connexions SQLAlchemy est configuré avec :
- `pool_size=20` connexions persistantes
- `max_overflow=30` connexions supplémentaires temporaires
- Détection automatique des colonnes géométrie via le catalogue PostGIS

Pour les triggers `pg_notify`, le `TriggerManager` installe des fonctions PostgreSQL qui émettent des notifications sur INSERT/UPDATE. Le `PgNotifyListener` (asyncpg) écoute ces notifications en temps réel.

Pour les déploiements production avec PostGIS, consultez le guide [Déploiement](/guide/deployment).

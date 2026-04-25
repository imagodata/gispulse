---
title: Configuration
description: TOML config file, environment variables, profiles, Pydantic Settings, Community/Pro tiers, and advanced configuration.
---

# Configuration

GISPulse uses three configuration mechanisms with a clear precedence chain:

```
env vars  >  gispulse.{profile}.toml  >  gispulse.toml  >  defaults
```

::: tip Pydantic Settings (v1.0.2+)
All configuration is centralised in a single Pydantic Settings module (`core/config.py`). In Python:
```python
from core.config import settings

settings.engine.backend   # "gpkg"
settings.api.env          # "development"
settings.redis.url        # ""
```
:::

## TOML config file (recommended)

Create a `gispulse.toml` file at your project root:

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
cors_origins = ""               # comma-separated CORS origins
max_upload_mb = 500

[logging]
level = "INFO"                  # DEBUG | INFO | WARNING | ERROR
format = "console"              # console | json

[session]
expiry = 28800                  # 8 hours

[jobs]
timeout = 3600
duckdb_threshold = 100000
```

The file is searched in this order:
1. `GISPULSE_CONFIG` env var (explicit path)
2. `./gispulse.toml` (current directory)
3. `~/.gispulse/gispulse.toml` (user-level)

::: info Secrets
Secrets (API keys, DSN, OIDC client_secret) should stay in environment variables, not in the TOML file.
:::

## Profiles

Set `GISPULSE_PROFILE` to load an overlay config file:

```bash
GISPULSE_PROFILE=prod
```

This loads `gispulse.prod.toml` on top of the base file. Profile values override base values (deep merge). Environment variables override everything.

Example `gispulse.prod.toml`:

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

## Pipeline schema validation

Rule files (v1) and pipeline files (v2) are automatically validated on load via JSON Schema. Two formats are supported:

**v1 format** (flat rule array):
```json
[
  {"name": "buffer_50m", "capability": "buffer", "config": {"distance": 50}}
]
```

**v2 format** (DAG pipeline with triggers):
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

Validation is enabled by default in `load_pipeline()` and `load_rules()`. To disable: `load_pipeline(path, validate=False)`.

## Environment variables

### Engine and storage

| Variable | Default | Description |
|----------|---------|-------------|
| `GISPULSE_ENGINE` | `gpkg` | Execution engine: `gpkg`, `duckdb`, `postgis`, `hybrid` |
| `GISPULSE_TIER` | `community` | Tier: `community`, `pro`, `enterprise` |
| `GISPULSE_LICENSE_KEY` | — | License key (required for pro/enterprise) |
| `GISPULSE_STORAGE` | `sqlite` | Metadata storage: `sqlite`, `memory` |
| `GISPULSE_DB_PATH` | `~/.gispulse/gispulse.db` | SQLite metadata database path |
| `GISPULSE_DATA_DIR` | `~/.gispulse/data` | File storage directory |
| `GISPULSE_ENV` | `development` | Environment: `development`, `production` |

### Database

| Variable | Default | Description |
|----------|---------|-------------|
| `GISPULSE_DSN` | — | PostgreSQL/PostGIS DSN (postgis/hybrid mode) |
| `GISPULSE_DATABASE_URL` | — | Alternative to `GISPULSE_DSN` |
| `GISPULSE_GPKG_PATH` | `project.gpkg` | Default GPKG path (portable mode) |
| `GISPULSE_POSTGIS_DSN` | — | Dedicated DSN for portal SQL queries |
| `GISPULSE_BASE_DSN` | — | Base DSN for multi-schema connections |

Example `GISPULSE_DSN`:

```bash
GISPULSE_DSN=postgresql://gispulse:secret@localhost:5432/gispulse
```

### API and security

| Variable | Default | Description |
|----------|---------|-------------|
| `GISPULSE_API_KEYS` | — | API keys (comma-separated). Empty = auth disabled |
| `GISPULSE_API_KEY` | — | Single API key (legacy, added to `API_KEYS`) |
| `GISPULSE_CORS_ORIGINS` | — | Allowed CORS origins (comma-separated) |
| `GISPULSE_RBAC` | `false` | Enable RBAC (roles and permissions) |
| `GISPULSE_MAX_UPLOAD_MB` | `500` | Max upload size in MB (capped at 5 GB) |
| `GISPULSE_METRICS_TOKEN` | — | Access token for the `/metrics` endpoint |
| `GISPULSE_SQL_ADMIN_KEY` | — | Admin key for portal SQL queries |

### OIDC / SSO (Enterprise)

| Variable | Default | Description |
|----------|---------|-------------|
| `GISPULSE_OIDC_ISSUER` | — | OIDC issuer URL |
| `GISPULSE_OIDC_CLIENT_ID` | — | OIDC Client ID |
| `GISPULSE_OIDC_CLIENT_SECRET` | — | OIDC Client secret |
| `GISPULSE_OIDC_REDIRECT_URI` | — | Callback URI |
| `GISPULSE_OIDC_SCOPES` | `openid,profile,email` | OIDC scopes (comma-separated) |
| `GISPULSE_OIDC_DEFAULT_ROLE` | `editor` | Default role for new SSO users |

### Session

| Variable | Default | Description |
|----------|---------|-------------|
| `GISPULSE_SESSION_SECRET` | — | JWT session secret (required in production with OIDC) |
| `GISPULSE_SESSION_EXPIRY` | `28800` | Session duration in seconds (8h default) |

### Redis and rate limiting

| Variable | Default | Description |
|----------|---------|-------------|
| `GISPULSE_REDIS_URL` | — | Redis for job queue, rate limiting, metering |
| `GISPULSE_RATE_LIMIT_STORAGE` | — | Rate limit backend (fallback: Redis URL, then `memory://`) |

### S3 / MinIO (Pro)

| Variable | Default | Description |
|----------|---------|-------------|
| `GISPULSE_S3_ENDPOINT` | — | S3/MinIO endpoint URL |
| `GISPULSE_S3_BUCKET` | `gispulse` | Bucket name |
| `GISPULSE_S3_ACCESS_KEY` | — | S3 access key |
| `GISPULSE_S3_SECRET_KEY` | — | S3 secret key |
| `GISPULSE_S3_REGION` | `us-east-1` | S3 region |

### Stripe (SaaS mode)

| Variable | Default | Description |
|----------|---------|-------------|
| `GISPULSE_STRIPE_API_KEY` | — | Stripe API key |
| `GISPULSE_STRIPE_WEBHOOK_SECRET` | — | Stripe webhook secret |
| `GISPULSE_STRIPE_PRICE_PRO_MONTHLY` | — | Stripe Price ID for Pro monthly |
| `GISPULSE_STRIPE_PRICE_PRO_ANNUAL` | — | Stripe Price ID for Pro annual |
| `GISPULSE_STRIPE_PRICE_TEAM_MONTHLY` | — | Stripe Price ID for Team monthly |
| `GISPULSE_STRIPE_PRICE_TEAM_ANNUAL` | — | Stripe Price ID for Team annual |

### Logging and observability

| Variable | Default | Description |
|----------|---------|-------------|
| `GISPULSE_LOG_LEVEL` | `INFO` | Log level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `GISPULSE_LOG_FORMAT` | `console` | Log format: `console`, `json` |
| `GISPULSE_AUDIT` | `false` | Enable audit logging (Pro) |
| `GISPULSE_AUDIT_RETENTION_DAYS` | `90` | Audit log retention in days |

### Jobs and execution

| Variable | Default | Description |
|----------|---------|-------------|
| `GISPULSE_JOB_TIMEOUT` | `3600` | Job execution timeout in seconds |
| `GISPULSE_DUCKDB_THRESHOLD` | `100000` | Feature count threshold to switch to DuckDB |

### Telemetry

| Variable | Default | Description |
|----------|---------|-------------|
| `GISPULSE_TELEMETRY` | — | `0` to disable, `1` to force enable |
| `GISPULSE_TELEMETRY_URL` | — | Telemetry collector URL |
| `GISPULSE_NO_UPDATE_CHECK` | — | `1` to disable update checks |

### Portal frontend

| Variable | Default | Description |
|----------|---------|-------------|
| `VITE_API_URL` | `http://localhost:8001` | API URL for the React frontend |

## .env file (alternative)

If you prefer environment variables in `.env` format:

```bash
# .env — GISPulse configuration
GISPULSE_ENGINE=gpkg
GISPULSE_STORAGE=sqlite
GISPULSE_GPKG_PATH=project.gpkg
GISPULSE_TIER=community

# PostGIS (optional, postgis/hybrid mode)
# GISPULSE_DSN=postgresql://gispulse:secret@localhost:5432/gispulse
```

::: warning Do not commit .env
Add `.env` to your `.gitignore`. It contains secrets. The `gispulse.toml` file can be committed since it should not contain secrets — use environment variables for sensitive values.
:::

## Tiers and features

### Community (free, AGPL-3.0)

- Full GPKG/DuckDB engine
- All CLI commands
- Single-user Portal
- Python SDK
- QGIS Plugin
- Docker
- 104 Community capabilities: vector, attributes, overlay, classification & styling, spatial statistics, clustering, topology, temporal, 3D pointcloud (LAS/LAZ)
- Unlimited local datasets

### Pro (79 EUR/month or 790 EUR/year)

All Community features, plus:

- Persistent PostGIS connection
- Hybrid mode (DuckDB + PostGIS)
- S3/MinIO storage
- ESB Triggers (pg_notify + actions)
- DAG executor (node graph)
- Visual editor (node editor)
- Monitoring / metrics (Prometheus)
- Cron pipelines
- Audit logging
- 6 raster capabilities (`zonal_stats`, `raster_clip`, `ndvi`, `raster_reproject`, `raster_merge`, `change_detection`)
- 6 network capabilities (`shortest_path`, `isochrone`, `od_matrix`, `mst`, `network_allocation`, `connectivity_check`)
- `postgis_sql` capability (parameterised SQL)
- Up to 5 API keys, 50 datasets, 1 instance

To activate a Pro license:

```bash
GISPULSE_TIER=pro
GISPULSE_LICENSE_KEY=gp-pro-xxxxxxxxxxxx
```

### Team (299 EUR/month or 2,990 EUR/year)

All Pro features, plus:

- RBAC (roles and permissions)
- Multi-project support (isolated PostGIS schemas)
- 48h support
- 2 instances, 20 API keys, unlimited datasets

### Enterprise (starting at 1,490 EUR/month, custom quote)

- SSO SAML / OIDC
- Clustering
- White-label
- 4h SLA
- Custom capabilities
- Unlimited instances and API keys

Contact [sales@gispulse.dev](mailto:sales@gispulse.dev) for a quote.

### Early Adopter

- 49 EUR/month (price locked for 24 months)
- Limited to the first 50 customers
- 30-day free trial

## Verifying configuration

```bash
gispulse doctor
```

Displays the status of all dependencies, the PostGIS connection if configured, disk space, and the detected license level.

## Advanced PostGIS configuration

In PostGIS mode, the SQLAlchemy connection pool is configured with:
- `pool_size=20` persistent connections
- `max_overflow=30` temporary overflow connections
- Automatic geometry column detection via the PostGIS catalogue

For `pg_notify` triggers, the `TriggerManager` installs PostgreSQL functions that emit notifications on INSERT/UPDATE. The `PgNotifyListener` (asyncpg) listens in real time.

For production deployments with PostGIS, see the [Deployment](/en/guide/deployment) guide.

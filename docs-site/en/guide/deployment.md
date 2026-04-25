---
title: Deployment
description: Docker Compose, production VPS with Caddy + Prometheus, secure configuration.
---

# Deployment

GISPulse can be deployed locally, on a VPS, or in a cluster. This guide covers production configurations.

## Local (development)

```bash
pip install "gispulse[all]"
gispulse portal --host 127.0.0.1 --port 8001
```

## Docker Compose — full stack

Recommended configuration for a VPS or team server:

```yaml
# docker-compose.yml
services:
  gispulse:
    image: ghcr.io/gispulse/gispulse:latest
    restart: unless-stopped
    ports:
      - "8001:8001"
    environment:
      - GISPULSE_DSN=postgresql://gispulse:${POSTGRES_PASSWORD}@postgis:5432/gispulse
      - GISPULSE_DATA_DIR=/data
      - GISPULSE_LOG_LEVEL=INFO
    volumes:
      - gispulse_data:/data
    command: portal --host 0.0.0.0 --port 8001
    depends_on:
      postgis:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8001/health"]
      interval: 30s
      timeout: 10s
      retries: 3

  postgis:
    image: postgis/postgis:16-3.4
    restart: unless-stopped
    environment:
      POSTGRES_USER: gispulse
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: gispulse
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U gispulse -d gispulse"]
      interval: 10s
      timeout: 5s
      retries: 5

volumes:
  pgdata:
  gispulse_data:
```

```bash
# .env (do not commit)
POSTGRES_PASSWORD=changeme_strong_password
```

```bash
docker compose up -d
```

## Production VPS — with Caddy (automatic HTTPS)

The `deploy/` directory in the repository contains a complete production stack:

```
deploy/
├── docker-compose.prod.yml    # Caddy + GISPulse + PostGIS
├── caddy/
│   └── Caddyfile              # Reverse proxy + automatic TLS
├── prometheus/
│   └── prometheus.yml         # Metrics
└── grafana/
    └── provisioning/          # Dashboards
```

**Basic Caddyfile:**

```
gispulse.example.com {
    reverse_proxy gispulse:8001
    encode gzip
    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
        X-Content-Type-Options nosniff
        X-Frame-Options DENY
    }
}
```

**Launch:**

```bash
cd deploy/
cp .env.example .env
# Edit .env with your values
docker compose -f docker-compose.prod.yml up -d
```

Caddy automatically obtains a Let's Encrypt certificate for your domain.

## Production environment variables

```bash
# .env production
POSTGRES_PASSWORD=CHANGE_THIS_PASSWORD
GISPULSE_LOG_LEVEL=WARNING
GISPULSE_CORS_ORIGINS=https://gispulse.example.com
GISPULSE_REDIS_URL=redis://localhost:6379

# Optional — monitoring
GRAFANA_PASSWORD=admin_password
```

## Security

### API authentication

In production, configure an API key:

```bash
GISPULSE_API_KEYS=sk-gp-key1,sk-gp-key2
```

All REST requests must then include:

```
Authorization: Bearer sk-gp-key1
```

### Firewall / network

- Only expose port 443 (Caddy) publicly
- PostGIS must not be accessible from the outside
- Use environment variables for all secrets (never in the Docker image)

### PostGIS backup

The production stack includes a `pg-backup` service with 30-day rotation:

```yaml
pg-backup:
  image: prodrigestivill/postgres-backup-local
  environment:
    - POSTGRES_HOST=postgis
    - POSTGRES_USER=gispulse
    - POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
    - POSTGRES_DB=gispulse
    - SCHEDULE=@daily
    - BACKUP_KEEP_DAYS=30
  volumes:
    - ./backups:/backups
```

## Monitoring (Pro)

The production stack includes Prometheus + Grafana:

- **Prometheus**: job execution metrics, API latencies, PostGIS status
- **Grafana**: preconfigured dashboards on port 3000

```bash
# Grafana access
http://your-server:3000
# Login: admin / ${GRAFANA_PASSWORD}
```

## Updating

```bash
docker compose pull
docker compose up -d
```

PostGIS data is stored in a Docker volume — no data loss during updates.

## Pre-production checklist

- [ ] Environment variables set (no hardcoded secrets)
- [ ] `GISPULSE_CORS_ORIGINS` restricted to your domain
- [ ] `GISPULSE_API_KEYS` configured with strong keys
- [ ] PostGIS backups configured
- [ ] HTTPS via Caddy active
- [ ] `gispulse doctor` returns all green
- [ ] Prometheus monitoring active

<!-- TODO: document horizontal clustering (Enterprise) -->

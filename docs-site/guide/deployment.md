---
title: Déploiement
description: Docker Compose, VPS production avec Caddy + Prometheus, Terraform, configuration sécurisée.
---

# Déploiement

GISPulse se déploie en local, sur un VPS ou en cluster. Ce guide couvre les configurations de production.

## Local (développement)

```bash
pip install "gispulse[all]"
gispulse portal --host 127.0.0.1 --port 8001
```

## Docker Compose — développement

Stack avec hot-reload pour le développement :

```yaml
# docker-compose.yml
services:
  postgis:
    image: postgis/postgis:16-3.4
    ports:
      - "5433:5432"
    environment:
      POSTGRES_USER: gispulse
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: gispulse
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U gispulse"]
      interval: 10s
      timeout: 5s
      retries: 5

  gispulse-api:
    build: .
    ports:
      - "8001:8001"
    environment:
      - GISPULSE_ENGINE=gpkg
      - GISPULSE_STORAGE=sqlite
    volumes:
      # Hot-reload des modules Python
      - ./core:/app/core
      - ./capabilities:/app/capabilities
      - ./rules:/app/rules
      - ./orchestration:/app/orchestration
      - ./persistence:/app/persistence
      - ./adapters:/app/adapters
    command: uvicorn adapters.http.app:create_app --host 0.0.0.0 --port 8001 --reload --factory
    depends_on:
      postgis:
        condition: service_healthy

  portal:
    image: node:20-alpine
    ports:
      - "8080:5173"
    working_dir: /app
    volumes:
      - ./portal:/app
      - portal_node_modules:/app/node_modules
    command: sh -c "npm install && npm run dev -- --host 0.0.0.0"

volumes:
  pgdata:
  gispulse_data:
  portal_node_modules:
```

```bash
docker compose up -d
```

## Docker Compose — production

Le répertoire `deploy/` contient une stack production complète :

```
deploy/
├── docker-compose.prod.yml    # Stack complète
├── caddy/
│   └── Caddyfile              # Reverse proxy + TLS automatique
├── prometheus/
│   └── prometheus.yml         # Métriques
└── grafana/
    └── provisioning/          # Dashboards
```

### Services production

```yaml
# deploy/docker-compose.prod.yml (simplifié)
services:
  postgis:
    image: postgis/postgis:16-3.4
    deploy:
      resources:
        limits: { memory: 2G, cpus: "2" }
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./docker/init.sql:/docker-entrypoint-initdb.d/init.sql

  gispulse-api:
    build: .
    ports:
      - "8001:8001"
    environment:
      - GISPULSE_ENGINE=postgis
      - GISPULSE_DSN=postgresql://gispulse:${POSTGRES_PASSWORD}@postgis:5432/gispulse
      - GISPULSE_ENV=production
      - GISPULSE_API_KEYS=${API_KEYS}
      - GISPULSE_CORS_ORIGINS=https://${DOMAIN}
    deploy:
      resources:
        limits: { memory: 4G, cpus: "4" }
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8001/health"]
      interval: 30s

  caddy:
    image: caddy:2-alpine
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./caddy/Caddyfile:/etc/caddy/Caddyfile

  prometheus:
    image: prom/prometheus:latest
    volumes:
      - ./prometheus/prometheus.yml:/etc/prometheus/prometheus.yml
      - prometheus:/prometheus
    command: --storage.tsdb.retention.time=30d

  grafana:
    image: grafana/grafana:latest
    ports:
      - "3000:3000"
    volumes:
      - grafana:/var/lib/grafana

  pg-backup:
    image: prodrigestivill/postgres-backup-local
    environment:
      - POSTGRES_HOST=postgis
      - POSTGRES_USER=gispulse
      - POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
      - SCHEDULE=@daily
      - BACKUP_KEEP_DAYS=30
    volumes:
      - ./backups:/backups
```

### Caddyfile

```
gispulse.example.com {
    reverse_proxy gispulse-api:8001
    encode gzip
    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
        X-Content-Type-Options nosniff
        X-Frame-Options DENY
    }
}
```

**Lancement :**

```bash
cd deploy/
cp .env.example .env
# Éditer .env avec vos valeurs
docker compose -f docker-compose.prod.yml up -d
```

Caddy obtient automatiquement un certificat Let's Encrypt pour votre domaine.

## Terraform (IaC)

Le répertoire `deploy/` inclut des modules Terraform pour DigitalOcean et Hetzner :

```
deploy/
├── digitalocean/     # Droplet + cloud-init
├── hetzner/          # Bare metal
└── modules/
    └── gispulse-stack/  # Module réutilisable (compose + Caddy + monitoring)
```

```bash
cd deploy/digitalocean
terraform init
terraform apply -var="domain=gispulse.example.com"
```

## Dockerfile — build multi-stage

Le `Dockerfile` utilise un build en 3 étapes :

1. **portal-build** (node:20-slim) — Build React portal
2. **viewer-build** (node:20-slim) — Build deck.gl viewer
3. **Python runtime** (python:3.12-slim) — GDAL + Python + frontends

```bash
# Build local
make docker-build

# Point d'entrée
uvicorn adapters.http.app:create_app --host 0.0.0.0 --port 8001 --factory
```

## Variables d'environnement

### Moteur et stockage

```bash
GISPULSE_ENGINE=gpkg            # gpkg | duckdb | postgis | hybrid
GISPULSE_STORAGE=sqlite         # sqlite | memory
GISPULSE_DSN=postgresql://...   # DSN PostGIS (mode postgis/hybrid)
GISPULSE_ENV=production         # development | production
```

### Authentification et sécurité

```bash
GISPULSE_API_KEYS=sk-gp-key1,sk-gp-key2   # Clés API (virgule-séparées)
GISPULSE_CORS_ORIGINS=https://example.com  # Origines CORS autorisées
```

### Tier et licence

```bash
GISPULSE_TIER=community         # community | pro | enterprise
GISPULSE_LICENSE_KEY=...        # Requis pour pro/enterprise
```

### OIDC / SSO (Enterprise)

```bash
GISPULSE_OIDC_ISSUER=https://auth.example.com
GISPULSE_OIDC_CLIENT_ID=gispulse
GISPULSE_OIDC_CLIENT_SECRET=secret
GISPULSE_OIDC_REDIRECT_URI=https://gispulse.example.com/auth/callback
GISPULSE_SESSION_SECRET=...     # Requis en production avec OIDC
```

### Services optionnels

```bash
GISPULSE_REDIS_URL=redis://localhost:6379
GISPULSE_RATE_LIMIT_STORAGE=redis://localhost:6379  # ou memory://
GISPULSE_POSTGIS_DSN=...       # DSN spécifique pour les requêtes SQL portal
```

### Portal frontend

```bash
VITE_API_URL=http://localhost:8001  # URL API pour le frontend
```

## Sécurisation

### Middleware de production

En mode `GISPULSE_ENV=production`, le `ProductionAuthMiddleware` est activé automatiquement et force l'authentification sur tous les endpoints.

### Firewall / réseau

- Exposez uniquement le port 443 (Caddy) publiquement
- PostGIS ne doit **pas** être accessible depuis l'extérieur
- Utilisez des variables d'environnement pour tous les secrets
- DSN PostGIS uniquement en variable d'environnement (`GISPULSE_DSN`), jamais dans les requêtes

### Protection SSRF

Les endpoints d'upload et d'import OGC vérifient les URLs contre une blocklist d'IPs privées.

### Rate Limiting

300 requêtes/minute par défaut. Redis-backed en production, in-memory en développement.

## Monitoring (Pro)

La stack production inclut Prometheus + Grafana :

- **Prometheus** : métriques d'exécution des jobs, latences API, état PostGIS
- **Grafana** : dashboards préconfigurés sur le port 3000

Métriques exposées :
- `gispulse_job_duration_seconds` — Durée d'exécution des jobs
- `gispulse_jobs_total` — Compteur de jobs
- `gispulse_jobs_failed` — Compteur d'échecs
- `gispulse_features_processed` — Features traitées

## Mise à jour

```bash
docker compose pull
docker compose up -d
```

Les données PostGIS sont dans un volume Docker — pas de perte lors de la mise à jour.

## Checklist avant production

- [ ] `GISPULSE_ENV=production` défini
- [ ] `GISPULSE_API_KEYS` configuré avec des clés fortes
- [ ] `GISPULSE_CORS_ORIGINS` restreint à votre domaine
- [ ] `GISPULSE_SESSION_SECRET` défini (si OIDC)
- [ ] `GISPULSE_DSN` uniquement en variable d'environnement
- [ ] Sauvegardes PostGIS configurées (pg-backup)
- [ ] HTTPS via Caddy actif
- [ ] `gispulse doctor` retourne tout vert
- [ ] Monitoring Prometheus actif
- [ ] Redis configuré pour le rate limiting en production

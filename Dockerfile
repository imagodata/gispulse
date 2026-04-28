# NB: the portal frontend lives in the sibling repository
# imagodata/gispulse-portal and is built/published independently. The
# OSS Docker image therefore ships only the Python engine + the embedded
# viewer; portal artefacts are consumed from the sibling repo at runtime.

# ---- Stage 1: Build viewer frontend ----
FROM node:20-slim AS viewer-build
WORKDIR /app/viewer
COPY viewer/package.json viewer/package-lock.json ./
RUN npm ci --ignore-scripts
COPY viewer/ .
COPY design-system/ /app/design-system/
RUN npm run build

# ---- Stage 2: Python runtime ----
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl libgdal-dev \
    gdal-bin \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir ".[postgis,api,sso,network]"

COPY . .

# Copy built viewer frontend from build stage. Portal frontend is shipped
# separately from the imagodata/gispulse-portal repo.
COPY --from=viewer-build /app/viewer/dist /app/viewer/dist

# Run as non-root user for security
RUN useradd -m -u 1000 -s /bin/bash appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8001

CMD ["uvicorn", "gispulse.adapters.http.app:create_app", "--host", "0.0.0.0", "--port", "8001", "--factory"]

# GISPulse OSS Docker image — the Python engine + HTTP API.
#
# The portal frontend lives in the sibling repository
# imagodata/gispulse-portal and is built/published independently; the OSS
# image ships only the Python engine and consumes portal artefacts from
# that repo at runtime.

FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl libgdal-dev \
    gdal-bin \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy the full source first: the v1.8.0 src/ layout means the package
# must be present for `pip install .` to discover and install it into
# site-packages (the runtime imports `gispulse` from there, not from /app).
COPY . .
RUN pip install --no-cache-dir ".[postgis,api,sso,network]"

# Run as non-root user for security
RUN useradd -m -u 1000 -s /bin/bash appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8001

CMD ["uvicorn", "gispulse.adapters.http.app:create_app", "--host", "0.0.0.0", "--port", "8001", "--factory"]

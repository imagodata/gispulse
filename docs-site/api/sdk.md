---
title: Python SDK
description: Documentation du SDK Python GISPulse — installation, client synchrone/asynchrone, streaming WebSocket.
---

# Python SDK

Le SDK Python `gispulse-sdk` est un client HTTP type-safe pour l'API REST GISPulse. Il supporte les modes synchrone et asynchrone, le streaming SSE et WebSocket.

## Installation

```bash
pip install gispulse-sdk

# Avec support WebSocket
pip install "gispulse-sdk[ws]"
```

**Prérequis :** Python 3.9+, `httpx`, `pydantic>=2.0`

## Client synchrone

```python
from gispulse_sdk import GISPulseClient

# Connexion locale (sans auth)
client = GISPulseClient("http://localhost:8001")

# Connexion distante avec clé API
client = GISPulseClient(
    "https://gispulse.exemple.com",
    api_key="sk-gp-votre-cle",
)
```

### Utilisation comme context manager

```python
with GISPulseClient("http://localhost:8001") as client:
    datasets = client.datasets.list()
    print(datasets)
# Connexion fermée automatiquement
```

## Client asynchrone

```python
from gispulse_sdk import AsyncGISPulseClient

async def main():
    async with AsyncGISPulseClient("http://localhost:8001") as client:
        datasets = await client.datasets.list()
        print(datasets)

import asyncio
asyncio.run(main())
```

## Endpoints disponibles

| `client.X` | Description |
|-------------|-------------|
| `client.datasets` | Gestion des datasets |
| `client.jobs` | Exécution et suivi des jobs |
| `client.rules` | CRUD des règles |
| `client.capabilities` | Liste des capabilities |
| `client.triggers` | Gestion des triggers (Pro) |
| `client.scenarios` | Gestion des scénarios (Pro) |
| `client.sessions` | Sessions PostGIS (Pro) |
| `client.projects` | Projets (Pro) |
| `client.catalog` | Catalogue OGC |
| `client.ogc` | OGC Features API |

---

## Datasets

### Uploader un fichier

```python
dataset = client.datasets.upload("data/parcelles.gpkg")
print(dataset.id)       # UUID du dataset
print(dataset.name)     # "parcelles.gpkg"
print(dataset.format)   # "GPKG"
print(dataset.crs)      # "EPSG:2154"
```

### Lister les datasets

```python
datasets = client.datasets.list(limit=50, offset=0)
for ds in datasets:
    print(f"{ds.id} — {ds.name} ({ds.format})")
```

### Récupérer les features

```python
fc = client.datasets.features(
    dataset_id=dataset.id,
    layer="parcelles",
    limit=1000,
    bbox=(2.3, 48.8, 2.4, 48.9),  # minx, miny, maxx, maxy
)
# fc est un dict GeoJSON FeatureCollection
```

### Exporter un dataset

```python
path = client.datasets.export(
    dataset_id=dataset.id,
    format="geojson",
    output_path="output/export.geojson",
)
print(f"Exporté vers {path}")
```

### Requête SQL

```python
result = client.datasets.sql(
    "SELECT code_dept, COUNT(*) as nb_parcelles FROM parcelles GROUP BY 1 ORDER BY 2 DESC"
)
print(result["rows"])
```

### Enregistrer un service OGC

```python
from gispulse_sdk.models import OGCDatasetCreate

dataset = client.datasets.upload_ogc(OGCDatasetCreate(
    url="https://wxs.ign.fr/parcellaire/geoportail/wfs",
    service_type="WFS",
    layer_name="BDPARCELLAIRE_VECTEUR:parcelle",
    name="Parcelles IGN",
))
```

---

## Jobs

### Créer et attendre un job

```python
from gispulse_sdk.models import JobCreate

job = client.jobs.create(JobCreate(
    name="buffer_parcelles",
    dataset_id=dataset.id,
    parameters={"rule_ids": [str(rule.id)]},
))

# Attendre la fin (polling)
import time
while True:
    job = client.jobs.get(job.id)
    if job.status in ("COMPLETED", "FAILED"):
        break
    time.sleep(1)

print(f"Job terminé: {job.status}")
```

### Streaming SSE (async)

```python
async with AsyncGISPulseClient("http://localhost:8001") as client:
    async for event in client.jobs.stream(job_id):
        print(event)
```

---

## Règles

### CRUD complet

```python
from gispulse_sdk.models import RuleCreate

# Créer
rule = client.rules.create(RuleCreate(
    name="buffer_50m",
    capability="buffer",
    config={"distance": 50},
    enabled=True,
))

# Lister
rules = client.rules.list()

# Mettre à jour
rule = client.rules.update(rule.id, {"config": {"distance": 100}})

# Supprimer
client.rules.delete(rule.id)
```

### Valider des règles (dry-run)

```python
results = client.rules.validate([
    {"capability": "buffer", "config": {"distance": 100}},
    {"capability": "reproject", "config": {"crs": "EPSG:2154"}},
])
for r in results:
    print(f"{r['name']}: {'OK' if r['valid'] else 'FAIL'}")
```

---

## Capabilities

```python
caps = client.capabilities()
for cap in caps:
    print(f"{cap.name}: {cap.description}")
```

---

## Streaming WebSocket

Disponible avec `pip install "gispulse-sdk[ws]"`.

```python
from gispulse_sdk import AsyncGISPulseClient

async def watch_job(job_id: str):
    async with AsyncGISPulseClient("http://localhost:8001") as client:
        async for message in client.streaming.watch_job(job_id):
            print(f"[{message['type']}] {message.get('message', '')}")

asyncio.run(watch_job("job-uuid"))
```

---

## Gestion des erreurs

Le SDK lève des exceptions typées :

```python
from gispulse_sdk.exceptions import (
    GISPulseError,       # Base
    NotFoundError,       # 404
    AuthError,           # 401/403
    ValidationError,     # 422
    RateLimitError,      # 429
    ServerError,         # 500
)

try:
    dataset = client.datasets.get("uuid-inexistant")
except NotFoundError:
    print("Dataset non trouvé")
except AuthError:
    print("Clé API invalide")
```

---

## Exemple complet — pipeline automatisé

```python
from gispulse_sdk import GISPulseClient
from gispulse_sdk.models import RuleCreate, JobCreate
import time

with GISPulseClient("http://localhost:8001") as client:
    # 1. Upload du fichier
    print("Upload...")
    ds = client.datasets.upload("data/communes_bretagne.gpkg")

    # 2. Créer les règles
    rules = [
        client.rules.create(RuleCreate(
            name="buffer_2km",
            capability="buffer",
            config={"distance": 2000, "order": 0},
        )),
        client.rules.create(RuleCreate(
            name="area_calc",
            capability="area_length",
            config={"area_column": "surface_buffer_m2", "order": 1},
        )),
    ]

    # 3. Lancer le job
    print("Exécution...")
    job = client.jobs.create(JobCreate(
        name="analyse_communes",
        dataset_id=ds.id,
        parameters={"rule_ids": [str(r.id) for r in rules]},
    ))

    # 4. Attendre
    while True:
        job = client.jobs.get(job.id)
        if job.status in ("COMPLETED", "FAILED"):
            break
        time.sleep(0.5)

    if job.status == "COMPLETED":
        print("Succès!")
        # 5. Exporter le résultat
        out = client.datasets.export(ds.id, format="geojson", output_path="output/result.geojson")
        print(f"Résultat: {out}")
    else:
        print(f"Échec: {job.status}")
```

---

## Référence des modèles

<!-- TODO: documenter tous les modèles Pydantic du SDK (DatasetResponse, JobResponse, etc.) -->

Les modèles Pydantic sont dans `gispulse_sdk/models.py`. Ils sont tous exportés depuis `gispulse_sdk` :

```python
from gispulse_sdk.models import (
    DatasetResponse,
    JobResponse,
    RuleResponse,
    CapabilityInfo,
    HealthResponse,
    OGCDatasetCreate,
    RuleCreate,
    JobCreate,
)
```

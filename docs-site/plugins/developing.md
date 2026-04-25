---
title: Développer un plugin / capability
description: Guide pour créer des capabilities GISPulse personnalisées et intégrer GISPulse dans des clients tiers.
---

# Développer un plugin ou une capability

GISPulse est conçu pour être extensible à deux niveaux :

1. **Capabilities** — nouvelles opérations spatiales enregistrées dans le moteur
2. **Plugins clients** — intégrations dans des logiciels GIS tiers (QGIS, ArcGIS, etc.)

## Créer une capability

Une capability est une classe Python qui hérite de `Capability` et se décore avec `@register`.

### Structure minimale

```python
# capabilities/ma_cap.py
from __future__ import annotations

import geopandas as gpd
from capabilities.base import Capability
from capabilities.registry import register


@register
class MaCapability(Capability):
    name = "ma_cap"
    description = "Description courte de ce que fait la capability"
    schema = {
        "type": "object",
        "properties": {
            "mon_parametre": {
                "type": "number",
                "default": 1.0,
                "description": "Paramètre numérique",
            }
        },
        "required": [],
    }

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        config: dict,
        **kwargs,
    ) -> gpd.GeoDataFrame:
        mon_parametre = config.get("mon_parametre", 1.0)
        # Votre logique ici
        result = gdf.copy()
        result["ma_colonne"] = mon_parametre
        return result
```

### Attributs requis

| Attribut | Type | Description |
|----------|------|-------------|
| `name` | `str` | Identifiant unique, utilisé dans les règles JSON (`"capability": "ma_cap"`) |
| `description` | `str` | Affiché dans `gispulse capabilities` et l'API |
| `schema` | `dict` | JSON Schema des paramètres de configuration |

### Auto-enregistrement

La capability est automatiquement disponible dès que le module est importé. GISPulse découvre les capabilities via import des modules dans `capabilities/`.

Pour une capability dans un package externe, importez le module au démarrage :

```python
# votre_package/__init__.py
import votre_package.capabilities  # déclenche @register
```

### Support multi-stratégie (Python + DuckDB)

Pour les performances sur gros volumes, implémentez deux stratégies :

```python
from capabilities.strategy import ExecutionStrategy, ExecutionContext, StrategyMode


class _MaCapPythonStrategy(ExecutionStrategy):
    mode = StrategyMode.PYTHON
    priority = 10  # priorité faible = fallback

    def can_execute(self, ctx: ExecutionContext) -> bool:
        return True  # toujours disponible

    def execute(self, gdf: gpd.GeoDataFrame, ctx: ExecutionContext) -> gpd.GeoDataFrame:
        # Implémentation GeoPandas
        return gdf


class _MaCapDuckDBStrategy(ExecutionStrategy):
    mode = StrategyMode.DUCKDB
    priority = 100  # priorité haute = préféré si applicable

    def can_execute(self, ctx: ExecutionContext) -> bool:
        return ctx.engine.backend_name == "duckdb" and ctx.feature_count > 10_000

    def execute(self, gdf: gpd.GeoDataFrame, ctx: ExecutionContext) -> gpd.GeoDataFrame:
        # Implémentation DuckDB SQL
        ctx.engine.register("_input", gdf)
        return ctx.engine.sql_to_gdf("SELECT *, ... FROM _input")


@register
class MaCapability(Capability):
    name = "ma_cap"
    description = "..."
    schema = {...}
    strategies = [_MaCapPythonStrategy, _MaCapDuckDBStrategy]
```

### Tester une capability

```python
# tests/test_ma_cap.py
import geopandas as gpd
from shapely.geometry import Point
from capabilities.registry import get


def test_ma_cap_basic():
    gdf = gpd.GeoDataFrame(
        {"geometry": [Point(0, 0), Point(1, 1)]},
        crs="EPSG:4326",
    )
    cap = get("ma_cap")
    result = cap.execute(gdf, config={"mon_parametre": 2.0})
    assert len(result) == 2
    assert "ma_colonne" in result.columns
    assert result["ma_colonne"].iloc[0] == 2.0
```

### Utiliser dans les règles JSON

```json
{
  "name": "appliquer_ma_cap",
  "capability": "ma_cap",
  "config": {
    "mon_parametre": 42.0,
    "order": 0
  },
  "enabled": true
}
```

---

## Développer un client GIS (plugin tiers)

Tout logiciel capable de faire des requêtes HTTP peut s'intégrer à GISPulse via l'API REST.

### Ressources

- [API REST — Référence](/api/rest) — endpoints complets
- [Python SDK](/api/sdk) — si votre client est en Python
- OGC API Features — standard pour charger des layers dans tout logiciel GIS compatible

### Pattern de base (HTTP)

```python
import httpx

BASE_URL = "http://localhost:8001"

# 1. Lister les datasets
resp = httpx.get(f"{BASE_URL}/datasets")
datasets = resp.json()

# 2. Charger les features d'un dataset
features = httpx.get(
    f"{BASE_URL}/api/portal/datasets/{dataset_id}/layers/default/features",
    params={"limit": 1000}
).json()

# 3. Exécuter un job
job = httpx.post(f"{BASE_URL}/jobs", json={
    "name": "test",
    "dataset_id": dataset_id,
    "parameters": {"rule_ids": [rule_id]},
}).json()
```

### Plugin QGIS — architecture de référence

Le plugin QGIS `clients/qgis/gispulse_qgis/` est la référence d'implémentation :

| Module | Rôle |
|--------|------|
| `plugin.py` | Point d'entrée QGIS, initialisation des panneaux |
| `api_bridge.py` | Client HTTP vers l'API GISPulse |
| `dock_datasets.py` | Panneau de gestion des datasets |
| `dock_jobs.py` | Panneau de suivi des jobs |
| `layer_factories.py` | Création des layers QGIS (OGC, MVT, PostGIS) |
| `connection_dialog.py` | Dialogue de configuration de la connexion |

### Streaming SSE pour le suivi en temps réel

Pour afficher la progression d'un job en temps réel :

```javascript
// JavaScript
const eventSource = new EventSource(`${BASE_URL}/jobs/${jobId}/stream`)
eventSource.onmessage = (event) => {
  const data = JSON.parse(event.data)
  console.log(data.message)
}
eventSource.addEventListener('done', () => {
  eventSource.close()
})
```

### Charger des tuiles vectorielles (MVT)

L'API MVT est compatible avec MapLibre GL JS, Mapbox GL JS et tout client tuile vectorielle :

```javascript
// MapLibre GL JS
map.addSource('gispulse', {
  type: 'vector',
  tiles: [`${BASE_URL}/ogc/collections/${datasetId}/tiles/{z}/{x}/{y}.mvt`],
  minzoom: 0,
  maxzoom: 14,
})
map.addLayer({
  id: 'features',
  type: 'fill',
  source: 'gispulse',
  'source-layer': 'default',
  paint: { 'fill-color': '#2d5016', 'fill-opacity': 0.6 },
})
```

---

## Contribuer

Les capabilities et plugins sont les bienvenues en PR. Voir [CONTRIBUTING.md](https://github.com/gispulse/gispulse/blob/main/CONTRIBUTING.md) pour les conventions.

Capability enterprise (paiement, redistribution) : contactez [contact@gispulse.dev](mailto:contact@gispulse.dev).

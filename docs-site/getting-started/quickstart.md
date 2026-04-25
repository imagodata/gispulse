---
title: Quickstart — Premier job en 5 minutes
description: Créer et exécuter votre premier pipeline GISPulse en 5 minutes.
---

# Quickstart

Vous aurez un pipeline spatial fonctionnel en moins de 5 minutes.

## Prérequis

- Python 3.10+ installé
- `pip install gispulse` effectué

## Étape 1 — Initialiser un projet

```bash
mkdir demo-gispulse && cd demo-gispulse
gispulse init --name demo
```

Vous obtenez :

```
Initialized GISPulse project: demo
  rules/rules.json  — rule template
  data/             — put your data here
  output/           — results go here

Next steps:
  1. Copy your spatial file to data/
  2. Edit rules/rules.json
  3. gispulse run data/myfile.gpkg --rules rules/rules.json -o output/result.gpkg
```

## Étape 2 — Préparer des données

Utilisez n'importe quel fichier vectoriel. Pour cet exemple, téléchargeons un GeoJSON public :

```bash
curl -L "https://raw.githubusercontent.com/datasets/geo-countries/master/data/countries.geojson" \
  -o data/countries.geojson
```

Inspectez le fichier :

```bash
gispulse info data/countries.geojson
```

```
File:     data/countries.geojson
Format:   GeoJSON
Size:     0.24 MB
CRS:      EPSG:4326
Category: vector

1 layer(s):
  - countries: 177 features, MultiPolygon, EPSG:4326
```

## Étape 3 — Écrire des règles

Éditez `rules/rules.json` :

```json
[
  {
    "name": "filter_europe",
    "description": "Garder uniquement les pays européens",
    "capability": "filter",
    "config": {
      "expression": "CONTINENT == 'Europe'",
      "order": 0
    },
    "enabled": true
  },
  {
    "name": "buffer_50km",
    "description": "Buffer de 50 km autour de chaque pays",
    "capability": "buffer",
    "config": {
      "distance": 50000,
      "order": 1
    },
    "enabled": true
  }
]
```

Validez les règles sans exécuter :

```bash
gispulse validate rules/rules.json
```

```
  OK  filter_europe
  OK  buffer_50km

2 rule(s) valid.
```

## Étape 4 — Exécuter le pipeline

```bash
gispulse run data/countries.geojson \
  --rules rules/rules.json \
  -o output/europe_buffered.gpkg
```

```
Loading data/countries.geojson (GeoJSON) [engine: python] ...
  [filter] filter_europe
  [buffer] buffer_50km
  44 features in -> 44 features out
  2 rule(s) applied [engine: python]
Output written to output/europe_buffered.gpkg (.gpkg)
```

## Étape 5 — Visualiser le résultat

```bash
gispulse serve output/europe_buffered.gpkg
```

```
Viewer at http://127.0.0.1:8765
```

Ouvrez le navigateur sur `http://127.0.0.1:8765` pour inspecter le résultat dans le viewer embarqué.

## Aller plus loin

| Objectif | Commande |
|----------|----------|
| Accélération DuckDB | `gispulse run ... --engine duckdb` |
| Toutes les layers d'un GPKG | `gispulse run ... --all-layers` |
| Portal complet | `gispulse portal` |
| Liste des capabilities | `gispulse capabilities` |
| Liste des formats | `gispulse formats` |

- [Référence CLI complète](/guide/cli)
- [Toutes les capabilities](/guide/capabilities)
- [Écrire des règles avancées](/guide/rules)

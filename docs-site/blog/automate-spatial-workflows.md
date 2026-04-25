---
title: "Automatiser un workflow spatial avec GISPulse : validation de donnees cadastrales"
description: "Tutoriel complet : automatiser la validation de donnees cadastrales avec GISPulse. CLI, regles JSON, scheduling cron, et notifications. Exemple concret et reproductible."
date: 2026-04-06
author: GISPulse
head:
  - - meta
    - name: keywords
      content: "automatisation workflow spatial, validation cadastrale, GISPulse tutoriel, spatial ETL, cron scheduling, regles JSON, CLI geospatial"
  - - meta
    - property: og:title
      content: "Automatiser un workflow spatial avec GISPulse : validation de donnees cadastrales"
  - - meta
    - property: og:description
      content: "Tutoriel complet : automatiser la validation de donnees cadastrales avec GISPulse. CLI, regles JSON, scheduling cron. Exemple concret et reproductible."
  - - meta
    - property: og:type
      content: article
  - - meta
    - name: author
      content: GISPulse
---

# Automatiser un workflow spatial avec GISPulse : validation de donnees cadastrales

<p style="font-size: 1.1em; color: var(--vp-c-text-2); max-width: 680px;">
Un cas concret : vous recevez chaque semaine un export GPKG des donnees cadastrales. Vous devez identifier les parcelles sans proprietaire connu, les parcelles en zone inondable, et produire un rapport de surface par commune. Manuellement, c'est 30 minutes dans QGIS. Automatise avec GISPulse, c'est un cron qui tourne sans supervision.
</p>

---

## Prerequis

```bash
# Python 3.10+ requis
pip install gispulse

# Verifier l'installation
gispulse --version
# GISPulse 0.1.0
```

Aucune base de donnees requise pour ce tutoriel — nous utilisons le mode DuckDB portable.

## Structure du projet

```
cadastre-validation/
├── data/
│   ├── parcelles.gpkg          # Export hebdomadaire
│   ├── zones_inondables.gpkg   # Referentiel national GASPAR
│   └── communes.gpkg           # Admin-Express IGN
├── rules/
│   ├── validation.json         # Regles de validation
│   └── reporting.json          # Regles de reporting
├── output/                     # Resultats (genere automatiquement)
└── run.sh                      # Script de lancement
```

---

## Etape 1 : Comprendre le jeu de donnees

Avant d'ecrire les regles, explorez vos donnees avec la CLI GISPulse :

```bash
# Inspecter le schema d'un GPKG
gispulse inspect data/parcelles.gpkg

# Sortie :
# Layer: parcelles
# Features: 142 847
# CRS: EPSG:2154 (RGF93 / Lambert-93)
# Columns: parcelle_id, commune_code, section, numero,
#          surface_m2, proprietaire_id, date_maj
```

```bash
# Verifier la coherence geometrique
gispulse validate data/parcelles.gpkg

# Sortie :
# Geometries valides: 142 821 / 142 847
# Geometries invalides: 26 (self-intersections)
# CRS detecte: EPSG:2154
```

---

## Etape 2 : Ecrire les regles de validation

Creez `rules/validation.json` :

```json
[
  {
    "name": "parcelles_sans_proprietaire",
    "capability": "filter",
    "params": {
      "input": "data/parcelles.gpkg",
      "where": "proprietaire_id IS NULL OR proprietaire_id = ''",
      "output": "output/sans_proprietaire.gpkg"
    }
  },
  {
    "name": "parcelles_zone_inondable",
    "capability": "spatial_join",
    "params": {
      "input": "data/parcelles.gpkg",
      "ref_layer": "data/zones_inondables.gpkg",
      "predicate": "intersects",
      "columns": ["alea", "niveau_risque", "date_arrete"],
      "how": "inner",
      "output": "output/parcelles_inondables.gpkg"
    }
  },
  {
    "name": "surface_inondable_par_commune",
    "capability": "spatial_aggregate",
    "params": {
      "input": "parcelles_zone_inondable",
      "ref_layer": "data/communes.gpkg",
      "predicate": "within",
      "agg": {
        "parcelle_id": "count",
        "surface_m2": "sum"
      },
      "output": "output/stats_communes.gpkg"
    }
  },
  {
    "name": "surface_m2_calculee",
    "capability": "area_length",
    "params": {
      "input": "parcelles_zone_inondable",
      "field_area": "surface_calculee_m2",
      "unit": "m2"
    }
  }
]
```

Lancez la validation :

```bash
gispulse run rules/validation.json --engine duckdb
```

Sortie attendue :

```
[1/4] parcelles_sans_proprietaire ... OK (3 421 features)
[2/4] parcelles_zone_inondable    ... OK (8 742 features)
[3/4] surface_inondable_par_commune ... OK (285 communes)
[4/4] surface_m2_calculee         ... OK

Output files:
  output/sans_proprietaire.gpkg    (3 421 features)
  output/parcelles_inondables.gpkg (8 742 features)
  output/stats_communes.gpkg       (285 features)

Execution time: 4.2s
Engine: DuckDB 1.0.0 (in-memory)
```

---

## Etape 3 : Ajouter les regles de reporting

Creez `rules/reporting.json` pour generer un CSV de synthese :

```json
[
  {
    "name": "rapport_hebdomadaire",
    "capability": "calculate",
    "params": {
      "input": "output/stats_communes.gpkg",
      "expressions": {
        "surface_ha": "surface_m2 / 10000",
        "taux_inondable_pct": "(surface_m2 / area_commune_m2) * 100"
      }
    }
  },
  {
    "name": "export_csv",
    "capability": "filter",
    "params": {
      "input": "rapport_hebdomadaire",
      "output": "output/rapport_{date}.csv",
      "format": "csv"
    }
  }
]
```

---

## Etape 4 : Creer le script de lancement

Creez `run.sh` :

```bash
#!/bin/bash
set -euo pipefail

DATE=$(date +%Y%m%d)
LOG_FILE="output/run_${DATE}.log"

echo "=== GISPulse Cadastre Validation — ${DATE} ===" | tee -a "$LOG_FILE"

# Nettoyer les anciens outputs
mkdir -p output

# Etape 1 : validation
echo "[$(date +%H:%M:%S)] Validation en cours..." | tee -a "$LOG_FILE"
gispulse run rules/validation.json \
  --engine duckdb \
  --log-level info \
  2>&1 | tee -a "$LOG_FILE"

# Etape 2 : reporting
echo "[$(date +%H:%M:%S)] Reporting en cours..." | tee -a "$LOG_FILE"
gispulse run rules/reporting.json \
  --engine duckdb \
  --var date="${DATE}" \
  2>&1 | tee -a "$LOG_FILE"

echo "[$(date +%H:%M:%S)] Termine." | tee -a "$LOG_FILE"

# Notification optionnelle (webhook Slack)
if [ -n "${SLACK_WEBHOOK:-}" ]; then
  FEATURE_COUNT=$(gispulse inspect output/parcelles_inondables.gpkg --count)
  curl -s -X POST "$SLACK_WEBHOOK" \
    -H "Content-Type: application/json" \
    -d "{\"text\": \"Validation cadastrale ${DATE} terminee. ${FEATURE_COUNT} parcelles en zone inondable.\"}"
fi
```

Test manuel :

```bash
chmod +x run.sh
./run.sh
```

---

## Etape 5 : Scheduling avec cron

Automatisez l'execution chaque lundi matin a 6h00 :

```bash
# Editer la crontab
crontab -e
```

Ajoutez la ligne suivante :

```cron
# Validation cadastrale chaque lundi a 6h00
0 6 * * 1 cd /opt/cadastre-validation && ./run.sh >> output/cron.log 2>&1
```

Si vous utilisez GISPulse en mode persistant avec le daemon, utilisez le scheduling natif :

```bash
# Enregistrer le job planifie via l'API
curl -X POST http://localhost:8000/schedules \
  -H "Content-Type: application/json" \
  -d '{
    "name": "validation_cadastrale_hebdo",
    "schedule": "0 6 * * 1",
    "pipeline": "rules/validation.json",
    "engine": "duckdb",
    "notify": {
      "webhook": "https://hooks.slack.com/services/XXX/YYY/ZZZ"
    }
  }'
```

Listez les jobs planifies :

```bash
curl http://localhost:8000/schedules
```

---

## Etape 6 : Integration CI/CD (GitHub Actions)

Pour une validation declenchee a chaque push d'un nouveau GPKG :

```yaml
# .github/workflows/cadastre-validation.yml
name: Validation cadastrale

on:
  push:
    paths:
      - 'data/parcelles.gpkg'
  schedule:
    - cron: '0 6 * * 1'

jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install GISPulse
        run: pip install gispulse

      - name: Run validation
        run: |
          gispulse run rules/validation.json --engine duckdb
          gispulse run rules/reporting.json --engine duckdb --var date=$(date +%Y%m%d)

      - name: Upload results
        uses: actions/upload-artifact@v4
        with:
          name: cadastre-output-${{ github.run_id }}
          path: output/
```

---

## Aller plus loin

### Valider la qualite geometrique avant traitement

Ajoutez une regle de validation en debut de pipeline :

```json
{
  "name": "geometries_valides",
  "capability": "filter",
  "params": {
    "input": "data/parcelles.gpkg",
    "where": "ST_IsValid(geometry)",
    "output": "data/parcelles_valides.gpkg",
    "on_empty": "warn"
  }
}
```

### Envoyer les resultats vers S3

```json
{
  "name": "export_s3",
  "capability": "export",
  "params": {
    "input": "output/stats_communes.gpkg",
    "destination": "s3://mon-bucket/cadastre/{date}/stats_communes.gpkg",
    "format": "gpkg"
  }
}
```

### Passer en mode PostGIS pour les gros volumes

Pour des millions de parcelles, basculez sur le moteur PostGIS :

```bash
# Mode persistant avec PostGIS
gispulse run rules/validation.json \
  --engine postgis \
  --db postgresql://user:pass@localhost:5432/cadastre
```

Les regles JSON sont identiques. Seul le moteur change.

---

## Resume du workflow

```
Export GPKG hebdo
       |
       v
  run.sh (cron lundi 6h)
       |
       v
gispulse run validation.json    # filtre, spatial_join, aggregate
       |
       v
gispulse run reporting.json     # calculate, export CSV
       |
       v
output/                          # GPKG + CSV
       |
       v
Notification Slack / webhook
```

**Temps de setup initial :** ~30 minutes pour creer les regles et le cron.
**Temps de traitement :** ~4-8 secondes pour ~150 000 parcelles en mode DuckDB portable.
**Supervision :** zero intervention manuelle.

---

<div style="padding: 1.5rem; background: var(--vp-c-bg-soft); border-radius: 12px; border-left: 4px solid var(--vp-c-brand-1); margin-top: 2rem;">

**Reproduire cet exemple**

```bash
pip install gispulse
gispulse examples cadastre  # telecharge l'exemple complet
cd cadastre-validation
./run.sh
```

[Documentation CLI](/guide/cli) · [Reference capabilities](/guide/capabilities) · [GitHub](https://github.com/imagodata/gispulse)

</div>

---
title: "Automate a Spatial Workflow with GISPulse: Cadastral Data Validation"
description: "Complete tutorial: automate cadastral data validation with GISPulse. CLI, JSON rules, cron scheduling, and notifications. A concrete, reproducible example."
date: 2026-04-06
author: GISPulse
head:
  - - meta
    - name: keywords
      content: "spatial workflow automation, cadastral validation, GISPulse tutorial, spatial ETL, cron scheduling, JSON rules, geospatial CLI"
  - - meta
    - property: og:title
      content: "Automate a Spatial Workflow with GISPulse: Cadastral Data Validation"
  - - meta
    - property: og:description
      content: "Complete tutorial: automate cadastral data validation with GISPulse. CLI, JSON rules, cron scheduling. A concrete, reproducible example."
  - - meta
    - property: og:type
      content: article
  - - meta
    - name: author
      content: GISPulse
---

# Automate a Spatial Workflow with GISPulse: Cadastral Data Validation

<p style="font-size: 1.1em; color: var(--vp-c-text-2); max-width: 680px;">
A real-world use case: every week you receive a GPKG export of cadastral data. You need to identify parcels without a known owner, parcels in flood zones, and produce a surface area report by municipality. Manually, that's 30 minutes in QGIS. Automated with GISPulse, it's a cron job running unattended.
</p>

---

## Prerequisites

```bash
# Python 3.10+ required
pip install gispulse

# Verify the installation
gispulse --version
# GISPulse 0.1.0
```

No database required for this tutorial -- we use DuckDB portable mode.

## Project Structure

```
cadastre-validation/
├── data/
│   ├── parcelles.gpkg          # Weekly export
│   ├── zones_inondables.gpkg   # National GASPAR flood zone reference
│   └── communes.gpkg           # Admin-Express IGN
├── rules/
│   ├── validation.json         # Validation rules
│   └── reporting.json          # Reporting rules
├── output/                     # Results (generated automatically)
└── run.sh                      # Launch script
```

---

## Step 1: Understand the Dataset

Before writing rules, explore your data with the GISPulse CLI:

```bash
# Inspect a GPKG schema
gispulse inspect data/parcelles.gpkg

# Output:
# Layer: parcelles
# Features: 142,847
# CRS: EPSG:2154 (RGF93 / Lambert-93)
# Columns: parcelle_id, commune_code, section, numero,
#          surface_m2, proprietaire_id, date_maj
```

```bash
# Check geometry consistency
gispulse validate data/parcelles.gpkg

# Output:
# Valid geometries: 142,821 / 142,847
# Invalid geometries: 26 (self-intersections)
# Detected CRS: EPSG:2154
```

---

## Step 2: Write the Validation Rules

Create `rules/validation.json`:

```json
[
  {
    "name": "parcels_without_owner",
    "capability": "filter",
    "params": {
      "input": "data/parcelles.gpkg",
      "where": "proprietaire_id IS NULL OR proprietaire_id = ''",
      "output": "output/sans_proprietaire.gpkg"
    }
  },
  {
    "name": "parcels_in_flood_zone",
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
    "name": "flood_area_by_municipality",
    "capability": "spatial_aggregate",
    "params": {
      "input": "parcels_in_flood_zone",
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
    "name": "computed_area_m2",
    "capability": "area_length",
    "params": {
      "input": "parcels_in_flood_zone",
      "field_area": "surface_calculee_m2",
      "unit": "m2"
    }
  }
]
```

Run the validation:

```bash
gispulse run rules/validation.json --engine duckdb
```

Expected output:

```
[1/4] parcels_without_owner       ... OK (3,421 features)
[2/4] parcels_in_flood_zone       ... OK (8,742 features)
[3/4] flood_area_by_municipality  ... OK (285 municipalities)
[4/4] computed_area_m2            ... OK

Output files:
  output/sans_proprietaire.gpkg    (3,421 features)
  output/parcelles_inondables.gpkg (8,742 features)
  output/stats_communes.gpkg       (285 features)

Execution time: 4.2s
Engine: DuckDB 1.0.0 (in-memory)
```

---

## Step 3: Add Reporting Rules

Create `rules/reporting.json` to generate a summary CSV:

```json
[
  {
    "name": "weekly_report",
    "capability": "calculate",
    "params": {
      "input": "output/stats_communes.gpkg",
      "expressions": {
        "surface_ha": "surface_m2 / 10000",
        "flood_rate_pct": "(surface_m2 / area_commune_m2) * 100"
      }
    }
  },
  {
    "name": "export_csv",
    "capability": "filter",
    "params": {
      "input": "weekly_report",
      "output": "output/report_{date}.csv",
      "format": "csv"
    }
  }
]
```

---

## Step 4: Create the Launch Script

Create `run.sh`:

```bash
#!/bin/bash
set -euo pipefail

DATE=$(date +%Y%m%d)
LOG_FILE="output/run_${DATE}.log"

echo "=== GISPulse Cadastre Validation — ${DATE} ===" | tee -a "$LOG_FILE"

# Clean old outputs
mkdir -p output

# Step 1: validation
echo "[$(date +%H:%M:%S)] Running validation..." | tee -a "$LOG_FILE"
gispulse run rules/validation.json \
  --engine duckdb \
  --log-level info \
  2>&1 | tee -a "$LOG_FILE"

# Step 2: reporting
echo "[$(date +%H:%M:%S)] Running reporting..." | tee -a "$LOG_FILE"
gispulse run rules/reporting.json \
  --engine duckdb \
  --var date="${DATE}" \
  2>&1 | tee -a "$LOG_FILE"

echo "[$(date +%H:%M:%S)] Done." | tee -a "$LOG_FILE"

# Optional notification (Slack webhook)
if [ -n "${SLACK_WEBHOOK:-}" ]; then
  FEATURE_COUNT=$(gispulse inspect output/parcelles_inondables.gpkg --count)
  curl -s -X POST "$SLACK_WEBHOOK" \
    -H "Content-Type: application/json" \
    -d "{\"text\": \"Cadastre validation ${DATE} completed. ${FEATURE_COUNT} parcels in flood zone.\"}"
fi
```

Manual test:

```bash
chmod +x run.sh
./run.sh
```

---

## Step 5: Scheduling with cron

Automate execution every Monday morning at 6:00 AM:

```bash
# Edit the crontab
crontab -e
```

Add the following line:

```cron
# Cadastre validation every Monday at 6:00 AM
0 6 * * 1 cd /opt/cadastre-validation && ./run.sh >> output/cron.log 2>&1
```

If you're using GISPulse in persistent mode with the daemon, use native scheduling:

```bash
# Register the scheduled job via the API
curl -X POST http://localhost:8000/schedules \
  -H "Content-Type: application/json" \
  -d '{
    "name": "weekly_cadastre_validation",
    "schedule": "0 6 * * 1",
    "pipeline": "rules/validation.json",
    "engine": "duckdb",
    "notify": {
      "webhook": "https://hooks.slack.com/services/XXX/YYY/ZZZ"
    }
  }'
```

List scheduled jobs:

```bash
curl http://localhost:8000/schedules
```

---

## Step 6: CI/CD Integration (GitHub Actions)

For validation triggered on each push of a new GPKG:

```yaml
# .github/workflows/cadastre-validation.yml
name: Cadastre Validation

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

## Going Further

### Validate Geometry Quality Before Processing

Add a validation rule at the start of your pipeline:

```json
{
  "name": "valid_geometries",
  "capability": "filter",
  "params": {
    "input": "data/parcelles.gpkg",
    "where": "ST_IsValid(geometry)",
    "output": "data/parcelles_valides.gpkg",
    "on_empty": "warn"
  }
}
```

### Send Results to S3

```json
{
  "name": "export_s3",
  "capability": "export",
  "params": {
    "input": "output/stats_communes.gpkg",
    "destination": "s3://my-bucket/cadastre/{date}/stats_communes.gpkg",
    "format": "gpkg"
  }
}
```

### Switch to PostGIS for Large Volumes

For millions of parcels, switch to the PostGIS engine:

```bash
# Persistent mode with PostGIS
gispulse run rules/validation.json \
  --engine postgis \
  --db postgresql://user:pass@localhost:5432/cadastre
```

The JSON rules are identical. Only the engine changes.

---

## Workflow Summary

```
Weekly GPKG export
       |
       v
  run.sh (cron Monday 6 AM)
       |
       v
gispulse run validation.json    # filter, spatial_join, aggregate
       |
       v
gispulse run reporting.json     # calculate, export CSV
       |
       v
output/                          # GPKG + CSV
       |
       v
Slack / webhook notification
```

**Initial setup time:** ~30 minutes to create the rules and cron.
**Processing time:** ~4-8 seconds for ~150,000 parcels in DuckDB portable mode.
**Supervision:** zero manual intervention.

---

<div style="padding: 1.5rem; background: var(--vp-c-bg-soft); border-radius: 12px; border-left: 4px solid var(--vp-c-brand-1); margin-top: 2rem;">

**Reproduce This Example**

```bash
pip install gispulse
gispulse examples cadastre  # downloads the complete example
cd cadastre-validation
./run.sh
```

[CLI documentation](/guide/cli) · [Capabilities reference](/guide/capabilities) · [GitHub](https://github.com/imagodata/gispulse)

</div>

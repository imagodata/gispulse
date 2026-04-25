---
title: Preset library
description: Catalog of 21 ready-to-run GISPulse presets — urban planning, FTTH, risk, land, agriculture, environment, energy, civil safety, health, building, retail.
---

# Preset library

Each preset is a JSON file **directly consumable by the GISPulse runner** (`gispulse run ... --rules templates/<name>.json`). Filter by domain or capability, preview the pipeline in place, download the JSON.

::: tip No data is loaded until you click "Preview"
The index (**~25 kB**) lists the 21 presets. Full JSON bodies are only fetched on demand.
:::

<TemplatesGallery />

## Using a preset

```bash
# v1 — flat rules
gispulse run input.gpkg \
  --rules templates/foncier_dvf_marche.json \
  -o output/dvf.gpkg \
  --layer dvf

# v2 — pipeline with ref_layers
gispulse run parcels.gpkg \
  --rules templates/foncier_parcelles_vacantes.json \
  --ref-source majic:data/majic.gpkg \
  --ref-source plu_zonage:data/plu.gpkg \
  -o output/brownfields.gpkg
```

## Contributing a preset

See [templates/INDEX.md](https://github.com/imagodata/gispulse/blob/main/templates/INDEX.md) — naming conventions, v1/v2 structure, allowed capabilities.

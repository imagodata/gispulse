---
title: Presets metier
description: Catalogue de 21 presets GISPulse prets a l'emploi — urbanisme, FTTH, risques, foncier, agriculture, environnement, energie, securite civile, sante, batiment, retail.
---

# Catalogue de presets

Chaque preset est un fichier JSON **directement consommable par le runner GISPulse** (`gispulse run ... --rules templates/<name>.json`). Filtrez par domaine ou capability, previsualisez le pipeline en place, telechargez le JSON.

::: tip Aucune donnee n'est chargee tant que vous ne cliquez pas sur "Apercu"
L'index (**~25 kB**) liste les 21 presets. Les corps JSON complets ne sont recuperes qu'a la demande.
:::

<TemplatesGallery />

## Utiliser un preset

```bash
# v1 — rules a plat
gispulse run input.gpkg \
  --rules templates/foncier_dvf_marche.json \
  -o output/dvf.gpkg \
  --layer dvf

# v2 — pipeline avec ref_layers
gispulse run parcelles.gpkg \
  --rules templates/foncier_parcelles_vacantes.json \
  --ref-source majic:data/majic.gpkg \
  --ref-source plu_zonage:data/plu.gpkg \
  -o output/friches.gpkg
```

## Contribuer un preset

Voir [templates/INDEX.md](https://github.com/imagodata/gispulse/blob/main/templates/INDEX.md) — conventions de nommage, structure v1/v2, capabilities autorisees.

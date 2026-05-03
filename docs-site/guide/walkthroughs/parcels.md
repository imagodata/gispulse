---
title: Walkthrough — Parcelles
description: Classer les bâtiments d'une parcelle selon leur appartenance à un isochrone, juste en sauvegardant le GeoPackage. Pas de plugin QGIS requis.
---

# Walkthrough — Parcelles

> **Promesse** : éditer un attribut dans QGIS → `Ctrl+S` → la règle GISPulse se déclenche et reclassifie les bâtiments. Aucun plugin GIS-client n'est nécessaire.

## Ce que vous allez voir

Une couche de **parcelles cadastrales** et une couche **isochrones piéton** (500/750/1000 m). À chaque modification d'une parcelle, GISPulse ré-évalue quels bâtiments tombent dans quel anneau d'accessibilité, et écrit le résultat dans un attribut `accessibility_tier`.

| Avant | Après save |
|---|---|
| `accessibility_tier` vide ou périmé sur les bâtiments d'une parcelle qu'on vient de redessiner | Tous les bâtiments à jour : `tier_500m`, `tier_750m`, `tier_1000m` ou `out_of_range` selon leur position |

## Prérequis

- QGIS ≥ 3.28
- `gispulse` ≥ 1.5.1 (`pipx install gispulse`)
- Le pack démo : `gispulse examples fetch parcels`

## Mise en place (≈ 1 min)

```bash
# 1. Pose le change-log + les triggers SQLite sur le GPKG démo
gispulse track install ~/.gispulse/examples/parcels/parcels.gpkg

# 2. Lance la boucle d'observation (laisse tourner dans un terminal)
gispulse triggers watch \
  --rules ~/.gispulse/examples/parcels/triggers.yaml \
  --dataset ~/.gispulse/examples/parcels/parcels.gpkg
```

Le terminal affiche en continu :

```text
[info] watching parcels.gpkg for change-log entries
[info] 0 pending events
```

## Le scénario en 3 étapes

### 1. Ouvrez la couche dans QGIS

```text
Couche → Ajouter une couche → Vecteur → ~/.gispulse/examples/parcels/parcels.gpkg
```

Sélectionnez **`parcels`**, puis ajoutez aussi **`buildings`** et **`isochrones`** depuis le même GeoPackage.

### 2. Modifiez une parcelle

Activez le mode édition (`Ctrl+E`), redessinez le contour d'une parcelle voisine d'un anneau isochrone, puis **sauvegardez** (`Ctrl+S`). C'est tout.

### 3. Le trigger se déclenche

Le terminal affiche immédiatement :

```text
[info] dml.changed parcels fid=42
[info] rule:classify_buildings_in_isochrones triggered
[info]   → 6 buildings reclassified
[info]   → 2 moved tier_750m → tier_500m
[info]   → 4 unchanged
[info] commit ok in 87 ms
```

Ré-ouvrez la table d'attributs des bâtiments dans QGIS (`F6`) — la colonne `accessibility_tier` est à jour.

## Voir le même scénario en ligne

Le portail de démo affiche exactement la même règle exécutée sur le même
dataset, sans rien installer :

> 🔗 [Try it on `try.gispulse.dev/parcels`](https://try.gispulse.dev/parcels)

Vous y choisissez le rayon d'isochrone (500/750/1000 m), vous cliquez **Run
trigger**, et le portail vous montre les bâtiments reclassifiés sur la
carte. C'est la même règle, le même dataset, le même moteur.

## Sortie attendue côté portail

Section **Events** (`/explorer`) :

```text
2026-05-02T14:32:11Z  classify_buildings_in_isochrones  parcels#42  ok 87ms
2026-05-02T14:32:11Z  dml.changed                       parcels    fid=42
```

## Et après ?

- Le walkthrough sœur [Isochrone](/guide/walkthroughs/isochrone) montre
  comment recomputer **les anneaux** quand c'est la parcelle elle-même
  qui change de forme.
- [Audit](/guide/walkthroughs/audit) trace **chaque** modification et
  exporte un CSV pour la conformité.
- La [matrice CLI ↔ Portail](/guide/symmetry) liste toutes les capabilities
  exposées des deux côtés sur la même source de vérité.

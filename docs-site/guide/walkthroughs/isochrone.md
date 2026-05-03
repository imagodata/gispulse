---
title: Walkthrough — Isochrone
description: Quand la géométrie d'une parcelle change, recomputer ses isochrones piéton 500/750/1000 m via le réseau routier — sans plugin QGIS.
---

# Walkthrough — Isochrone

> **Promesse** : redessinez le contour d'une parcelle dans QGIS → `Ctrl+S` →
> les **anneaux d'accessibilité** sont recalculés depuis son nouveau
> centroïde. Aucun plugin GIS-client n'est nécessaire.

## Ce que vous allez voir

Quand la géométrie d'une parcelle bouge (fusion, division, rectification
cadastrale), ses **isochrones piéton** doivent suivre. Cette règle
recompute les 3 anneaux concentriques via le réseau routier OSM dès que
la parcelle est sauvegardée.

| Avant | Après save |
|---|---|
| Anneaux figés sur l'ancien centroïde de la parcelle | 3 polygones isochrones recalculés à partir du nouveau centroïde |

## Prérequis

- QGIS ≥ 3.28
- `gispulse` ≥ 1.5.1 (`pipx install gispulse`)
- Le pack démo : `gispulse examples fetch isochrone`

## Mise en place (≈ 1 min)

```bash
gispulse track install ~/.gispulse/examples/isochrone/parcels.gpkg

gispulse triggers watch \
  --rules ~/.gispulse/examples/isochrone/triggers.yaml \
  --dataset ~/.gispulse/examples/isochrone/parcels.gpkg
```

La règle utilise le réseau routier embarqué dans le pack démo (`network.gpkg`
sous `~/.gispulse/examples/isochrone/`) — pas d'appel réseau à OSRM ni
Valhalla, tout est local.

## Le scénario en 3 étapes

### 1. Identifiez une parcelle à éditer

Ouvrez `parcels` et `isochrones` côte-à-côte dans QGIS. Choisissez une
parcelle qui touche le bord d'un anneau — l'effet visuel sera plus parlant.

### 2. Redessinez son contour

Activez l'édition (`Ctrl+E`), modifiez quelques sommets pour décaler le
centroïde de plusieurs dizaines de mètres, puis **sauvegardez** (`Ctrl+S`).

### 3. Le trigger ré-isochrone

Le terminal affiche :

```text
[info] dml.changed parcels fid=87
[info] rule:recompute_isochrones triggered
[info]   → 3 rings recomputed (500m, 750m, 1000m)
[info]   → routing graph cache hit
[info] commit ok in 312 ms
```

Rafraîchissez la couche `isochrones` dans QGIS (`F5`) — les 3 polygones
suivent le nouveau centroïde de la parcelle.

## Voir le même scénario en ligne

> 🔗 [Try it on `try.gispulse.dev/isochrone`](https://try.gispulse.dev/isochrone)

Sur le portail vous pouvez **glisser-déposer** le contour de la parcelle
directement sur la carte. La règle tourne en mode `dryrun` (les actions
sont capturées mais pas commit) pour que vous voyiez le résultat sans
toucher au dataset démo.

## Sortie attendue côté portail

Section **Events** (`/explorer`) :

```text
2026-05-02T14:32:11Z  recompute_isochrones  parcels#87  ok 312ms
2026-05-02T14:32:11Z  dml.changed           parcels    fid=87
```

Section **Map** : les 3 anneaux changent de forme en direct quand vous
déplacez la parcelle.

## Coût et limites

- Cache de graphe routier en mémoire : la première recompute après
  démarrage prend ~800 ms, les suivantes ~300 ms.
- Plafond `gispulse triggers watch` : 50 triggers par seconde, suffisant
  pour le scénario démo (édition manuelle).
- Pour des batches >1k parcelles modifiées, préférez `gispulse triggers
  run --once --bulk-threshold 100` qui désactive le watch et fait du
  vectorisé en une passe.

## Et après ?

- [Parcelles](/guide/walkthroughs/parcels) montre l'effet inverse :
  reclassifier les **bâtiments** d'une parcelle quand celle-ci change.
- [Audit](/guide/walkthroughs/audit) trace **chaque** recompute pour
  facilité de revue.
- La [matrice CLI ↔ Portail](/guide/symmetry) liste tous les points
  d'entrée disponibles des deux côtés.

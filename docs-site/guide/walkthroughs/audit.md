---
title: Walkthrough — Audit
description: Tracer chaque modification d'un GeoPackage suivi et exporter un CSV horodaté — pour la conformité, le retour-arrière, ou simplement comprendre qui a touché quoi.
---

# Walkthrough — Audit

> **Promesse** : chaque INSERT/UPDATE/DELETE sur le GeoPackage est tracé
> avec timestamp + utilisateur OS + diff attributaire, exportable en CSV.
> Aucun plugin GIS-client n'est nécessaire.

## Ce que vous allez voir

Une règle `log_event` capture chaque ligne du change-log SQLite et la
réécrit dans une couche de log dédiée (`_gispulse_audit_log`). Cette
couche est lisible comme n'importe quelle table QGIS, et `gispulse audit
export` produit le CSV correspondant.

| Avant | Après quelques saves |
|---|---|
| Aucune trace des modifications dans le GeoPackage | La table `_gispulse_audit_log` contient une ligne par DML, attribut par attribut |

## Prérequis

- QGIS ≥ 3.28
- `gispulse` ≥ 1.5.1 (`pipx install gispulse`)
- Le pack démo : `gispulse examples fetch audit`

## Mise en place (≈ 1 min)

```bash
gispulse track install ~/.gispulse/examples/audit/parcels.gpkg

gispulse triggers watch \
  --rules ~/.gispulse/examples/audit/triggers.yaml \
  --dataset ~/.gispulse/examples/audit/parcels.gpkg
```

Le pack démo contient déjà la couche `_gispulse_audit_log` avec son
schéma (timestamp, op_type, layer, fid, user, before, after) — la règle
`log_event` ne fait que l'alimenter.

## Le scénario en 3 étapes

### 1. Faites quelques modifications

Ouvrez `parcels` dans QGIS, passez en édition, modifiez deux ou trois
attributs (par ex. `zonage_plu` sur deux parcelles, supprimez-en une
troisième), puis **sauvegardez** (`Ctrl+S`).

### 2. Le trigger logge chaque DML

```text
[info] dml.changed parcels fid=12 op=update
[info] rule:log_event triggered
[info]   → audit row +1 (op=update zonage_plu: UA → AU)
[info] dml.changed parcels fid=34 op=update
[info]   → audit row +1 (op=update zonage_plu: N → AU)
[info] dml.changed parcels fid=58 op=delete
[info]   → audit row +1 (op=delete fid=58 snapshot saved)
```

### 3. Inspectez la trace

Dans QGIS, ouvrez la couche `_gispulse_audit_log` et trier par `timestamp
desc`. Chaque modification apparaît avec :

| timestamp | op_type | layer | fid | user | before | after |
|---|---|---|---|---|---|---|
| 2026-05-02T14:32:11Z | update | parcels | 12 | simon | `{"zonage_plu":"UA"}` | `{"zonage_plu":"AU"}` |
| 2026-05-02T14:32:11Z | update | parcels | 34 | simon | `{"zonage_plu":"N"}` | `{"zonage_plu":"AU"}` |
| 2026-05-02T14:32:12Z | delete | parcels | 58 | simon | `{...full snapshot...}` | `null` |

### 4. Exportez le CSV

```bash
gispulse audit export ~/.gispulse/examples/audit/parcels.gpkg \
  --since "2026-05-02T00:00:00Z" \
  --out audit-2026-05-02.csv
```

Le CSV peut être joint à un PV de séance, à un workflow de validation
PLU, ou archivé pour la traçabilité.

## Voir le même scénario en ligne

> 🔗 [Try it on `try.gispulse.dev/audit`](https://try.gispulse.dev/audit)

Sur le portail, chaque modification que vous faites sur la carte est
loggée en direct dans le panneau **Events**. Le bouton **Download CSV**
exporte la même chose que `gispulse audit export`.

## Sortie attendue côté portail

Section **Events** :

```text
2026-05-02T14:32:11Z  log_event  parcels#12  ok 24ms
2026-05-02T14:32:11Z  log_event  parcels#34  ok 22ms
2026-05-02T14:32:12Z  log_event  parcels#58  ok 31ms
```

Section **Audit** : table identique à celle de QGIS, filtrable par
`op_type`, `user`, plage de dates.

## Cas d'usage courants

- **Retour-arrière** : la colonne `before` contient le snapshot complet
  pour les `delete` et le diff pour les `update` — suffisant pour rejouer
  manuellement un état antérieur.
- **Compliance PLU** : exporter le CSV au moment d'une instruction de
  permis prouve que la version du zonage utilisée correspond bien à
  l'état du GPKG ce jour-là.
- **Détection de régression** : combiné à `gispulse track diff`, on
  identifie quelle modification a cassé une règle aval.

## Et après ?

- [Parcelles](/guide/walkthroughs/parcels) — exemple de règle métier
  reclassifiant des features en réaction à un DML.
- [Isochrone](/guide/walkthroughs/isochrone) — règle plus lourde
  (calcul réseau) déclenchée par le même change-log.
- La [matrice CLI ↔ Portail](/guide/symmetry) confirme que `audit export`
  côté CLI et le panneau **Audit** côté portail consomment la même table.

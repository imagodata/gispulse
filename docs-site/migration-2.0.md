---
title: Migration vers GISPulse 2.0
description: Guide de migration de gispulse 1.x vers gispulse 2.0 — ce qui change, ce qui est shimmé, et la date de retrait des shims.
---

# Guide de migration — `gispulse` 1.x → 2.0

`gispulse 2.0.0` est la première release majeure depuis `1.6.2`. Le saut
de version emballe trois chantiers qui s'étaient accumulés sur `main`
sans jamais être publiés sur PyPI (`Foundations` v1.8.0, `agrégateur
mondial` v1.9.0, et les rails data-pack). Le détail complet se trouve
dans le [Changelog](./changelog). Cette page se concentre sur **ce que
les appelants doivent savoir pour mettre à jour**.

## TL;DR — presque rien

Les deux vraies ruptures d'API (réagencement du package `gispulse.*` et
renommage `PluginHub → ExtensionHub`) sont toutes deux **shimmées** :
un projet 1.x continue de fonctionner sous 2.0 sans aucun changement de
code. Vous verrez un `DeprecationWarning` par racine d'import obsolète,
la première fois que le processus l'importe.

Les deux shims seront retirés en **2.1.0**.

## Ce qui change (et ce qui ne change pas)

| Changement | Sévérité | Shim jusqu'en 2.1.0 | Action |
|---|---|---|---|
| Imports `core.*`, `capabilities.*`, `rules.*`, `orchestration.*`, `persistence.*`, `catalog.*` déplacés sous `gispulse.*` | **soft** | redirection meta-path + un `DeprecationWarning` | optionnel — renommez vos imports quand vous voulez |
| `core.plugin_hub.PluginHub` renommé `ExtensionHub` | **soft** | alias `PluginHub = ExtensionHub` | optionnel — renommez quand vous voulez |
| Package racine `viewer/` supprimé | cosmétique | n/a (paquet vide en 1.6.2) | aucune |
| `PROTOCOL_VERSION` passe de `"1.0"` à `"1.1"` | non-cassant | n/a | aucune — `_check_protocol_version` ne fait qu'avertir depuis #182 |
| Symboles `gispulse.core.plugin_contracts` (`Tier`, `PluginManifest`, …) | non-cassant | n/a | aucune — ils n'étaient **jamais** dans `plugin_contracts` en 1.6.2, ils vivent dans `plugin_model` |

La CLI, les routers HTTP, `triggers.yaml`, et toutes les bornes de
dépendances publiées sont **inchangés** entre 1.6.2 et 2.0.0 (vérifié
contre le wheel publié).

## Renommer les imports — quand vous êtes prêt·e

```diff
- from core.plugin_hub import PluginHub
+ from gispulse.core.plugin_hub import ExtensionHub
```

```diff
- from capabilities.vector import calculate
+ from gispulse.capabilities.vector import calculate
```

```diff
- from rules.evaluator import evaluate
+ from gispulse.rules.evaluator import evaluate
```

Tout import qualifié sous les anciens packages racines (`core`,
`capabilities`, `rules`, `orchestration`, `persistence`, `catalog`) est
couvert par le shim meta-path dans `gispulse/_compat.py`. Le shim émet
un `DeprecationWarning` une seule fois par racine, à la première
importation du processus — un `grep _compat` dans les logs de test
suffit pour trouver ce qui reste sur l'ancien nom.

## Écosystème data-pack — nouveau et opt-in

`gispulse 2.0.0` ouvre la porte à des data-packs tiers distribués via
PyPI. Trois pièces importent pour les intégrateurs :

### Entry-point `gispulse.data_packs`

Les packages tiers enregistrent leurs manifests via un groupe
d'entry-point :

```toml
# pyproject.toml d'un package data-pack
[project.entry-points."gispulse.data_packs"]
my_pack = "my_pack._gispulse_entry:manifest_paths"
```

```python
# my_pack/_gispulse_entry.py
from importlib.resources import files


def manifest_paths():
    return [files("my_pack") / "manifests" / "zoning.yml"]
```

Le callable peut renvoyer soit un seul chemin, soit un itérable de
chemins (`str` n'est **pas** itéré caractère par caractère). Un pack
défectueux n'empêche jamais les autres de se charger.

### Signature de manifest (Ed25519)

Un manifest de pack peut porter un champ `signature` — la signature
Ed25519 en base64-URL de la JSON canonique du manifest **sans** ce
champ. Configuration de la clé de vérification :

```bash
# DER base64 de la clé publique Ed25519.
export GISPULSE_DATA_PACK_PUBLIC_KEY="MCowBQYD..."
# Mode strict optionnel — refuser les manifests EXTERNAL non signés.
export GISPULSE_DATA_PACK_REQUIRE_SIGNATURE=true
```

Les manifests OSS bundlés (`Origin.INTERNAL`) sont exemptés — l'arbre
OSS fait foi. Par défaut les manifests EXTERNAL non signés sont admis
(déploiement progressif).

### Type de contenu `regulatory-zoning`

Nouvelle valeur dans `DATA_PACK_CONTENTS` (`"regulatory-zoning"`),
réservée aux sources de zonage d'urbanisme. Voir `RegulatoryZoningEntry`
pour la forme déclarative et les entrées pays-par-pays livrées par
`gispulse-data-regulatory`.

## Note de numérotation — pourquoi `2.0.0` et pas `1.10.0`

En semver strict, les changements ci-dessus tiendraient dans un
`1.10.0`. Le saut `2.0.0` est un **jalon produit** : nouvelle
disposition de package, agrégateur mondial, rails data-pack. Le coût
de migration reste minimal grâce aux shims listés ci-dessus.

Les shims (redirection meta-path `_compat.py` et alias `PluginHub`)
seront retirés en **2.1.0**. Renommer vos imports avant cette
échéance est sans risque.

## Remonter un problème

Toute erreur d'import inattendue, symbole manquant ou changement de
comportement après la mise à jour — ouvrez une issue sur
[`imagodata/gispulse`](https://github.com/imagodata/gispulse/issues)
en précisant le chemin d'import 1.x utilisé et la version de
`gispulse` désormais installée.

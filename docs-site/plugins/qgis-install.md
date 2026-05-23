---
title: Installer le plugin QGIS
description: Installation pas-à-pas du plugin GISPulse (CLI bridge) sur Windows OSGeo4W, Windows Standalone, macOS et Linux.
---

# Installer le plugin QGIS

Le plugin QGIS GISPulse est un *bridge* entre QGIS et la CLI `gispulse`
publiée sur PyPI. Il **ne contient pas le moteur** — il appelle votre
installation locale de `gispulse` pour exécuter les triggers sur vos
GeoPackages.

## Prérequis

| Composant | Version minimale | Pourquoi |
|---|---|---|
| QGIS | 3.28 (LTR) | API du plugin manager + `QgsVectorFileWriter` v3 |
| Python | 3.10+ | Exigé par la CLI gispulse |
| `gispulse` (CLI) | ≥ 1.3.0 | Sous-commande `gispulse triggers run` |

## Étape 1 : installer la CLI `gispulse`

::: tabs

== Windows · OSGeo4W

QGIS installé via **OSGeo4W** embarque son propre Python. Ouvrez le
*OSGeo4W Shell* depuis le menu Démarrer puis :

```bat
pip install gispulse
gispulse --version
```

> Attention : utilisez bien **OSGeo4W Shell**, pas un PowerShell
> classique. Sinon `pip` cible un autre Python que celui de QGIS.

== Windows · Standalone

L'installeur Standalone n'expose pas de shell dédié. Installez la CLI
au niveau utilisateur dans n'importe quel terminal Python 3.10+ :

```bat
py -m pip install --user gispulse
py -m pip show gispulse
```

Vérifiez que `gispulse` est sur le `PATH` (`%APPDATA%\Python\Python3xx\Scripts`).

== macOS · Homebrew

```bash
brew install pipx
pipx install gispulse
gispulse --version
```

`pipx` isole la CLI dans son propre venv — préférable à `pip3 install`
car ça évite de polluer le Python système.

== Linux

```bash
pipx install gispulse
# ou, sans pipx :
pip install --user gispulse
gispulse --version
```

:::

## Étape 2 : installer le plugin

Tant que la soumission sur [plugins.qgis.org](https://plugins.qgis.org)
n'est pas finalisée ([#v1.4-8](https://github.com/imagodata/gispulse-enterprise/issues/474)),
installez le ZIP **depuis la GitHub Release** :

1. Téléchargez `gispulse-qgis-plugin-<version>.zip` depuis la
   [page Releases](https://github.com/imagodata/gispulse/releases)
2. Dans QGIS : **Extensions → Installer/Gérer les extensions… →
   Installer depuis un ZIP**
3. Sélectionnez le ZIP téléchargé → **Installer le plugin**

## Étape 3 : vérifier l'installation

1. Dans QGIS : **Extensions → GISPulse → Check gispulse install…**
2. Vous devriez voir : *« GISPulse `<version>` found at
   `/path/to/gispulse` »*

Si à la place vous obtenez « *GISPulse CLI was not found on this
system* » : le plugin a parfaitement chargé mais la CLI manque ou n'est
pas sur le `PATH`. Direction le guide
[**Dépannage**](/plugins/qgis-troubleshooting).

## Étape 4 : ouvrir le panneau

1. **Extensions → GISPulse → Show panel**
2. Le dock GISPulse s'ouvre à droite
3. Choisissez une layer vecteur, un fichier `rules.yml`, cliquez
   **Run trigger** — les logs s'affichent en temps réel et la layer
   est rechargée à la fin avec un résumé des changements

## Et après ?

- [Guide des règles](/guide/rules) — apprendre à écrire des règles déclaratives
- [Guide des triggers YAML](https://github.com/imagodata/gispulse/blob/main/docs/TRIGGERS_GUIDE.md) — DSL prédicats, webhook, sécurité
- [Dépannage du plugin](/plugins/qgis-troubleshooting) — solutions
  aux erreurs les plus fréquentes
- [Soumettre un bug](https://github.com/imagodata/gispulse/issues) —
  référez-vous au log produit dans `<projet>/.gispulse/runs/`

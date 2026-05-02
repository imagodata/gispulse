---
title: Dépannage — plugin QGIS
description: Diagnostiquer "gispulse not found", erreurs de permission, modules manquants, plugin qui crashe au démarrage de QGIS.
---

# Dépannage — plugin QGIS

Cette page liste les erreurs les plus fréquentes au démarrage du plugin
GISPulse, par ordre décroissant de fréquence dans le tracker.

## "gispulse not found" — diagnostic en 3 questions

C'est l'erreur n°1. Le plugin a chargé, mais ne trouve pas la CLI à
appeler. Posez-vous les questions ci-dessous **dans l'ordre** :

### 1. La CLI est-elle installée ?

Ouvrez le **même terminal** que celui où vous avez lancé `pip install
gispulse` à l'étape 1 du guide d'install :

```bash
gispulse --version
```

- ✅ « `gispulse, version 1.x.x` » → passez à la question 2
- ❌ « *command not found* » → revenez à
  [Étape 1 du guide d'install](/plugins/qgis-install#étape-1-installer-la-cli-gispulse)

### 2. Est-elle sur le `PATH` que QGIS voit ?

QGIS sous OSGeo4W et Standalone utilise un Python embarqué qui n'a
**pas le même `PATH` que votre shell**. Dans QGIS, ouvrez la console
Python (Plugins → Python Console) et tapez :

```python
import shutil; shutil.which("gispulse")
```

- ✅ Affiche un chemin → la CLI est trouvée. Cliquez « Test again »
  dans le dialog d'erreur du plugin.
- ❌ Renvoie `None` → la CLI est installée ailleurs que sur le `PATH`
  de QGIS. Voir question 3.

### 3. Quel Python le plugin utilise-t-il ?

```python
import sys; sys.executable
```

Comparez avec là où `pip install gispulse` a effectivement écrit la CLI :

```bash
pip show -f gispulse | grep -E "(Location|gispulse$)"
```

Si les deux paths divergent (ex. : `pip` vise le Python système, mais
QGIS embarque OSGeo4W Python), **réinstallez avec le bon Python** :

::: tabs

== Windows · OSGeo4W

Ouvrez bien le **OSGeo4W Shell** (pas PowerShell) avant `pip install`.

== Windows · Standalone

```bat
"C:\Program Files\QGIS 3.28\bin\python-qgis.bat" -m pip install --user gispulse
```

== macOS · Homebrew

`pipx install gispulse` doit suffire — le plugin probe `~/.local/bin`
via la variable `HOME`.

== Linux

Ajoutez `~/.local/bin` au `PATH` lancé par votre desktop manager (pas
juste votre `.bashrc`, qui n'est pas chargé pour les apps GUI).

:::

## "Permission denied" (Windows)

Survient quand `pip install gispulse` essaie d'écrire dans un
répertoire système (ex. : `C:\Program Files\…`).

**Solution** : utilisez `pip install --user gispulse` ou lancez le
*OSGeo4W Shell* en mode administrateur (clic droit → *Exécuter en tant
qu'administrateur*).

## "ModuleNotFoundError: No module named 'gdal'"

La CLI gispulse a besoin de bindings GDAL pour certaines opérations.
Sur Linux/macOS :

```bash
pip install --upgrade "gispulse[gdal]"
```

Sur Windows OSGeo4W, GDAL est déjà fourni par QGIS et ré-utilisé par la
CLI — si l'erreur persiste, vérifiez que vous avez ouvert le bon shell
(voir « gispulse not found · question 3 »).

## Le plugin crashe au démarrage de QGIS

1. Ouvrez **Vue → Panneaux → Journal des messages** dans QGIS
2. Onglet **Plugins** — la trace Python est visible
3. Copiez-la dans une [issue GitHub](https://github.com/imagodata/gispulse/issues/new)
   en y joignant : version QGIS, OS, version `gispulse --version`

## "Layer is being edited" — Save / Discard / Cancel

Le plugin refuse de lancer un trigger sur une layer encore en édition.
La modale propose trois options :

- **Save** — commite les modifications dans la layer puis lance le run
- **Discard** — annule les modifications puis lance le run
- **Cancel** — n'exécute rien (à vous de gérer manuellement)

## "Trigger succeeded but reload failed"

Le trigger a bien tourné mais QGIS n'a pas pu rafraîchir la layer (par
exemple : le GeoPackage temporaire a été supprimé). Le bouton
*Restore previous version* reste actif 5 minutes pour récupérer l'état
pré-run depuis `<projet>/.gispulse/backups/`.

## Où sont les logs ?

Pour chaque run, le plugin écrit un fichier complet dans :

```
<dossier-du-projet>/.gispulse/runs/<UTC-timestamp>.log
```

La première ligne contient la commande shell exacte exécutée — copiez-la
si vous voulez reproduire le run hors du plugin.

## Toujours bloqué ?

- Cherchez dans les [issues ouvertes](https://github.com/imagodata/gispulse/issues)
- Si l'issue n'existe pas, ouvrez-en une avec : version QGIS, OS,
  version `gispulse --version`, contenu du `.gispulse/runs/<timestamp>.log`

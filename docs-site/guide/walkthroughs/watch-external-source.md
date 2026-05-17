---
title: Walkthrough — Surveiller une source externe
description: Déclencher une action quand une source de données distante (cadastre IGN) publie un nouveau millésime. Aucune édition locale, aucun plugin SIG. Ajouté en v1.7.0.
---

# Walkthrough — Surveiller une source externe

> **Promesse** : l'IGN publie un nouveau millésime du cadastre → GISPulse le détecte au prochain sondage → votre action se déclenche (webhook, log, SQL…). Vous n'éditez rien, vous n'ouvrez aucun SIG.

Jusqu'à la v1.6.x, un trigger GISPulse réagissait à une **édition locale** (DML sur un GeoPackage, diff d'un fichier). La v1.7.0 ajoute un second mode : le trigger `source_changed`, qui réagit à la **fraîcheur d'une source distante**. C'est la brique « Extract » de la plateforme ETL (EPIC #175).

## Ce que vous allez voir

Un trigger qui surveille le **cadastre Parcellaire Express** (IGN). À chaque nouveau millésime publié, GISPulse émet un événement et POST un webhook — sans jamais télécharger les parcelles tant que vous ne le demandez pas.

| Avant | Après nouvelle révision |
|---|---|
| `gispulse watch` tourne, sonde `revision()` à la cadence `frequency` | Le jeton de révision change → événement `source.changed` → webhook POST |

## Prérequis

- `gispulse` ≥ 1.7.0 (`pipx install gispulse`)
- Le plugin source **`gispulse-src-cadastre`** : `pip install gispulse-src-cadastre`. Il enregistre la source `cadastre://` interrogée ci-dessous. Pour écrire le vôtre, voir le [guide d'authoring de source](https://github.com/imagodata/gispulse/blob/main/docs/SOURCE_PLUGIN_GUIDE.md).
- Un endpoint HTTP pour recevoir le webhook ([webhook.site](https://webhook.site/) pour un test rapide).

## Setup (≈ 1 minute)

### 1. Vérifier que la source est découverte

```bash
gispulse marketplace list --kind source
```

`cadastre` doit apparaître `active`. Si elle est `locked`, c'est un plugin d'un tier supérieur ; `failed`, une dépendance manque (`gispulse doctor`).

### 2. Écrire les règles

```yaml
# triggers.yaml
version: 1
gpkg: ./project.gpkg          # requis par le schéma v1, même inutilisé ici

triggers:
  - name: refresh_on_new_cadastre
    on:
      source_changed: cadastre://parcelles
      frequency: mensuel       # cadence de sondage de revision()
    actions:
      - type: log_event
      - type: webhook
        url: https://webhook.site/VOTRE-ID-UNIQUE

security:
  webhook_allowlist:
    - webhook.site
```

> Un trigger `source_changed` ne déclare **ni `table`, ni `when`, ni `predicate`** — il ne surveille pas une couche locale mais une source. Le schéma v1 exige tout de même une clé `gpkg:` (la base de projet) : pointez-la vers le GeoPackage de votre projet.

L'exemple complet et commenté est livré dans le dépôt : [`examples/triggers/source_changed_cadastre.yaml`](https://github.com/imagodata/gispulse/blob/main/examples/triggers/source_changed_cadastre.yaml).

### 3. Lancer la boucle de surveillance

```bash
gispulse watch ./project.gpkg --rules triggers.yaml
```

Le terminal affiche :

```text
[info] source watcher: 1 source trigger wired (cadastre://parcelles, every 24h)
[info] watching… (Ctrl+C to stop)
```

Au premier tick, le watcher lit la révision courante et la mémorise comme **référence** — il ne déclenche pas. Les déclenchements viennent ensuite, à chaque changement de jeton.

## Tester sans attendre un vrai millésime

Un millésime cadastral réel paraît une fois par mois — trop lent pour une démo. Trois façons de vérifier la boucle :

- **Réduire la cadence** : `frequency: temps-reel` sonde toutes les 5 minutes.
- **Une source de test** : écrivez un mini `gispulse-src-*` dont `revision()` renvoie l'heure courante — il « change » à chaque sondage. Voir le [guide d'authoring](https://github.com/imagodata/gispulse/blob/main/docs/SOURCE_PLUGIN_GUIDE.md).
- **Inspecter sans exécuter** : `gispulse triggers validate --config triggers.yaml` confirme que le trigger et l'URI de source sont valides.

Quand la révision change, le webhook reçoit :

```json
{
  "event": "source.changed",
  "source": "cadastre://parcelles",
  "revision": "\"a1b2c3-2026-02\"",
  "previous_revision": "\"a1b2c3-2026-01\"",
  "ts": "2026-05-17T09:00:00Z"
}
```

## Comment ça marche sous le capot

```
gispulse watch
      │
      ▼
SourceWatcherRegistry  ← un _WatchEntry par trigger source_changed
      │  toutes les `frequency` secondes
      ▼
DataSource.revision(entry_id)   ← sondage léger (HEAD HTTP, jeton ETag/millésime)
      │
      ▼
jeton différent du dernier connu ?
      │ oui
      ▼
broadcast("source.changed")  →  TriggerEvaluator  →  ActionDispatcher
```

**Sondage, pas téléchargement.** `revision()` est délibérément bon marché — pour le cadastre c'est un `HTTP HEAD` sur le `GetCapabilities` WFS, dont on lit l'en-tête `ETag` / `Last-Modified`. Aucune parcelle n'est rapatriée tant qu'une action ne le demande pas explicitement (un `fetch()`, voir le [guide ETL](https://github.com/imagodata/gispulse/blob/main/docs/SOURCE_PLUGIN_GUIDE.md)).

**Référence en mémoire.** Le dernier jeton connu vit dans le process `gispulse watch`. Au redémarrage, le premier tick re-pose la référence — pas de déclenchement fantôme, mais un millésime publié pendant l'arrêt passe inaperçu. La persistance du jeton est suivie pour une version ultérieure.

## Limitations honnêtes

- **Le jeton de référence n'est pas persistant** — un changement survenu pendant que `gispulse watch` est arrêté n'est pas rattrapé.
- **`revision()` peut renvoyer `None`** — endpoint injoignable ou sans en-tête de fraîcheur. Le watcher traite « inconnu » comme « inchangé » et ne déclenche pas (pas de faux positif).
- **`frequency` est une cadence, pas une garantie** — `mensuel` sonde toutes les 24 h ; la latence de détection est au plus un intervalle.
- **Pas de `predicate` sur un trigger source** — il se déclenche sur tout changement de révision. Filtrez côté action si besoin.

## Voir aussi

- [Guide d'authoring de source](https://github.com/imagodata/gispulse/blob/main/docs/SOURCE_PLUGIN_GUIDE.md) — écrire un paquet `gispulse-src-*`
- [Triggers Guide](https://github.com/imagodata/gispulse/blob/main/docs/TRIGGERS_GUIDE.md) — section « Source-watched triggers »
- [Walkthrough GeoJSON CDC](./geojson-cdc.md) — l'autre mode : réagir à une édition de fichier locale
- [`examples/triggers/source_changed_cadastre.yaml`](https://github.com/imagodata/gispulse/blob/main/examples/triggers/source_changed_cadastre.yaml) — l'exemple runnable

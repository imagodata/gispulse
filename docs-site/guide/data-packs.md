# Data packs

Les **data packs** sont le second régime de l'[ExtensionHub](./extension-hub),
introduit en `gispulse 2.0.0` (chantier C des Foundations v1.8.0). Un data pack
embarque **uniquement des données** — un manifeste YAML/JSON déclaratif —
jamais de code Python. Il est découvert sans rien importer, donc il est
trivialement de confiance `verified` et le filtrage par tier (`community` /
`pro` / `team` / `enterprise`) est entièrement piloté par les données.

Cette page documente :

- les deux **canaux de discovery** (bundle OSS + entry-point PyPI),
- le format `DataPackManifest`,
- le filtrage par tier et la signature **Ed25519** des manifestes externes,
- les contenus supportés (`template-pack`, `source-catalog`,
  `basemap-pack`, `projection-pack`, `regulatory-zoning`),
- le pack de référence `gispulse-data-regulatory` (FR + NL + DK).

> Si vous voulez ajouter du **code** (un nouveau capability, un router,
> un connecteur d'authentification...), reportez-vous au régime *code*
> de [l'ExtensionHub](./extension-hub).

## Pourquoi un régime data ?

Avant 2.0.0, étendre le catalogue de templates / projections / basemaps
demandait un paquet Python avec un entry-point — donc une revue de code,
un cycle de release PyPI, et un risque de chargement de code arbitraire.
Une part importante des contributions est en réalité **purement
déclarative** : un catalogue de zones d'urbanisme, une liste de basemaps,
un pack de templates métier. Le régime data-pack rend ces contributions
sûres et triviales :

- chargement **sans `import`** — pas de risque d'exécution de code,
- découverte uniforme via `ExtensionHub` (bundle OSS + entry-point + dossier),
- filtrage par `tier` géré par le moteur OSS,
- gating premium possible via signature **Ed25519** sans rien partager d'autre
  qu'une clé publique.

## Canaux de discovery

Trois canaux, fusionnés dans l'inventaire unique de `ExtensionHub` :

| Origine             | Canal                                                                 | Confiance      |
|---------------------|-----------------------------------------------------------------------|----------------|
| Bundle OSS          | `templates/manifest.yml` du repo `gispulse`                           | `first_party`  |
| PyPI tiers          | entry-point `gispulse.data_packs` (story T5, [#269](https://github.com/imagodata/gispulse/issues/269)) | `verified` si listé dans `marketplace/registry.json`, sinon `community` |
| Dossier utilisateur | variable d'env `GISPULSE_DATA_PACKS_DIR` pointant sur un répertoire de `*.yml` / `*.yaml` / `*.json` | `community`    |

Les manifestes d'origine `INTERNAL` (bundle OSS) ne passent pas par la
vérification de signature ; les `EXTERNAL` (PyPI + dossier utilisateur)
sont soumis à la politique de signature ci-dessous.

### Enregistrer un pack PyPI

```toml
# pyproject.toml d'un paquet data-pack tiers
[project.entry-points."gispulse.data_packs"]
my_pack = "my_pack._gispulse_entry:manifest_paths"
```

```python
# my_pack/_gispulse_entry.py
from importlib.resources import files


def manifest_paths():
    return [files("my_pack") / "manifests" / "zoning.yml"]
```

Le callable peut retourner soit un seul chemin (ou objet `path-like`), soit
un itérable — `str` n'est jamais déroulé caractère par caractère. Un pack
défaillant ne bloque pas les autres : l'erreur est journalisée et le
manifeste suivant continue à charger.

## Format `DataPackManifest`

Le manifeste est défini par
[`gispulse.core.plugin_model.DataPackManifest`](https://github.com/imagodata/gispulse/blob/main/src/gispulse/core/plugin_model.py).
Champs :

| Champ          | Type           | Obligatoire | Notes                                                                                            |
|----------------|----------------|-------------|--------------------------------------------------------------------------------------------------|
| `name`         | str            | **oui**     | identifiant du pack, non vide                                                                    |
| `content`      | str            | **oui**     | un de `template-pack`, `source-catalog`, `basemap-pack`, `projection-pack`, `regulatory-zoning`  |
| `version`      | str            | non         | défaut `"0.0.0"` ; libre de format                                                               |
| `display_name` | str            | non         | défaut = `name` ; libellé pour la galerie portail                                                |
| `description`  | str            | non         | description longue                                                                               |
| `tier`         | `Tier`         | non         | défaut `community` ; un des `community`, `pro`, `team`, `enterprise`                              |
| `entries`      | `list[dict]`   | non         | charges utiles spécifiques au `content` — voir le détail par type ci-dessous                      |
| `metadata`     | `dict`         | non         | étiquettes libres (jurisdiction, license, provider, ...)                                          |
| `signature`    | `str` \| None  | non         | signature Ed25519 base64-url du manifeste sans ce champ (voir [§ Signature](#signature-ed25519)) |

Un manifeste sans `name` ou avec un `content` inconnu est **rejeté**
(`ValueError`) avant tout enregistrement dans `ExtensionHub`.

### Contenus supportés

| `content`           | Description                                                                                 |
|---------------------|---------------------------------------------------------------------------------------------|
| `template-pack`     | Pipelines presets exposés par `gispulse.templates` et la galerie du portail.                |
| `source-catalog`    | Entrées du catalogue ETL (`SourceEntryRef`) ajoutées à l'agrégateur worldwide.              |
| `basemap-pack`      | Fonds de carte additionnels pour la `DualMapView` du portail.                               |
| `projection-pack`   | Définitions PROJ supplémentaires utilisables côté moteur DuckDB.                            |
| `regulatory-zoning` | Bibliothèque de zonage par pays — câblage `RegulatoryZoningEntry` (story T2 [#268](https://github.com/imagodata/gispulse/issues/268), pack `gispulse-data-regulatory`). |

## Exemple minimal (`template-pack`)

```yaml
# my_pack/manifests/templates.yml
name: my-isochrone-templates
display_name: Mon catalogue isochrones
content: template-pack
version: 1.0.0
tier: community
description: Trois presets isochrones (1, 3, 5 min) sur le réseau OSM.
entries:
  - id: isochrone-1min
    label: Isochrone 1 min
    pipeline:
      - capability: isochrone
        params: { minutes: 1 }
  - id: isochrone-3min
    label: Isochrone 3 min
    pipeline:
      - capability: isochrone
        params: { minutes: 3 }
metadata:
  jurisdiction: FR
  license: CC-BY-4.0
```

## Signature Ed25519

Story G1a ([#271](https://github.com/imagodata/gispulse/issues/271)). Les
manifestes `EXTERNAL` peuvent porter un champ `signature` — la signature
Ed25519 (encodage base64-URL sans padding) du JSON canonique du
manifeste **privé du champ `signature` lui-même** (sinon la signature
devrait s'auto-référencer). La canonicalisation réutilise
`gispulse.core.licence_format.canonicalise` (mêmes octets que pour la
licence : clés triées, JSON compact, UTF-8).

### Configuration côté moteur

```bash
# Clé publique Ed25519, DER encodée en base64.
export GISPULSE_DATA_PACK_PUBLIC_KEY="MCowBQYDK2VwAyEA..."

# Mode strict — rejeter tout manifeste EXTERNAL sans signature.
# Recommandé en CI dès qu'un déploiement utilise du contenu gated.
export GISPULSE_DATA_PACK_REQUIRE_SIGNATURE=true
```

Par défaut, `GISPULSE_DATA_PACK_REQUIRE_SIGNATURE` est `false` :
les manifestes externes non signés (community) chargent quand même. Les
manifestes du bundle OSS (`Origin.INTERNAL`) sont exempts de la
vérification — l'arbre OSS est source de vérité.

### Générer une signature (côté éditeur de pack)

Le helper `sign_manifest_dict` existe dans
[`gispulse.core.data_pack_signature`](https://github.com/imagodata/gispulse/blob/main/src/gispulse/core/data_pack_signature.py)
pour les tests de bout-en-bout. **En production**, la clé privée vit dans
le pipeline de release du pack (par exemple
`gispulse-data-regulatory`) et le verifier OSS ne fait que vérifier.

```python
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from gispulse.core.data_pack_signature import sign_manifest_dict

manifest = {
    "name": "my-pack",
    "content": "template-pack",
    "version": "1.0.0",
    "tier": "pro",
    "entries": [...],
}
private_key = Ed25519PrivateKey.generate()  # ou chargée d'un secret manager
manifest["signature"] = sign_manifest_dict(manifest, private_key)
```

> La clé privée GISPulse de référence vit dans `gispulse-enterprise`.
> Tout pack visant la marketplace officielle doit être signé avec cette
> clé via le pipeline de release contrôlé par Imagodata.

## Pack de référence — `gispulse-data-regulatory`

Le premier pack PyPI conforme est `gispulse-data-regulatory` : la
bibliothèque de zonage d'urbanisme par pays (FR + NL + DK pour la version
inaugurale, story T2 [#268](https://github.com/imagodata/gispulse/issues/268)).

- **Contenu** : `regulatory-zoning` — entrées `RegulatoryZoningEntry` câblées
  pour `gispulse-src-gpu` (FR) et les WFS nationaux NL/DK.
- **Tier** : `pro` — gated par signature Ed25519.
- **Cadence** : suit le millésime amont (annuel NL/DK, continu FR via PLU).

```bash
pip install gispulse-data-regulatory
```

Une fois installé et la clé publique configurée, le moteur enregistre
automatiquement les entrées dans l'agrégateur worldwide ; les règles
peuvent référencer la juridiction et le pays par leur libellé sans avoir
à hardcoder un endpoint WFS.

## Voir aussi

- [ExtensionHub](./extension-hub) — vue d'ensemble des deux régimes.
- [Worldwide aggregator](./worldwide-aggregator) — où atterrissent les
  entrées `source-catalog`.
- [Migration 2.0](../migration-2.0) — section « Data-pack ecosystem ».

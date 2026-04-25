---
title: Add-in ArcGIS Pro
description: Installer et utiliser l'add-in ArcGIS Pro GISPulse — datasets, jobs, OGC layers.
---

# Add-in ArcGIS Pro

L'add-in ArcGIS Pro GISPulse permet de connecter ArcGIS Pro à un backend GISPulse pour parcourir des datasets, exécuter des jobs et charger des layers OGC.

**Compatibilité :** ArcGIS Pro 3.0+

## Installation

### Depuis le fichier .esriaddin

1. Télécharger `gispulse_arcgis.esriaddin` depuis les [releases GitHub](https://github.com/gispulse/gispulse/releases)
2. Double-cliquer le fichier `.esriaddin`
3. ArcGIS Pro demande confirmation d'installation
4. Redémarrer ArcGIS Pro

### Installation manuelle (développement)

```bash
cd clients/arcgis
python makeaddin.py     # génère gispulse_arcgis.esriaddin
```

## Configuration

1. Dans ArcGIS Pro : onglet **GISPulse** dans le ruban
2. Cliquer **Connection Settings**
3. Entrer l'URL du serveur et la clé API
4. **Test** puis **OK**

## Panneaux disponibles

### Datasets Dockpane

Parcourir et uploader des datasets. Chargement comme FeatureLayer OGC ou layer PostGIS.

### Jobs Dockpane

Créer des jobs, suivre l'exécution, télécharger les résultats.

### Sessions Dockpane (Pro)

Gérer les sessions PostGIS actives.

## Outils de géotraitement

Trois outils sont disponibles dans la **Toolbox GISPulse** :

<!-- TODO: documenter les outils gp_tools -->

| Outil | Description |
|-------|-------------|
| `Upload Dataset` | Uploader la couche sélectionnée vers GISPulse |
| `Run Rules` | Exécuter des règles sur un dataset GISPulse |
| `Load OGC Layer` | Charger un dataset GISPulse comme FeatureLayer OGC |

## Chargement des layers

### Via OGC API Features

Le plugin charge les datasets GISPulse comme des FeatureLayers via le endpoint OGC :

```
http://localhost:8001/ogc/collections/{dataset_id}/items
```

### Via PostGIS (Pro)

Connection directe à la table PostGIS sous-jacente pour les datasets persistants.

## Code source

`clients/arcgis/gispulse_arcgis/`

<!-- TODO: documenter les options avancées et le manifest config.daml -->

---
title: Plugin QGIS
description: Installer et utiliser le plugin QGIS GISPulse — connexion au backend, dataset browser, jobs, OGC, MVT.
---

# Plugin QGIS

Le plugin QGIS GISPulse connecte QGIS à un backend GISPulse (local ou distant) pour parcourir les datasets, exécuter des règles et charger des layers via OGC API Features, PostGIS et MVT.

**Compatibilité :** QGIS 3.28 – 4.x

## Installation

### Depuis le gestionnaire d'extensions QGIS

1. Dans QGIS : **Extensions → Gérer et installer les extensions**
2. Rechercher **GISPulse**
3. Cliquer **Installer**

### Installation manuelle (développement)

```bash
# Cloner le dépôt
git clone https://github.com/gispulse/gispulse
cd gispulse/clients/qgis

# Construire le plugin (crée gispulse_qgis.zip)
python build_plugin.py

# Installer dans QGIS
cp -r gispulse_qgis ~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/
```

Activer le plugin dans **Extensions → Gérer et installer les extensions → Installées**.

## Configuration

Au premier démarrage, la boîte de dialogue de connexion s'ouvre :

1. **Server URL** : URL de votre backend GISPulse (ex: `http://localhost:8001` ou `https://gispulse.exemple.com`)
2. **API Key** : Clé API si votre serveur est sécurisé (laisser vide pour un serveur local sans auth)
3. Cliquer **Test Connection** pour vérifier
4. **OK** pour valider

Les paramètres sont sauvegardés dans les settings QGIS (chiffrés si une clé est définie).

## Panneaux disponibles

Le plugin ajoute quatre panneaux accessibles via **Vue → Panneaux** :

### Panneau Datasets

- Liste les datasets enregistrés sur le serveur GISPulse
- Upload d'un fichier spatial directement depuis QGIS
- Chargement d'une layer en un clic :
  - **OGC Features** — layer vecteur via l'API OGC (pagination automatique)
  - **MVT** — tuiles vectorielles pour les gros volumes
  - **PostGIS** — connexion directe si session PostGIS active (Pro)
- Prévisualisation des métadonnées (CRS, feature count, format)

### Panneau Jobs

- Créer et exécuter des jobs sur un dataset sélectionné
- Suivi en temps réel de l'exécution (barre de progression)
- Télécharger le résultat comme nouvelle layer QGIS
- Historique des jobs récents

### Panneau Triggers (Pro)

- Voir et gérer les triggers actifs sur le serveur
- Activer/désactiver un trigger
- Voir les événements récents

### Panneau Scenarios (Pro)

- Parcourir et exécuter les scénarios définis
- Suivre l'exécution du DAG

## Charger des données depuis GISPulse

### Via OGC API Features

```
Panneau Datasets → sélectionner un dataset → Charger (OGC)
```

La layer est chargée dans QGIS comme une layer vecteur WFS standard. Supporte les filtres bbox et attributaires.

### Via MVT (tuiles vectorielles)

```
Panneau Datasets → sélectionner un dataset → Charger (MVT)
```

Idéal pour les datasets > 100 000 features. La layer est chargée comme une layer tuile vectorielle MapLibre.

URL du endpoint MVT :
```
http://localhost:8001/ogc/collections/{dataset_id}/tiles/{z}/{x}/{y}.mvt
```

### Via PostGIS direct (Pro)

Quand une session PostGIS est active, le plugin peut charger les tables PostGIS directement via la connexion PostgreSQL de QGIS. Les couches restent synchronisées avec le backend.

## Exécuter un job depuis QGIS

1. Sélectionner un dataset dans le panneau Datasets
2. Aller dans le panneau Jobs
3. Choisir les règles à appliquer (ou un scénario)
4. Cliquer **Exécuter**
5. Suivre la progression
6. Une fois terminé : **Charger le résultat** pour ajouter la layer dans QGIS

## Outils de géotraitement

Le plugin expose 3 outils dans la **Boîte à outils de traitement QGIS** :

<!-- TODO: documenter les outils de géotraitement exposés -->

| Outil | Description |
|-------|-------------|
| `GISPulse — Run Rules` | Exécuter des règles sur la layer active |
| `GISPulse — Upload Dataset` | Uploader la layer active vers GISPulse |
| `GISPulse — Export Result` | Télécharger le résultat d'un job |

## Dépannage

### "Connection refused"
Vérifiez que `gispulse portal` tourne sur le port indiqué.

### "Unauthorized"
Vérifiez la clé API dans les paramètres du plugin (**Extensions → GISPulse → Settings**).

### Layers MVT vides
Le endpoint MVT nécessite que le dataset soit indexé côté serveur. Essayez d'abord le chargement OGC.

## Développement du plugin

Voir [Développer un plugin](/plugins/developing) pour contribuer ou créer votre propre plugin GISPulse.

Code source : `clients/qgis/gispulse_qgis/`

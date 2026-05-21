---
title: FAQ
description: Questions frequemment posees sur GISPulse — moteur geospatial declaratif, formats, moteurs, migration, securite.
---

# FAQ

Questions frequemment posees sur GISPulse. Pour les questions liees aux tarifs, consultez la [FAQ pricing](/pricing#faq).

---

## Qu'est-ce que GISPulse ?

GISPulse est un moteur geospatial declaratif open-source (AGPL-3.0). Il permet d'appliquer des traitements spatiaux (buffer, jointure, filtre, clip, etc.) sur des datasets via des regles JSON, sans ecrire de code.

Pensez-y comme **dbt pour le geospatial** : vous declarez vos transformations, GISPulse les execute de maniere reproductible et versionnable.

---

## Qu'est-ce que le "rules-as-config" ?

C'est le principe fondamental de GISPulse : les operations spatiales sont declarees dans des fichiers JSON (les "regles"), pas dans du code Python ou SQL. Ces regles sont :

- **Versionnables** avec Git, comme du code
- **Lisibles** par un non-developpeur
- **Portables** entre moteurs (DuckDB, PostGIS)
- **Composables** en pipelines ordonnees
- **Validables** automatiquement (`gispulse validate`)

```json
{
  "name": "buffer_protection",
  "capability": "buffer",
  "config": { "distance": 100, "order": 0 }
}
```

---

## En quoi GISPulse differe-t-il de FME ?

| | GISPulse | FME |
|---|---------|-----|
| **Licence** | Open-source AGPL-3.0 | Proprietaire ($$$) |
| **Approche** | Regles JSON declaratives | Workbench visuel |
| **Versionnement** | Git natif (fichiers JSON) | Fichiers binaires .fmw |
| **Moteurs** | DuckDB / PostGIS / Python | Moteur proprietaire |
| **CI/CD** | Integrable directement | Necessite FME Flow |
| **Extensibilite** | Capabilities Python ouvertes | Transformers proprietaires |
| **Prix** | Gratuit (Community) / 79 EUR/mois (Pro) | A partir de ~4 000 EUR/an |

GISPulse est plus adapte aux equipes qui veulent des pipelines spatiaux versionnables, reproductibles et integres dans un workflow DevOps. FME reste pertinent pour les ETL complexes multi-formats avec son ecosysteme de transformers.

---

## En quoi GISPulse differe-t-il de QGIS Processing ?

QGIS Processing est un framework d'execution d'algorithmes a l'interieur de QGIS Desktop. GISPulse est un moteur autonome :

- **Headless** : tourne en CLI, API, CI/CD sans interface graphique
- **Declaratif** : regles JSON au lieu de scripts Python
- **Multi-moteurs** : DuckDB et PostGIS en plus de Python
- **Pipeline natif** : enchainement ordonne de regles avec execution DAG (Pro)

Le plugin QGIS de GISPulse permet d'utiliser le moteur directement depuis QGIS.

---

## En quoi GISPulse differe-t-il de PostGIS seul ?

PostGIS est un moteur SQL spatial. GISPulse l'utilise comme backend mais ajoute :

- **Abstraction declarative** : pas besoin d'ecrire du SQL
- **Portabilite** : le meme pipeline tourne sur DuckDB (local) ou PostGIS (serveur)
- **Orchestration** : DAG, triggers, cron integres
- **Interfaces** : CLI, API REST, SDK, plugins SIG
- **Versionnement** : les regles sont des fichiers JSON, pas des scripts SQL eparpilles

---

## Quels formats de donnees sont supportes ?

### En entree

| Format | Extension | Moteur |
|--------|-----------|--------|
| GeoPackage | `.gpkg` | Tous |
| Shapefile | `.shp` | Tous |
| GeoJSON | `.geojson` | Tous |
| GeoParquet | `.parquet` | DuckDB (natif) |
| CSV avec coordonnees | `.csv` | Tous |
| PostGIS table | — | PostGIS |

### En sortie

| Format | Extension |
|--------|-----------|
| GeoPackage | `.gpkg` |
| GeoJSON | `.geojson` |
| Shapefile | `.shp` |
| GeoParquet | `.parquet` |
| PostGIS table | — |

::: tip Format recommande
Le **GeoPackage** est le format recommande : fichier unique, multi-couches, metadonnees, pas de limitation de noms de colonnes.
:::

---

## Peut-on utiliser GISPulse sans PostGIS ?

Oui. Le tier **Community** fonctionne entierement sans PostGIS, avec les moteurs Python (GeoPandas) et DuckDB. PostGIS est uniquement necessaire pour :

- Le mode persistant (stockage serveur)
- Les triggers temps reel (`pg_notify`)
- Les pipelines cron
- Le mode hybride DuckDB + PostGIS

```bash
# Fonctionne sans PostGIS
gispulse run data.gpkg --rules rules.json -o output.gpkg --engine duckdb
```

---

## GISPulse est-il pret pour la production ?

Oui. **GISPulse v2.0.0** est la version stable courante : 118 capabilities, 3 600+ tests, moteur multi-backend DuckDB / PostGIS, metriques Prometheus, RBAC, SSO (OIDC / SAML), audit log et stockage S3. La CLI, l'API REST, le SDK Python, le plugin QGIS, l'add-in ArcGIS et le client desktop Tauri sont tous livres. La v2.0.0 ajoute l'ExtensionHub a 2 regimes (code plugins + data packs), l'agregateur geo mondial (4 fetchers) et le serveur MCP.

| Composant | Statut |
|-----------|--------|
| Moteur (DuckDB / PostGIS / GPKG portable) | **Stable — v2.0.0** |
| CLI | **Stable** |
| API REST + SDK Python | **Stable** |
| 118 capabilities (vecteur, attributs, classification, stats, topologie, 3D pointcloud, raster, reseau, PostGIS SQL) | **Stable** |
| Plugin QGIS / Add-in ArcGIS / Desktop Tauri | **Stable** |
| Portal web (single-user Community, multi-user Pro / Team / Enterprise) | **Stable** |
| Visual node editor | **Beta** |

---

## Comment migrer depuis FME ?

Il n'existe pas d'outil de migration automatique FME -> GISPulse. La migration se fait par re-expression des transformations :

1. **Inventoriez** vos workbenches FME et les transformers utilises
2. **Mappez** chaque transformer vers une capability GISPulse (buffer, filter, clip, spatial_join, etc.)
3. **Reecrivez** chaque workbench en fichier de regles JSON
4. **Validez** avec `gispulse validate rules.json`
5. **Testez** sur un echantillon avec `gispulse run`

La plupart des pipelines FME spatiaux se transposent directement. Les transformers sans equivalent peuvent etre implementes comme [capabilities custom](/plugins/developing).

---

## Peut-on ecrire des capabilities personnalisees ?

Oui. Toute classe Python qui herite de `Capability` et s'enregistre via le decorateur `@register` devient disponible dans les regles :

```python
from capabilities.base import Capability
from capabilities.registry import register

@register
class MonTraitement(Capability):
    name = "mon_traitement"
    description = "Description de mon traitement"
    schema = {
        "type": "object",
        "properties": {
            "seuil": {"type": "number", "default": 100}
        }
    }

    def execute(self, gdf, config, **kwargs):
        # logique metier ici
        return gdf
```

Voir le guide complet : [Developper un plugin](/plugins/developing).

---

## Comment fonctionne le double moteur DuckDB / PostGIS ?

GISPulse abstrait le moteur d'execution derriere une interface commune. Le meme fichier de regles JSON produit le meme resultat quel que soit le moteur :

```
Regles JSON ─── Moteur Python (GeoPandas) ─── Resultat
            ├── Moteur DuckDB (spatial)    ─── Resultat (identique)
            └── Moteur PostGIS (SQL)       ─── Resultat (identique)
```

Le choix du moteur depend du volume, de l'environnement et du tier :

- **Python** : defaut, < 50k features, toujours disponible
- **DuckDB** : 50k - 10M features, pas de serveur, Community
- **PostGIS** : persistance, triggers, multi-user, Pro+

Certaines capabilities basculent automatiquement sur DuckDB quand le volume depasse 50 000 features.

Voir la documentation complete : [Moteurs d'execution](/guide/engines).

---

## Qu'en est-il de la securite des donnees ?

GISPulse traite les donnees **localement** par defaut. Aucune donnee n'est envoyee vers un service cloud.

- **Mode portable** (DuckDB) : tout reste sur votre machine
- **Mode persistant** (PostGIS) : les donnees sont dans votre instance PostgreSQL, que vous controlez
- **API REST** (Pro) : authentification par cle API, HTTPS
- **RBAC** (Team) : roles et permissions granulaires
- **SSO** (Enterprise) : SAML / OIDC pour l'authentification centralisee
- **On-premise** (Enterprise) : deploiement air-gapped sans acces Internet

::: tip
GISPulse n'a pas de service cloud heberge. Vos donnees ne quittent jamais votre infrastructure.
:::

---

## Ou obtenir de l'aide ?

| Canal | Usage |
|-------|-------|
| [GitHub Discussions](https://github.com/gispulse/gispulse/discussions) | Questions, idees, retours d'experience |
| [GitHub Issues](https://github.com/gispulse/gispulse/issues) | Bugs et demandes de fonctionnalites |
| [Documentation](/getting-started/quickstart) | Guides et reference |
| [contact@gispulse.dev](mailto:contact@gispulse.dev) | Contact commercial et partenariats |
| Support prioritaire (Team/Enterprise) | Reponse garantie 48h / 4h |

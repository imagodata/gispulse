---
title: "Predicats, agregations, triggers : le trio qui remplace vos scripts GIS"
description: "Comment trois concepts declaratifs — predicats geometriques, agregations spatiales et triggers temps-reel — eliminent des centaines de lignes de code dans vos pipelines geospatiaux."
head:
  - - meta
    - name: keywords
      content: "predicats geometriques, agregation spatiale, triggers PostGIS, spatial join, intersects, within, rules-as-config, GISPulse, pipeline geospatial, automatisation GIS"
  - - meta
    - property: og:title
      content: "Predicats, agregations, triggers : le trio qui remplace vos scripts GIS"
  - - meta
    - property: og:description
      content: "Trois concepts declaratifs pour eliminer des centaines de lignes de code dans vos pipelines geospatiaux."
  - - meta
    - property: og:type
      content: article
---

# Predicats, agregations, triggers : le trio qui remplace vos scripts GIS

Ouvrez n'importe quel projet SIG. Cherchez les scripts de traitement. Vous trouverez des centaines de lignes de Python qui font toujours la meme chose : croiser deux couches, compter des entites par zone, relancer un calcul quand une donnee change.

Ces trois operations — **predicats geometriques**, **agregations spatiales**, **triggers** — representent facilement 80% du code de vos pipelines. Et ce code est fragile, difficile a maintenir, illisible pour quiconque ne l'a pas ecrit.

GISPulse les remplace par trois blocs JSON declaratifs.

---

## 1. Predicats geometriques : la question spatiale en un mot

Un predicat geometrique, c'est une question binaire entre deux geometries. Est-ce que A croise B ? Est-ce que A est contenu dans B ? Est-ce que A touche B ?

En Python avec GeoPandas, ca ressemble a ca :

```python
import geopandas as gpd

batiments = gpd.read_file("batiments.gpkg")
zones_inondables = gpd.read_file("zones_inondables.gpkg")

# Reprojection manuelle si les CRS different
if batiments.crs != zones_inondables.crs:
    zones_inondables = zones_inondables.to_crs(batiments.crs)

# Jointure spatiale
batiments_exposes = gpd.sjoin(
    batiments,
    zones_inondables,
    how="inner",
    predicate="intersects"
)

# Nettoyage des colonnes
batiments_exposes = batiments_exposes[
    ["geometry", "id_batiment", "adresse", "alea", "hauteur_eau_max"]
]

# Export
batiments_exposes.to_file("batiments_exposes.gpkg", driver="GPKG")
```

20 lignes. Import, lecture, reprojection, jointure, nettoyage, export. Et on n'a gere aucune erreur, aucun log, aucun cas limite.

Avec GISPulse :

```json
{
  "name": "batiments_exposes",
  "capability": "spatial_join",
  "params": {
    "input": "batiments.gpkg",
    "ref_layer": "zones_inondables.gpkg",
    "predicate": "intersects",
    "columns": ["alea", "hauteur_eau_max"]
  }
}
```

Le moteur gere la reprojection, le filtrage des colonnes, le format de sortie. Vous ne declarez que l'intention : **quelles entites croisent quelles zones, et quelles informations recuperer**.

### Les predicats disponibles

| Predicat | Question posee |
|----------|---------------|
| `intersects` | A et B ont-ils une partie commune ? |
| `within` | A est-il entierement contenu dans B ? |
| `contains` | A contient-il entierement B ? |
| `crosses` | A traverse-t-il B ? |
| `touches` | A et B se touchent-ils sans se chevaucher ? |
| `overlaps` | A et B se chevauchent-ils partiellement ? |

Ces predicats suivent la norme **OGC Simple Features**. Ils sont universels — ce sont les memes que ceux de PostGIS, Shapely, JTS, GEOS. La difference : ici, vous les declarez au lieu de les coder.

---

## 2. Agregations spatiales : compter, sommer, moyenner par zone

Le predicat repond a une question geometrique. L'agregation repond a une question statistique : **combien ? quelle somme ? quelle moyenne ?**

Le cas classique : vous avez des batiments exposes et vous voulez des statistiques par quartier. En Python :

```python
import geopandas as gpd

batiments_exposes = gpd.read_file("batiments_exposes.gpkg")
quartiers = gpd.read_file("quartiers.gpkg")

# Jointure spatiale batiments -> quartiers
joined = gpd.sjoin(batiments_exposes, quartiers, how="inner", predicate="within")

# Agregation manuelle
stats = joined.groupby("nom_quartier").agg(
    nb_batiments=("id_batiment", "count"),
    population_exposee=("population", "sum"),
    hauteur_max=("hauteur_eau_max", "max")
).reset_index()

# Re-joindre les geometries des quartiers
result = quartiers.merge(stats, on="nom_quartier", how="left")
result = result.fillna(0)

# Export
result.to_file("exposition_par_quartier.gpkg", driver="GPKG")
```

25 lignes. Jointure, groupby, aggregation, merge, fillna, export. Et si vous changez de zone de reference — arrondissements au lieu de quartiers — vous reecrivez la moitie.

Avec GISPulse :

```json
{
  "name": "exposition_par_quartier",
  "capability": "spatial_aggregate",
  "params": {
    "input": "batiments_exposes",
    "ref_layer": "quartiers.gpkg",
    "predicate": "within",
    "agg": {
      "id_batiment": "count",
      "population": "sum",
      "hauteur_eau_max": "max"
    }
  }
}
```

Changer de maille ? Remplacez `quartiers.gpkg` par `arrondissements.gpkg`. C'est un parametre, pas une reecriture.

### Les fonctions d'agregation

| Fonction | Usage |
|----------|-------|
| `count` | Nombre d'entites par zone |
| `sum` | Somme d'un attribut numerique |
| `mean` | Moyenne |
| `min` / `max` | Extremes |
| `median` | Mediane |
| `std` | Ecart-type |

Combinables librement. Vous pouvez agreger 10 champs differents dans la meme regle.

---

## 3. Triggers : le pipeline qui tourne tout seul

Les predicats et les agregations repondent a des questions. Les triggers repondent a une autre : **quand recalculer ?**

Dans un workflow classique, c'est un cron job, un script lance a la main, ou pire — quelqu'un qui se souvient de relancer le traitement apres une mise a jour des donnees.

Avec GISPulse en mode PostGIS :

```json
{
  "trigger": "on_update",
  "layer": "zones_inondables",
  "rule": "batiments_exposes",
  "engine": "postgis"
}
```

Le modele hydro se met a jour. La regle `batiments_exposes` se declenche. L'agregation en aval se recalcule. Le tableau de bord suit.

Pas de cron. Pas de glue code. Pas d'orchestrateur externe.

### Types de triggers

| Trigger | Declenchement |
|---------|--------------|
| `on_insert` | Une nouvelle entite est ajoutee a la couche |
| `on_update` | Une entite existante est modifiee |
| `on_delete` | Une entite est supprimee |
| `on_schedule` | Execution planifiee (cron) |

Les triggers chainables : le resultat d'un trigger peut declencher le suivant. C'est comme ca que vous construisez des pipelines reactifs sans ecrire une ligne d'orchestration.

---

## Le pipeline complet : 3 regles, zero code

Reprenons l'exemple depuis le debut. Objectif : identifier les batiments en zone inondable, calculer les statistiques par quartier, et recalculer automatiquement quand le modele de risque change.

```json
[
  {
    "name": "batiments_exposes",
    "capability": "spatial_join",
    "params": {
      "input": "batiments.gpkg",
      "ref_layer": "zones_inondables.gpkg",
      "predicate": "intersects",
      "columns": ["alea", "hauteur_eau_max"]
    }
  },
  {
    "name": "exposition_par_quartier",
    "capability": "spatial_aggregate",
    "params": {
      "input": "batiments_exposes",
      "ref_layer": "quartiers.gpkg",
      "predicate": "within",
      "agg": {
        "id_batiment": "count",
        "population": "sum",
        "hauteur_eau_max": "max"
      }
    }
  },
  {
    "trigger": "on_update",
    "layer": "zones_inondables",
    "rule": "batiments_exposes",
    "engine": "postgis"
  }
]
```

3 regles. Lisibles par un charge SIG qui n'a jamais ouvert un terminal. Versionnables sous Git. Executables sur DuckDB en local ou PostGIS en production.

Le script Python equivalent ? 60 a 80 lignes, sans la gestion d'erreurs, sans les triggers, sans le mode portable.

---

## Pourquoi c'est important

Le probleme n'est pas technique. GeoPandas marche. PostGIS marche. Vos scripts marchent — jusqu'a ce qu'ils ne marchent plus.

Le probleme, c'est que :

- **Personne d'autre ne peut les lire.** Un script de jointure spatiale ecrit par un dev il y a 18 mois est illisible pour l'equipe SIG qui doit le modifier.
- **Ils ne sont pas portables.** Le script tourne sur une machine avec la bonne version de Python, les bonnes libs, le bon CRS. Changez un element, il casse.
- **Ils ne reagissent pas.** Vos donnees changent tous les jours. Vos scripts tournent une fois par semaine. L'ecart grandit.

Les regles declaratives resolvent les trois problemes :

1. **Lisibilite** — Un JSON avec `"predicate": "intersects"` est comprehensible sans contexte.
2. **Portabilite** — Le meme fichier tourne sur DuckDB (zero install) ou PostGIS (production).
3. **Reactivite** — Les triggers ferment la boucle entre la donnee et le traitement.

---

## En resume

| | Scripts Python | GISPulse |
|---|---|---|
| **Predicats** | `gpd.sjoin(..., predicate="intersects")` | `"predicate": "intersects"` |
| **Agregations** | `groupby().agg()` + merge + fillna | `"agg": { "field": "sum" }` |
| **Triggers** | cron + script + monitoring | `"trigger": "on_update"` |
| **Lisible par un non-dev** | Non | Oui |
| **Portable DuckDB / PostGIS** | Non | Oui |
| **Versionnable Git** | Oui (mais illisible en diff) | Oui (diff lisible) |

---

## Bientot disponible

GISPulse est developpe par **Imagodata** et sera disponible prochainement en open source (AGPL-3.0).

Pour un acces anticipe : [contact@gispulse.dev](mailto:contact@gispulse.dev)

Pour decouvrir le projet : [gispulse.dev](https://imagodata.github.io/gispulse/)

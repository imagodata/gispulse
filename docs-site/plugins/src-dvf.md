# gispulse-src-dvf

Source de transactions immobilières françaises (DVF) pour GISPulse — miroir GeoParquet géo-enrichi d'Etalab (domaine `STATISTIQUE`, juridiction `FR`).

## Fournisseur

| Champ                | Valeur                                                                |
|----------------------|-----------------------------------------------------------------------|
| Producteur amont     | DGFiP (Direction générale des finances publiques)                     |
| Provider (runtime)   | Etalab (`metadata.provider = "Etalab"`)                               |
| Redistributeur       | data.gouv.fr / `files.data.gouv.fr`                                   |
| Jeu de données       | Demandes de Valeurs Foncières (DVF)                                   |
| Miroir               | `files.data.gouv.fr/geo-dvf` (GeoParquet géo-enrichi)                 |
| Licence              | Licence Ouverte 2.0                                                   |
| Cadence              | Semestrielle (avril / octobre, fenêtre glissante 5 ans)               |

## Entrées

| id          | Libellé                                       | AccessProtocol  | Endpoint                                                               | Payload | Juridiction |
|-------------|-----------------------------------------------|-----------------|------------------------------------------------------------------------|---------|-------------|
| `mutations` | Mutations DVF (transactions immobilières)     | `REMOTE_TABLE`  | `https://files.data.gouv.fr/geo-dvf/latest/parquet/full.parquet`       | TABLE   | FR          |

Schéma (les noms de champs reflètent l'en-tête CSV geo-dvf d'Etalab) :

`id_mutation`, `date_mutation`, `nature_mutation`, `valeur_fonciere`, `type_local`, `surface_reelle_bati`, `surface_terrain`, `code_commune`, `nom_commune`, `code_departement`, `prefixe_section`, `section`, `numero_plan`, `id_parcelle` (clé de jointure synthétisée), `longitude`, `latitude`.

L'adaptateur `AccessProtocol.REMOTE_TABLE` est porté par `GeoParquetS3Fetcher` (A3, issue [#229](https://github.com/imagodata/gispulse/issues/229), embarqué dans le cœur depuis la v1.9.0) : il scanne `full.parquet` via DuckDB `read_parquet` + `httpfs` avec push-down du prédicat bbox — une requête foncière sur un département touche quelques Mo du fichier national, pas 2 Go.

## Revision

`revision(entry_id)` exécute un seul appel **HTTP GET** sur l'API métadonnées du jeu de données data.gouv.fr :

```
https://www.data.gouv.fr/api/1/datasets/demandes-de-valeurs-foncieres/
```

Le champ `last_modified` (ISO-8601, racine de la réponse JSON) sert de jeton de fraîcheur. Une requête HEAD n'est pas utilisée parce que l'edge `static.data.gouv.fr` n'expose ni `ETag` ni `Last-Modified` pour les ressources. Retourne `None` — « fraîcheur inconnue » — sur erreur réseau, réponse non-2xx, JSON malformé ou champ absent.

## Usage

```python
from gispulse.plugins.api import get_catalog_entry

entry = get_catalog_entry("dvf", "mutations")
# entry.access.protocol → AccessProtocol.REMOTE_TABLE
# entry.access.endpoint → "https://files.data.gouv.fr/geo-dvf/latest/parquet/full.parquet"
# entry.access.format   → "application/parquet"
```

Le plugin s'enregistre automatiquement via le entry-point `gispulse.data_sources` à l'installation :

```bash
pip install gispulse-src-dvf
```

## Références

- Issue amont : [#184](https://github.com/imagodata/gispulse/issues/184) (vague 2 du pilote — source `Payload.TABLE`)
- Issue amont : [#198](https://github.com/imagodata/gispulse/issues/198) (sonde de fraîcheur `revision()`)
- Issue amont : [#229](https://github.com/imagodata/gispulse/issues/229) (`GeoParquetS3Fetcher` — transport `REMOTE_TABLE`, A3)
- PR mergée : [#223](https://github.com/imagodata/gispulse/pull/223)
- EPIC : [#175](https://github.com/imagodata/gispulse/issues/175)
- Portail data : <https://www.data.gouv.fr/fr/datasets/demandes-de-valeurs-foncieres/>

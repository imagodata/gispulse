# Worldwide aggregator

L'**agrégateur géo mondial** est une source `gispulse` first-party,
livrée en `1.9.0` (EPIC [#226](https://github.com/imagodata/gispulse/issues/226))
et publiée sur PyPI dans la `2.0.0`. Plutôt que de multiplier les
paquets marketplace pour chaque dataset public, GISPulse maintient un
**catalogue curé** dans le repo (`core/data/worldwide_catalog.yml`) et
une `WorldwideCatalogSource` unique qui le matérialise au runtime.

> Design `#226`, décision 2 — *le catalogue worldwide est gratuit* :
> publié sous `gispulse.data_sources` dans la distribution `gispulse`
> elle-même, donc `ExtensionHub` le résout en `first-party` et le gate
> communauté passe sans code spécifique.

## Concept

```
core/data/worldwide_catalog.yml      ← source de vérité (YAML curé)
              │
              ▼
WorldwideCatalogSource               ← une DeclarativeSource first-party
              │
              ▼ (.fetch())
LazyFetcher (4 adapters)             ← un par AccessProtocol
              │
              ▼
DuckDB / GDAL / WFS                  ← exécution paresseuse + push-down bbox
```

Chaque entrée YAML devient un `SourceEntryRef` portant les **quatre axes
de filtrage** du catalogue (issue
[#227](https://github.com/imagodata/gispulse/issues/227)) :

| Axe              | Valeur                                                                |
|------------------|-----------------------------------------------------------------------|
| `domain`         | `SourceDomain` (`base`, `observation`, `elevation`, …)                |
| `payload`        | `Payload` (`vector`, `raster`, `pointcloud`, `tiles`, `table`)        |
| `jurisdiction`   | ISO 3166-1 / `world` / `eu`                                           |
| `access.protocol`| `AccessProtocol` (`remote-table`, `ogc-features`, `stac`, `http-file`) |

Plus un groupage `family` (en `metadata`) pour la galerie portail.

## Les quatre fetchers

Quatre familles d'adapters, un par `AccessProtocol`, tous héritant de
`LazyFetcher`. Le moteur DuckDB ne télécharge **rien** à l'instanciation
— la scan est paresseuse, le push-down `bbox` est généré côté SQL.

| Protocole         | Fetcher                | Issue                                                                 | Notes                                                                                                              |
|-------------------|------------------------|-----------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------|
| `remote-table`    | `GeoParquetS3Fetcher`  | [#229](https://github.com/imagodata/gispulse/issues/229) (A3)         | `read_parquet('s3://…', hive_partitioning=true)` + push-down bbox sur la struct Overture. DuckDB `httpfs`.         |
| `ogc-features`    | `OGCFeaturesFetcher`   | [#230](https://github.com/imagodata/gispulse/issues/230) (A4)         | OGC API Features / WFS. `ST_Read(...)` lazy + COPY via le client WFS pour matérialiser.                            |
| `stac`            | `STACFetcher`          | [#231](https://github.com/imagodata/gispulse/issues/231) (A5)         | Recherche STAC + `download_asset` du COG ; lecture raster à la demande.                                            |
| `http-file`       | `HttpFileFetcher`      | [#232](https://github.com/imagodata/gispulse/issues/232) (A6)         | Fichier vectoriel/raster public exposé via `/vsicurl/` GDAL ; téléchargement streamé.                              |

### Push-down `bbox`

Chaque fetcher implémente `_scan_sql(extent)` qui prend l'`Extent`
demandé par la règle et l'injecte dans la requête DuckDB. Conséquence
mesurable : une requête départementale sur Overture Buildings touche
quelques **Mo**, pas les 2 Go du parquet mondial.

## Sécurité SSRF

Les endpoints sont contrôlés **structurellement à la lecture** du YAML
(schème allow-listé, hôte non privé/loopback), sans aucune résolution
DNS — l'import du module reste *network-free*. Le garde DNS-resolving
complet (issue [#199](https://github.com/imagodata/gispulse/issues/199))
s'exécute **par fetch** à l'intérieur du `LazyFetcher`, donc un attaquant
ne peut pas faire chevaucher un nom DNS public au load et un IP privé à
l'exécution.

Schèmes acceptés à la lecture : `http`, `https`, `s3`.

## Le catalogue curé

`core/data/worldwide_catalog.yml` ouvre avec quatre familles initiales :

| Famille                  | Dataset typique             | Protocole         |
|--------------------------|-----------------------------|-------------------|
| `overture-geoparquet`    | Overture Places / Buildings | `remote-table`    |
| `ogc-features`           | INSPIRE & WFS publics       | `ogc-features`    |
| `stac-imagery`           | Sentinel-2, MS Planetary    | `stac`            |
| `opendata-fr`            | Vecteurs FR (data.gouv)     | `http-file`       |

Chaque entrée porte un `revision_token` immuable (pour un release
versionné) ou `null` (service live) — A14
([#240](https://github.com/imagodata/gispulse/issues/240)) câble la
sonde de fraîcheur dans le `SourceWatcherRegistry`.

## Exemple — interroger l'agrégateur en Python

```python
from gispulse.plugins.api import get_source

worldwide = get_source("worldwide-catalog")

# Lister une famille
overture = [
    e for e in worldwide.entries()
    if e.metadata.get("family") == "overture-geoparquet"
]

# Récupérer une entrée
entry = worldwide.catalog().lookup("overture-buildings")
# entry.access.protocol → AccessProtocol.REMOTE_TABLE
# entry.access.endpoint → "s3://overturemaps-us-west-2/release/..."
# entry.payload         → Payload.VECTOR
# entry.jurisdiction    → "world"
```

## Exemple — ajouter une source à l'agrégateur

Ajouter une entrée au catalogue revient à ajouter une stanza YAML :

```yaml
# core/data/worldwide_catalog.yml
entries:
  - id: my-public-dataset
    name: Mon dataset public
    family: opendata-fr
    domain: environnement
    payload: vector
    jurisdiction: FR
    access:
      protocol: http-file
      endpoint: https://data.gouv.fr/.../layer.gpkg
      format: application/x-sqlite3
    revision_token: null
    metadata:
      provider: Mon Ministère
      license: Licence Ouverte 2.0
```

L'entrée est immédiatement disponible à `entries()` / `catalog()`. Pour
distribuer hors du repo, voir le contenu
[`source-catalog`](./data-packs#contenus-supportés) des data packs —
l'ajout passe alors par un `DataPackManifest` plutôt qu'un fork.

## CLI / portail

- `gispulse sources list` — inventaire des sources `ExtensionHub`, dont
  `worldwide-catalog`.
- `gispulse sources catalog worldwide-catalog --family ogc-features` —
  filtre par famille (CLI).
- Portail : panneau « Catalogue mondial » avec filtres `domain` /
  `payload` / `jurisdiction` / `protocol` ([symétrie CLI ↔
  portail](./symmetry)).

## Références code

- [`gispulse.plugins.worldwide_source.WorldwideCatalogSource`](https://github.com/imagodata/gispulse/blob/main/src/gispulse/plugins/worldwide_source.py)
- [`gispulse.core.fetchers/*`](https://github.com/imagodata/gispulse/tree/main/src/gispulse/core/fetchers)
- [`core/data/worldwide_catalog.yml`](https://github.com/imagodata/gispulse/blob/main/src/gispulse/core/data/worldwide_catalog.yml)
- EPIC [#226](https://github.com/imagodata/gispulse/issues/226) — sprint v1.9.0
- Issue [#199](https://github.com/imagodata/gispulse/issues/199) — SSRF guard

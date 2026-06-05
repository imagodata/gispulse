# Plan de travail — gispulse comme socle de MILOU (branche `milou`)

> Objectif : MILOU (moteur de pré-dimensionnement réseau fibre belge) devient **client
> hybride de gispulse**. gispulse fournit l'**I/O géo, la persistence GeoPackage, DuckDB/
> spatial et la lecture R2/S3** ; MILOU garde la **logique fibre** (terrain, oil-slick, rings,
> costing, livrables, confidentiel `input_client`).
>
> Branche `milou` créée depuis `main` + cherry-pick `c451a64` (config secret S3/R2 → DuckDB).
> Workflow : push sur `origin` (fork `superWorldSavior/gispulse`) → PR vers `imagodata/gispulse`.
> Issues côté MILOU : `imagodata/milou#5`→`#10`.

## 1. Déjà utilisable (rien à coder)
- `gispulse.persistence.io.read_vector` — GPKG/GeoJSON/SHP/FGB/KML/CSV/XLSX/Parquet (`bbox`, `rows`, `layer`, `crs`).
- `gispulse.persistence.gpkg` — `list_layers`, `read_gpkg`, `write_gpkg`, `read_all_layers`, `write_all_layers`.
- `gispulse.persistence.duckdb_engine.DuckDBSession` — `spatial` + `httpfs` + **secret S3/R2** (déjà testé : endpoint Garage/R2, credentials, escaping, scope bucket).
- Config R2 via `GISPULSE_S3_*` (`gispulse.core.config`).
- Pattern de référence (NE PAS importer en bloc) : cube DuckDB de `gispulse-foncier` — scan Parquet paramétré, `read_parquet(..., union_by_name=true)`, géométrie normalisée.

## 2. À durcir / ajouter côté gispulse
- [ ] **KMZ réel** dans `persistence.io` : `.kmz` → unzip → `doc.kml` (fallback 1er `.kml`) → GeoDataFrame WGS84. Aujourd'hui `.kmz` n'est qu'inféré côté engine CDC, pas lu par `read_vector`.
- [ ] **XLSX projeté CRS-explicite** : ajouter `x_col` / `y_col` / `source_crs` / `target_crs` ; **refuser l'inférence naïve `X/Y → EPSG:4326`** (les fichiers Orange ont des **X/Y en Lambert 72 / EPSG:31370**).
- [ ] **Déclarer `openpyxl`** (extra `xlsx` ou dépendance directe) — `pandas.read_excel` est utilisé mais la dépendance n'est pas déclarée.
- [ ] **Helpers DuckDB/R2** : builders sûrs `read_parquet('s3://…')` (+ `union_by_name`), matérialisation GPKG distante en cache local, helper `ST_DWithin` métrique.
- [ ] **Conventions GPKG de pipeline** : helper de noms de couches + manifest (`feature_count`, `source`, `crs`, `params_hash`).
- [ ] **Doc API cliente minimale** (exemples sans règles métier MILOU).

## 3. API cliente cible (ce que MILOU importera)
```python
from gispulse.persistence import read_vector, write_vector, DuckDBSession
from gispulse.persistence.gpkg import (
    list_layers, read_gpkg, write_gpkg, read_all_layers, write_all_layers,
)
from gispulse.persistence.duckdb_relations import (   # à créer
    parquet_scan, materialize_remote_gpkg, st_dwithin_join_sql,
)

# Sites Orange (X/Y Lambert 72) :
sites = read_vector(path_xlsx, x_col="X", y_col="Y", source_crs="EPSG:31370")
network = read_vector(path_kmz)  # WGS84
with DuckDBSession() as db:
    companies = parquet_scan("s3://milou-data/data/processed/companies_be.parquet", union_by_name=True)
    sql = st_dwithin_join_sql("sites", companies, distance_m=200)
```

## 4. Frontière générique (gispulse) / métier (MILOU)
- **gispulse** (générique, dual-license) : KMZ/KML/XLSX projeté, CRS explicite, GPKG pipeline, DuckDB spatial, S3/R2/httpfs, relations Parquet/GPKG, `ST_DWithin`.
- **MILOU** (métier, confidentiel) : mapping Orange, règles d'anneaux, re-homing IC, coût marginal, TPR savings, Infrabel 10/20/40 ans, cashflow/MRC, `input_client`.

## 5. Plan ordonné (cible 13/14 juin)
| Quand | Lot |
|---|---|
| 5-7 juin | **P0** : `read_vector(.kmz)` + XLSX Lambert 72 + tests fixtures |
| 8 juin | API importable stabilisée + dépendance `openpyxl` |
| 9-10 juin | Helpers DuckDB/R2 (Parquet) + `ST_DWithin` |
| 10-11 juin | Conventions GPKG pipeline + manifest |
| 12 juin | Brancher MILOU en dépendance editable ; remplacer `kmz_reader`/lectures XLSX maison |
| 13-14 juin | Buffer livraison ; smoke : sites Orange + companies R2 + GPKG pipeline |

## 6. Note licence
MILOU (propriétaire) dépend de gispulse (AGPL-3.0 + dual-license commercial) sous **exception/
licence commerciale ImagoData**. Le générique reversé ici reste dual-licensé ; le métier
confidentiel reste côté MILOU.

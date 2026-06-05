# N3 bulk sources -> Garage S3 wiring cadrage

Statut : cadrage d'investigation, pas implementation.

Contexte verifie sur la branche `feat/bulkify-wfs-sources` :

- Les variantes bulk de #356 sont bien des sources declaratives : elles ne font pas de reseau elles-memes, elles exposent des `AccessSpec`.
- Le pattern S3 existant est deja dans les fetchers core : `GeoParquetS3Fetcher` pour `REMOTE_TABLE`, et un chemin proche existe aussi dans `HttpFileFetcher` pour `DOWNLOAD`.
- Le runner v3 actuel ne cable pas encore les sources declaratives dans `sources:` : par defaut, il fait `engine.load_layer(src.uri, layer=...)`.
- Le stockage Garage est configure par `settings.s3` / `GISPULSE_S3_*`, et DuckDB lit/ecrit via `httpfs` + `CREATE SECRET`.

## 1. Pattern `geoparquet_s3`

Le pattern vit dans `src/gispulse/core/fetchers/geoparquet_s3.py`, classe `GeoParquetS3Fetcher`.

Contrat :

- protocole : `AccessProtocol.REMOTE_TABLE`;
- payload : `Payload.VECTOR`;
- mode `REFERENCE` : retourne un scan DuckDB sous `SourceResult.metadata["duckdb_scan"]`;
- mode `MATERIALIZE` : execute un `COPY (SELECT * FROM <scan>) TO '<destination>' (FORMAT PARQUET)`;
- destination locale par defaut : `access.params["local_path"]` ou tempfile `.parquet`;
- destination S3/Garage opt-in : `access.params["s3_uri"]` ou `access.params["s3_key"]`.

Le scan est construit comme :

```sql
read_parquet('s3://bucket/prefix/**', hive_partitioning=true)
```

ou, avec bbox :

```sql
(SELECT * FROM read_parquet(...) WHERE bbox.xmin <= ... AND ...)
```

La resolution de destination S3 est factorisee dans `src/gispulse/core/fetchers/base.py` :

- `s3_uri` gagne toujours tel quel;
- sinon `s3_key` est resolu sous `s3_bucket`;
- sinon sous `settings.s3.bucket`;
- absence de `s3_uri` / `s3_key` => chemin local.

L'ecriture S3 reelle n'est pas faite par boto3 : elle est faite par DuckDB `COPY` vers `s3://...`. Le pre-requis est donc `DuckDBSession.open()`, qui charge `spatial`, charge `httpfs`, puis configure le secret S3.

Le secret DuckDB est cree dans `src/gispulse/persistence/duckdb_engine.py` :

```sql
CREATE OR REPLACE SECRET gispulse_s3 (
    TYPE s3,
    PROVIDER config,
    KEY_ID '...',
    SECRET '...',
    REGION 'garage',
    ENDPOINT 'garage:3900',
    URL_STYLE 'path',
    USE_SSL false,
    SCOPE 's3://gispulse'
);
```

Pour reutiliser le pattern hors parquet :

- Pour des entrees deja lisibles en DuckDB (`read_csv_auto`, `ST_Read`, `read_parquet`), le pattern est "scan DuckDB -> `COPY ... FORMAT PARQUET` vers `s3://...`". C'est deja exactement ce que fait `HttpFileFetcher` quand un `DOWNLOAD` recoit `s3_uri` / `s3_key`.
- Pour un CSV/ZIP tabulaire (`TABLE_FILE`), le scan existe deja (`read_csv_auto('/vsicurl/...')` ou `read_csv_auto('/vsizip//vsicurl/.../*.csv')`), mais `TableFileFetcher._materialize()` ne sait pas encore ecrire vers S3. Il telecharge uniquement en local. C'est un trou a combler pour `sis-bulk` / `gaspar-bulk`.
- Pour GPKG distant simple, `HttpFileFetcher` sait construire `ST_Read('/vsicurl/...gpkg', layer='...')`, puis `COPY` en Parquet S3 si `s3_key` est fourni.
- Pour ZIP shapefile, il faut verifier la forme GDAL exacte. Le fetcher actuel tombe sur `ST_Read('/vsicurl/...zip')`; le chemin robuste sera probablement `/vsizip//vsicurl/...zip[/member]`.
- Pour 7z (`iris_bulk`), le support DuckDB/GDAL read-in-place n'est pas prouve par le code. Le chemin probable est extraction temporaire -> `ST_Read(<gpkg extrait>)` -> `COPY` Parquet S3 -> suppression locale. Si l'objectif est aussi d'archiver le brut, il faudra en plus uploader le `.7z` brut vers `raw/...`.

Decision importante : "Garage contient le brut" et "dbt lit sans recopie locale" ne sont pas forcement le meme objet. Pour N3 robuste, je recommande deux prefixes :

- `raw/...` : archive officielle brute, reproductibilite / audit;
- `stage/...` : Parquet canonique lu par dbt avec `read_parquet('s3://...')`.

## 2. Mecanisme ingest / runner actuel

### Source declarative

Le contrat source est dans `src/gispulse/core/sources.py` :

```text
DeclarativeSource.fetch(entry_id)
  -> entry = self._entry(entry_id)
  -> self._registry.dispatch_fetch(entry.access, extent=..., mode=...)
```

`ProtocolRegistry.dispatch_fetch()` fait :

1. resolution des placeholders `{key}` via `resolve_access_endpoint`;
2. garde SSRF sur l'endpoint resolu;
3. dispatch vers le fetcher enregistre pour `access.protocol`.

Les fetchers core sont enregistres par `register_core_fetchers()` dans `src/gispulse/core/fetchers/__init__.py`. Le roster inclut :

- `GeoParquetS3Fetcher` -> `REMOTE_TABLE`;
- `WfsFetcher` -> `WFS`;
- `HttpFileFetcher` -> `DOWNLOAD`;
- `TableFileFetcher` -> `TABLE_FILE`.

### Virtual datasets

Le chemin lazy existe pour les catalogues "worldwide" :

```text
VirtualDatasetRegistry.create(entry)
  -> registry.get_fetcher(entry.access.protocol)
materialize_virtual_view(...)
  -> dispatch_fetch(..., mode=FetchMode.REFERENCE)
  -> CREATE OR REPLACE VIEW ... AS SELECT * FROM <duckdb_scan>
```

Ce chemin cree une vue DuckDB et lit ensuite la vue en GeoDataFrame. Il ne persiste pas les fichiers bulk vers Garage.

Le endpoint HTTP `/catalog/virtual/{id}/materialize` re-dispatche en `MATERIALIZE`, mais l'enregistrement final `_register_materialized()` attend surtout un fichier local ou une GeoDataFrame. Si un fetcher renvoie directement `s3://...`, le code actuel ne l'enregistre pas comme dataset local utilisable. Ce n'est donc pas le bon point de branchement pour N3 national.

### Manifest v3 / ELT runner

`docs/adr/0005-unified-manifest.md` dit que `staging` doit reposer sur `LayerRegistry`, `LazyFetcher` / `FetchMode.REFERENCE` et `VirtualDatasetRegistry`, sans nouveau moteur d'ATTACH.

Le code actuel de `src/gispulse/runtime/manifest_runner.py` n'a pas encore ce branchement :

```python
def source_loader(src: SourceSpec) -> gpd.GeoDataFrame:
    return engine.load_layer(src.uri, layer=src.layer or "")
```

Donc aujourd'hui, une source v3 `uri: insee://iris_bulk` ne serait pas resolue par `SOURCES` / `PROTOCOLS` sans `source_loader` custom.

Le branchement N3 a deux couches distinctes :

1. Ingest bulk : runner explicite qui clone l'`AccessSpec`, injecte les params departement/partition + `s3_key`, puis appelle `dispatch_fetch(..., MATERIALIZE)`.
2. Staging dbt/DuckDB : les modeles lisent les objets `stage/.../*.parquet` depuis Garage via `read_parquet('s3://gispulse/...')`.

Ce n'est pas encore un simple flag du runner v3 : il faut un livrable qui cree le runner bulk et un autre qui apprend au staging/dbt a pointer vers les objets Garage.

## 3. Config Garage / S3

Le modele de config vit dans `src/gispulse/core/config.py`.

`S3Settings` expose :

- `endpoint` <- `GISPULSE_S3_ENDPOINT` ou `[s3].endpoint`;
- `bucket` <- `GISPULSE_S3_BUCKET` ou `[s3].bucket`, defaut `gispulse`;
- `access_key` <- `GISPULSE_S3_ACCESS_KEY`;
- `secret_key` <- `GISPULSE_S3_SECRET_KEY`;
- `region` <- `GISPULSE_S3_REGION`, defaut `us-east-1`.

La precedence globale est : env `GISPULSE_*` > `gispulse.toml` > defauts.

`gispulse.toml.example` documente `[s3]`. `.env.example` documente le mode Garage :

- endpoint compose : `http://garage:3900`;
- endpoint hote : `http://localhost:3900`;
- endpoint public exemple : `https://garage.casys.ai`;
- bucket : `gispulse`;
- region : `garage`.

`docker-compose.yml` declare un service `garage` opt-in, profile `garage`, ports :

- `3900` : S3 API;
- `3903` : admin API.

`docker/garage.toml` configure aussi `s3_region = "garage"` et `api_bind_addr = "[::]:3900"`.

Deux chemins S3 coexistent :

- `DatasetStorage` / `S3Storage` dans `src/gispulse/persistence/storage.py` : utilise boto3, active S3 seulement si `GISPULSE_S3_ENDPOINT` est defini et si `check_tier("pro")` passe; sinon fallback local.
- `DuckDBSession` : configure le secret DuckDB si `settings.s3.endpoint` est defini. Ce chemin sert a `read_parquet`, `ST_Read`, `COPY ... TO 's3://...'`. Il ne passe pas par `create_storage()`.

Inconnues runtime VPS a verifier plus tard, sans le faire dans ce cadrage :

- valeur exacte de `GISPULSE_S3_ENDPOINT` cote VPS (`http://127.0.0.1:3900`, service Docker, ou endpoint public);
- presence du bucket `gispulse`;
- validite des credentials et de `GISPULSE_S3_REGION=garage`;
- capacite DuckDB/httpfs sur le VPS a ecrire puis relire un Parquet de smoke test;
- besoin ou non du tier Pro si le runner N3 utilise `S3Storage` pour uploader les archives brutes;
- comportement Caddy/Garage si endpoint public HTTPS est utilise pour de gros objets.

## 4. Plan de cablage par source bulk #356

Convention de layout proposee :

```text
s3://gispulse/raw/<source>/<entry>/millesime=<token>/departement=<dept>/...
s3://gispulse/stage/<source>/<entry>/millesime=<token>/departement=<dept>/...
```

Pour les partitions non strictement departementales :

```text
s3://gispulse/raw/<source>/<entry>/departement=<dept>/partition=<partition>/archive.zip
s3://gispulse/stage/<source>/<entry>/departement=<dept>/partition=<partition>/*.parquet
```

### `insee:iris_bulk` (`DOWNLOAD`, 7z -> GPKG)

Declaration actuelle :

- endpoint IGN `IRIS-GE_3-0__GPKG_LAMB93_{zone}_2026-01-01.7z`;
- params par defaut `zone=D075`, `layer=iris_ge`;
- metadata `zone_format = "D{code_departement:0>3}"`, `format=GPKG`, `archive_format=7z`, join key `code_iris`.

Chemin propose :

1. Le runner derive `zone` depuis le departement.
2. Il telecharge l'archive brute vers `raw/insee/iris_bulk/millesime=2026-01-01/departement=<dept>/...7z`.
3. Il extrait temporairement le GPKG, lit `layer=iris_ge`, puis ecrit un Parquet canonical :

```text
s3://gispulse/stage/insee/iris_bulk/millesime=2026-01-01/departement=<dept>/iris.parquet
```

4. dbt lit :

```sql
read_parquet('s3://gispulse/stage/insee/iris_bulk/millesime=2026-01-01/departement={dept}/iris.parquet')
```

Decisions :

- schema exact de zone pour `2A`, `2B` et DOM;
- garder ou non le GPKG brut en plus du Parquet;
- nom de couche GPKG reel (`iris_ge` est declare, mais doit etre verifie sur un echantillon);
- conversion CRS : garder LAMB93 ou normaliser en EPSG:4326 / WKB selon les contrats dbt existants.

### `gpu:gpu_documents_bulk_index` (`DOWNLOAD`, CNIG zip par partition)

Declaration actuelle :

- endpoint `https://www.geoportail-urbanisme.gouv.fr/api/document/download-by-partition/{partition}`;
- params par defaut `partition=DU_200046977`, `departement=69`;
- metadata : familles `pack_plu1`, `pack_plu2`, `pack_plui`, `pack_cc`, join keys `idurba`, `insee`.

Chemin propose :

1. Construire la liste des partitions du departement avant fetch. Le plugin expose `gpu_du_partition()` et `gpu_du_partitions_for_department()`, mais il faut une source d'autorite pour les couples commune/SIREN par departement.
2. Pour chaque partition :

```text
raw/gpu/gpu_documents_bulk_index/departement=<dept>/partition=<partition>/archive.zip
stage/gpu/gpu_documents_bulk_index/departement=<dept>/partition=<partition>/<layer>.parquet
```

3. dbt peut lire par departement avec un glob :

```sql
read_parquet('s3://gispulse/stage/gpu/gpu_documents_bulk_index/departement={dept}/**/*.parquet', union_by_name=true)
```

Decisions :

- source de la liste partitions DU par departement;
- granularite stage : un Parquet par partition/layer ou union departementale `gpu-{dept}.parquet`;
- quelles couches CNIG deviennent le contrat dbt minimal N3;
- idempotence par partition : skip si objet stage existe + manifest revision identique, ou overwrite systematique.

### `sup:pack_sup` (`DOWNLOAD`, CNIG zip par partition)

Declaration actuelle :

- endpoint `download-by-partition/{partition}`;
- params par defaut `partition=172014607_SUP_69_AC1`, `codeGeo=69`, `categorie=AC1`;
- helper `sup_partition(code_geo, categorie, id_gest=None)`;
- metadata : pattern `{idGest_}SUP_<codeGeo>_<categorie>`, join keys `idsup`, `suptype`.

Chemin propose :

```text
raw/sup/pack_sup/departement=<dept>/partition=<partition>/archive.zip
stage/sup/pack_sup/departement=<dept>/partition=<partition>/<layer>.parquet
```

Puis dbt :

```sql
read_parquet('s3://gispulse/stage/sup/pack_sup/departement={dept}/**/*.parquet', union_by_name=true)
```

Decisions :

- enumeration des categories SUP et `idGest` par departement;
- compatibilite avec le contrat foncier actuel `SUP_PARQUET_DIR/*.parquet`;
- conserver la sortie "un fichier par layer" pour rester proche de `stg_sup_layers`;
- politique `--allow-partial` : pour SUP, un departement peut etre partiel sans masquer l'etat dans le manifest d'ingest.

### `georisques:rga-bulk` / `tri-bulk` (`DOWNLOAD`, ZIP shapefile par departement)

Declarations actuelles :

- `rga-bulk` : `https://files.georisques.fr/argiles/2025/AleaRG_2025_{departement}_L93.zip`;
- `tri-bulk` : `https://files.georisques.fr/di_2020/tri_2020_sig_di_{departement}.zip`;
- metadata : `archive_format=zip`, `data_format=shapefile`, join spatial.

Chemin propose :

```text
raw/georisques/rga-bulk/departement=<dept>/archive.zip
stage/georisques/rga-bulk/departement=<dept>/rga.parquet

raw/georisques/tri-bulk/departement=<dept>/archive.zip
stage/georisques/tri-bulk/departement=<dept>/tri.parquet
```

Le fetcher `DOWNLOAD` sait deja ecrire un scan en Parquet S3 quand `s3_key` est donne, mais il faut fiabiliser le scan ZIP shapefile (`/vsizip//vsicurl/...` et eventuel `archive_member`) avant d'en faire un runner national.

Decisions :

- scan ZIP direct vs extraction temporaire;
- conserver brut + Parquet ou Parquet seulement;
- contrat colonnes normalisees pour le dbt foncier (`rga_class`, `tri_present`) vs raw upstream.

### `georisques:sis-bulk` / `gaspar-bulk` (`TABLE_FILE`, CSV / ZIP CSV)

Declarations actuelles :

- `sis-bulk` : CSV WFS BRGM, `TABLE_FILE`, `table_format=csv`;
- `gaspar-bulk` : `gaspar.zip`, `TABLE_FILE`, `archive_format=zip`, `table_format=csv`;
- join keys : `code_insee`.

Chemin propose :

```text
raw/georisques/sis-bulk/national/sis.csv
stage/georisques/sis-bulk/national/sis.parquet

raw/georisques/gaspar-bulk/national/gaspar.zip
stage/georisques/gaspar-bulk/national/gaspar.parquet
```

dbt :

```sql
read_parquet('s3://gispulse/stage/georisques/gaspar-bulk/national/gaspar.parquet')
```

Trou actuel : `TableFileFetcher` sait faire `REFERENCE` en `read_csv_auto(...)`, mais son `MATERIALIZE` n'a pas de branche `s3_uri` / `s3_key`. Il faut l'aligner sur `HttpFileFetcher` :

```sql
COPY (SELECT * FROM read_csv_auto(...)) TO 's3://gispulse/stage/...' (FORMAT PARQUET)
```

Decisions :

- delimiter/encoding exacts de SIS et GASPAR;
- selection de membre dans `gaspar.zip` si plusieurs CSV existent;
- national unique vs partitionnement par departement derive apres lecture.

## 5. Plan d'implementation testable

### Etape 1 - Verrouiller le layout S3 et le manifest d'ingest

Livrable futur :

- nouveau helper pur pour construire les `s3_key` a partir de `(source, entry, departement, partition, millesime, kind=raw|stage)`;
- manifest JSON/Parquet d'ingest listant `source`, `entry`, `scope`, `revision`, `raw_s3_uri`, `stage_s3_uri`, `row_count`, `status`.

Test :

- unit test sans reseau : les cles sont stables, pas de `..`, pas de double slash, departements normalises.

### Etape 2 - Ajouter S3 materialization a `TableFileFetcher`

Livrable futur :

- `TableFileFetcher._materialize()` reconnait `s3_uri` / `s3_key`;
- il execute `COPY (SELECT * FROM <_reference_scan>) TO '<s3>' (FORMAT PARQUET)`;
- il renvoie `SourceResult(reference=s3_uri, metadata={"s3_uri": ..., "copy_sql": ...})`.

Test :

- monkeypatch `DuckDBSession`, comme `test_geoparquet_s3_fetcher.py`;
- couvrir CSV simple et ZIP CSV avec `archive_member`.

### Etape 3 - Runner bulk declaratif minimal

Livrable futur :

- runner CLI ou module core qui :
  - charge/enregistre les source plugins;
  - appelle `register_core_fetchers()`;
  - clone l'`AccessSpec` avec params runtime (`departement`, `zone`, `partition`) et `s3_key`;
  - dispatch `MATERIALIZE`;
  - ecrit un manifest d'ingest.

Test :

- faux `DataSource` + faux `Fetcher`, zero reseau;
- assert que `s3_key` est injecte et que les placeholders sont resolus avant fetch.

### Etape 4 - Strategy archives `DOWNLOAD`

Livrable futur :

- strategie explicite par format :
  - `.gpkg` direct `ST_Read`;
  - `.zip` via `/vsizip` ou extraction temporaire si necessaire;
  - `.7z` extraction temporaire obligatoire tant que le support DuckDB/GDAL n'est pas prouve;
  - upload optionnel du brut via `S3Storage` ou `COPY`/client S3 dedie.

Test :

- fixtures locales petites (`.gpkg`, `.zip` shapefile/CSV);
- le test `.7z` peut etre separe et marque dependency-gated si une lib d'extraction est ajoutee.

### Etape 5 - dbt/foncier lit Garage read-in-place

Livrable futur dans le consommateur dbt :

- remplacer les templates locaux type `GPU_PARQUET_TEMPLATE=/path/gpu-{dept}.parquet` par des templates acceptant `s3://gispulse/stage/.../departement={dept}/...parquet`;
- garder les macros `read_parquet(..., union_by_name=true)`;
- verifier que le profil dbt/DuckDB charge `httpfs` et le secret S3 avant build.

Test :

- test de compilation dbt avec env vars `s3://...{dept}...`;
- smoke local si Garage compose disponible : `COPY` d'une fixture vers Garage, `dbt build --select stg_*` sur un departement minuscule.

### Etape 6 - Smoke runtime VPS avant national

Livrable futur, avec accord humain explicite :

- sur le VPS seulement apres validation :
  - `COPY (SELECT 1 AS ok) TO 's3://gispulse/smoke/n3.parquet' (FORMAT PARQUET)`;
  - `SELECT * FROM read_parquet('s3://gispulse/smoke/n3.parquet')`;
  - suppression de l'objet smoke.

Test :

- preuve runtime datee dans le runbook, pas dans le code.

## Decisions a trancher avant codage

1. Layout final : `raw/` + `stage/` ou `stage/` uniquement pour N3 ?
2. Format canonique dbt : toujours Parquet, ou lecture directe des ZIP/GPKG depuis Garage quand possible ?
3. Granularite : un objet par departement, par partition, par couche, ou un mix selon source ?
4. Idempotence : overwrite atomique, skip si existe, ou versionner par `revision_token` / millesime ?
5. SUP/GPU : quelle source d'autorite enumerera les partitions par departement ?
6. Partials : surtout pour SUP, est-ce qu'un departement partiel est un succes explicite (`allow_partial`) ou un echec ?
7. Runtime Garage : endpoint exact VPS, credentials, bucket, et preuve DuckDB `COPY`/`read_parquet` a valider plus tard.

## Recommendation de cadrage

Pour N3, ne pas essayer de faire lire a dbt des archives heterogenes. Le chemin le plus stable est :

```text
source declarative -> fetch materialize -> Garage stage Parquet -> dbt read_parquet(s3://...)
```

Archiver les fichiers bruts dans `raw/` est utile pour l'audit et la reproductibilite, mais c'est un choix produit/ops separe. Le minimum technique testable est le Parquet canonique dans `stage/`, car il reutilise le pattern `geoparquet_s3` : DuckDB scan + `COPY` vers S3 + `read_parquet` read-in-place.

# Ingestion nationale cadastre + DVF vers Garage

Statut : cadrage de process, pas implementation.

Objectif : materialiser une base nationale cadastre + DVF dans Garage, de facon
idempotente, relancable, et lisible par dbt/DuckDB sans recopie locale lourde.

## Verites code ancrees

- Le layout N3 canonique est deja centralise : les cles sont
  `<raw|stage>/<source>/<entry>/millesime=<revision>/<scope>/<filename>`,
  avec `scope = departement=<dept>` ou `national` (`src/gispulse/core/bulk_ingest.py:89`).
- La normalisation actuelle des codes pour les cles S3 gere `2A`/`2B`, padde
  les codes numeriques courts, et garde `national` comme scope special
  (`src/gispulse/core/bulk_ingest.py:56`).
- `BulkIngestRunner` orchestre une entree declarative vers `raw/` + `stage/`,
  mais il ne supporte aujourd'hui que `TABLE_FILE` et `DOWNLOAD`
  (`src/gispulse/core/bulk_runner.py:104`, `src/gispulse/core/bulk_runner.py:126`,
  `src/gispulse/core/bulk_runner.py:144`).
- Le chemin `DOWNLOAD` du runner sait uploader le brut et convertir des membres
  d'archive vectorielle en Parquet S3, mais l'extraction ne couvre que `zip` et
  `7z` (`src/gispulse/core/bulk_runner.py:329`, `src/gispulse/core/bulk_runner.py:347`,
  `src/gispulse/core/bulk_runner.py:584`).
- Le cadastre bulk Etalab existe bien dans le plugin core sous les entrees
  `parcelles_bulk`, `communes_bulk`, `sections_bulk`, `batiments_bulk`, avec un
  endpoint `.../departements/{departement}/cadastre-{departement}-{layer}.json.gz`
  (`plugins/gispulse-src-cadastre/gispulse_src_cadastre/source.py:11`,
  `plugins/gispulse-src-cadastre/gispulse_src_cadastre/source.py:149`,
  `plugins/gispulse-src-cadastre/gispulse_src_cadastre/source.py:190`).
- Le runner cadastre existant dans `gispulse-foncier` ecrit un DuckDB local :
  il telecharge le `.json.gz`, lit via `ST_Read('/vsigzip/<path>')`, puis cree
  des tables `v_<entry_id>__<dpt>` (`../gispulse-foncier/scripts/ingest_bulk.py:13`,
  `../gispulse-foncier/scripts/ingest_bulk.py:139`,
  `../gispulse-foncier/scripts/ingest_bulk.py:177`).
- DVF n'est pas un bulk `DOWNLOAD` dans le plugin : c'est un `REMOTE_TABLE`
  CSV, avec shards `latest/csv/{year}/departements/{departement}.csv.gz`, annees
  codees `2021..2025`, et `read_csv_auto(..., all_varchar=true)` comme scan
  DuckDB (`plugins/gispulse-src-dvf/gispulse_src_dvf/source.py:22`,
  `plugins/gispulse-src-dvf/gispulse_src_dvf/source.py:53`,
  `plugins/gispulse-src-dvf/gispulse_src_dvf/source.py:181`).
- Le script DVF actuel produit un Parquet local par departement en resolvant le
  scan plugin puis en faisant un `COPY (...) TO '<out>' (FORMAT PARQUET)`
  (`../gispulse-foncier/scripts/ingest_dvf_dept.py:1`,
  `../gispulse-foncier/scripts/ingest_dvf_dept.py:203`,
  `../gispulse-foncier/scripts/ingest_dvf_dept.py:227`).
- dbt est deja multi-departement via `var('departements')`, normalisation
  `3 -> 03`, et templates Parquet a placeholder `{dept}`
  (`../gispulse-foncier/dbt/macros/department_sources.sql:1`,
  `../gispulse-foncier/dbt/macros/department_sources.sql:9`,
  `../gispulse-foncier/dbt/macros/department_sources.sql:42`).
- Les sources cadastre/DVF/GPU/IRIS par departement sont volontairement hors
  `sources.yml`; elles passent par macros parce que le YAML dbt est statique
  (`../gispulse-foncier/dbt/models/staging/sources.yml:3`).
- Le profil dbt actuel attache encore un DuckDB cadastre local et ne charge pas
  `httpfs` (`../gispulse-foncier/dbt/profiles.example.yml:8`,
  `../gispulse-foncier/dbt/profiles.example.yml:11`).
- Le `DuckDBSession` core sait charger `httpfs` et creer un secret S3 depuis
  `settings.s3`, ce qui donne le modele a reproduire cote dbt
  (`src/gispulse/persistence/duckdb_engine.py:121`,
  `src/gispulse/persistence/duckdb_engine.py:41`).

## 1. Perimetre departements

Recommandation pour une base nationale **cadastre + DVF appariee** : demarrer
avec l'intersection cadastre/DVF geree aujourd'hui par le runner cadastre, soit
101 codes :

```text
01 02 03 04 05 06 07 08 09
10 11 12 13 14 15 16 17 18 19
21 22 23 24 25 26 27 28 29 2A 2B
30 31 32 33 34 35 36 37 38 39
40 41 42 43 44 45 46 47 48 49
50 51 52 53 54 55 56 57 58 59
60 61 62 63 64 65 66 67 68 69
70 71 72 73 74 75 76 77 78 79
80 81 82 83 84 85 86 87 88 89
90 91 92 93 94 95
971 972 973 974 976
```

Raison : `gispulse-foncier/scripts/ingest_bulk.py` definit exactement
`01..95` sauf `20`, ajoute `2A`/`2B`, puis ajoute `971,972,973,974,976`
(`../gispulse-foncier/scripts/ingest_bulk.py:71`). Le script DVF est plus large
et accepte `971..978` (`../gispulse-foncier/scripts/ingest_dvf_dept.py:60`).
Pour ne pas produire une base "nationale" partiellement cadastree, `975`,
`977`, `978` restent une decision explicite, pas le default.

Implication : le process doit exposer deux listes nommees.

- `CADASTRE_DVF_NATIONAL_DEPARTEMENTS` : les 101 codes ci-dessus, default run.
- `DVF_EXTRA_TERRITORIES` : `975 977 978`, a activer seulement si l'humain veut
  des shards DVF sans cadastre associe dans le premier lot.

## 2. Cadastre vers Garage

### Source

Source officielle process : `gispulse-src-cadastre`, entries bulk Etalab.
Les quatre entrees existent dans le catalogue, mais dbt Foncier Radar consomme
aujourd'hui seulement `parcelles`, `sections`, `communes` via
`cadastre_bulk_relation` (`../gispulse-foncier/dbt/macros/department_sources.sql:28`).
`batiments_bulk` peut etre archive pour completeness, mais ne doit pas bloquer
la base nationale Foncier si on decide de livrer uniquement les trois layers
utilises.

### Layout Garage recommande

Conserver le layout N3 existant au lieu d'introduire un deuxieme schema :

```text
s3://gispulse/raw/cadastre/parcelles_bulk/millesime=<cadastre_revision>/departement=<dept>/cadastre-<dept>-parcelles.json.gz
s3://gispulse/stage/cadastre/parcelles_bulk/millesime=<cadastre_revision>/departement=<dept>/parcelles.parquet

s3://gispulse/raw/cadastre/sections_bulk/millesime=<cadastre_revision>/departement=<dept>/cadastre-<dept>-sections.json.gz
s3://gispulse/stage/cadastre/sections_bulk/millesime=<cadastre_revision>/departement=<dept>/sections.parquet

s3://gispulse/raw/cadastre/communes_bulk/millesime=<cadastre_revision>/departement=<dept>/cadastre-<dept>-communes.json.gz
s3://gispulse/stage/cadastre/communes_bulk/millesime=<cadastre_revision>/departement=<dept>/communes.parquet
```

Si `batiments_bulk` est inclus :

```text
s3://gispulse/raw/cadastre/batiments_bulk/millesime=<cadastre_revision>/departement=<dept>/cadastre-<dept>-batiments.json.gz
s3://gispulse/stage/cadastre/batiments_bulk/millesime=<cadastre_revision>/departement=<dept>/batiments.parquet
```

Le nom logique "stage/cadastre/{dept}/..." demande dans le cadrage est donc
couvert par le scope `departement=<dept>` du layout N3, avec l'entry comme axe
supplementaire obligatoire pour eviter de melanger les layers.

### Branchement propre

Ne pas reutiliser tel quel `gispulse-foncier/scripts/ingest_bulk.py` comme
runner national Garage. Il est utile comme preuve terrain, mais son contrat est
"archive locale -> table DuckDB locale" (`../gispulse-foncier/scripts/ingest_bulk.py:181`),
pas "archive Garage -> Parquet Garage".

Le branchement propre est :

1. Reutiliser `BulkIngestRunner` et `bulk_ingest.py` pour les cles, les
   manifests et la normalisation departement.
2. Ajouter au runner N3 un chemin `DOWNLOAD` gzip spatial :
   telecharger `cadastre-<dept>-<layer>.json.gz` en temp/cache, uploader le brut
   dans `raw/`, puis `COPY (SELECT * FROM ST_Read('/vsigzip/<local_path>')) TO
   's3://gispulse/stage/...' (FORMAT PARQUET)`.
3. Reprendre la logique `ST_Read('/vsigzip/...')` du runner local, parce que le
   code existant documente que `/vsicurl/` ne suffit pas pour le gzip Etalab
   (`../gispulse-foncier/scripts/ingest_bulk.py:27`,
   `../gispulse-foncier/scripts/ingest_bulk.py:146`).
4. Ne pas passer par `HttpFileFetcher` direct avec `s3_key` pour ces `.json.gz`
   tant que le support gzip n'est pas explicite : son chemin S3 materialise un
   scan `ST_Read('/vsicurl/...')` (`src/gispulse/core/fetchers/http_file.py:138`),
   alors que le runner terrain indique que ce n'est pas viable pour gzip.

## 3. DVF vers Garage

### Source et millesimes

Source process : `gispulse-src-dvf`, entry `mutations`.

Le plugin expose actuellement une fenetre roulante `2021,2022,2023,2024,2025`
(`plugins/gispulse-src-dvf/gispulse_src_dvf/source.py:53`). Pour Foncier Radar,
il faut garder **toutes les annees de la fenetre exposee**, pas seulement la
derniere, parce que les modeles calculent des fenetres de marche et lisent les
faits DVF avant filtrage (`../gispulse-foncier/dbt/models/staging/stg_dvf_facts.sql:5`,
`../gispulse-foncier/dbt/models/staging/stg_dvf_facts.sql:19`).

Recommandation :

- default initial : `years = 2021..2025`, identique au plugin actuel;
- ne pas coder "dernier millesime seulement";
- parametrer `years` au niveau orchestration pour pouvoir suivre le prochain
  refresh DVF sans patch code;
- utiliser `millesime=<year>` dans les cles DVF, et stocker le token
  dataset-wide `last_modified` dans le manifest de run.

### Layout Garage recommande

Un objet raw et un objet stage par `(dept, year)` :

```text
s3://gispulse/raw/dvf/mutations/millesime=<year>/departement=<dept>/mutations.csv.gz
s3://gispulse/stage/dvf/mutations/millesime=<year>/departement=<dept>/mutations.parquet
```

Le Parquet stage doit garder uniquement les colonnes lues par dbt, soit le
contrat `DVF_COLUMNS` du script actuel (`../gispulse-foncier/scripts/ingest_dvf_dept.py:35`).
Cela evite de stocker cinq fois des colonnes inutiles pour le compute national.

### Branchement propre

DVF ne passe pas par le `BulkIngestRunner` actuel sans evolution, parce que
`run_entry()` rejette tout ce qui n'est pas `TABLE_FILE` ou `DOWNLOAD`
(`src/gispulse/core/bulk_runner.py:144`), alors que DVF est `REMOTE_TABLE`
(`plugins/gispulse-src-dvf/gispulse_src_dvf/source.py:347`).

Deux options existent, mais une seule est recommandee :

- Recommande : ajouter un chemin N3 `REMOTE_TABLE -> COPY stage S3` qui demande
  au fetcher DVF son scan `REFERENCE`, puis fait `COPY (...) TO 's3://...'`.
  C'est le meme pattern que `GeoParquetS3Fetcher` utilise deja pour S3
  (`src/gispulse/core/fetchers/geoparquet_s3.py:102`) et que le script DVF
  utilise localement (`../gispulse-foncier/scripts/ingest_dvf_dept.py:227`).
- Non recommande : faire grossir `scripts/ingest_dvf_dept.py` pour uploader
  lui-meme dans Garage. Ce serait plus rapide a patcher, mais on dupliquerait
  le manifeste, le layout, la reprise et les logs deja portes par N3.

## 4. Lecture dbt depuis Garage

### DVF

Le chemin minimal est deja presque pret : `dvf_parquet_scan(departement)`
resout `DVF_PARQUET_TEMPLATE` et appelle `read_parquet(..., union_by_name=true)`
(`../gispulse-foncier/dbt/macros/department_sources.sql:51`).

Template cible :

```bash
export DVF_PARQUET_TEMPLATE="s3://gispulse/stage/dvf/mutations/millesime=*/departement={dept}/mutations.parquet"
```

ou, si on veut figer une annee concrete sans glob :

```bash
export DVF_PARQUET_TEMPLATE="s3://gispulse/stage/dvf/mutations/millesime=2025/departement={dept}/mutations.parquet"
```

Le premier template demande une petite evolution de macro, car le template
actuel ne remplace que `{dept}`. Variante plus simple sans evolution macro :
mettre toutes les annees d'un dept dans un unique stage Parquet
`stage/dvf/mutations/millesime=<release>/departement=<dept>/mutations.parquet`.
Je recommande pourtant le partitionnement par annee, puis une macro explicite
`dvf_parquet_scan(dept)` qui lit un glob multi-annee.

### Cadastre

Le changement dbt le plus propre est de garder le nom de macro existant :
`cadastre_bulk_relation(layer, departement)`.

Aujourd'hui, elle retourne une table DuckDB attachee :

```sql
cadastre.main.v_<layer>_bulk__<dept>
```

(`../gispulse-foncier/dbt/macros/department_sources.sql:28`).

Demain, elle doit pouvoir retourner un scan Parquet quand un template Garage est
configure :

```bash
export CADASTRE_PARQUET_TEMPLATE="s3://gispulse/stage/cadastre/{layer}_bulk/millesime=<cadastre_revision>/departement={dept}/{layer}.parquet"
```

Pseudo-contrat macro :

```jinja
if CADASTRE_PARQUET_TEMPLATE is set:
  read_parquet(template.replace('{layer}', layer).replace('{dept}', normalise_departement(departement)), union_by_name=true)
else:
  cadastre.main.v_<layer>_bulk__<dept>
```

Cela preserve tous les modeles existants, qui utilisent deja
`from {{ cadastre_bulk_relation('parcelles', dept) }}`
(`../gispulse-foncier/dbt/models/marts/mart_radar_parcels.sql:28`,
`../gispulse-foncier/dbt/models/intermediate/int_cells_commune.sql:22`,
`../gispulse-foncier/dbt/models/intermediate/int_cells_section.sql:32`).

### Configuration DuckDB/dbt pour S3

Il faudra ajouter `httpfs` et le secret S3 cote dbt. Le modele a recopier est
celui de `DuckDBSession` : `LOAD httpfs`, puis `CREATE OR REPLACE SECRET ...`
avec `REGION`, `ENDPOINT`, `URL_STYLE 'path'`, `USE_SSL`, et `SCOPE`
(`src/gispulse/persistence/duckdb_engine.py:65`). Les variables sont deja
documentees dans `.env.example` et `docker-compose.yml`
(`.env.example:59`, `docker-compose.yml:32`, `docker-compose.yml:70`).

## 5. Orchestration nationale

### Unite de travail

Un job doit etre une unite petite et relancable :

```text
cadastre: (dept, layer, cadastre_revision)
dvf:      (dept, year, dvf_dataset_revision)
```

Chaque job produit :

- `raw_s3_uri`
- `stage_s3_uri`
- `row_count`
- `status = success | failed | skipped`
- `elapsed_ms`
- `error_type` / `error_message` si echec
- `source_revision` ou `dataset_last_modified`

### Idempotence

- Les cles stables incluent `millesime` et `departement`.
- Un rerun sans `--force` peut skipper un job si le manifest dit `success` et
  si l'objet stage existe.
- Un rerun avec `--force` reecrit les memes cles stables.
- Un objet S3 individuel est publie par PUT/COPY atomique; pour un groupe
  multi-objets, la verite de run doit etre le manifest, pas l'existence
  partielle d'un prefixe.

### Reprise sur erreur

Default national : `continue_on_error=true`.

Un departement qui echoue ne doit pas casser les autres. Le precedent local
cadastre a deja ce mode : `--continue-on-error` loggue et saute le departement,
puis la CLI sort non-zero si au moins une erreur a ete absorbee
(`../gispulse-foncier/scripts/ingest_bulk.py:200`,
`../gispulse-foncier/scripts/ingest_bulk.py:238`,
`../gispulse-foncier/scripts/ingest_bulk.py:346`).

### Logs

Logs JSON-lines recommandes, un event par transition :

```json
{"event":"downloaded","source":"cadastre","layer":"parcelles","dept":"63","raw_s3_uri":"s3://...","bytes":202375168}
{"event":"staged","source":"cadastre","layer":"parcelles","dept":"63","stage_s3_uri":"s3://...","rows":1571465,"elapsed_ms":123456}
{"event":"failed","source":"dvf","year":"2024","dept":"2A","error_type":"HTTPStatusError","error_message":"..."}
```

La sortie finale doit ressembler aux scripts existants : total jobs, succes,
skips, erreurs, lignes, duree. Le script cadastre imprime deja `loaded`,
`errors`, rows par table et elapsed (`../gispulse-foncier/scripts/ingest_bulk.py:338`);
le script DVF imprime deja departements et rows (`../gispulse-foncier/scripts/ingest_dvf_dept.py:304`).

### Parallellisme

Demarrage conservateur :

- cadastre : 2 departements en parallele, 1 layer lourd (`parcelles`) par
  departement a la fois; `sections`/`communes` peuvent suivre dans le meme worker;
- DVF : 4 a 8 jobs `(dept, year)` en parallele, avec backoff HTTP;
- Garage : limiter les gros `COPY` concurrents a 2 ou 3 tant que la capacite
  disque/reseau n'a pas ete mesuree.

Avant le run national : smoke sur `63`, `75`, `2A`, `971`, et un petit
departement metropolitain. Ce lot couvre gros volume, dense urbain, Corse,
DOM, et cas moyen.

## 6. Volumetrie et duree

Ces chiffres sont des ordres de grandeur pour dimensionner le run, pas des
quotas garantis. Ils doivent etre remplaces par un rapport de smoke avant le
premier run national.

Cadastre observe sur le departement `63` :

- raw `parcelles` `.json.gz` : `193M`;
- raw `sections` `.json.gz` : `14M`;
- raw `communes` `.json.gz` : `3.4M`;
- rows : `1,571,465` parcelles, `7,748` sections, `463` communes
  (`../gispulse-foncier/docs/runbooks/foncier-radar-cadastre-source-stage.md:111`,
  `../gispulse-foncier/docs/runbooks/foncier-radar-cadastre-source-stage.md:138`).

Ordre de grandeur national cadastre :

- raw + stage pour `parcelles/sections/communes` : prevoir dizaines de Go,
  typiquement `30-80 GB` tant que les plus gros departements n'ont pas ete
  mesures;
- ajouter `batiments` peut doubler ou plus l'enveloppe, a calibrer avant
  inclusion;
- PostGIS local observe apres le run `63` : environ `2 GB`, ce qui rappelle que
  la base de serving nationale ne se dimensionne pas comme le stage Garage
  (`../gispulse-foncier/docs/runbooks/foncier-radar-cadastre-source-stage.md:218`).

DVF observe localement dans ce checkout :

- `dvf-63.parquet` : `8.3 MB`;
- `dvf-75.parquet` : `12 MB`.

Ordre de grandeur national DVF stage projete :

- stage Parquet projete colonnes dbt : quelques Go pour 101 departements et la
  fenetre `2021..2025`;
- raw CSV `.gz` complet par `(dept, year)` : potentiellement plusieurs dizaines
  de Go, a mesurer par `HEAD`/manifest source avant run.

Duree initiale recommandee :

- smoke 5 departements : 1 a 3 heures selon debit source/Garage et conversion;
- national cadastre + DVF : partir sur une fenetre operationnelle de 12 a 24
  heures pour le premier run prudent;
- apres cache et calibration, viser des reruns incrementaux par millesime en
  quelques heures.

## 7. Plan d'implementation testable

1. Extraire une liste canonique `CADASTRE_DVF_NATIONAL_DEPARTEMENTS` cote core
   ou orchestration, avec tests sur `01`, `2A`, `971`, rejet de `20`, et option
   explicite pour `975/977/978`.
2. Ajouter au runner N3 le support cadastre gzip GeoJSON : fixture `.json.gz`
   locale, assertion sur `raw_s3_uri`, `stage_s3_uri`, SQL `ST_Read('/vsigzip/`,
   et `row_count`.
3. Ajouter au runner N3 un chemin `REMOTE_TABLE` DVF : fixture CSV locale via
   fetcher DVF, `COPY` vers `s3://...`, un Parquet par `(dept, year)`, et test
   que `2A`/`971` gardent les bons prefixes.
4. Ajouter un manifest national relancable : tests sur skip success, force
   overwrite, erreur absorbee, et sortie finale non-zero si erreurs.
5. Adapter dbt : `CADASTRE_PARQUET_TEMPLATE`, glob DVF multi-annees, `httpfs`
   + secret S3; tests `dbt parse`/macro compile sans Garage reel.
6. Smoke Garage local/public sur 1 departement : ecrire raw/stage, relire avec
   `read_parquet('s3://...')`, verifier row counts contre le runner local.
7. Smoke multi-profils : `63`, `75`, `2A`, `971`, petit departement; produire un
   rapport taille/duree avant d'ouvrir le run national.
8. Run national en batchs regionaux, avec manifest final et rapport :
   departements success/failed/skipped, rows cadastre/DVF, bytes raw/stage,
   duree, prochaine reprise.

## Decisions a trancher avec l'humain

1. Perimetre : base appariee 101 codes seulement, ou ajout DVF-only
   `975/977/978` des le premier run ?
2. Cadastre : inclure `batiments_bulk` dans Garage national maintenant, ou
   livrer d'abord les trois layers consommes par dbt (`parcelles`,
   `sections`, `communes`) ?
3. DVF : garder la fenetre plugin actuelle `2021..2025`, ou parametrer un
   historique plus large si le miroir source l'expose ?
4. Layout : confirmer le layout N3 strict
   `stage/<source>/<entry>/millesime=<...>/departement=<dept>/...`, meme si le
   raccourci verbal est `stage/cadastre/{dept}/...`.
5. Runtime : faire evoluer `BulkIngestRunner` comme chemin officiel national,
   et garder les scripts `gispulse-foncier` seulement comme reference/pilote.

READY : le process est cadrable sans VPS ni gros telechargement. Les deux
travaux bloquants avant national sont le support N3 cadastre gzip et le support
N3 DVF `REMOTE_TABLE -> Parquet S3`.

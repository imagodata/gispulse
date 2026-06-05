# N3 Smoke Isolé Bulk -> Garage

Objectif : valider `BulkIngestRunner` sur le VPS contre le Garage réel, sans
toucher au checkout prod `/opt/gispulse-core-beta` ni aux préfixes servis.

## Déploiement isolé

```bash
rm -rf /tmp/n3-smoke
git clone <gispulse-core-repo-url> /tmp/n3-smoke
cd /tmp/n3-smoke
git checkout feat/bulkify-wfs-sources

python3 -m venv .venv
. .venv/bin/activate
python -m pip install -U pip
python -m pip install -e ".[s3]" -e plugins/gispulse-src-georisques
```

Source ensuite l'env Garage utilisé par `settings.s3`, sans afficher les
valeurs :

```bash
set -a
. /path/to/gispulse-garage.env
set +a
```

## Lancement

```bash
export N3_SMOKE_BUCKET="${GISPULSE_S3_BUCKET:-gispulse}"
export N3_SMOKE_PREFIX="smoke-n3/"
export N3_SMOKE_DEPARTMENT="63"
python scripts/n3_smoke.py
```

Par défaut, le smoke lance `georisques:sis-bulk`, source `TABLE_FILE` CSV, sous
la revision isolée `smoke-n3`. Le département sert à borner le chemin N3 écrit :
`smoke-n3/raw/.../departement=63/...` et
`smoke-n3/stage/.../departement=63/...`.

La sortie attendue logge :

- l'URI S3 raw écrite et sa taille ;
- l'URI S3 stage écrite et sa taille ;
- le nombre de lignes relues via `DuckDB read_parquet('s3://...')`.

## Nettoyage

Toujours faire un dry-run avant suppression :

```bash
export AWS_ACCESS_KEY_ID="$GISPULSE_S3_ACCESS_KEY"
export AWS_SECRET_ACCESS_KEY="$GISPULSE_S3_SECRET_KEY"
export AWS_DEFAULT_REGION="${GISPULSE_S3_REGION:-us-east-1}"

aws --endpoint-url "$GISPULSE_S3_ENDPOINT" \
  s3 rm "s3://${N3_SMOKE_BUCKET:-gispulse}/${N3_SMOKE_PREFIX:-smoke-n3/}" \
  --recursive --dryrun

aws --endpoint-url "$GISPULSE_S3_ENDPOINT" \
  s3 rm "s3://${N3_SMOKE_BUCKET:-gispulse}/${N3_SMOKE_PREFIX:-smoke-n3/}" \
  --recursive
```

Le script ne lit les credentials que via l'environnement `GISPULSE_S3_*` et ne
les logge pas.

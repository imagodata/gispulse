# gispulse-src-dvf

French real-estate transactions (DVF) data source for GISPulse — Etalab geo-enriched CSV mirror (domain: `STATISTIQUE`, jurisdiction: `FR`).

## Provider

| Field             | Value                                                         |
|-------------------|---------------------------------------------------------------|
| Upstream producer | DGFiP (Direction générale des finances publiques)             |
| Provider (runtime)| Etalab (`metadata.provider = "Etalab"`)                       |
| Redistributor     | data.gouv.fr / `files.data.gouv.fr`                           |
| Dataset           | Demandes de Valeurs Foncières (DVF)                           |
| Mirror            | `files.data.gouv.fr/geo-dvf` (geo-enriched CSV gzip)          |
| Licence           | Licence Ouverte 2.0                                           |
| Cadence           | Semestrial (April / October — rolling 5-year window)          |

## Entries

| id          | Label                                        | AccessProtocol  | Endpoint base                                           | Payload | Jurisdiction |
|-------------|----------------------------------------------|-----------------|---------------------------------------------------------|---------|--------------|
| `mutations` | Mutations DVF (transactions immobilières)    | `REMOTE_TABLE`  | `https://files.data.gouv.fr/geo-dvf/latest/csv`         | TABLE   | FR           |

Schema highlights (field names mirror the Etalab geo-dvf CSV header):

`id_mutation`, `date_mutation`, `nature_mutation`, `valeur_fonciere`, `type_local`, `surface_reelle_bati`, `surface_terrain`, `code_commune`, `nom_commune`, `code_departement`, `id_parcelle`, `prefixe_section`, `section`, `numero_plan`, `longitude`, `latitude`

The former `latest/parquet/full.parquet` mirror now resolves to a missing S3 object. The plugin uses the live CSV layout instead:

- preferred when a department hint is available: `https://files.data.gouv.fr/geo-dvf/latest/csv/{year}/departements/{departement}.csv.gz`
- fallback without a department hint: `https://files.data.gouv.fr/geo-dvf/latest/csv/{year}/full.csv.gz`

The local `REMOTE_TABLE` fetcher emits DuckDB `read_csv_auto` scans over the rolling `2021..2025` files, then applies bbox predicates on `longitude` / `latitude`. The CSV no longer carries `prefixe_section`, `section`, and `numero_plan` as standalone fields, so the scan recreates those legacy columns from `id_parcelle`.

## Revision

`revision(entry_id)` issues a single **HTTP GET** against the data.gouv.fr dataset metadata API:

```
https://www.data.gouv.fr/api/1/datasets/demandes-de-valeurs-foncieres/
```

It parses the top-level `last_modified` ISO-8601 field from the JSON response. A HEAD request is not used because the `static.data.gouv.fr` edge returns neither `ETag` nor `Last-Modified` for resource files. Returns `None` — meaning "freshness unknown" — on any network error, non-2xx response, malformed JSON, or missing field.

## Usage

```python
from gispulse.plugins.api import get_catalog_entry

entry = get_catalog_entry("dvf", "mutations")
# entry.access.protocol → AccessProtocol.REMOTE_TABLE
# entry.access.endpoint → "https://files.data.gouv.fr/geo-dvf/latest/csv"
# entry.access.format   → "text/csv"
```

To use the lighter department shards during a lazy fetch, pass the department hint alongside the bbox:

```python
from gispulse.core.plugin_model import FetchMode

result = source.fetch(
    "mutations",
    extent={"bbox": (3.0, 45.0, 4.0, 46.0), "departement": "63"},
    mode=FetchMode.REFERENCE,
)
```

The plugin registers automatically via the `gispulse.data_sources` entry-point when installed:

```bash
pip install gispulse-src-dvf
```

## References

- Issue upstream: [#184](https://github.com/imagodata/gispulse/issues/184) (pilot wave 2 — `Payload.TABLE` source)
- Issue upstream: [#198](https://github.com/imagodata/gispulse/issues/198) (`revision()` freshness probe)
- Issue upstream: [#229](https://github.com/imagodata/gispulse/issues/229) (`GeoParquetS3Fetcher` — `REMOTE_TABLE` transport, A3)
- PR merged: [#223](https://github.com/imagodata/gispulse/pull/223)
- EPIC: [#175](https://github.com/imagodata/gispulse/issues/175)
- Data portal: <https://www.data.gouv.fr/fr/datasets/demandes-de-valeurs-foncieres/>

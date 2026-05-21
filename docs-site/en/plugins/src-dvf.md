# gispulse-src-dvf

French real-estate transactions (DVF) data source for GISPulse — Etalab geo-enriched GeoParquet mirror (domain `STATISTIQUE`, jurisdiction `FR`).

## Provider

| Field             | Value                                                         |
|-------------------|---------------------------------------------------------------|
| Upstream producer | DGFiP (Direction générale des finances publiques)             |
| Provider (runtime)| Etalab (`metadata.provider = "Etalab"`)                       |
| Redistributor     | data.gouv.fr / `files.data.gouv.fr`                           |
| Dataset           | Demandes de Valeurs Foncières (DVF)                           |
| Mirror            | `files.data.gouv.fr/geo-dvf` (geo-enriched GeoParquet)        |
| Licence           | Licence Ouverte 2.0                                           |
| Cadence           | Semestrial (April / October — rolling 5-year window)          |

## Entries

| id          | Label                                        | AccessProtocol  | Endpoint                                                               | Payload | Jurisdiction |
|-------------|----------------------------------------------|-----------------|------------------------------------------------------------------------|---------|--------------|
| `mutations` | Mutations DVF (real-estate transactions)     | `REMOTE_TABLE`  | `https://files.data.gouv.fr/geo-dvf/latest/parquet/full.parquet`       | TABLE   | FR           |

Schema highlights (field names mirror the Etalab geo-dvf CSV header):

`id_mutation`, `date_mutation`, `nature_mutation`, `valeur_fonciere`, `type_local`, `surface_reelle_bati`, `surface_terrain`, `code_commune`, `nom_commune`, `code_departement`, `prefixe_section`, `section`, `numero_plan`, `id_parcelle` (synthesised join key), `longitude`, `latitude`.

The `AccessProtocol.REMOTE_TABLE` adapter is handled by `GeoParquetS3Fetcher` (A3, issue [#229](https://github.com/imagodata/gispulse/issues/229), shipped in core since v1.9.0): it scans `full.parquet` via DuckDB `read_parquet` + `httpfs` with bbox predicate pushdown — a foncier query on one département touches a few MB of the national file, not 2 GB.

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
# entry.access.endpoint → "https://files.data.gouv.fr/geo-dvf/latest/parquet/full.parquet"
# entry.access.format   → "application/parquet"
```

The plugin registers automatically via the `gispulse.data_sources` entry-point when installed:

```bash
pip install gispulse-src-dvf
```

## References

- Upstream issue: [#184](https://github.com/imagodata/gispulse/issues/184) (pilot wave 2 — `Payload.TABLE` source)
- Upstream issue: [#198](https://github.com/imagodata/gispulse/issues/198) (`revision()` freshness probe)
- Upstream issue: [#229](https://github.com/imagodata/gispulse/issues/229) (`GeoParquetS3Fetcher` — `REMOTE_TABLE` transport, A3)
- Merged PR: [#223](https://github.com/imagodata/gispulse/pull/223)
- EPIC: [#175](https://github.com/imagodata/gispulse/issues/175)
- Data portal: <https://www.data.gouv.fr/fr/datasets/demandes-de-valeurs-foncieres/>

# gispulse-src-insee

French INSEE statistical source for GISPulse. The plugin exposes IRIS contours
and reusable IRIS-level sociodemographic tables. It is intentionally source-level:
product-specific joins, aliases and scoring stay in downstream consumers such as
`gispulse-foncier`.

## Provider

| Field             | Value                                                       |
|-------------------|-------------------------------------------------------------|
| Upstream producer | INSEE                                                       |
| Redistributor     | IGN / Geoplateforme WFS and bulk download for contours; insee.fr for tables |
| Licence           | Licence Ouverte 2.0                                         |
| Cadence           | Annual                                                      |

## Entries

| id                                        | Label                                      | Protocol   | Payload | Millesime | Geography date |
|-------------------------------------------|--------------------------------------------|------------|---------|-----------|----------------|
| `iris`                                    | IRIS contours                              | WFS        | VECTOR  | service   | service        |
| `iris_bulk`                               | IRIS GE contours by department             | DOWNLOAD   | VECTOR  | 2026      | 2026-01-01     |
| `iris_population_2022`                    | IRIS population                            | TABLE_FILE | TABLE   | 2022      | 2024-01-01     |
| `iris_logement_2022`                      | IRIS logement                              | TABLE_FILE | TABLE   | 2022      | 2024-01-01     |
| `iris_menages_2022`                       | IRIS couples, familles, menages            | TABLE_FILE | TABLE   | 2022      | 2024-01-01     |
| `iris_activite_2022`                      | IRIS activite des residents                | TABLE_FILE | TABLE   | 2022      | 2024-01-01     |
| `iris_diplomes_2022`                      | IRIS diplomes et formation                 | TABLE_FILE | TABLE   | 2022      | 2024-01-01     |
| `iris_filosofi_revenus_declares_2021`     | IRIS Filosofi revenus declares             | TABLE_FILE | TABLE   | 2021      | 2022-01-01     |
| `iris_filosofi_revenus_disponibles_2021`  | IRIS Filosofi revenus disponibles          | TABLE_FILE | TABLE   | 2021      | 2022-01-01     |

## Contours IRIS

The `iris` entry uses `AccessProtocol.WFS`, endpoint
`https://data.geopf.fr/wfs/ows`, format `application/json`, typename
`STATISTICALUNITS.IRIS:contours_iris`.

The complementary `iris_bulk` entry uses the official IGN/Géoplateforme
`IRIS-GE` department GeoPackage archives through `AccessProtocol.DOWNLOAD`. Its endpoint is
templated on the official `Dxxx` zone code:

```text
https://data.geopf.fr/telechargement/download/IRIS-GE/IRIS-GE_3-0__GPKG_LAMB93_{zone}_2026-01-01/IRIS-GE_3-0__GPKG_LAMB93_{zone}_2026-01-01.7z
```

The catalogue default is `zone=D075`; national ingest should override it, for
example `zone=D069` for Rhone. The archive is a 7z-compressed GeoPackage bundle in
Lambert-93. The join key to INSEE tables is `code_iris`.

Schema highlights:

- `code_iris`
- `nom_iris`
- `insee_com`
- `nom_com`
- `type_iris`
- `geometry`

## Sociodemographic IRIS Tables

The sociodemographic entries use official insee.fr CSV ZIP downloads through
`AccessProtocol.TABLE_FILE`. They materialize raw non-spatial tables and never
invent geometry. The row key is the raw INSEE `IRIS` code.

| Entry | Official page | CSV ZIP |
|-------|---------------|---------|
| `iris_population_2022` | https://www.insee.fr/fr/statistiques/8647014 | https://www.insee.fr/fr/statistiques/fichier/8647014/base-ic-evol-struct-pop-2022_csv.zip |
| `iris_logement_2022` | https://www.insee.fr/fr/statistiques/8647012 | https://www.insee.fr/fr/statistiques/fichier/8647012/base-ic-logement-2022_csv.zip |
| `iris_menages_2022` | https://www.insee.fr/fr/statistiques/8647008 | https://www.insee.fr/fr/statistiques/fichier/8647008/base-ic-couples-familles-menages-2022_csv.zip |
| `iris_activite_2022` | https://www.insee.fr/fr/statistiques/8647006 | https://www.insee.fr/fr/statistiques/fichier/8647006/base-ic-activite-residents-2022_csv.zip |
| `iris_diplomes_2022` | https://www.insee.fr/fr/statistiques/8647010 | https://www.insee.fr/fr/statistiques/fichier/8647010/base-ic-diplomes-formation-2022_csv.zip |
| `iris_filosofi_revenus_declares_2021` | https://www.insee.fr/fr/statistiques/8229323 | https://www.insee.fr/fr/statistiques/fichier/8229323/BASE_TD_FILO_IRIS_2021_DEC_CSV.zip |
| `iris_filosofi_revenus_disponibles_2021` | https://www.insee.fr/fr/statistiques/8229323 | https://www.insee.fr/fr/statistiques/fichier/8229323/BASE_TD_FILO_IRIS_2021_DISP_CSV.zip |

Recensement IRIS 2022 tables include the geographic columns `IRIS`, `COM`,
`TYP_IRIS`, `LAB_IRIS` in the data file. Labels such as `nom_iris` and `nom_com`
come from the contour entry, not from the sociodemographic CSV itself.

Filosofi IRIS 2021 tables use the 2022-01-01 geography and expose indicators
subject to statistical confidentiality. INSEE notes that the indicators are not
summable.

## Revision

`revision("iris")` issues a single **HTTP HEAD** against the Geoplateforme WFS
GetCapabilities URL:

```text
https://data.geopf.fr/wfs/ows?SERVICE=WFS&VERSION=2.0.0&REQUEST=GetCapabilities
```

The freshness token is derived from the `ETag` header (preferred) or the
`Last-Modified` header. Returns `None` when the endpoint is unreachable or
exposes neither header.

Bulk and table entries return stable millesime/geography tokens such as
`ign-iris-ge-gpkg-lamb93-2026-01-01` or
`insee-rp-iris-population-2022-geo-2024-01-01`.

## Usage

```python
from gispulse.plugins.api import get_catalog_entry

entry = get_catalog_entry("insee", "iris_population_2022")
# entry.access.protocol -> AccessProtocol.TABLE_FILE
# entry.payload          -> Payload.TABLE
# entry.access.params    -> {"archive_format": "zip", "table_format": "csv"}

bulk = get_catalog_entry("insee", "iris_bulk")
# bulk.access.protocol -> AccessProtocol.DOWNLOAD
# bulk.access.endpoint -> ".../IRIS-GE_3-0__GPKG_LAMB93_{zone}_2026-01-01.7z"
# bulk.access.params   -> {"zone": "D075", "layer": "iris_ge"}
```

The plugin registers automatically via the `gispulse.data_sources` entry-point
when installed:

```bash
pip install gispulse-src-insee
```

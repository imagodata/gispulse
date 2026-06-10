# gispulse-src-ocsge

Declarative GISPulse source for IGN OCS GE (Occupation du Sol a Grande Echelle).

## Entries

| id | Label | AccessProtocol | Endpoint | Payload | Jurisdiction |
| --- | --- | --- | --- | --- | --- |
| `occupation-sol` | OCS GE - occupation du sol (GPKG departemental) | `DOWNLOAD` | `https://data.geopf.fr/telechargement/download/OCSGE/OCS-GE_{version}__GPKG_{crs}_{zone}_{millesime}/OCS-GE_{version}__GPKG_{crs}_{zone}_{millesime}.7z` | `VECTOR` | `FR` |

Default params target the live Paris sample verified during implementation:
`zone=D075`, `departement=75`, `millesime=2021-01-01`, `version=2-0`,
`crs=LAMB93`.

For per-department access, call `access_for(...)`: the plugin queries the
Géoplateforme `resource/OCSGE?zone=...` listing, ignores `DIFF` products, and
selects the latest listed `OCS-GE_2-0__GPKG_*` archive unless a `millesime`
and/or `crs` is requested. Availability is not uniform by department; an
unlisted pair raises an explicit error instead of returning a URL that would
404.

## Endpoint notes

IGN exposes OCS GE through the Geoplateforme Atom download API:

- resource listing: `https://data.geopf.fr/telechargement/resource/OCSGE`
- file listing: `https://data.geopf.fr/telechargement/resource/OCSGE/{subresource}`
- direct archive: `https://data.geopf.fr/telechargement/download/OCSGE/{subresource}/{subresource}.7z`

Live verification on 2026-06-02:

- `HEAD /telechargement/resource/OCSGE?lang=fre&zone=D075&format=GPKG`
  returned `HTTP 200` and `application/atom+xml`.
- The D075 GPKG resource listed
  `OCS-GE_2-0__GPKG_LAMB93_D075_2021-01-01`.
- `HEAD /telechargement/download/OCSGE/OCS-GE_2-0__GPKG_LAMB93_D075_2021-01-01/OCS-GE_2-0__GPKG_LAMB93_D075_2021-01-01.7z`
  returned `HTTP 200`, `content-type: application/x-7z-compressed`, and
  `content-length: 8275603`.
- `resource/OCSGE?zone=D063` listed D063 GPKG millesimes `2019-01-01` and
  `2022-01-01`; the `HEAD` for `LAMB93_D063_2022-01-01` returned `HTTP 200`.
- `resource/OCSGE?zone=D083` listed D083 GPKG millesimes `2017-01-01`,
  `2020-01-01`, and `2023-01-01`; `HEAD` for `D083_2021-01-01` returned
  `HTTP 404`, while `D083_2023-01-01` returned `HTTP 200`.

Verified DROM CRS/latest GPKG pairs on 2026-06-02:

| Department | CRS | Latest GPKG millesime | Download HEAD |
| --- | --- | --- | --- |
| `971` Guadeloupe | `RGAF09UTM20` | `2022-01-01` | `200` |
| `972` Martinique | `RGAF09UTM20` | `2022-01-01` | `200` |
| `973` Guyane | `RGFG95UTM22` | `2022-01-01` | `200` |
| `974` La Reunion | `RGR92UTM40S` | `2022-01-01` | `200` |
| `976` Mayotte | `RGM04UTM38S` | `2023-01-01` | `200` |

## Schema

The verified `OCCUPATION_SOL.gpkg` table uses:

- `fid`
- `the_geom`
- `id`
- `code_cs`
- `code_us`
- `millesime`
- `source`
- `ossature`
- `id_origine`
- `code_or`

The geometry join key is spatial: downstream foncier workflows should join
OCS GE polygons to parcels with `ST_Intersects`, not with an attribute key.

## Artificialisation nuance

This plugin exposes the raw OCS GE coverage/use polygons and documents the
CS/US hints used by the derived artificialisation workflow. The official
derived product is separately listed by IGN as `OCSGE-ARTIFICIALISATION` and
applies decree thresholds/topological regrouping. For legally reported ZAN
surfaces, prefer that derived product or reproduce the full documented
post-processing.

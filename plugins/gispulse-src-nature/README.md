# gispulse-src-nature

IGN / INPN protected natural areas for GISPulse via API Carto Nature
(domain: `ENVIRONNEMENT`, jurisdiction: `FR`).

## Provider

| Field             | Value                           |
|-------------------|---------------------------------|
| Upstream producer | IGN / INPN                      |
| Platform          | API Carto Nature                |
| Base endpoint     | `https://apicarto.ign.fr/api/nature` |
| Licence           | Licence Ouverte 2.0             |

## Entries

All entries use `AccessProtocol.REST_API`, format `application/json`, and
`params = {"geom_param": "geom"}` so the REST GeoJSON fetcher sends the
runtime extent as a GeoJSON polygon in the `geom` query parameter.

| id                | Label                          | Path              |
|-------------------|--------------------------------|-------------------|
| `natura-habitat`  | Natura 2000 directive Habitat  | `/natura-habitat` |
| `natura-oiseaux`  | Natura 2000 directive Oiseaux  | `/natura-oiseaux` |
| `znieff1`         | ZNIEFF type 1                  | `/znieff1`        |
| `znieff2`         | ZNIEFF type 2                  | `/znieff2`        |

## Revision

`revision(entry_id)` returns `None`: the API is queried with a runtime
geometry filter and exposes no simple dataset-wide freshness probe.

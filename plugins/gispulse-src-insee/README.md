# gispulse-src-insee

French INSEE statistical units source for GISPulse. The first entry is the IRIS infra-communal statistical mesh, redistributed through the IGN Géoplateforme WFS (domain: `STATISTIQUE`, jurisdiction: `FR`).

## Provider

| Field             | Value                                                       |
|-------------------|-------------------------------------------------------------|
| Upstream producer | INSEE                                                       |
| Redistributor     | IGN / Géoplateforme WFS                                     |
| Platform          | WFS Géoplateforme                                           |
| Licence           | Licence Ouverte 2.0                                         |
| Cadence           | Annual                                                      |

## Entries

All entries use `AccessProtocol.WFS`, endpoint `https://data.geopf.fr/wfs/ows`, format `application/json`.

| id     | Label                                  | WFS typename                         | Payload | Jurisdiction |
|--------|----------------------------------------|--------------------------------------|---------|--------------|
| `iris` | IRIS — découpage infra-communal INSEE  | `STATISTICALUNITS.IRIS:contour_iris` | VECTOR  | FR           |

Schema highlights for `iris`:

- `code_iris`
- `nom_iris`
- `insee_com`
- `nom_com`
- `type_iris`
- `geometry`

Field names are raw upstream names. The plugin does not normalise INSEE commune codes or IRIS labels.

## Revision

`revision(entry_id)` issues a single **HTTP HEAD** against the Géoplateforme WFS GetCapabilities URL:

```text
https://data.geopf.fr/wfs/ows?SERVICE=WFS&VERSION=2.0.0&REQUEST=GetCapabilities
```

The freshness token is derived from the `ETag` header (preferred) or the `Last-Modified` header. Returns `None` when the endpoint is unreachable or exposes neither header.

## Usage

```python
from gispulse.plugins.api import get_catalog_entry

entry = get_catalog_entry("insee", "iris")
# entry.access.protocol -> AccessProtocol.WFS
# entry.access.endpoint -> "https://data.geopf.fr/wfs/ows"
# entry.access.params   -> {"typename": "STATISTICALUNITS.IRIS:contour_iris"}
```

The plugin registers automatically via the `gispulse.data_sources` entry-point when installed:

```bash
pip install gispulse-src-insee
```

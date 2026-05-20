# gispulse-src-cadastre

French cadastre data source for GISPulse — parcels, communes and buildings via IGN Géoplateforme WFS (domain: `FONCIER`, jurisdiction: `FR`).

## Provider

| Field          | Value                                              |
|----------------|----------------------------------------------------|
| Upstream producer | IGN (Institut national de l'information géographique et forestière) |
| Redistributor  | IGN Géoplateforme (public WFS, no API key required) |
| Dataset        | Parcellaire Express (`CADASTRALPARCELS.PARCELLAIRE_EXPRESS`) |
| Licence        | Licence Ouverte 2.0                                |
| Cadence        | Annual millésime (dataset-wide)                    |

## Entries

| id         | Label                      | AccessProtocol | Endpoint                          | WFS typename                                  | Payload | Jurisdiction |
|------------|----------------------------|----------------|-----------------------------------|-----------------------------------------------|---------|--------------|
| `parcelles` | Parcelles cadastrales      | `WFS`          | `https://data.geopf.fr/wfs/ows`   | `CADASTRALPARCELS.PARCELLAIRE_EXPRESS:parcelle` | VECTOR  | FR           |
| `communes`  | Communes cadastrales       | `WFS`          | `https://data.geopf.fr/wfs/ows`   | `CADASTRALPARCELS.PARCELLAIRE_EXPRESS:commune`  | VECTOR  | FR           |
| `batiments` | Bâtiments cadastraux       | `WFS`          | `https://data.geopf.fr/wfs/ows`   | `CADASTRALPARCELS.PARCELLAIRE_EXPRESS:batiment` | VECTOR  | FR           |

Schema highlights:

- **parcelles**: `idu`, `commune`, `section`, `numero`, `contenance` (int), `geometry`
- **communes**: `idu`, `nom`, `code_insee`, `geometry`
- **batiments**: `idu`, `nature`, `geometry`

## Revision

`revision(entry_id)` issues a single **HTTP HEAD** against the Géoplateforme WFS GetCapabilities URL:

```
https://data.geopf.fr/wfs/ows?SERVICE=WFS&VERSION=2.0.0&REQUEST=GetCapabilities
```

The freshness token is derived from the `ETag` header (preferred) or the `Last-Modified` header. The Parcellaire Express millésime is dataset-wide, so all three entries share one probe. Returns `None` — meaning "freshness unknown" — when the endpoint is unreachable or exposes neither header; the source watcher skips it rather than emit a spurious change.

## Usage

```python
from gispulse.plugins.api import get_catalog_entry

entry = get_catalog_entry("cadastre", "parcelles")
# entry.access.protocol → AccessProtocol.WFS
# entry.access.endpoint → "https://data.geopf.fr/wfs/ows"
# entry.access.params   → {"typename": "CADASTRALPARCELS.PARCELLAIRE_EXPRESS:parcelle"}
```

The plugin registers automatically via the `gispulse.data_sources` entry-point when installed:

```bash
pip install gispulse-src-cadastre
```

## References

- Issue upstream: [#184](https://github.com/imagodata/gispulse/issues/184) (pilot wave 1 — `DeclarativeSource` contract)
- Issue upstream: [#198](https://github.com/imagodata/gispulse/issues/198) (`revision()` freshness probe)
- EPIC: [#175](https://github.com/imagodata/gispulse/issues/175) (SOURCE→CAPABILITY→SINK unified plugins)
- Data portal: <https://data.geopf.fr/>

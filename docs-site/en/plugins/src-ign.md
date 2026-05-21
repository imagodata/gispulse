# gispulse-src-ign

IGN reference data source for GISPulse — BD TOPO vector layers and Admin Express administrative boundaries via IGN Géoplateforme WFS (domain `BASE`, jurisdiction `FR`).

## Provider

| Field             | Value                                                                     |
|-------------------|---------------------------------------------------------------------------|
| Upstream producer | IGN (Institut national de l'information géographique et forestière)       |
| Redistributor     | IGN Géoplateforme (public WFS, no API key required)                       |
| Datasets          | BD TOPO v3 (`BDTOPO_V3`), Admin Express COG (`ADMINEXPRESS-COG.LATEST`)   |
| Licence           | Licence Ouverte 2.0                                                       |
| Cadence           | Annual millésime (service-wide)                                           |

> **Note:** GEOFLA is deprecated upstream. The `geofla` entry id is kept as a legacy alias of the Admin Express `communes` entry and resolves transparently.

## Entries

All entries use `AccessProtocol.WFS`, endpoint `https://data.geopf.fr/wfs/ows`, format `application/json`.

| id             | Label                          | WFS typename                           | Dataset        | Payload | Jurisdiction |
|----------------|--------------------------------|----------------------------------------|----------------|---------|--------------|
| `batiments`    | Buildings (BD TOPO)            | `BDTOPO_V3:batiment`                   | BD TOPO v3     | VECTOR  | FR           |
| `routes`       | Road segments (BD TOPO)        | `BDTOPO_V3:troncon_de_route`           | BD TOPO v3     | VECTOR  | FR           |
| `cours_eau`    | Waterways (BD TOPO)            | `BDTOPO_V3:cours_d_eau`                | BD TOPO v3     | VECTOR  | FR           |
| `communes`     | Communes (Admin Express)       | `ADMINEXPRESS-COG.LATEST:commune`      | Admin Express  | VECTOR  | FR           |
| `departements` | Départements (Admin Express)   | `ADMINEXPRESS-COG.LATEST:departement`  | Admin Express  | VECTOR  | FR           |
| `regions`      | Régions (Admin Express)        | `ADMINEXPRESS-COG.LATEST:region`       | Admin Express  | VECTOR  | FR           |

> **Legacy alias:** `geofla` resolves to `communes` in `_entry()` / `revision()` lookups but is **not** listed by `entries()` or `catalog()`. Use `communes` for new code; `geofla` is accepted silently for backwards-compat.

## Revision

`revision(entry_id)` issues a single **HTTP HEAD** against the Géoplateforme WFS GetCapabilities URL:

```
https://data.geopf.fr/wfs/ows?SERVICE=WFS&VERSION=2.0.0&REQUEST=GetCapabilities
```

The freshness token is derived from the `ETag` header (preferred) or the `Last-Modified` header. The IGN millésime is service-wide, so all six entries share one probe. The legacy `geofla` alias is resolved before the id is validated. Returns `None` — meaning "freshness unknown" — when the endpoint is unreachable or exposes neither header.

## Usage

```python
from gispulse.plugins.api import get_catalog_entry

entry = get_catalog_entry("ign", "communes")
# entry.access.protocol → AccessProtocol.WFS
# entry.access.endpoint → "https://data.geopf.fr/wfs/ows"
# entry.access.params   → {"typename": "ADMINEXPRESS-COG.LATEST:commune"}

# Legacy alias — resolves to communes internally (not listed in catalog):
entry = get_catalog_entry("ign", "geofla")  # equivalent to communes
```

The plugin registers automatically via the `gispulse.data_sources` entry-point when installed:

```bash
pip install gispulse-src-ign
```

## References

- Upstream issue: [#194](https://github.com/imagodata/gispulse/issues/194) (pilot — multi-layer `DeclarativeSource`, BD TOPO + Admin Express)
- Upstream issue: [#197](https://github.com/imagodata/gispulse/issues/197) (source watcher)
- Upstream issue: [#198](https://github.com/imagodata/gispulse/issues/198) (`revision()` freshness probe)
- EPIC: [#175](https://github.com/imagodata/gispulse/issues/175)
- Data portal: <https://data.geopf.fr/>

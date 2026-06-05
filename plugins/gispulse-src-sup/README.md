# gispulse-src-sup

French Servitudes d'Utilité Publique (SUP) data source for GISPulse —
Géoplateforme WFS layers from the Géoportail de l'Urbanisme.

## Provider

| Field | Value |
|-------|-------|
| Upstream producer | IGN / Géoportail de l'Urbanisme |
| Platform | WFS SUP |
| Endpoint | `https://data.geopf.fr/wfs/ows` |
| Namespace | `wfs_sup` |
| Payload | `VECTOR` |
| Domain | `REGLEMENTAIRE` |
| Jurisdiction | `FR` |

`SupSource` is deliberately declarative. It exposes raw SUP WFS feature
types and two filtered assiette views; it does not implement
`RegulatorySource` or `ruleset()`.

## Entries

All entries use `AccessProtocol.WFS`, endpoint
`https://data.geopf.fr/wfs/ows`, and format `application/json`.

| id | WFS typename | CQL filter |
|----|--------------|------------|
| `servitude` | `wfs_sup:servitude` | |
| `assiette-surf` | `wfs_sup:assiette_sup_s` | |
| `assiette-lin` | `wfs_sup:assiette_sup_l` | |
| `assiette-pct` | `wfs_sup:assiette_sup_p` | |
| `generateur-surf` | `wfs_sup:generateur_sup_s` | |
| `generateur-lin` | `wfs_sup:generateur_sup_l` | |
| `generateur-pct` | `wfs_sup:generateur_sup_p` | |
| `heritage-abf` | `wfs_sup:assiette_sup_s` | `suptype IN ('AC1','AC2','AC4')` |
| `risk-ppr-zoning` | `wfs_sup:assiette_sup_s` | `suptype IN ('PM1','PM1BIS','PM3')` |

Schema highlights: `gid`, `suptype`, `idsup`, `nomsuplitt`, `geometry`.
The schema remains raw WFS SUP; ABF/PPR interpretation belongs to
product plugins such as Permis Check.

## Revision

`revision(entry_id)` issues a single HTTP HEAD against:

```text
https://data.geopf.fr/wfs/ows?SERVICE=WFS&VERSION=2.0.0&REQUEST=GetCapabilities
```

The freshness token is derived from `ETag` or `Last-Modified`. Network
errors return `None`.

## Usage

```python
from gispulse_src_sup.source import SupSource

src = SupSource()
entry = next(e for e in src.catalog() if e.id == "heritage-abf")
entry.access.params
# {"typename": "wfs_sup:assiette_sup_s", "cql_filter": "suptype IN ('AC1','AC2','AC4')"}
```

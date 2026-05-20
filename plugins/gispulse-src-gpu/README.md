# gispulse-src-gpu

French urban-planning documents data source for GISPulse — Géoportail de l'Urbanisme WFS layers: zoning, prescriptions and informational layers (domain: `REGLEMENTAIRE`, jurisdiction: `FR`).

## Provider

| Field             | Value                                                                                     |
|-------------------|-------------------------------------------------------------------------------------------|
| Upstream producer | IGN + DGALN (Direction générale de l'aménagement, du logement et de la nature)            |
| Platform          | Géoportail de l'Urbanisme (<https://www.geoportail-urbanisme.gouv.fr/>)                   |
| Redistributor     | IGN Géoplateforme (public WFS `wfs_du` namespace, no API key required)                    |
| Mandate           | Loi ALUR / ordonnance 2013-1184 (dematerialised urban-planning documents)                 |
| Licence           | Licence Ouverte 2.0                                                                       |
| Cadence           | Continuous (updated as communes publish/approve new PLU documents)                        |

> **Note:** `GpuSource` is declared with `SourceDomain.REGLEMENTAIRE` and is semantically a `RegulatorySource`. Promotion (wiring `ruleset()` over the `wfs_du` attributes) is deferred to a follow-up plugin once the `RuleClause`-to-PLU mapping is stabilised.

> **Note:** Servitudes d'utilité publique (SUP — `servitude`, `assiette_sup_*`, `generateur_sup_*`, `acte_sup`) are intentionally **not** in this plugin. They are conceptually distinct and warrant a dedicated `gispulse-src-sup` package.

## Entries

All entries use `AccessProtocol.WFS`, endpoint `https://data.geopf.fr/wfs/ows`, format `application/json`.

| id                    | Label                                                     | WFS typename                    | Payload | Jurisdiction |
|-----------------------|-----------------------------------------------------------|---------------------------------|---------|--------------|
| `zone-urba`           | Zones d'urbanisme (PLU, PLUi, POS)                        | `wfs_du:zone_urba`              | VECTOR  | FR           |
| `doc-urba`            | Documents d'urbanisme — emprises et métadonnées           | `wfs_du:doc_urba`               | VECTOR  | FR           |
| `secteur-cc`          | Secteurs de carte communale                               | `wfs_du:secteur_cc`             | VECTOR  | FR           |
| `prescription-surf`   | Prescriptions surfaciques                                 | `wfs_du:prescription_surf`      | VECTOR  | FR           |
| `prescription-lin`    | Prescriptions linéaires                                   | `wfs_du:prescription_lin`       | VECTOR  | FR           |
| `prescription-pct`    | Prescriptions ponctuelles                                 | `wfs_du:prescription_pct`       | VECTOR  | FR           |
| `info-surf`           | Informations surfaciques                                  | `wfs_du:info_surf`              | VECTOR  | FR           |
| `info-lin`            | Informations linéaires                                    | `wfs_du:info_lin`               | VECTOR  | FR           |
| `info-pct`            | Informations ponctuelles                                  | `wfs_du:info_pct`               | VECTOR  | FR           |

Schema highlights:

- **Common fields** (all entries): `gid` (int), `idurba` (str — parent document id, joins to `doc-urba`), `geometry`
- **zone-urba**: adds `libelle`, `libelong`, `typezone` (U/AU/A/N), `destdomi`, `nomfic`, `urlfic`
- **doc-urba**: adds `typedoc` (PLU/PLUi/POS/CC/RNU), `datappro`, `datefin`, `datvalid`, `intercoid`, `insee`, `siren`
- **secteur-cc**: adds `libelle`, `libelong`, `typesect` (constructible/non-constructible), `insee`
- **prescription-{surf,lin,pct}**: adds `libelle`, `txt`, `typepsc`, `stypepsc`, `nomfic`, `urlfic`
- **info-{surf,lin,pct}**: adds `libelle`, `txt`, `typeinf`, `stypeinf`, `nomfic`, `urlfic`

## Revision

`revision(entry_id)` issues a single **HTTP HEAD** against the Géoplateforme WFS GetCapabilities URL:

```
https://data.geopf.fr/wfs/ows?SERVICE=WFS&VERSION=2.0.0&REQUEST=GetCapabilities
```

The freshness token is derived from the `ETag` header (preferred) or the `Last-Modified` header. The GPU millésime is service-wide (one consolidated GetCapabilities for all `wfs_du` layers), so all nine entries share one probe. Returns `None` — meaning "freshness unknown" — when the endpoint is unreachable or exposes neither header.

## Usage

```python
from gispulse.plugins.api import get_catalog_entry

entry = get_catalog_entry("gpu", "zone-urba")
# entry.access.protocol → AccessProtocol.WFS
# entry.access.endpoint → "https://data.geopf.fr/wfs/ows"
# entry.access.params   → {"typename": "wfs_du:zone_urba"}

entry = get_catalog_entry("gpu", "doc-urba")
entry = get_catalog_entry("gpu", "prescription-surf")
```

The plugin registers automatically via the `gispulse.data_sources` entry-point when installed:

```bash
pip install gispulse-src-gpu
```

## References

- Issue upstream: [#184](https://github.com/imagodata/gispulse/issues/184) (pilot wave 2 — multi-entry regulatory source)
- Issue upstream: [#198](https://github.com/imagodata/gispulse/issues/198) (`revision()` freshness probe)
- PR merged: [#224](https://github.com/imagodata/gispulse/pull/224)
- EPIC: [#175](https://github.com/imagodata/gispulse/issues/175)
- Data portal: <https://www.geoportail-urbanisme.gouv.fr/>

# gispulse-src-gpu

Source des documents d'urbanisme français pour GISPulse — couches WFS du Géoportail de l'Urbanisme : zonage, prescriptions et couches informatives (domaine `REGLEMENTAIRE`, juridiction `FR`).

## Fournisseur

| Champ                | Valeur                                                                                    |
|----------------------|-------------------------------------------------------------------------------------------|
| Producteur amont     | IGN + DGALN (Direction générale de l'aménagement, du logement et de la nature)            |
| Plateforme           | Géoportail de l'Urbanisme (<https://www.geoportail-urbanisme.gouv.fr/>)                   |
| Redistributeur       | IGN Géoplateforme (WFS public namespace `wfs_du`, sans clé d'API)                         |
| Cadre légal          | Loi ALUR / ordonnance 2013-1184 (dématérialisation des documents d'urbanisme)             |
| Licence              | Licence Ouverte 2.0                                                                       |
| Cadence              | Continue (mise à jour au fil des publications/approbations de PLU par les communes)       |

> **Note :** `GpuSource` est déclarée avec `SourceDomain.REGLEMENTAIRE` et représente sémantiquement une `RegulatorySource`. La promotion (câblage `ruleset()` sur les attributs `wfs_du`) est reportée à un plugin de suivi, le temps de stabiliser la cartographie `RuleClause` ↔ PLU.

> **Note :** Les servitudes d'utilité publique (SUP — `servitude`, `assiette_sup_*`, `generateur_sup_*`, `acte_sup`) ne sont **pas** dans ce plugin. Elles sont conceptuellement distinctes et justifient un paquet dédié `gispulse-src-sup`.

## Entrées

Toutes les entrées utilisent `AccessProtocol.WFS`, endpoint `https://data.geopf.fr/wfs/ows`, format `application/json`.

| id                    | Libellé                                                     | WFS typename                    | Payload | Juridiction |
|-----------------------|-------------------------------------------------------------|---------------------------------|---------|-------------|
| `zone-urba`           | Zones d'urbanisme (PLU, PLUi, POS)                          | `wfs_du:zone_urba`              | VECTOR  | FR          |
| `doc-urba`            | Documents d'urbanisme — emprises et métadonnées             | `wfs_du:doc_urba`               | VECTOR  | FR          |
| `secteur-cc`          | Secteurs de carte communale                                 | `wfs_du:secteur_cc`             | VECTOR  | FR          |
| `prescription-surf`   | Prescriptions surfaciques                                   | `wfs_du:prescription_surf`      | VECTOR  | FR          |
| `prescription-lin`    | Prescriptions linéaires                                     | `wfs_du:prescription_lin`       | VECTOR  | FR          |
| `prescription-pct`    | Prescriptions ponctuelles                                   | `wfs_du:prescription_pct`       | VECTOR  | FR          |
| `info-surf`           | Informations surfaciques                                    | `wfs_du:info_surf`              | VECTOR  | FR          |
| `info-lin`            | Informations linéaires                                      | `wfs_du:info_lin`               | VECTOR  | FR          |
| `info-pct`            | Informations ponctuelles                                    | `wfs_du:info_pct`               | VECTOR  | FR          |

Schéma (extrait) :

- **Champs communs** (toutes les entrées) : `gid` (int), `idurba` (str — identifiant du document parent, jointure avec `doc-urba`), `geometry`
- **zone-urba** : ajoute `libelle`, `libelong`, `typezone` (U/AU/A/N), `destdomi`, `nomfic`, `urlfic`
- **doc-urba** : ajoute `typedoc` (PLU/PLUi/POS/CC/RNU), `datappro`, `datefin`, `datvalid`, `intercoid`, `insee`, `siren`
- **secteur-cc** : ajoute `libelle`, `libelong`, `typesect` (constructible / non constructible), `insee`
- **prescription-{surf,lin,pct}** : ajoutent `libelle`, `txt`, `typepsc`, `stypepsc`, `nomfic`, `urlfic`
- **info-{surf,lin,pct}** : ajoutent `libelle`, `txt`, `typeinf`, `stypeinf`, `nomfic`, `urlfic`

## Revision

`revision(entry_id)` exécute un seul appel **HTTP HEAD** sur l'URL `GetCapabilities` du WFS Géoplateforme :

```
https://data.geopf.fr/wfs/ows?SERVICE=WFS&VERSION=2.0.0&REQUEST=GetCapabilities
```

Le jeton de fraîcheur dérive de l'en-tête `ETag` (prioritaire) ou `Last-Modified`. Le millésime GPU est porté par le service (un seul `GetCapabilities` consolidé pour toutes les couches `wfs_du`), donc les neuf entrées partagent une sonde unique. Retourne `None` — « fraîcheur inconnue » — si l'endpoint est injoignable ou si aucun en-tête n'est exposé.

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

Le plugin s'enregistre automatiquement via le entry-point `gispulse.data_sources` à l'installation :

```bash
pip install gispulse-src-gpu
```

## Références

- Issue amont : [#184](https://github.com/imagodata/gispulse/issues/184) (vague 2 du pilote — source réglementaire multi-entrée)
- Issue amont : [#198](https://github.com/imagodata/gispulse/issues/198) (sonde de fraîcheur `revision()`)
- PR mergée : [#224](https://github.com/imagodata/gispulse/pull/224)
- EPIC : [#175](https://github.com/imagodata/gispulse/issues/175)
- Portail data : <https://www.geoportail-urbanisme.gouv.fr/>

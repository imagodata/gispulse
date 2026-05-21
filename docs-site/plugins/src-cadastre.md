# gispulse-src-cadastre

Source du cadastre français pour GISPulse — parcelles, communes et bâtiments via le WFS IGN Géoplateforme (domaine `FONCIER`, juridiction `FR`).

## Fournisseur

| Champ                | Valeur                                                                  |
|----------------------|-------------------------------------------------------------------------|
| Producteur amont     | IGN (Institut national de l'information géographique et forestière)     |
| Redistributeur       | IGN Géoplateforme (WFS public, sans clé d'API)                          |
| Jeu de données       | Parcellaire Express (`CADASTRALPARCELS.PARCELLAIRE_EXPRESS`)            |
| Licence              | Licence Ouverte 2.0                                                     |
| Cadence              | Millésime annuel (à l'échelle du jeu de données)                        |

## Entrées

| id          | Libellé                    | AccessProtocol | Endpoint                          | WFS typename                                    | Payload | Juridiction |
|-------------|----------------------------|----------------|-----------------------------------|-------------------------------------------------|---------|-------------|
| `parcelles` | Parcelles cadastrales      | `WFS`          | `https://data.geopf.fr/wfs/ows`   | `CADASTRALPARCELS.PARCELLAIRE_EXPRESS:parcelle` | VECTOR  | FR          |
| `communes`  | Communes cadastrales       | `WFS`          | `https://data.geopf.fr/wfs/ows`   | `CADASTRALPARCELS.PARCELLAIRE_EXPRESS:commune`  | VECTOR  | FR          |
| `batiments` | Bâtiments cadastraux       | `WFS`          | `https://data.geopf.fr/wfs/ows`   | `CADASTRALPARCELS.PARCELLAIRE_EXPRESS:batiment` | VECTOR  | FR          |

Schéma (extrait) :

- **parcelles** : `idu`, `commune`, `section`, `numero`, `contenance` (int), `geometry`
- **communes** : `idu`, `nom`, `code_insee`, `geometry`
- **batiments** : `idu`, `nature`, `geometry`

## Revision

`revision(entry_id)` exécute un seul appel **HTTP HEAD** sur l'URL `GetCapabilities` du WFS Géoplateforme :

```
https://data.geopf.fr/wfs/ows?SERVICE=WFS&VERSION=2.0.0&REQUEST=GetCapabilities
```

Le jeton de fraîcheur dérive de l'en-tête `ETag` (prioritaire) ou `Last-Modified`. Le millésime Parcellaire Express est porté par le jeu de données, donc les trois entrées partagent une sonde unique. Retourne `None` — « fraîcheur inconnue » — si l'endpoint est injoignable ou si aucun en-tête n'est exposé ; le watcher saute la source plutôt que d'émettre un faux changement.

## Usage

```python
from gispulse.plugins.api import get_catalog_entry

entry = get_catalog_entry("cadastre", "parcelles")
# entry.access.protocol → AccessProtocol.WFS
# entry.access.endpoint → "https://data.geopf.fr/wfs/ows"
# entry.access.params   → {"typename": "CADASTRALPARCELS.PARCELLAIRE_EXPRESS:parcelle"}
```

Le plugin s'enregistre automatiquement via le entry-point `gispulse.data_sources` à l'installation :

```bash
pip install gispulse-src-cadastre
```

## Références

- Issue amont : [#184](https://github.com/imagodata/gispulse/issues/184) (vague 1 du pilote — contrat `DeclarativeSource`)
- Issue amont : [#198](https://github.com/imagodata/gispulse/issues/198) (sonde de fraîcheur `revision()`)
- EPIC : [#175](https://github.com/imagodata/gispulse/issues/175) (plugins unifiés SOURCE → CAPABILITY → SINK)
- Portail data : <https://data.geopf.fr/>

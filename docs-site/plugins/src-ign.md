# gispulse-src-ign

Source des données de référence IGN pour GISPulse — couches vectorielles BD TOPO et limites administratives Admin Express via le WFS IGN Géoplateforme (domaine `BASE`, juridiction `FR`).

## Fournisseur

| Champ                | Valeur                                                                    |
|----------------------|---------------------------------------------------------------------------|
| Producteur amont     | IGN (Institut national de l'information géographique et forestière)       |
| Redistributeur       | IGN Géoplateforme (WFS public, sans clé d'API)                            |
| Jeux de données      | BD TOPO v3 (`BDTOPO_V3`), Admin Express COG (`ADMINEXPRESS-COG.LATEST`)   |
| Licence              | Licence Ouverte 2.0                                                       |
| Cadence              | Millésime annuel (à l'échelle du service)                                 |

> **Note :** GEOFLA est déprécié en amont. L'identifiant `geofla` est conservé comme alias de l'entrée `communes` d'Admin Express et se résout de façon transparente.

## Entrées

Toutes les entrées utilisent `AccessProtocol.WFS`, endpoint `https://data.geopf.fr/wfs/ows`, format `application/json`.

| id             | Libellé                          | WFS typename                           | Jeu de données   | Payload | Juridiction |
|----------------|----------------------------------|----------------------------------------|------------------|---------|-------------|
| `batiments`    | Bâtiments (BD TOPO)              | `BDTOPO_V3:batiment`                   | BD TOPO v3       | VECTOR  | FR          |
| `routes`       | Tronçons de route (BD TOPO)      | `BDTOPO_V3:troncon_de_route`           | BD TOPO v3       | VECTOR  | FR          |
| `cours_eau`    | Cours d'eau (BD TOPO)            | `BDTOPO_V3:cours_d_eau`                | BD TOPO v3       | VECTOR  | FR          |
| `communes`     | Communes (Admin Express)         | `ADMINEXPRESS-COG.LATEST:commune`      | Admin Express    | VECTOR  | FR          |
| `departements` | Départements (Admin Express)     | `ADMINEXPRESS-COG.LATEST:departement`  | Admin Express    | VECTOR  | FR          |
| `regions`      | Régions (Admin Express)          | `ADMINEXPRESS-COG.LATEST:region`       | Admin Express    | VECTOR  | FR          |

> **Alias historique :** `geofla` se résout vers `communes` dans `_entry()` / `revision()`, mais n'est **pas** listé par `entries()` ni `catalog()`. Préférer `communes` pour le nouveau code ; `geofla` est accepté silencieusement par rétro-compatibilité.

## Revision

`revision(entry_id)` exécute un seul appel **HTTP HEAD** sur l'URL `GetCapabilities` du WFS Géoplateforme :

```
https://data.geopf.fr/wfs/ows?SERVICE=WFS&VERSION=2.0.0&REQUEST=GetCapabilities
```

Le jeton de fraîcheur dérive de l'en-tête `ETag` (prioritaire) ou `Last-Modified`. Le millésime IGN est porté par le service, donc les six entrées partagent une sonde unique. L'alias historique `geofla` est résolu avant validation de l'id. Retourne `None` — « fraîcheur inconnue » — si l'endpoint est injoignable ou si aucun en-tête n'est exposé.

## Usage

```python
from gispulse.plugins.api import get_catalog_entry

entry = get_catalog_entry("ign", "communes")
# entry.access.protocol → AccessProtocol.WFS
# entry.access.endpoint → "https://data.geopf.fr/wfs/ows"
# entry.access.params   → {"typename": "ADMINEXPRESS-COG.LATEST:commune"}

# Alias historique — se résout vers communes (non listé dans le catalogue) :
entry = get_catalog_entry("ign", "geofla")  # équivaut à communes
```

Le plugin s'enregistre automatiquement via le entry-point `gispulse.data_sources` à l'installation :

```bash
pip install gispulse-src-ign
```

## Références

- Issue amont : [#194](https://github.com/imagodata/gispulse/issues/194) (pilote — `DeclarativeSource` multi-couches, BD TOPO + Admin Express)
- Issue amont : [#197](https://github.com/imagodata/gispulse/issues/197) (watcher de sources)
- Issue amont : [#198](https://github.com/imagodata/gispulse/issues/198) (sonde de fraîcheur `revision()`)
- EPIC : [#175](https://github.com/imagodata/gispulse/issues/175)
- Portail data : <https://data.geopf.fr/>

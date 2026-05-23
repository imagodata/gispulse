---
title: Intégrer QGIS sans plugin
description: 4 scénarios pour consommer GISPulse depuis QGIS via standards (GPKG, OGC API Features, MVT, PyQGIS) — sans installer le plugin natif.
---

# Intégrer QGIS sans plugin

Cette page couvre les **4 scénarios standards** pour utiliser GISPulse depuis QGIS sans dépendre d'un plugin natif. Le [plugin GISPulse pour QGIS](../plugins/qgis) reste recommandé pour le confort UI (dataset browser, job runner) ; ces scénarios fonctionnent avec n'importe quelle installation QGIS 3.28+.

## Pré-requis

- QGIS 3.28 ou supérieur
- Une instance GISPulse joignable (locale `http://localhost:8001` ou demo `https://demo.gispulse.dev`)
- Une API key si l'instance est protégée (variable `GISPULSE_API_KEY`)

::: tip
Pour tester sans rien installer côté serveur : `pip install gispulse && gispulse serve --port 8001`. La demo publique [demo.gispulse.dev](https://demo.gispulse.dev) sert aussi tous les endpoints en lecture.
:::

## Scénario A — GPKG enrichi en drag-drop

C'est le flux le plus simple : exécuter un pipeline GISPulse qui produit un `.gpkg` et le glisser dans QGIS. Les sidecars `.style.qml` / `.legend.json` émis à côté du fichier sont reconnus automatiquement par QGIS au chargement.

```bash
# 1. Lancer le pipeline (CLI ou HTTP)
gispulse run pipeline buffer_road_setback.json \
    --input parcels.gpkg \
    --output parcels_with_setback.gpkg

# 2. Lister les fichiers générés
ls -la parcels_with_setback.gpkg*
# parcels_with_setback.gpkg
# parcels_with_setback.gpkg.style.qml      ← style auto-importé par QGIS
# parcels_with_setback.gpkg.legend.json    ← légende structurée
```

Glisser `parcels_with_setback.gpkg` dans QGIS → la couche apparaît stylée (couleurs, classes, labels) sans intervention.

## Scénario B — Live OGC API Features / WFS

Pour des données qui changent (triggers actifs, dataset partagé), connecter QGIS via OGC API Features évite d'avoir à recharger un fichier à chaque modif.

1. Dans QGIS : **Couche → Ajouter une couche → Ajouter une couche WFS / OGC API – Features…**
2. **Nouvelle connexion** :
   - Nom : `GISPulse local`
   - URL : `http://localhost:8001/ogc/features` (ou `/ogc` selon version)
   - Version : `OGC API Features` (ou `WFS 2.0` en fallback)
3. Si auth : ajouter l'en-tête `X-API-Key: <ta_clé>` dans **Authentification → Configurations → En-têtes HTTP**
4. **Connexion → Découvrir les collections → Glisser** une collection dans le canvas

QGIS interroge alors `/ogc/features/collections/{id}/items?bbox=…&limit=1000` au déplacement de la carte. Le rafraîchissement (F5) recharge la dernière version.

## Scénario C — Vector tiles MVT (PostGIS backend)

Pour des datasets volumineux (>100 k features), les MVT donnent une expérience zoom-fluide sans charger toute la couche. Nécessite un backend PostGIS côté GISPulse.

1. Récupérer le doc TileJSON :
   ```bash
   curl http://localhost:8001/tiles/parcels/tilejson.json
   ```
   ```json
   {
     "tilejson": "3.0.0",
     "tiles": ["http://localhost:8001/tiles/parcels/{z}/{x}/{y}.mvt"],
     "minzoom": 0,
     "maxzoom": 18,
     "vector_layers": [{"id": "parcels", "fields": {"id": "Number", "code": "String"}}]
   }
   ```

2. Dans QGIS : **Couche → Ajouter une couche → Ajouter une couche Vector Tile…**
   - Type : **Service URL**
   - URL : `http://localhost:8001/tiles/parcels/{z}/{x}/{y}.mvt`
   - Niveau zoom min/max : copier depuis le TileJSON

::: warning Backend
Les tuiles MVT requièrent **PostGIS** côté GISPulse aujourd'hui. Le support DuckDB est planifié v1.3+ — voir [INTEGRATION_MATRIX](https://github.com/imagodata/gispulse/blob/main/docs/INTEGRATION_MATRIX.md).
:::

## Scénario D — Évaluer un trigger depuis PyQGIS

Pour intégrer GISPulse dans un workflow QGIS (ex : sur un clic d'outil custom, vérifier qu'une géométrie respecte une règle), appeler `POST /api/triggers/{id}/evaluate` depuis la console PyQGIS.

```python
# Python console QGIS — copier-coller direct
import json
from urllib import request
from qgis.core import QgsProject

GISPULSE_URL = "http://localhost:8001"
TRIGGER_ID = "3fa85f64-5717-4562-b3fc-2c963f66afa6"

# Récupérer la feature sélectionnée
layer = QgsProject.instance().mapLayersByName("parcels")[0]
selection = layer.selectedFeatures()
if not selection:
    print("Aucune feature sélectionnée")
else:
    feat = selection[0]
    payload = {
        "table": layer.name(),
        "operation": "INSERT",
        "row_id": str(feat.id()),
        "new_attrs": {
            "geom_wkt": feat.geometry().asWkt(),
            **{k: feat[k] for k in feat.fields().names()},
        },
    }
    req = request.Request(
        f"{GISPULSE_URL}/api/triggers/{TRIGGER_ID}/evaluate",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=5) as resp:
        result = json.loads(resp.read())
        print("Trigger result:", result)
```

Le résultat liste les `FiredTrigger` avec `matched: true/false` — utile pour bloquer une saisie côté QGIS si une règle est violée.

## Limitations

- **Single writer GPKG** — un seul écrivain à la fois sur un GPKG GISPulse. Pour usage multi-utilisateur concurrent, utiliser PostGIS. Voir [TRIGGERS_GUIDE → Limites OSS](https://github.com/imagodata/gispulse/blob/main/docs/TRIGGERS_GUIDE.md#limites-oss-community-tier).
- **Plugin natif** — un plugin dataset-browser + job-runner est planifié v1.3+ ; en attendant, voir l'[add-in actuel](../plugins/qgis).
- **CRS** — GISPulse retourne par défaut en CRS du dataset source ; reprojeter via les paramètres OGC `crs=` ou directement dans QGIS.

## Voir aussi

- [Plugin natif QGIS](../plugins/qgis) — installation et features additionnelles
- [Integration matrix](https://github.com/imagodata/gispulse/blob/main/docs/INTEGRATION_MATRIX.md) — toutes les combinaisons client × mode × version
- [Triggers Guide](https://github.com/imagodata/gispulse/blob/main/docs/TRIGGERS_GUIDE.md) — sécurité, retries, limites OSS

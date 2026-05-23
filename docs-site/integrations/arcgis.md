---
title: Intégrer ArcGIS Pro / Online / GeoEvent
description: 5 scénarios pour consommer GISPulse depuis l'écosystème ArcGIS via standards (FileGDB, OGC API Features, MVT, webhooks bidirectionnels).
---

# Intégrer ArcGIS Pro / Online / GeoEvent

Cette page couvre **5 scénarios standards** pour brancher GISPulse à l'écosystème ArcGIS sans plugin natif. Le client REST ArcGIS dédié arrive en v1.3+ — voir [INTEGRATION_MATRIX](https://github.com/imagodata/gispulse/blob/main/docs/INTEGRATION_MATRIX.md).

## Pré-requis

- ArcGIS Pro 3.0+ (ou ArcGIS Online + ArcGIS Server)
- Un endpoint GISPulse (local ou demo)
- Pour les scénarios webhook : ArcGIS GeoEvent Server (pour ingest temps réel)

## Scénario A — FileGDB en lecture

GISPulse peut écrire un `.gdb` (File Geodatabase) directement depuis un pipeline. ArcGIS Pro lit le GDB sans intermédiaire.

```bash
# Exporter un dataset GISPulse vers FileGDB
gispulse export dataset parcels \
    --format filegdb \
    --output parcels_export.gdb

# Le fichier est zippé pour transfert
```

Dans **ArcGIS Pro → Catalog → Folders → Add Folder Connection**, pointer sur le dossier contenant `parcels_export.gdb`. Le GDB apparaît avec ses feature classes ; les sidecars `.qml` GISPulse ne sont pas convertis (limite v1.2).

::: tip Sidecars SLD/QML→ArcGIS
Le portage automatique des styles QGIS vers ArcGIS Pro est planifié v1.3+. En attendant, ré-appliquer les classes via **Symbology** dans Pro et sauvegarder le `.lyrx`.
:::

## Scénario B — OGC API Features dans ArcGIS Pro

Pour des données live, ArcGIS Pro 3.x supporte nativement OGC API Features.

1. **Insert → Connections → New OGC API Connection**
2. URL : `http://localhost:8001/ogc/features`
3. Si auth : ajouter l'en-tête `X-API-Key` dans les credentials de la connexion
4. Glisser une collection dans la **Map view**

Pro interroge `/ogc/features/collections/{id}/items?bbox=…` au displacement. Comportement identique à la connexion WFS classique mais sur protocole REST/JSON.

## Scénario C — Vector tiles dans ArcGIS Online

Les tuiles MVT GISPulse fonctionnent comme une **Vector Tile Service URL** dans ArcGIS Online.

1. Récupérer le doc TileJSON :
   ```bash
   curl http://localhost:8001/tiles/parcels/tilejson.json
   ```

2. Dans **ArcGIS Online → Content → Add Item → Add an item from a URL** :
   - Type : **Vector Tile Layer**
   - URL : copier la valeur de `tiles[0]` du TileJSON

3. Le layer est visible dans **Map Viewer** ; styling via le `style.json` MapLibre (voir [tuto MapLibre](maplibre.md)).

## Scénario D — Webhook GeoEvent → GISPulse

ArcGIS GeoEvent Server peut pousser un évènement vers GISPulse via le **HTTP/JSON Output Connector**. En attendant l'endpoint dédié `/webhooks/arcgis` (v1.3+), utiliser `POST /api/triggers/{id}/evaluate`.

**Configuration GeoEvent** (Manager UI) :

- **Output Connector** : `Send a JSON Object on a HTTP/Endpoint`
- **URL** : `https://gispulse.example.com/api/triggers/<TRIGGER_ID>/evaluate`
- **HTTP Method** : `POST`
- **Headers** :
  - `Content-Type: application/json`
  - `X-API-Key: <ta_clé>`
- **JSON Format** : map les champs GeoEvent vers le contrat GISPulse :
  ```json
  {
    "table": "vehicles",
    "operation": "UPDATE",
    "row_id": "${OBJECTID}",
    "new_attrs": {
      "geom_wkt": "${GEOMETRY_WKT}",
      "speed": ${speed},
      "vehicle_id": "${vehicle_id}"
    }
  }
  ```

GISPulse évalue les prédicats du trigger et retourne `[{matched: true|false, transition: "...", ...}]`. GeoEvent peut router la réponse selon le résultat.

## Scénario E — Trigger GISPulse → ArcGIS GeoEvent (webhook out)

Le sens inverse : un trigger GISPulse fire et POST le payload vers un endpoint GeoEvent (ou Zapier, n8n, Make).

**Côté GISPulse** — créer un trigger avec une action `WEBHOOK` :

```json
{
  "name": "alert_speed_breach",
  "trigger_type": "DML",
  "category": "MONITORING",
  "predicates": [
    { "type": "attr", "field": "speed", "op": ">", "value": 130 }
  ],
  "actions": [
    {
      "action_type": "WEBHOOK",
      "config": {
        "url": "https://geoevent.example.com:6143/geoevent/rest/receiver/gispulse-input"
      }
    }
  ],
  "enabled": true
}
```

**Format payload reçu par GeoEvent** (figé v1.2+, voir [INTEGRATION_MATRIX](https://github.com/imagodata/gispulse/blob/main/docs/INTEGRATION_MATRIX.md#webhook-payload)) :

```json
{
  "event_type": "trigger_fired",
  "trigger_id": "...",
  "trigger_name": "alert_speed_breach",
  "table": "vehicles",
  "operation": "UPDATE",
  "row_id": "...",
  "matched": true,
  "transition": null,
  "timestamp": "2026-04-26T14:32:11.123+00:00",
  "custom": {}
}
```

**Sécurité** :
- HMAC-SHA256 optionnel dans `X-GISPulse-Signature: sha256=<hex>` (set `GISPULSE_WEBHOOK_SIGNING_SECRET`)
- Retries auto sur 5xx (2 tentatives, 1 s + 3 s back-off) ; les 4xx ne sont pas retentés
- SSRF blocklist active : RFC1918 + cloud-metadata (169.254.169.254) bloqués par défaut

## Limitations

- **Pas de client REST natif ArcGIS Online/Server** — lecture/écriture programmatique des feature services côté GISPulse est planifiée v1.3+
- **Sidecars styles** — les `.qml` (QGIS) ne sont pas convertis vers `.lyrx` (Pro) automatiquement
- **GeoEvent inbound** — passer par `/api/triggers/{id}/evaluate` aujourd'hui, l'endpoint dédié `/webhooks/arcgis` arrive v1.3+

## Voir aussi

- [Add-in ArcGIS Pro natif](../plugins/arcgis) — UX intégrée + dataset browser
- [Integration matrix](https://github.com/imagodata/gispulse/blob/main/docs/INTEGRATION_MATRIX.md)
- [Triggers Guide](https://github.com/imagodata/gispulse/blob/main/docs/TRIGGERS_GUIDE.md) — webhook payload, retries, HMAC

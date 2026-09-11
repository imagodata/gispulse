# PICC Wallonia vector source

Provides raw road footprints (`picc-road-surfaces-wa`, layer 24) and axes
(`picc-road-axes-wa`, layer 21). Requires the counted pagination core shipped
alongside this plugin on `integration/v2.4`; older cores fail before HTTP access.

Official services:
- https://geoservices.wallonie.be/arcgis/rest/services/TOPOGRAPHIE/PICC_VDIFF/MapServer/24
- https://geoservices.wallonie.be/arcgis/rest/services/TOPOGRAPHIE/PICC_VDIFF/MapServer/21

## Local acquisition

Install the plugin with the matching core. From a source checkout, the equivalent
command is below; `uv run` must use this checkout's core. The default performs no
HTTP requests and creates no files. Output must be a fresh directory.

```sh
PYTHONPATH=src:plugins/gispulse-src-picc uv run python -m gispulse_src_picc.export \
  --bbox 4.863 50.463 4.865 50.465 --output /tmp/picc-namur
# Execute the same command with --write to fetch and publish both layers.
```

Bounds are WGS84 longitude/latitude. Per-layer limits are explicit:
`--page-size` (default 2000, service maximum), `--max-pages` (1000),
`--max-features` (1000000). Exceeding limits fails; narrow the bbox or deliberately
adjust the bounds. Acquisition is in memory and large areas should be partitioned.

The output contains two GeoParquet files reprojected to EPSG:31370 and
`report.json`: endpoints, bbox, source metadata, raw columns, source count,
page count, SHA256 and acquisition time. Staging is removed on failure; no output
directory is published until both layers pass. Existing output is never reused.
An empty source count produces an empty geometry layer, without inventing raw fields.

## Completeness contract

The REST adapter counts before and after, checks unique OBJECTID values, compares
the collected total, and refuses truncated, repeated or malformed pages. PICC uses
`cursor_policy=arcgis_page_window`: resultOffset advances by the requested page
size; exceededTransferLimit drives completion. Spatial index filtering can yield
short or empty pages, even before completion, as documented by Esri:
https://developers.arcgis.com/rest/services-reference/enterprise/query-map-service-layer/

This is **not a snapshot guarantee**: edits that preserve counts during a fetch
cannot all be detected. The report states this limitation. No upstream revision
identifier is invented.

## Consumer boundary

The plugin keeps raw SPW fields, including NATUR_DESC and NIVEAU. It assigns no
material class, client price or ground/bridge/tunnel meaning. NIVEAU must be
interpreted against a validated source contract before constructing crossing
proofs. Separate axis and polygon files do not themselves prove a road crossing.

Validation on 2026-09-08: small Namur bbox above fetched successfully for both
layers with page size 2, including a short surface page. This validates transport
and pagination, not regional coverage or client costing.

## NIVEAU semantics and axis/footprint join, verified 2026-09-11

Source: the PICC conceptual data model ("PICC : description du modèle de la
donnée", tables `VOIRIE_AXE` and `VOIRIE_SURFACE`).

`NIVEAU` is **not the same reference frame on the two layers that share the
name**. On `VOIRIE_AXE` (this plugin's layer 21) it is documented only as
"position verticale relative par rapport aux autres axes de voiries", domain
`{-2, -1, 0, 1, 2}` — relative to other axes, no absolute meaning. On
`VOIRIE_SURFACE` (layer 24) it is documented as absolute: "0 si surface sol,
+x si surfaces au dessus = pont, -x si surfaces en dessous = tunnel". Treating
the layer-21 value as if it carried the layer-24 absolute meaning (or vice
versa) would silently mislabel a crossing. A crossing proof from NIVEAU alone
requires comparing values within one layer's own reference frame, never across
layers, and layer 24's values are the only ones with an absolute ground/bridge/
tunnel meaning.

The conceptual model also documents an explicit axis-to-footprint join table,
`VOIRIE_SURF_AXE_REL` (`VSU_ID` → `VOIRIE_SURFACE.GEOREF_ID`, `VAX_ID` →
`VOIRIE_AXE.GEOREF_ID`). This plugin cannot currently reach it: the wired
ArcGIS REST endpoint (`PICC_VDIFF` MapServer) advertises `"tables": []` — no
relational table, only the geometry layers — and the equivalent FeatureServer
returns a server-side `featureserver not found` error. `GEOREF_ID` by itself is
a per-object identifier, not a foreign key between layers 21 and 24. Until this
relation table (or an equivalent channel) becomes reachable, any axis-to-
footprint association is necessarily geometric (nearest/covering match), which
is an approximation this plugin does not perform and which any consumer must
label as such, not as an official-contract match.

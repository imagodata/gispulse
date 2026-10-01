# PICC Wallonia vector source

Provides raw road footprints (`picc-road-surfaces-wa`, layer 24) and axes
(`picc-road-axes-wa`, layer 21). Requires gispulse >= 2.5.0 (counted pagination
core); older cores fail before HTTP access.

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

## Road crossings: level evidence

`export_picc` also publishes `road_crossings.geoparquet`,
`road_crossings_coverage.geoparquet` and `road_crossings_exclusions.geoparquet`,
built by `build_picc_road_crossings`
(`src/gispulse/capabilities/vector/picc_road_crossings.py`). The contract is
the GRB one: one row per road unit, in EPSG:31370, with the footprint, an
`axis` column, `road_id`, `structure`, `complex_crossing` and provenance. A
defect in the level evidence degrades that artifact only (`road_crossings:
{"status": "failed"}`), never the raw layers.

The rules rest on the absolute `NIVEAU` of `VOIRIE_SURFACE` documented above
(decision D5).

| surface | structure |
|---|---|
| `Ouvrage d'art`, `NIVEAU` ≥ 1, touching surfaces grouped | `grade_separated` (deck, stated level); takes priority over the ground under it |
| `Ouvrage d'art`, `NIVEAU` ≤ −1, touching surfaces grouped | `unknown`, with every axis inside; takes priority over the ground above it; excluded from coverage |
| `Ouvrage d'art`, `NIVEAU` 0 | `ground` (stated) |
| `Tronçon`, `Carrefour`, `Aire de repos`, `NIVEAU` empty | `ground`, by a closed-world reading of the continuous inventory (every structure surface is an `Ouvrage d'art`) |
| `NIVEAU` present but unreadable (`1,0`, `1.5`, text), an `Ouvrage d'art` with no `NIVEAU`, any other nature | excluded from coverage |

Measured on Liège: the PICC draws a `NIVEAU` 0 surface under every deck (75
overlaps with `+1` surfaces) and above every tunnel (60 with `−1`). The
`Tronçon` surfaces overlap almost nothing.

**Why tunnels are `unknown` although their level is stated.** Axes carry no
level (`NIVEAU` is empty on layer 21) and no official link to their surface.
Inside a tunnel footprint, the tunnel's axis cannot be told from a street
passing above it.
- Labelled `grade_separated`, the tunnel would hide that street: a crossing
  there would count zero without an error.
- Left to the ground surface above, the tunnel's axis would be bored as a
  ground crossing. A first version did this, and an adversarial review
  measured 674 m of tunnel axes carried by `ground` rows on Liège.

`unknown` makes the consumer ask for proof instead. A deck has no such
ambiguity, because it takes the ground under it out of the ground units.

Axes are sorted by `NATUR_DESC`:
- `Communale`, `Nationale`, `Autoroute` and `Ring` are carriageways;
- `Piste cyclable`, `Chemin ou sentier` and `Sentier` are not (`Chemin ou
  sentier` follows the decision taken for the GRB aardeweg);
- anything else is unresolved.

`complex_crossing` is true for `Autoroute`. Fragments are merged as for the
GRB: touching `Tronçon` surfaces sharing an axis and with no junction or dead
end inside are merged; a `Carrefour` is never merged. An empty layer may come
back without its property columns.

Validated live on a 6 × 6 km tile at Liège (`export_picc --write`, 4 s):

| item | value |
|---|---|
| ground units (from 3 798 ground surfaces) | 3 755 |
| bridges, `grade_separated` | 77 |
| tunnels, `unknown` | 16 |
| ground footprints overlapping | none |
| `ground` axes inside a tunnel | none |
| coverage | 98.7 % |
| surfaces carrying no axis | 174 |
| node-less axis crossings outside structures | 33 |

The artifact loads in MILOU's `load_road_crossings`. Of the tile's 31 tracés,
one now stops on `ROAD_CROSSING_LEVEL_UNKNOWN` at a tunnel, which is the
intended behaviour.

The source requests XY-only geometry (`returnZ=false`). Projected
server-side to WGS84 *with* Z, two surfaces of that tile came back
self-intersecting, although the same features are valid in XY or in
EPSG:31370. The export used to fail on them (`PICC_LAYER_INVALID`).

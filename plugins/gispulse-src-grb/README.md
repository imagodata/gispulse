# GRB road source and candidate surface preparation

The plugin exposes WBN (whole road corridor), WGO (internal functional boundary
lines), Wegsegment (axes), Wegknoop (nodes), KNW (structures), and WGA (ancillary
structures). WBN is not a carriageway-only footprint; WGA is not a sidewalk layer.

Official descriptions:
- https://www.vlaanderen.be/digitaal-vlaanderen/onze-diensten-en-platformen/basiskaart-vlaanderen-grb/objectenhandboek-basiskaart-vlaanderen-grb/wegbaan-wbn
- https://www.vlaanderen.be/digitaal-vlaanderen/onze-diensten-en-platformen/basiskaart-vlaanderen-grb/objectenhandboek-basiskaart-vlaanderen-grb/wegopdeling-wgo
- https://www.vlaanderen.be/digitaal-vlaanderen/onze-diensten-en-platformen/basiskaart-vlaanderen-grb/grb-webdiensten

## Acquisition contract

Install the plugin together with the matching counted-WFS core. Older cores
without XML-hits support fail before HTTP. All requests require a finite ordered
bbox in **EPSG:31370**, the native and output CRS.

The request uses explicit `INTERSECTS(SHAPE, POLYGON(...))`, not WFS BBOX. A live
probe found BBOX responses dependent on requested page size; native CQL INTERSECTS
returned consistent IDs. CQL literals are in the native CRS, so the adapter
refuses a different output/filter CRS for this mode. The same predicate is used
for data and XML `resultType=hits` counts. Unknown counts, changed counts, repeated
IDs, incomplete totals or absent/mismatched projected CRS all fail.

The adapter checks counts before and after, with stable OIDN ordering. This does
not guarantee a transactional snapshot against edits that preserve counts.
Limits are explicit and apply per layer. Raw attributes and geometry are retained;
new entries expose the specific VERH/MORF/STATUS/METHODE and source identifiers.
The two original entries retain their existing canonical schema metadata.

## Prepare a local bundle

The command defaults to dry-run: no HTTP and no output files. It fetches five
layers (WBN/WGO/Wegsegment/Wegknoop/KNW); WGA remains available through the plugin
but is not part of the road partition bundle.

```sh
PYTHONPATH=src:plugins/gispulse-src-grb uv run python -m gispulse_src_grb.prepare \
  --bbox 104509.05124249801 193510.7503413614 104862.53457979232 193847.5291547682 \
  --output /tmp/grb-gent
# Add --write to acquire and publish a fresh bundle.
```

Controls: `--page-size` (2000), `--max-pages` (1000), `--max-features` (1000000),
`--area-tolerance-m2` and `--length-tolerance-m` (both 1e-6). The last two are
numerical diagnostic tolerances, not survey accuracy or snapping distances.
Processing is in memory; choose bounded study areas and a halo around the target.

All raw GeoParquet layers, `candidate_faces.geoparquet`,
`partition_diagnostics.geoparquet` and `report.json` are staged together. The
report includes counts, topology statuses, SHA256 checksums, bbox and acquisition
time. Failure cleans staging; an existing output directory is never reused.
Empty collections remain explicit and do not imply road coverage.

## Partition and downstream boundary

`partition_polygons` nodes WGO lines together with each WBN boundary, then
polygonizes. It never snaps, buffers, repairs invalid source geometry or assigns
a material, function, ground level or client price.

Only WBN polygons fully contained in the acquisition bbox are partitioned.
Others are retained in diagnostics as `outside_acquisition_coverage`, because
missing WGO beyond the bbox could produce a false closed surface.

Statuses distinguish `partitioned`, `unsplit`, `unresolved_lines`, and
`area_mismatch`. Cuts, dangling lines and invalid rings are measured and retained
in the diagnostic secondary geometry column. Area conservation alone is not a
subdivision or classification proof. Disconnected original polygon components
are not counted as an internal subdivision.

Candidate face IDs combine the source polygon ID with normalized geometry SHA256.
Input order does not change IDs. The bundle always states `ready_for_costing=false`:
functional classification, axis/structure reconciliation and client costing are
subsequent steps. A clean partition is not yet a road-crossing proof.

The core helper `validate_functional_faces` enforces the next boundary: it accepts
only explicit upstream `functional_class` values (`carriageway_paved`, `sidewalk`,
or `unmapped`). It never infers a side from WGO geometry. Unknown or missing
classes fail or remain unmapped, so a raw candidate bundle cannot silently enter
GC costing.


## Validation

2026-09-08, the Gand bbox above, page size 5: 52 WBN (11 pages), 139 WGO
(28 pages), 61 axes (13 pages), 33 nodes (7 pages), 50 structures (10 pages).
All five counts agreed before/after and all IDs were unique. Reconstruction:
121 candidate faces; 29 partitioned corridors, 3 unresolved, 3 unsplit,
17 outside acquisition coverage. This is a local transport/topology validation,
not a regional classification or crossing certificate.

Schema distinction verified against DescribeFeatureType: GRB `UIDN` is numeric;
Wegenregister references `WS_UIDN` and `WK_UIDN` are strings (for example `4_3`).
These are different source identifiers, not inconsistent normalizations.

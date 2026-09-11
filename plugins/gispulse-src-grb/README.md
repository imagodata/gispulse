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
`--boundary-tolerance-m`, `--axis-coverage-ratio-min`, `--axis-min-extent-m`,
`--wcz-ratio-min` and `--wrb-ratio-max` drive the classification below.
Processing is in memory; choose bounded study areas and a halo around the target.

All raw GeoParquet layers, `candidate_faces.geoparquet`,
`classified_faces.geoparquet`, `partition_diagnostics.geoparquet` and
`report.json` are staged together. The
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

## Functional classification

`classify_grb_faces` labels each candidate face from source codes alone and
publishes `classified_faces.geoparquet`. It is a separate file:
`candidate_faces.geoparquet` stays deliberately unclassified, and a
classification failure (for example a broken Wegsegment record) degrades to a
`classification: {"status": "failed", "error": ...}` report section rather
than losing the raw layers and `candidate_faces.geoparquet` already acquired.

Classification runs in two passes over each corridor (the union of its
`partitioned` and `unsplit` faces — both keep area conservation with no
topology anomaly; `unsplit` simply has nothing to subdivide, which is not
"unresolved"). A face whose own `topology_status` is neither gives `unmapped`,
reason `unresolved_topology:<status>`, without consulting axes or boundaries.

**Pass 1 — axis evidence.** For a resolved face, every Wegsegment axis with
`STATUS=4` (in service) whose length inside the corridor is at least
`--axis-min-extent-m` *and* whose share of that length falls inside this face
is at least `--axis-coverage-ratio-min` becomes a candidate.

- All candidates paved (`VERH` 1 or 12): `carriageway_paved`, reason
  `in_service_paved_axis`, evidence citing every retained `WS_OIDN:VERH`.
- All candidates not paved: `unmapped`, reason `axis_surface_not_paved`.
- A mix of both: `unmapped`, reason `contradictory_axis_surface` — an
  in-service axis disagreeing with itself on `VERH` is a data conflict, not
  something this step resolves by picking a winner.
- No candidate at all: undecided, carried into pass 2.

**Pass 2 — boundary evidence, anchored to a proven carriageway.** WGO `TYPE`
qualifies the *boundary line itself*, not the area it delimits (see the
official semantics above): a Wcz line borders both the slow-user zone and the
carriageway beside it, so a face's own Wcz perimeter share alone never proves
which side it is on — it is only evidence once anchored to a face pass 1
already proved `carriageway_paved` in the *same* corridor. A face that does
not share a boundary with such a face (longer than `--boundary-tolerance-m`)
stays `unmapped`, reason `no_carriageway_anchor_in_corridor` — including every
face in a corridor with no proven carriageway at all. For an anchored face:

- Wcz share below `--wcz-ratio-min`: `unmapped`, reason
  `woz_only_not_sidewalk` if the Woz share reaches that same threshold,
  otherwise `no_dominant_wcz_boundary`. Woz is the unpaved shoulder and is
  reported but never mapped to `sidewalk`.
- Wrb share above `--wrb-ratio-max`, or at least the Wcz share: `unmapped`,
  reason `wrb_boundary_dominant`.
- Otherwise: `sidewalk`, reason `dominant_wcz_boundary`, evidence citing the
  touching WGO `OIDN`s.

Defaults: `--axis-coverage-ratio-min 0.8`, `--axis-min-extent-m 5.0`,
`--wcz-ratio-min 0.4`, `--wrb-ratio-max 0.5`, `--boundary-tolerance-m 0.001`.
The axis ratio's denominator is the axis length inside the *corridor*, not its
total length, because a Wegsegment normally runs across several corridors —
but that ratio alone does not stop a short in-service paved stub (a driveway
or side-street graze at a junction) from scoring 1.0 purely because it never
leaves the one face it grazes. `--axis-min-extent-m` is the absolute floor
that catches that case: 5 m is twice the official minimum Wrb (paved
carriageway) width of 2.5 m, enough to distinguish an axis that actually runs
through the corridor from one that merely touches it. Numerator and
denominator both exclude, identically, any run collinear with *any* boundary
in the corridor (its outer edge and every internal WGO cut) — not just the
target face's own boundary — so an axis that hugs an internal boundary before
turning to genuinely cross into a face is not penalised for the hugging
stretch, while an axis lying entirely on a shared boundary never becomes a
candidate for any face (extent 0), regardless of how low
`--axis-coverage-ratio-min` is set. A rectangular sidewalk has only one of its
two long sides on the Wcz, so its perimeter share tops out near 0.5; requiring
0.4 means most of that side must be an actual Wcz. One millimetre is below
GRB survey accuracy and above GEOS noding residuals: it compares distances and
never moves a coordinate, and the same tolerance also gates the minimum shared
length pass 2 requires between a face and its carriageway anchor.

Each face carries `functional_class`, `classification_reason`,
`classification_evidence` and the four measured ratios; `report.json` gains a
`classification` section with counts, areas and ratios per class and per
reason, plus the thresholds used. `validate_functional_faces` runs on the
result as an internal self-check before the file is written, but its own
`ready_for_costing` column is deliberately **not** what gets published: this
chantier's mandate keeps that flag false until axis/structure reconciliation
is complete, and a per-face `True` in the published file would invite a
downstream reader to skip straight to costing on this step alone.

Limits. Wcz bounds the *slow user* zone, which covers pedestrians and cyclists
alike: a `sidewalk` face may be a cycle track. Capture follows the Wcz > Wrb >
Woz priority, so a missing type is not evidence that the zone is absent; a
corridor with no proven carriageway anywhere stays entirely `unmapped` rather
than guessing a side from geometry alone. The step does not reconcile axes
with KNW structures and does not certify a road crossing, so
`ready_for_costing` stays false at the bundle level regardless of any per-face
evidence.

## Validation

2026-09-08, the Gand bbox above, page size 5: 52 WBN (11 pages), 139 WGO
(28 pages), 61 axes (13 pages), 33 nodes (7 pages), 50 structures (10 pages).
All five counts agreed before/after and all IDs were unique. Reconstruction:
121 candidate faces; 29 partitioned corridors, 3 unresolved, 3 unsplit,
17 outside acquisition coverage. This is a local transport/topology validation,
not a regional classification or crossing certificate.

Classification re-run live on 2026-09-11 against the same bbox and the same
five counts: 121 candidate faces classified into 36 `carriageway_paved`
(38.0% of reconstructed area), 53 `sidewalk` (37.1%) and 32 `unmapped`
(24.8%) — reasons `unresolved_topology:unresolved_lines` (17, the 3
`unresolved_lines` corridors), `no_dominant_wcz_boundary` (11) and
`woz_only_not_sidewalk` (4). Every ratio column stayed within `[0, 1]`; every
`NaN` axis-coverage row was exactly the 17 unresolved-topology faces; every
`sidewalk` face carried a non-empty WGO-`OIDN` evidence string; no
`classified_faces.geoparquet` row carried a `ready_for_costing` column. Zero
faces landed on `contradictory_axis_surface`, `wrb_boundary_dominant` or
`no_carriageway_anchor_in_corridor` on this bbox — those paths exist for
regional data this local sample did not happen to contain, and stay covered
only by the unit tests' synthetic geometries. This is still a local
transport/topology/classification validation, not a regional classification
or crossing certificate.

Schema distinction verified against DescribeFeatureType: GRB `UIDN` is numeric;
Wegenregister references `WS_UIDN` and `WK_UIDN` are strings (for example `4_3`).
These are different source identifiers, not inconsistent normalizations.

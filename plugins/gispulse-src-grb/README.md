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
`--boundary-tolerance-m`, `--axis-coverage-ratio-min` and `--axis-min-extent-m`
drive the classification below. Processing is in memory; choose bounded study
areas and a halo around the target.

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

For each resolved face (the union of a corridor's `partitioned` and `unsplit`
faces — both keep area conservation with no topology anomaly; `unsplit`
simply has nothing to subdivide, which is not "unresolved"), every Wegsegment
axis with `STATUS=4` (in service) whose length inside the corridor is at
least `--axis-min-extent-m` *and* whose share of that length falls inside
this face is at least `--axis-coverage-ratio-min` becomes a candidate. Among
candidates, only those whose `MORF` is a documented motor-traffic code (101
through 112: motorway, dual/single carriageway, roundabout, special traffic
situation, traffic square, on/off-ramps, parallel/service road, parking/
service entrance) count as **motorized** — see "MORF: an in-service, paved
axis is not necessarily a carriageway" below.

- Motorized candidates split between paved and unpaved: `unmapped`, reason
  `contradictory_axis_surface` — an in-service axis disagreeing with itself
  on `VERH` is a data conflict, not something this step resolves by picking
  a winner.
- Motorized candidates all paved (`VERH` 1 or 12): `carriageway_paved`,
  reason `in_service_paved_axis`, evidence citing every retained
  `WS_OIDN:VERH` — unless the face's `topology_status` is `unsplit` and its
  area divided by the longest paved axis's covered length exceeds
  `--unsplit-max-width-m`, in which case the axis alone cannot vouch for the
  full width of a corridor with no internal WGO line, and the face stays
  `unmapped`, reason `unsplit_corridor_too_wide_for_single_carriageway`.
- Motorized candidates all unpaved (`VERH` 2): `unmapped`, reason
  `axis_surface_not_paved`.
- Motorized candidates all `VERH` unknown/not-applicable (-8/-9): `unmapped`,
  reason `axis_surface_unknown` — an unknown code is insufficient evidence,
  never a contradiction of a genuinely paved or unpaved axis on the same face.
- Candidates present but none motorized: `unmapped`, reason
  `axis_not_motorized_carriageway`.
- No usable candidate at all: `unmapped`, reason `no_in_service_axis_evidence`.
  A face whose `topology_status` is not `partitioned`/`unsplit` gives
  `unmapped` first, reason `unresolved_topology:<status>`, without consulting
  axes at all.

Defaults: `--axis-coverage-ratio-min 0.8`, `--axis-min-extent-m 5.0`,
`--unsplit-max-width-m 12.0`, `--boundary-tolerance-m 0.001`. The axis ratio's
denominator is the axis length inside the *corridor*, not its total length,
because a Wegsegment normally runs across several corridors — but that ratio
alone does not stop a short in-service paved stub (a driveway or side-street
graze at a junction) from scoring 1.0 purely because it never leaves the one
face it grazes. `--axis-min-extent-m` is the absolute floor that catches that
case: 5 m is twice the official minimum Wrb (paved carriageway) width of
2.5 m, enough to distinguish an axis that actually runs through the corridor
from one that
merely touches it. The numerator (an axis's length inside one face) excludes
runs collinear with that face's own boundary — internal or outer — so an axis
lying on a shared boundary proves membership of neither adjacent face; the
denominator (the same axis's length inside the whole corridor) excludes only
the corridor's *outer* boundary. This asymmetry is deliberate: a fully
symmetric exclusion was tried and rejected after adversarial review (see
below) because it shrinks the denominator by the same collinear stretch the
numerator drops, letting an axis that mostly hugs an internal cut before a
short genuine crossing reach ratio 1.0 for the face it diverges into. The
asymmetric version instead understates that axis's evidence — a false
negative, never a promotion.

Each face carries `functional_class`, `classification_reason`,
`classification_evidence` and four measured ratios (`axis_coverage_ratio` plus
`wcz_boundary_ratio`/`wrb_boundary_ratio`/`woz_boundary_ratio` — the share of
the face's own perimeter carried by each WGO type); `report.json` gains a
`classification` section with counts, areas and ratios per class and per
reason, plus the thresholds used. `validate_functional_faces` runs on the
result as an internal self-check before the file is written, but its own
`ready_for_costing` column is deliberately **not** what gets published: this
chantier's mandate keeps that flag false until axis/structure reconciliation
is complete, and a per-face `True` in the published file would invite a
downstream reader to skip straight to costing on this step alone.

### MORF: an in-service, paved axis is not necessarily a carriageway

`VERH`/`STATUS` alone are not sufficient evidence: Wegsegment's `MORF`
(morfologische wegklasse) classifies what the axis physically *is*,
independently of surface and service state, and several of its documented
codes are officially not a carriageway even when `STATUS=4` and `VERH=1` —
`voetgangerszone` (113, pedestrian zone), `wandel- en/of fietsweg niet
toegankelijk voor andere voertuigen` (114, walking/cycling path, explicitly
closed to other vehicles), `tramweg, niet toegankelijk voor andere
voertuigen` (116, tram-only), `dienstweg` (120, an unpaved-by-default service
track), `aardeweg` (125, an earthen track, unpaved by definition) and `veer`
(130, a ferry crossing — not a road at all).

A first cut of this module read only `VERH`/`STATUS` and, on real data from
the documented Gand bbox, labelled 14 `voetgangerszone`/`wandel- of
fietsweg` faces `carriageway_paved` (adversarial review found up to 15,
depending on face boundaries) — the exact failure mode this whole chantier
exists to remove, just moved from PICC/GRB material inference to a Wegsegment
attribute nobody had read yet, even though it was already part of the
acquired bundle. `MORF` is therefore required evidence: only axes whose
`MORF` is 101–112 (motorway, dual/single carriageway, roundabout, special
traffic situation, traffic square, on/off-ramps, parallel/service road,
parking/service entrance — the documented motor-traffic and junction codes)
count toward `carriageway_paved`; any other or unknown `MORF` gives
`unmapped`, reason `axis_not_motorized_carriageway`, unless a motorized
candidate is also present on the same face. Regression tests cover every
excluded code (`test_other_non_motorized_morf_codes_never_become_carriageway`)
and every included one
(`test_every_documented_motorized_morf_code_can_become_carriageway`).

A related gap the same review found: an `unsplit` face (no internal WGO line
at all) has `corridor == face`, so the axis-coverage ratio is 1.0 by
construction the moment any qualifying axis reaches it — `--axis-coverage-
ratio-min` has no effect there, only `--axis-min-extent-m` does. A single
motorized paved axis running through a wide, WGO-less corridor does not by
itself prove the *entire* width is carriageway. `--unsplit-max-width-m`
guards this: for an `unsplit` face only, the corridor's area divided by the
longest paved axis's covered length must not exceed it (default 12 m), or
the face stays `unmapped`, reason
`unsplit_corridor_too_wide_for_single_carriageway`. This guard does not apply
to `partitioned` faces, where the subdivision itself is evidence.

### Why there is no `sidewalk` class yet

Two designs for deriving `sidewalk` from WGO boundary composition were built
and rejected by adversarial review, both for the same underlying reason: WGO
`TYPE` qualifies the *boundary line itself* (see the official semantics
above), not the area it delimits, and a Wcz line borders **two** faces — the
slow-user zone on one side, but also, just as validly, the carriageway
beside it, or even one portion of the *same* carriageway split from another
by a Wcz line at a raised pedestrian crossing. Nothing in WGO or Wegsegment
tells these apart:

1. A face's own Wcz perimeter share, used directly, is invertible: a
   carriageway flanked by two Wcz lines can carry a *higher* Wcz share than a
   genuine sidewalk beside it.
2. Requiring the Wcz-dominant face to additionally be adjacent to a face pass
   1 already proved `carriageway_paved` does not fix this: adjacency is
   symmetric, so a carriageway split in two by a transversal Wcz line — one
   side covered by a Wegsegment axis, the other not, which is a realistic gap
   given axes commonly end at junctions — is adjacent to itself, and the
   uncovered half gets misread as `sidewalk`.

Both mechanisms are exercised as regression tests in
`tests/unit/test_classify_grb_faces.py` and kept unmapped:
`test_a_wcz_line_can_split_the_same_carriageway_without_being_misread_as_sidewalk`
and
`test_the_true_carriageway_flanked_by_two_wcz_lines_is_never_promoted_to_sidewalk`.
This module therefore only ever produces `carriageway_paved` or `unmapped`;
`sidewalk` is not currently reachable, even though
`validate_functional_faces` still accepts it as a legal value for whichever
future upstream evidence source makes it derivable without this ambiguity
(the Wetteren POC's `fnc`/`mtc` split, PICC/UrbIS reconciliation, or a wider
GRB attribute not yet reviewed here). `wcz_boundary_ratio`,
`wrb_boundary_ratio` and `woz_boundary_ratio` are still measured and reported
on every resolved face — diagnostic only, never decisive — so a future
attempt, or a human reviewer, has the raw signal without this module
asserting a conclusion from it.

Limits. Capture follows the Wcz > Wrb > Woz priority, so a missing WGO type is
not evidence that the corresponding zone is absent. The step does not
reconcile axes with KNW structures and does not certify a road crossing, so
`ready_for_costing` stays false at the bundle level regardless of any
per-face evidence.

## KNW structure reconciliation — contract verified, not yet implemented

Official semantics (objectenhandboek, `kunstwerk-knw`): KNW geometry is a
polygon. `TYPE` has 15 documented codes; only two describe a road structure —
1 = overbrugging (bridge), 12 = tunnelmond (tunnel entrance). The other 13
(hydraulic structures, monuments, pylons, chimneys, silos, wind turbines,
breakwaters, palisades, …) cover unrelated infrastructure the layer also
carries. `VORM`: 1 = enkelvoudig (single structure), 2 = samengesteld
(several grouped installations).

The official worked example is explicit about the WBN relationship: "the road
(WBN) is interrupted at a bridge; the bridge is measured at ground level; the
waterway underneath passes without interruption." A KNW bridge or tunnel
entrance is therefore evidence of a **corridor discontinuity**, not a
same-level road crossing — a candidate face near one should not be assumed
drillable at grade without checking it. This is not yet implemented: no
capability here reads KNW, and `classify_grb_faces` does not consult it.
Building the reconciliation would need at minimum: identifying WBN corridors
whose boundary sits near a KNW polygon typed 1 or 12, and flagging faces
inside such a corridor as level-ambiguous rather than assuming ground level.

## Validation

2026-09-08, the Gand bbox above, page size 5: 52 WBN (11 pages), 139 WGO
(28 pages), 61 axes (13 pages), 33 nodes (7 pages), 50 structures (10 pages).
All five counts agreed before/after and all IDs were unique. Reconstruction:
121 candidate faces; 29 partitioned corridors, 3 unresolved, 3 unsplit,
17 outside acquisition coverage. This is a local transport/topology validation,
not a regional classification or crossing certificate.

Classification re-run live on 2026-09-11 against the same bbox and the same
five counts, after **three** rounds of adversarial review: round 1 found the
axis-graze false positive and the unsplit-corridor exclusion; round 2 found
that neither `sidewalk` design held up (see "Why there is no `sidewalk` class
yet" above) and that a symmetric numerator/denominator fix introduced a worse
regression than the asymmetry it "fixed"; round 3, re-reviewing the module
with `sidewalk` already removed, found that reading only `VERH`/`STATUS`
(ignoring the already-acquired `MORF`) labelled real Gand
`voetgangerszone`/`wandel- of fietsweg` faces `carriageway_paved` — see "MORF"
above. Final result: 121 candidate faces classified into 22
`carriageway_paved` (24.9% of reconstructed area) and 99 `unmapped` (75.1%) —
reasons `no_in_service_axis_evidence` (68), `axis_not_motorized_carriageway`
(14), `unresolved_topology:unresolved_lines` (17). Every ratio column stayed
within `[0, 1]`; every `NaN` axis-coverage row was exactly the 17
unresolved-topology faces; no `classified_faces.geoparquet` row carried a
`ready_for_costing` column. Zero faces landed on `contradictory_axis_surface`,
`axis_surface_unknown` or `unsplit_corridor_too_wide_for_single_carriageway`
on this bbox — those paths exist for regional data this local sample did not
happen to contain, and stay covered only by the unit tests' synthetic
geometries.

Coverage on this bbox: 36/121 faces with the pre-MORF-filter cut, 89/121 with
the pre-round-2 cut that included a broken `sidewalk` mechanism, 22/121 now.
Each drop was made deliberately, fail-closed, in response to a specific
adversarially-proven false positive — not a regression to be reverted. This
is still a local transport/topology/classification validation, not a
regional classification or crossing certificate; a regional run should expect
a comparably conservative `carriageway_paved` share until axis/structure
reconciliation (KNW) and a real evidence source for `sidewalk` exist.

Schema distinction verified against DescribeFeatureType: GRB `UIDN` is numeric;
Wegenregister references `WS_UIDN` and `WK_UIDN` are strings (for example `4_3`).
These are different source identifiers, not inconsistent normalizations.

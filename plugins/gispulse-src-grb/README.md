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
this face is at least `--axis-coverage-ratio-min` becomes a candidate.

- All candidates paved (`VERH` 1 or 12): `carriageway_paved`, reason
  `in_service_paved_axis`, evidence citing every retained `WS_OIDN:VERH`.
- All candidates unpaved (`VERH` 2): `unmapped`, reason
  `axis_surface_not_paved`.
- A mix of paved and unpaved: `unmapped`, reason `contradictory_axis_surface`
  — an in-service axis disagreeing with itself on `VERH` is a data conflict,
  not something this step resolves by picking a winner.
- Candidates present but all `VERH` unknown/not-applicable (-8/-9): `unmapped`,
  reason `axis_surface_unknown` — an unknown code is insufficient evidence,
  never a contradiction of a genuinely paved or unpaved axis on the same face.
- No usable candidate at all: `unmapped`, reason `no_in_service_axis_evidence`.
  A face whose `topology_status` is not `partitioned`/`unsplit` gives
  `unmapped` first, reason `unresolved_topology:<status>`, without consulting
  axes at all.

Defaults: `--axis-coverage-ratio-min 0.8`, `--axis-min-extent-m 5.0`,
`--boundary-tolerance-m 0.001`. The axis ratio's denominator is the axis
length inside the *corridor*, not its total length, because a Wegsegment
normally runs across several corridors — but that ratio alone does not stop a
short in-service paved stub (a driveway or side-street graze at a junction)
from scoring 1.0 purely because it never leaves the one face it grazes.
`--axis-min-extent-m` is the absolute floor that catches that case: 5 m is
twice the official minimum Wrb (paved carriageway) width of 2.5 m, enough to
distinguish an axis that actually runs through the corridor from one that
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
five counts, after two rounds of adversarial review (the first round found the
axis-graze false positive and the unsplit-corridor exclusion; the second round
found that neither `sidewalk` design held up — see "Why there is no `sidewalk`
class yet" above — and that a symmetric numerator/denominator fix introduced a
worse regression than the asymmetry it "fixed"): 121 candidate faces
classified into 36 `carriageway_paved` (38.0% of reconstructed area) and 85
`unmapped` (62.0%) — reasons `no_in_service_axis_evidence` (68),
`unresolved_topology:unresolved_lines` (17). 62 of those 68 unmapped faces
still measure a nonzero `wcz_boundary_ratio` (reported, never decisive) —
plausible sidewalks this module deliberately declines to assert. Every ratio
column stayed within `[0, 1]`; every `NaN` axis-coverage row was exactly the
17 unresolved-topology faces; no `classified_faces.geoparquet` row carried a
`ready_for_costing` column. Zero faces landed on `contradictory_axis_surface`
or `axis_surface_unknown` on this bbox — those paths exist for regional data
this local sample did not happen to contain, and stay covered only by the
unit tests' synthetic geometries. Coverage dropped sharply from an earlier,
incorrect cut (which had reached 89/121 faces including a `sidewalk` class)
to this one's 36/121 — the trade made deliberately, fail-closed, after that
earlier cut's `sidewalk` mechanism was shown twice to be invertible on
realistic synthetic geometry. This is still a local
transport/topology/classification validation, not a regional classification
or crossing certificate.

Schema distinction verified against DescribeFeatureType: GRB `UIDN` is numeric;
Wegenregister references `WS_UIDN` and `WK_UIDN` are strings (for example `4_3`).
These are different source identifiers, not inconsistent normalizations.

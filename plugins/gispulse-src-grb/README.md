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
`--unsplit-max-width-m` and `--dienstweg-unpaved-evidence` drive the
classification below. Processing is in memory; choose bounded study
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
only explicit upstream `functional_class` values (`carriageway_paved`,
`carriageway_unpaved`, `sidewalk`, or `unmapped`). It never infers a side from WGO geometry. Unknown or missing
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
least `--axis-min-extent-m` and that has some length inside this face is a
**candidate**; a candidate whose share of that length falling inside this
face is at least `--axis-coverage-ratio-min` is a **match**. Only matches
decide a class; any candidate can veto one. Among matches, only those whose
`MORF` is a documented motor-traffic code (101 through 112: motorway,
dual/single carriageway, roundabout, special traffic situation, traffic
square, on/off-ramps, parallel/service road, parking/service entrance) count
as **motorized** — see "MORF: an in-service, paved axis is not necessarily a
carriageway" below.

A match is **paved evidence** when motorized with `VERH` 1 or 12, and
**unpaved evidence** when motorized with `VERH=2`, when its `MORF` is 125
(`aardeweg`) with `VERH` 2, -8, -9 or missing, or — only with
`--dienstweg-unpaved-evidence` — when its `MORF` is 120 (`dienstweg`) with
`VERH=2`. See "`carriageway_unpaved`" below. In priority order:

- A positive verdict contradicted by a candidate, or a matched `MORF=125`
  axis coded `VERH` 1 or 12 (which contradicts itself): `unmapped`, reason
  `axis_surface_conflict` — the sources disagree, and this step does not
  resolve that by picking a winner. Paved evidence is vetoed by unpaved
  evidence that is matched or runs, inside the face, at least
  `--axis-min-extent-m` and half the longest paved match; unpaved evidence
  is vetoed by any candidate coded `VERH` 1 or 12, whatever its `MORF` (see
  the asymmetry below).
- Unpaved evidence with a motor-traffic candidate of unknown surface on the
  same face: `unmapped`, reason `axis_surface_unknown`; with any other
  candidate that is not itself unpaved evidence (a path, a service road, an
  unknown `MORF`): `unmapped`, reason `axis_not_motorized_carriageway`.
- Paved evidence: `carriageway_paved`,
  reason `in_service_paved_axis`, evidence citing every retained
  `WS_OIDN:VERH` — unless the face's `topology_status` is `unsplit` and its
  area divided by the longest paved axis's covered length exceeds
  `--unsplit-max-width-m`, in which case the axis alone cannot vouch for the
  full width of a corridor with no internal WGO line, and the face stays
  `unmapped`, reason `unsplit_corridor_too_wide_for_single_carriageway`.
- Unpaved evidence: `carriageway_unpaved`, reason
  `in_service_unpaved_axis`, evidence citing every retained
  `WS_OIDN:VERH:MORF`, under the same `unsplit` width guard (measured on the
  longest unpaved axis).
- Motorized candidates all `VERH` unknown/not-applicable (-8/-9): `unmapped`,
  reason `axis_surface_unknown` — an unknown code is insufficient evidence,
  never a contradiction of a genuinely paved or unpaved axis on the same face.
- Matches present but none motorized nor unpaved evidence (pedestrian,
  cycling, tram, ferry or unknown `MORF`; a `dienstweg` not opted in or not
  coded `VERH=2`; an `aardeweg` with an undocumented `VERH`): `unmapped`,
  reason `axis_not_motorized_carriageway`. Its evidence cites `WS_OIDN:VERH`
  only, as before; `:MORF` is added whenever unpaved evidence or a conflict
  is involved.
- No usable candidate at all: `unmapped`, reason `no_in_service_axis_evidence`.
  A face whose `topology_status` is not `partitioned`/`unsplit` gives
  `unmapped` first, reason `unresolved_topology:<status>`, without consulting
  axes at all.

Defaults: `--axis-coverage-ratio-min 0.8`, `--axis-min-extent-m 5.0`,
`--unsplit-max-width-m 12.0`, `--boundary-tolerance-m 0.001`;
`--dienstweg-unpaved-evidence` is off. The axis ratio's
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
reason, plus the thresholds and options used. `validate_functional_faces` runs on the
result as an internal self-check before the file is written, but its own
`ready_for_costing` column is deliberately **not** what gets published: this
chantier's mandate keeps that flag false regardless, and a per-face `True` in
the published file would invite a downstream reader to skip straight to
costing on this step alone. Two more columns, `structure_proximity` and
`structure_evidence`, are added afterwards by KNW reconciliation — see below.

### MORF: an in-service, paved axis is not necessarily a carriageway

`VERH`/`STATUS` alone are not sufficient evidence: Wegsegment's `MORF`
(morfologische wegklasse) classifies what the axis physically *is*,
independently of surface and service state, and several of its documented
codes are officially not a carriageway even when `STATUS=4` and `VERH=1` —
`voetgangerszone` (113, pedestrian zone), `wandel- en/of fietsweg niet
toegankelijk voor andere voertuigen` (114, walking/cycling path, explicitly
closed to other vehicles), `tramweg, niet toegankelijk voor andere
voertuigen` (116, tram-only) and `veer` (130, a ferry crossing — not a road
at all). `dienstweg` (120, a service road) and `aardeweg` (125, an earthen
track) are excluded from the paved class too, by this module's conservative
reading: the handbook gives them as bare labels, without a surface rule.

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
match is also present on the same face, or the axis is unpaved evidence
(125, or an opted-in 120 coded `VERH=2`: see `carriageway_unpaved`).
Regression tests cover every excluded code
(`test_other_non_motorized_morf_codes_never_become_carriageway`, plus the
`aardeweg` tests for 125) and every included one
(`test_every_documented_motorized_morf_code_can_become_carriageway`).

A related gap the same review found: an `unsplit` face (no internal WGO line
at all) has `corridor == face`, so the axis-coverage ratio is 1.0 by
construction the moment any qualifying axis reaches it — `--axis-coverage-
ratio-min` has no effect there, only `--axis-min-extent-m` does. A single
motorized paved axis running through a wide, WGO-less corridor does not by
itself prove the *entire* width is carriageway. `--unsplit-max-width-m`
guards this: for an `unsplit` face only, the corridor's area divided by the
longest paved (or, for `carriageway_unpaved`, unpaved) axis's covered length
must not exceed it (default 12 m), or
the face stays `unmapped`, reason
`unsplit_corridor_too_wide_for_single_carriageway`. This guard does not apply
to `partitioned` faces, where the subdivision itself is evidence.

### `carriageway_unpaved`: what the GRB says is not paved

Before this class existed, an axis the GRB explicitly codes as unpaved fell
into `unmapped` with the reasons `axis_surface_not_paved` or
`axis_not_motorized_carriageway`: the source measured it, and the bundle
dropped it. A downstream consumer falling back on a default tariff would then
price these roads as paved. `carriageway_unpaved` keeps that evidence, so its
first role is a **guard** — extending GRB coverage must not overwrite what the
GRB itself knows is unpaved — before it is any refinement.

It rests on the same quality of evidence as `carriageway_paved` and nothing
else: an in-service (`STATUS=4`) Wegsegment axis, the same coverage ratio and
extent floor, the same `unsplit` width guard, and one documented code.

Official wording, from the GRB objectenhandboek (Wegsegment) domain and from
the `LBLVERH`/`LBLMORF` labels the WFS delivers with each record:

| Source code (Wegsegment) | Handbook domain / delivered label | Unpaved evidence? |
|---|---|---|
| `VERH=2` on `MORF` 101–112 | *onverharde weg* / *weg met losse verharding* | yes |
| `MORF=125`, `VERH` 2, -8, -9 or missing | *aardeweg* | yes |
| `MORF=125`, `VERH` 1 or 12 | *aardeweg* coded paved | no — `axis_surface_conflict` |
| `MORF=125`, any other `VERH` | undocumented surface code | no — `axis_not_motorized_carriageway` |
| `MORF=120`, `VERH=2` | *dienstweg* | only with `--dienstweg-unpaved-evidence` |
| `MORF=120`, any other `VERH` | *dienstweg* | no — never assumed unpaved |
| `VERH=2` on `MORF` 113/114/116/130 | unpaved, but not a carriageway | no — `axis_not_motorized_carriageway` |
| `VERH=12` | *weg met zowel vaste en losse verharding* | unchanged: paved evidence |
| `VERH` -8 / -9 | *niet gekend* / *niet van toepassing* | no, except on an `aardeweg` (above) |

Counts of in-service (`STATUS=4`) Wegsegment records on the live WFS for all
of Flanders (`resultType=hits`, 1 October 2026; the server caps
`numberMatched` at 10000) back these choices:

- `VERH=2`: `MORF` 103 ≥ 10000, 114 ≥ 10000, 125 ≥ 10000, 120 = 115, other
  motor-traffic codes 102–112 = 137 together, 113 = 9, 116 = 1. Unpaved
  single-carriageway roads are common; unpaved cycling paths (114) are as
  common and are not a carriageway.
- `MORF=125` (`aardeweg`): `VERH=2` ≥ 10000, -9 = 1282, -8 = 12, 1 = 405,
  12 = 30. An `aardeweg` with no surface code is overwhelmingly an unpaved
  one; the 435 coded paved go to `axis_surface_conflict`.
- `MORF=120` (`dienstweg`): `VERH=1` = 797, 2 = 115, 12 = 6, -8 = 3, -9 = 2.
  A `dienstweg` is therefore **not** unpaved by default — that earlier
  reading had no source and the data contradict it — so the opt-in only
  admits one explicitly coded `VERH=2`.

Two precisions the name does not carry:

- `VERH=2` is delivered as *weg met losse verharding* — an unbound surface
  such as gravel or crushed stone — **not** necessarily bare earth. The
  handbook's *onverharde weg* says "not paved", not which material. The
  class states that the source says "not paved"; it does not state a
  material.
- Which client cost class an unpaved carriageway maps to is the consumer's
  decision, made in the consumer's own mapping. This plugin assigns no tariff.

The option is recorded in `report.json` (`dienstweg_unpaved_evidence`, and
`classification.options`).

**Vetoes, and why they are asymmetric.** A contradiction is looked for among
every candidate, not only the matches: an axis that merely crosses the face
below the coverage threshold can still veto a verdict (for instance a gravel
road crossing a paved road's face, cut transversally by Wcz lines so the
paved axis falls under threshold in each piece). A veto must run more than
`--length-tolerance-m` inside the face, which discards a vertex overshooting
a WGO line by numerical noise — though not an axis drawn collinear with a
WGO line through interpolated vertices, which the exact boundary exclusion
of `_interior_length` can count in both faces (see the limitations below).

- `carriageway_unpaved`, the new class, gets the strict rule: every candidate
  on the face must itself be unpaved evidence. A candidate coded `VERH` 1 or
  12, whatever its `MORF` — a paved cycle path or service road is enough —
  gives `axis_surface_conflict`; a motor-traffic candidate of unknown
  surface, which may be the face's own road with the unpaved one only
  crossing it, gives `axis_surface_unknown`; any other (a path, a service
  road, an unknown `MORF`) gives `axis_not_motorized_carriageway`.
- `carriageway_paved`, validated on real data, keeps its rule. Only unpaved
  evidence vetoes it, and only when matched or running, inside the face
  itself, at least `--axis-min-extent-m` **and** half the length of the
  longest paved match. A gravel side road joining the paved axis at any
  angle without being matched itself, or a neighbouring unpaved road grazing
  the WGO line, does not demote a long carriageway; an unbound footpath says nothing about the
  carriageway's own surface at all. A paved face can still become
  `axis_surface_conflict` when a comparably long unpaved-evidence axis
  overlaps it; on the Gand sample that never happens, as no such axis exists
  there.

A *matched* `aardeweg` coded `VERH` 1 or 12 contradicts itself
(`axis_surface_conflict`); as a mere candidate it only vetoes the unpaved
class, like any paved axis.

The class is deliberately **not** derived from WGO. A Woz line marks the edge
of a soft shoulder, but capture follows the Wcz > Wrb > Woz priority (a
missing Woz proves nothing), and reading the shoulder from adjacency is the
same invertible inference that removed `sidewalk` (next section).
`woz_boundary_ratio` stays diagnostic.

On `ready_for_costing`: a `carriageway_unpaved` face is classified by explicit
evidence exactly as a `carriageway_paved` one is, so `validate_functional_faces`
counts both alike in its per-face self-check. That column is still not
published, and the bundle-level `ready_for_costing` stays `false` — this class
does not loosen it. On the Gand sample (121 faces) no in-service axis carries
`VERH=2`, `MORF=120` or `MORF=125`: the class is reachable only in tests there,
and the published classification is unchanged face for face.

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
This module therefore only ever produces `carriageway_paved`,
`carriageway_unpaved` or `unmapped`;
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
not evidence that the corresponding zone is absent. Classification itself
does not certify a road crossing; KNW reconciliation (below) only adds
diagnostic columns. `ready_for_costing` stays false at the bundle level
regardless of any per-face evidence.

### Known limitations, not yet addressed

Flagged by the third and fourth adversarial reviews, deliberately left open:

- `axis_coverage_ratio`/`classification_evidence` report `0.0`/empty for an
  axis rejected by the `axis_min_extent_m` floor, rather than its measured
  ratio, because that floor is applied before an axis enters `candidates` at
  all. A candidate rejected only by `axis_coverage_ratio_min` *is* reported
  (the fallback branch uses the full `candidates` list); one rejected by the
  extent floor is not.
- A contradicting axis rejected by the `axis_min_extent_m` floor is not a
  candidate, so it vetoes nothing — the same floor that stops a short graze
  from proving a class also stops it from vetoing one.
- **Promotion path still open:** an unpaved axis crossing a road whose own
  axis is absent, out of service, or under the extent floor faces no veto, so
  the crossed piece can come out `carriageway_unpaved` (pinned by
  `test_an_unpaved_crossing_over_a_road_with_no_in_service_axis_still_classifies`).
  The same holds, as before, for a paved axis crossing an axis-less road.
  For `carriageway_unpaved`, vetoes close the case where the crossed road has
  any in-service axis past the extent floor; for `carriageway_paved`, the
  crossed piece of an unpaved carriageway or `aardeweg` only vetoes when it
  is comparably long, and an unbound path never vetoes, by design.
- A *matched* unpaved-evidence axis always vetoes a paved one, however short
  — as a matched `VERH=2` carriageway already did before this class existed.
  In an `unsplit` corridor every axis past the extent floor is matched (ratio
  1.0 by construction), so an `aardeweg` or gravel side road entering it
  demotes the whole face to `axis_surface_conflict`; likewise for a side road
  ending entirely inside a partitioned face. This loses paved coverage but
  never promotes; relaxing it would let a short matched axis be overruled,
  which this module does not do.
- An axis drawn collinear with a WGO line through interpolated vertices is
  not excluded by `_interior_length` (exact difference, see above) and can
  count, as a veto, in both adjacent faces.
- `carriageway_unpaved` has not been validated on real unpaved data: the
  Gand bbox has no `VERH=2`/`MORF=120`/`MORF=125` axis. Run a rural bbox with
  `aardewegen` before any consumer arms it.
- `_interior_length`'s boundary exclusion is an exact (unbuffered) geometry
  difference: an axis a millimetre inside a boundary, rather than exactly on
  it, is not excluded at all, unlike the diagnostic `_boundary_ratios`
  (`dwithin` with `boundary_tolerance_m`). No axis in the validated Gand
  bundle triggers this; it is a documented gap, not yet observed in the wild.
- `axis_min_extent_m` measures length inside the corridor, not depth of
  penetration or angle: an axis entering at a shallow angle from the outer
  boundary and staying within a narrow band can clear the floor while never
  genuinely crossing into a wider carriageway.
- `VERH=12` is *weg met zowel vaste en losse verharding* (both bound and
  unbound surface) and stays paved evidence; whether a mixed road is priced as
  paved is again the consumer's call.
- `MORF=120` (dienstweg) and `125` (aardeweg) are excluded from the
  motor-traffic set by this module's own conservative reading, not because
  the handbook states they cannot carry vehicles. A paved `dienstweg` (the
  common case) therefore stays `unmapped` rather than `carriageway_paved` —
  worth revisiting, since such roads are frequent in regional data.

## KNW structure reconciliation

Official semantics (objectenhandboek, `kunstwerk-knw`): KNW geometry is a
polygon, with **17** documented `TYPE` codes; only two describe a road
structure — 1 = overbrugging (bridge), 12 = tunnelmond (tunnel entrance). The
official page for `overbrugging` states the WBN corridor "always connects to
a Knw of type overbrugging at the expansion joint or edge of the bridge
deck" — a corridor discontinuity, not a same-level road crossing.

The other 15 codes (hydraulic structures, monuments, pylons, pillars,
chimneys, silos, wind turbines, cabins, water towers, chemical
installations, breakwaters, palisades, …) are excluded, but not all for the
same reason `TYPE=5` (pijler/pillar) deserves a specific note: the
handbook's own worked example for it describes bridge piers ("pijlers van
deze brug ... binnen de wegbaan"), so it is not simply unrelated street
furniture. Its coverage rule also captures pillars supporting *any* civil
structure within 5 m of a WBN, including buildings — ambiguous enough that
this module excludes it from the structure filter rather than risk a false
`bridge`. That is a one-directional, known gap: on a 3.5 km sample around
Gand centre, 59 corridors intersect a `pijler` and 51 of them are not
flagged, including 17 corridors each touching 3 or more pillars (one, with
13 aligned piers, has a real bridge only 21 m away). `TYPE=2` (waterbouwkundige
constructie, hydraulic structure) is excluded too and is real, non-trivial
GRB data (6 in that same sample) — a filter conflating it with `TYPE=1`
would flag canal/lock infrastructure that has nothing to do with a road
bridge. `VORM` (1 = enkelvoudig/single, 2 = samengesteld/several grouped
installations) is not used to decide anything.

The bridge/WBN-touching claim was checked past the validation bbox: on the
3.5 km sample, 91 of 92 bridges touch a WBN corridor at distance exactly 0,
the remaining one at 7.1 m with no corridor within 0.5 m — no case falls in
the grey zone between the default 1 mm tolerance and a plausible looser one.

`reconcile_knw_structures` (`src/gispulse/capabilities/vector/reconcile_knw_structures.py`)
implements this: it adds `structure_proximity`
(`bridge`/`tunnel`/`bridge,tunnel`/`none`) and `structure_evidence` (the
touching KNW IDs, e.g. `21528:bridge`) to the classified faces, wired into
`prepare_grb` after classification succeeds. **This is corridor-level
contact, not per-face distance**: every face in a touched corridor is
flagged identically, whether it sits right at the join or far across a wide
corridor, because nothing in WBN/KNW identifies which face *within* a
touched corridor sits at the actual join — narrowing that down without
further explicit evidence would repeat the geometric-inference mistakes
`classify_grb_faces` has twice had to retract. On the 3.5 km sample, flagged
corridors extend past 300 m from the touching structure in the worst case,
and a `bridge`-flagged face is not guaranteed closer to a bridge than a
`none` face elsewhere — the label says which corridor a face belongs to,
never a distance ranking. This is **diagnostic only**: it never changes
`functional_class` or the bundle-level `ready_for_costing`, which stay
exactly as `classify_grb_faces` and the overall bundle already state. Only
structure-typed (1/12) KNW rows are required to have valid geometry and a
usable ID: an unrelated defective record (a malformed pillar, say) must not
withhold reconciliation for a bundle that never needed it. A genuine KNW
defect on a structure-typed row degrades to
`structure_reconciliation: {"status": "failed", ...}` in the report — the
classified faces are still published, just without the
`structure_proximity`/`structure_evidence` columns, matching the
pre-reconciliation contract.

Validated live on the Gand bbox (`report.json`'s `structure_reconciliation`
section): 3 KNW polygons are typed `bridge` in that extent (OIDN 21528,
21529, 51104). Two of them (21528, 21529) each touch a WBN corridor that
carries at least one face in this bundle — 21529 touches two such corridors
(a bridge commonly touches more than one; on the 3.5 km sample, 2.6
corridors on average, up to 9) — for 3 flagged corridors (130368, 130374,
130533) in total. The third bridge (51104) touches only a corridor that is
`outside_acquisition_coverage` and carries no face at all;
`structures_matching_no_corridor` reports this as `1` rather than silently
dropping it. 17 of the 121 candidate faces sit in one of the 3 flagged
corridors (104 stay `none`), citing `21528:bridge` or `21529:bridge` as
`structure_evidence`. **2 of the 22 `carriageway_paved` faces are among the
flagged 17** — proof this reconciliation catches a real case: a face this
module's own evidence says is a paved, in-service carriageway, sitting in a
corridor that leads directly into a bridge, and therefore not to be assumed
drillable at grade without a separate check. No KNW polygon in this bbox is
typed `tunnel`
(12), so that path is exercised only by the unit tests' synthetic geometry.

## Road crossings: level evidence

`build_grb_road_crossings`
(`src/gispulse/capabilities/vector/grb_road_crossings.py`), wired into
`prepare_grb`, publishes three files next to the classification (it reads
WBN, Wegsegment and KNW directly, never the classification):

- `road_crossings.geoparquet` — one row per physical road unit, EPSG:31370:

  | column | content |
  |---|---|
  | geometry (active) | footprint, `Polygon`/`MultiPolygon` |
  | `axis` | second geometry column, `LineString`/`MultiLineString`, clipped to the footprint |
  | `road_id` | `GRB:WBN:<first OIDN>` or `GRB:KNW:<OIDN>+<OIDN>…` |
  | `structure` | `ground`, `grade_separated` or `unknown` |
  | `complex_crossing` | `MORF` in `complex_morf_codes` (default 101 autosnelweg) |
  | `structure_evidence`, `source`, `source_layer`, `source_ids`, `axis_ids`, `morf_codes` | provenance |

- `road_crossings_coverage.geoparquet` — one polygon: where the artifact is
  complete;
- `road_crossings_exclusions.geoparquet` — every area removed from that
  coverage, with its reason.

Axes are sorted by `MORF`/`STATUS` only. In-service (`STATUS` 4) 101–112
are carriageways (the classification's gate). 113 voetgangerszone, 114
wandel- of fietsweg, 116 tramweg and 130 veer, documented as closed to
general motor traffic, are not. Everything else — 120 dienstweg and 125
aardeweg (bare labels in the domain), −8, a carriageway not in service — is
**unresolved**.

**`ground` comes from a WBN element, never from a default.** The
objectenhandboek page *wegbaan*: "enkel de aan het maaiveld zichtbare
wegcorridor wordt opgenomen als wegbaan. Waar de wegcorridor ingetunneld is,
op een overbrugging gelegen is of door een overbrugging wordt afgedekt, wordt
geen wegbaan (Wbn) opgenomen." WBN elements carrying a carriageway axis are
therefore positive evidence of a ground-level corridor. **The footprint is
the whole WBN corridor** (sidewalks and verges inside it), not a
carriageway-only surface. Touching `wegsegment`-type elements sharing a
carriageway axis and the same `complex_crossing` are fragments of one
carriageway: one `road_id`, footprints united, the original axes clipped
against the union so they stay continuous across arbitrary element cuts. A
`kruispuntzone` (WBN `TYPE` 1), an element holding a node where three or
more axis ends meet (a junction not mapped as a kruispuntzone — a side
street, a parking entrance) and an element with an undocumented `TYPE` are
never merged. On the three bboxes below, 13, 3 and 5 units are merged; the
few carrying several street names are one carriageway whose left/right or
municipal names differ.

**`grade_separated` needs a level relation, not a bridge polygon.** Touching
KNW `overbrugging` (1) / `tunnelmond` (12) polygons form one structure. It
is `grade_separated` when at least two carriageway axes cross inside it
without a node, every such axis takes part in such a crossing, and none ends
inside. The OSLO Wegenregister profile defines a Wegknoop as the object
describing connectivity between two segments: axes crossing with no node are
not connected there, and the KNW polygon documents the structure separating
them. This is an **inference from two official facts**, not a stated rule
(the Wegenregister `OngelijkgrondseKruising` relation states it directly
and is not exposed by any WFS). Measured on a Gand sample: 91 of the 101
node-less crossings of carriageway axes lie inside a KNW 1/12 polygon.

**`unknown`**: any other structure carrying a carriageway axis —
`single_axis` (a road over water, or under a railway bridge: KNW alone
cannot tell which), `axis_ends_inside` (a junction or dead end on or under
the structure), `axis_not_crossed`.

**Coverage** starts from the bbox and excludes:

| reason | what |
|---|---|
| `structure_<reason>` | every `unknown` footprint |
| `structure_axes_unresolved` | every structure carrying an unresolved axis (it could change the verdict) |
| `wbn_axes_unresolved` | a WBN element carrying only unresolved axes; inside a `ground` element, a buffer around the unresolved axis only (a trench crossing that axis alone would see none) |
| `wbn_without_axis` | a WBN element with no axis at all |
| `footprint_crosses_bounds` | every footprint leaving the bbox: axes entirely outside were never acquired, so its verdict cannot be trusted |
| `axis_without_unit` | a `crossing_exclusion_buffer_m` buffer (default 15 m) around drivable axis length no unit accounts for: a road with no footprint (a private road the GRB did not capture, a tunnel body) or one lying exactly on a boundary |
| `nodeless_crossing_outside_structure` | the same buffer around node-less crossings and self-crossings of drivable axes outside every structure: level evidence contradicting the ground rule |

A structure with no drivable axis is no road and stays covered; a WBN
element carrying only documented non-motor axes stays covered without a
row. Footprints are never claimed twice: structures take priority over WBN
("een wegbaanelement houdt op ter hoogte van een kunstwerk"), then the lower
WBN `OIDN`.

**Validated live** on three bboxes (Gand 5.5 × 6.5 km, two 6 × 6 km tiles
near Oudenaarde and Leuven), 1.8–2.5 s each:

| bbox | ground units (elements) | grade_separated | unknown: single / ends inside / not crossed | ground elements with an unresolved axis + unresolved-only elements | drivable axis without unit | coverage |
|---|---|---|---|---|---|---|
| Gand | 2 401 (2 414) | 21 | 8 / 7 / 3 | 87 + 19 | 61.1 km | 93.3 % |
| Oudenaarde | 1 791 (1 795) | 6 | 11 / 3 / 2 | 33 + 2 | 35.9 km | 96.3 % |
| Leuven | 1 790 (1 795) | 8 | 12 / 4 / 0 | 146 + 19 | 87.2 km | 91.4 % |

No WBN element overlaps a KNW 1/12 polygon and no two WBN elements overlap
(above 0.01 m²); every axis is simple. Most axes without a unit are local
access roads the GRB maps with no WBN (in Gand, 57 of 85 sampled were
private) and aardewegen.

**Known limits.**

- KNW completeness: `ground` relies on the GRB not having missed a
  structure; the node-less-crossing exclusion catches part of these.
- Up/down is never resolved. `grade_separated` holds for an excavation that
  follows one of the crossing roads through the structure; an excavation dug
  at ground level under a deck, off any mapped road, would cross the lower
  road there and is not represented. `single_axis` structures stay
  `unknown` even when the road is plainly on the deck.
- `complex_crossing` is per unit: a corridor carrying an autosnelweg and its
  parallelweg is complex as a whole.
- The footprint is the whole corridor. A consumer that bores "the full
  length inside the footprint" over-bores a trench that runs *along* a road
  inside its corridor and wavers across its axis: run against 25 real MILOU
  tracés, that accounts for 36 such "crossings" totalling 7.35 km (up to
  922 m). Telling a longitudinal run from a crossing belongs to the consumer.
- `road_id` is stable for one acquisition only; bundles from different
  bounds must be deduplicated on `source_ids`, never concatenated.
- An axis piece ending within floating-point distance of a footprint
  boundary, or at a self-crossing split, is a measure-zero consumer edge case.
- The exclusion buffer has no source basis; it is an explicit, reported
  parameter. Flanders only: PICC/UrbIS level evidence is not wired here.

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
`ready_for_costing` column. Zero faces landed on `contradictory_axis_surface` (since
renamed `axis_surface_conflict`),
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

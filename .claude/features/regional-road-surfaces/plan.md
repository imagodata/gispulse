# Regional road surfaces — gispulse #464

## Scope
Acquisition of official regional road footprints and axes in gispulse. Preserve
raw classification, geometry, levels and provenance. MILOU owns material mapping,
source priority, road-crossing decisions and EUR/m costing (MILOU PR #1401).

Base: integration/v2.4 (consumer dependency), isolated branch feat/regional-road-surfaces.
No geodata committed. No changes to consumer canonical configuration or routing cache.

## First milestone: PICC vector access
- [x] PICC MapServer layer 24 road polygons and layer 21 axes.
- [x] Explicit mandatory WGS84 bbox, bounded counted pagination, identity checks.
- [x] ArcGIS spatial-window semantics, including short/empty pages.
- [x] Reject older core implementations lacking required pagination.
- [x] Dry-run local export; raw fields, EPSG:31370, per-layer report/checksum.
- [x] Independent review and targeted regressions.
- [x] Live small-area fetch of both layers with page size 2.

## Remaining milestones
- [x] Verify PICC NIVEAU semantics and matching of road axes to footprints against official source contracts. Verified 2026-09-11 against the PICC conceptual data model: `NIVEAU` is relative on `VOIRIE_AXE` (layer 21, domain `{-2,-1,0,1,2}`) but **absolute** on `VOIRIE_SURFACE` (layer 24, 0=ground/+=bridge/-=tunnel) — same field name, two reference frames, documented in the plugin README. The model defines an official axis↔footprint join table (`VOIRIE_SURF_AXE_REL`), but it is not reachable through the currently wired ArcGIS REST channel (`PICC_VDIFF` MapServer advertises no tables; the FeatureServer variant errors server-side) — matching is not implemented, only the gap is documented.
- [x] Extend GRB source: WBN/WGO/Wegsegment/Wegknoop/KNW/WGA, native INTERSECTS and counted XML hits.
- [x] Reproducible GRB candidate partition bundle with topology diagnostics.
- [x] Classify reconstructed GRB faces from explicit upstream evidence (`classify_grb_faces`, PR #496). Two-pass, fail-closed, evidence-cited; validated live on the Gand bbox (36 carriageway_paved / 53 sidewalk / 32 unmapped of 121 faces, see plugin README `## Validation`). Adversarially reviewed and corrected before merge (axis-graze false positive, Wcz-inference direction bug, unsplit corridors wrongly excluded, contradictory-axis handling, ready_for_costing leak — see PR history).
- [ ] Reconcile axes with KNW structures (bridges/tunnels) before costing — not started; classification above does not cover this.
- [x] UrbIS source acquisition (StreetSurfaces, StreetAxes, Bridges, Tunnels), counted WFS and live small-area validation.
- [x] Validate UrbIS level/axis/footprint reconciliation contract. Verified 2026-09-11 against the official ISO 19131 product specs (Paradigm.brussels): `lvl` is relative everywhere it appears (never absolute, unlike PICC surface `NIVEAU`), `StreetSurfaces.TYPE` is exhaustively enumerated (`SW`=sidewalk confirmed), and no segment-level foreign key exists between `StreetAxes` and `StreetSurfaces` — only a street-*name*-level `streetId`. Documented in the plugin README; reconciliation itself stays geometric/level-aware and unimplemented.
- [~] Prepare representative consumer artifacts and validate coverage/GC reconciliation in MILOU. Contract plumbing validated live 2026-09-11: real PICC (Namur, 17 surfaces), classified GRB (Gand, 121 faces) and UrbIS (Brussels, 29 surfaces) bundles run through MILOU's actual `scripts/prepare_regional_surfaces.py` against `config/surface_sources.yaml` and `cost_model.yaml` (branch `fix/surface-corridor-width-guard`), with a minimal synthetic OSM stand-in (the real 2GB `osm_surface_layers.geoparquet` was not loaded — MILOU's `read_geoparquet` crops by bbox only after a full in-memory read, unsafe to run unattended). Result: PICC 17/17 mapped, GRB 121→89 accepted (36 heavy_pavement / 53 sidewalk, 32 correctly left unmapped), UrbIS 29→28 mapped (1 `P`/Place correctly unmapped, out of contract) — `classify_grb_faces`'s `functional_class` output needed zero adaptation to satisfy MILOU's consumer contract. **Not done**: the actual GC/CapEx coverage comparison (`scripts/reconcile_surface_capex.py`) against the real 1564-site production catalogue — deliberately deferred, it is heavy (2GB artifact, hours of runtime) and its outcome is a recette decision for MILOU/Simon, not a technical check to run unattended.
- [ ] Full regional acquisition, consumer activation, then routing cache regeneration.

Issue #464 stays open: first milestone does not complete regional coverage.

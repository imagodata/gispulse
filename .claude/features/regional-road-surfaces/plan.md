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
- [ ] Verify PICC NIVEAU semantics and matching of road axes to footprints against official source contracts.
- [ ] Extend existing GRB source where needed; WGA is ancillary structures, not sidewalk polygons.
- [x] UrbIS source acquisition (StreetSurfaces, StreetAxes, Bridges, Tunnels), counted WFS and live small-area validation.
- [ ] Validate UrbIS level/axis/footprint reconciliation contract.
- [ ] Prepare representative consumer artifacts and validate coverage/GC reconciliation in MILOU.
- [ ] Full regional acquisition, consumer activation, then routing cache regeneration.

Issue #464 stays open: first milestone does not complete regional coverage.

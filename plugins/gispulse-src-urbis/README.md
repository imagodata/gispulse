# UrbIS Brussels roads and structures

Four raw layers from the official WFS:
`urbis-street-surfaces-bxl`, `urbis-street-axes-bxl`, `urbis-bridges-bxl`,
`urbis-tunnels-bxl`.

Endpoint: https://geoservices-vector.irisnet.be/geoserver/urbisvector/wfs

The plugin requires this branch's counted WFS core. The request extent and output
are **EPSG:31370**, unlike PICC's WGS84 request extent. A missing or invalid bbox
fails before HTTP. Counts (`numberMatched`) are checked before/after collection;
IDs (`INSPIRE_ID`) must be unique and pagination is sorted by that field. Each
layer defaults to page size 2000, max pages 1000 and max features 1000000. A count
query requests one feature because this service's WFS `resultType=hits` response
is XML; GeoJSON GetFeature exposes `numberMatched`.

## Local use

Install the matching core and plugin, or use
`PYTHONPATH=src:plugins/gispulse-src-urbis` from the source checkout. The source
registry discovers the installed plugin through `gispulse.data_sources`.

The following explicit acquisition example uses the existing gispulse fetcher
and writer (it performs network access and writes the requested local file):

```python
from gispulse_src_urbis.source import UrbisSource
from gispulse.adapters.ogc.wfs_fetcher import WfsFetcher
from gispulse.core.io.geoparquet import write_geoparquet

access = UrbisSource().access_for("urbis-street-surfaces-bxl")
result = WfsFetcher().fetch(access, extent=(148890, 170399, 148961, 170511))
assert result.metadata["complete"]
write_geoparquet(result.data, "/tmp/urbis-street-surfaces.geoparquet")
print(result.metadata)
```

Keep the metadata with exported files. The guarantee is counted collection with
unique IDs, **not a remote transactional snapshot**. Unknown counts, duplicate
pages, changed counts or mismatched advertised CRS fail. Empty layers retain a
geometry column with CRS; raw field columns are absent if the service returns no
features.

## Raw semantics

The live service exposes uppercase `TYPE`, `LVL`, `INSPIRE_ID`; all other returned
properties are preserved. Published specifications use camel/lowercase names,
so use the actual WFS names when preparing consumer mappings.

- Land cover: https://urbisdownload.datastore.brussels/UrbIS/TechSpec/LandCover_TechSpec_FR20240401.pdf
- Transport: https://urbisdownload.datastore.brussels/UrbIS/TechSpec/Network_TechSpec_FR20240408.pdf

StreetSurfaces `SW` denotes sidewalk, `S` a road segment and `I` an intersection.
These are functional classes, not observed paving materials. The `LVL` attribute
is relative to other surfaces: this plugin never converts it into an absolute
ground/bridge/tunnel determination. Axes and footprints have distinct identifiers;
a geometric and level-aware reconciliation is required for road crossing proofs.

Live validation on 2026-09-08: bbox near 4.353–4.354 E / 50.844–50.845 N,
transformed to EPSG:31370, returned 28 surfaces in 14 pages and 10 axes in 5 pages
with page size 2; bridge/tunnel counts were zero in that small bbox. Separate
one-feature service probes confirmed nonempty Bridges and Tunnels on a larger
bbox. No regional coverage or client crossing decisions are certified by this test.

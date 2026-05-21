# Worldwide aggregator

The **worldwide geo-data aggregator** is a first-party `gispulse` source,
shipped in `1.9.0` (EPIC
[#226](https://github.com/imagodata/gispulse/issues/226)) and published
to PyPI in `2.0.0`. Rather than multiplying marketplace packages for
every public dataset, GISPulse maintains a **curated catalog** in-repo
(`core/data/worldwide_catalog.yml`) and a single
`WorldwideCatalogSource` that materialises it at runtime.

> Design `#226`, decision 2 — *the worldwide catalog is free*: published
> under `gispulse.data_sources` in the `gispulse` distribution itself, so
> `ExtensionHub` resolves it as `first-party` and the community gate
> passes with no extra code.

## Concept

```
core/data/worldwide_catalog.yml      ← source of truth (curated YAML)
              │
              ▼
WorldwideCatalogSource               ← a first-party DeclarativeSource
              │
              ▼ (.fetch())
LazyFetcher (4 adapters)             ← one per AccessProtocol
              │
              ▼
DuckDB / GDAL / WFS                  ← lazy execution + bbox push-down
```

Each YAML entry becomes a `SourceEntryRef` carrying the catalog's **four
filter axes** (issue
[#227](https://github.com/imagodata/gispulse/issues/227)):

| Axis              | Value                                                                |
|-------------------|----------------------------------------------------------------------|
| `domain`          | `SourceDomain` (`base`, `observation`, `elevation`, …)               |
| `payload`         | `Payload` (`vector`, `raster`, `pointcloud`, `tiles`, `table`)       |
| `jurisdiction`    | ISO 3166-1 / `world` / `eu`                                          |
| `access.protocol` | `AccessProtocol` (`remote-table`, `ogc-features`, `stac`, `http-file`)|

Plus a `family` grouping (in `metadata`) for the portal gallery.

## The four fetchers

Four families of adapters, one per `AccessProtocol`, all subclassing
`LazyFetcher`. The DuckDB engine downloads **nothing** at instantiation
— the scan is lazy, the `bbox` push-down is built on the SQL side.

| Protocol          | Fetcher                | Issue                                                                 | Notes                                                                                                              |
|-------------------|------------------------|-----------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------|
| `remote-table`    | `GeoParquetS3Fetcher`  | [#229](https://github.com/imagodata/gispulse/issues/229) (A3)         | `read_parquet('s3://…', hive_partitioning=true)` + bbox push-down on the Overture struct. DuckDB `httpfs`.         |
| `ogc-features`    | `OGCFeaturesFetcher`   | [#230](https://github.com/imagodata/gispulse/issues/230) (A4)         | OGC API Features / WFS. Lazy `ST_Read(...)` + COPY via the WFS client to materialise.                              |
| `stac`            | `STACFetcher`          | [#231](https://github.com/imagodata/gispulse/issues/231) (A5)         | STAC search + `download_asset` of the COG; raster read on demand.                                                  |
| `http-file`       | `HttpFileFetcher`      | [#232](https://github.com/imagodata/gispulse/issues/232) (A6)         | Public vector/raster file behind a GDAL `/vsicurl/` virtual path; streamed download.                               |

### `bbox` push-down

Each fetcher implements `_scan_sql(extent)`, which takes the rule's
`Extent` and injects it into the DuckDB query. Measurable consequence:
a département-scoped query on Overture Buildings touches a few **MB**,
not the 2 GB world parquet.

## SSRF security

Endpoints are checked **structurally at load** of the YAML (allow-listed
scheme, non-private / non-loopback host), with **no** DNS resolution —
the module import stays network-free. The full DNS-resolving guard
(issue [#199](https://github.com/imagodata/gispulse/issues/199)) runs
**per fetch** inside the `LazyFetcher`, so an attacker cannot rebind a
public hostname at load to a private IP at execution.

Schemes accepted at load time: `http`, `https`, `s3`.

## The curated catalog

`core/data/worldwide_catalog.yml` ships four initial families:

| Family                   | Typical dataset             | Protocol         |
|--------------------------|-----------------------------|------------------|
| `overture-geoparquet`    | Overture Places / Buildings | `remote-table`   |
| `ogc-features`           | INSPIRE & public WFS        | `ogc-features`   |
| `stac-imagery`           | Sentinel-2, MS Planetary    | `stac`           |
| `opendata-fr`            | FR vectors (data.gouv)      | `http-file`      |

Each entry carries an immutable `revision_token` (for a versioned
release) or `null` (live service) — A14
([#240](https://github.com/imagodata/gispulse/issues/240)) wires the
freshness probe into the `SourceWatcherRegistry`.

## Example — querying the aggregator in Python

```python
from gispulse.plugins.api import get_source

worldwide = get_source("worldwide-catalog")

# List a family
overture = [
    e for e in worldwide.entries()
    if e.metadata.get("family") == "overture-geoparquet"
]

# Fetch an entry
entry = worldwide.catalog().lookup("overture-buildings")
# entry.access.protocol → AccessProtocol.REMOTE_TABLE
# entry.access.endpoint → "s3://overturemaps-us-west-2/release/..."
# entry.payload         → Payload.VECTOR
# entry.jurisdiction    → "world"
```

## Example — adding a source to the aggregator

Adding an entry to the catalog is one YAML stanza:

```yaml
# core/data/worldwide_catalog.yml
entries:
  - id: my-public-dataset
    name: My public dataset
    family: opendata-fr
    domain: environnement
    payload: vector
    jurisdiction: FR
    access:
      protocol: http-file
      endpoint: https://data.gouv.fr/.../layer.gpkg
      format: application/x-sqlite3
    revision_token: null
    metadata:
      provider: My Ministry
      license: Licence Ouverte 2.0
```

The entry is immediately available to `entries()` / `catalog()`. To
distribute outside the repo, see the
[`source-catalog`](./data-packs#supported-contents) content type of
data packs — the addition then goes through a `DataPackManifest`
rather than a fork.

## CLI / portal

- `gispulse sources list` — inventory of `ExtensionHub` sources,
  including `worldwide-catalog`.
- `gispulse sources catalog worldwide-catalog --family ogc-features` —
  family filter (CLI).
- Portal: "Worldwide catalog" panel with `domain` / `payload` /
  `jurisdiction` / `protocol` filters ([CLI ↔ portal
  symmetry](./symmetry)).

## Code references

- [`gispulse.plugins.worldwide_source.WorldwideCatalogSource`](https://github.com/imagodata/gispulse/blob/main/src/gispulse/plugins/worldwide_source.py)
- [`gispulse.core.fetchers/*`](https://github.com/imagodata/gispulse/tree/main/src/gispulse/core/fetchers)
- [`core/data/worldwide_catalog.yml`](https://github.com/imagodata/gispulse/blob/main/src/gispulse/core/data/worldwide_catalog.yml)
- EPIC [#226](https://github.com/imagodata/gispulse/issues/226) — sprint v1.9.0
- Issue [#199](https://github.com/imagodata/gispulse/issues/199) — SSRF guard

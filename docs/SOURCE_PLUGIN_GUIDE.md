# Source Plugin Authoring Guide

How to write a `gispulse-src-*` package — a **data source** plugin for the
GISPulse ETL platform (EPIC #175). A source answers *where data comes
from* (the "Extract" stage); it is discovered by the `PluginHub`, gated
by tier/trust, and consumed by pipelines and `source_changed` triggers.

This guide uses the shipped reference plugin
[`plugins/gispulse-src-cadastre`](https://github.com/imagodata/gispulse/tree/main/plugins/gispulse-src-cadastre)
as its worked example.

---

## 1. The idea — declare, don't fetch

A source plugin is **declarative**. You describe *what* entries exist and
*how* each is reached (an `AccessSpec`); GISPulse owns the network code.
A subclass of `DeclarativeSource` gets `fetch()` for free — it dispatches
to a transport adapter (`Fetcher`) registered for the entry's
`AccessProtocol`. **You write no HTTP code.**

```
DataSource.fetch(entry_id)
      → DeclarativeSource resolves the entry's AccessSpec
      → PROTOCOLS.dispatch_fetch(access)        ← SSRF-guarded
      → Fetcher for access.protocol  (WFS / OGC_FEATURES / STAC / REST_API / …)
      → SourceResult
```

The contract surface is re-exported from the **`gispulse.plugins.api`**
SDK module — import everything from there, never from `core.*` directly.

## 2. Package layout

```
gispulse-src-myprovider/
├── pyproject.toml
└── gispulse_src_myprovider/
    ├── __init__.py        # the register() entry-point hook
    └── source.py          # the DeclarativeSource subclass
```

## 3. `pyproject.toml` — declare the plugin

Two blocks make a package a GISPulse source plugin: the entry-point and
the `[tool.gispulse.plugin]` manifest.

```toml
[project]
name = "gispulse-src-myprovider"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = ["gispulse"]

# Discovered by core.plugin_hub.PluginHub under the data_sources group.
[project.entry-points."gispulse.data_sources"]
myprovider = "gispulse_src_myprovider:register"

[tool.gispulse.plugin]
kind = "source"
protocol = ">=1.0,<2.0"          # plugin protocol the package targets
domain = "foncier"               # one SourceDomain value
jurisdiction = "FR"              # ISO country, or "*" for worldwide
display_name = "My Provider"
```

- The entry-point **name** (`myprovider`) becomes the source name used in
  `myprovider://<entry>` URIs.
- The entry-point **value** points at a `register` callable (section 5).
- `[tool.gispulse.plugin]` feeds the hub manifest. `protocol` is checked
  (warn-only) against the host `PROTOCOL_VERSION`.

## 4. The source class — `DeclarativeSource`

`source.py` subclasses `DeclarativeSource` and implements one method,
`entries()`, plus the three describing attributes.

```python
from gispulse.plugins.api import (
    AccessProtocol,
    AccessSpec,
    DeclarativeSource,
    Payload,
    SourceDomain,
    SourceEntryRef,
)


class MyProviderSource(DeclarativeSource):
    name = "myprovider"
    domain = SourceDomain.FONCIER      # base / foncier / reglementaire / reseau /
                                       # elevation / imagerie / environnement /
                                       # observation / statistique
    payload = Payload.VECTOR           # vector / raster / pointcloud / tiles / table
    jurisdiction = "FR"                # ISO country code, or "*"

    def entries(self) -> list[SourceEntryRef]:
        return [
            SourceEntryRef(
                id="parcelles",
                name="Cadastral parcels",
                access=AccessSpec(
                    protocol=AccessProtocol.WFS,
                    endpoint="https://data.geopf.fr/wfs/ows",
                    params={"typename": "CADASTRALPARCELS.PARCELLAIRE_EXPRESS:parcelle"},
                    format="application/json",
                ),
            ),
        ]
```

`fetch()`, `catalog()`, `schema()` and `revision()` all have working
defaults from `DeclarativeSource` — override only what you need.

### Choosing the `AccessProtocol`

Pick the protocol whose registered fetcher matches your endpoint. The
core fetchers shipped with GISPulse:

| `AccessProtocol` | Fetcher | `AccessSpec.params` keys |
|---|---|---|
| `WFS` | `WfsFetcher` | `typename` *(required)*, `version`, `crs`, `cql_filter` |
| `OGC_FEATURES` | `OgcFeaturesFetcher` | `collection` *(required)*, `crs` |
| `STAC` | `StacFetcher` | `collection`/`collections` *(required)*, `datetime`, `limit`, `asset` |
| `REST_API` | `RestGeoJsonFetcher` | `geom_param` *(optional)*; any other key is forwarded verbatim |
| `REST_TABLE` | `RestTableFetcher` | `query` *(dict, forwarded verbatim)*; `pagination` *(dict, see below)* |

`REST_TABLE` reads a **paginated tabular JSON** API that answers
`{"data": [...], "next": ...}` (Géorisques, BAN, RNB) — as opposed to
`REST_API` which expects a GeoJSON `FeatureCollection`. It is
**materialize-only** (`FetchMode.REFERENCE` raises): a paginated API has no
zero-copy DuckDB scan. Rows are streamed to a local JSONL file; the
`SourceResult.metadata` carries `row_count`, `page_count`, `source_url` and
a `sha256` of the output. The `pagination` block is bounded and same-origin
guarded:

```python
params={
    "query": {"code_insee": "63113", "page_size": 100},
    "pagination": {
        "data_key": "data",          # JSON key holding the row list
        "next_key": "next",          # key holding the next-page URL (abs or relative)
        "max_pages": 1000,           # hard ceiling (default)
        "max_rows": 100_000,         # optional row cap (truncates)
        "max_total_seconds": 120,    # optional wall-clock deadline
    },
}
```

A `next` link is resolved relative to the current page, must stay
**same-origin** as the declared endpoint, is SSRF-guarded per hop, and a
loop on an already-seen URL stops the walk.

A plugin of `kind = "protocol"` can register additional fetchers for
other protocols — but most source authors only declare an `AccessSpec`
against a protocol that already has one.

## 5. The `register()` hook

`__init__.py` exposes the `register` callable named by the entry-point.
It registers a source instance in the process-wide `SOURCES` registry.

```python
def register() -> None:
    """Entry-point hook for the gispulse.data_sources group."""
    from core.sources import SOURCES
    from gispulse_src_myprovider.source import MyProviderSource

    SOURCES.register(MyProviderSource())
```

Keep imports **inside** `register()` — discovery loads the entry-point
lazily, and a heavy import at module top would slow every `gispulse`
invocation.

## 6. `revision()` — feeding `source_changed` triggers

If your source should drive a [`source_changed` trigger](./TRIGGERS_GUIDE.md),
override `revision()` with a **cheap freshness probe** — never a full
`fetch()`. Return a token that changes when the upstream data changes,
or `None` when freshness is unknown (the watcher treats `None` as
"unchanged" and will not fire a false positive).

```python
def revision(self, entry_id: str) -> str | None:
    self._entry(entry_id)  # validate the id
    import httpx
    try:
        resp = httpx.head(_CAPABILITIES_URL, timeout=8.0, follow_redirects=True)
    except Exception:
        return None
    return resp.headers.get("etag") or resp.headers.get("last-modified")
```

## 7. `RegulatorySource` — zones that carry a rule

A source whose zones imply an applicable rule (PLU/PLUi, SUP, building
code) implements `RegulatorySource` and adds `ruleset(entry_id, at=...)`,
returning jurisdiction-agnostic `RuleClause` objects. This is the bridge
to the GISPulse rules engine — see `gispulse.plugins.api`.

## 8. Testing

A source plugin is unit-testable with **zero network**:

- `entries()` / `catalog()` — pure, assert directly.
- `fetch()` — register a fake `Fetcher` in a fresh `ProtocolRegistry`
  and pass it to the source constructor (`DeclarativeSource(registry=...)`),
  or monkeypatch the relevant `gispulse.adapters.*` client.
- `revision()` — monkeypatch `httpx.head`.

See `tests/unit/test_wfs_fetcher.py` for the dispatch-test pattern.

## 9. Publishing

1. Publish the wheel to PyPI (`gispulse-src-myprovider`).
2. On install, the `PluginHub` discovers it automatically — no host
   change needed. `gispulse marketplace list --kind source` shows it.
3. **Tier / trust.** An external plugin cannot grant itself a tier. The
   curated `marketplace/registry.json` in the OSS repo is the authority;
   open a PR there to have your package listed `verified`. Absent from
   the registry, a plugin runs as `community` trust — usable, but blocked
   when `GISPULSE_PLUGINS_ALLOW_UNVERIFIED=false`.

## See also

- [`PLUGIN_CONTRACT.md`](./PLUGIN_CONTRACT.md) — the full plugin contract spec
- [`TRIGGERS_GUIDE.md`](./TRIGGERS_GUIDE.md) — `source_changed` triggers
- [`plugins/gispulse-src-cadastre`](https://github.com/imagodata/gispulse/tree/main/plugins/gispulse-src-cadastre) — the reference source plugin
- [`examples/triggers/source_changed_cadastre.yaml`](https://github.com/imagodata/gispulse/blob/main/examples/triggers/source_changed_cadastre.yaml) — a runnable `source_changed` config

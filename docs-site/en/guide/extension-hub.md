# ExtensionHub

`ExtensionHub` is the **single entry point** for extending GISPulse —
whether you ship Python plugins (capabilities, routers, sinks…) or
declarative content (templates, basemaps, projections, zoning). It
landed in `1.8.0` (chantier C of the Foundations work) and is exposed
as-is in `2.0.0`; `PluginHub` is kept as a deprecated alias until
`2.1.0`.

## Why the rename?

The `PluginHub` → `ExtensionHub` rename is not cosmetic. It captures an
architectural decision: **GISPulse extensibility no longer reduces to
code plugins**. v1.8.0 introduces an explicit second regime — *data
packs* — which ship **no** Python code and load from a declarative
manifest. Keeping the name `PluginHub` would have implied everything
goes through a Python entry-point to import; `ExtensionHub` reflects
the unified inventory across both regimes.

```diff
- from core.plugin_hub import PluginHub
+ from gispulse.core.plugin_hub import ExtensionHub
```

The `PluginHub = ExtensionHub` alias is defined in the same module; no
code change is required before `2.1.0` (see
[migration 2.0](../migration-2.0#what-changed-and-what-didn-t)).

## The two regimes at a glance

| Regime        | Source                                              | What gets loaded                              |
|---------------|-----------------------------------------------------|-----------------------------------------------|
| **Code**      | Python entry-points via `importlib.metadata`        | classes (capabilities, routers, middleware…)  |
| **Data pack** | YAML/JSON manifests (bundle / PyPI / folder)        | `DataPackManifest` — fully declarative        |

Both regimes produce unified `PluginRecord`s classified by `PluginKind`
(`SOURCE` / `CAPABILITY` / `SINK` / `PROTOCOL` / `EXTENSION` /
`DATA_PACK`). `tier`/`trust` is resolved the same way for both — one
gate per activation, no parallel code path.

## Code-plugin regime

Thirteen entry-point groups are scanned. The first nine extend the host
FastAPI app (routers, middleware, providers, lifecycle, MCP); the last
four extend the ETL graph:

| Entry-point group               | Role                                                            |
|---------------------------------|-----------------------------------------------------------------|
| `gispulse.routers`              | FastAPI sub-routers (admin, billing…)                           |
| `gispulse.middleware`           | ASGI middleware                                                 |
| `gispulse.auth_provider`        | Authentication providers (`AuthProvider`)                       |
| `gispulse.billing_provider`     | Billing providers (`BillingProvider`)                           |
| `gispulse.licence_provider`     | Licence providers (`LicenceProvider`)                           |
| `gispulse.connectors`           | Session connectors (DuckDB / PostGIS / …)                       |
| `gispulse.lifecycle`            | App lifecycle hooks                                             |
| `gispulse.mcp_tools`            | Extra MCP tools (see [MCP page](./mcp))                         |
| `gispulse.mcp_resources`        | Extra MCP resources                                             |
| `gispulse.catalog_providers`    | Catalog providers (legacy ETL, promoted to `DATA_PACK` in v1.8) |
| `gispulse.data_sources`         | ETL sources (`DeclarativeSource`)                               |
| `gispulse.data_sinks`           | ETL sinks                                                       |
| `gispulse.protocols`            | Transport adapters (fetch/write)                                |

### Exposing a capability

```toml
# pyproject.toml of the plugin package
[project.entry-points."gispulse.capabilities"]
my_capability = "my_pkg.my_module:capability_factory"
```

The engine loops on the contract (`Capability`, `DeclarativeSource`,
`AuthProvider`, …) defined by `gispulse.core.plugin_contracts`.
`PROTOCOL_VERSION` (currently `"1.1"`) must be declared on the plugin
side via `requires_protocol = ">=1.0,<2.0"` — a mismatch triggers a
warning but doesn't block activation (issue #182).

### Trust and tier — commercial gating

Three trust levels derived from the marketplace registry
(`marketplace/registry.json`):

| Trust          | Typical origin                                                             |
|----------------|----------------------------------------------------------------------------|
| `first_party`  | Distributions `gispulse` / `gispulse-enterprise`                           |
| `verified`     | Third-party PyPI package listed in `marketplace/registry.json` with a known tier |
| `community`    | Third-party PyPI package unknown to the registry                           |

The `GISPULSE_PLUGINS_ALLOW_UNVERIFIED` env var (default `true`)
controls whether `community` plugins may activate; on a sensitive
deployment, set it to `false` to refuse any plugin outside the
marketplace.

The tier (`community` / `pro` / `team` / `enterprise`) is read from the
registry, cross-checked with the active licence (resolved by
`LicenceProvider.current()`), and a plugin asking for a higher tier than
the org's flips to `PluginState.LOCKED`.

## Data-pack regime

Detailed on the [Data packs](./data-packs) page. Summary:

- declarative (`DataPackManifest`), zero `import`,
- three channels: OSS bundle, `gispulse.data_packs` entry-point, folder
  pointed at by `GISPULSE_DATA_PACKS_DIR`,
- optional Ed25519 signature for `EXTERNAL` manifests (story G1a #271),
- typed contents: `template-pack` / `source-catalog` / `basemap-pack` /
  `projection-pack` / `regulatory-zoning`.

## Record lifecycle

```
discovered  →  resolve  →  gate  →  activate
                              ↘  LOCKED  (tier not satisfied)
                              ↘  FAILED  (exception on load)
                              ↘  ACTIVE  (loaded and wired)
```

Inspectable via `gispulse plugins list` (CLI) or the MCP tool
`list_plugins()` ([MCP page](./mcp)).

## Upgrading from `1.7.x`

| Before 1.8.0                          | After 1.8.0 / 2.0.0                                        |
|---------------------------------------|------------------------------------------------------------|
| `from core.plugin_hub import PluginHub` | `from gispulse.core.plugin_hub import ExtensionHub`         |
| `PluginHub.get()`                     | `ExtensionHub.get()` (`PluginHub.get()` alias still works) |
| Templates added by copy into `templates/` | `template-pack` manifest (PyPI or folder)              |
| Catalog providers via `gispulse.catalog_providers` | still scanned, promoted to `DataPack` in the inventory |

See also the [migration 2.0](../migration-2.0).

## Code references

- [`gispulse.core.plugin_hub.ExtensionHub`](https://github.com/imagodata/gispulse/blob/main/src/gispulse/core/plugin_hub.py)
- [`gispulse.core.plugin_model`](https://github.com/imagodata/gispulse/blob/main/src/gispulse/core/plugin_model.py) — `PluginKind`, `Tier`, `Trust`, `PluginState` enums
- [`gispulse.core.plugin_contracts`](https://github.com/imagodata/gispulse/blob/main/src/gispulse/core/plugin_contracts.py) — `Capability`, `AuthProvider`, …

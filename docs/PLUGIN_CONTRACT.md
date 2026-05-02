# GISPulse Plugin Contract

This document describes the current external plugin surface used by
`PluginHub`. It is intentionally factual: the contract below is usable today,
but the typed SDK host context is not yet stable.

This work is tracked by
[imagodata/gispulse#68](https://github.com/imagodata/gispulse/issues/68).
Commits and PRs that advance this surface should use `Refs #68` until the full
acceptance criteria are covered.

## SDK Scope

`sdk/gispulse_sdk` is the existing REST client SDK. It is for external clients
that call a running GISPulse HTTP API.

Issue #68 is about a different surface: the future in-process plugin-author API
used by packages installed into the host process. Do not put this authoring
surface under `gispulse_sdk`; keep it separate from the REST client SDK.

## Entry Point Groups

GISPulse discovers plugins through Python package entry points.

| Group | Contract | Current status |
| --- | --- | --- |
| `gispulse.capabilities` | Callable `register()` that imports capability classes using `@register` | Legacy supported / transitional surface |
| `gispulse.routers` | `RouterFactory.create(app)` returning a FastAPI `APIRouter` or `None` | Current host surface |
| `gispulse.middleware` | `MiddlewareFactory.install(app)` | Current host surface |
| `gispulse.auth_provider` | `AuthProvider` protocol | Current host surface |
| `gispulse.billing_provider` | `BillingProvider` protocol | Current host surface |
| `gispulse.licence_provider` | `LicenceProvider` protocol | Current host surface |
| `gispulse.connectors` | `Connector` protocol | Current host surface |

Capability plugins are discovered by the capability registry. Host plugins for
routers, middleware, auth, billing, licence and connectors are discovered by
`core.plugin_hub.PluginHub` and mounted by the HTTP app in full mode.

`gispulse.mcp_tools` has been observed in the Permis Check integration, but
this first host-runtime slice does not prove or stabilize it. Do not treat it as
equally wired as `gispulse.routers` unless core host discovery is added and
tested.

## Router Contract

An external router plugin declares:

```toml
[project.entry-points."gispulse.routers"]
example = "gispulse_cap_example.routers:ExampleRouterFactory"
```

The factory object may be an instance or a class. If it is a class, `PluginHub`
instantiates it with no arguments.

```python
from fastapi import APIRouter


class ExampleRouterFactory:
    name = "example"

    def create(self, app):
        router = APIRouter(prefix="/plugins/example")

        @router.get("/health")
        def health():
            return {"plugin": "example", "status": "ok"}

        return router
```

`RouterFactory.create(app)` receives the FastAPI application object. Returning
`None` skips mounting without failing host startup. Raising an exception is
caught by the host and logged as a plugin mount failure.

`app` is the current contract. New plugins should not treat `app.state` as a
stable SDK. Use `app.state` only as a temporary escape hatch when no documented
host surface exists, and keep that access narrow so it can be replaced by the
typed context planned in #68.

## Capability Contract

Capability plugins declare:

```toml
[project.entry-points."gispulse.capabilities"]
example = "gispulse_cap_example:register"
```

The `register()` function imports modules that use the capability registry
decorator:

```python
from gispulse.plugins.api import Capability, register_capability


@register_capability
class ExampleCapability(Capability):
    name = "example"
    description = "Example capability."

    def execute(self, gdf, **params):
        return gdf
```

Use explicit keyword parameters when a capability accepts configuration. Avoid
the old `execute(gdf, config: dict)` shape; orchestration dispatch now validates
keyword parameters through the current `Capability.execute(gdf, **params)`
contract.

`gispulse.capabilities` is supported for existing plugins, but direct imports
from `capabilities.*` are transitional rather than the final new SDK shape. New
plugins should import capability primitives from `gispulse.plugins.api`.

## Plugin Author Imports

New plugins should prefer the curated authoring namespace over internal module
paths:

```python
from gispulse.plugins.api import (
    CatalogEntry,
    Capability,
    OGCSourceConfig,
    PipelineExecutor,
    PipelineSpec,
    StepSpec,
    fetch_wfs,
    get_catalog_entry,
    get_flux_entry,
    is_angular,
    register_capability,
    suggest_metric_crs,
)
```

The namespace is intentionally thin for now. It re-exports the runtime
primitives already exercised by real plugins while keeping the supported import
path stable for plugin authors. Direct imports from `core.*`, `catalog.*`,
`orchestration.*`, `capabilities.*`, or `gispulse.adapters.*` should be treated
as legacy/transitional in new plugin code.

Focused submodules are also available when plugins need narrower imports:

- `gispulse.plugins.pipeline`: `PipelineSpec`, `StepSpec`, `PipelineExecutor`
- `gispulse.plugins.sources`: catalog lookup, flux entry lookup, OGC source
  config and WFS helpers
- `gispulse.plugins.spatial`: CRS helpers used by spatial capabilities

## Additive Host Context

`RouterFactory.create(app)` remains the legacy-compatible contract. This slice
also introduces a small `PluginHostContext` for new router factories. A factory
can opt into it by naming the first `create()` parameter `ctx`, `context`,
`host_context` or `plugin_context`, or by annotating it as `PluginHostContext`.

```python
from fastapi import APIRouter
from gispulse.plugins.api import PluginHostContext


class ExampleRouterFactory:
    name = "example"

    def create(self, ctx: PluginHostContext):
        router = APIRouter(prefix="/plugins/example")
        ctx.logger.info("example_plugin_mounting")
        return router
```

The first context is deliberately small: `app`, `settings`, `logger` and
`plugin_hub`. Catalog/source, spatial and pipeline primitives are exposed
through `gispulse.plugins.api`; auth/tenant context and cache/storage remain
future tested slices for `#68`.

## Known Limits

The current host plugin API passes the raw FastAPI `app` to router and
middleware factories. This keeps existing plugins simple, but it also exposes
internal application details through `app.state`.

The current `PluginHostContext` is additive, small and not yet the full stable SDK.
It intentionally exposes only mount-time host objects that are already needed by
real plugins.

Issue #68 should reduce the need for product plugins to import directly from
internal modules such as `core.*`, `catalog.*`, `orchestration.*`,
`capabilities.*`, or `gispulse.adapters.*`. Those imports work today, but they
create an accidental public API and make host refactors harder.

## Template

See `examples/plugin-template` for a minimal package that declares both a
capability entry point and a host router entry point.

## PR Plan

The issue is the durable target. The intended PR series is:

1. `#72`: add the additive `PluginHostContext`, preserve `create(app)`, and
   document the current host contract.
2. Add the curated plugin-author imports in `gispulse.plugins.api` and focused
   submodules for capabilities, pipeline, sources and spatial helpers.
3. Migrate `gispulse-permis` to the new imports for capabilities and spatial
   pipelines first, proving that product plugins can avoid `capabilities.*`,
   `core.pipeline` and `orchestration.*` imports.
4. Follow with source/client and MCP-compatible plugin integration once the
   underlying host pieces exist on the upstream base, including any API Carto or
   `gispulse.mcp_tools` discovery work needed to make the contract real.

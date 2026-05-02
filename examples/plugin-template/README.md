# GISPulse Plugin Template

This is a minimal template for creating a GISPulse external plugin on the
current host contract.

## Structure

```
gispulse-cap-example/
  pyproject.toml                        # Package metadata + entry-point
  gispulse_cap_example/
    __init__.py                         # register() function (entry-point target)
    capabilities.py                     # Capability classes with register_capability decorator
    routers.py                          # RouterFactory for host API extension
```

## How it works

1. The plugin declares an entry-point in `pyproject.toml`:
   ```toml
   [project.entry-points."gispulse.capabilities"]
   example = "gispulse_cap_example:register"

   [project.entry-points."gispulse.routers"]
   example = "gispulse_cap_example.routers:ExampleRouterFactory"
   ```

2. When GISPulse starts, it scans the `gispulse.capabilities` entry-point group
   and calls each `register()` function.

3. The `register()` function imports the capabilities module, which triggers
   the `register_capability` decorator to add capabilities to the global
   registry.

4. In full HTTP mode, GISPulse scans `gispulse.routers`, instantiates the
   factory, calls `RouterFactory.create(app)`, and mounts the returned
   `APIRouter`.

The example capability uses the current `execute(gdf, **params)` shape. Do not
use the old `execute(gdf, config: dict)` signature for new capabilities.

## Creating your own plugin

1. Copy this template
2. Rename `gispulse_cap_example` to `gispulse_cap_<yourname>`
3. Update `pyproject.toml` (name, entry-point key, module path)
4. Implement your capabilities by importing `Capability` and `register_capability`
   from `gispulse.plugins.api`
5. Install in development mode: `pip install -e .`
6. Verify: `gispulse capabilities` should list your new capability
7. Start the HTTP host and call `/plugins/example/health`

The legacy router API still accepts `RouterFactory.create(app)`. New router
factories can opt into the additive `PluginHostContext`; see
`docs/PLUGIN_CONTRACT.md`. Keep any direct `app.state` access narrow and
temporary.

## Naming convention

- Package name: `gispulse-cap-<name>` (e.g. `gispulse-cap-ftth`)
- Module name: `gispulse_cap_<name>` (e.g. `gispulse_cap_ftth`)
- Entry-point key: `<name>` (e.g. `ftth`)

## Publishing

```bash
pip install build twine
python -m build
twine upload dist/*
```

Users can then install with:
```bash
gispulse marketplace install <name>
```

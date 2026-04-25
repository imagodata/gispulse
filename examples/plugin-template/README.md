# GISPulse Plugin Template

This is a minimal template for creating a GISPulse capability plugin.

## Structure

```
gispulse-cap-example/
  pyproject.toml                        # Package metadata + entry-point
  gispulse_cap_example/
    __init__.py                         # register() function (entry-point target)
    capabilities.py                     # Capability classes with @register decorator
```

## How it works

1. The plugin declares an entry-point in `pyproject.toml`:
   ```toml
   [project.entry-points."gispulse.capabilities"]
   example = "gispulse_cap_example:register"
   ```

2. When GISPulse starts, it scans the `gispulse.capabilities` entry-point group
   and calls each `register()` function.

3. The `register()` function imports the capabilities module, which triggers
   the `@register` decorator to add capabilities to the global registry.

## Creating your own plugin

1. Copy this template
2. Rename `gispulse_cap_example` to `gispulse_cap_<yourname>`
3. Update `pyproject.toml` (name, entry-point key, module path)
4. Implement your capabilities by subclassing `Capability` and using `@register`
5. Install in development mode: `pip install -e .`
6. Verify: `gispulse capabilities` should list your new capability

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

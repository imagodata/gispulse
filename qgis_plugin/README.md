# GISPulse — QGIS plugin

Companion plugin for the [`gispulse`](https://pypi.org/project/gispulse/) CLI.
Lets you attach trigger rules to QGIS layers and run them on save.

## Status

`v1.4.0` is the first scaffold release. Full UI lands across child issues
[v1.4-2](https://github.com/imagodata/gispulse-enterprise/issues/468) →
[v1.4-8](https://github.com/imagodata/gispulse-enterprise/issues/474).

## Requirements

- QGIS ≥ 3.28
- `gispulse` CLI on `PATH` — `pipx install gispulse` (recommended) or `pip install gispulse`

## Build a ZIP locally

```bash
make plugin-zip            # → dist/gispulse-qgis-plugin-<version>.zip
```

Install the ZIP from QGIS → Extensions → Install from ZIP.

## License

AGPL-3.0-or-later, same as the parent project.

# GISPulse — QGIS plugin

Companion plugin for the [`gispulse`](https://pypi.org/project/gispulse/)
Python CLI. Pick a vector layer, point it at a `rules.yml`, click Run —
the plugin shells out to your local gispulse install and streams its
logs into a dock widget, then reloads the layer with a per-feature
change summary.

## Status

First public release. **Pending approval** on the official
[QGIS Plugin Repository](https://plugins.qgis.org/) — install from the
ZIP attached to each [GitHub Release](https://github.com/imagodata/gispulse/releases)
in the meantime.

## User documentation

- [Install guide (FR)](https://gispulse.dev/plugins/qgis-install) ·
  [(EN)](https://gispulse.dev/en/plugins/qgis-install)
- [Troubleshooting (FR)](https://gispulse.dev/plugins/qgis-troubleshooting) ·
  [(EN)](https://gispulse.dev/en/plugins/qgis-troubleshooting)

## Requirements

- QGIS ≥ 3.28
- `gispulse` CLI on a `PATH` visible to QGIS — `pipx install gispulse`
  (recommended) or `pip install gispulse`

## Build a ZIP locally

```bash
make plugin-zip            # → dist/gispulse-qgis-plugin-<version>.zip
```

Install the ZIP from QGIS → Extensions → Install from ZIP.

## Submitting to plugins.qgis.org

See [PUBLISHING.md](PUBLISHING.md) for the maintainer workflow.

## License

AGPL-3.0-or-later, same as the parent project.

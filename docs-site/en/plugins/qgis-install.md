---
title: Install the QGIS plugin
description: Step-by-step install for the GISPulse QGIS plugin (CLI bridge) on Windows OSGeo4W, Windows Standalone, macOS, and Linux.
---

# Install the QGIS plugin

The GISPulse QGIS plugin is a *bridge* between QGIS and the `gispulse`
CLI shipped on PyPI. It **does not contain the engine** — it shells
out to your local `gispulse` install to run triggers against your
GeoPackages.

## Prerequisites

| Component | Minimum version | Why |
|---|---|---|
| QGIS | 3.28 (LTR) | plugin manager API + `QgsVectorFileWriter` v3 |
| Python | 3.10+ | required by the gispulse CLI |
| `gispulse` (CLI) | ≥ 1.3.0 | the `gispulse triggers run` sub-command |

## Step 1 — install the `gispulse` CLI

::: tabs

== Windows · OSGeo4W

QGIS installed via **OSGeo4W** ships its own embedded Python. Open
the *OSGeo4W Shell* from the Start menu, then:

```bat
pip install gispulse
gispulse --version
```

> Use **OSGeo4W Shell**, not a regular PowerShell. Otherwise `pip`
> targets a different Python than the one QGIS uses.

== Windows · Standalone

The Standalone installer doesn't expose a dedicated shell. Install the
CLI at user level from any Python 3.10+ terminal:

```bat
py -m pip install --user gispulse
py -m pip show gispulse
```

Make sure `gispulse` is on the `PATH` (`%APPDATA%\Python\Python3xx\Scripts`).

== macOS · Homebrew

```bash
brew install pipx
pipx install gispulse
gispulse --version
```

`pipx` isolates the CLI in its own venv — preferable to `pip3 install`
which would pollute the system Python.

== Linux

```bash
pipx install gispulse
# or, without pipx:
pip install --user gispulse
gispulse --version
```

:::

## Step 2 — install the plugin

Until the [plugins.qgis.org](https://plugins.qgis.org) submission is
finalised ([#v1.4-8](https://github.com/imagodata/gispulse-enterprise/issues/474)),
install the ZIP **from the GitHub Release**:

1. Download `gispulse-qgis-plugin-<version>.zip` from the
   [Releases page](https://github.com/imagodata/gispulse/releases)
2. In QGIS: **Plugins → Manage and Install Plugins → Install from ZIP**
3. Pick the ZIP → **Install plugin**

## Step 3 — verify the install

1. In QGIS: **Plugins → GISPulse → Check gispulse install…**
2. You should see: *"GISPulse `<version>` found at
   `/path/to/gispulse`"*

If you instead see *"GISPulse CLI was not found on this system"*: the
plugin loaded correctly but the CLI is missing or off-PATH. Head to
the [**Troubleshooting** guide](/en/plugins/qgis-troubleshooting).

## Step 4 — open the panel

1. **Plugins → GISPulse → Show panel**
2. The GISPulse dock opens on the right
3. Pick a vector layer, a `rules.yml` file, click **Run trigger** —
   logs stream live and the layer reloads with a change summary on
   completion

## Next

- [YAML triggers guide](/en/guide/triggers) — write your own rules
- [Plugin troubleshooting](/en/plugins/qgis-troubleshooting) — fixes
  for the most common errors
- [Open a bug](https://github.com/imagodata/gispulse/issues) — attach
  the log produced under `<project>/.gispulse/runs/`

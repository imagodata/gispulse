---
title: Troubleshooting — QGIS plugin
description: Diagnose "gispulse not found", permission errors, missing modules, plugin crashing on QGIS startup.
---

# Troubleshooting — QGIS plugin

This page lists the most common errors when starting the GISPulse
plugin, in decreasing order of frequency in the tracker.

## "gispulse not found" — 3-question triage

The number-one error. The plugin loaded, but it can't find the CLI to
call. Walk through the questions below **in order**:

### 1. Is the CLI installed at all?

Open the **same terminal** where you ran `pip install gispulse` in
step 1 of the install guide:

```bash
gispulse --version
```

- ✅ "`gispulse, version 1.x.x`" → go to question 2
- ❌ "*command not found*" → revisit
  [Step 1 of the install guide](/en/plugins/qgis-install#step-1-install-the-gispulse-cli)

### 2. Is it on the `PATH` QGIS sees?

QGIS on OSGeo4W and Standalone runs an embedded Python which **doesn't
share your shell `PATH`**. In QGIS, open the Python Console
(Plugins → Python Console) and type:

```python
import shutil; shutil.which("gispulse")
```

- ✅ Returns a path → CLI found. Click "Test again" in the plugin
  error dialog.
- ❌ Returns `None` → the CLI is installed somewhere QGIS doesn't
  look. See question 3.

### 3. Which Python is the plugin using?

```python
import sys; sys.executable
```

Compare with where `pip install gispulse` actually wrote the CLI:

```bash
pip show -f gispulse | grep -E "(Location|gispulse$)"
```

If the two paths differ (e.g. `pip` targeted system Python but QGIS
ships OSGeo4W Python), **reinstall with the right Python**:

::: tabs

== Windows · OSGeo4W

Make sure to open **OSGeo4W Shell** (not PowerShell) before `pip install`.

== Windows · Standalone

```bat
"C:\Program Files\QGIS 3.28\bin\python-qgis.bat" -m pip install --user gispulse
```

== macOS · Homebrew

`pipx install gispulse` is enough — the plugin probes `~/.local/bin`
through the `HOME` env var.

== Linux

Add `~/.local/bin` to the `PATH` your desktop manager launches (not
just `.bashrc`, which doesn't load for GUI apps).

:::

## "Permission denied" (Windows)

Happens when `pip install gispulse` tries to write to a system folder
(e.g. `C:\Program Files\…`).

**Fix**: use `pip install --user gispulse` or run the *OSGeo4W Shell*
as Administrator (right-click → *Run as administrator*).

## "ModuleNotFoundError: No module named 'gdal'"

The gispulse CLI needs GDAL bindings for some operations. On
Linux/macOS:

```bash
pip install --upgrade "gispulse[gdal]"
```

On Windows OSGeo4W, GDAL is already shipped by QGIS and reused by the
CLI — if the error persists, check you opened the right shell (see
"gispulse not found · question 3").

## Plugin crashes at QGIS startup

1. Open **View → Panels → Log Messages** in QGIS
2. **Plugins** tab — the Python traceback is there
3. Copy it into a [GitHub issue](https://github.com/imagodata/gispulse/issues/new)
   along with: QGIS version, OS, `gispulse --version`

## "Layer is being edited" — Save / Discard / Cancel

The plugin refuses to fire a trigger on a layer with unsaved edits.
The modal offers three choices:

- **Save** — commit the in-progress edits, then run
- **Discard** — roll back the edits, then run
- **Cancel** — don't run (resolve manually)

## "Trigger succeeded but reload failed"

The trigger ran fine but QGIS couldn't refresh the layer (e.g. the
temp GeoPackage got removed). The *Restore previous version* button
stays active for 5 minutes to recover the pre-run snapshot from
`<project>/.gispulse/backups/`.

## Where are the logs?

Each run writes a full log to:

```
<project-folder>/.gispulse/runs/<UTC-timestamp>.log
```

The first line contains the exact shell command — copy it if you want
to reproduce the run outside the plugin.

## Still stuck?

- Check the [open issues](https://github.com/imagodata/gispulse/issues)
- If yours isn't there, open one with: QGIS version, OS, `gispulse
  --version` output, and the contents of `.gispulse/runs/<timestamp>.log`

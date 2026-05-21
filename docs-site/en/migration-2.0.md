---
title: Migrating to GISPulse 2.0
description: Migration guide from gispulse 1.x to gispulse 2.0 — what changed, what is shimmed, and when shims go away.
---

# Migration guide — `gispulse` 1.x → 2.0

`gispulse 2.0.0` is the first major release since `1.6.2`. The version
bump packages three threads that accumulated on `main` without ever
hitting PyPI (`Foundations` v1.8.0, `worldwide aggregator` v1.9.0, and
the new data-pack rails); see the [Changelog](./changelog) for the
full list. This document focuses on **what callers need to know to
upgrade**.

## TL;DR — almost nothing

The two real API changes (`gispulse.*` package layout and
`PluginHub → ExtensionHub` rename) are both **shimmed**, so a working
1.x project will keep working under 2.0 without any code change. You
will see one `DeprecationWarning` per stale import root the first time
the process imports from it.

Both shims are scheduled for removal in **2.1.0**.

## What changed (and what didn't)

| Change | Severity | Shim until 2.1.0 | Action |
|---|---|---|---|
| Imports `core.*`, `capabilities.*`, `rules.*`, `orchestration.*`, `persistence.*`, `catalog.*` moved under `gispulse.*` | **soft** | meta-path redirect + one `DeprecationWarning` | optional — rename imports at your leisure |
| `core.plugin_hub.PluginHub` renamed to `ExtensionHub` | **soft** | `PluginHub = ExtensionHub` alias | optional — rename at your leisure |
| Top-level `viewer/` package removed | cosmetic | n/a (the package was empty in 1.6.2) | none |
| `PROTOCOL_VERSION` bumped `"1.0"` → `"1.1"` | non-breaking | n/a | none — `_check_protocol_version` is warn-only since #182 |
| `gispulse.core.plugin_contracts` symbols (`Tier`, `PluginManifest`, …) | non-breaking | n/a | none — these were **never** in `plugin_contracts` in 1.6.2, they live in `plugin_model` |

The CLI, the HTTP routers, `triggers.yaml`, and every published
dependency bound are **unchanged** between 1.6.2 and 2.0.0 (verified
against the published wheel).

## Renaming imports — when you're ready

```diff
- from core.plugin_hub import PluginHub
+ from gispulse.core.plugin_hub import ExtensionHub
```

```diff
- from capabilities.vector import calculate
+ from gispulse.capabilities.vector import calculate
```

```diff
- from rules.evaluator import evaluate
+ from gispulse.rules.evaluator import evaluate
```

Any qualified import under the legacy top-level packages (`core`,
`capabilities`, `rules`, `orchestration`, `persistence`, `catalog`) is
covered by the meta-path shim in `gispulse/_compat.py`. The shim emits
`DeprecationWarning` once per root the first time the process touches
it, so you can `grep` your test logs for `_compat` to find what's still
on the old name.

## Data-pack ecosystem — new and opt-in

`gispulse 2.0.0` opens the door to third-party data packs distributed
via PyPI. Three pieces matter for integrators:

### `gispulse.data_packs` entry-point

Third-party packages register their manifests through an entry-point
group:

```toml
# pyproject.toml of a data-pack package
[project.entry-points."gispulse.data_packs"]
my_pack = "my_pack._gispulse_entry:manifest_paths"
```

```python
# my_pack/_gispulse_entry.py
from importlib.resources import files


def manifest_paths():
    return [files("my_pack") / "manifests" / "zoning.yml"]
```

The callable may return either a single path-like or an iterable of
path-likes (`str` is **not** iterated char-by-char). One bad pack never
locks out the others.

### Manifest signature (Ed25519)

A pack manifest may carry a `signature` field — the base64-URL Ed25519
signature of the canonical JSON of the manifest **without** that field.
Verification key configuration:

```bash
# Base64 DER of the Ed25519 public key.
export GISPULSE_DATA_PACK_PUBLIC_KEY="MCowBQYD..."
# Optional strict mode — refuse unsigned EXTERNAL manifests.
export GISPULSE_DATA_PACK_REQUIRE_SIGNATURE=true
```

The bundled OSS manifests (`Origin.INTERNAL`) are exempt — the OSS
tree is the source of truth. By default unsigned EXTERNAL manifests
are admitted (rollout-friendly).

### `regulatory-zoning` content type

A new value in `DATA_PACK_CONTENTS` (`"regulatory-zoning"`) describes
urban-planning zoning sources. See `RegulatoryZoningEntry` for the
declarative shape and the country-by-country entries shipped by
`gispulse-data-regulatory`.

## Numbering note — why `2.0.0` and not `1.10.0`

By strict semver the changes above could fit in a `1.10.0`. The
`2.0.0` bump is a **product-level milestone** for: the consolidated
package layout, the worldwide aggregator, and the data-pack rails.
The migration cost stays minimal thanks to the shims listed above.

The shims (`_compat.py` meta-path redirect and the `PluginHub` alias)
will be removed in **2.1.0**. Renaming your imports any time before
then is safe.

## Reporting issues

Any unexpected import error, missing symbol, or behaviour change after
upgrading — please open an issue against
[`imagodata/gispulse`](https://github.com/imagodata/gispulse/issues)
with the import path you used in 1.x and the version of `gispulse`
you're now running.

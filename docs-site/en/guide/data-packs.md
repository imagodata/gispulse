# Data packs

**Data packs** are the second regime of the [ExtensionHub](./extension-hub),
introduced in `gispulse 2.0.0` (chantier C of the Foundations v1.8.0 work).
A data pack ships **data only** — a declarative YAML/JSON manifest — and
never any Python code. It is discovered without importing anything, so its
trust is trivially `verified`, and the tier filter (`community` / `pro` /
`team` / `enterprise`) is fully data-driven.

This page covers:

- the two **discovery channels** (bundled OSS + PyPI entry-point),
- the `DataPackManifest` format,
- tier gating and **Ed25519** signatures for external manifests,
- the supported content types (`template-pack`, `source-catalog`,
  `basemap-pack`, `projection-pack`, `regulatory-zoning`),
- the reference pack `gispulse-data-regulatory` (FR + NL + DK).

> If you want to ship **code** (a new capability, a router, an auth
> connector…), see the *code* regime of the
> [ExtensionHub](./extension-hub).

## Why a data regime?

Before 2.0.0, extending the catalog of templates / projections / basemaps
required a Python package with an entry-point — therefore a code review,
a PyPI release cycle, and a risk of arbitrary code execution. A large
share of contributions are in fact **purely declarative**: a zoning
catalog, a list of basemaps, a metier template pack. The data-pack
regime makes those contributions safe and trivial:

- loaded **without `import`** — no code execution risk,
- uniform discovery via `ExtensionHub` (bundle OSS + entry-point + folder),
- tier filtering handled by the OSS engine,
- premium gating via **Ed25519** signature without sharing anything other
  than a public key.

## Discovery channels

Three channels, merged into the single inventory of `ExtensionHub`:

| Origin             | Channel                                                                       | Trust         |
|--------------------|-------------------------------------------------------------------------------|---------------|
| OSS bundle         | `templates/manifest.yml` from the `gispulse` repo                             | `first_party` |
| Third-party PyPI   | `gispulse.data_packs` entry-point (story T5, [#269](https://github.com/imagodata/gispulse/issues/269)) | `verified` if listed in `marketplace/registry.json`, else `community` |
| User folder        | `GISPULSE_DATA_PACKS_DIR` env var pointing to a folder of `*.yml` / `*.yaml` / `*.json` | `community`   |

Manifests with `INTERNAL` origin (OSS bundle) skip signature verification;
`EXTERNAL` manifests (PyPI + user folder) go through the signature
policy below.

### Registering a PyPI pack

```toml
# pyproject.toml of a third-party data-pack
[project.entry-points."gispulse.data_packs"]
my_pack = "my_pack._gispulse_entry:manifest_paths"
```

```python
# my_pack/_gispulse_entry.py
from importlib.resources import files


def manifest_paths():
    return [files("my_pack") / "manifests" / "zoning.yml"]
```

The callable may return either a single path-like or an iterable — `str`
is **not** iterated char-by-char. A failing pack never locks the others
out: the error is logged and the next manifest keeps loading.

## `DataPackManifest` format

Defined in
[`gispulse.core.plugin_model.DataPackManifest`](https://github.com/imagodata/gispulse/blob/main/src/gispulse/core/plugin_model.py).
Fields:

| Field          | Type           | Required | Notes                                                                                            |
|----------------|----------------|----------|--------------------------------------------------------------------------------------------------|
| `name`         | str            | **yes**  | non-empty pack identifier                                                                        |
| `content`      | str            | **yes**  | one of `template-pack`, `source-catalog`, `basemap-pack`, `projection-pack`, `regulatory-zoning` |
| `version`      | str            | no       | defaults to `"0.0.0"`; free format                                                               |
| `display_name` | str            | no       | defaults to `name`; portal gallery label                                                         |
| `description`  | str            | no       | long description                                                                                 |
| `tier`         | `Tier`         | no       | defaults to `community`; one of `community`, `pro`, `team`, `enterprise`                          |
| `entries`      | `list[dict]`   | no       | content-specific payloads — see content table below                                              |
| `metadata`     | `dict`         | no       | free-form labels (jurisdiction, license, provider, …)                                            |
| `signature`    | `str` \| None  | no       | Ed25519 base64-url signature of the manifest without that field (see [§ Signature](#ed25519-signature)) |

A manifest with no `name` or with an unknown `content` is **rejected**
(`ValueError`) before any registration in `ExtensionHub`.

### Supported contents

| `content`           | Description                                                                                  |
|---------------------|----------------------------------------------------------------------------------------------|
| `template-pack`     | Pipeline presets exposed via `gispulse.templates` and the portal gallery.                    |
| `source-catalog`    | ETL catalog entries (`SourceEntryRef`) added to the worldwide aggregator.                    |
| `basemap-pack`      | Additional basemaps for the portal `DualMapView`.                                            |
| `projection-pack`   | Extra PROJ definitions consumable on the DuckDB engine side.                                 |
| `regulatory-zoning` | Per-country zoning library — `RegulatoryZoningEntry` wiring (story T2 [#268](https://github.com/imagodata/gispulse/issues/268), pack `gispulse-data-regulatory`). |

## Minimal example (`template-pack`)

```yaml
# my_pack/manifests/templates.yml
name: my-isochrone-templates
display_name: My isochrone catalog
content: template-pack
version: 1.0.0
tier: community
description: Three isochrone presets (1, 3, 5 min) over the OSM network.
entries:
  - id: isochrone-1min
    label: Isochrone 1 min
    pipeline:
      - capability: isochrone
        params: { minutes: 1 }
  - id: isochrone-3min
    label: Isochrone 3 min
    pipeline:
      - capability: isochrone
        params: { minutes: 3 }
metadata:
  jurisdiction: FR
  license: CC-BY-4.0
```

## Ed25519 signature

Story G1a ([#271](https://github.com/imagodata/gispulse/issues/271)).
`EXTERNAL` manifests may carry a `signature` field — the Ed25519 signature
(base64-URL, no padding) of the canonical JSON of the manifest **minus
that very field** (otherwise the signature would have to commit to
itself). Canonicalisation reuses
`gispulse.core.licence_format.canonicalise` (same bytes as the licence
payload: sorted keys, compact JSON, UTF-8).

### Engine-side configuration

```bash
# Ed25519 public key, base64-encoded DER.
export GISPULSE_DATA_PACK_PUBLIC_KEY="MCowBQYDK2VwAyEA..."

# Strict mode — refuse any unsigned EXTERNAL manifest.
# Recommended in CI as soon as a deploy ships gated content.
export GISPULSE_DATA_PACK_REQUIRE_SIGNATURE=true
```

By default `GISPULSE_DATA_PACK_REQUIRE_SIGNATURE` is `false`: unsigned
EXTERNAL manifests (community) still load. OSS bundle manifests
(`Origin.INTERNAL`) skip verification entirely — the OSS tree is the
source of truth.

### Generating a signature (pack publisher side)

The helper `sign_manifest_dict` exists in
[`gispulse.core.data_pack_signature`](https://github.com/imagodata/gispulse/blob/main/src/gispulse/core/data_pack_signature.py)
for end-to-end tests. **In production**, the private key lives in the
pack's release pipeline (e.g. `gispulse-data-regulatory`) and the OSS
verifier only verifies.

```python
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from gispulse.core.data_pack_signature import sign_manifest_dict

manifest = {
    "name": "my-pack",
    "content": "template-pack",
    "version": "1.0.0",
    "tier": "pro",
    "entries": [...],
}
private_key = Ed25519PrivateKey.generate()  # or loaded from a secret manager
manifest["signature"] = sign_manifest_dict(manifest, private_key)
```

> The reference GISPulse private key lives in `gispulse-enterprise`. Any
> pack targeting the official marketplace must be signed with that key
> through the release pipeline controlled by Imagodata.

## Reference pack — `gispulse-data-regulatory`

The first compliant PyPI pack is `gispulse-data-regulatory`: the
per-country urban-zoning library (FR + NL + DK for the inaugural
release, story T2
[#268](https://github.com/imagodata/gispulse/issues/268)).

- **Content**: `regulatory-zoning` — `RegulatoryZoningEntry` entries
  wired against `gispulse-src-gpu` (FR) and the national WFS endpoints
  for NL/DK.
- **Tier**: `pro` — gated through Ed25519 signature.
- **Cadence**: tracks upstream millésimes (annual NL/DK, continuous FR
  via PLU).

```bash
pip install gispulse-data-regulatory
```

Once installed and the public key configured, the engine auto-registers
the entries into the worldwide aggregator; rules can reference the
jurisdiction and country by label without hardcoding any WFS endpoint.

## See also

- [ExtensionHub](./extension-hub) — overview of the two regimes.
- [Worldwide aggregator](./worldwide-aggregator) — where `source-catalog`
  entries land.
- [Migration 2.0](../migration-2.0) — “Data-pack ecosystem” section.

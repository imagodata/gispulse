# Publishing the GISPulse QGIS plugin

Maintainer workflow for the official
[QGIS Plugin Repository](https://plugins.qgis.org/). This file is
excluded from the user-facing ZIP by `scripts/build_qgis_plugin_zip.py`.

## What the repository validates

Checks performed by `qgis-app/plugins/validator.py` on every upload:

- ZIP ≤ 25 MB with exactly one top-level folder (`gispulse/`), no `.pyc`,
  no `__pycache__`, `.git` or `__MACOSX` entries.
- `gispulse/metadata.txt` with the required keys `name`, `description`,
  `version`, `qgisMinimumVersion`, `author`, `email`, `about`, `tracker`,
  `repository`.
- **`gispulse/LICENSE` present** — mandatory since 3 June 2024
  (QGIS-Enhancement-Proposals #279). `qgis_plugin/LICENSE` is a copy of the
  repository's AGPL-3.0 licence and `tests/unit/test_qgis_plugin_scaffold.py`
  asserts it lands in the ZIP. ZIPs built before this file existed were
  rejected by the upload form.
- `homepage`, `tracker` and `repository` URLs must resolve: the validator
  follows them. `homepage` points to the documentation site
  (`https://imagodata.github.io/gispulse/`) for as long as `gispulse.dev`
  is not served.
- `experimental=False`. Experimental plugins are hidden by default in the
  QGIS plugin manager ("Show also experimental plugins" is off), which is
  why the 1.5–2.5.1 metadata would have made the plugin invisible to most
  users even once listed.

## Prerequisites

1. An OSGeo user id
   (<https://www.osgeo.org/community/getting-started-osgeo/osgeo_userid/>).
   The first upload creates the plugin entry under that account; ask the
   plugin approvers to add co-maintainers afterwards.
2. A tagged release `vX.Y.Z` — the
   [release workflow](../.github/workflows/release.yml) builds
   `dist/gispulse-qgis-plugin-X.Y.Z.zip` and attaches it to the GitHub
   Release. Never upload a dirty local build.

## First submission (manual, once)

1. `make plugin-zip-check && make plugin-zip`.
2. Install the ZIP in a fresh QGIS profile (**Extensions → Install from
   ZIP**), then **Extensions → GISPulse → Check gispulse install…**.
3. Sign in at <https://plugins.qgis.org/> and upload the ZIP at
   <https://plugins.qgis.org/plugins/add/>. The form prints validator
   errors verbatim, which is why the first upload is done by hand.
4. The plugin is reviewed by the QGIS plugin approvers (days to a few
   weeks). Until approval it is installable only through its direct page;
   after approval it appears in the default catalogue.
5. Reviewer feedback: edit `metadata.txt` (and whatever it points at), bump
   `version=` to the next patch and re-upload. **Never** edit a published
   version in place — QGIS caches by version number.

## Subsequent releases (automated)

The `publish-qgis-plugin` job of `release.yml` uploads the freshly built ZIP
through the repository's XML-RPC endpoint
(`scripts/upload_qgis_plugin.py`, the same `plugin.upload` call as the
Plugin Builder's `plugin_upload.py`) whenever the repository secrets
`OSGEO_USERNAME` and `OSGEO_PASSWORD` are set. Without them the job is
skipped, not failed.

Manual fallback: <https://plugins.qgis.org/plugins/GISPulse/> → **Manage
versions → Upload new version**.

Per-release checklist for the plugin:

1. Bump `version=` in `qgis_plugin/metadata.txt` (lockstep with
   `pyproject.toml`, enforced by `make plugin-zip-check` and by CI).
2. Rename the top `changelog=` entry to the released version and describe
   the user-visible change (or "no behaviour change; lockstep bump").
3. Keep `experimental=False` and `deprecated=False`.

## metadata.txt field inventory

| Field | Status | Notes |
|---|---|---|
| `name` | required | Free-form, unique in the QGIS repo |
| `qgisMinimumVersion` | required | `3.28` (LTR) |
| `qgisMaximumVersion` | optional | `3.99` to allow 3.x bumps |
| `description` | required | ≤ 512 chars, single line |
| `about` | required | Multiline, used as the listing body |
| `version` | required | semver, **must** match the wheel version |
| `author` | required | Display name |
| `email` | required | Public — visible on the listing |
| `homepage` | optional | Docs site (validator follows the URL) |
| `tracker` | required | GitHub issues URL |
| `repository` | required | GitHub repo URL |
| `icon` | optional | PNG, path relative to `metadata.txt` |
| `category` | optional | `Vector` |
| `tags` | optional | CSV, used for search ranking |
| `experimental` | optional | `False` since 2.5.2 |
| `deprecated` | optional | `False` |
| `hasProcessingProvider` | optional | `no` |
| `server` | optional | `False` |
| `plugin_dependencies` | optional | none |
| `changelog` | optional | Multiline; QGIS renders it on the version page |
| `LICENSE` file | required | Not a metadata key: a file next to `metadata.txt` |

## Post-approval comms

Out of scope for this repo; planned channels are a toot on
`@foss4g.org`, a LinkedIn post, and a note in the next release
announcement on the documentation changelog.

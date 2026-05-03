# Publishing the GISPulse QGIS plugin

Maintainer workflow for shipping the plugin to the official
[QGIS Plugin Repository](https://plugins.qgis.org/).

## Prerequisites

1. An OSGeo / QGIS account with maintainer rights on the `GISPulse`
   plugin once it exists (the first upload creates the entry under
   the uploader's account; transfer to `imagodata` org is a follow-up
   request to the QGIS plugin admins).
2. A tagged release on this repo (`v*.*.*`) — the
   [release workflow](../.github/workflows/release.yml) builds the
   ZIP and attaches it to the GitHub Release.

## First-time submission

Done **once** for the lifetime of the plugin entry on plugins.qgis.org.

1. Confirm `qgis_plugin/metadata.txt` is up to date — see the field
   inventory below.
2. Run `make plugin-zip-check` to enforce the
   `metadata.txt` ↔ `pyproject.toml` lockstep.
3. Run `make plugin-zip` and verify the resulting ZIP installs cleanly
   in a fresh QGIS profile via Extensions → Install from ZIP.
4. Sign in to <https://plugins.qgis.org/> and open
   <https://plugins.qgis.org/plugins/add/>.
5. Upload `dist/gispulse-qgis-plugin-<version>.zip` from a tagged
   release (do **not** upload a dirty local build). The form parses
   `metadata.txt` to populate the listing.
6. The plugin lands as `experimental` (because `experimental=True` in
   metadata) — it's installable immediately for users who tick "Show
   experimental plugins" in QGIS, but won't appear in the default
   plugin list until a reviewer approves the entry.
7. Reviewer turnaround is typically 1–4 weeks. While waiting, the
   GitHub Release ZIP remains the canonical install path; both routes
   point at the same artefact.

## Subsequent releases

Once the plugin entry exists, every new tagged release should bump it.

1. Bump `qgis_plugin/metadata.txt` `version=` and prepend a new
   `changelog=` entry. Keep the version in lockstep with
   `pyproject.toml` — `make plugin-zip-check` enforces this.
2. Push a tag `v*.*.*` — CI builds + attaches the ZIP to the GitHub
   Release ([`build-plugin-zip` job](../.github/workflows/release.yml)).
3. From <https://plugins.qgis.org/plugins/GISPulse/>, click **Manage
   versions → Upload new version** and pick the freshly tagged ZIP.
4. Publishing a new version retains the `experimental` flag from
   metadata; flip it to `False` only after a beta cycle (target:
   v1.6.x).

## metadata.txt field inventory

| Field | Status | Notes |
|---|---|---|
| `name` | required | Free-form, must be unique in the QGIS repo |
| `qgisMinimumVersion` | required | We pin `3.28` (LTR) |
| `qgisMaximumVersion` | recommended | We pin `3.99` to allow 3.x bumps |
| `description` | required | ≤ 512 chars, single line |
| `about` | required | Multiline, used as the listing body |
| `version` | required | semver, **must** match the wheel version |
| `author` | required | Display name |
| `email` | required | Public — visible on the listing |
| `homepage` | recommended | <https://gispulse.dev> |
| `tracker` | recommended | GitHub issues URL |
| `repository` | recommended | GitHub repo URL |
| `icon` | recommended | 24×24 PNG, path relative to `metadata.txt` |
| `category` | recommended | `Vector` for us |
| `tags` | recommended | CSV, used for search ranking |
| `experimental` | recommended | `True` until v1.6.x |
| `deprecated` | recommended | `False` |
| `hasProcessingProvider` | recommended | `no` (no processing algorithms exposed) |
| `server` | recommended | `False` (no server-side plugin) |
| `plugin_dependencies` | optional | none |
| `changelog` | recommended | Multiline; QGIS renders it on the version page |

## Reviewer feedback

If the OSGeo reviewer asks for changes, edit `metadata.txt` (and any
referenced files), bump `version` to the next patch (e.g. 1.5.1 →
1.5.2), and re-upload via the standard release workflow above.
**Do not** edit a published version in place — the QGIS plugin manager
caches by version number and won't re-fetch.

## Post-approval comms

Out of scope for this repo, but the planned channels are:

- A short `Mastodon @foss4g.org` toot
- A LinkedIn post on the company page
- A note in the next release announcement on
  [`docs.gispulse.dev/changelog`](https://docs.gispulse.dev/changelog)

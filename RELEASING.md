# Releasing GISPulse

GISPulse releases are cut from `main` and published to PyPI as `gispulse`.
The full pipeline runs in `.github/workflows/release.yml` — this page is the
operator handbook.

## Pre-flight (15 min)

1. **Pick the version.** Bump `pyproject.toml`'s `version = "X.Y.Z"` on a
   release branch. Follow [SemVer](https://semver.org): `MAJOR` for breaking,
   `MINOR` for additive, `PATCH` for bug fixes. Pre-releases use the trailing
   suffix `rcN`, `aN`, `bN` (e.g. `1.3.0rc1`).
2. **Update CHANGELOG.md.** Promote the `## [Unreleased]` heading into
   `## [X.Y.Z] - YYYY-MM-DD` and seed a fresh empty `## [Unreleased]` above
   it. Keep the `### Added / Changed / Removed / Fixed / Security` sub-sections
   that have content; drop the empty ones. The release workflow extracts the
   matching `## [X.Y.Z]` block as the GitHub Release notes — an empty section
   fails the build.
3. **Run the local guards.**
   ```bash
   python -m pytest -q                 # full suite must be green
   python -m build                     # sanity-check build
   twine check dist/*                  # metadata/markdown rendering OK
   ```
4. **Open a release PR** with the bump + CHANGELOG promotion. Get it merged.

## Dry run (recommended on every release)

Before tagging, validate the workflow end-to-end without publishing:

1. Go to **Actions → Release → Run workflow** on GitHub.
2. Set `dry_run` to `true` (default) and optionally pick a `version` for the
   CHANGELOG extraction (defaults to `pyproject.toml`).
3. The job builds the sdist + wheel, runs `twine check`, executes the
   import + CLI smoke test, and extracts the CHANGELOG block. It **skips**
   the PyPI upload and the GitHub Release creation.
4. Inspect the `Extract changelog section` step output — that is what the
   real release will publish.

If anything fails, fix it on `main` before tagging.

## Cut the release

```bash
git checkout main
git pull --ff-only
git tag -s vX.Y.Z -m "GISPulse X.Y.Z"
git push origin vX.Y.Z
```

Pushing the tag triggers the same workflow with `dry_run` implicitly off:

* `publish-pypi` — verifies tag matches `pyproject.toml`, builds artefacts,
  smoke-tests the wheel in a fresh venv, then uploads to PyPI via
  [trusted publisher OIDC](https://docs.pypi.org/trusted-publishers/) (no
  long-lived `PYPI_API_TOKEN` is stored in this repo).
* `github-release` — generates `dist/SHA256SUMS`, extracts the CHANGELOG
  section into `release-notes.md`, then creates the GitHub Release with the
  wheel, sdist and checksum file attached.

The PyPI side requires a one-time **Trusted Publisher** binding for the
`imagodata/gispulse` repository, environment `pypi`, workflow
`release.yml`. Configure it at
<https://pypi.org/manage/account/publishing/>.

## Verify the release

```bash
python -m venv /tmp/verify
/tmp/verify/bin/pip install gispulse==X.Y.Z
/tmp/verify/bin/python -c "import gispulse; print(gispulse.__version__)"
/tmp/verify/bin/gispulse --help
```

Spot-check the GitHub Release page:

* notes match the CHANGELOG section
* `*.whl`, `*.tar.gz`, `SHA256SUMS` attached
* `pip install gispulse==X.Y.Z` resolves on a fresh machine

## Rollback

PyPI does **not** support deleting a published version (only yanking).
If a critical bug ships:

1. Bump to `X.Y.Z+1` with the fix.
2. Yank the broken version on PyPI: <https://pypi.org/manage/project/gispulse/release/X.Y.Z/>
   → *Options → Yank*. Yanked versions stay installable for users who pin
   them but disappear from `pip install gispulse` resolution.
3. File a CHANGELOG entry under the new version describing what was wrong.

Never delete or move a published Git tag.

## Pre-releases

PyPI accepts `1.3.0rc1`, `1.3.0a1`, `1.3.0b1` natively. They install only
when callers opt in via `pip install --pre gispulse` or an exact pin
(`gispulse==1.3.0rc1`). Use them for the final dry-run on real PyPI when
the workflow's GitHub-side `workflow_dispatch` isn't enough — typical
flow:

```bash
# bump pyproject.toml to 1.3.0rc1, commit on a branch
git tag v1.3.0rc1 && git push origin v1.3.0rc1
# verify with: pip install --pre gispulse==1.3.0rc1
# then bump to 1.3.0, tag again
```

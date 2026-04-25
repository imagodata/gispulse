# scripts/

Build, validation and utility scripts for GISPulse.

## Docs-site build pipeline

Three scripts back the docs-site (`docs-site/`), which deploys to GitHub Pages.

### `build_playground_data.py`

Generates the lightweight static datasets that power the **MiniDemo** cards
(docs-site/.vitepress/theme/components/playground/MiniDemo.vue) — the offline,
backend-free previews shown on every playground scenario page.

- Reads source GPKGs from `examples/datasets/`
- Applies per-scenario: bbox clip, Douglas-Peucker simplification, feature
  decimation, column pruning, coordinate rounding
- Writes gzipped GeoJSON under `docs-site/public/playground/data/<scenario>/`
- Emits a `manifest.json` consumed by [useStaticPlayground](../docs-site/.vitepress/theme/composables/useStaticPlayground.ts)

```bash
# build everything
python scripts/build_playground_data.py

# CI mode — fail if any layer > 80 kB or scenario > 100 kB gzipped
python scripts/build_playground_data.py --strict

# rebuild one scenario
python scripts/build_playground_data.py --scenario flood-risk

# dry-run (no writes, size log only)
python scripts/build_playground_data.py --dry-run
```

Current output (6 scenarios): **~300 kB total**, each page loads **7-65 kB**
gzipped. That budget keeps rendering freeze-safe even on 3G / low-power devices.

### `build_templates_index.py`

Scans `templates/*.json` (21 business presets) and produces a compact index
consumed by [TemplatesGallery](../docs-site/.vitepress/theme/components/TemplatesGallery.vue).

- `docs-site/public/templates/index.json` — metadata only (~25 kB)
- `docs-site/public/templates/<name>.json` — copy of each preset for download

The full JSON body of a preset is only fetched when a user clicks "Preview",
so the page stays small on first paint.

```bash
python scripts/build_templates_index.py
python scripts/build_templates_index.py --dry-run
```

### `smoke_test_docs.py`

Post-build validation of `docs-site/.vitepress/dist/`. Verifies:

- Critical pages rendered (`use-cases.html`, `templates.html`, `playground/index.html`)
- `playground/data/manifest.json` lists every scenario, and every layer file it
  references exists on disk and decompresses to valid JSON
- `templates/index.json` lists > 0 presets, and every referenced `.json` file
  is present

Exits 1 on the first hard failure — intended as a CI guard.

```bash
python scripts/smoke_test_docs.py
```

### Makefile targets

```bash
make docs-data    # rebuild static datasets + templates index (with --strict)
make docs-dev     # data + vitepress dev server
make docs-build   # data + vitepress build + smoke test
```

## Other scripts

- `export_openapi.py` — exports the FastAPI OpenAPI schema (for `/docs`)
- `install.sh` — one-shot install helper for development environments

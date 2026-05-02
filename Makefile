.PHONY: install test test-unit test-integration lint format clean docs build-sidecar build-portal build-viewer build-desktop docs-data docs-build docs-dev plugin-zip plugin-zip-check

install:
	pip install -e ".[dev]"

test:
	python -m pytest tests/ -v

test-unit:
	python -m pytest tests/unit/ -v

test-integration:
	python -m pytest tests/integration/ -v

lint:
	ruff check .

format:
	ruff format .

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
	find . -type d -name "*.egg-info" -exec rm -rf {} +

docs:
	python scripts/export_openapi.py --json

# ── Docs site (VitePress / GitHub Pages) ──────────────────────────
# Regenerate static playground datasets + templates index, then build the site.
# The static datasets (<60 kB each, gzipped) replace the runtime API on GH Pages
# and avoid freezing the browser when rendering large BD TOPO extracts.

docs-data:
	python scripts/build_playground_data.py --strict
	python scripts/build_templates_index.py

docs-dev: docs-data
	cd docs-site && npx vitepress dev

docs-build: docs-data
	cd docs-site && npx vitepress build
	python scripts/smoke_test_docs.py

# ── Build targets ──────────────────────────────────────────────────

plugin-zip:
	python scripts/build_qgis_plugin_zip.py

plugin-zip-check:
	python scripts/build_qgis_plugin_zip.py --check

build-portal:
	cd portal && npm ci && npm run build

build-viewer:
	cd viewer && npm ci && npm run build

build-sidecar: build-portal build-viewer
	pip install pyinstaller
	pyinstaller gispulse.spec
	@echo "Sidecar binary: dist/gispulse-engine"

build-desktop: build-sidecar
	@# Copy sidecar to Tauri binaries dir with target triple
	@mkdir -p clients/desktop/src-tauri/binaries
	@TRIPLE=$$(rustc -vV | grep host | cut -d' ' -f2); \
	if [ -f dist/gispulse-engine.exe ]; then \
		cp dist/gispulse-engine.exe "clients/desktop/src-tauri/binaries/gispulse-engine-$$TRIPLE.exe"; \
	else \
		cp dist/gispulse-engine "clients/desktop/src-tauri/binaries/gispulse-engine-$$TRIPLE"; \
	fi
	cd clients/desktop && npm ci && npm run tauri:build
	@echo "Desktop app built in clients/desktop/src-tauri/target/release/bundle/"

# ── Docker ─────────────────────────────────────────────────────────

docker-build:
	docker build -t gispulse:latest .

docker-up:
	docker compose up -d

docker-prod:
	cd deploy && docker compose -f docker-compose.prod.yml up -d

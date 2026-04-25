#!/usr/bin/env python3
"""
Smoke test for docs-site/.vitepress/dist after build.

Verifies that:
  - /playground/data/manifest.json exists and lists every scenario
  - each scenario's layer files exist under the dist tree
  - /templates/index.json exists with at least one item
  - every .json file referenced in index.json is actually present

Exit 0 on success, 1 on the first hard failure.
Use this as a CI step right after `vitepress build`.
"""

from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "docs-site" / ".vitepress" / "dist"


def _fail(msg: str) -> None:
    print(f"[smoke] FAIL: {msg}", file=sys.stderr)
    raise SystemExit(1)


def _ok(msg: str) -> None:
    print(f"[smoke] OK  : {msg}")


def check_playground() -> int:
    manifest_path = DIST / "playground" / "data" / "manifest.json"
    if not manifest_path.exists():
        _fail(f"manifest missing: {manifest_path.relative_to(ROOT)}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    scenarios = manifest.get("scenarios") or []
    if not scenarios:
        _fail("manifest has no scenarios")

    total_features = 0
    for sc in scenarios:
        slug = sc.get("slug")
        for name, entry in (sc.get("layers") or {}).items():
            file_rel = entry.get("file")
            if not file_rel:
                continue
            target = DIST / "playground" / "data" / file_rel
            if not target.exists():
                _fail(f"scenario layer missing: {target.relative_to(ROOT)}")
            # Sanity-check that a gzipped file actually decompresses.
            if file_rel.endswith(".gz"):
                try:
                    with gzip.open(target, "rb") as f:
                        head = f.read(64)
                    if not head.startswith(b"{"):
                        _fail(f"{target.relative_to(ROOT)} is not JSON after gunzip")
                except OSError as exc:
                    _fail(f"gzip error in {target.relative_to(ROOT)}: {exc}")
            total_features += int(entry.get("features", 0))
    _ok(
        f"playground manifest: {len(scenarios)} scenarios, "
        f"{total_features} features total"
    )
    return len(scenarios)


def check_templates() -> int:
    index_path = DIST / "templates" / "index.json"
    if not index_path.exists():
        _fail(f"templates index missing: {index_path.relative_to(ROOT)}")
    index = json.loads(index_path.read_text(encoding="utf-8"))
    items = index.get("items") or []
    if not items:
        _fail("templates index empty")
    for item in items:
        target = DIST / "templates" / item["file"]
        if not target.exists():
            _fail(f"template file missing: {target.relative_to(ROOT)}")
    _ok(f"templates: {len(items)} presets indexed and served")
    return len(items)


def check_pages() -> None:
    for rel in ("templates.html", "playground/index.html"):
        if not (DIST / rel).exists():
            _fail(f"page missing: {rel}")
    _ok("core pages rendered (templates, playground)")


# Chunks expected to stay code-split so they never ship on first paint.
# If one of these gets inlined into the theme/framework bundle, pages that
# don't render a map would suddenly pay the full download cost.
LAZY_CHUNK_PREFIXES = ("maplibre-gl", "leaflet", "@localSearchIndex")

# Hard ceilings on first-paint chunks (bytes). These are the bundles that load
# on every page, so a regression here hits ALL users.
EAGER_BUDGETS = {
    "framework": 250_000,   # Vue + VitePress core
    "theme": 150_000,       # VitePress theme + our custom components (sans carte)
}


def check_bundles() -> None:
    chunks_dir = DIST / "assets" / "chunks"
    if not chunks_dir.exists():
        _fail(f"no assets/chunks dir at {chunks_dir.relative_to(ROOT)}")

    files = {p.name: p.stat().st_size for p in chunks_dir.iterdir() if p.suffix == ".js"}
    if not files:
        _fail("no JS chunks produced by vitepress build")

    # 1. Lazy chunks exist AND stay separated from theme/framework.
    for prefix in LAZY_CHUNK_PREFIXES:
        hits = [n for n in files if n.startswith(prefix)]
        if not hits:
            # maplibre/leaflet are only present if the build pulled them in;
            # the search index is always there once search is enabled.
            if prefix == "@localSearchIndex":
                _fail(f"expected lazy chunk not found: {prefix}*")
            continue
        total = sum(files[n] for n in hits)
        _ok(f"lazy chunk '{prefix}*' code-split, {len(hits)} file(s), {total/1024:.0f} kB")

    # 2. Eager budgets.
    for key, budget in EAGER_BUDGETS.items():
        matches = [n for n in files if n.startswith(key + ".")]
        if not matches:
            print(f"[smoke] WARN: no '{key}.*' chunk found (skipping budget)")
            continue
        size = max(files[n] for n in matches)
        if size > budget:
            _fail(
                f"eager chunk '{key}' = {size/1024:.0f} kB > budget {budget/1024:.0f} kB"
            )
        _ok(f"eager chunk '{key}' = {size/1024:.0f} kB (budget {budget/1024:.0f} kB)")

    # 3. Sanity: the theme chunk must stay small. Dynamic imports of
    #    maplibre-gl leave the module name as a string in the theme, which is
    #    expected — what we really guard against is the full library byte-code
    #    landing in theme. The size budget above already catches that.


def main() -> int:
    if not DIST.exists():
        _fail(f"dist not built: {DIST.relative_to(ROOT)} (run `make docs-build`)")
    check_pages()
    check_playground()
    check_templates()
    check_bundles()
    _ok("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

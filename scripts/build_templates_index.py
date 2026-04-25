#!/usr/bin/env python3
"""
Build a lightweight index of the /templates/*.json presets for the docs site.

Produces:
  docs-site/public/templates/index.json
  docs-site/public/templates/<name>.json   (copy of each preset for download)

The index contains only metadata (name, domain, tags, steps summary, capabilities
used) — the full JSON stays downloadable per file. This avoids shipping ~100 kB
of template bodies on first page render.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT / "templates"
OUT_DIR = ROOT / "docs-site" / "public" / "templates"


def _collect_capabilities(payload: dict) -> list[str]:
    """Extract the set of capabilities referenced by steps or rules."""
    seen: list[str] = []

    def _add(cap: str) -> None:
        if cap and cap not in seen:
            seen.append(cap)

    # v2 pipeline: steps[].capability
    for step in payload.get("steps") or []:
        if isinstance(step, dict):
            _add(step.get("capability", ""))
    # v1 flat rules
    for rule in payload.get("rules") or []:
        if isinstance(rule, dict):
            _add(rule.get("capability", ""))
    # sometimes top-level "capability"
    _add(payload.get("capability", ""))
    return seen


def _summarize_steps(payload: dict) -> list[str]:
    out: list[str] = []
    for step in payload.get("steps") or []:
        if not isinstance(step, dict):
            continue
        label = step.get("id") or step.get("name") or step.get("capability", "")
        cap = step.get("capability", "")
        if label and cap:
            out.append(f"{label} ({cap})")
        elif cap:
            out.append(cap)
    for rule in payload.get("rules") or []:
        if not isinstance(rule, dict):
            continue
        label = rule.get("name") or rule.get("capability", "")
        out.append(label)
    return out[:12]  # cap summary length to keep index tiny


def _guess_domain(name: str, payload: dict) -> str:
    explicit = payload.get("domain")
    if isinstance(explicit, str) and explicit:
        return explicit
    # Fallback: derive from filename prefix (e.g. "urbanisme_*")
    m = re.match(r"^([a-z]+)_", name)
    return m.group(1) if m else "divers"


def build(src: Path, out: Path, *, dry_run: bool = False) -> dict:
    out.mkdir(parents=True, exist_ok=True)

    files = sorted(p for p in src.glob("*.json") if p.is_file())
    index = {"generated_by": "scripts/build_templates_index.py", "count": 0, "items": []}

    for path in files:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(f"  ! skip {path.name}: {exc}", file=sys.stderr)
            continue

        # v1 shape: top-level array of rules
        payload: dict
        if isinstance(raw, list):
            payload = {"version": 1, "rules": raw}
        elif isinstance(raw, dict):
            payload = raw
        else:
            print(f"  ! skip {path.name}: unexpected root type", file=sys.stderr)
            continue

        name = path.stem
        entry = {
            "slug": name,
            "file": f"{name}.json",
            "title": payload.get("name") or name,
            "description": payload.get("description", ""),
            "domain": _guess_domain(name, payload),
            "tags": payload.get("tags", []),
            "requires_pro": bool(payload.get("requires_pro")),
            "requires_plugins": payload.get("requires_plugins", []),
            "version": payload.get("version", 1),
            "capabilities": _collect_capabilities(payload),
            "step_count": len(payload.get("steps") or []) or len(payload.get("rules") or []),
            "steps": _summarize_steps(payload),
            "size_bytes": path.stat().st_size,
        }
        index["items"].append(entry)

        if not dry_run:
            shutil.copyfile(path, out / f"{name}.json")

    index["count"] = len(index["items"])

    if not dry_run:
        (out / "index.json").write_text(
            json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"[templates] wrote {index['count']} presets -> {out.relative_to(ROOT)}")
    else:
        for item in index["items"]:
            print(f"  . {item['slug']} ({item['domain']}, {item['step_count']} steps)")

    return index


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    build(SRC_DIR, OUT_DIR, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

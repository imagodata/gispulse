#!/usr/bin/env python3
"""Export the GISPulse OpenAPI spec to JSON and generate a Markdown API reference.

Usage::

    python scripts/export_openapi.py           # writes docs/API_REFERENCE.md
    python scripts/export_openapi.py --json    # also writes openapi.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def get_openapi_spec() -> dict:
    """Create the app and extract the OpenAPI schema."""
    from gispulse.adapters.http.app import create_app
    app = create_app(mode="portal")
    return app.openapi()


def spec_to_markdown(spec: dict) -> str:
    """Convert an OpenAPI spec dict to a Markdown API reference."""
    lines: list[str] = []
    info = spec.get("info", {})
    lines.append(f"# {info.get('title', 'GISPulse')} — API Reference")
    lines.append("")
    lines.append(f"> Auto-generated from OpenAPI spec v{info.get('version', '?')}")
    lines.append("> Run `python scripts/export_openapi.py` to regenerate.")
    lines.append("")

    # Group paths by tag
    tag_groups: dict[str, list[tuple[str, str, dict]]] = {}
    for path, methods in sorted(spec.get("paths", {}).items()):
        for method, detail in methods.items():
            if method in ("parameters", "servers"):
                continue
            tags = detail.get("tags", ["Untagged"])
            for tag in tags:
                tag_groups.setdefault(tag, []).append((method.upper(), path, detail))

    for tag in sorted(tag_groups):
        lines.append(f"## {tag.title()}")
        lines.append("")
        for method, path, detail in tag_groups[tag]:
            summary = detail.get("summary", detail.get("operationId", ""))
            lines.append(f"### `{method} {path}`")
            lines.append("")
            if summary:
                lines.append(f"**{summary}**")
                lines.append("")
            desc = detail.get("description", "")
            if desc:
                lines.append(desc.strip())
                lines.append("")

            # Parameters
            params = detail.get("parameters", [])
            if params:
                lines.append("**Parameters:**")
                lines.append("")
                lines.append("| Name | In | Type | Required | Description |")
                lines.append("|------|----|------|----------|-------------|")
                for p in params:
                    schema = p.get("schema", {})
                    ptype = schema.get("type", "string")
                    req = "Yes" if p.get("required") else "No"
                    lines.append(f"| `{p['name']}` | {p.get('in', '?')} | {ptype} | {req} | {p.get('description', '')} |")
                lines.append("")

            # Request body
            body = detail.get("requestBody", {})
            if body:
                content = body.get("content", {})
                for ctype, cdef in content.items():
                    ref = cdef.get("schema", {}).get("$ref", "")
                    if ref:
                        schema_name = ref.split("/")[-1]
                        lines.append(f"**Request body:** `{schema_name}` ({ctype})")
                        lines.append("")

            # Responses
            responses = detail.get("responses", {})
            if responses:
                lines.append("**Responses:**")
                lines.append("")
                for code, rdef in sorted(responses.items()):
                    rdesc = rdef.get("description", "")
                    lines.append(f"- **{code}**: {rdesc}")
                lines.append("")

            lines.append("---")
            lines.append("")

    # Schema definitions
    schemas = spec.get("components", {}).get("schemas", {})
    if schemas:
        lines.append("## Schemas")
        lines.append("")
        for name, sdef in sorted(schemas.items()):
            lines.append(f"### {name}")
            lines.append("")
            if sdef.get("description"):
                lines.append(sdef["description"])
                lines.append("")
            props = sdef.get("properties", {})
            required = set(sdef.get("required", []))
            if props:
                lines.append("| Field | Type | Required | Description |")
                lines.append("|-------|------|----------|-------------|")
                for fname, fdef in sorted(props.items()):
                    ftype = fdef.get("type", fdef.get("$ref", "?").split("/")[-1])
                    freq = "Yes" if fname in required else "No"
                    fdesc = fdef.get("description", "")
                    lines.append(f"| `{fname}` | {ftype} | {freq} | {fdesc} |")
                lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Export GISPulse OpenAPI spec")
    parser.add_argument("--json", action="store_true", help="Also write openapi.json")
    args = parser.parse_args()

    spec = get_openapi_spec()

    docs_dir = ROOT / "docs"
    docs_dir.mkdir(exist_ok=True)

    if args.json:
        json_path = docs_dir / "openapi.json"
        json_path.write_text(json.dumps(spec, indent=2))
        print(f"Written: {json_path}")

    md = spec_to_markdown(spec)
    md_path = docs_dir / "API_REFERENCE.md"
    md_path.write_text(md)
    print(f"Written: {md_path} ({len(md)} chars)")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Build the capability coverage matrix dashboard for the docs site.

Renders one Markdown table summarising every registered Capability by:

* tested      — at least one test file references the class
* documented  — capability name appears in `docs-site/guide/capabilities.md`
* exposed-ui  — capability name appears in any `portal/src/**/*.{ts,vue,tsx}`
* example     — capability used in any pipeline preset under `templates/*.json`

Produces:
  docs-site/guide/coverage.md       (FR)
  docs-site/en/guide/coverage.md    (EN)

The CI workflow ``capability-matrix-drift`` runs this script and fails on
any diff against the committed files — the matrix is therefore always in
sync with the registry, the test suite, the docs and the templates.

Usage::

    python scripts/build_capability_matrix.py             # write
    python scripts/build_capability_matrix.py --check     # exit 1 on drift
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from capabilities import registry  # noqa: E402

CAPABILITIES_ROOT = ROOT / "capabilities"
TESTS_ROOT = ROOT / "tests"
PLAYGROUND_ROOT = ROOT / "docs-site" / "playground"
TEMPLATES_ROOT = ROOT / "templates"
DOCS_FR = ROOT / "docs-site" / "guide" / "coverage.md"
DOCS_EN = ROOT / "docs-site" / "en" / "guide" / "coverage.md"
CAPABILITIES_DOC = ROOT / "docs-site" / "guide" / "capabilities.md"


def _iter_capabilities() -> list[tuple[str, str, type]]:
    registry._ensure_defaults_loaded()  # noqa: SLF001
    items: list[tuple[str, str, type]] = []
    for cls in registry.REGISTRY.values():
        items.append((cls.name, cls.__name__, cls))
    items.sort(key=lambda t: t[0])
    return items


def _module_path(cls: type) -> Path | None:
    mod = sys.modules.get(cls.__module__)
    if mod is None or not getattr(mod, "__file__", None):
        return None
    p = Path(mod.__file__)
    try:
        return p.relative_to(ROOT)
    except ValueError:
        return None


def _build_test_index() -> dict[str, list[Path]]:
    """Map ClassName → [test files referencing it].

    Counts a test file only if it *explicitly* mentions the class via
    instantiation (``ClassName(``) or named import (``import ClassName`` /
    ``from x import ClassName``). Wildcard imports and ``from x import *``
    do NOT count — otherwise a single broad smoke-test inflates every
    capability to "tested".
    """
    index: dict[str, list[Path]] = {}
    if not TESTS_ROOT.exists():
        return index
    # Sort `rglob` output: filesystem ordering is environment-dependent
    # (local dev vs GitHub Actions runner) so the first test file picked
    # for each capability would otherwise drift between runs and break
    # the capability-matrix-drift CI gate.
    for path in sorted(TESTS_ROOT.rglob("test_*.py")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for cls_name in set(re.findall(r"\b([A-Z][A-Za-z0-9_]*Capability)\b", text)):
            specific = (
                re.search(rf"\b{cls_name}\(", text)
                or re.search(rf"\bimport[^\n]*\b{cls_name}\b", text)
                or re.search(rf"\bfrom[^\n]+\bimport[^\n]*\b{cls_name}\b", text)
            )
            if specific:
                index.setdefault(cls_name, []).append(path.relative_to(ROOT))
    return index


def _build_doc_set() -> set[str]:
    r"""capability names that appear in `docs-site/guide/capabilities.md`.

    Only counts a capability when it shows up as a Markdown table-cell
    leader (``| \`name\` |``) — this is the convention used by every row
    of the capability index. Free-form mentions (``e.g. \`buffer\` pads…``)
    do NOT count, otherwise a single intro paragraph would mark every
    capability as "documented".
    """
    if not CAPABILITIES_DOC.exists():
        return set()
    text = CAPABILITIES_DOC.read_text(encoding="utf-8", errors="replace")
    found: set[str] = set()
    for match in re.finditer(r"^\|\s*`([a-z][a-z0-9_]*)`", text, re.MULTILINE):
        found.add(match.group(1))
    return found


def _build_playground_set() -> set[str]:
    """capability names referenced in the playground scenarios.

    Playground pages embed JSON pipeline snippets with ``"capability": "<name>"``
    which is a strong signal that the capability is reachable from the
    public-facing UI showcase.
    """
    if not PLAYGROUND_ROOT.exists():
        return set()
    found: set[str] = set()
    quoted = re.compile(r'"capability"\s*:\s*"([a-z][a-z0-9_]*)"')
    for path in PLAYGROUND_ROOT.rglob("*.md"):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in quoted.findall(text):
            found.add(match)
    return found


def _build_template_set() -> set[str]:
    """capability names used in any `templates/*.json` preset."""
    if not TEMPLATES_ROOT.exists():
        return set()
    found: set[str] = set()
    for path in TEMPLATES_ROOT.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            continue
        _walk_for_capability(data, found)
    return found


def _walk_for_capability(node, out: set[str]) -> None:
    if isinstance(node, dict):
        cap = node.get("capability")
        if isinstance(cap, str):
            out.add(cap)
        for v in node.values():
            _walk_for_capability(v, out)
    elif isinstance(node, list):
        for v in node:
            _walk_for_capability(v, out)


def _badge(flag: bool) -> str:
    return "✅" if flag else "—"


def _link(target: Path | None, label: str) -> str:
    if target is None:
        return label
    # Both docs files live under docs-site/{,en}/guide/, so two `..`
    # bring us back to the repo root.
    return f"[{label}](../../../{target.as_posix()})"


def _render(items, test_index, doc_set, portal_set, template_set, *, lang: str) -> str:
    if lang == "fr":
        intro = (
            "# Matrice de couverture des capabilities\n\n"
            "Source de vérité auto-générée par "
            "`scripts/build_capability_matrix.py`. La CI fait échouer un PR "
            "qui désaligne cette page de la combinaison registry × tests × "
            "docs × templates.\n\n"
            "**Légende** — `✅` couvert · `—` non couvert.\n\n"
            "**Colonnes** :\n"
            "- *Source* — fichier qui définit la capability\n"
            "- *Tests* — au moins un fichier `tests/**/test_*.py` "
            "référence la classe\n"
            "- *Docs* — la capability apparaît dans `guide/capabilities`\n"
            "- *Playground* — la capability est référencée dans une scène "
            "publique du playground (`docs-site/playground/*.md`)\n"
            "- *Template* — au moins un preset `templates/*.json` l'utilise\n\n"
        )
        cols = "| Capability | Source | Tests | Docs | Playground | Template |\n"
        sep = "|---|---|---|---|---|---|\n"
        summary_label = "**Total**"
    else:
        intro = (
            "# Capability coverage matrix\n\n"
            "Auto-generated source of truth produced by "
            "`scripts/build_capability_matrix.py`. CI fails any PR that "
            "drifts this page from the live registry × tests × docs × "
            "templates combination.\n\n"
            "**Legend** — `✅` covered · `—` not covered.\n\n"
            "**Columns**:\n"
            "- *Source* — file that defines the capability\n"
            "- *Tests* — at least one file under `tests/**/test_*.py` "
            "references the class\n"
            "- *Docs* — the capability appears in `guide/capabilities`\n"
            "- *Playground* — the capability is referenced by a public "
            "playground scenario (`docs-site/playground/*.md`)\n"
            "- *Template* — at least one preset under `templates/*.json` "
            "uses it\n\n"
        )
        cols = "| Capability | Source | Tests | Docs | Playground | Template |\n"
        sep = "|---|---|---|---|---|---|\n"
        summary_label = "**Total**"

    rows: list[str] = []
    n_total = len(items)
    n_tested = n_doc = n_portal = n_tpl = 0

    for name, class_name, cls in items:
        src = _module_path(cls)
        tested = bool(test_index.get(class_name))
        if tested:
            n_tested += 1
        documented = name in doc_set
        if documented:
            n_doc += 1
        in_portal = name in portal_set
        if in_portal:
            n_portal += 1
        in_template = name in template_set
        if in_template:
            n_tpl += 1

        first_test = test_index.get(class_name, [None])[0]
        rows.append(
            "| `{name}` | {src} | {tests} | {docs} | {portal} | {tpl} |".format(
                name=name,
                src=_link(src, src.name if src else "n/a"),
                tests=(
                    _link(first_test, _badge(True))
                    if tested and first_test is not None
                    else _badge(False)
                ),
                docs=_badge(documented),
                portal=_badge(in_portal),
                tpl=_badge(in_template),
            )
        )

    summary_row = (
        f"| {summary_label} | — | "
        f"{n_tested} / {n_total} | "
        f"{n_doc} / {n_total} | "
        f"{n_portal} / {n_total} | "
        f"{n_tpl} / {n_total} |"
    )

    body = (
        intro
        + cols
        + sep
        + "\n".join(rows)
        + "\n"
        + summary_row
        + "\n\n"
        + (
            f"*Generated by `scripts/build_capability_matrix.py` "
            f"({date.today().isoformat()}). "
            "Run `python scripts/build_capability_matrix.py` after "
            "adding / removing a capability.*\n"
        )
    )
    return body


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _check(path: Path, content: str) -> bool:
    if not path.exists():
        return False
    return path.read_text(encoding="utf-8") == content


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 if any output file would change (CI drift gate).",
    )
    args = parser.parse_args()

    items = _iter_capabilities()
    test_index = _build_test_index()
    doc_set = _build_doc_set()
    portal_set = _build_playground_set()
    template_set = _build_template_set()

    fr = _render(items, test_index, doc_set, portal_set, template_set, lang="fr")
    en = _render(items, test_index, doc_set, portal_set, template_set, lang="en")

    if args.check:
        ok_fr = _check(DOCS_FR, fr)
        ok_en = _check(DOCS_EN, en)
        if not (ok_fr and ok_en):
            missing = [
                str(p.relative_to(ROOT))
                for p, ok in [(DOCS_FR, ok_fr), (DOCS_EN, ok_en)]
                if not ok
            ]
            print(
                "Capability matrix drift detected in: "
                + ", ".join(missing)
                + "\nRun: python scripts/build_capability_matrix.py",
                file=sys.stderr,
            )
            return 1
        print(f"Matrix in sync ({len(items)} capabilities).")
        return 0

    _write(DOCS_FR, fr)
    _write(DOCS_EN, en)
    print(
        f"Wrote {DOCS_FR.relative_to(ROOT)} and "
        f"{DOCS_EN.relative_to(ROOT)} ({len(items)} capabilities)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

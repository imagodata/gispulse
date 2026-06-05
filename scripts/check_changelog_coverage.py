#!/usr/bin/env python3
"""
Changelog coverage gate (Phase 4 of EPIC #309, closes #320).

Reads the current version from ``pyproject.toml`` (``[project] version = ...``)
and verifies that an entry for that version exists in both:

  - ``docs-site/changelog.md`` (FR, hard requirement)
  - ``docs-site/en/changelog.md`` (EN, soft warning by default; flip with
    ``--strict-en`` to make it hard)

Wired into a ``pull_request`` job that path-filters on ``pyproject.toml`` so
that a version bump cannot land on ``main`` without a matching changelog
entry. See ``.github/workflows/docs-gate.yml``.

Exit codes:
  0  — both changelogs (or FR only when EN soft) cover the current version
  1  — FR changelog missing the entry
  2  — EN changelog missing the entry AND ``--strict-en`` is set
  3  — version cannot be parsed from ``pyproject.toml``

The entry header matched is ``## [<version>]`` — anything else (notes,
date, suffix) is allowed on the same line. ``[Unreleased]`` is not a
match. We deliberately match on the bracketed form to avoid false hits
in prose.
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
FR_CHANGELOG = ROOT / "docs-site" / "changelog.md"
EN_CHANGELOG = ROOT / "docs-site" / "en" / "changelog.md"


def _read_version() -> str:
    if not PYPROJECT.exists():
        print(f"[gate] FAIL: {PYPROJECT} missing", file=sys.stderr)
        raise SystemExit(3)
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    version = (data.get("project") or {}).get("version")
    if not isinstance(version, str) or not version.strip():
        print(
            "[gate] FAIL: [project].version not found in pyproject.toml",
            file=sys.stderr,
        )
        raise SystemExit(3)
    return version.strip()


def _has_entry(path: Path, version: str) -> bool:
    if not path.exists():
        return False
    pattern = re.compile(rf"^##\s+\[{re.escape(version)}\]", re.MULTILINE)
    return bool(pattern.search(path.read_text(encoding="utf-8")))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strict-en",
        action="store_true",
        help="treat missing EN entry as a hard failure (default: warn only)",
    )
    args = parser.parse_args()

    version = _read_version()
    print(f"[gate] pyproject.toml version = {version}")

    fr_ok = _has_entry(FR_CHANGELOG, version)
    en_ok = _has_entry(EN_CHANGELOG, version)

    if fr_ok:
        print(f"[gate] OK  : FR changelog covers {version}")
    else:
        print(
            f"[gate] FAIL: {FR_CHANGELOG.relative_to(ROOT)} has no "
            f"`## [{version}]` entry",
            file=sys.stderr,
        )

    if en_ok:
        print(f"[gate] OK  : EN changelog covers {version}")
    elif args.strict_en:
        print(
            f"[gate] FAIL: {EN_CHANGELOG.relative_to(ROOT)} has no "
            f"`## [{version}]` entry (--strict-en)",
            file=sys.stderr,
        )
    else:
        print(
            f"[gate] WARN: {EN_CHANGELOG.relative_to(ROOT)} has no "
            f"`## [{version}]` entry (soft)",
            file=sys.stderr,
        )

    if not fr_ok:
        return 1
    if not en_ok and args.strict_en:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

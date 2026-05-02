#!/usr/bin/env python3
"""Package qgis_plugin/ into a QGIS-installable ZIP.

The output is `dist/gispulse-qgis-plugin-<version>.zip` whose root directory
is `gispulse/` (the plugin folder name expected by QGIS Plugin Manager).

Version is read from `qgis_plugin/metadata.txt` and asserted equal to the
`version` declared in `pyproject.toml` so the wheel and the plugin ZIP stay
lockstep (per memory `qgis_plugin_monorepo`).

Usage::

    python scripts/build_qgis_plugin_zip.py             # build
    python scripts/build_qgis_plugin_zip.py --check     # validate version match only
"""

from __future__ import annotations

import argparse
import configparser
import sys
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = ROOT / "qgis_plugin"
DIST_DIR = ROOT / "dist"
PLUGIN_FOLDER_NAME = "gispulse"

INCLUDED_SUFFIXES = {".py", ".png", ".txt", ".md", ".svg", ".qml", ".ui"}
EXCLUDED_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache"}


def _read_plugin_version() -> str:
    parser = configparser.ConfigParser()
    parser.read(PLUGIN_DIR / "metadata.txt", encoding="utf-8")
    return parser["general"]["version"].strip()


def _read_pyproject_version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as fh:
        data = tomllib.load(fh)
    return str(data["project"]["version"]).strip()


def _check_version_lockstep() -> str:
    plugin_v = _read_plugin_version()
    wheel_v = _read_pyproject_version()
    if plugin_v != wheel_v:
        sys.stderr.write(
            f"version drift: qgis_plugin/metadata.txt={plugin_v} but "
            f"pyproject.toml={wheel_v} — keep them lockstep.\n"
        )
        sys.exit(2)
    return plugin_v


def _iter_plugin_files() -> list[Path]:
    files: list[Path] = []
    for path in PLUGIN_DIR.rglob("*"):
        if path.is_dir():
            continue
        if any(part in EXCLUDED_DIRS for part in path.parts):
            continue
        if path.suffix and path.suffix not in INCLUDED_SUFFIXES:
            continue
        files.append(path)
    return sorted(files)


def build(version: str) -> Path:
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    out = DIST_DIR / f"gispulse-qgis-plugin-{version}.zip"
    if out.exists():
        out.unlink()
    files = _iter_plugin_files()
    if not files:
        sys.stderr.write(f"no files found under {PLUGIN_DIR}\n")
        sys.exit(1)
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            arcname = Path(PLUGIN_FOLDER_NAME) / path.relative_to(PLUGIN_DIR)
            zf.write(path, arcname.as_posix())
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate that metadata.txt and pyproject.toml versions match, then exit.",
    )
    args = parser.parse_args()
    version = _check_version_lockstep()
    if args.check:
        print(f"versions in lockstep: {version}")
        return 0
    out = build(version)
    print(f"built {out.relative_to(ROOT)} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

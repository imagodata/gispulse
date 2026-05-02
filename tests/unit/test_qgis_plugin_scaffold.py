"""Smoke tests for the QGIS plugin scaffold (issue v1.4-1).

The plugin itself can't be imported outside QGIS (uses qgis.PyQt) so we
validate the static surface only:

* metadata.txt is parseable and lockstep with pyproject.toml
* the ZIP build produces a QGIS-installable archive (root dir = "gispulse")
* required acceptance files are present
"""

from __future__ import annotations

import configparser
import subprocess
import sys
import zipfile
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_DIR = ROOT / "qgis_plugin"
BUILD_SCRIPT = ROOT / "scripts" / "build_qgis_plugin_zip.py"


def test_plugin_dir_present() -> None:
    assert PLUGIN_DIR.is_dir()
    for required in ("metadata.txt", "__init__.py", "main_plugin.py", "icon.png"):
        assert (PLUGIN_DIR / required).is_file(), f"missing qgis_plugin/{required}"


def test_metadata_txt_minimum_fields() -> None:
    parser = configparser.ConfigParser()
    parser.read(PLUGIN_DIR / "metadata.txt", encoding="utf-8")
    g = parser["general"]
    for key in (
        "name",
        "version",
        "qgisMinimumVersion",
        "description",
        "about",
        "author",
        "email",
        "homepage",
        "repository",
        "icon",
    ):
        assert g.get(key, "").strip(), f"metadata.txt missing or empty key: {key}"


def test_version_lockstep_with_pyproject() -> None:
    parser = configparser.ConfigParser()
    parser.read(PLUGIN_DIR / "metadata.txt", encoding="utf-8")
    plugin_v = parser["general"]["version"].strip()
    with (ROOT / "pyproject.toml").open("rb") as fh:
        wheel_v = str(tomllib.load(fh)["project"]["version"]).strip()
    assert plugin_v == wheel_v, (
        f"version drift: qgis_plugin/metadata.txt={plugin_v} vs pyproject.toml={wheel_v}"
    )


def test_classfactory_returns_a_class(tmp_path: Path) -> None:
    """`classFactory` is the QGIS entry-point — must exist and be callable.
    It can't be imported transitively because main_plugin needs qgis.PyQt,
    so we only assert presence in the source.
    """
    init_src = (PLUGIN_DIR / "__init__.py").read_text(encoding="utf-8")
    assert "def classFactory" in init_src
    assert "GISPulsePlugin" in init_src


def test_build_zip_produces_installable_archive(tmp_path: Path) -> None:
    res = subprocess.run(
        [sys.executable, str(BUILD_SCRIPT)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, f"build script failed: {res.stderr}"
    parser = configparser.ConfigParser()
    parser.read(PLUGIN_DIR / "metadata.txt", encoding="utf-8")
    version = parser["general"]["version"].strip()
    zip_path = ROOT / "dist" / f"gispulse-qgis-plugin-{version}.zip"
    assert zip_path.is_file(), f"expected ZIP at {zip_path}"
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
    assert all(n.startswith("gispulse/") for n in names), (
        "ZIP root folder must be 'gispulse/' for QGIS Plugin Manager"
    )
    for required in ("gispulse/metadata.txt", "gispulse/__init__.py", "gispulse/icon.png"):
        assert required in names, f"missing {required} in ZIP"
    # PUBLISHING.md is a maintainer-only guide; shipping it would clutter
    # the QGIS Plugin Manager listing and confuse users.
    assert "gispulse/PUBLISHING.md" not in names, (
        "PUBLISHING.md must stay out of the user-facing ZIP — see EXCLUDED_FILES"
    )


def test_metadata_has_required_qgis_fields() -> None:
    """plugins.qgis.org rejects uploads missing any of these fields,
    so we enforce them here rather than discovering on submission."""
    parser = configparser.ConfigParser()
    parser.read(PLUGIN_DIR / "metadata.txt", encoding="utf-8")
    g = parser["general"]
    assert g["category"] == "Vector"
    assert g["experimental"] in ("True", "true")
    assert "changelog" in g
    # Description must be a single line under the QGIS 512-char cap.
    assert len(g["description"]) <= 512
    assert "\n" not in g["description"].strip()

"""SDK surface + plugin-migration tests (issue #183)."""

from __future__ import annotations

from pathlib import Path

import pytest

# tomllib is stdlib on Python 3.11+; fall back to tomli on 3.10.
try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    try:
        import tomli as tomllib
    except ModuleNotFoundError:
        tomllib = None  # type: ignore[assignment]

_REPO = Path(__file__).resolve().parents[2]
_PLUGIN_PYPROJECTS = sorted((_REPO / "plugins").glob("*/pyproject.toml"))
_PLUGIN_SOURCES = sorted((_REPO / "plugins").glob("*/gispulse_cap_*/*.py"))
_TEMPLATE = _REPO / "examples" / "plugin-template"


# --------------------------------------------------------------------------
# gispulse.plugins.api — the single public surface
# --------------------------------------------------------------------------


def test_api_exports_manifest_and_contracts() -> None:
    from gispulse.plugins import api

    for symbol in (
        "Capability",
        "register_capability",
        "PluginHostContext",
        "PluginManifest",
        "DataSource",
        "RegulatorySource",
        "DataSink",
        "Fetcher",
        "Writer",
        "SourceResult",
        "AccessSpec",
        "RuleClause",
    ):
        assert symbol in api.__all__, f"{symbol} missing from api.__all__"
        assert hasattr(api, symbol), f"{symbol} not importable from api"


def test_api_symbols_are_the_core_objects() -> None:
    from core.plugin_model import PluginManifest, SourceResult
    from core.sources import DataSource
    from gispulse.plugins import api

    assert api.PluginManifest is PluginManifest
    assert api.SourceResult is SourceResult
    assert api.DataSource is DataSource


# --------------------------------------------------------------------------
# Migration — no plugin imports the internal layout directly
# --------------------------------------------------------------------------


def test_plugins_dir_is_present() -> None:
    assert _PLUGIN_SOURCES, "no plugin sources found — repo layout changed?"


@pytest.mark.parametrize("source", _PLUGIN_SOURCES, ids=lambda p: p.parent.name)
def test_plugin_does_not_import_internal_layout(source: Path) -> None:
    # Forbid importing the top-level ``capabilities`` package directly;
    # an intra-package ``from gispulse_cap_x import capabilities`` is fine.
    for raw in source.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        assert not line.startswith(("from capabilities.", "from capabilities import")), (
            f"{source} still imports the internal capabilities.* layout — "
            f"migrate to gispulse.plugins.api ({line!r})"
        )
        assert not line.startswith(("import capabilities ", "import capabilities.")), (
            f"{source} still imports the internal capabilities package ({line!r})"
        )
        assert line != "import capabilities", (
            f"{source} still imports the internal capabilities package"
        )


def test_template_uses_the_sdk() -> None:
    caps = (_TEMPLATE / "gispulse_cap_example" / "capabilities.py").read_text("utf-8")
    assert "from gispulse.plugins.api import" in caps
    assert "from capabilities" not in caps


# --------------------------------------------------------------------------
# Manifest — [tool.gispulse.plugin] in every plugin pyproject
# --------------------------------------------------------------------------


def test_plugin_pyprojects_present() -> None:
    # 6 gispulse-cap-* plugins, plus any gispulse-src-* pilots (#184).
    assert len(_PLUGIN_PYPROJECTS) >= 6


_VALID_KINDS = {"capability", "source", "sink", "protocol"}


@pytest.mark.skipif(tomllib is None, reason="needs tomllib (py3.11+) or tomli")
@pytest.mark.parametrize(
    "pyproject",
    [*_PLUGIN_PYPROJECTS, _TEMPLATE / "pyproject.toml"],
    ids=lambda p: p.parent.name,
)
def test_pyproject_declares_manifest_and_gispulse_dep(pyproject: Path) -> None:
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    manifest = data.get("tool", {}).get("gispulse", {}).get("plugin")
    assert manifest is not None, f"{pyproject} missing [tool.gispulse.plugin]"
    assert manifest.get("protocol"), "manifest must declare a protocol specifier"
    assert manifest.get("kind") in _VALID_KINDS, (
        f"{pyproject}: kind must be one of {sorted(_VALID_KINDS)}"
    )

    deps = " ".join(data.get("project", {}).get("dependencies", []))
    assert "gispulse" in deps, f"{pyproject} must depend on gispulse"

"""Tests for the third PyPI discovery channel (story T5, issue #269).

Validates that data packs installed via PyPI — declaring an entry-point in
the ``gispulse.data_packs`` group — are picked up by ExtensionHub, alongside
the bundled OSS manifests and the ``GISPULSE_DATA_PACKS_DIR`` user-dir
channel, and that one bad pack never locks out the rest.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Iterable

import pytest

from gispulse.core import plugin_hub


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ep(name: str, factory) -> SimpleNamespace:
    """Build a minimal stand-in for an importlib.metadata EntryPoint."""
    return SimpleNamespace(name=name, load=lambda: factory)


def _write_manifest(dirpath: Path, name: str, content: str = "source-catalog") -> Path:
    """Write a minimal valid data-pack manifest and return its path."""
    payload = {
        "name": name,
        "content": content,
        "version": "1.0.0",
        "display_name": name.title(),
        "description": "fixture",
        "tier": "community",
        "entries": [],
    }
    p = dirpath / f"{name}.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


@pytest.fixture
def patch_entrypoints(monkeypatch: pytest.MonkeyPatch):
    """Replace ``_eps`` so we feed our own entry-points into the scanner."""

    def factory(items_by_group: dict[str, list]) -> None:
        def fake_eps(group: str):
            return list(items_by_group.get(group, []))

        monkeypatch.setattr(plugin_hub, "_eps", fake_eps)

    return factory


# ---------------------------------------------------------------------------
# Happy path — manifest discovered through an entry-point
# ---------------------------------------------------------------------------


def test_entrypoint_returning_single_path_is_discovered(
    tmp_path: Path, patch_entrypoints, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pack whose entry-point returns one path lands in the discovery list."""
    monkeypatch.delenv(plugin_hub._DATA_PACKS_DIR_ENV, raising=False)
    manifest = _write_manifest(tmp_path, "my_pack")

    def factory_single() -> Path:
        return manifest

    patch_entrypoints(
        {plugin_hub._DATA_PACK_ENTRYPOINT_GROUP: [_ep("my_pack", factory_single)]}
    )

    paths = plugin_hub._data_pack_manifest_paths()
    assert (manifest, plugin_hub.Origin.EXTERNAL) in paths


def test_entrypoint_returning_iterable_is_discovered(
    tmp_path: Path, patch_entrypoints, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pack returning multiple manifest paths discovers all of them."""
    monkeypatch.delenv(plugin_hub._DATA_PACKS_DIR_ENV, raising=False)
    a = _write_manifest(tmp_path, "pack_a")
    b = _write_manifest(tmp_path, "pack_b")

    def factory_many() -> Iterable[Path]:
        return [a, b]

    patch_entrypoints(
        {plugin_hub._DATA_PACK_ENTRYPOINT_GROUP: [_ep("multi", factory_many)]}
    )

    found = {path for path, _ in plugin_hub._data_pack_manifest_paths()}
    assert a in found and b in found


def test_entrypoint_returning_string_path_is_discovered(
    tmp_path: Path, patch_entrypoints, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bare ``str`` return is treated as one path, not iterated char-by-char."""
    monkeypatch.delenv(plugin_hub._DATA_PACKS_DIR_ENV, raising=False)
    manifest = _write_manifest(tmp_path, "stringy")

    def factory_str() -> str:
        return str(manifest)

    patch_entrypoints(
        {plugin_hub._DATA_PACK_ENTRYPOINT_GROUP: [_ep("stringy", factory_str)]}
    )

    paths = plugin_hub._data_pack_manifest_paths()
    assert (manifest, plugin_hub.Origin.EXTERNAL) in paths


# ---------------------------------------------------------------------------
# Resilience — one bad pack never locks out the rest
# ---------------------------------------------------------------------------


def test_bad_entrypoint_does_not_break_others(
    tmp_path: Path,
    patch_entrypoints,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Mix one broken EP and one valid EP; only the valid manifest is kept."""
    monkeypatch.delenv(plugin_hub._DATA_PACKS_DIR_ENV, raising=False)
    good = _write_manifest(tmp_path, "good")

    def bad_load_factory() -> Path:
        raise RuntimeError("ka-boom")

    bad_ep = SimpleNamespace(
        name="bad",
        load=lambda: (_ for _ in ()).throw(ImportError("module not found")),
    )
    good_ep = _ep("good", lambda: good)
    patch_entrypoints(
        {plugin_hub._DATA_PACK_ENTRYPOINT_GROUP: [bad_ep, good_ep]}
    )

    paths = plugin_hub._data_pack_manifest_paths()
    assert (good, plugin_hub.Origin.EXTERNAL) in paths
    # bad pack contributed zero paths (its load() raised so the file we
    # would have created never existed); compare against a run with only
    # the good entry-point.
    patch_entrypoints(
        {plugin_hub._DATA_PACK_ENTRYPOINT_GROUP: [_ep("good", lambda: good)]}
    )
    baseline = plugin_hub._data_pack_manifest_paths()
    assert len(paths) == len(baseline)


def test_non_callable_entrypoint_is_skipped(
    tmp_path: Path, patch_entrypoints, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(plugin_hub._DATA_PACKS_DIR_ENV, raising=False)
    not_callable = SimpleNamespace(name="not_callable", load=lambda: "this-is-a-string")
    patch_entrypoints({plugin_hub._DATA_PACK_ENTRYPOINT_GROUP: [not_callable]})

    paths = plugin_hub._data_pack_manifest_paths()
    # only bundled manifests (no EP, no env-var) — count is finite & does not crash
    assert all(p[0].is_file() for p in paths)


def test_callable_raising_at_call_is_skipped(
    tmp_path: Path, patch_entrypoints, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(plugin_hub._DATA_PACKS_DIR_ENV, raising=False)

    def boom() -> Path:
        raise RuntimeError("nope")

    patch_entrypoints(
        {plugin_hub._DATA_PACK_ENTRYPOINT_GROUP: [_ep("boom", boom)]}
    )

    paths = plugin_hub._data_pack_manifest_paths()
    assert sum(1 for p, _ in paths if "boom" in str(p)) == 0


def test_callable_returning_bad_type_is_skipped(
    tmp_path: Path, patch_entrypoints, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A return type that is neither path-like nor iterable is logged + skipped."""
    monkeypatch.delenv(plugin_hub._DATA_PACKS_DIR_ENV, raising=False)
    patch_entrypoints(
        {plugin_hub._DATA_PACK_ENTRYPOINT_GROUP: [_ep("garbage", lambda: 42)]}
    )

    paths = plugin_hub._data_pack_manifest_paths()
    assert all(not str(p[0]).endswith("42") for p in paths)


def test_callable_returning_iterable_with_garbage_skips_only_garbage(
    tmp_path: Path, patch_entrypoints, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Within an iterable, non-path-like items are skipped one-by-one."""
    monkeypatch.delenv(plugin_hub._DATA_PACKS_DIR_ENV, raising=False)
    ok = _write_manifest(tmp_path, "ok")

    def factory_mixed():
        return [ok, 42, object()]

    patch_entrypoints(
        {plugin_hub._DATA_PACK_ENTRYPOINT_GROUP: [_ep("mixed", factory_mixed)]}
    )

    paths = plugin_hub._data_pack_manifest_paths()
    assert (ok, plugin_hub.Origin.EXTERNAL) in paths


def test_missing_file_path_is_skipped(
    tmp_path: Path, patch_entrypoints, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(plugin_hub._DATA_PACKS_DIR_ENV, raising=False)
    ghost = tmp_path / "does-not-exist.yml"
    patch_entrypoints(
        {plugin_hub._DATA_PACK_ENTRYPOINT_GROUP: [_ep("ghost", lambda: ghost)]}
    )

    paths = plugin_hub._data_pack_manifest_paths()
    assert ghost not in {p for p, _ in paths}


# ---------------------------------------------------------------------------
# Cohabitation — the new channel does not regress bundle + user-dir
# ---------------------------------------------------------------------------


def test_bundle_user_dir_and_entrypoint_cohabit(
    tmp_path: Path, patch_entrypoints, monkeypatch: pytest.MonkeyPatch
) -> None:
    """All three channels feed into the same discovery list, bundle first."""
    # user-dir
    user_dir = tmp_path / "user"
    user_dir.mkdir()
    user_pack = _write_manifest(user_dir, "user_pack")
    monkeypatch.setenv(plugin_hub._DATA_PACKS_DIR_ENV, str(user_dir))

    # entry-point
    ep_pack = _write_manifest(tmp_path, "ep_pack")
    patch_entrypoints(
        {plugin_hub._DATA_PACK_ENTRYPOINT_GROUP: [_ep("ep_pack", lambda: ep_pack)]}
    )

    paths = plugin_hub._data_pack_manifest_paths()
    # bundled manifest comes first (Origin.INTERNAL); EP and user-dir both EXTERNAL.
    internals = [p for p, o in paths if o is plugin_hub.Origin.INTERNAL]
    externals = [p for p, o in paths if o is plugin_hub.Origin.EXTERNAL]
    assert any(p.is_file() for p in internals)
    assert ep_pack in externals
    assert user_pack in externals


# ---------------------------------------------------------------------------
# Full end-to-end through the hub — a PyPI pack lights up as a DATA_PACK record
# ---------------------------------------------------------------------------


def test_end_to_end_pypi_pack_registered_as_data_pack_record(
    tmp_path: Path, patch_entrypoints, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An EP-discovered manifest becomes an ACTIVE DATA_PACK record in the hub."""
    monkeypatch.delenv(plugin_hub._DATA_PACKS_DIR_ENV, raising=False)
    manifest = _write_manifest(tmp_path, "pypi_visible_pack")

    # No host EXTENSION entry-points either — keep the inventory minimal.
    patch_entrypoints(
        {plugin_hub._DATA_PACK_ENTRYPOINT_GROUP: [_ep("pypi_visible_pack", lambda: manifest)]}
    )

    hub = plugin_hub.ExtensionHub()
    hub._discover_data_packs()
    names = {
        r.name for r in hub.records_by_kind(plugin_hub.PluginKind.DATA_PACK)
    }
    assert "pypi_visible_pack" in names

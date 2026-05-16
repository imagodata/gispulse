"""Unit tests for the PluginHub unified inventory (issue #177)."""

from __future__ import annotations

import pytest

from core import plugin_hub
from core.plugin_hub import PluginHub
from core.plugin_model import PluginKind, PluginState


class FakeEP:
    """Minimal stand-in for ``importlib.metadata.EntryPoint``."""

    def __init__(self, name: str, loader) -> None:
        self.name = name
        self.value = f"pkg_{name}:obj"
        self._loader = loader

    def load(self):
        return self._loader()


def _patch_entry_points(monkeypatch, groups: dict[str, list]) -> None:
    """Make ``plugin_hub.entry_points`` resolve from a fixed group map."""
    monkeypatch.setattr(
        plugin_hub, "entry_points", lambda group: groups.get(group, [])
    )


@pytest.fixture(autouse=True)
def _reset_hub():
    PluginHub.reset()
    yield
    PluginHub.reset()


# --------------------------------------------------------------------------
# _discover_records — coverage of the 11 groups
# --------------------------------------------------------------------------


def test_inventory_maps_each_group_to_its_kind(monkeypatch) -> None:
    _patch_entry_points(
        monkeypatch,
        {
            "gispulse.routers": [FakeEP("admin", object)],
            "gispulse.mcp_tools": [FakeEP("permis", object)],
            "gispulse.capabilities": [FakeEP("h3", lambda: (lambda: None))],
            "gispulse.data_sources": [FakeEP("cadastre", object)],
            "gispulse.data_sinks": [FakeEP("postgis", object)],
            "gispulse.protocols": [FakeEP("wfs", object)],
        },
    )
    hub = PluginHub()
    hub._discover_records()

    by_name = {r.name: r for r in hub.records}
    assert by_name["admin"].kind is PluginKind.EXTENSION
    assert by_name["permis"].kind is PluginKind.EXTENSION  # mcp_tools collapse too
    assert by_name["h3"].kind is PluginKind.CAPABILITY
    assert by_name["cadastre"].kind is PluginKind.SOURCE
    assert by_name["postgis"].kind is PluginKind.SINK
    assert by_name["wfs"].kind is PluginKind.PROTOCOL


def test_inventory_empty_when_no_entry_points(monkeypatch) -> None:
    _patch_entry_points(monkeypatch, {})
    hub = PluginHub()
    hub._discover_records()
    assert hub.records == []


# --------------------------------------------------------------------------
# Lifecycle state — activate / fail
# --------------------------------------------------------------------------


def test_loadable_entry_point_becomes_active(monkeypatch) -> None:
    sentinel = object()
    _patch_entry_points(
        monkeypatch, {"gispulse.data_sources": [FakeEP("ok", lambda: sentinel)]}
    )
    hub = PluginHub()
    hub._discover_records()

    rec = hub.records[0]
    assert rec.state is PluginState.ACTIVE
    assert rec.obj is sentinel
    assert rec.detail == ""
    assert rec.available is True


def test_failing_entry_point_is_isolated_as_failed(monkeypatch) -> None:
    def boom():
        raise ImportError("missing native dep")

    _patch_entry_points(
        monkeypatch,
        {
            "gispulse.capabilities": [
                FakeEP("good", object),
                FakeEP("broken", boom),
            ]
        },
    )
    hub = PluginHub()
    hub._discover_records()

    states = {r.name: r for r in hub.records}
    assert states["good"].state is PluginState.ACTIVE
    assert states["broken"].state is PluginState.FAILED
    assert "missing native dep" in states["broken"].detail
    assert states["broken"].obj is None  # a bad plugin never crashes discovery


# --------------------------------------------------------------------------
# records_by_kind helper
# --------------------------------------------------------------------------


def test_records_by_kind_filters(monkeypatch) -> None:
    _patch_entry_points(
        monkeypatch,
        {
            "gispulse.data_sources": [FakeEP("a", object), FakeEP("b", object)],
            "gispulse.routers": [FakeEP("admin", object)],
        },
    )
    hub = PluginHub()
    hub._discover_records()

    sources = hub.records_by_kind(PluginKind.SOURCE)
    assert sorted(r.name for r in sources) == ["a", "b"]
    assert len(hub.records_by_kind(PluginKind.EXTENSION)) == 1
    assert hub.records_by_kind(PluginKind.SINK) == []


# --------------------------------------------------------------------------
# Integration through the singleton + reset
# --------------------------------------------------------------------------


def test_get_runs_discovery_and_reset_clears_records(monkeypatch) -> None:
    _patch_entry_points(
        monkeypatch, {"gispulse.protocols": [FakeEP("stac", object)]}
    )
    hub = PluginHub.get()
    assert [r.name for r in hub.records] == ["stac"]

    PluginHub.reset()
    assert PluginHub._instance is None
    hub2 = PluginHub.get()
    assert hub2 is not hub

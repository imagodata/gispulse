"""Tests for ``gispulse.runtime.source_watch`` — the #197 wiring.

Covers the bridge that turns a ``SourceWatcherRegistry`` revision change
into an ``ActionDispatcher`` dispatch, and the end-to-end builder that
the watch loop calls.
"""

from __future__ import annotations

import textwrap

import pytest

from gispulse.runtime.source_watch import (
    SourceChangeBridge,
    build_source_watcher,
    parse_source_uri,
    resolve_via_registry,
)


# ---------------------------------------------------------------------------
# Fakes + helpers
# ---------------------------------------------------------------------------


class FakeSource:
    """A DataSource whose revision() walks a predefined sequence."""

    def __init__(self, name: str, revisions: list[str | None]) -> None:
        self.name = name
        self._revisions = list(revisions)
        self.calls = 0

    def revision(self, entry_id: str) -> str | None:
        idx = min(self.calls, len(self._revisions) - 1)
        self.calls += 1
        return self._revisions[idx]


class FakeDispatcher:
    """Records every dispatch_all call."""

    def __init__(self) -> None:
        self.calls: list = []

    def dispatch_all(self, actions: list, context) -> int:
        self.calls.append((actions, context))
        return len(actions)


def _triggers(yaml_doc: str) -> list:
    """Parse a full ``triggers.yaml`` document into domain Trigger objects."""
    from gispulse.runtime.config_loader import parse_config_text, to_triggers

    cfg = parse_config_text(textwrap.dedent(yaml_doc).strip(), resolve_gpkg=False)
    return to_triggers(cfg)


_ONE_SOURCE_TRIGGER = """
    version: 1
    gpkg: ./unused.gpkg
    triggers:
      - name: refresh
        on:
          source_changed: cadastre://parcelles
        actions:
          - type: log_event
"""


# ---------------------------------------------------------------------------
# parse_source_uri
# ---------------------------------------------------------------------------


def test_parse_source_uri_valid() -> None:
    assert parse_source_uri("cadastre://parcelles") == ("cadastre", "parcelles")


def test_parse_source_uri_nested_entry() -> None:
    assert parse_source_uri("ign://bdtopo/batiment") == ("ign", "bdtopo/batiment")


@pytest.mark.parametrize("bad", ["", "parcelles", "://entry", "cadastre://"])
def test_parse_source_uri_invalid(bad: str) -> None:
    with pytest.raises(ValueError, match="invalid source URI"):
        parse_source_uri(bad)


# ---------------------------------------------------------------------------
# resolve_via_registry
# ---------------------------------------------------------------------------


def test_resolve_via_registry() -> None:
    from core.sources import SOURCES

    src = FakeSource("demo", ["r1"])
    SOURCES.register(src)
    try:
        resolved, entry = resolve_via_registry("demo://things")
        assert resolved is src
        assert entry == "things"
    finally:
        SOURCES.clear()


def test_resolve_via_registry_unknown_source() -> None:
    from core.sources import SOURCES

    SOURCES.clear()
    with pytest.raises(KeyError, match="no data source named 'ghost'"):
        resolve_via_registry("ghost://x")


# ---------------------------------------------------------------------------
# SourceChangeBridge
# ---------------------------------------------------------------------------


def test_bridge_dispatches_matching_trigger() -> None:
    triggers = _triggers(_ONE_SOURCE_TRIGGER)
    dispatcher = FakeDispatcher()
    bridge = SourceChangeBridge(triggers, dispatcher)

    bridge.broadcast(
        "source.changed",
        {"source": "cadastre://parcelles", "revision": "2026-07"},
    )

    assert bridge.fired == 1
    assert len(dispatcher.calls) == 1
    actions, ctx = dispatcher.calls[0]
    assert ctx.operation == "source_changed"
    assert ctx.new_attrs["revision"] == "2026-07"


def test_bridge_ignores_non_matching_source() -> None:
    bridge = SourceChangeBridge(_triggers(_ONE_SOURCE_TRIGGER), FakeDispatcher())
    bridge.broadcast(
        "source.changed", {"source": "ign://bdtopo", "revision": "x"}
    )
    assert bridge.fired == 0


def test_bridge_ignores_non_source_event() -> None:
    dispatcher = FakeDispatcher()
    bridge = SourceChangeBridge(_triggers(_ONE_SOURCE_TRIGGER), dispatcher)
    bridge.broadcast("dml.changed", {"source": "cadastre://parcelles"})
    bridge.broadcast("source.changed", None)
    assert bridge.fired == 0
    assert dispatcher.calls == []


def test_bridge_skips_disabled_trigger() -> None:
    triggers = _triggers(_ONE_SOURCE_TRIGGER)
    triggers[0].enabled = False
    bridge = SourceChangeBridge(triggers, FakeDispatcher())
    bridge.broadcast(
        "source.changed", {"source": "cadastre://parcelles", "revision": "x"}
    )
    assert bridge.fired == 0


# ---------------------------------------------------------------------------
# build_source_watcher
# ---------------------------------------------------------------------------


def test_build_source_watcher_none_without_source_triggers() -> None:
    """A DML-only config yields no source watcher."""
    dml = _triggers(
        """
        version: 1
        gpkg: ./unused.gpkg
        triggers:
          - name: dml_only
            table: parcels
            actions: []
        """
    )
    assert build_source_watcher(dml, FakeDispatcher()) is None


def test_build_source_watcher_registers_entry() -> None:
    src = FakeSource("cadastre", ["baseline"])
    watcher = build_source_watcher(
        _triggers(_ONE_SOURCE_TRIGGER),
        FakeDispatcher(),
        resolver=lambda uri: (src, "parcelles"),
    )
    assert watcher is not None
    assert watcher.list_watched() == ["cadastre:parcelles"]


def test_build_source_watcher_skips_unresolved_uri() -> None:
    def boom(uri: str):
        raise KeyError("not registered")

    watcher = build_source_watcher(
        _triggers(_ONE_SOURCE_TRIGGER), FakeDispatcher(), resolver=boom
    )
    # Watcher is still built (a source trigger exists) but has no entry.
    assert watcher is not None
    assert watcher.list_watched() == []


def test_source_watcher_poll_fires_trigger_end_to_end() -> None:
    """The headline #197 path: a new revision dispatches the trigger."""
    # Baseline revision captured at register() = 'r1'; next poll = 'r2'.
    src = FakeSource("cadastre", ["r1", "r2"])
    dispatcher = FakeDispatcher()
    watcher = build_source_watcher(
        _triggers(_ONE_SOURCE_TRIGGER),
        dispatcher,
        resolver=lambda uri: (src, "parcelles"),
    )
    assert watcher is not None

    # First poll observes 'r2' ≠ baseline 'r1' → one source.changed.
    changes = watcher.poll()
    assert len(changes) == 1
    assert changes[0]["revision"] == "r2"
    assert len(dispatcher.calls) == 1  # trigger dispatched

    # Second poll observes 'r2' again (sequence exhausted) → no change.
    assert watcher.poll() == []
    assert len(dispatcher.calls) == 1

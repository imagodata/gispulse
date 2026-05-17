"""Unit tests for SourceWatcherRegistry (issue #187)."""

from __future__ import annotations

import pytest

from persistence.source_watcher import (
    SourceWatcherRegistry,
    interval_from_frequency,
)


class FakeSource:
    """A DataSource stub with a mutable revision token."""

    def __init__(self, name: str, revision: str | None) -> None:
        self.name = name
        self._revision = revision
        self.calls = 0

    def revision(self, entry_id: str) -> str | None:
        self.calls += 1
        return self._revision


class BrokenSource:
    name = "broken"

    def revision(self, entry_id: str) -> str | None:
        raise ConnectionError("upstream unreachable")


class FakeHub:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def broadcast(self, event_type: str, data=None) -> None:
        self.events.append((event_type, data))


# --------------------------------------------------------------------------
# interval_from_frequency
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("freq", "expected"),
    [
        ("quotidien", 3600.0),
        ("ANNUEL", 24 * 3600.0),
        ("pluriannuel", 24 * 3600.0),
        (None, 6 * 3600.0),
        ("inconnu", 6 * 3600.0),
    ],
)
def test_interval_from_frequency(freq, expected) -> None:
    assert interval_from_frequency(freq) == expected


# --------------------------------------------------------------------------
# register
# --------------------------------------------------------------------------


def test_register_captures_baseline_and_returns_key() -> None:
    reg = SourceWatcherRegistry()
    key = reg.register(FakeSource("cadastre", "2026-01"), "parcelles", interval_s=3600)
    assert key == "cadastre:parcelles"
    assert reg.list_watched() == ["cadastre:parcelles"]


def test_register_rejects_sub_minute_interval() -> None:
    reg = SourceWatcherRegistry()
    with pytest.raises(ValueError, match=">= 60"):
        reg.register(FakeSource("x", "r1"), "e", interval_s=5)


def test_register_uses_frequency_when_no_interval() -> None:
    reg = SourceWatcherRegistry()
    reg.register(FakeSource("x", "r1"), "e", frequency="quotidien")
    assert reg.list_watched() == ["x:e"]


def test_unregister() -> None:
    reg = SourceWatcherRegistry()
    reg.register(FakeSource("x", "r1"), "e", interval_s=3600)
    reg.unregister("x:e")
    assert reg.list_watched() == []


# --------------------------------------------------------------------------
# poll — change detection + event emission
# --------------------------------------------------------------------------


def test_poll_no_change_emits_nothing() -> None:
    hub = FakeHub()
    reg = SourceWatcherRegistry(event_hub=hub)
    reg.register(FakeSource("cadastre", "2026-01"), "parcelles", interval_s=3600)
    assert reg.poll() == []
    assert hub.events == []


def test_poll_detects_new_millesime_and_broadcasts() -> None:
    hub = FakeHub()
    reg = SourceWatcherRegistry(event_hub=hub)
    source = FakeSource("cadastre", "2026-01")
    reg.register(source, "parcelles", interval_s=3600)

    # A new millésime is published upstream.
    source._revision = "2026-02"
    changes = reg.poll()

    assert len(changes) == 1
    change = changes[0]
    assert change["source"] == "cadastre://parcelles"
    assert change["revision"] == "2026-02"
    assert change["previous"] == "2026-01"

    assert hub.events == [("source.changed", change)]


def test_poll_is_idempotent_after_a_change() -> None:
    hub = FakeHub()
    reg = SourceWatcherRegistry(event_hub=hub)
    source = FakeSource("bdtopo", "v1")
    reg.register(source, "batiments", interval_s=3600)

    source._revision = "v2"
    assert len(reg.poll()) == 1   # change detected once
    assert reg.poll() == []        # same revision — no re-fire
    assert len(hub.events) == 1


def test_poll_isolates_a_failing_source() -> None:
    hub = FakeHub()
    reg = SourceWatcherRegistry(event_hub=hub)
    reg._entries["broken:e"] = reg._entries.get("broken:e") or _broken_entry()
    ok = FakeSource("ok", "r1")
    reg.register(ok, "e", interval_s=3600)

    ok._revision = "r2"
    changes = reg.poll()  # broken source raises, ok source still detected

    assert [c["source"] for c in changes] == ["ok://e"]


def _broken_entry():
    from persistence.source_watcher import _WatchEntry

    return _WatchEntry(source=BrokenSource(), entry_id="e", interval_s=3600.0,
                       last_revision="r0")


# --------------------------------------------------------------------------
# daemon lifecycle
# --------------------------------------------------------------------------


def test_daemon_start_stop() -> None:
    reg = SourceWatcherRegistry()
    reg.register(FakeSource("x", "r1"), "e", interval_s=3600)
    assert reg.is_running() is False
    reg.start(tick_s=3600)
    try:
        assert reg.is_running() is True
    finally:
        reg.stop()
    assert reg.is_running() is False

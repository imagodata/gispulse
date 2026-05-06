"""Unit tests for :class:`persistence.watcher_registry.WatcherRegistry`.

These tests open real GeoPackage files in a tmp_path so the registry can
exercise its full register / unregister / shutdown lifecycle. They run
in <1s because the watcher's poll thread does no work when there are no
pending changes.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

import pytest

from persistence.gpkg_schema import bootstrap_gpkg_project
from persistence.watcher_registry import WatcherRegistry


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


class _RecordingHub:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.events: list[tuple[str, dict]] = []

    def broadcast(self, event_type: str, data: dict | None = None) -> None:
        with self._lock:
            self.events.append((event_type, data or {}))


def _make_gpkg(path: Path) -> None:
    """Bootstrap a minimal GPKG with a tracked ``parcels`` layer."""
    conn = sqlite3.connect(str(path))
    try:
        bootstrap_gpkg_project(conn)
        conn.execute(
            'CREATE TABLE IF NOT EXISTS "parcels" '
            "(fid INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT)"
        )
        conn.commit()
    finally:
        conn.close()


def _wait_until(predicate, timeout: float = 2.0, step: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(step)
    return predicate()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRegisterUnregister:
    def test_register_starts_watcher(self, tmp_path: Path) -> None:
        path = tmp_path / "a.gpkg"
        _make_gpkg(path)
        hub = _RecordingHub()
        reg = WatcherRegistry(event_hub=hub)
        try:
            reg.register("ds-1", path)
            assert reg.is_registered("ds-1")
            assert "ds-1" in reg.list_registered()
        finally:
            reg.shutdown_all()

    def test_register_idempotent_same_path(self, tmp_path: Path) -> None:
        path = tmp_path / "a.gpkg"
        _make_gpkg(path)
        hub = _RecordingHub()
        reg = WatcherRegistry(event_hub=hub)
        try:
            reg.register("ds-1", path)
            reg.register("ds-1", path)  # no-op
            reg.register("ds-1", path)  # no-op
            assert reg.list_registered() == ["ds-1"]
        finally:
            reg.shutdown_all()

    def test_register_rejects_different_path(self, tmp_path: Path) -> None:
        path1 = tmp_path / "a.gpkg"
        path2 = tmp_path / "b.gpkg"
        _make_gpkg(path1)
        _make_gpkg(path2)
        hub = _RecordingHub()
        reg = WatcherRegistry(event_hub=hub)
        try:
            reg.register("ds-1", path1)
            with pytest.raises(ValueError):
                reg.register("ds-1", path2)
        finally:
            reg.shutdown_all()

    def test_unregister_stops_watcher(self, tmp_path: Path) -> None:
        path = tmp_path / "a.gpkg"
        _make_gpkg(path)
        hub = _RecordingHub()
        reg = WatcherRegistry(event_hub=hub)
        reg.register("ds-1", path)
        assert reg.is_registered("ds-1")
        reg.unregister("ds-1")
        assert not reg.is_registered("ds-1")
        assert reg.list_registered() == []

    def test_unregister_unknown_is_noop(self, tmp_path: Path) -> None:
        hub = _RecordingHub()
        reg = WatcherRegistry(event_hub=hub)
        # Must not raise.
        reg.unregister("ds-does-not-exist")


class TestMultiDataset:
    def test_register_multiple_datasets(self, tmp_path: Path) -> None:
        paths = [tmp_path / f"ds{i}.gpkg" for i in range(3)]
        for p in paths:
            _make_gpkg(p)
        hub = _RecordingHub()
        reg = WatcherRegistry(event_hub=hub)
        try:
            for i, p in enumerate(paths):
                reg.register(f"ds-{i}", p)
            assert sorted(reg.list_registered()) == ["ds-0", "ds-1", "ds-2"]
        finally:
            reg.shutdown_all()

    def test_each_dataset_broadcasts_to_shared_hub(self, tmp_path: Path) -> None:
        """Two registered watchers fan out to the same EventHub. We wire
        triggers on each GPKG, INSERT a row into each via a fresh SQLite
        handle, and confirm BOTH dml.changed events land on the hub.
        """
        from persistence.gpkg_schema import install_change_tracking

        path1 = tmp_path / "a.gpkg"
        path2 = tmp_path / "b.gpkg"
        _make_gpkg(path1)
        _make_gpkg(path2)

        # Install triggers on each layer (matches what the
        # /enable_tracking endpoint does before register()).
        for p in (path1, path2):
            conn = sqlite3.connect(str(p))
            try:
                install_change_tracking(conn, "parcels")
            finally:
                conn.close()

        hub = _RecordingHub()
        reg = WatcherRegistry(event_hub=hub)
        try:
            reg.register("ds-1", path1)
            reg.register("ds-2", path2)

            # External writer on ds-1
            ext = sqlite3.connect(str(path1))
            try:
                ext.execute('INSERT INTO "parcels"(name) VALUES (?)', ("a",))
                ext.commit()
            finally:
                ext.close()

            # External writer on ds-2
            ext = sqlite3.connect(str(path2))
            try:
                ext.execute('INSERT INTO "parcels"(name) VALUES (?)', ("b",))
                ext.commit()
            finally:
                ext.close()

            assert _wait_until(
                lambda: sum(1 for e in hub.events if e[0] == "dml.changed") >= 2,
                timeout=3.0,
            )
        finally:
            reg.shutdown_all()


class TestShutdown:
    def test_shutdown_all_stops_every_watcher(self, tmp_path: Path) -> None:
        paths = [tmp_path / f"ds{i}.gpkg" for i in range(3)]
        for p in paths:
            _make_gpkg(p)
        hub = _RecordingHub()
        reg = WatcherRegistry(event_hub=hub)
        for i, p in enumerate(paths):
            reg.register(f"ds-{i}", p)

        reg.shutdown_all()
        assert reg.list_registered() == []

    def test_shutdown_all_idempotent(self, tmp_path: Path) -> None:
        path = tmp_path / "a.gpkg"
        _make_gpkg(path)
        hub = _RecordingHub()
        reg = WatcherRegistry(event_hub=hub)
        reg.register("ds-1", path)
        reg.shutdown_all()
        reg.shutdown_all()  # no-op
        assert reg.list_registered() == []


class TestGetEngine:
    def test_get_engine_returns_engine(self, tmp_path: Path) -> None:
        path = tmp_path / "a.gpkg"
        _make_gpkg(path)
        hub = _RecordingHub()
        reg = WatcherRegistry(event_hub=hub)
        try:
            reg.register("ds-1", path)
            engine = reg.get_engine("ds-1")
            assert engine is not None
            assert engine.backend_name == "gpkg"
        finally:
            reg.shutdown_all()

    def test_get_engine_missing_returns_none(self) -> None:
        hub = _RecordingHub()
        reg = WatcherRegistry(event_hub=hub)
        assert reg.get_engine("ds-missing") is None


# ---------------------------------------------------------------------------
# #95 (P0-3 dashboard) — observability counters
# ---------------------------------------------------------------------------


class TestStatsSnapshot:
    """Cover the ``get_stats`` / ``list_with_stats`` paths added for #95."""

    def test_get_stats_unknown_returns_none(self) -> None:
        hub = _RecordingHub()
        reg = WatcherRegistry(event_hub=hub)
        assert reg.get_stats("ds-missing") is None

    def test_get_stats_initial_snapshot(self, tmp_path: Path) -> None:
        """Right after register(), counters are zero, ``running`` is True."""
        path = tmp_path / "a.gpkg"
        _make_gpkg(path)
        hub = _RecordingHub()
        reg = WatcherRegistry(event_hub=hub)
        try:
            reg.register("ds-1", path, layers=["parcels"])
            stats = reg.get_stats("ds-1")
            assert stats is not None
            assert stats["dataset_id"] == "ds-1"
            assert stats["running"] is True
            assert stats["tick_count"] == 0
            assert stats["fire_count"] == 0
            assert stats["error_count"] == 0
            assert stats["last_error_msg"] is None
            assert stats["layers"] == ["parcels"]
            # Engine path round-trips through the registry.
            assert stats["gpkg_path"] == str(path)
            # Config knobs surface correctly (defaults from register()).
            assert stats["poll_interval"] == 0.2
            assert stats["batch_limit"] == 100
            assert stats["bulk_threshold"] == 0
            assert stats["bulk_eval"] == "skip"
            assert stats["started_at"] is not None
        finally:
            reg.shutdown_all()

    def test_tick_count_increments(self, tmp_path: Path) -> None:
        """The polling thread bumps ``tick_count`` even with no rows."""
        path = tmp_path / "a.gpkg"
        _make_gpkg(path)
        hub = _RecordingHub()
        reg = WatcherRegistry(event_hub=hub)
        try:
            # Short poll interval so we observe ticks quickly.
            reg.register("ds-1", path, poll_interval=0.05)
            assert _wait_until(
                lambda: (reg.get_stats("ds-1") or {}).get("tick_count", 0) >= 2,
                timeout=2.0,
            )
            stats = reg.get_stats("ds-1")
            assert stats is not None
            assert stats["tick_count"] >= 2
            # No rows touched the change log → rows_processed is still 0.
            assert stats["rows_processed"] == 0
            assert stats["last_tick_at"] is not None
        finally:
            reg.shutdown_all()

    def test_rows_processed_after_insert(self, tmp_path: Path) -> None:
        """A real INSERT bumps ``rows_processed`` once the watcher drains."""
        from persistence.gpkg_schema import install_change_tracking

        path = tmp_path / "a.gpkg"
        _make_gpkg(path)
        conn = sqlite3.connect(str(path))
        try:
            install_change_tracking(conn, "parcels")
        finally:
            conn.close()

        hub = _RecordingHub()
        reg = WatcherRegistry(event_hub=hub)
        try:
            reg.register("ds-1", path, poll_interval=0.05)

            ext = sqlite3.connect(str(path))
            try:
                ext.execute('INSERT INTO "parcels"(name) VALUES (?)', ("x",))
                ext.commit()
            finally:
                ext.close()

            assert _wait_until(
                lambda: (reg.get_stats("ds-1") or {}).get("rows_processed", 0) >= 1,
                timeout=3.0,
            )
            stats = reg.get_stats("ds-1")
            assert stats is not None
            assert stats["rows_processed"] >= 1
            # No triggers wired → fire_count remains 0.
            assert stats["fire_count"] == 0
        finally:
            reg.shutdown_all()

    def test_list_with_stats_returns_one_entry_per_dataset(
        self, tmp_path: Path
    ) -> None:
        paths = [tmp_path / f"ds{i}.gpkg" for i in range(3)]
        for p in paths:
            _make_gpkg(p)
        hub = _RecordingHub()
        reg = WatcherRegistry(event_hub=hub)
        try:
            for i, p in enumerate(paths):
                reg.register(f"ds-{i}", p, layers=[f"layer-{i}"])
            items = reg.list_with_stats()
            assert len(items) == 3
            ids = sorted(it["dataset_id"] for it in items)
            assert ids == ["ds-0", "ds-1", "ds-2"]
            # Layers snapshot folds in correctly per dataset.
            for it in items:
                idx = it["dataset_id"].rsplit("-", 1)[-1]
                assert it["layers"] == [f"layer-{idx}"]
        finally:
            reg.shutdown_all()

    def test_list_with_stats_empty_when_no_registrations(self) -> None:
        hub = _RecordingHub()
        reg = WatcherRegistry(event_hub=hub)
        assert reg.list_with_stats() == []

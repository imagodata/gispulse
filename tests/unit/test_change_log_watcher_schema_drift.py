"""B-13 (#103, v1.5.3) — schema-drift watchdog.

The watcher periodically re-hashes ``PRAGMA table_info`` for every
tracked layer; on mismatch it drops + re-installs change tracking and
broadcasts a ``schema.changed`` event so subscribers (portal, plugin)
can refresh their UI.

Reproducer: a QGIS user adds / drops / renames a column via Field
Calculator. Pre-B-13 the AFTER UPDATE trigger's baked ``new_values``
JSON references a stale column list — further edits crash with
``no such column`` or silently omit the new column. The watchdog
rebuilds the trigger DDL the next time it ticks (default every 5 s).
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from gispulse.persistence.change_log_watcher import ChangeLogWatcher
from gispulse.persistence.gpkg_schema import (
    bootstrap_gpkg_project,
    install_change_tracking,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _RecordingHub:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.events: list[tuple[str, dict[str, Any]]] = []

    def broadcast(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        with self._lock:
            self.events.append((event_type, data or {}))


class _GpkgEngine:
    """Minimal engine façade over a real GPKG SQLite connection.

    Exposes the ``_get_conn`` shim the watcher uses for the drift check
    and ``_load_row_values`` plus the polling API ``get_pending_changes``
    / ``mark_changes_processed``.
    """

    backend_name = "gpkg"

    def __init__(self, path: Path) -> None:
        self._path = path
        self.acked: list[int] = []

    # --- Watcher polling API --------------------------------------------------

    def get_pending_changes(self, limit: int) -> list[dict]:
        with self._open() as conn:
            rows = conn.execute(
                "SELECT id, table_name, operation, row_pk, changed_at "
                "FROM _gispulse_change_log "
                "WHERE processed = 0 ORDER BY id LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def mark_changes_processed(self, up_to_id: int) -> int:
        self.acked.append(up_to_id)
        with self._open() as conn:
            conn.execute(
                "UPDATE _gispulse_change_log SET processed = 1 WHERE id <= ?",
                (up_to_id,),
            )
            conn.commit()
        return 1

    # --- Watcher schema-drift / row-load shim ---------------------------------

    def _get_conn(self) -> sqlite3.Connection:
        # The watcher does NOT close the connection — it does direct
        # SELECT / DDL on it. Match the shape of the real gpkg_engine
        # which returns a long-lived connection.
        return self._open(close_after=False)

    def _open(self, *, close_after: bool = True) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path), isolation_level=None)
        conn.row_factory = sqlite3.Row
        if close_after:

            class _Ctx:
                def __init__(self, c):
                    self._c = c

                def __enter__(self):
                    return self._c

                def __exit__(self, exc_type, exc, tb):
                    self._c.close()

            return _Ctx(conn)  # type: ignore[return-value]
        return conn


@pytest.fixture
def gpkg(tmp_path: Path) -> Path:
    path = tmp_path / "test.gpkg"
    conn = sqlite3.connect(str(path))
    bootstrap_gpkg_project(conn)
    conn.execute(
        'CREATE TABLE "parcels" '
        "(fid INTEGER PRIMARY KEY, name TEXT, area REAL)"
    )
    conn.commit()
    install_change_tracking(conn, "parcels")
    conn.close()
    return path


def _make_watcher(
    *,
    gpkg_path: Path,
    drift_interval_s: float = 0.001,
    hub: _RecordingHub | None = None,
) -> tuple[ChangeLogWatcher, _RecordingHub, _GpkgEngine]:
    hub = hub or _RecordingHub()
    engine = _GpkgEngine(gpkg_path)
    watcher = ChangeLogWatcher(
        engine=engine,
        event_hub=hub,
        dataset_id="ds-test",
        poll_interval=0.05,
        batch_limit=100,
        schema_drift_check_interval_s=drift_interval_s,
    )
    return watcher, hub, engine


# ---------------------------------------------------------------------------
# Throttling
# ---------------------------------------------------------------------------


def test_drift_disabled_when_interval_zero(gpkg: Path) -> None:
    """``schema_drift_check_interval_s=0`` disables the watchdog. The
    watcher must not call ``_get_conn`` for drift checking."""
    watcher, hub, _engine = _make_watcher(
        gpkg_path=gpkg, drift_interval_s=0.0
    )
    # Add a column to provoke drift — but the watchdog is off so no
    # event should fire.
    conn = sqlite3.connect(str(gpkg))
    conn.execute('ALTER TABLE "parcels" ADD COLUMN extra TEXT')
    conn.commit()
    conn.close()

    watcher._tick()  # noqa: SLF001
    types = [e[0] for e in hub.events]
    assert "schema.changed" not in types


def test_drift_throttled_within_interval(gpkg: Path) -> None:
    """Two ticks back-to-back with a 60s interval must check at most
    once. The second tick is throttled."""
    watcher, _hub, _engine = _make_watcher(
        gpkg_path=gpkg, drift_interval_s=60.0
    )
    # First tick caches the layer; second tick should skip the check.
    watcher._tick()  # noqa: SLF001
    cached_after_first = dict(watcher._schema_hashes)  # noqa: SLF001
    # Mutate the schema between ticks.
    conn = sqlite3.connect(str(gpkg))
    conn.execute('ALTER TABLE "parcels" ADD COLUMN extra TEXT')
    conn.commit()
    conn.close()
    watcher._tick()  # noqa: SLF001
    # The second tick was throttled so the cached hash never refreshed.
    assert watcher._schema_hashes == cached_after_first  # noqa: SLF001


# ---------------------------------------------------------------------------
# Drift detection — first sighting is silent
# ---------------------------------------------------------------------------


def test_first_tick_caches_without_event(gpkg: Path) -> None:
    """The watcher caches schema hashes on first sight without firing
    ``schema.changed`` (otherwise every boot would replay events)."""
    watcher, hub, _engine = _make_watcher(gpkg_path=gpkg)
    watcher._tick()  # noqa: SLF001
    types = [e[0] for e in hub.events]
    assert "schema.changed" not in types
    # ``parcels`` should be cached.
    assert "parcels" in watcher._schema_hashes  # noqa: SLF001


# ---------------------------------------------------------------------------
# Drift detection — ALTER TABLE add / drop / rename
# ---------------------------------------------------------------------------


def test_add_column_fires_schema_changed(gpkg: Path) -> None:
    """ALTER TABLE ADD COLUMN flips the hash → drift event + repaired
    triggers."""
    watcher, hub, _engine = _make_watcher(gpkg_path=gpkg)
    watcher._tick()  # noqa: SLF001 — first sighting (silent)

    # ADD COLUMN behind the watcher's back, e.g. via Field Calculator.
    conn = sqlite3.connect(str(gpkg))
    conn.execute('ALTER TABLE "parcels" ADD COLUMN computed_area REAL')
    conn.commit()
    conn.close()

    drifted = watcher._drift_check_tick()  # noqa: SLF001
    assert drifted == ["parcels"]
    # Event broadcast.
    drift_events = [e for e in hub.events if e[0] == "schema.changed"]
    assert len(drift_events) == 1
    payload = drift_events[0][1]
    assert payload["table"] == "parcels"
    assert payload["change_type"] == "columns_changed"
    assert payload["dataset_id"] == "ds-test"


def test_rename_column_fires_schema_changed(gpkg: Path) -> None:
    """SQLite supports ``ALTER TABLE ... RENAME COLUMN`` since 3.25;
    the rename flips PRAGMA table_info, so the watchdog catches it."""
    watcher, hub, _engine = _make_watcher(gpkg_path=gpkg)
    watcher._tick()  # noqa: SLF001 — first sighting

    conn = sqlite3.connect(str(gpkg))
    conn.execute('ALTER TABLE "parcels" RENAME COLUMN name TO label')
    conn.commit()
    conn.close()

    drifted = watcher._drift_check_tick()  # noqa: SLF001
    assert drifted == ["parcels"]
    assert any(e[0] == "schema.changed" for e in hub.events)


def test_drop_column_fires_schema_changed(gpkg: Path) -> None:
    """ALTER TABLE DROP COLUMN (SQLite ≥ 3.35) flips the hash."""
    watcher, hub, _engine = _make_watcher(gpkg_path=gpkg)
    watcher._tick()  # noqa: SLF001 — first sighting

    conn = sqlite3.connect(str(gpkg))
    try:
        conn.execute('ALTER TABLE "parcels" DROP COLUMN area')
    except sqlite3.OperationalError:
        pytest.skip("SQLite build does not support DROP COLUMN")
    conn.commit()
    conn.close()

    drifted = watcher._drift_check_tick()  # noqa: SLF001
    assert drifted == ["parcels"]


# ---------------------------------------------------------------------------
# Drift repair — triggers reflect the new column list
# ---------------------------------------------------------------------------


def test_drift_repair_picks_up_new_column_in_payload(gpkg: Path) -> None:
    """After a column is added and the watchdog repairs the trigger,
    a subsequent UPDATE must include the new column in ``new_values``."""
    watcher, _hub, _engine = _make_watcher(gpkg_path=gpkg)
    watcher._tick()  # noqa: SLF001 — first sighting

    conn = sqlite3.connect(str(gpkg))
    conn.execute('ALTER TABLE "parcels" ADD COLUMN computed_area REAL')
    conn.commit()
    conn.close()

    watcher._drift_check_tick()  # noqa: SLF001 — repair

    # Now write a row with the new column populated and check the
    # change-log captured it.
    conn = sqlite3.connect(str(gpkg))
    conn.execute(
        'INSERT INTO "parcels"(fid, name, area, computed_area) '
        "VALUES (1, 'A', 10.0, 100.0)"
    )
    conn.commit()
    rows = conn.execute(
        "SELECT new_values FROM _gispulse_change_log "
        "WHERE table_name='parcels' ORDER BY id DESC LIMIT 1"
    ).fetchall()
    assert len(rows) == 1
    new_values_json = rows[0][0] or ""
    assert "computed_area" in new_values_json, (
        "post-repair trigger DDL must include the new column "
        f"(got: {new_values_json!r})"
    )
    conn.close()


# ---------------------------------------------------------------------------
# Idempotence — no drift means no event
# ---------------------------------------------------------------------------


def test_no_drift_no_event(gpkg: Path) -> None:
    """Two consecutive drift checks on a stable schema must be silent."""
    watcher, hub, _engine = _make_watcher(gpkg_path=gpkg)
    watcher._tick()  # noqa: SLF001 — first sighting
    drifted = watcher._drift_check_tick()  # noqa: SLF001
    assert drifted == []
    types = [e[0] for e in hub.events]
    assert "schema.changed" not in types


# ---------------------------------------------------------------------------
# Engine without _get_conn shim is a soft no-op
# ---------------------------------------------------------------------------


def test_engine_without_get_conn_is_silent(gpkg: Path) -> None:
    """Engines that don't expose ``_get_conn`` (rare — most concrete
    engines do) must not crash the watchdog."""

    class _NoConnEngine:
        backend_name = "noop"

        def get_pending_changes(self, _limit):
            return []

        def mark_changes_processed(self, _up):
            return 0

    hub = _RecordingHub()
    watcher = ChangeLogWatcher(
        engine=_NoConnEngine(),  # type: ignore[arg-type]
        event_hub=hub,
        dataset_id="ds-test",
        schema_drift_check_interval_s=0.001,
    )
    # Force the wall-clock past the throttle window.
    watcher._last_drift_check_ts = 0.0  # noqa: SLF001
    time.sleep(0.005)
    watcher._tick()  # noqa: SLF001
    # No crash, no events.
    assert hub.events == []

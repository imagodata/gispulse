"""
Beta — Lot 2 shadow zones.

Marco's golden-path suite (10/10 unit + 3/3 integration) covers the happy
path of ``ChangeLogWatcher`` → ``EventHub`` → ``/ws/events``. These tests
poke the dark corners that didn't get tested:

  * What happens at volume (10 000 INSERT in one shot)?
  * What happens when ``EventHub.broadcast`` raises (subscriber dies)?
  * What happens when ``mark_changes_processed`` raises (read-only GPKG)?
  * Does the change_log backlog drain at all if writes outpace polling?
  * Does ``install_change_tracking`` survive a layer name with quotes/semicolons?
  * Is ``enable_change_tracking`` truly idempotent across re-uploads?

Every test is **read-only on production code** — no fixes here, just bug
exposers. Failures = bugs to triage with Marco.
"""

from __future__ import annotations

import sqlite3
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from persistence.change_log_watcher import ChangeLogWatcher
from persistence.gpkg_schema import bootstrap_gpkg_project, install_change_tracking


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _RecordingHub:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.raise_on_broadcast = False

    def broadcast(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        if self.raise_on_broadcast:
            raise RuntimeError("subscriber detonated")
        with self._lock:
            self.events.append((event_type, data or {}))


class _FakeEngine:
    backend_name = "gpkg"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rows: list[dict[str, Any]] = []
        self._next_id = 1
        self.processed_calls: list[int] = []
        self.fail_mark_processed = False

    def push(self, table: str, op: str, fid: str) -> int:
        with self._lock:
            row_id = self._next_id
            self._next_id += 1
            self._rows.append(
                {
                    "id": row_id,
                    "table_name": table,
                    "operation": op,
                    "row_pk": fid,
                    "changed_at": "2026-04-25T00:00:00",
                    "processed": 0,
                }
            )
            return row_id

    def get_pending_changes(self, limit: int = 100) -> list[dict]:
        with self._lock:
            pending = [r for r in self._rows if r["processed"] == 0]
            return [dict(r) for r in pending[:limit]]

    def mark_changes_processed(self, up_to_id: int) -> int:
        if self.fail_mark_processed:
            raise sqlite3.OperationalError("attempt to write a readonly database")
        with self._lock:
            self.processed_calls.append(up_to_id)
            n = 0
            for r in self._rows:
                if r["id"] <= up_to_id and r["processed"] == 0:
                    r["processed"] = 1
                    n += 1
            return n

    def pending_count(self) -> int:
        with self._lock:
            return sum(1 for r in self._rows if r["processed"] == 0)


def _wait_until(predicate, timeout: float = 3.0, step: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(step)
    return predicate()


# ---------------------------------------------------------------------------
# 1. Volume / backlog drain (does poll keep up with bulk insert?)
# ---------------------------------------------------------------------------


class TestVolume:
    def test_drains_10k_events_eventually(self) -> None:
        """Push 10k rows in one shot; the watcher must drain the queue.

        Configured with poll_interval=0.01s and batch_limit=500 so the test
        finishes in <2s. Each poll pulls 500 rows; with 200ms default and
        batch_limit=100 in PRODUCTION CONFIG, 10k events would take 20s
        minimum — plenty of room for a backlog to grow if upstream writes
        outpace polls.

        This test will FAIL silently if the watcher loses events on volume.
        """
        engine = _FakeEngine()
        hub = _RecordingHub()
        watcher = ChangeLogWatcher(
            engine, hub, dataset_id="ds-shadow", poll_interval=0.01, batch_limit=500
        )

        for i in range(10_000):
            engine.push("parcels", "INSERT", str(i))

        watcher.start()
        try:
            # Up to 5s budget — that's already 25× the polling interval.
            drained = _wait_until(
                lambda: engine.pending_count() == 0, timeout=5.0
            )
        finally:
            watcher.stop()

        assert drained, (
            f"backlog never drained — {engine.pending_count()} rows still "
            f"pending after 5s. Watcher cannot keep up with bursts."
        )
        # Every change row must produce exactly one dml.changed event.
        dml_events = [e for e in hub.events if e[0] == "dml.changed"]
        assert len(dml_events) == 10_000, (
            f"event loss: 10000 changes pushed, {len(dml_events)} broadcast"
        )

    def test_production_defaults_throughput_ceiling(self) -> None:
        """With production defaults (poll=0.2s, batch=100), the ceiling is
        500 changes/s. Document this here so we have a reference point.

        If upstream produces > 500 DML/s sustained, the change_log will
        grow without bound and never be acked. This test asserts the
        ceiling is what we think it is — if Marco changes the defaults
        without thinking, this catches it.
        """
        from persistence.change_log_watcher import ChangeLogWatcher as _W
        import inspect
        sig = inspect.signature(_W.__init__)
        assert sig.parameters["poll_interval"].default == 0.2, (
            "poll_interval default changed — re-check throughput math."
        )
        assert sig.parameters["batch_limit"].default == 100, (
            "batch_limit default changed — re-check throughput math."
        )
        # 100 rows / 0.2 s = 500 changes/s ceiling.
        # ACTION FOR JORDAN: document this in the Lot 2 sales sheet.


# ---------------------------------------------------------------------------
# 2. Failure modes — broadcast raises, ack fails
# ---------------------------------------------------------------------------


class TestFailureModes:
    def test_watcher_continues_when_broadcast_raises_and_acks_anyway(self) -> None:
        """P0-4a fix verified: a broadcast that raises must NOT block the
        ack of the surrounding batch.

        Scenario:
            * ``EventHub.broadcast`` raises ``RuntimeError`` on the very
              first event, then succeeds on every subsequent call.
            * Three rows are queued in the change_log before the watcher
              starts, so they all land in a single tick.

        Expected behaviour (the contract Marco implemented):
            1. All 3 rows are marked ``processed=1`` — the failed
               broadcast does NOT pin the backlog.
            2. The hub records exactly 2 successful events (the 1st was
               lost, but at-least-once is the documented contract).
            3. The watcher thread is still alive when we assert.

        Trade-off accepted: events from a broken subscriber are lost.
        SDK clients dedupe on ``change_id``. Documented in ``ws_router.py``.
        """
        engine = _FakeEngine()
        hub = _RecordingHub()

        # Wire a counter-based mock: 1st broadcast raises, rest succeed.
        original_broadcast = hub.broadcast
        call_count = {"n": 0}

        def _flaky_broadcast(event_type: str, data: dict[str, Any] | None = None) -> None:
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("subscriber detonated on first event")
            original_broadcast(event_type, data)

        hub.broadcast = _flaky_broadcast  # type: ignore[assignment]

        watcher = ChangeLogWatcher(engine, hub, dataset_id="ds-shadow", poll_interval=0.02)
        watcher._error_backoff = 0.05

        engine.push("parcels", "INSERT", "1")
        engine.push("parcels", "INSERT", "2")
        engine.push("parcels", "INSERT", "3")

        watcher.start()
        try:
            # Wait until the watcher has drained the backlog.
            drained = _wait_until(
                lambda: engine.pending_count() == 0, timeout=2.0
            )
            assert drained, (
                f"backlog never drained — {engine.pending_count()} rows still "
                f"pending. Broadcast failure should not block ack."
            )

            # 1) All 3 changes acked despite the 1st broadcast raising.
            assert engine.pending_count() == 0
            # mark_changes_processed must have been called at least once
            # with the max id we saw.
            assert engine.processed_calls, (
                "mark_changes_processed must run even when broadcast raises"
            )
            assert max(engine.processed_calls) >= 3

            # 2) Hub holds 2 events (1st lost, 2nd and 3rd delivered).
            dml_events = [e for e in hub.events if e[0] == "dml.changed"]
            assert len(dml_events) == 2, (
                f"expected 2 surviving events (1st lost on raise), "
                f"got {len(dml_events)}: {dml_events!r}"
            )

            # 3) Watcher still running — a buggy subscriber must NOT kill
            # the daemon thread.
            assert watcher.is_running(), (
                "watcher thread died after a broadcast raised — daemon "
                "must isolate subscriber failures"
            )
        finally:
            watcher.stop()

    def test_ack_failure_causes_event_duplication(self) -> None:
        """When mark_changes_processed raises (e.g. disk full, GPKG locked
        by QGIS in exclusive mode), the watcher logs and continues. The
        next tick sees the same rows and re-broadcasts them.

        Subscribers will receive the SAME change_id twice (or N times until
        ack succeeds). Marco's docstring says this is "safer than dropping
        events" — but client code receiving duplicates without idempotency
        will double-count.

        Document the duplication clearly so Jordan can warn integrators.
        """
        engine = _FakeEngine()
        hub = _RecordingHub()
        engine.fail_mark_processed = True
        watcher = ChangeLogWatcher(engine, hub, dataset_id="ds-shadow", poll_interval=0.02)

        engine.push("parcels", "INSERT", "1")

        watcher.start()
        try:
            # Wait for at least 3 broadcasts of the same change_id.
            assert _wait_until(
                lambda: sum(
                    1 for e in hub.events
                    if e[0] == "dml.changed" and e[1].get("change_id") == 1
                ) >= 3,
                timeout=2.0,
            )
        finally:
            watcher.stop()

        change_ids = [
            e[1]["change_id"] for e in hub.events if e[0] == "dml.changed"
        ]
        # The same change_id is broadcast multiple times.
        assert change_ids.count(1) >= 3, (
            f"expected duplicate broadcasts of change_id=1, got {change_ids}"
        )
        # CONTRACT WARNING for SDK consumers: dml.changed events are AT-LEAST-ONCE,
        # not exactly-once. They MUST de-duplicate by change_id client-side.

    def test_triggers_provider_returning_huge_list_is_called_every_batch(
        self,
    ) -> None:
        """The ``triggers_provider`` is called every tick that has rows.
        If a deployment has 10k triggers and 10k DML/s, the provider runs
        constantly and rebuilds the list every 200 ms. Memory churn risk.

        Document the contract: the provider is hot-path. Implementations
        must be O(triggers) cached, not a database round-trip.
        """
        engine = _FakeEngine()
        hub = _RecordingHub()
        provider_calls = [0]

        def _heavy_provider():
            provider_calls[0] += 1
            return []

        watcher = ChangeLogWatcher(
            engine,
            hub,
            dataset_id="ds-shadow",
            poll_interval=0.02,
            triggers_provider=_heavy_provider,
        )

        for i in range(50):
            engine.push("parcels", "INSERT", str(i))

        watcher.start()
        try:
            assert _wait_until(lambda: len(hub.events) >= 50, timeout=2.0)
        finally:
            watcher.stop()

        # Provider was called once per non-empty batch — at least once.
        # On 50 rows with batch_limit=100, that's 1 call.  But on bursts
        # spread across multiple ticks, it scales linearly.
        assert provider_calls[0] >= 1
        # ACTION: document that triggers_provider must be cached.


# ---------------------------------------------------------------------------
# 3. EventHub — slow subscriber blocks queue, isolation
# ---------------------------------------------------------------------------


class TestEventHubFanout:
    def test_queue_full_drops_silently_for_slow_subscriber(self) -> None:
        """``EventHub`` uses asyncio.Queue(maxsize=1000) per subscriber.
        On QueueFull, the broadcast logs a warning and DROPS the event.

        A slow client (no consumer pulling from the queue) will lose events
        silently — no error to the producer, no backpressure, no metric.
        Document this and expose it.
        """
        import asyncio

        from gispulse.adapters.http.event_hub import EventHub

        async def _scenario():
            hub = EventHub()
            # Subscribe but never read — simulate a stuck WS client.
            queue = hub.subscribe()

            # Fill past maxsize=1000.
            for i in range(1500):
                hub.broadcast("dml.changed", {"change_id": i})

            # Queue saturated at 1000; remaining 500 events are LOST.
            assert queue.qsize() == 1000
            # No exception was raised. No callback. No metric.
            # The producer (watcher) cannot tell the client lost data.
            return queue.qsize()

        size = asyncio.run(_scenario())
        assert size == 1000, (
            f"queue maxsize behaviour changed — got {size}, expected 1000"
        )

    def test_no_tenant_isolation_in_broadcast(self) -> None:
        """SECURITY-RELEVANT: EventHub fans out the same payload to EVERY
        subscriber. There is no session_id / project_id / tenant filtering.

        Two users connected to /ws/events from two different sessions
        receive each other's dml.changed events. This is acceptable for
        Community single-tenant deployments, but in any multi-tenant
        deployment it leaks the existence (and timing) of writes from
        other tenants.

        The payload is redacted (no values) — confirmed by Marco's test
        — but the SHAPE of the event still leaks: which table, which fid,
        what operation, when.
        """
        import asyncio
        from gispulse.adapters.http.event_hub import EventHub

        async def _scenario():
            hub = EventHub()
            tenant_a = hub.subscribe()
            tenant_b = hub.subscribe()

            # Producer in "tenant A's" thread broadcasts a change.
            hub.broadcast(
                "dml.changed",
                {"table": "tenantA_secret_layer", "op": "INSERT", "fid": "999"},
            )

            payload_a = await tenant_a.get()
            payload_b = await tenant_b.get()
            return payload_a, payload_b

        a, b = asyncio.run(_scenario())
        # B sees A's table name. This is the leak.
        assert a == b, (
            "EventHub broadcasts identically to all subscribers — "
            "tenant B sees tenant A's table name and timestamp. "
            "Mitigation: filter in WS router or add per-subscriber predicates."
        )
        assert "tenantA_secret_layer" in b, (
            "Even the table name leaks — that's a metadata side-channel."
        )


# ---------------------------------------------------------------------------
# 4. Layer name edge cases — SQL injection / quoting
# ---------------------------------------------------------------------------


class TestLayerNameEdgeCases:
    def _bootstrap_gpkg(self, path: Path) -> sqlite3.Connection:
        conn = sqlite3.connect(str(path))
        bootstrap_gpkg_project(conn)
        return conn

    @pytest.mark.parametrize(
        "evil_name",
        [
            'foo"; DROP TABLE x; --',
            "o'malley",
            "foo;bar",
            "foo bar",
            "",
            'evil\'); DROP TABLE _gispulse_change_log; --',
        ],
    )
    def test_install_change_tracking_rejects_unsafe_identifiers(
        self, evil_name: str
    ) -> None:
        """P0-4c fix verified: ``install_change_tracking`` interpolates
        ``layer_name`` into trigger DDL via f-strings (DDL cannot use bound
        parameters), so unsafe names were a textbook SQL injection vector.

        The ``_validate_identifier`` guard now rejects anything outside
        ``[A-Za-z_]\\w*`` **before** any SQL runs — a malicious or simply
        whitespace-bearing layer name raises ``ValueError`` and the
        change_log table stays intact.
        """
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "evil.gpkg"
            conn = self._bootstrap_gpkg(path)
            try:
                # The guard refuses unsafe identifiers before any DDL runs.
                with pytest.raises(ValueError):
                    install_change_tracking(conn, evil_name)

                still_alive = conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name='_gispulse_change_log'"
                ).fetchone()
                assert still_alive is not None, (
                    "_gispulse_change_log must survive a rejected install — "
                    "no DDL should have run."
                )
            finally:
                conn.close()

    @pytest.mark.parametrize(
        "good_name", ["parcelles", "parcelles_2024", "_test"]
    )
    def test_install_change_tracking_accepts_safe_identifiers(
        self, good_name: str
    ) -> None:
        """Plain identifiers must continue to install cleanly. Catches
        regressions in the validation regex."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ok.gpkg"
            conn = self._bootstrap_gpkg(path)
            try:
                conn.execute(
                    f'CREATE TABLE "{good_name}" (fid INTEGER PRIMARY KEY, name TEXT)'
                )
                conn.commit()
                # Should not raise.
                install_change_tracking(conn, good_name)
            finally:
                conn.close()

    def test_install_change_tracking_with_unicode_layer_name(self) -> None:
        """Layer names with accents / non-ASCII should round-trip cleanly.
        SQLite handles UTF-8 identifiers natively, so this should pass —
        if it doesn't, we have an encoding bug.
        """
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "unicode.gpkg"
            conn = self._bootstrap_gpkg(path)
            try:
                layer = "parcelles_éàü"
                conn.execute(f'CREATE TABLE "{layer}" (fid INTEGER PRIMARY KEY, name TEXT)')
                conn.commit()
                install_change_tracking(conn, layer)

                # Insert and check the change_log captured the UTF-8 name.
                conn.execute(f'INSERT INTO "{layer}"(name) VALUES (?)', ("alpha",))
                conn.commit()
                row = conn.execute(
                    "SELECT table_name FROM _gispulse_change_log ORDER BY id DESC LIMIT 1"
                ).fetchone()
                assert row is not None and row[0] == layer
            finally:
                conn.close()

    def test_install_change_tracking_idempotent_on_reupload(self) -> None:
        """Re-uploading the same GPKG must not produce duplicate triggers
        nor duplicate events. The DDL uses CREATE TRIGGER IF NOT EXISTS,
        so a 2nd install is a no-op — confirm it.
        """
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ident.gpkg"
            conn = self._bootstrap_gpkg(path)
            try:
                conn.execute('CREATE TABLE "parcels" (fid INTEGER PRIMARY KEY, name TEXT)')
                conn.commit()
                install_change_tracking(conn, "parcels")
                install_change_tracking(conn, "parcels")
                install_change_tracking(conn, "parcels")

                triggers = conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='trigger' AND name LIKE '_gispulse_trg_parcels_%' "
                    "ORDER BY name"
                ).fetchall()
                # Exactly 3: insert, update, delete.
                assert len(triggers) == 3, (
                    f"Expected 3 triggers, got {len(triggers)}: {triggers}. "
                    "Idempotency broken — re-uploads will multiply events."
                )

                # Now insert and confirm only ONE event lands in the log,
                # not three.
                conn.execute('INSERT INTO "parcels"(name) VALUES (?)', ("a",))
                conn.commit()
                rows = conn.execute(
                    "SELECT COUNT(*) FROM _gispulse_change_log "
                    "WHERE table_name='parcels' AND operation='INSERT'"
                ).fetchone()
                assert rows[0] == 1, (
                    f"Expected 1 INSERT row in change_log, got {rows[0]}. "
                    "Triggers are firing multiple times per DML — check "
                    "for duplicate trigger registration."
                )
            finally:
                conn.close()


# ---------------------------------------------------------------------------
# 5. Lifecycle — multi-dataset, single watcher
# ---------------------------------------------------------------------------


class TestSingleEngineConstraint:
    def test_app_state_holds_only_one_change_log_watcher(self) -> None:
        """Read app.py L259–282: the lifespan creates ONE watcher bound
        to ``spatial_engine``, the single engine instantiated at startup.

        Implication: uploading 3 different .gpkg files does NOT spawn 3
        watchers. They are stored in ``data_dir`` as separate files and
        are NEVER read by the change_log_watcher — the watcher only sees
        the ENGINE'S project GPKG (``GISPULSE_GPKG_PATH``).

        This means: ``_activate_change_tracking_if_eligible`` calls
        ``engine.enable_change_tracking(layer_name)`` on the ENGINE GPKG,
        which probably doesn't have that layer at all (the layer lives
        in the user's uploaded file). The except clause at L125 swallows
        the OperationalError and logs a warning. Net result: change tracking
        for uploaded GPKGs is **silently a no-op in this code path**.

        This test simply asserts the architectural constraint via code
        inspection so Jordan understands the limitation.
        """
        from gispulse.adapters.http.app import create_app
        import inspect

        src = inspect.getsource(create_app)
        # Single engine creation.
        assert src.count("create_spatial_engine(") == 1, (
            "Multiple engines created in lifespan? Architecture changed."
        )
        # Single watcher creation.
        assert src.count("ChangeLogWatcher(") == 1, (
            "Multiple watchers in lifespan? Architecture changed."
        )
        # ACTION FOR JORDAN: clarify product intent. Either:
        #   (a) The watcher should reach into uploaded GPKGs (multi-file
        #       polling — significant rework), or
        #   (b) Uploaded GPKGs are imported into the project engine (data
        #       copy — current behaviour suggests this is NOT the case),
        #   (c) Live-sync only works for the project GPKG, not uploads
        #       (current de-facto behaviour — needs documentation).

"""
ChangeLogWatcher — poll-based bridge from native SQLite triggers to EventHub.

Lot 2 of the live-sync stack: the GeoPackage / SpatiaLite engines install
``AFTER INSERT/UPDATE/DELETE`` triggers on each tracked layer that append
rows to ``_gispulse_change_log``. This watcher polls that table from a
daemon thread, broadcasts a redacted ``dml.changed`` event per row to the
:class:`EventHub`, optionally evaluates GISPulse triggers and broadcasts a
``trigger.fired`` event for each one that matches, then acks the rows by
calling :meth:`engine.mark_changes_processed`.

Architecture::

    SQLite triggers  ──INSERT──▶  _gispulse_change_log
                                          │
                          poll (200 ms)   │
                                          ▼
                              ChangeLogWatcher._tick()
                                          │
                                          ├──▶ EventHub.broadcast("dml.changed", ...)
                                          │
                                          ├──▶ TriggerEvaluator.evaluate(...)
                                          │       │
                                          │       └──▶ EventHub.broadcast("trigger.fired", ...)
                                          │
                                          └──▶ engine.mark_changes_processed(max_id)

Threading:
    The watcher runs on a single daemon ``threading.Thread``. It only ever
    consumes from the change_log; it never installs or removes the SQLite
    triggers themselves — that is the responsibility of whoever opens the
    dataset (HTTP upload endpoint or app lifespan).

Security:
    The ``dml.changed`` payload contains only ``table``, ``op``, ``fid``,
    ``change_id`` and ``ts``. It deliberately omits row values to avoid
    leaking record content over ``/ws/events`` if that endpoint is exposed
    without auth. Trigger payloads are scrubbed the same way.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from typing import Any, Callable, Protocol

from core.models import ChangeOperation, ChangeRecord, Trigger

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Engine protocol — keeps the watcher decoupled from any specific backend
# ---------------------------------------------------------------------------


class _ChangeLogEngine(Protocol):
    """The minimal engine surface the watcher needs.

    Implemented by :class:`persistence.gpkg_engine.GeoPackageEngine`. A
    SpatiaLite engine that exposes ``get_pending_changes`` /
    ``mark_changes_processed`` against the same ``_gispulse_change_log``
    schema is also compatible.
    """

    @property
    def backend_name(self) -> str: ...  # pragma: no cover

    def get_pending_changes(self, limit: int = 100) -> list[dict]: ...  # pragma: no cover

    def mark_changes_processed(self, up_to_id: int) -> int: ...  # pragma: no cover


class _EventHubProtocol(Protocol):
    """Hub surface used by the watcher (matches
    :class:`gispulse.adapters.http.event_hub.EventHub`)."""

    def broadcast(
        self, event_type: str, data: dict[str, Any] | None = None
    ) -> None: ...  # pragma: no cover


class _TriggerEvaluatorProtocol(Protocol):
    """Minimal evaluator surface (matches
    :class:`rules.trigger_evaluator.TriggerEvaluator`)."""

    def evaluate(
        self, change_record: ChangeRecord, triggers: list[Trigger]
    ) -> list[Any]: ...  # pragma: no cover


class _ActionDispatcherProtocol(Protocol):
    """Minimal dispatcher surface (matches
    :class:`gispulse.adapters.esb.action_dispatcher.ActionDispatcher`)."""

    def dispatch_all(
        self, actions: list[Any], context: Any
    ) -> int: ...  # pragma: no cover


# ---------------------------------------------------------------------------
# Watcher
# ---------------------------------------------------------------------------


class ChangeLogWatcher:
    """Polls ``_gispulse_change_log`` on a SQLite-based engine and broadcasts
    DML events to an :class:`EventHub`.

    The watcher is a *consumer* only: it does not install the SQLite triggers
    that populate the change log. Whoever opens the dataset (the FastAPI
    upload endpoint, the lifespan when reopening an existing GPKG, etc.)
    must call ``engine.enable_change_tracking(layer)`` for each layer they
    want tracked.

    Args:
        engine:             A GPKG or SpatiaLite engine exposing
                            ``get_pending_changes`` and
                            ``mark_changes_processed``.
        event_hub:          Sink for ``dml.changed`` and ``trigger.fired``
                            events.
        dataset_id:         Stable handle of the dataset this watcher
                            polls (UUID string or the synthetic
                            ``"__project__"`` for the lifespan-bound
                            project GPKG). Injected into every broadcast
                            payload so multi-tenant consumers can
                            disambiguate tables that collide across
                            datasets. Mandatory.
        poll_interval:      Seconds between two ``_tick()`` calls.
                            Default 0.2 s.
        batch_limit:        Max rows pulled per tick.
        trigger_evaluator:  Optional evaluator used when ``triggers_provider``
                            is set. Lazily replaced by a default
                            :class:`TriggerEvaluator` if ``None`` and
                            triggers are configured.
        triggers_provider:  Callable returning the list of currently active
                            :class:`Trigger`. Called once per tick when
                            change_log rows are present (so trigger
                            edits made via the API take effect on the
                            next batch). When *None*, only ``dml.changed``
                            events are broadcast.
        action_dispatcher:  Optional :class:`ActionDispatcher`. When set,
                            matched triggers are dispatched (NOTIFY,
                            WEBHOOK, SET_FIELD, RUN_SQL, …) in addition
                            to the WS broadcast. Each handler is wrapped
                            in try/except by the dispatcher so a single
                            failing action cannot abort the tick. When
                            *None*, fired triggers are broadcast-only
                            (current default — backward-compatible).

    Lifecycle:
        - :meth:`start` spawns the daemon thread.
        - :meth:`stop` flips the kill flag and joins with a 2 s timeout.
        - :meth:`is_running` reports the current state.
    """

    def __init__(
        self,
        engine: _ChangeLogEngine,
        event_hub: _EventHubProtocol,
        *,
        dataset_id: str,
        poll_interval: float = 0.2,
        batch_limit: int = 100,
        trigger_evaluator: _TriggerEvaluatorProtocol | None = None,
        triggers_provider: Callable[[], list[Trigger]] | None = None,
        action_dispatcher: _ActionDispatcherProtocol | None = None,
        bulk_threshold: int = 0,
        bulk_eval: str = "skip",
        schema_drift_check_interval_s: float = 5.0,
        validation_runner: Any | None = None,
    ) -> None:
        if poll_interval <= 0:
            raise ValueError("poll_interval must be > 0")
        if batch_limit <= 0:
            raise ValueError("batch_limit must be > 0")
        if bulk_threshold < 0:
            raise ValueError("bulk_threshold must be >= 0 (0 disables bulk mode)")
        if bulk_eval not in ("skip", "per_row"):
            raise ValueError(
                "bulk_eval must be 'skip' (collapse batch, no eval — Mode 2)"
                " or 'per_row' (1 bulk WS event + per-row trigger eval — Mode 3)"
            )
        if schema_drift_check_interval_s < 0:
            raise ValueError(
                "schema_drift_check_interval_s must be >= 0 "
                "(0 disables the drift watchdog)"
            )
        # Lot 2 v2 (Beta E2E): ``dataset_id`` is mandatory. Multi-tenant
        # event consumers cannot tell two tables named ``parcels`` apart
        # across two GPKGs without it. Empty string is rejected because
        # downstream filters treat ``""`` as a wildcard match.
        if not isinstance(dataset_id, str) or not dataset_id:
            raise ValueError(
                "dataset_id must be a non-empty string (multi-tenant contract)"
            )

        self._engine = engine
        self._hub = event_hub
        self._dataset_id = dataset_id
        self._poll_interval = float(poll_interval)
        self._batch_limit = int(batch_limit)
        # v1.3.0 #8: when len(pending_rows) >= bulk_threshold, the tick
        # collapses the batch into a single ``bulk.changed`` event instead
        # of broadcasting one ``dml.changed`` per row. ``0`` (default)
        # keeps the legacy per-row behaviour (Mode 1) for safety.
        # v1.5.3 #103 (B-01): ``bulk_eval`` selects what happens to
        # trigger evaluation under bulk mode:
        #   - ``"skip"``    — Mode 2 (default, back-compat): evaluation
        #                     is skipped entirely, receivers get a
        #                     summary and run their own batch-level
        #                     reconciliation if they need to.
        #   - ``"per_row"`` — Mode 3 (new): emit ONE ``bulk.changed``
        #                     summary on the wire AND still evaluate
        #                     triggers per row (firing
        #                     ``trigger.fired`` + dispatching actions).
        #                     The 50-paste-in-QGIS scenario gets one WS
        #                     event for subscribers but every DSL
        #                     trigger still sees every row.
        self._bulk_threshold = int(bulk_threshold)
        self._bulk_eval = bulk_eval
        # B-13 (#103, v1.5.3): schema-drift watchdog. Every
        # :attr:`_schema_drift_check_interval_s` wall-clock seconds the
        # watcher re-hashes ``PRAGMA table_info("<layer>")`` for every
        # tracked layer; on mismatch it drops + re-installs change
        # tracking and broadcasts a ``schema.changed`` event so
        # subscribers (portal, plugin) can refresh. Set to 0 to disable
        # entirely (tests / SaaS Pro where mutating DDL goes through a
        # different code path).
        self._schema_drift_check_interval_s = float(
            schema_drift_check_interval_s
        )
        self._schema_hashes: dict[str, str] = {}
        self._last_drift_check_ts = 0.0
        self._evaluator = trigger_evaluator
        self._triggers_provider = triggers_provider
        self._action_dispatcher = action_dispatcher
        # v1.6.0 — optional ValidationRunner that evaluates ``validate:``
        # rules per row. The watcher calls ``evaluate(table, row_id)``
        # after the trigger evaluator block on INSERT / UPDATE_GEOM /
        # UPDATE_ATTR events. Failures broadcast on the event hub via
        # the runner's own hub binding (we don't double-broadcast here).
        # Typed as ``Any`` to avoid a circular import on the runtime
        # package; the runner protocol is just ``evaluate(table, row_id)``.
        self._validation_runner = validation_runner
        # Cache active triggers indexed by id within a tick so the
        # dispatcher can recover the full Trigger (with .actions) from
        # the FiredTrigger summary, without re-querying the repo.
        self._trigger_lookup: dict[Any, Trigger] = {}

        self._running = False
        self._thread: threading.Thread | None = None
        # S5: replace bare ``time.sleep(self._poll_interval)`` with an
        # :class:`Event` so :meth:`stop` (and the CLI ``--watch`` daemon's
        # SIGINT handler) can interrupt the wait immediately rather than
        # waiting up to ``poll_interval`` seconds before the loop notices.
        self._stop_event = threading.Event()
        # Backoff window when get_pending_changes raises.
        self._error_backoff = 1.0

        # #95 (P0-3 dashboard): observability counters surfaced through
        # ``get_stats()`` and the ``GET /watchers/{id}`` endpoint. Single
        # producer (the polling thread) → no lock needed; integer
        # increments are atomic in CPython. Timestamps are wall-clock
        # ``time.time()`` floats — easy to render in the portal without
        # an additional dep, easy to subtract for "ran for X seconds".
        self._started_at: float | None = None
        self._tick_count = 0
        self._rows_processed = 0
        self._fire_count = 0
        self._error_count = 0
        self._last_tick_at: float | None = None
        self._last_fire_at: float | None = None
        self._last_error_at: float | None = None
        self._last_error_msg: str | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the polling thread (idempotent)."""
        if self._running:
            return
        self._running = True
        self._started_at = time.time()
        # Re-arm in case the watcher is being restarted after a stop().
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name=f"gispulse-change-log-watcher-{getattr(self._engine, 'backend_name', '?')}",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "change_log_watcher_started backend=%s interval=%.3fs",
            getattr(self._engine, "backend_name", "?"),
            self._poll_interval,
        )

    def stop(self) -> None:
        """Stop the polling thread and join (2 s timeout).

        S5: signals the internal :class:`Event` so a thread sleeping
        inside ``Event.wait(poll_interval)`` returns immediately. With
        the prior ``time.sleep()`` an operator pressing Ctrl-C against
        a watcher configured for ``poll_interval=5s`` would wait up to
        a full poll window before the loop noticed.
        """
        self._running = False
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2.0)
            if thread.is_alive():
                logger.warning("change_log_watcher_join_timeout")
        self._thread = None
        logger.info("change_log_watcher_stopped")

    def is_running(self) -> bool:
        """Return True when the polling thread is active."""
        return self._running and self._thread is not None and self._thread.is_alive()

    # ------------------------------------------------------------------
    # Observability — #95 (P0-3 dashboard)
    # ------------------------------------------------------------------

    def get_stats(self) -> dict[str, Any]:
        """Return a snapshot of the watcher's runtime counters.

        Surfaced through :meth:`WatcherRegistry.get_stats` and the
        ``GET /watchers/{dataset_id}`` endpoint. Single producer (the
        polling thread) → no lock taken; reads of integer / float fields
        are atomic in CPython, and the consumer treats the snapshot as
        eventually-consistent (a tick happening between two field reads
        is acceptable).

        Returns:
            ``{"running": bool, "started_at": float|None,
              "tick_count": int, "rows_processed": int, "fire_count": int,
              "error_count": int, "last_tick_at": float|None,
              "last_fire_at": float|None, "last_error_at": float|None,
              "last_error_msg": str|None, "dataset_id": str,
              "poll_interval": float, "batch_limit": int,
              "bulk_threshold": int, "bulk_eval": str}``.
        """
        return {
            "dataset_id": self._dataset_id,
            "running": self.is_running(),
            "started_at": self._started_at,
            "tick_count": self._tick_count,
            "rows_processed": self._rows_processed,
            "fire_count": self._fire_count,
            "error_count": self._error_count,
            "last_tick_at": self._last_tick_at,
            "last_fire_at": self._last_fire_at,
            "last_error_at": self._last_error_at,
            "last_error_msg": self._last_error_msg,
            "poll_interval": self._poll_interval,
            "batch_limit": self._batch_limit,
            "bulk_threshold": self._bulk_threshold,
            "bulk_eval": self._bulk_eval,
        }

    @property
    def stop_event(self) -> threading.Event:
        """Expose the cancel event so external loops (CLI ``--watch``)
        can wait on the same primitive when they drive ``_tick`` directly
        instead of starting the daemon thread."""
        return self._stop_event

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _run(self) -> None:
        """Polling loop — runs in the daemon thread.

        ``wait -> tick`` so that on restart with un-acked rows, the WS
        subscriber has a chance to (re)connect before the watcher consumes
        and acks the backlog (otherwise replay events are broadcast to no
        subscribers and lost).

        S5: the wait uses :class:`threading.Event` instead of ``time.sleep``
        so :meth:`stop` returns control immediately (no poll-interval-sized
        latency on Ctrl-C).
        """
        while self._running:
            # ``wait`` returns True when the event is set (stop) or False
            # when it timed out (next tick). Either way we re-check
            # ``_running`` afterwards to honour external state changes.
            if self._stop_event.wait(timeout=self._poll_interval):
                break
            if not self._running:
                break
            try:
                rows_processed = self._tick() or 0
                self._tick_count += 1
                self._last_tick_at = time.time()
                if rows_processed:
                    self._rows_processed += rows_processed
            except Exception as exc:  # pragma: no cover — defensive
                self._error_count += 1
                self._last_error_at = time.time()
                self._last_error_msg = f"{type(exc).__name__}: {exc}"
                logger.exception("change_log_watcher_tick_failed: %s", exc)
                # Use the same cancellable wait so error backoff also
                # respects stop().
                if self._stop_event.wait(timeout=self._error_backoff):
                    break

    def _tick(self) -> int:
        """One polling cycle. Returns the number of rows processed.

        S5: when the engine raises while listing pending changes (typical
        cause: a transient SQLite lock during a concurrent QGIS save) we
        sleep on :attr:`_stop_event` rather than ``time.sleep`` so the
        external stop signal is honoured without an extra poll.

        B-13 (#103): the wall-clock-throttled schema-drift watchdog
        runs at the start of every tick. It is a no-op until
        :attr:`_schema_drift_check_interval_s` seconds have passed,
        and again a no-op when the engine has no ``_get_conn`` shim.
        """
        self._maybe_drift_check()
        try:
            rows = self._engine.get_pending_changes(self._batch_limit)
        except Exception as exc:
            logger.warning("change_log_get_pending_failed: %s", exc)
            # Cancellable backoff: ``wait`` returns immediately when the
            # event is set (stop), otherwise after ``_error_backoff`` s.
            self._stop_event.wait(timeout=self._error_backoff)
            return 0

        if not rows:
            return 0

        # v1.3.0 #8: bulk-mode short-circuit. When a single tick pulls
        # ``bulk_threshold`` or more rows (typical of an ogr2ogr append,
        # a QGIS bulk paste, or a Python loop INSERT), collapse the
        # per-row ``dml.changed`` flood into a single ``bulk.changed``
        # summary. The bulk path's evaluation behaviour is driven by
        # :attr:`_bulk_eval` (B-01 #103, v1.5.3):
        #   - ``"skip"``    — Mode 2 (back-compat): no per-row eval.
        #   - ``"per_row"`` — Mode 3: 1 bulk WS event + N trigger evals.
        if self._bulk_threshold > 0 and len(rows) >= self._bulk_threshold:
            return self._bulk_tick(rows)

        active_triggers, evaluator = self._resolve_active_triggers_and_evaluator()

        max_id = 0
        for row in rows:
            change_id = self._process_row(
                row, evaluator, active_triggers, broadcast_dml=True
            )
            if change_id > max_id:
                max_id = change_id

        # ---- Ack ------------------------------------------------------
        # P0-4a (Beta): always ack the max_id we successfully processed,
        # even if some broadcasts raised. Otherwise a single bad subscriber
        # would pin the backlog and re-broadcast the same rows forever.
        # at-least-once semantics are documented in ws_router.py.
        if max_id > 0:
            try:
                self._engine.mark_changes_processed(max_id)
            except Exception as exc:
                # If we can't ack, the next tick will see the same rows
                # and re-broadcast. That's safer than dropping events,
                # but log it so an operator can investigate.
                logger.warning("change_log_mark_processed_failed: %s", exc)

        return len(rows)

    # ------------------------------------------------------------------
    # Bulk-mode tick (v1.3.0 #8)
    # ------------------------------------------------------------------

    def _bulk_tick(self, rows: list[dict]) -> int:
        """Collapse a large batch into a single ``bulk.changed`` event.

        Behaviour driven by :attr:`_bulk_eval` (v1.5.3 #103, B-01):

        * ``"skip"`` — Mode 2 (default, back-compat):
            - Broadcast ONE summary event with op_counts, layers, and
              the ``change_id_range`` so receivers can de-duplicate.
            - Skip per-row trigger evaluation entirely. Receivers that
              care about bulk imports should subscribe to ``bulk.changed``
              and run their own batch-level reconciliation; rules that
              filter on per-row attributes are not designed for bulk.

        * ``"per_row"`` — Mode 3 (B-01):
            - Same single ``bulk.changed`` summary on the wire.
            - **Plus** evaluate triggers per row (firing ``trigger.fired``
              for matched triggers and dispatching their actions). The
              50-row QGIS paste scenario gets one WS event AND every DSL
              trigger still sees every row.

        Common to both modes:
            - Ack rows up to ``max(change_id)`` so the backlog drains in
              one go (no re-scan on the next tick).
            - Broadcast failure → log + still ack (otherwise a dead
              subscriber pins the watcher to the same backlog forever).
            - Ack failure → log + return processed count anyway. Next
              tick will see the same rows; the bulk summary is
              idempotent against ``change_id_range[0]``.
        """
        payload = _summarise_batch(rows, self._dataset_id)
        try:
            self._hub.broadcast("bulk.changed", payload)
        except Exception as exc:
            logger.error(
                "bulk_changed_broadcast_failed change_id_range=%s err=%s",
                payload["change_id_range"],
                exc,
            )

        # Mode 3 (B-01): evaluate triggers per row even though we
        # collapsed the WS broadcast. ``broadcast_dml=False`` skips the
        # per-row ``dml.changed`` (already replaced by the bulk summary
        # above). The trigger.fired + action dispatch still run.
        if self._bulk_eval == "per_row":
            active_triggers, evaluator = (
                self._resolve_active_triggers_and_evaluator()
            )
            for row in rows:
                self._process_row(
                    row, evaluator, active_triggers, broadcast_dml=False
                )

        max_id = int(payload["change_id_range"][1] or 0)
        if max_id > 0:
            try:
                self._engine.mark_changes_processed(max_id)
            except Exception as exc:
                logger.warning(
                    "bulk_mark_processed_failed max_id=%d err=%s", max_id, exc
                )

        logger.info(
            "change_log_bulk_tick rows=%d layers=%d max_id=%d eval=%s",
            payload["row_count"],
            len(payload["layers"]),
            max_id,
            self._bulk_eval,
        )
        return len(rows)

    # ------------------------------------------------------------------
    # Per-row dispatch helpers (extracted v1.5.3 #103, B-01)
    # ------------------------------------------------------------------

    def _resolve_active_triggers_and_evaluator(
        self,
    ) -> tuple[list[Trigger], _TriggerEvaluatorProtocol | None]:
        """Resolve active triggers + evaluator once per tick.

        Refreshes :attr:`_trigger_lookup` so the dispatcher can recover
        a full Trigger from a ``FiredTrigger.trigger_id`` without
        re-querying the repo. Lazy-imports
        :class:`rules.trigger_evaluator.TriggerEvaluator` so the
        persistence layer stays free of a hard rules dependency.
        """
        active_triggers: list[Trigger] = []
        if self._triggers_provider is not None:
            try:
                active_triggers = list(self._triggers_provider() or [])
            except Exception as exc:
                logger.warning("change_log_triggers_provider_failed: %s", exc)
                active_triggers = []
        # Build the per-tick lookup only when an action_dispatcher is
        # wired (otherwise pure overhead — and tolerates
        # ``triggers_provider`` implementations that pass in placeholder
        # strings rather than full :class:`Trigger`).
        if self._action_dispatcher is not None:
            self._trigger_lookup = {
                t.id: t for t in active_triggers if hasattr(t, "id")
            }
        else:
            self._trigger_lookup = {}

        evaluator = self._evaluator
        if active_triggers and evaluator is None:
            from rules.trigger_evaluator import TriggerEvaluator

            evaluator = TriggerEvaluator()
            self._evaluator = evaluator
        return active_triggers, evaluator

    def _process_row(
        self,
        row: dict,
        evaluator: _TriggerEvaluatorProtocol | None,
        active_triggers: list[Trigger],
        *,
        broadcast_dml: bool,
    ) -> int:
        """Process one change-log row.

        When *broadcast_dml* is ``True`` (Mode 1, default per-row tick),
        emit a ``dml.changed`` event before evaluating triggers. When
        ``False`` (Mode 3 bulk path), the bulk summary has already been
        emitted upstream so we skip the per-row event but still evaluate
        triggers, broadcast ``trigger.fired`` for matched ones, and
        dispatch their actions.

        Returns:
            The row's ``change_id`` (or ``0`` when the row was malformed).
        """
        try:
            change_id = int(row["id"])
        except (KeyError, TypeError, ValueError):
            # Malformed row — skip but don't ack so the next tick can
            # still see it (or surface the issue).
            logger.warning("change_log_row_missing_id row=%r", row)
            return 0

        table = str(row.get("table_name") or "")
        op = str(row.get("operation") or "").upper()
        fid_raw = row.get("row_pk")
        fid = str(fid_raw) if fid_raw is not None else None
        ts = row.get("changed_at")
        # v1.6.0 #119/#120: resolve coarse UPDATE → granular UPDATE_GEOM /
        # UPDATE_ATTR via the change_log's ``geom_changed`` column. The
        # column is INTEGER DEFAULT 0 (cf gpkg_schema.py v2 migration), so
        # a missing column on legacy GPKGs degrades to UPDATE_ATTR.
        geom_changed = bool(row.get("geom_changed"))
        if op == "UPDATE":
            op = "UPDATE_GEOM" if geom_changed else "UPDATE_ATTR"

        if broadcast_dml:
            # Payload is intentionally minimal: no field values, no
            # geom. This matches the security note in Lot 2 (do not
            # leak data via /ws/events when the endpoint is
            # unauthenticated). P0-4a (Beta): wrap each broadcast in
            # its own try/except — a buggy/dead subscriber must NOT
            # abort the whole tick.
            try:
                self._hub.broadcast(
                    "dml.changed",
                    {
                        "dataset_id": self._dataset_id,
                        "table": table,
                        "op": op,
                        "fid": fid,
                        "change_id": change_id,
                        "ts": ts,
                        "geom_changed": geom_changed,
                    },
                )
            except Exception as exc:
                logger.error(
                    "event_hub_broadcast_failed change_id=%d table=%s op=%s err=%s",
                    change_id,
                    table,
                    op,
                    exc,
                )
                # Continue: still update max_id so we ack and don't loop.

        if active_triggers and evaluator is not None:
            try:
                operation = ChangeOperation(op)
            except ValueError:
                operation = ChangeOperation.INSERT

            # When any active trigger carries a DSL predicate
            # (S4: ``conditions["predicate_ast"]``), fetch the row
            # attributes from the underlying table so the evaluator can
            # match against real values. Opt-in per trigger — triggers
            # without predicates never pay the SELECT cost.
            new_values: dict[str, Any] = {}
            old_values: dict[str, Any] = {}
            has_predicate = any(
                (getattr(t, "conditions", None) or {}).get("predicate_ast")
                is not None
                for t in active_triggers
            )
            if (
                op != "DELETE"
                and fid is not None
                and table
                and has_predicate
            ):
                new_values = self._load_row_values(table, fid) or {}
            # v1.6.0 (#120 B-08): DELETE events expose the row attributes
            # captured at trigger time via ``old_values`` JSON. The
            # column is populated by the AFTER DELETE SQLite trigger
            # since v1 — we just hydrate ``ChangeRecord.old_values`` so
            # the existing predicate evaluator can filter on the row's
            # last-known state. Falling back silently when the column
            # is missing or unparseable keeps legacy / out-of-sync
            # GPKGs alive.
            if op == "DELETE" and has_predicate:
                raw = row.get("old_values")
                if isinstance(raw, (str, bytes)) and raw:
                    try:
                        decoded = json.loads(raw)
                    except (ValueError, TypeError):
                        decoded = None
                    if isinstance(decoded, dict):
                        old_values = decoded
                        # The evaluator's attribute predicates inspect
                        # ``new_values``; for DELETE we mirror the old
                        # row so ``predicate: status == 'active'`` keeps
                        # the same surface as on INSERT/UPDATE.
                        if not new_values:
                            new_values = dict(decoded)

            record = ChangeRecord(
                session_id="",
                table_name=table,
                feature_id=fid,
                operation=operation,
                new_values=new_values,
                old_values=old_values,
            )

            try:
                fired = evaluator.evaluate(record, active_triggers)
            except Exception as exc:
                logger.warning(
                    "change_log_trigger_eval_failed change_id=%d: %s",
                    change_id,
                    exc,
                )
                fired = []

            for ft in fired:
                matched = bool(getattr(ft, "matched", False))
                if not matched:
                    continue
                # #95: counter / timestamp tracked by every matched fire,
                # whether the broadcast or dispatch later succeed. This
                # is what the dashboard surfaces — keeping it in sync
                # with the on-the-wire ``trigger.fired`` count would
                # require a finally block, which is more noise than
                # signal for the operator view.
                self._fire_count += 1
                self._last_fire_at = time.time()
                trigger_id = getattr(ft, "trigger_id", None)
                try:
                    self._hub.broadcast(
                        "trigger.fired",
                        {
                            "dataset_id": self._dataset_id,
                            "trigger_id": str(trigger_id) if trigger_id else None,
                            "change_id": change_id,
                            "table": table,
                            "op": op,
                            "fid": fid,
                            "actions": list(
                                getattr(ft, "actions_dispatched", []) or []
                            ),
                            "eval_time_ms": float(
                                getattr(ft, "eval_time_ms", 0.0)
                            ),
                        },
                    )
                except Exception as exc:
                    logger.error(
                        "event_hub_broadcast_failed event=trigger.fired "
                        "change_id=%d trigger_id=%s err=%s",
                        change_id,
                        trigger_id,
                        exc,
                    )
                    # Continue — don't abort the row.

                # Bridge to ActionDispatcher so NOTIFY / WEBHOOK /
                # SET_FIELD / RUN_SQL / … run end-to-end, not just
                # broadcast over WS. The dispatcher itself wraps every
                # handler in try/except.
                if self._action_dispatcher is not None:
                    self._dispatch_fired(
                        ft,
                        table=table,
                        operation=op,
                        row_id=fid,
                        change_id=change_id,
                        ts=ts,
                    )

        # v1.6.0 — declarative validate: rules. Runs *after* the trigger
        # evaluator block so a tag_field action emitted by a future
        # mode=tag bridge sees the post-trigger row state. DELETE is
        # skipped because the row no longer exists; ditto for BULK
        # which the watcher already collapses into a single summary
        # event before this point. Any exception in the runner is
        # contained so the tick keeps moving.
        if (
            self._validation_runner is not None
            and fid is not None
            and table
            and op in ("INSERT", "UPDATE", "UPDATE_GEOM", "UPDATE_ATTR")
        ):
            try:
                self._validation_runner.evaluate(table, fid)
            except Exception as exc:
                logger.warning(
                    "change_log_validation_runner_failed change_id=%d: %s",
                    change_id,
                    exc,
                )

        return change_id

    # ------------------------------------------------------------------
    # Row materialisation for DSL predicate evaluation (S4)
    # ------------------------------------------------------------------

    def _load_row_values(self, table: str, fid: str) -> dict[str, Any] | None:
        """Read the current row from the underlying table.

        Used only when at least one active trigger carries a DSL
        ``predicate_ast`` (no overhead otherwise). The row is fetched
        through the engine's GPKG connection and returned as a flat
        ``dict``. Geometry columns are kept as raw blobs / WKT (the
        evaluator currently only matches on attributes — geometry
        predicates remain on the structured ``GeomPredicate`` path).

        Returns ``None`` when the table or row no longer exists; the
        evaluator then sees an empty payload and the predicate
        resolves to a non-match (fail-safe). Identifier validation
        prevents SQL injection via the trigger config.
        """
        from core.sql_safety import validate_identifier as _validate_ident

        try:
            safe_table = _validate_ident(table)
        except Exception as exc:  # ValueError from validator
            logger.warning(
                "change_log_load_row_invalid_table table=%r err=%s", table, exc
            )
            return None

        # Default GPKG primary key is "fid". We fall back to a generic
        # ``id``/``rowid`` lookup so non-GPKG layers still resolve.
        pk_candidates = ("fid", "id", "rowid")
        get_conn = getattr(self._engine, "_get_conn", None)
        if get_conn is None:
            return None
        try:
            conn = get_conn()
        except Exception:
            return None

        try:
            for pk in pk_candidates:
                try:
                    cur = conn.execute(
                        f'SELECT * FROM "{safe_table}" WHERE "{pk}" = ? LIMIT 1',
                        (fid,),
                    )
                except Exception:
                    continue
                row = cur.fetchone()
                if row is not None:
                    return {k: row[k] for k in row.keys()}
        except Exception as exc:
            logger.warning(
                "change_log_load_row_failed table=%s fid=%s err=%s",
                table,
                fid,
                exc,
            )
        return None

    # ------------------------------------------------------------------
    # Action dispatch bridge (#458)
    # ------------------------------------------------------------------

    def _dispatch_fired(
        self,
        ft: Any,
        *,
        table: str,
        operation: str,
        row_id: str | None,
        change_id: int,
        ts: Any,
    ) -> None:
        """Translate a FiredTrigger into a TriggerContext + dispatch_all.

        Imports are local so persistence stays free of a hard dependency
        on the ESB / rules layer (matches the lazy-import pattern used
        for :class:`TriggerEvaluator`).
        """
        trigger_id = getattr(ft, "trigger_id", None)
        if trigger_id is None:
            return

        trigger = self._trigger_lookup.get(trigger_id)
        if trigger is None or not getattr(trigger, "actions", None):
            # No actions to run, or trigger evaporated between provider
            # call and dispatch — nothing to do.
            return

        try:
            from datetime import datetime, timezone

            from gispulse.adapters.esb.action_dispatcher import TriggerContext
            from core.models import EvalResult

            timestamp = ts if isinstance(ts, datetime) else datetime.now(timezone.utc)
            ctx = TriggerContext(
                trigger=trigger,
                eval_result=EvalResult(matched=True, transition=None),
                table=table,
                operation=str(operation),
                row_id=str(row_id) if row_id else "",
                new_attrs={},
                timestamp=timestamp,
            )
            self._action_dispatcher.dispatch_all(list(trigger.actions), ctx)
        except Exception as exc:
            # The dispatcher already wraps each action handler — this
            # outer guard catches construction failures (bad Trigger
            # shape, import errors). Never abort the watcher tick.
            logger.warning(
                "change_log_action_dispatch_failed change_id=%d trigger_id=%s err=%s",
                change_id,
                trigger_id,
                exc,
            )

    # ------------------------------------------------------------------
    # Schema-drift watchdog (B-13, v1.5.3 #103)
    # ------------------------------------------------------------------

    def _maybe_drift_check(self) -> None:
        """Throttled entry point for the schema-drift check.

        Runs :meth:`_drift_check_tick` no more than once every
        :attr:`_schema_drift_check_interval_s` wall-clock seconds; a
        ``0`` interval disables the watchdog entirely (used by tests
        and for SaaS contexts where DDL is gated through a different
        code path).
        """
        if self._schema_drift_check_interval_s <= 0:
            return
        now = time.time()
        if now - self._last_drift_check_ts < self._schema_drift_check_interval_s:
            return
        self._last_drift_check_ts = now
        try:
            self._drift_check_tick()
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("schema_drift_check_failed: %s", exc)

    def _drift_check_tick(self) -> list[str]:
        """Re-hash every tracked layer; on mismatch drop+reinstall the
        triggers and broadcast ``schema.changed``.

        Returns the list of layer names that drifted (and were
        repaired). Empty when nothing changed since the last tick.

        B-13 reproducer: a QGIS user adds / drops / renames a column
        via Field Calculator. Pre-B-13 the AFTER UPDATE trigger's
        baked ``new_values`` JSON references a stale column list;
        further edits crash with ``no such column`` or silently
        omit the new column from the change_log payload. The watchdog
        rebuilds the trigger DDL the next time it ticks (default
        every 5 s), and pushes a ``schema.changed`` event so the
        portal / plugin can refresh their layer panels.
        """
        get_conn = getattr(self._engine, "_get_conn", None)
        if get_conn is None:
            return []
        try:
            conn = get_conn()
        except Exception as exc:
            logger.warning("schema_drift_get_conn_failed: %s", exc)
            return []

        try:
            rows = conn.execute(
                "SELECT DISTINCT tbl_name FROM sqlite_master "
                "WHERE type = 'trigger' "
                "AND name LIKE '\\_gispulse\\_trg\\_%' ESCAPE '\\'"
            ).fetchall()
        except Exception as exc:
            logger.warning("schema_drift_enumerate_failed: %s", exc)
            return []

        # Cope with both sqlite3.Row (subscriptable by index) and bare
        # tuple results (e.g. when the engine wraps the conn).
        tracked = [str(r[0]) for r in rows if r[0]]
        drifted: list[str] = []
        for layer in tracked:
            cur_hash = self._compute_schema_hash(conn, layer)
            if cur_hash is None:
                # Layer disappeared mid-check or PRAGMA returned
                # nothing — drop the cached entry so a future
                # re-creation triggers a rebuild.
                self._schema_hashes.pop(layer, None)
                continue
            cached = self._schema_hashes.get(layer)
            if cached is None:
                # First sighting since the watcher started — cache
                # without firing. Mass-replay of schema.changed at
                # boot would just spam subscribers.
                self._schema_hashes[layer] = cur_hash
                continue
            if cached == cur_hash:
                continue
            # Drift detected — repair via install_change_tracking
            # which drops the existing GISPulse triggers, ensures the
            # ``_gispulse_origin`` column (B-02), and recreates the
            # full trigger set with the v3 WHEN clause baked over the
            # *new* column list.
            try:
                from persistence.gpkg_schema import install_change_tracking

                install_change_tracking(conn, layer)
                self._schema_hashes[layer] = cur_hash
                drifted.append(layer)
            except Exception as exc:
                logger.warning(
                    "schema_drift_repair_failed layer=%s err=%s",
                    layer,
                    exc,
                )
                continue

        for layer in drifted:
            try:
                self._hub.broadcast(
                    "schema.changed",
                    {
                        "dataset_id": self._dataset_id,
                        "table": layer,
                        "change_type": "columns_changed",
                    },
                )
            except Exception as exc:
                logger.error(
                    "schema_changed_broadcast_failed layer=%s err=%s",
                    layer,
                    exc,
                )

        if drifted:
            logger.info(
                "schema_drift_repaired layers=%s", drifted
            )
        return drifted

    @staticmethod
    def _compute_schema_hash(conn: Any, layer: str) -> str | None:
        """Hash the layer's column structure (cid, name, type, notnull,
        default, pk) so add / drop / rename / type-change all flip the
        hash.

        Returns ``None`` if the table is missing or PRAGMA fails — the
        caller drops any cached hash so a future re-creation goes
        through the first-sighting path.
        """
        try:
            cols = conn.execute(
                f'PRAGMA table_info("{layer}")'
            ).fetchall()
        except Exception:
            return None
        if not cols:
            return None
        payload = "\n".join(
            "|".join(
                "" if r[i] is None else str(r[i])
                for i in range(6)
            )
            for r in cols
        )
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Bulk-mode tick (v1.3.0 #8) — pure summary helper
# ---------------------------------------------------------------------------


def _summarise_batch(
    rows: list[dict], dataset_id: str
) -> dict[str, Any]:
    """Build the ``bulk.changed`` event payload from a row batch.

    Pure function (extracted for unit testing without spinning up a
    watcher / engine). Counts ops globally and per layer, computes the
    change_id range and timestamp range, and lists the touched layers.
    """
    op_counts: dict[str, int] = {}
    by_layer: dict[str, dict[str, int]] = {}
    min_id = None
    max_id = None
    min_ts: str | None = None
    max_ts: str | None = None

    for row in rows:
        try:
            cid = int(row["id"])
        except (KeyError, TypeError, ValueError):
            continue
        if min_id is None or cid < min_id:
            min_id = cid
        if max_id is None or cid > max_id:
            max_id = cid

        table = str(row.get("table_name") or "")
        op = str(row.get("operation") or "").upper()
        op_counts[op] = op_counts.get(op, 0) + 1
        by_layer.setdefault(table, {})
        by_layer[table][op] = by_layer[table].get(op, 0) + 1

        ts = row.get("changed_at")
        if ts is not None:
            ts_str = str(ts)
            # ISO timestamps sort lexicographically.
            if min_ts is None or ts_str < min_ts:
                min_ts = ts_str
            if max_ts is None or ts_str > max_ts:
                max_ts = ts_str

    return {
        "dataset_id": dataset_id,
        "bulk": True,
        "row_count": len(rows),
        "layers": sorted(by_layer.keys()),
        "op_counts": op_counts,
        "by_layer": by_layer,
        "change_id_range": [min_id or 0, max_id or 0],
        "ts_range": [min_ts, max_ts],
    }


__all__ = ["ChangeLogWatcher"]

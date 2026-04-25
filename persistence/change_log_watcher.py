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
        poll_interval: float = 0.2,
        batch_limit: int = 100,
        trigger_evaluator: _TriggerEvaluatorProtocol | None = None,
        triggers_provider: Callable[[], list[Trigger]] | None = None,
    ) -> None:
        if poll_interval <= 0:
            raise ValueError("poll_interval must be > 0")
        if batch_limit <= 0:
            raise ValueError("batch_limit must be > 0")

        self._engine = engine
        self._hub = event_hub
        self._poll_interval = float(poll_interval)
        self._batch_limit = int(batch_limit)
        self._evaluator = trigger_evaluator
        self._triggers_provider = triggers_provider

        self._running = False
        self._thread: threading.Thread | None = None
        # Backoff window when get_pending_changes raises.
        self._error_backoff = 1.0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the polling thread (idempotent)."""
        if self._running:
            return
        self._running = True
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
        """Stop the polling thread and join (2 s timeout)."""
        self._running = False
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
    # Internals
    # ------------------------------------------------------------------

    def _run(self) -> None:
        """Polling loop — runs in the daemon thread."""
        # First sleep before the first tick keeps CPU low when no data
        # has changed yet and lets uvicorn finish startup.
        while self._running:
            time.sleep(self._poll_interval)
            if not self._running:
                break
            try:
                self._tick()
            except Exception as exc:  # pragma: no cover — defensive
                # Never let an exception escape the worker; otherwise the
                # daemon thread dies silently and live-sync stops.
                logger.exception("change_log_watcher_tick_failed: %s", exc)
                # Back off after a hard error so we don't pin a CPU when
                # the engine is temporarily unavailable.
                time.sleep(self._error_backoff)

    def _tick(self) -> int:
        """One polling cycle. Returns the number of rows processed."""
        try:
            rows = self._engine.get_pending_changes(self._batch_limit)
        except Exception as exc:
            logger.warning("change_log_get_pending_failed: %s", exc)
            time.sleep(self._error_backoff)
            return 0

        if not rows:
            return 0

        # Resolve active triggers once per batch so changes made via the
        # API show up on the next tick without restarting the watcher.
        active_triggers: list[Trigger] = []
        if self._triggers_provider is not None:
            try:
                active_triggers = list(self._triggers_provider() or [])
            except Exception as exc:
                logger.warning("change_log_triggers_provider_failed: %s", exc)
                active_triggers = []

        evaluator = self._evaluator
        if active_triggers and evaluator is None:
            # Lazy import: rules → core, persistence → core; this keeps
            # the persistence layer free of a hard rules dependency.
            from rules.trigger_evaluator import TriggerEvaluator

            evaluator = TriggerEvaluator()
            self._evaluator = evaluator

        max_id = 0
        for row in rows:
            try:
                change_id = int(row["id"])
            except (KeyError, TypeError, ValueError):
                # Malformed row — skip but don't ack so the next tick can
                # still see it (or surface the issue).
                logger.warning("change_log_row_missing_id row=%r", row)
                continue

            table = str(row.get("table_name") or "")
            op = str(row.get("operation") or "").upper()
            fid_raw = row.get("row_pk")
            fid = str(fid_raw) if fid_raw is not None else None
            ts = row.get("changed_at")

            # ---- Broadcast dml.changed ---------------------------------
            # Payload is intentionally minimal: no field values, no geom.
            # This matches the security note in Lot 2 (do not leak data
            # via /ws/events when the endpoint is unauthenticated).
            #
            # P0-4a (Beta): wrap each broadcast in its own try/except. A
            # buggy/dead subscriber must NOT abort the whole tick — that
            # would block ack and create a stuck backlog (same rows
            # re-broadcast forever).
            try:
                self._hub.broadcast(
                    "dml.changed",
                    {
                        "table": table,
                        "op": op,
                        "fid": fid,
                        "change_id": change_id,
                        "ts": ts,
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

            # ---- Evaluate triggers -------------------------------------
            if active_triggers and evaluator is not None:
                try:
                    operation = ChangeOperation(op)
                except ValueError:
                    operation = ChangeOperation.INSERT

                record = ChangeRecord(
                    session_id="",
                    table_name=table,
                    feature_id=fid,
                    operation=operation,
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
                    trigger_id = getattr(ft, "trigger_id", None)
                    try:
                        self._hub.broadcast(
                            "trigger.fired",
                            {
                                "trigger_id": str(trigger_id) if trigger_id else None,
                                "change_id": change_id,
                                "table": table,
                                "op": op,
                                "fid": fid,
                                "actions": list(getattr(ft, "actions_dispatched", []) or []),
                                "eval_time_ms": float(getattr(ft, "eval_time_ms", 0.0)),
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


__all__ = ["ChangeLogWatcher"]

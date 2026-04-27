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
    ) -> None:
        if poll_interval <= 0:
            raise ValueError("poll_interval must be > 0")
        if batch_limit <= 0:
            raise ValueError("batch_limit must be > 0")
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
        self._evaluator = trigger_evaluator
        self._triggers_provider = triggers_provider
        self._action_dispatcher = action_dispatcher
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

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the polling thread (idempotent)."""
        if self._running:
            return
        self._running = True
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
                self._tick()
            except Exception as exc:  # pragma: no cover — defensive
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
        """
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

        # Resolve active triggers once per batch so changes made via the
        # API show up on the next tick without restarting the watcher.
        active_triggers: list[Trigger] = []
        if self._triggers_provider is not None:
            try:
                active_triggers = list(self._triggers_provider() or [])
            except Exception as exc:
                logger.warning("change_log_triggers_provider_failed: %s", exc)
                active_triggers = []
        # Refresh the per-tick lookup so dispatch can recover full
        # Trigger objects from FiredTrigger.trigger_id. Only built when
        # an action_dispatcher is wired (otherwise pure overhead — and
        # tolerates triggers_provider implementations that pass in
        # placeholder strings rather than full :class:`Trigger`).
        if self._action_dispatcher is not None:
            self._trigger_lookup = {
                t.id: t for t in active_triggers if hasattr(t, "id")
            }
        else:
            self._trigger_lookup = {}

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
                        "dataset_id": self._dataset_id,
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

                # When any active trigger carries a DSL predicate
                # (S4: ``conditions["predicate_ast"]``), fetch the
                # row attributes from the underlying table so the
                # evaluator can match against real values. This is
                # opt-in per trigger — triggers without predicates
                # never pay the SELECT cost.
                #
                # We guard with table+pk presence: DELETE rows have
                # no row to load (``new_values`` stays empty, the
                # predicate then evaluates over what amounts to
                # NULLs everywhere, which is the documented semantics
                # for DSL on DELETE).
                new_values: dict[str, Any] = {}
                if (
                    op != "DELETE"
                    and fid is not None
                    and table
                    and any(
                        (getattr(t, "conditions", None) or {}).get("predicate_ast")
                        is not None
                        for t in active_triggers
                    )
                ):
                    new_values = self._load_row_values(table, fid) or {}

                record = ChangeRecord(
                    session_id="",
                    table_name=table,
                    feature_id=fid,
                    operation=operation,
                    new_values=new_values,
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
                                "dataset_id": self._dataset_id,
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

                    # ---- Dispatch actions (#458) ----------------------
                    # Bridge to ActionDispatcher so NOTIFY / WEBHOOK /
                    # SET_FIELD / RUN_SQL / … run end-to-end, not just
                    # broadcast over WS. Wrapped in try/except: the
                    # dispatcher already wraps each handler too, but a
                    # bad Trigger lookup or context build must not abort
                    # the tick.
                    if self._action_dispatcher is not None:
                        self._dispatch_fired(
                            ft,
                            table=table,
                            operation=op,
                            row_id=fid,
                            change_id=change_id,
                            ts=ts,
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


__all__ = ["ChangeLogWatcher"]

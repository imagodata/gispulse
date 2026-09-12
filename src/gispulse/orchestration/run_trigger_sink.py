"""RunCompletionTriggerSink — branche les événements run.* vers TriggerJobBridge.

Décore un RunEventSink existant (inner) et, sur chaque ``run.completed`` ou
``run.failed``, évalue les triggers de type ``on_run_completed`` et enqueue les
jobs résultants via TriggerJobBridge.

Architecture (layering) :
    orchestration/ ← pas d'import de adapters/http/*
    Le branchement du sink dans le worker se fait dans adapters/http/app.py
    (exactement comme EventHubSink).

Garantie anti-boucle infinie :
    Chaque job enqueué par ce sink reçoit ``triggered_by="run_trigger"`` et
    ``trigger_depth = event_depth + 1``. L'évaluation lit ``depth`` depuis
    les ``new_values`` du ChangeRecord synthétique, et
    ``OnRunCompletedConditions.max_depth`` bloque le tir au-delà du seuil.

Usage::

    from gispulse.orchestration.run_trigger_sink import RunCompletionTriggerSink

    sink = RunCompletionTriggerSink(
        trigger_repo=trigger_repo,
        bridge=bridge,
        inner=hub_sink,
    )
    worker = JobWorker(..., event_sink=sink)
"""
from __future__ import annotations

import asyncio
from typing import Any

from gispulse.core.logging import get_logger
from gispulse.core.models import ChangeOperation, ChangeRecord, TriggerType
from gispulse.orchestration.event_sink import NoOpSink, RunEventSink

log = get_logger(__name__)


def _log_enqueue_error(future: "asyncio.Future[Any]") -> None:  # type: ignore[name-defined]
    """Done-callback for asyncio.ensure_future — logs P3 enqueue errors."""
    try:
        exc = future.exception()
    except Exception:
        exc = None
    if exc is not None:
        log.error(
            "run_trigger_sink_enqueue_error",
            error=str(exc),
            detail="on_triggers_fired coroutine raised; original run event intact.",
        )


def _log_concurrent_future_error(future: "asyncio.futures.Future[Any]") -> None:  # type: ignore[name-defined]
    """Done-callback for run_coroutine_threadsafe concurrent.futures.Future."""
    try:
        exc = future.exception()
    except Exception:
        exc = None
    if exc is not None:
        log.error(
            "run_trigger_sink_enqueue_error",
            error=str(exc),
            detail="on_triggers_fired (threadsafe) raised; original run event intact.",
        )


# Terminal run event types that may fire on_run_completed triggers
_RUN_TERMINAL_EVENTS = frozenset({"run.completed", "run.failed"})

# Event-type → run status value forwarded in ChangeRecord.new_values
_EVENT_TO_STATUS = {
    "run.completed": "completed",
    "run.failed": "failed",
}


class RunCompletionTriggerSink:
    """Sink composite qui évalue les triggers on_run_completed sur chaque run terminal.

    Args:
        trigger_repo:  Repository<Trigger> — source des triggers actifs.
        bridge:        TriggerJobBridge — traduit FiredTrigger en Job et enqueue.
        inner:         Sink délégué (e.g. EventHubSink). Reçoit *tous* les événements
                       indépendamment de l'évaluation des triggers.
        loop:          Event loop asyncio pour l'enqueue depuis un contexte sync.
                       Si None, ``asyncio.get_event_loop()`` est utilisé.
    """

    def __init__(
        self,
        trigger_repo: Any,
        bridge: Any,
        inner: RunEventSink | None = None,
        loop: Any | None = None,
    ) -> None:
        self._trigger_repo = trigger_repo
        self._bridge = bridge
        self._inner = inner if inner is not None else NoOpSink()
        self._loop = loop

    def emit(self, event_type: str, data: dict) -> None:
        """Émet l'événement vers l'inner sink puis évalue les triggers si terminal."""
        # Toujours déléguer en premier — l'inner (EventHubSink) broadcast sur WS
        self._inner.emit(event_type, data)

        if event_type not in _RUN_TERMINAL_EVENTS:
            return

        # Évaluation des triggers on_run_completed
        try:
            self._evaluate_and_dispatch(event_type, data)
        except Exception as exc:
            log.error(
                "run_trigger_sink_eval_error",
                event_type=event_type,
                error=str(exc),
            )

    def _evaluate_and_dispatch(self, event_type: str, data: dict) -> None:
        """Évalue les triggers et enqueue les jobs déclenchés."""
        from gispulse.rules.trigger_evaluator import TriggerEvaluator
        from gispulse.core.models import Trigger

        # Récupérer les triggers on_run_completed actifs
        try:
            all_triggers: list[Trigger] = self._trigger_repo.list_all()
        except Exception as exc:
            log.warning("run_trigger_sink_repo_error", error=str(exc))
            return

        run_triggers = [
            t for t in all_triggers
            if getattr(t, "enabled", True)
            and (
                (hasattr(t.trigger_type, "value") and t.trigger_type.value == TriggerType.ON_RUN_COMPLETED.value)
                or str(t.trigger_type) == TriggerType.ON_RUN_COMPLETED.value
                or t.trigger_type == TriggerType.ON_RUN_COMPLETED
            )
        ]

        if not run_triggers:
            return

        # Construire un ChangeRecord synthétique portant le contexte du run
        run_id = data.get("run_id", "")
        status = _EVENT_TO_STATUS.get(event_type, "completed")
        depth = int(data.get("depth", 0))

        record = ChangeRecord(
            table_name="run.events",
            operation=ChangeOperation.INSERT,
            new_values={
                "run_id": run_id,
                "status": status,
                "source": data.get("source", ""),
                "spec_ref": data.get("spec_ref", ""),
                "scope": data.get("scope", ""),
                "depth": depth,
            },
        )

        evaluator = TriggerEvaluator()
        try:
            fired = evaluator.evaluate(record, run_triggers)
        except Exception as exc:
            log.warning("run_trigger_sink_evaluator_error", error=str(exc))
            return

        matched = [ft for ft in fired if ft.matched]
        if not matched:
            return

        # Build a trigger lookup to recover action configs (rule_id / graph_id)
        trigger_map = {t.id: t for t in run_triggers}

        # Enrichir chaque FiredTrigger avec le contexte run pour le job
        for ft in matched:
            ft.result_summary.update({
                "run_id": run_id,
                "scope": data.get("scope", ""),
                "status": status,
                "trigger_depth": depth + 1,
            })
            # Reconstruct full action dicts from the trigger's ActionDef.config so
            # TriggerJobBridge can extract rule_id / graph_id. The evaluator
            # stores only action_type strings in actions_dispatched; we merge
            # the config back here.
            trigger = trigger_map.get(ft.trigger_id)
            trigger_action_configs: dict[str, dict] = {}
            if trigger is not None:
                for adef in trigger.actions:
                    atype = adef.action_type.value if hasattr(adef.action_type, "value") else str(adef.action_type)
                    trigger_action_configs[atype] = dict(adef.config) if adef.config else {}

            enriched_actions = []
            for action in ft.actions_dispatched:
                if isinstance(action, dict):
                    a = dict(action)
                else:
                    # action may be an ActionType enum (str subclass) or a plain string
                    # Normalise to the .value string so the lookup key is consistent
                    aval = action.value if hasattr(action, "value") else str(action)
                    a = {"action_type": aval}
                    # Merge rule_id / graph_id from the original ActionDef.config
                    a.update(trigger_action_configs.get(aval, {}))
                a.setdefault("trigger_depth", depth + 1)
                a.setdefault("scope", data.get("scope", ""))
                enriched_actions.append(a)
            ft.actions_dispatched = enriched_actions

        # Enqueue — bridge.on_triggers_fired est async; on schedule sur l'event loop.
        #
        # Quatre cas :
        #   A) emit() appelé DEPUIS le thread de la loop (worker asyncio) ET loop
        #      fournie → asyncio.ensure_future (schedule dans la loop courante).
        #   B) emit() appelé DEPUIS un thread sync (FastAPI handler thread) ET loop
        #      fournie → asyncio.run_coroutine_threadsafe (thread-safe cross-thread).
        #   C) Pas de loop fournie mais une loop en cours → ensure_future (même thread).
        #   D) Aucune loop disponible → contexte synchrone pur (tests sans asyncio).
        #
        # Détermination A vs B : on tente get_running_loop() ; si ça retourne la
        # même loop que self._loop on est sur le bon thread (cas A) ; si ça lève
        # RuntimeError ou retourne une loop différente, on est dans un thread
        # externe (cas B).
        loop = self._loop

        # Attempt to detect whether we're on the event loop thread.
        try:
            calling_loop = asyncio.get_running_loop()
        except RuntimeError:
            calling_loop = None

        if loop is not None and loop.is_running():
            if calling_loop is loop:
                # Case A: we ARE on the event loop thread — schedule directly.
                future = asyncio.ensure_future(
                    self._bridge.on_triggers_fired(matched),
                    loop=loop,
                )
                future.add_done_callback(_log_enqueue_error)
            else:
                # Case B: called from a sync handler thread — use thread-safe variant.
                future = asyncio.run_coroutine_threadsafe(
                    self._bridge.on_triggers_fired(matched),
                    loop,
                )
                future.add_done_callback(_log_concurrent_future_error)
        elif calling_loop is not None and calling_loop.is_running():
            # Case C: no explicit loop stored but one is currently running (rare).
            future = asyncio.ensure_future(
                self._bridge.on_triggers_fired(matched),
                loop=calling_loop,
            )
            future.add_done_callback(_log_enqueue_error)
        else:
            # Case D: purely synchronous context (unit tests without asyncio).
            if loop is None and calling_loop is None:
                log.warning(
                    "run_trigger_sink_triggers_discarded",
                    reason="no_event_loop",
                    matched_triggers=len(matched),
                    detail=(
                        "No event loop available. Triggers were matched but "
                        "on_triggers_fired could not be scheduled. "
                        "Ensure RunCompletionTriggerSink receives the lifespan "
                        "loop via the 'loop' constructor argument."
                    ),
                )
            jobs = self._bridge.on_triggers_fired_sync(matched)
            log.info("run_trigger_sink_jobs_created_sync", count=len(jobs))
            return

        log.info(
            "run_trigger_sink_dispatched",
            event_type=event_type,
            run_id=run_id,
            matched_triggers=len(matched),
        )

"""Central ESB event router for GISPulse.

Receives events from all sources (DML triggers, CRON, API, manual),
loads active predicates, evaluates them, and dispatches actions.
Connects :class:`TriggerManager`, :class:`PredicateEvaluator`,
:class:`ActionDispatcher`, and :class:`EventHub`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from shapely.geometry.base import BaseGeometry
from shapely import wkt as shapely_wkt

from gispulse.core.logging import get_logger
from gispulse.core.models import (
    ActionDef,
    EvalResult,
    Trigger,
)
from gispulse.adapters.esb.predicate_evaluator import PredicateEvaluator
from gispulse.adapters.esb.action_dispatcher import ActionDispatcher, TriggerContext

log = get_logger(__name__)


@dataclass
class DMLPayload:
    """Parsed DML event from PostgreSQL NOTIFY."""
    table: str
    schema: str = "public"
    operation: str = ""   # INSERT, UPDATE, DELETE
    row_id: str = ""
    trigger_id: str = ""
    new_geom: BaseGeometry | None = None
    new_attrs: dict[str, Any] = field(default_factory=dict)
    old_geom: BaseGeometry | None = None
    old_attrs: dict[str, Any] | None = None


class EventRouter:
    """Central orchestrator for ESB events.

    Args:
        predicate_evaluator: Evaluator for spatial/attr predicates.
        action_dispatcher:   Multi-action dispatcher.
        event_hub:           WebSocket broadcaster (optional).
        trigger_loader:      ``(table: str) -> list[Trigger]`` — load
                             active triggers for a given table.
    """

    def __init__(
        self,
        predicate_evaluator: PredicateEvaluator,
        action_dispatcher: ActionDispatcher,
        event_hub: Any | None = None,
        trigger_loader: Any | None = None,
    ) -> None:
        self._evaluator = predicate_evaluator
        self._dispatcher = action_dispatcher
        self._event_hub = event_hub
        self._trigger_loader = trigger_loader
        self._stats = _RouterStats()

    # ------------------------------------------------------------------
    # DML events (from TriggerManager LISTEN/NOTIFY)
    # ------------------------------------------------------------------

    def handle_dml_event(self, payload: DMLPayload) -> list[EvalResult]:
        """Full pipeline: DML → load predicates → evaluate → dispatch.

        Returns list of EvalResults for all matched predicates.
        """
        self._stats.events_received += 1
        triggers = self._load_triggers(payload.table)
        if not triggers:
            return []

        results: list[EvalResult] = []
        for trigger in triggers:
            if not trigger.predicates:
                # Legacy trigger without structured predicates — use old path
                self._dispatch_legacy(trigger, payload)
                continue

            eval_result = self._evaluate_trigger(trigger, payload)
            if eval_result.matched:
                self._stats.predicates_matched += 1
                context = TriggerContext(
                    trigger=trigger,
                    eval_result=eval_result,
                    table=payload.table,
                    operation=payload.operation,
                    row_id=payload.row_id,
                    new_attrs=payload.new_attrs,
                    old_attrs=payload.old_attrs,
                )
                actions = trigger.actions or self._default_actions(trigger)
                self._dispatcher.dispatch_all(actions, context)
                self._broadcast_event(trigger, eval_result, payload)
                results.append(eval_result)

        return results

    # ------------------------------------------------------------------
    # CRON events
    # ------------------------------------------------------------------

    def handle_cron_event(self, trigger: Trigger) -> None:
        """Handle a scheduled trigger firing."""
        log.info("cron_event", trigger_id=str(trigger.id), name=trigger.name)
        self._stats.events_received += 1
        actions = trigger.actions or self._default_actions(trigger)
        context = TriggerContext(
            trigger=trigger,
            eval_result=EvalResult(matched=True),
            table="",
            operation="CRON",
        )
        self._dispatcher.dispatch_all(actions, context)

    # ------------------------------------------------------------------
    # API / manual events
    # ------------------------------------------------------------------

    def handle_api_event(
        self, trigger: Trigger, data: dict[str, Any] | None = None
    ) -> EvalResult | None:
        """Handle a manually or API-triggered event."""
        log.info("api_event", trigger_id=str(trigger.id), name=trigger.name)
        self._stats.events_received += 1
        data = data or {}

        # If the trigger has predicates, evaluate them
        if trigger.predicates:
            geom = None
            geom_wkt = data.get("geom_wkt")
            if geom_wkt:
                geom = shapely_wkt.loads(geom_wkt)
            attrs = data.get("attrs", {})

            # Build a DMLPayload to reuse _evaluate_trigger for full predicate tree
            payload = DMLPayload(
                table=data.get("table", ""),
                operation="API",
                row_id=data.get("row_id", ""),
                new_geom=geom,
                new_attrs=attrs,
            )
            eval_result = self._evaluate_trigger(trigger, payload)
            if not eval_result.matched:
                return eval_result
        else:
            eval_result = EvalResult(matched=True)

        actions = trigger.actions or self._default_actions(trigger)
        context = TriggerContext(
            trigger=trigger,
            eval_result=eval_result,
            table=data.get("table", ""),
            operation="API",
            row_id=data.get("row_id", ""),
            new_attrs=data.get("attrs", {}),
        )
        self._dispatcher.dispatch_all(actions, context)
        return eval_result

    # ------------------------------------------------------------------
    # Raw NOTIFY callback (wire into TriggerManager)
    # ------------------------------------------------------------------

    def handle_notify(self, payload_str: str) -> list[EvalResult]:
        """Parse a raw pg_notify JSON payload and route it."""
        try:
            raw = json.loads(payload_str)
        except json.JSONDecodeError:
            log.warning("event_router_bad_payload", raw=payload_str[:200])
            return []

        geom = None
        geom_wkt = raw.get("new_geom_wkt")
        if geom_wkt:
            geom = shapely_wkt.loads(geom_wkt)

        old_geom = None
        old_wkt = raw.get("old_geom_wkt")
        if old_wkt:
            old_geom = shapely_wkt.loads(old_wkt)

        dml = DMLPayload(
            table=raw.get("table", ""),
            schema=raw.get("schema", "public"),
            operation=raw.get("operation", ""),
            row_id=raw.get("row_id", ""),
            trigger_id=raw.get("trigger_id", ""),
            new_geom=geom,
            new_attrs=raw.get("new_attrs", {}),
            old_geom=old_geom,
            old_attrs=raw.get("old_attrs"),
        )
        return self.handle_dml_event(dml)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_triggers(self, table: str) -> list[Trigger]:
        if self._trigger_loader:
            return self._trigger_loader(table)
        return []

    def _evaluate_trigger(
        self, trigger: Trigger, payload: DMLPayload
    ) -> EvalResult:
        """Evaluate all predicates on a trigger according to its logic."""
        if not trigger.predicates:
            return EvalResult(matched=True)

        results = [
            self._evaluator.evaluate_with_transition(
                pred,
                object_id=UUID(payload.row_id) if payload.row_id else None,
                predicate_id=trigger.id,
                new_geom=payload.new_geom,
                new_attrs=payload.new_attrs,
            )
            for pred in trigger.predicates
        ]

        if trigger.predicate_logic == "AND":
            matched = all(r.matched for r in results)
        else:
            matched = any(r.matched for r in results)

        # Use the first transition found
        transition = next((r.transition for r in results if r.transition), None)
        total_time = sum(r.eval_time_ms for r in results)

        return EvalResult(
            matched=matched,
            transition=transition,
            eval_time_ms=round(total_time, 3),
        )

    def _dispatch_legacy(self, trigger: Trigger, payload: DMLPayload) -> None:
        """Backward-compatible dispatch for triggers without structured predicates."""
        if trigger.rule_id and trigger.actions:
            context = TriggerContext(
                trigger=trigger,
                eval_result=EvalResult(matched=True),
                table=payload.table,
                operation=payload.operation,
                row_id=payload.row_id,
                new_attrs=payload.new_attrs,
            )
            self._dispatcher.dispatch_all(trigger.actions, context)

    @staticmethod
    def _default_actions(trigger: Trigger) -> list[ActionDef]:
        """Build default action list from legacy rule_id."""
        from gispulse.core.models import ActionType
        actions = []
        if trigger.rule_id:
            actions.append(ActionDef(
                action_type=ActionType.RUN_JOB,
                config={"rule_id": str(trigger.rule_id)},
            ))
        return actions

    def _broadcast_event(
        self, trigger: Trigger, result: EvalResult, payload: DMLPayload
    ) -> None:
        if self._event_hub:
            self._event_hub.broadcast("trigger_fired", {
                "trigger_id": str(trigger.id),
                "trigger_name": trigger.name,
                "table": payload.table,
                "operation": payload.operation,
                "row_id": payload.row_id,
                "matched": result.matched,
                "transition": result.transition.value if result.transition else None,
                "eval_time_ms": result.eval_time_ms,
            })

    @property
    def stats(self) -> dict[str, int]:
        return {
            "events_received": self._stats.events_received,
            "predicates_matched": self._stats.predicates_matched,
        }


@dataclass
class _RouterStats:
    events_received: int = 0
    predicates_matched: int = 0

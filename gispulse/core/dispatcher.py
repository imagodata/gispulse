"""
Base dispatcher for GISPulse ESB actions.

Provides a generic interface for action dispatching, compatible with
both the current PostgreSQL-based ESB and future hybrid systems (e.g., SousByzcje).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from core.models import ActionDef, EvalResult, Trigger


@dataclass
class TriggerContext:
    """Context passed to action handlers."""
    trigger: Trigger
    eval_result: EvalResult
    table: str = ""
    operation: str = ""  # INSERT, UPDATE, DELETE
    row_id: str = ""
    new_attrs: Dict[str, Any] = field(default_factory=dict)
    old_attrs: Optional[Dict[str, Any]] = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class BaseDispatcher(ABC):
    """Abstract base class for action dispatchers.

    Args:
        job_runner:     Callable to run jobs (e.g., ``(rule_id, table, row_id) -> None``).
        graph_runner:   Callable to run graphs (e.g., ``(graph_id, params) -> None``).
        event_hub:      Object with ``broadcast(event_type, data)`` method.
        sql_executor:   Callable to execute SQL (e.g., ``(sql, params) -> Any``).
        webhook_client: Callable to send webhooks (e.g., ``(url, payload) -> None``).n    """

    def __init__(
        self,
        job_runner: Optional[Callable] = None,
        graph_runner: Optional[Callable] = None,
        event_hub: Optional[Any] = None,
        sql_executor: Optional[Callable] = None,
        webhook_client: Optional[Callable] = None,
    ):
        self._job_runner = job_runner
        self._graph_runner = graph_runner
        self._event_hub = event_hub
        self._sql_executor = sql_executor
        self._webhook_client = webhook_client

    @abstractmethod
    def dispatch(self, action: ActionDef, context: TriggerContext) -> None:
        """Dispatch a single action."""
        pass

    @abstractmethod
    def dispatch_all(self, actions: List[ActionDef], context: TriggerContext) -> int:
        """Dispatch multiple actions. Returns count of successful dispatches."""
        pass

    @staticmethod
    def _render_payload(action: ActionDef, context: TriggerContext) -> Dict[str, Any]:
        """Render payload for webhooks or notifications."""
        template = action.config.get("payload_template", {})
        if isinstance(template, str):
            rendered = template
            for key, val in context.new_attrs.items():
                rendered = rendered.replace(f"{{NEW.{key}}}", str(val))
            return {"message": rendered}
        return {
            "trigger_id": str(context.trigger.id),
            "table": context.table,
            "row_id": context.row_id,
            "transition": context.eval_result.transition.value if context.eval_result.transition else None,
            **template,
        }
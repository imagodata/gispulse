"""
Trigger → JobQueue bridge.

Connects TriggerEvaluator results to the job execution pipeline.
When triggers fire with RUN_JOB or RUN_GRAPH actions, this bridge
creates Job objects and enqueues them for execution by JobWorker.

Usage::

    bridge = TriggerJobBridge(job_queue, dataset_repo)
    # After trigger evaluation:
    bridge.on_triggers_fired(fired_triggers)
"""
from __future__ import annotations

from typing import Any

from gispulse.core.logging import get_logger
from gispulse.core.models import FiredTrigger, Job

log = get_logger(__name__)


class TriggerJobBridge:
    """Routes fired trigger actions to the job queue.

    Handles action types:
    - RUN_JOB: creates a Job with the rule_id and enqueues it
    - RUN_GRAPH: creates a Job with graph_id and enqueues it
    """

    def __init__(
        self,
        job_queue: Any,
        dataset_repo: Any | None = None,
    ) -> None:
        """
        Args:
            job_queue: JobQueue instance (InMemoryJobQueue or RedisJobQueue).
            dataset_repo: Optional dataset repository for resolving dataset IDs.
        """
        self._queue = job_queue
        self._dataset_repo = dataset_repo

    async def on_triggers_fired(self, fired: list[FiredTrigger]) -> list[Job]:
        """Process fired triggers and enqueue jobs for matching actions.

        Args:
            fired: List of FiredTrigger results from TriggerEvaluator.

        Returns:
            List of Job objects that were enqueued.
        """
        jobs_created: list[Job] = []

        for ft in fired:
            if not ft.matched:
                continue

            for action in ft.actions_dispatched:
                action_type, action_dict = self._normalize_action(action)

                if action_type == "RUN_JOB":
                    job = self._create_job_from_trigger(ft, action_dict)
                    if job:
                        await self._queue.enqueue(job)
                        jobs_created.append(job)
                        log.info(
                            "trigger_job_enqueued",
                            trigger_id=ft.trigger_id,
                            job_id=str(job.id),
                        )

                elif action_type == "RUN_GRAPH":
                    job = self._create_graph_job_from_trigger(ft, action_dict)
                    if job:
                        await self._queue.enqueue(job)
                        jobs_created.append(job)
                        log.info(
                            "trigger_graph_job_enqueued",
                            trigger_id=ft.trigger_id,
                            job_id=str(job.id),
                        )

        return jobs_created

    def on_triggers_fired_sync(self, fired: list[FiredTrigger]) -> list[Job]:
        """Synchronous version for non-async contexts.

        Creates jobs but does NOT enqueue them (caller must enqueue).

        Args:
            fired: FiredTrigger list. Each trigger's ``actions_dispatched``
                   can be a list of dicts (with ``action_type`` key) or
                   a list of action-type strings.

        Returns:
            List of Job objects ready to be enqueued.
        """
        jobs: list[Job] = []
        for ft in fired:
            if not ft.matched:
                continue
            for action in ft.actions_dispatched:
                action_type, action_dict = self._normalize_action(action)
                if action_type == "RUN_JOB":
                    job = self._create_job_from_trigger(ft, action_dict)
                    if job:
                        jobs.append(job)
                elif action_type == "RUN_GRAPH":
                    job = self._create_graph_job_from_trigger(ft, action_dict)
                    if job:
                        jobs.append(job)
        return jobs

    @staticmethod
    def _normalize_action(action: Any) -> tuple[str, dict]:
        """Normalize an action to (action_type_upper, action_dict).

        Accepts:
        - dict with ``action_type`` key (may be "RUN_JOB" or "run_job")
        - str / ActionType enum (str subclass) — converted to UPPER for comparison
        """
        if isinstance(action, dict):
            raw_type = action.get("action_type", "")
            # Normalize: enum value "run_job" → "RUN_JOB"; already upper stays upper
            if hasattr(raw_type, "value"):
                raw_type = raw_type.value
            return str(raw_type).upper(), action
        # str / ActionType enum subclass
        raw = getattr(action, "value", None) or str(action)
        return str(raw).upper(), {}

    @staticmethod
    def _extract_run_context(ft: FiredTrigger, action: dict) -> dict:
        """Extract run-chain context (depth, scope) from a FiredTrigger action.

        These keys are injected by RunCompletionTriggerSink when an
        on_run_completed trigger fires, so downstream jobs know their depth
        in the trigger chain and can parameterize (e.g. inherit scope).
        """
        ctx: dict = {}
        depth = action.get("trigger_depth")
        if depth is not None:
            ctx["trigger_depth"] = int(depth)
        scope = action.get("scope") or ft.result_summary.get("scope")
        if scope:
            ctx["scope"] = str(scope)
        run_id = ft.result_summary.get("run_id")
        if run_id:
            ctx["source_run_id"] = str(run_id)
        return ctx

    def _create_job_from_trigger(
        self, ft: FiredTrigger, action: Any
    ) -> Job | None:
        """Create a Job from a RUN_JOB trigger action."""
        rule_id = action.get("rule_id") if isinstance(action, dict) else None
        if not rule_id:
            log.warning("trigger_run_job_no_rule_id", trigger_id=ft.trigger_id)
            return None

        params = {
            "rule_ids": [str(rule_id)],
            "triggered_by": "trigger",
            "trigger_id": str(ft.trigger_id),
            "change_record_id": str(ft.change_record_id) if ft.change_record_id else None,
        }
        params.update(self._extract_run_context(ft, action if isinstance(action, dict) else {}))
        return Job(
            name=f"trigger_{ft.trigger_id}",
            parameters=params,
        )

    def _create_graph_job_from_trigger(
        self, ft: FiredTrigger, action: Any
    ) -> Job | None:
        """Create a Job from a RUN_GRAPH trigger action."""
        graph_id = action.get("graph_id") if isinstance(action, dict) else None
        if not graph_id:
            log.warning("trigger_run_graph_no_graph_id", trigger_id=ft.trigger_id)
            return None

        params = {
            "graph_id": str(graph_id),
            "triggered_by": "trigger",
            "trigger_id": str(ft.trigger_id),
            "change_record_id": str(ft.change_record_id) if ft.change_record_id else None,
        }
        params.update(self._extract_run_context(ft, action if isinstance(action, dict) else {}))
        return Job(
            name=f"trigger_graph_{ft.trigger_id}",
            parameters=params,
        )

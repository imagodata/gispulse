"""RunEventSink protocol for pipeline lifecycle events.

Defines the minimal contract that execution layers (PipelineExecutor,
GraphExecutor, JobWorker) use to emit events without importing the
HTTP adapter layer.

The actual EventHubSink implementation lives in adapters/http/app.py
and is injected at construction time when the HTTP stack is running.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from gispulse.core.run_models import PipelineRun


@runtime_checkable
class RunEventSink(Protocol):
    """Emit a lifecycle event for a pipeline run.

    Args:
        event_type: e.g. "run.started", "run.step.completed", "run.failed"
        data:       JSON-serializable dict. Must not contain non-serializable
                    types (UUIDs/datetimes must be pre-converted to str/ISO).
    """

    def emit(self, event_type: str, data: dict) -> None: ...  # pragma: no cover


class NoOpSink:
    """Default no-op implementation — drops all events silently.

    Used when no sink is injected (backward-compat: all existing callers
    that don't pass an event_sink get this behavior automatically).
    """

    def emit(self, event_type: str, data: dict) -> None:
        pass


class RecordingSink:
    """Composable sink that records run.step.* events into a PipelineRun entity.

    On each step event it updates the entity in memory, persists it via
    ``run_repo`` (if provided), then delegates to the inner sink (e.g.
    EventHubSink) so the event is still broadcast over WebSocket.

    This is the single source of truth for PipelineRunStep persistence:
    the worker creates the run record, this sink keeps it up-to-date
    throughout execution, so GET /runs/{id} always reflects current steps.
    """

    def __init__(
        self,
        run: PipelineRun,
        run_repo: Any | None = None,
        inner: RunEventSink | None = None,
    ) -> None:
        self._run = run
        self._run_repo = run_repo
        self._inner = inner if inner is not None else NoOpSink()

    def emit(self, event_type: str, data: dict) -> None:
        from gispulse.core.run_models import PipelineRunStep
        from gispulse.core.models import JobStatus

        step_id = data.get("step_id")
        if step_id is not None:
            if event_type == "run.step.started":
                step = PipelineRunStep(
                    step_id=step_id,
                    status=JobStatus.RUNNING,
                    started_at=datetime.now(timezone.utc),
                )
                # Replace or append — idempotent on step_id
                existing = next((s for s in self._run.steps if s.step_id == step_id), None)
                if existing is None:
                    self._run.steps.append(step)
                else:
                    existing.status = JobStatus.RUNNING
                    existing.started_at = step.started_at
                    existing.ended_at = None
                    existing.error = ""
            elif event_type in ("run.step.completed", "run.step.failed"):
                existing = next((s for s in self._run.steps if s.step_id == step_id), None)
                if existing is not None:
                    existing.status = JobStatus.COMPLETED if event_type == "run.step.completed" else JobStatus.FAILED
                    existing.ended_at = datetime.now(timezone.utc)
                    existing.error = data.get("error", "")

            if self._run_repo is not None:
                self._run_repo.save(self._run)

        # Always delegate so the inner sink (e.g. EventHubSink) still broadcasts
        self._inner.emit(event_type, data)

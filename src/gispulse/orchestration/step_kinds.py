"""Step-kind registry for GISPulse pipeline executor.

Extends the step execution contract beyond ``capability`` (GeoDataFrame →
GeoDataFrame) to support **long-running external work** (dbt builds, tile
exports, ingest scripts) via a pluggable kind-handler system.

The registry mirrors the probe-kind registry in :mod:`gispulse.core.readiness`:
``register_step_kind(name, handler)`` is idempotent-safe (duplicate →
:exc:`ValueError` unless *replace=True*), and built-in kinds are registered
lazily via :func:`_ensure_builtin_kinds`.

Handler signature::

    (params: dict, context: StepContext) -> StepResult

``StepContext`` gives the handler access to run/step ids, the scope string, an
event-emitting sink, a cancel-check callable, and a heartbeat callable.
The handler must call ``context.heartbeat()`` regularly for long-running work so
the worker's stuck-job recovery (``recover_stuck_jobs``) does not kill a healthy
run.

``StepResult`` is the uniform return type for all non-capability kinds.
``StepResult.artifacts["skip_marker"]`` carries the optional opaque token emitted
by a process writing ``GISPULSE_SKIP_MARKER=<opaque>`` to stdout — consumed by a
future resume feature.
"""

from __future__ import annotations

import threading as _threading
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# StepContext
# ---------------------------------------------------------------------------


@runtime_checkable
class _EventSinkProto(Protocol):
    def emit(self, event_type: str, data: dict) -> None: ...


@dataclass
class StepContext:
    """Runtime context passed to every non-capability step handler.

    Attributes:
        run_id:        UUID string of the parent run.
        step_id:       Step identifier from the PipelineSpec.
        scope:         Optional scope string (e.g. department code).
        event_sink:    Sink for lifecycle + progress events.
        cancel_check:  Returns ``True`` when the job has been cancelled.
        heartbeat:     Callable that must be called periodically to signal
                       liveness and prevent stuck-job recovery from killing
                       a healthy long-running step.
        resume_marker: Opaque token from the previous run's step artifacts
                       (``PipelineRunStep.artifacts["skip_marker"]``).
                       Non-empty only when this step is being re-executed as
                       part of a resume and the previous run's corresponding
                       step emitted a ``GISPULSE_SKIP_MARKER=<token>`` line.
                       The ``external`` kind passes this token to the subprocess
                       via the ``GISPULSE_RESUME_MARKER`` environment variable
                       so idempotent scripts can restart from a known checkpoint.
                       Contract: resume assumes steps are idempotent — the
                       marker is a hint, not a guarantee of consistency.
    """

    run_id: str
    step_id: str
    scope: str = ""
    event_sink: Any = None  # RunEventSink (avoid circular import)
    cancel_check: Callable[[], bool] = field(default_factory=lambda: (lambda: False))
    heartbeat: Callable[[], None] = field(default_factory=lambda: (lambda: None))
    resume_marker: str = ""

    def emit_progress(self, data: dict) -> None:
        """Emit a ``run.step.progress`` event via the injected event sink.

        Args:
            data: Arbitrary JSON-serialisable progress metadata. The step_id
                  and run_id are injected automatically.
        """
        if self.event_sink is None:
            return
        payload = {"run_id": self.run_id, "step_id": self.step_id, **data}
        self.event_sink.emit("run.step.progress", payload)


# ---------------------------------------------------------------------------
# StepResult
# ---------------------------------------------------------------------------


@dataclass
class StepResult:
    """Return type for non-capability step handlers.

    Attributes:
        status:    ``"completed"`` or ``"failed"``.
        artifacts: Opaque dict preserved in ``PipelineRunStep.artifacts``
                   after execution (log_tail, skip_marker, …).
        metrics:   Numeric counters emitted in the ``run.step.completed``
                   payload (lines_processed, duration_seconds, …).
        error:     Human-readable error message when status is ``"failed"``.
    """

    status: str = "completed"
    artifacts: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    error: str = ""


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

# name → handler callable
_STEP_KIND_REGISTRY: dict[
    str, Callable[[dict, StepContext], StepResult]
] = {}

_BUILTINS_LOCK = _threading.Lock()
_BUILTINS_REGISTERED: bool = False


def register_step_kind(
    name: str,
    handler: Callable[[dict, StepContext], StepResult],
    *,
    replace: bool = False,
) -> None:
    """Register a step-kind handler.

    Mirrors :func:`gispulse.core.readiness.register_probe_kind`.

    Args:
        name:    Unique identifier (e.g. ``"external"``, ``"dbt_build"``).
        handler: Callable ``(params, context) -> StepResult``.
        replace: When ``False`` (default), a second registration for the same
                 name raises :exc:`ValueError`. Set ``True`` to override.

    Raises:
        ValueError: If *name* is already registered and *replace* is ``False``.
    """
    if name in _STEP_KIND_REGISTRY and not replace:
        raise ValueError(
            f"step kind {name!r} is already registered; "
            "pass replace=True to override"
        )
    _STEP_KIND_REGISTRY[name] = handler


def get_step_kind_handler(
    name: str,
) -> Callable[[dict, StepContext], StepResult] | None:
    """Return the handler for *name*, or ``None`` if not registered."""
    return _STEP_KIND_REGISTRY.get(name)


def list_step_kinds() -> list[str]:
    """Return sorted list of all registered step-kind names."""
    return sorted(_STEP_KIND_REGISTRY.keys())


def _ensure_builtin_kinds() -> None:
    """Register the built-in step kinds exactly once (idempotent, thread-safe).

    Mirrors ``_ensure_fetchers_registered`` in ``gispulse.persistence.loader``.
    Called at the start of ``_execute_step_kind`` so the external kind is
    available in production without requiring callers to import external_step.
    """
    global _BUILTINS_REGISTERED
    if _BUILTINS_REGISTERED:
        return
    with _BUILTINS_LOCK:
        if _BUILTINS_REGISTERED:
            return
        from gispulse.orchestration import external_step as _  # noqa: F401

        _BUILTINS_REGISTERED = True

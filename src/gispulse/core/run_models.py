"""Pipeline run entity and step models for GISPulse.

Tracks execution state for every pipeline run (job, schedule,
scenario, manifest). Persisted via RunRepository (SQLite).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID, uuid4

from gispulse.core.enums import JobStatus


@dataclass
class PipelineRunStep:
    """State of a single step within a PipelineRun.

    Attributes:
        step_id:    Identifier matching the StepSpec/NodeDef id.
        status:     Current status (reuses JobStatus: RUNNING/COMPLETED/FAILED).
        started_at: UTC timestamp when the step started.
        ended_at:   UTC timestamp when the step finished (None if still running).
        attempt:    Retry counter, 1-based.
        error:      Error message if status=FAILED, empty string otherwise.
        artifacts:  Opaque dict persisted after non-capability step completion.
                    Carries ``log_tail``, ``skip_marker``, and other kind-specific
                    artefacts.  Empty for capability steps.
    """

    step_id: str
    status: JobStatus = JobStatus.RUNNING
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    ended_at: datetime | None = None
    attempt: int = 1
    error: str = ""
    artifacts: dict = field(default_factory=dict)


@dataclass
class PipelineRun:
    """Execution record for a single pipeline invocation.

    One PipelineRun is created per job/schedule/scenario/manifest execution.
    Steps are appended as the executor progresses.

    Attributes:
        run_id:     Unique UUID for this run.
        source:     Entry point: "job" | "schedule" | "scenario" | "manifest".
        spec_ref:   Human-readable reference (job name, schedule name, etc.).
        scope:      Optional scope string (e.g. department code for ingest jobs).
        status:     Aggregate status (RUNNING -> COMPLETED | FAILED).
        started_at: UTC timestamp when the run started.
        ended_at:   UTC timestamp when the run finished.
        error:      Top-level error message if status=FAILED.
        steps:      Ordered list of step execution records.
    """

    source: str
    spec_ref: str
    run_id: UUID = field(default_factory=uuid4)
    scope: str = ""
    status: JobStatus = JobStatus.RUNNING
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    ended_at: datetime | None = None
    error: str = ""
    steps: list[PipelineRunStep] = field(default_factory=list)
    depth: int = 0

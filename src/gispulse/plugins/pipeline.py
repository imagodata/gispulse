"""Pipeline primitives supported for external plugin authors."""

from __future__ import annotations

from gispulse.core.pipeline import PipelineSpec, StepSpec
from gispulse.orchestration.pipeline_executor import PipelineExecutor

__all__ = ["PipelineExecutor", "PipelineSpec", "StepSpec"]

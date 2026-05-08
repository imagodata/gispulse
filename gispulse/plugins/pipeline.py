"""Pipeline primitives supported for external plugin authors."""

from __future__ import annotations

from core.pipeline import PipelineSpec, StepSpec
from orchestration.pipeline_executor import PipelineExecutor

__all__ = ["PipelineExecutor", "PipelineSpec", "StepSpec"]

"""Unit tests for the GISPulse job runner (orchestration)."""

from __future__ import annotations

import time
from uuid import uuid4

import geopandas as gpd
import pytest
from shapely.geometry import Point

from gispulse.core.models import Job, JobStatus, Rule
from gispulse.orchestration.runner import JobRunner
from gispulse.persistence.repository import InMemoryRepository
from gispulse.rules.engine import RuleEngine

# Minimal v2 pipeline_config dict as produced by the scheduler (_enqueue_pipeline)
_SCHEDULER_PIPELINE_CONFIG = {
    "version": 2,
    "name": "scheduled_filter_pipeline",
    "steps": [
        {
            "id": "step_filter",
            "type": "capability",
            "capability": "filter",
            "params": {"expression": "value > 10"},
        },
    ],
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def point_gdf() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "id": [1, 2, 3],
            "value": [5, 15, 25],
            "geometry": [Point(2.35, 48.85), Point(2.30, 48.87), Point(2.40, 48.90)],
        },
        crs="EPSG:4326",
    )


@pytest.fixture
def setup_runner():
    """Returns (runner, repo) with a pre-populated repository."""
    repo: InMemoryRepository = InMemoryRepository()
    engine = RuleEngine(repository=repo)
    runner = JobRunner(repository=repo, rule_engine=engine)
    return runner, repo


# ---------------------------------------------------------------------------
# Job runner tests
# ---------------------------------------------------------------------------


class TestJobRunner:
    def test_run_job_no_rules(self, setup_runner, point_gdf):
        runner, repo = setup_runner
        job = Job(name="empty_job", parameters={})
        updated_job, result = runner.run(job, point_gdf)
        assert updated_job.status == JobStatus.COMPLETED
        assert updated_job.started_at is not None
        assert updated_job.completed_at is not None
        assert len(result) == len(point_gdf)

    def test_run_job_with_filter_rule(self, setup_runner, point_gdf):
        runner, repo = setup_runner
        rule = Rule(
            name="filter_rule",
            capability="filter",
            config={"expression": "value > 10"},
        )
        repo.save(rule)
        job = Job(
            name="filter_job",
            parameters={"rule_ids": [str(rule.id)]},
        )
        updated_job, result = runner.run(job, point_gdf)
        assert updated_job.status == JobStatus.COMPLETED
        assert len(result) == 2

    def test_run_job_status_running_then_completed(self, setup_runner, point_gdf):
        runner, repo = setup_runner
        job = Job(name="test_job")

        # Before run
        assert job.status == JobStatus.PENDING
        assert job.started_at is None

        updated_job, _ = runner.run(job, point_gdf)
        assert updated_job.status == JobStatus.COMPLETED
        assert updated_job.started_at is not None
        assert updated_job.completed_at is not None
        assert updated_job.completed_at >= updated_job.started_at

    def test_run_job_with_invalid_rule_fails(self, setup_runner, point_gdf):
        runner, repo = setup_runner
        rule = Rule(
            name="bad_capability_rule",
            capability="nonexistent_capability",
            config={},
        )
        repo.save(rule)
        job = Job(
            name="failing_job",
            parameters={"rule_ids": [str(rule.id)]},
        )
        # Validation now raises ValueError before reaching the registry KeyError
        with pytest.raises((KeyError, ValueError)):
            runner.run(job, point_gdf)

        assert job.status == JobStatus.FAILED
        assert job.completed_at is not None

    def test_run_job_all_rule_ids_missing_raises(self, setup_runner, point_gdf):
        """Job with all rule IDs unresolvable now fails instead of silently completing."""
        runner, repo = setup_runner
        missing_id = str(uuid4())
        job = Job(
            name="skip_missing_rule",
            parameters={"rule_ids": [missing_id]},
        )
        with pytest.raises(ValueError, match="none of the .* requested rule"):
            runner.run(job, point_gdf)
        assert job.status == JobStatus.FAILED

    def test_run_job_pipeline(self, setup_runner, point_gdf):
        """Chain filter -> reproject."""
        runner, repo = setup_runner
        rule_filter = Rule(
            name="filter",
            capability="filter",
            config={"expression": "value > 10", "order": 1},
        )
        rule_reproject = Rule(
            name="reproject",
            capability="reproject",
            config={"target_crs": "EPSG:3857", "order": 2},
        )
        repo.save(rule_filter)
        repo.save(rule_reproject)
        job = Job(
            name="pipeline_job",
            parameters={"rule_ids": [str(rule_filter.id), str(rule_reproject.id)]},
        )
        updated_job, result = runner.run(job, point_gdf)
        assert updated_job.status == JobStatus.COMPLETED
        assert len(result) == 2
        assert result.crs.to_epsg() == 3857


# ---------------------------------------------------------------------------
# Tests: pipeline_config path (scheduler-originated jobs)
# ---------------------------------------------------------------------------


class TestJobRunnerPipelineConfig:
    """Jobs carrying pipeline_config (scheduler path) must execute the pipeline."""

    def test_pipeline_config_steps_are_executed(self, setup_runner, point_gdf):
        """Core regression: pipeline_config filter step reduces feature count."""
        runner, _ = setup_runner
        job = Job(
            name="scheduled:nightly",
            parameters={
                "schedule_id": str(uuid4()),
                "pipeline_config": _SCHEDULER_PIPELINE_CONFIG,
                "triggered_by": "scheduler",
            },
        )
        updated_job, result = runner.run(job, point_gdf)

        assert updated_job.status == JobStatus.COMPLETED
        # filter "value > 10" keeps rows with value=15 and value=25 (point_gdf has 3 rows: 5, 15, 25)
        assert len(result) == 2, (
            f"Expected 2 features after filter(value>10) but got {len(result)}. "
            "pipeline_config steps were silently ignored."
        )

    def test_pipeline_config_invalid_fails_with_message(self, setup_runner, point_gdf):
        """pipeline_config with unknown capability → job FAILED, explicit error message."""
        runner, _ = setup_runner
        bad_config = {
            "version": 2,
            "name": "bad_pipeline",
            "steps": [
                {
                    "id": "bad_step",
                    "type": "capability",
                    "capability": "nonexistent_capability_xyz",
                    "params": {},
                },
            ],
        }
        job = Job(
            name="scheduled:bad",
            parameters={
                "pipeline_config": bad_config,
                "triggered_by": "scheduler",
            },
        )
        with pytest.raises(Exception):
            runner.run(job, point_gdf)

        assert job.status == JobStatus.FAILED
        assert job.error_message  # must not be empty

    def test_both_pipeline_config_and_rule_ids_pipeline_config_wins(
        self, setup_runner, point_gdf
    ):
        """When both pipeline_config and rule_ids are present, pipeline_config takes priority."""
        runner, repo = setup_runner
        rule = Rule(
            name="reproject_rule",
            capability="reproject",
            config={"target_crs": "EPSG:3857"},
        )
        repo.save(rule)

        job = Job(
            name="ambiguous_job",
            parameters={
                # rule_ids would reproject to EPSG:3857
                "rule_ids": [str(rule.id)],
                # pipeline_config filters — if it wins, crs stays 4326 and rows shrink
                "pipeline_config": _SCHEDULER_PIPELINE_CONFIG,
                "triggered_by": "scheduler",
            },
        )
        updated_job, result = runner.run(job, point_gdf)

        assert updated_job.status == JobStatus.COMPLETED
        # pipeline_config (filter) wins: 2 rows, CRS unchanged (4326)
        assert len(result) == 2
        assert result.crs.to_epsg() == 4326

    def test_rule_ids_path_unchanged(self, setup_runner, point_gdf):
        """Existing rule_ids path is unaffected by the fix."""
        runner, repo = setup_runner
        rule = Rule(
            name="filter_rule",
            capability="filter",
            config={"expression": "value > 10"},
        )
        repo.save(rule)
        job = Job(
            name="rule_ids_job",
            parameters={"rule_ids": [str(rule.id)]},
        )
        updated_job, result = runner.run(job, point_gdf)
        assert updated_job.status == JobStatus.COMPLETED
        assert len(result) == 2


# ---------------------------------------------------------------------------
# Tests: P1a — validation guards in _execute_pipeline_config
# ---------------------------------------------------------------------------


class TestPipelineConfigValidation:
    """P1a: schema validation, version guard, zero-step guard, empty-input guard."""

    def test_empty_dict_fails_with_message(self, setup_runner, point_gdf):
        """{} has no version/steps → schema validation → FAILED with message."""
        runner, _ = setup_runner
        job = Job(
            name="sched:empty",
            parameters={"pipeline_config": {}, "triggered_by": "scheduler"},
        )
        with pytest.raises(ValueError, match="version"):
            runner.run(job, point_gdf)
        assert job.status == JobStatus.FAILED
        assert job.error_message

    def test_v3_manifest_rejected_naming_version(self, setup_runner, point_gdf):
        """A v3 manifest dict → FAILED with message naming version 3."""
        runner, _ = setup_runner
        v3_config = {"version": 3, "name": "manifest", "sources": {}}
        job = Job(
            name="sched:v3",
            parameters={"pipeline_config": v3_config, "triggered_by": "scheduler"},
        )
        with pytest.raises(ValueError, match="version=3"):
            runner.run(job, point_gdf)
        assert job.status == JobStatus.FAILED
        assert "version=3" in (job.error_message or "")

    def test_v2_all_steps_disabled_fails(self, setup_runner, point_gdf):
        """v2 spec with all steps disabled → zero runnable steps → FAILED."""
        runner, _ = setup_runner
        config = {
            "version": 2,
            "name": "disabled_steps",
            "steps": [
                {
                    "id": "step1",
                    "type": "capability",
                    "capability": "filter",
                    "params": {"expression": "value > 0"},
                    "enabled": False,
                },
            ],
        }
        job = Job(
            name="sched:disabled",
            parameters={"pipeline_config": config, "triggered_by": "scheduler"},
        )
        with pytest.raises(ValueError, match="no runnable steps"):
            runner.run(job, point_gdf)
        assert job.status == JobStatus.FAILED

    def test_empty_input_gdf_without_dataset_id_fails(self, setup_runner):
        """pipeline_config job with empty GDF and no dataset_id → FAILED explicit."""
        runner, _ = setup_runner
        empty_gdf = gpd.GeoDataFrame({"geometry": []}, crs="EPSG:4326")
        job = Job(
            name="sched:no-input",
            dataset_id=None,
            parameters={
                "pipeline_config": _SCHEDULER_PIPELINE_CONFIG,
                "triggered_by": "scheduler",
            },
        )
        with pytest.raises(ValueError, match="no input dataset"):
            runner.run(job, empty_gdf)
        assert job.status == JobStatus.FAILED

    def test_v2_valid_one_step_executes(self, setup_runner, point_gdf):
        """Positive control: valid v2 1-step pipeline executes correctly."""
        runner, _ = setup_runner
        job = Job(
            name="sched:valid",
            parameters={
                "pipeline_config": _SCHEDULER_PIPELINE_CONFIG,
                "triggered_by": "scheduler",
            },
        )
        updated_job, result = runner.run(job, point_gdf)
        assert updated_job.status == JobStatus.COMPLETED
        assert len(result) == 2  # filter value > 10


# ---------------------------------------------------------------------------
# Tests: P2 — timeout does not hang (pipeline_config path and rule_ids path)
# ---------------------------------------------------------------------------


class TestTimeoutNonBlocking:
    """P2: timeout on both execution paths raises FAILED without blocking."""

    def test_pipeline_config_timeout_raises_and_job_fails(self, setup_runner, point_gdf):
        """timeout=0 on pipeline_config path → TimeoutError → job FAILED, no hang.

        max_retries=0 prevents a retry from succeeding before the future is
        submitted (trivial filter can beat a 0-second timeout on a fast host).
        """
        runner, _ = setup_runner
        job = Job(
            name="sched:timeout",
            parameters={
                "pipeline_config": _SCHEDULER_PIPELINE_CONFIG,
                "triggered_by": "scheduler",
                "timeout": 0,  # expires immediately
                "max_retries": 0,
            },
        )
        t0 = time.monotonic()
        with pytest.raises(Exception):
            runner.run(job, point_gdf)
        elapsed = time.monotonic() - t0
        assert job.status == JobStatus.FAILED
        # Must return in well under 5 s (not block on pool.shutdown(wait=True))
        assert elapsed < 4.0, f"Took {elapsed:.1f}s — likely blocking on pool shutdown"

    def test_rule_ids_timeout_raises_and_job_fails(self, setup_runner, point_gdf):
        """timeout=0 on rule_ids path → TimeoutError → job FAILED, no hang.

        max_retries=0 prevents a retry from succeeding on a trivial fast-path
        before the executor even touches the thread pool.
        """
        runner, repo = setup_runner
        rule = Rule(
            name="filter_rule",
            capability="filter",
            config={"expression": "value > 10"},
        )
        repo.save(rule)
        job = Job(
            name="rule:timeout",
            parameters={
                "rule_ids": [str(rule.id)],
                "timeout": 0,
                "max_retries": 0,
            },
        )
        t0 = time.monotonic()
        with pytest.raises(Exception):
            runner.run(job, point_gdf)
        elapsed = time.monotonic() - t0
        assert job.status == JobStatus.FAILED
        assert elapsed < 4.0, f"Took {elapsed:.1f}s — likely blocking on pool shutdown"

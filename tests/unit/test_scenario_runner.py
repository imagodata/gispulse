"""Unit tests for the GISPulse ScenarioRunner (orchestration)."""

from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import Point

from gispulse.core.models import Job, JobStatus, Rule, Scenario
from gispulse.orchestration.runner import JobRunner
from gispulse.orchestration.scenario_runner import ScenarioRunner
from gispulse.persistence.repository import InMemoryRepository
from gispulse.rules.engine import RuleEngine


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def point_gdf() -> gpd.GeoDataFrame:
    """Small GeoDataFrame in EPSG:4326 with a numeric 'value' column."""
    return gpd.GeoDataFrame(
        {
            "id": [1, 2, 3],
            "value": [5, 15, 25],
            "geometry": [Point(2.35, 48.85), Point(2.30, 48.87), Point(2.40, 48.90)],
        },
        crs="EPSG:4326",
    )


@pytest.fixture
def setup_runners():
    """Return (scenario_runner, job_runner, rule_repo, job_repo)."""
    rule_repo: InMemoryRepository = InMemoryRepository()
    job_repo: InMemoryRepository = InMemoryRepository()
    engine = RuleEngine(repository=rule_repo)
    job_runner = JobRunner(repository=rule_repo, rule_engine=engine)
    scenario_runner = ScenarioRunner(
        job_runner=job_runner,
        rule_repo=rule_repo,
        job_repo=job_repo,
    )
    return scenario_runner, job_runner, rule_repo, job_repo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_buffer_job(rule_repo: InMemoryRepository, job_repo: InMemoryRepository) -> Job:
    """Register a buffer rule and a job that references it."""
    rule = Rule(
        name="buffer_rule",
        capability="buffer",
        config={"distance": 1000.0},
    )
    rule_repo.save(rule)
    job = Job(
        name="buffer_job",
        parameters={"rule_ids": [str(rule.id)]},
    )
    job_repo.save(job)
    return job


def _make_filter_job(rule_repo: InMemoryRepository, job_repo: InMemoryRepository) -> Job:
    """Register a filter rule (value > 10) and a job that references it."""
    rule = Rule(
        name="filter_rule",
        capability="filter",
        config={"expression": "value > 10"},
    )
    rule_repo.save(rule)
    job = Job(
        name="filter_job",
        parameters={"rule_ids": [str(rule.id)]},
    )
    job_repo.save(job)
    return job


def _make_failing_job(rule_repo: InMemoryRepository, job_repo: InMemoryRepository) -> Job:
    """Register a job whose rule capability does not exist (will raise KeyError)."""
    rule = Rule(
        name="bad_rule",
        capability="nonexistent_capability",
        config={},
    )
    rule_repo.save(rule)
    job = Job(
        name="failing_job",
        parameters={"rule_ids": [str(rule.id)]},
    )
    job_repo.save(job)
    return job


# ---------------------------------------------------------------------------
# Sequential (pipeline) tests
# ---------------------------------------------------------------------------


class TestScenarioRunnerSequential:
    def test_sequential_two_jobs_chaining(self, setup_runners, point_gdf):
        """Buffer then filter: filter job should receive the buffered GDF."""
        scenario_runner, _, rule_repo, job_repo = setup_runners

        buffer_job = _make_buffer_job(rule_repo, job_repo)
        filter_job = _make_filter_job(rule_repo, job_repo)

        scenario = Scenario(
            name="buffer_then_filter",
            jobs=[buffer_job.id, filter_job.id],
        )

        result = scenario_runner.run(scenario, point_gdf)

        assert result.status == "COMPLETED"
        assert result.failed_job_id is None
        assert result.error is None
        assert len(result.job_results) == 2

        # Both jobs completed successfully
        for job, gdf_out in result.job_results:
            assert job.status == JobStatus.COMPLETED
            assert gdf_out is not None

        # Final output: buffer applied (geometry has changed from points to polygons
        # after reprojection through buffer capability), then filter keeps value > 10
        _, final_gdf = result.job_results[-1]
        assert len(final_gdf) == 2  # rows with value 15 and 25

    def test_sequential_chaining_passes_output_forward(self, setup_runners, point_gdf):
        """The GDF fed to the second job must be the output of the first job."""
        scenario_runner, _, rule_repo, job_repo = setup_runners

        # Job 1: filter to keep only value > 10 (2 rows)
        filter_job = _make_filter_job(rule_repo, job_repo)

        # Job 2: reproject — applied to the already-filtered GDF
        reproject_rule = Rule(
            name="reproject_rule",
            capability="reproject",
            config={"target_crs": "EPSG:3857"},
        )
        rule_repo.save(reproject_rule)
        reproject_job = Job(
            name="reproject_job",
            parameters={"rule_ids": [str(reproject_rule.id)]},
        )
        job_repo.save(reproject_job)

        scenario = Scenario(
            name="filter_then_reproject",
            jobs=[filter_job.id, reproject_job.id],
        )

        result = scenario_runner.run(scenario, point_gdf)

        assert result.status == "COMPLETED"
        _, gdf_after_filter = result.job_results[0]
        _, gdf_after_reproject = result.job_results[1]

        # Filter reduced rows from 3 to 2; reproject output must still have 2 rows
        assert len(gdf_after_filter) == 2
        assert len(gdf_after_reproject) == 2
        assert gdf_after_reproject.crs.to_epsg() == 3857

    def test_sequential_fails_on_first_bad_job(self, setup_runners, point_gdf):
        """Execution must halt at the failing job and return status FAILED."""
        scenario_runner, _, rule_repo, job_repo = setup_runners

        failing_job = _make_failing_job(rule_repo, job_repo)
        filter_job = _make_filter_job(rule_repo, job_repo)

        scenario = Scenario(
            name="fail_then_filter",
            jobs=[failing_job.id, filter_job.id],
        )

        result = scenario_runner.run(scenario, point_gdf)

        assert result.status == "FAILED"
        assert result.failed_job_id == str(failing_job.id)
        assert result.error is not None

        # Only one entry in job_results (execution stopped after first failure)
        assert len(result.job_results) == 1
        failed_job_obj, failed_gdf = result.job_results[0]
        assert failed_job_obj.id == failing_job.id
        assert failed_gdf is None

    def test_sequential_fails_stops_before_second_job(self, setup_runners, point_gdf):
        """Jobs after the failing one must not execute."""
        scenario_runner, _, rule_repo, job_repo = setup_runners

        filter_job = _make_filter_job(rule_repo, job_repo)
        failing_job = _make_failing_job(rule_repo, job_repo)
        # A third job that should never run
        third_filter_job = _make_filter_job(rule_repo, job_repo)

        scenario = Scenario(
            name="filter_fail_neverrun",
            jobs=[filter_job.id, failing_job.id, third_filter_job.id],
        )

        result = scenario_runner.run(scenario, point_gdf)

        assert result.status == "FAILED"
        assert result.failed_job_id == str(failing_job.id)
        # Only 2 entries: filter (OK) + failing (error); third never ran
        assert len(result.job_results) == 2

    def test_sequential_empty_scenario(self, setup_runners, point_gdf):
        """A scenario with 0 jobs must return COMPLETED with an empty list."""
        scenario_runner, _, _, _ = setup_runners

        scenario = Scenario(name="empty_scenario", jobs=[])
        result = scenario_runner.run(scenario, point_gdf)

        assert result.status == "COMPLETED"
        assert result.job_results == []
        assert result.failed_job_id is None
        assert result.error is None


# ---------------------------------------------------------------------------
# Independent (parallel-like) tests
# ---------------------------------------------------------------------------


class TestScenarioRunnerIndependent:
    def test_independent_two_jobs_receive_original_gdf(self, setup_runners, point_gdf):
        """Each job must receive the original GDF, not the output of the other."""
        scenario_runner, _, rule_repo, job_repo = setup_runners

        filter_job = _make_filter_job(rule_repo, job_repo)  # keeps 2 rows
        # A second job that would also produce 2 rows if given the original GDF
        filter_job_2 = _make_filter_job(rule_repo, job_repo)

        scenario = Scenario(
            name="two_independent_filters",
            jobs=[filter_job.id, filter_job_2.id],
        )

        result = scenario_runner.run_independent(scenario, point_gdf)

        assert result.status == "COMPLETED"
        assert len(result.job_results) == 2

        for job_obj, gdf_out in result.job_results:
            assert job_obj.status == JobStatus.COMPLETED
            assert gdf_out is not None
            # Both jobs started from the 3-row original → each filtered to 2 rows
            assert len(gdf_out) == 2

    def test_independent_chaining_does_not_occur(self, setup_runners, point_gdf):
        """Verify independence: buffer result does NOT shrink the filter input."""
        scenario_runner, _, rule_repo, job_repo = setup_runners

        buffer_job = _make_buffer_job(rule_repo, job_repo)
        filter_job = _make_filter_job(rule_repo, job_repo)

        scenario = Scenario(
            name="independent_buffer_filter",
            jobs=[buffer_job.id, filter_job.id],
        )

        result = scenario_runner.run_independent(scenario, point_gdf)

        assert result.status == "COMPLETED"
        _, buffer_out = result.job_results[0]
        _, filter_out = result.job_results[1]

        # Buffer job: geometry changed but row count unchanged (3 rows)
        assert len(buffer_out) == 3
        # Filter job: started from original 3-row GDF → 2 rows (value > 10)
        assert len(filter_out) == 2

    def test_independent_partial_on_failure(self, setup_runners, point_gdf):
        """A failing job must not stop the others; status must be PARTIAL."""
        scenario_runner, _, rule_repo, job_repo = setup_runners

        filter_job = _make_filter_job(rule_repo, job_repo)
        failing_job = _make_failing_job(rule_repo, job_repo)

        scenario = Scenario(
            name="partial_scenario",
            jobs=[filter_job.id, failing_job.id],
        )

        result = scenario_runner.run_independent(scenario, point_gdf)

        assert result.status == "PARTIAL"
        assert result.failed_job_id == str(failing_job.id)
        assert result.error is not None
        # Both jobs have an entry in job_results
        assert len(result.job_results) == 2

        # First job succeeded
        ok_job, ok_gdf = result.job_results[0]
        assert ok_job.status == JobStatus.COMPLETED
        assert ok_gdf is not None

        # Second job failed
        bad_job, bad_gdf = result.job_results[1]
        assert bad_gdf is None

    def test_independent_continues_after_first_failure(self, setup_runners, point_gdf):
        """Jobs after a failure must still execute in independent mode."""
        scenario_runner, _, rule_repo, job_repo = setup_runners

        failing_job = _make_failing_job(rule_repo, job_repo)
        filter_job = _make_filter_job(rule_repo, job_repo)

        scenario = Scenario(
            name="fail_then_continue",
            jobs=[failing_job.id, filter_job.id],
        )

        result = scenario_runner.run_independent(scenario, point_gdf)

        assert result.status == "PARTIAL"
        assert len(result.job_results) == 2

        # Second job must have run and succeeded
        second_job, second_gdf = result.job_results[1]
        assert second_job.status == JobStatus.COMPLETED
        assert second_gdf is not None
        assert len(second_gdf) == 2

    def test_independent_empty_scenario(self, setup_runners, point_gdf):
        """A scenario with 0 jobs must return COMPLETED with an empty list."""
        scenario_runner, _, _, _ = setup_runners

        scenario = Scenario(name="empty_independent", jobs=[])
        result = scenario_runner.run_independent(scenario, point_gdf)

        assert result.status == "COMPLETED"
        assert result.job_results == []
        assert result.failed_job_id is None
        assert result.error is None

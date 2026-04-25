"""Unit tests for the GISPulse job runner (orchestration)."""

from __future__ import annotations

from uuid import uuid4

import geopandas as gpd
import pytest
from shapely.geometry import Point

from core.models import Job, JobStatus, Rule
from orchestration.runner import JobRunner
from persistence.repository import InMemoryRepository
from rules.engine import RuleEngine


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

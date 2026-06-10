"""Tests for PipelineRun and PipelineRunStep domain models."""
from __future__ import annotations

from uuid import UUID, uuid4


from gispulse.core.run_models import PipelineRun, PipelineRunStep
from gispulse.core.models import JobStatus


def test_pipeline_run_defaults():
    run = PipelineRun(source="job", spec_ref="my_pipeline")
    assert isinstance(run.run_id, UUID)
    assert run.source == "job"
    assert run.spec_ref == "my_pipeline"
    assert run.scope == ""
    assert run.status == JobStatus.RUNNING
    assert run.started_at is not None
    assert run.ended_at is None
    assert run.error == ""
    assert run.steps == []


def test_pipeline_run_step_defaults():
    step = PipelineRunStep(step_id="s1")
    assert step.step_id == "s1"
    assert step.status == JobStatus.RUNNING
    assert step.attempt == 1
    assert step.error == ""
    assert step.started_at is not None
    assert step.ended_at is None


def test_pipeline_run_importable_from_models():
    # Backward compat: also importable from core.models
    from gispulse.core.models import PipelineRun as PR, PipelineRunStep as PRS
    assert PR is PipelineRun
    assert PRS is PipelineRunStep


# ---------------------------------------------------------------------------
# RunRepository round-trip tests
# ---------------------------------------------------------------------------

from gispulse.persistence.run_repository import RunRepository  # noqa: E402


def test_run_repository_save_and_get(tmp_path):
    repo = RunRepository(db_path=tmp_path / "test.db")
    run = PipelineRun(source="job", spec_ref="test_pipeline", scope="63")
    run.steps.append(PipelineRunStep(step_id="s1", status=JobStatus.COMPLETED))

    repo.save(run)
    fetched = repo.get(run.run_id)

    assert fetched is not None
    assert fetched.run_id == run.run_id
    assert fetched.source == "job"
    assert fetched.spec_ref == "test_pipeline"
    assert fetched.scope == "63"
    assert fetched.status == JobStatus.RUNNING
    assert len(fetched.steps) == 1
    assert fetched.steps[0].step_id == "s1"
    assert fetched.steps[0].status == JobStatus.COMPLETED


def test_run_repository_list_all(tmp_path):
    repo = RunRepository(db_path=tmp_path / "test.db")
    run1 = PipelineRun(source="job", spec_ref="p1")
    run2 = PipelineRun(source="schedule", spec_ref="p2")
    repo.save(run1)
    repo.save(run2)
    all_runs = repo.list_all()
    assert len(all_runs) == 2


def test_run_repository_upsert(tmp_path):
    repo = RunRepository(db_path=tmp_path / "test.db")
    run = PipelineRun(source="job", spec_ref="p1")
    repo.save(run)
    run.status = JobStatus.COMPLETED
    repo.save(run)
    fetched = repo.get(run.run_id)
    assert fetched.status == JobStatus.COMPLETED


def test_run_repository_idempotent_migration(tmp_path):
    """Creating the repo twice on the same DB must not raise."""
    db = tmp_path / "test.db"
    RunRepository(db_path=db)
    RunRepository(db_path=db)  # second init — idempotent


def test_run_repository_get_missing(tmp_path):
    repo = RunRepository(db_path=tmp_path / "test.db")
    assert repo.get(uuid4()) is None


# ---------------------------------------------------------------------------
# recover_stale_runs tests (P1e)
# ---------------------------------------------------------------------------

def test_recover_stale_runs_marks_running_as_failed(tmp_path):
    """P1e: RUNNING runs are marked FAILED with orphaned error on recovery."""
    from datetime import datetime, timezone

    repo = RunRepository(db_path=tmp_path / "test.db")
    r1 = PipelineRun(source="job", spec_ref="p1")
    r2 = PipelineRun(source="job", spec_ref="p2")
    r2.status = JobStatus.COMPLETED
    r2.ended_at = datetime.now(timezone.utc)
    repo.save(r1)
    repo.save(r2)

    count = repo.recover_stale_runs()

    assert count == 1
    recovered = repo.get(r1.run_id)
    assert recovered.status == JobStatus.FAILED
    assert "orphaned" in recovered.error
    # Completed run must be untouched
    completed = repo.get(r2.run_id)
    assert completed.status == JobStatus.COMPLETED


def test_recover_stale_runs_emits_events(tmp_path):
    """P1e: recover_stale_runs emits run.failed via provided sink."""
    repo = RunRepository(db_path=tmp_path / "test.db")
    r = PipelineRun(source="schedule", spec_ref="cron")
    repo.save(r)

    class SpySink:
        def __init__(self): self.events = []
        def emit(self, t, d): self.events.append((t, d))

    spy = SpySink()
    count = repo.recover_stale_runs(event_sink=spy)

    assert count == 1
    assert any(t == "run.failed" for t, _ in spy.events)
    failed_data = [d for t, d in spy.events if t == "run.failed"][0]
    assert failed_data["run_id"] == str(r.run_id)
    assert failed_data["error"] == "orphaned by worker restart"


def test_recover_stale_runs_no_stale(tmp_path):
    """P1e: no RUNNING runs → returns 0, nothing changes."""
    repo = RunRepository(db_path=tmp_path / "test.db")
    count = repo.recover_stale_runs()
    assert count == 0

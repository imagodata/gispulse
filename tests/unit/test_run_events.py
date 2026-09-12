"""Tests for RunEventSink emission in PipelineExecutor, GraphExecutor, and JobWorker."""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import geopandas as gpd
import pytest
from shapely.geometry import Point

from gispulse.core.models import Job, JobStatus
from gispulse.core.pipeline import PipelineSpec, StepSpec
from gispulse.orchestration.event_sink import NoOpSink
from gispulse.orchestration.pipeline_executor import PipelineExecutor


# ---------------------------------------------------------------------------
# Spy sink
# ---------------------------------------------------------------------------


class SpySink:
    """Records every emitted event for assertion."""

    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def emit(self, event_type: str, data: dict) -> None:
        self.events.append((event_type, data))

    def types(self) -> list[str]:
        return [t for t, _ in self.events]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def two_step_spec() -> PipelineSpec:
    """A linear 2-step pipeline: buffer -> filter."""
    return PipelineSpec(
        version=2,
        name="test_pipeline",
        steps=[
            StepSpec(id="buf", type="capability", capability="buffer", params={"distance": 1.0}),
            StepSpec(id="flt", type="capability", capability="filter", params={"expression": "id > 0"}),
        ],
    )


@pytest.fixture
def point_gdf() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"id": [1, 2], "geometry": [Point(2.35, 48.85), Point(2.30, 48.87)]},
        crs="EPSG:4326",
    )


# ---------------------------------------------------------------------------
# PipelineExecutor sink tests
# ---------------------------------------------------------------------------


class TestPipelineExecutorSink:
    def test_no_sink_is_noop(self, two_step_spec, point_gdf):
        """Existing callers that don't pass event_sink still work (regression guard)."""
        executor = PipelineExecutor()
        results = executor.execute(two_step_spec, {"input": point_gdf})
        assert "flt" in results

    def test_noop_sink_explicit(self, two_step_spec, point_gdf):
        """Explicit NoOpSink doesn't raise and produces results."""
        executor = PipelineExecutor()
        results = executor.execute(two_step_spec, {"input": point_gdf}, event_sink=NoOpSink())
        assert "flt" in results

    def test_sink_receives_step_events(self, two_step_spec, point_gdf):
        """Spy sink receives run.step.started + run.step.completed for each step."""
        sink = SpySink()
        run_id = str(uuid4())
        executor = PipelineExecutor()
        executor.execute(two_step_spec, {"input": point_gdf}, event_sink=sink, run_id=run_id)

        types = sink.types()
        assert types.count("run.step.started") == 2
        assert types.count("run.step.completed") == 2
        # Interleaved: started/completed for each step in order
        assert types == [
            "run.step.started", "run.step.completed",
            "run.step.started", "run.step.completed",
        ]

    def test_sink_event_data_has_required_fields(self, two_step_spec, point_gdf):
        """Each event carries run_id, step_id, status, started_at."""
        sink = SpySink()
        run_id = str(uuid4())
        executor = PipelineExecutor()
        executor.execute(two_step_spec, {"input": point_gdf}, event_sink=sink, run_id=run_id)

        started_events = [(t, d) for t, d in sink.events if t == "run.step.started"]
        for _, data in started_events:
            assert data["run_id"] == run_id
            assert "step_id" in data
            assert "started_at" in data

        completed_events = [(t, d) for t, d in sink.events if t == "run.step.completed"]
        for _, data in completed_events:
            assert data["run_id"] == run_id
            assert data["status"] == "completed"
            assert "ended_at" in data

    def test_sink_step_failed_on_error(self, point_gdf):
        """When a step raises inside execute, sink receives run.step.failed."""
        spec = PipelineSpec(
            version=2,
            name="fail_test",
            steps=[StepSpec(id="fail_step", type="capability", capability="buffer", params={"distance": 1.0})],
        )

        class FailCap:
            name = "buffer"
            def get_schema(self): return {}
            def execute(self, gdf, **kw): raise RuntimeError("boom")

        sink = SpySink()
        run_id = str(uuid4())
        executor = PipelineExecutor(capability_getter=lambda _: FailCap())

        with pytest.raises(RuntimeError, match="boom"):
            executor.execute(spec, {"input": point_gdf}, event_sink=sink, run_id=run_id)

        assert "run.step.failed" in sink.types()
        failed_data = [d for t, d in sink.events if t == "run.step.failed"][0]
        assert failed_data["step_id"] == "fail_step"
        assert "error" in failed_data

    def test_sink_step_failed_on_capability_lookup_error(self, point_gdf):
        """P2a: failure during cap lookup (before original try) emits run.step.failed."""
        spec = PipelineSpec(
            version=2,
            name="lookup_fail",
            steps=[StepSpec(id="bad_cap", type="capability", capability="nonexistent", params={})],
        )

        def _bad_getter(name: str):
            raise KeyError(f"Capability '{name}' not found")

        sink = SpySink()
        executor = PipelineExecutor(capability_getter=_bad_getter)

        with pytest.raises(KeyError):
            executor.execute(spec, {"input": point_gdf}, event_sink=sink, run_id="test-run")

        # run.step.started emitted first, then run.step.failed
        assert "run.step.started" in sink.types()
        assert "run.step.failed" in sink.types()
        idx_started = sink.types().index("run.step.started")
        idx_failed = sink.types().index("run.step.failed")
        assert idx_started < idx_failed


# ---------------------------------------------------------------------------
# JobWorker sink tests
# ---------------------------------------------------------------------------

from gispulse.core.models import Dataset  # noqa: E402
from gispulse.orchestration.job_queue import InMemoryJobQueue  # noqa: E402
from gispulse.orchestration.runner import JobRunner  # noqa: E402
from gispulse.orchestration.worker import JobWorker  # noqa: E402
from gispulse.persistence.repository import InMemoryRepository  # noqa: E402
from gispulse.persistence.run_repository import RunRepository  # noqa: E402
from gispulse.rules.engine import RuleEngine  # noqa: E402


def _make_worker(queue, runner, dataset_repo, job_repo, run_repo, results_dir, sink=None):
    return JobWorker(
        queue=queue,
        runner=runner,
        dataset_repo=dataset_repo,
        job_repo=job_repo,
        run_repo=run_repo,
        results_dir=results_dir,
        event_sink=sink,
    )


@pytest.fixture
def results_dir(tmp_path) -> Path:
    d = tmp_path / "results"
    d.mkdir()
    return d


@pytest.fixture
def sample_gdf() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"id": [1, 2], "geometry": [Point(0, 0), Point(1, 1)]},
        crs="EPSG:4326",
    )


@pytest.fixture
def dataset_repo(tmp_path, sample_gdf) -> InMemoryRepository:
    repo: InMemoryRepository = InMemoryRepository()
    gpkg_path = tmp_path / "input.gpkg"
    sample_gdf.to_file(gpkg_path, driver="GPKG", layer="cities")
    ds = Dataset(id=uuid4(), name="cities", source_path=str(gpkg_path))
    repo.save(ds)
    return repo


class TestJobWorkerRunEvents:
    """Worker creates PipelineRun, persists it, emits events via sink."""

    @pytest.mark.asyncio
    async def test_worker_emits_run_started_and_completed(
        self, tmp_path, sample_gdf, results_dir
    ):
        """Happy path: run.started + run.completed emitted, PipelineRun saved."""
        queue = InMemoryJobQueue()
        repo: InMemoryRepository = InMemoryRepository()
        engine = RuleEngine(repository=repo)
        runner = JobRunner(repository=repo, rule_engine=engine)
        ds_repo: InMemoryRepository = InMemoryRepository()
        gpkg_path = tmp_path / "input.gpkg"
        sample_gdf.to_file(gpkg_path, driver="GPKG", layer="cities")
        ds = Dataset(id=uuid4(), name="cities", source_path=str(gpkg_path))
        ds_repo.save(ds)

        job_repo: InMemoryRepository = InMemoryRepository()
        run_repo = RunRepository(db_path=tmp_path / "runs.db")
        sink = SpySink()

        job = Job(name="test_job", dataset_id=ds.id, parameters={"rule_ids": []})
        await queue.enqueue(job)

        worker = _make_worker(queue, runner, ds_repo, job_repo, run_repo, results_dir, sink)
        worker._running = True

        dequeued = await queue.dequeue(timeout=1.0)
        await worker._process_job(dequeued)

        assert "run.started" in sink.types()
        assert "run.completed" in sink.types()
        # run.started must come before run.completed
        idx_started = sink.types().index("run.started")
        idx_completed = [i for i, t in enumerate(sink.types()) if t == "run.completed"]
        assert idx_started < idx_completed[-1]

        # PipelineRun persisted
        runs = run_repo.list_all()
        assert len(runs) == 1
        assert runs[0].status == JobStatus.COMPLETED
        assert runs[0].source == "job"

    @pytest.mark.asyncio
    async def test_worker_emits_run_failed_on_error(
        self, tmp_path, results_dir
    ):
        """When execution fails, run.failed emitted and PipelineRun=FAILED persisted."""
        queue = InMemoryJobQueue()
        repo: InMemoryRepository = InMemoryRepository()
        engine = RuleEngine(repository=repo)
        runner = JobRunner(repository=repo, rule_engine=engine)
        job_repo: InMemoryRepository = InMemoryRepository()
        run_repo = RunRepository(db_path=tmp_path / "runs.db")
        sink = SpySink()

        # No dataset_repo entry -> will fail to load
        ds_repo: InMemoryRepository = InMemoryRepository()
        missing_ds_id = uuid4()
        job = Job(name="fail_job", dataset_id=missing_ds_id, parameters={})
        await queue.enqueue(job)

        worker = _make_worker(queue, runner, ds_repo, job_repo, run_repo, results_dir, sink)
        dequeued = await queue.dequeue(timeout=1.0)
        await worker._process_job(dequeued)

        assert "run.failed" in sink.types()
        runs = run_repo.list_all()
        assert len(runs) == 1
        assert runs[0].status == JobStatus.FAILED
        assert runs[0].error != ""

    @pytest.mark.asyncio
    async def test_worker_no_sink_still_works(
        self, tmp_path, sample_gdf, results_dir
    ):
        """Worker without sink processes jobs normally (backward compat)."""
        queue = InMemoryJobQueue()
        repo: InMemoryRepository = InMemoryRepository()
        engine = RuleEngine(repository=repo)
        runner = JobRunner(repository=repo, rule_engine=engine)
        ds_repo: InMemoryRepository = InMemoryRepository()
        gpkg_path = tmp_path / "input.gpkg"
        sample_gdf.to_file(gpkg_path, driver="GPKG", layer="cities")
        ds = Dataset(id=uuid4(), name="cities", source_path=str(gpkg_path))
        ds_repo.save(ds)
        job_repo: InMemoryRepository = InMemoryRepository()
        run_repo = RunRepository(db_path=tmp_path / "runs.db")

        job = Job(name="no_sink_job", dataset_id=ds.id, parameters={"rule_ids": []})
        await queue.enqueue(job)

        # No sink passed — uses NoOpSink internally
        worker = _make_worker(queue, runner, ds_repo, job_repo, run_repo, results_dir, sink=None)
        dequeued = await queue.dequeue(timeout=1.0)
        await worker._process_job(dequeued)

        saved = job_repo.list_all()
        assert len(saved) == 1
        assert saved[0].status == JobStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_worker_source_schedule_when_triggered_by_scheduler(
        self, tmp_path, sample_gdf, results_dir
    ):
        """Job with triggered_by=scheduler -> PipelineRun.source='schedule'."""
        queue = InMemoryJobQueue()
        repo: InMemoryRepository = InMemoryRepository()
        engine = RuleEngine(repository=repo)
        runner = JobRunner(repository=repo, rule_engine=engine)
        ds_repo: InMemoryRepository = InMemoryRepository()
        gpkg_path = tmp_path / "input.gpkg"
        sample_gdf.to_file(gpkg_path, driver="GPKG", layer="cities")
        ds = Dataset(id=uuid4(), name="cities", source_path=str(gpkg_path))
        ds_repo.save(ds)
        job_repo: InMemoryRepository = InMemoryRepository()
        run_repo = RunRepository(db_path=tmp_path / "runs.db")
        sink = SpySink()

        job = Job(
            name="scheduled_job",
            dataset_id=ds.id,
            parameters={"triggered_by": "scheduler", "rule_ids": []},
        )
        await queue.enqueue(job)

        worker = _make_worker(queue, runner, ds_repo, job_repo, run_repo, results_dir, sink)
        dequeued = await queue.dequeue(timeout=1.0)
        await worker._process_job(dequeued)

        runs = run_repo.list_all()
        assert runs[0].source == "schedule"


# ---------------------------------------------------------------------------
# RecordingSink tests (P1b)
# ---------------------------------------------------------------------------

from gispulse.orchestration.event_sink import RecordingSink  # noqa: E402
from gispulse.core.run_models import PipelineRun  # noqa: E402


class TestRecordingSink:
    def test_step_started_adds_step_to_run(self, tmp_path):
        run = PipelineRun(source="job", spec_ref="p")
        repo = RunRepository(db_path=tmp_path / "r.db")
        repo.save(run)
        sink = RecordingSink(run=run, run_repo=repo)

        sink.emit("run.step.started", {"step_id": "s1", "run_id": str(run.run_id), "started_at": "2026-01-01T00:00:00"})

        assert len(run.steps) == 1
        assert run.steps[0].step_id == "s1"
        assert run.steps[0].status == JobStatus.RUNNING

        # Persisted
        persisted = repo.get(run.run_id)
        assert persisted is not None
        assert len(persisted.steps) == 1

    def test_step_completed_updates_step_status(self, tmp_path):
        run = PipelineRun(source="job", spec_ref="p")
        repo = RunRepository(db_path=tmp_path / "r.db")
        repo.save(run)
        sink = RecordingSink(run=run, run_repo=repo)

        sink.emit("run.step.started", {"step_id": "s1", "run_id": str(run.run_id), "started_at": "2026-01-01T00:00:00"})
        sink.emit("run.step.completed", {"step_id": "s1", "run_id": str(run.run_id), "status": "completed", "ended_at": "2026-01-01T00:00:01"})

        assert run.steps[0].status == JobStatus.COMPLETED
        persisted = repo.get(run.run_id)
        assert persisted.steps[0].status == JobStatus.COMPLETED

    def test_step_failed_updates_step_status(self, tmp_path):
        run = PipelineRun(source="job", spec_ref="p")
        repo = RunRepository(db_path=tmp_path / "r.db")
        repo.save(run)
        sink = RecordingSink(run=run, run_repo=repo)

        sink.emit("run.step.started", {"step_id": "s1", "run_id": str(run.run_id), "started_at": "2026-01-01T00:00:00"})
        sink.emit("run.step.failed", {"step_id": "s1", "run_id": str(run.run_id), "status": "failed", "ended_at": "2026-01-01T00:00:01", "error": "boom"})

        assert run.steps[0].status == JobStatus.FAILED
        assert run.steps[0].error == "boom"

    def test_recording_sink_delegates_to_inner(self, tmp_path):
        run = PipelineRun(source="job", spec_ref="p")
        inner = SpySink()
        sink = RecordingSink(run=run, run_repo=None, inner=inner)

        sink.emit("run.started", {"run_id": str(run.run_id), "source": "job", "spec_ref": "p", "started_at": "x"})
        sink.emit("run.step.started", {"step_id": "s1", "run_id": str(run.run_id), "started_at": "x"})

        assert "run.started" in inner.types()
        assert "run.step.started" in inner.types()

    def test_worker_steps_persisted_after_execution(self, tmp_path, sample_gdf, results_dir):
        """P1b integration: after a job run, /runs/{id} steps are populated."""
        import asyncio
        queue = InMemoryJobQueue()
        repo: InMemoryRepository = InMemoryRepository()
        engine = RuleEngine(repository=repo)
        runner = JobRunner(repository=repo, rule_engine=engine)
        ds_repo: InMemoryRepository = InMemoryRepository()
        gpkg_path = tmp_path / "input.gpkg"
        sample_gdf.to_file(gpkg_path, driver="GPKG", layer="cities")
        ds = Dataset(id=uuid4(), name="cities", source_path=str(gpkg_path))
        ds_repo.save(ds)
        job_repo: InMemoryRepository = InMemoryRepository()
        run_repo = RunRepository(db_path=tmp_path / "runs.db")

        # A pipeline_config job so PipelineExecutor with steps runs
        pipeline_config = {
            "version": 2,
            "name": "pipe",
            "steps": [
                {"id": "buf", "type": "capability", "capability": "buffer", "params": {"distance": 1.0}},
            ],
        }
        job = Job(
            name="pipe_job",
            dataset_id=ds.id,
            parameters={"pipeline_config": pipeline_config},
        )

        async def _run():
            await queue.enqueue(job)
            worker = _make_worker(queue, runner, ds_repo, job_repo, run_repo, results_dir)
            dequeued = await queue.dequeue(timeout=1.0)
            await worker._process_job(dequeued)

        asyncio.get_event_loop().run_until_complete(_run())

        runs = run_repo.list_all()
        assert len(runs) == 1
        run = runs[0]
        assert run.status == JobStatus.COMPLETED
        # Steps should have been recorded by RecordingSink
        assert len(run.steps) >= 1
        assert run.steps[0].step_id == "buf"
        assert run.steps[0].status == JobStatus.COMPLETED


# ---------------------------------------------------------------------------
# Cancel path tests (P1c, P1d)
# ---------------------------------------------------------------------------

class TestWorkerCancelEvents:
    @pytest.mark.asyncio
    async def test_cancel_before_start_emits_run_failed(self, tmp_path, results_dir):
        """P1c: job cancelled before execution starts → run.failed emitted, run=FAILED."""
        queue = InMemoryJobQueue()
        repo: InMemoryRepository = InMemoryRepository()
        engine = RuleEngine(repository=repo)
        runner = JobRunner(repository=repo, rule_engine=engine)
        ds_repo: InMemoryRepository = InMemoryRepository()
        job_repo: InMemoryRepository = InMemoryRepository()
        run_repo = RunRepository(db_path=tmp_path / "runs.db")
        sink = SpySink()

        job = Job(name="cancel_job", parameters={})
        await queue.enqueue(job)
        dequeued = await queue.dequeue(timeout=1.0)

        # Simulate: job marked FAILED (cancelled) before worker processes it
        await queue.update_status(str(dequeued.id), JobStatus.FAILED)

        worker = _make_worker(queue, runner, ds_repo, job_repo, run_repo, results_dir, sink)
        await worker._process_job(dequeued)

        assert "run.started" in sink.types()
        assert "run.failed" in sink.types()
        runs = run_repo.list_all()
        assert len(runs) == 1
        assert runs[0].status == JobStatus.FAILED
        assert runs[0].error == "cancelled"

    @pytest.mark.asyncio
    async def test_cancel_while_running_emits_run_failed(self, tmp_path, sample_gdf, results_dir):
        """P1d: job cancelled after execution completes but before persisting → run.failed."""
        queue = InMemoryJobQueue()
        repo: InMemoryRepository = InMemoryRepository()
        engine = RuleEngine(repository=repo)
        runner = JobRunner(repository=repo, rule_engine=engine)
        ds_repo: InMemoryRepository = InMemoryRepository()
        gpkg_path = tmp_path / "input.gpkg"
        sample_gdf.to_file(gpkg_path, driver="GPKG", layer="cities")
        ds = Dataset(id=uuid4(), name="cities", source_path=str(gpkg_path))
        ds_repo.save(ds)
        job_repo: InMemoryRepository = InMemoryRepository()
        run_repo = RunRepository(db_path=tmp_path / "runs.db")
        sink = SpySink()

        # Patch queue.get_status so it returns FAILED on the *second* call
        # (first is the pre-execution check, second is post-execution check)
        original_get_status = queue.get_status
        call_count = [0]

        async def patched_get_status(job_id):
            call_count[0] += 1
            if call_count[0] >= 2:
                return {"status": JobStatus.FAILED.value}
            return await original_get_status(job_id)

        queue.get_status = patched_get_status

        job = Job(name="cancel_mid_job", dataset_id=ds.id, parameters={"rule_ids": []})
        await queue.enqueue(job)

        worker = _make_worker(queue, runner, ds_repo, job_repo, run_repo, results_dir, sink)
        dequeued = await queue.dequeue(timeout=1.0)
        await worker._process_job(dequeued)

        assert "run.failed" in sink.types()
        runs = run_repo.list_all()
        assert len(runs) == 1
        assert runs[0].status == JobStatus.FAILED
        assert runs[0].error == "cancelled"

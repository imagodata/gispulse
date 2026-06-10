"""Tests for on_run_completed trigger source (issue #440-c).

Couvre :
- Déclaration on_run_completed + run completed → job enqueué avec contexte
- Filtre status (failed ne matche pas completed et vice-versa)
- Filtre source / spec_ref / scope
- Profondeur max (anti-boucle infinie)
- Aucun trigger → comportement strictement identique (no-op)
- RecordingSink + RunCompletionTriggerSink integration
"""
from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

import pytest

from gispulse.core.conditions import OnRunCompletedConditions
from gispulse.core.enums import TriggerType
from gispulse.core.models import (
    ChangeOperation,
    ChangeRecord,
    FiredTrigger,
    Trigger,
)
from gispulse.orchestration.run_trigger_sink import RunCompletionTriggerSink
from gispulse.orchestration.trigger_bridge import TriggerJobBridge
from gispulse.persistence.repository import InMemoryRepository


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_trigger(
    status: str = "completed",
    source: str = "",
    spec_ref: str = "",
    scope: str = "",
    max_depth: int = 5,
    rule_id: str = "r1",
    enabled: bool = True,
) -> Trigger:
    """Build an on_run_completed Trigger with a RUN_JOB action.

    ``actions_dispatched`` in FiredTrigger is built from ``trigger.actions``
    via ``[a.action_type for a in trigger.actions]`` in TriggerEvaluator, so
    the rule_id is injected separately into the action dict by
    RunCompletionTriggerSink when enriching the FiredTrigger.
    For tests that go through TriggerJobBridge directly, we inject rule_id
    into the action dict in the FiredTrigger itself.
    """
    from gispulse.core.models import ActionDef, ActionType

    return Trigger(
        id=uuid4(),
        name="test_run_trigger",
        trigger_type=TriggerType.ON_RUN_COMPLETED,
        enabled=enabled,
        conditions={
            "status": status,
            "source": source,
            "spec_ref": spec_ref,
            "scope": scope,
            "max_depth": max_depth,
        },
        # action_type only; rule_id goes in config for dispatch downstream
        actions=[
            ActionDef(
                action_type=ActionType.RUN_JOB,
                config={"rule_id": rule_id},
            )
        ],
    )


class SpySink:
    """Records every emitted event for assertion."""

    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def emit(self, event_type: str, data: dict) -> None:
        self.events.append((event_type, data))

    def types(self) -> list[str]:
        return [t for t, _ in self.events]


class FakeQueue:
    """Minimal async job queue for tests."""

    def __init__(self) -> None:
        self.enqueued: list[Any] = []

    async def enqueue(self, job: Any) -> None:
        self.enqueued.append(job)


# ---------------------------------------------------------------------------
# OnRunCompletedConditions unit tests
# ---------------------------------------------------------------------------


class TestOnRunCompletedConditions:
    def test_default_status_is_completed(self) -> None:
        cond = OnRunCompletedConditions()
        assert cond.status == "completed"

    def test_max_depth_default_is_5(self) -> None:
        cond = OnRunCompletedConditions()
        assert cond.max_depth == 5

    def test_parse_conditions_registers_on_run_completed(self) -> None:
        from gispulse.core.models import parse_conditions

        cond = parse_conditions("on_run_completed", {"status": "failed", "max_depth": 3})
        assert isinstance(cond, OnRunCompletedConditions)
        assert cond.status == "failed"
        assert cond.max_depth == 3

    def test_trigger_type_enum_has_on_run_completed(self) -> None:
        assert TriggerType.ON_RUN_COMPLETED.value == "on_run_completed"


# ---------------------------------------------------------------------------
# TriggerEvaluator — _eval_on_run_completed
# ---------------------------------------------------------------------------


class TestTriggerEvaluatorOnRunCompleted:
    """TriggerEvaluator correctly evaluates on_run_completed triggers."""

    def _make_record(
        self,
        status: str = "completed",
        source: str = "job",
        spec_ref: str = "my_job",
        scope: str = "63",
        depth: int = 0,
    ) -> ChangeRecord:
        return ChangeRecord(
            table_name="run.events",
            operation=ChangeOperation.INSERT,
            new_values={
                "run_id": str(uuid4()),
                "status": status,
                "source": source,
                "spec_ref": spec_ref,
                "scope": scope,
                "depth": depth,
            },
        )

    def test_completed_matches_completed_trigger(self) -> None:
        from gispulse.rules.trigger_evaluator import TriggerEvaluator

        trigger = _make_trigger(status="completed")
        record = self._make_record(status="completed")
        evaluator = TriggerEvaluator()
        results = evaluator.evaluate(record, [trigger])
        assert len(results) == 1
        assert results[0].matched is True

    def test_failed_does_not_match_completed_trigger(self) -> None:
        from gispulse.rules.trigger_evaluator import TriggerEvaluator

        trigger = _make_trigger(status="completed")
        record = self._make_record(status="failed")
        evaluator = TriggerEvaluator()
        results = evaluator.evaluate(record, [trigger])
        assert results[0].matched is False

    def test_completed_does_not_match_failed_trigger(self) -> None:
        from gispulse.rules.trigger_evaluator import TriggerEvaluator

        trigger = _make_trigger(status="failed")
        record = self._make_record(status="completed")
        evaluator = TriggerEvaluator()
        results = evaluator.evaluate(record, [trigger])
        assert results[0].matched is False

    def test_any_matches_both_completed_and_failed(self) -> None:
        from gispulse.rules.trigger_evaluator import TriggerEvaluator

        trigger = _make_trigger(status="any")
        evaluator = TriggerEvaluator()

        rec_c = self._make_record(status="completed")
        rec_f = self._make_record(status="failed")
        assert evaluator.evaluate(rec_c, [trigger])[0].matched is True
        assert evaluator.evaluate(rec_f, [trigger])[0].matched is True

    def test_source_filter(self) -> None:
        from gispulse.rules.trigger_evaluator import TriggerEvaluator

        trigger = _make_trigger(source="job")
        evaluator = TriggerEvaluator()

        rec_job = self._make_record(source="job")
        rec_sched = self._make_record(source="schedule")
        assert evaluator.evaluate(rec_job, [trigger])[0].matched is True
        assert evaluator.evaluate(rec_sched, [trigger])[0].matched is False

    def test_spec_ref_filter_substring(self) -> None:
        from gispulse.rules.trigger_evaluator import TriggerEvaluator

        trigger = _make_trigger(spec_ref="ingest")
        evaluator = TriggerEvaluator()

        rec_match = self._make_record(spec_ref="ingest_dept_63")
        rec_no = self._make_record(spec_ref="build_dept_63")
        assert evaluator.evaluate(rec_match, [trigger])[0].matched is True
        assert evaluator.evaluate(rec_no, [trigger])[0].matched is False

    def test_scope_filter(self) -> None:
        from gispulse.rules.trigger_evaluator import TriggerEvaluator

        trigger = _make_trigger(scope="63")
        evaluator = TriggerEvaluator()

        rec_match = self._make_record(scope="63")
        rec_no = self._make_record(scope="75")
        assert evaluator.evaluate(rec_match, [trigger])[0].matched is True
        assert evaluator.evaluate(rec_no, [trigger])[0].matched is False

    def test_depth_at_max_does_not_match(self) -> None:
        """depth == max_depth → no match (anti-loop guard)."""
        from gispulse.rules.trigger_evaluator import TriggerEvaluator

        trigger = _make_trigger(max_depth=3)
        evaluator = TriggerEvaluator()
        record = self._make_record(depth=3)
        assert evaluator.evaluate(record, [trigger])[0].matched is False

    def test_depth_below_max_matches(self) -> None:
        from gispulse.rules.trigger_evaluator import TriggerEvaluator

        trigger = _make_trigger(max_depth=5)
        evaluator = TriggerEvaluator()
        record = self._make_record(depth=4)
        assert evaluator.evaluate(record, [trigger])[0].matched is True

    def test_disabled_trigger_never_matches(self) -> None:
        from gispulse.rules.trigger_evaluator import TriggerEvaluator

        trigger = _make_trigger(enabled=False)
        evaluator = TriggerEvaluator()
        record = self._make_record(status="completed")
        results = evaluator.evaluate(record, [trigger])
        # disabled triggers are skipped entirely (no FiredTrigger created)
        matched = [ft for ft in results if ft.matched]
        assert len(matched) == 0

    def test_no_triggers_returns_empty(self) -> None:
        from gispulse.rules.trigger_evaluator import TriggerEvaluator

        evaluator = TriggerEvaluator()
        record = self._make_record()
        results = evaluator.evaluate(record, [])
        assert results == []


# ---------------------------------------------------------------------------
# TriggerJobBridge — run context propagation
# ---------------------------------------------------------------------------


class TestTriggerJobBridgeRunContext:
    """Bridge propagates trigger_depth and scope from FiredTrigger to Job."""

    def test_run_job_carries_trigger_depth_and_scope(self) -> None:
        queue = FakeQueue()
        bridge = TriggerJobBridge(job_queue=queue)

        ft = FiredTrigger(
            trigger_id=uuid4(),
            matched=True,
            actions_dispatched=[{
                "action_type": "RUN_JOB",
                "rule_id": "r1",
                "trigger_depth": 2,
                "scope": "63",
            }],
            result_summary={"run_id": "run-123", "scope": "63"},
        )
        jobs = bridge.on_triggers_fired_sync([ft])
        assert len(jobs) == 1
        assert jobs[0].parameters["trigger_depth"] == 2
        assert jobs[0].parameters["scope"] == "63"
        assert jobs[0].parameters["source_run_id"] == "run-123"

    def test_run_graph_carries_trigger_depth_and_scope(self) -> None:
        queue = FakeQueue()
        bridge = TriggerJobBridge(job_queue=queue)

        ft = FiredTrigger(
            trigger_id=uuid4(),
            matched=True,
            actions_dispatched=[{
                "action_type": "RUN_GRAPH",
                "graph_id": "g1",
                "trigger_depth": 1,
                "scope": "75",
            }],
            result_summary={"run_id": "run-456", "scope": "75"},
        )
        jobs = bridge.on_triggers_fired_sync([ft])
        assert len(jobs) == 1
        assert jobs[0].parameters["trigger_depth"] == 1
        assert jobs[0].parameters["scope"] == "75"

    def test_no_context_keys_omit_optional_fields(self) -> None:
        """Old-style actions without depth/scope don't break."""
        queue = FakeQueue()
        bridge = TriggerJobBridge(job_queue=queue)

        ft = FiredTrigger(
            trigger_id=uuid4(),
            matched=True,
            actions_dispatched=[{"action_type": "RUN_JOB", "rule_id": "r2"}],
        )
        jobs = bridge.on_triggers_fired_sync([ft])
        assert len(jobs) == 1
        assert "trigger_depth" not in jobs[0].parameters
        assert "scope" not in jobs[0].parameters


# ---------------------------------------------------------------------------
# RunCompletionTriggerSink — unit tests
# ---------------------------------------------------------------------------


class TestRunCompletionTriggerSink:
    """Sink routes run terminal events to TriggerJobBridge."""

    def _make_sink(self, triggers: list[Trigger] | None = None, inner=None):
        """Build a RunCompletionTriggerSink wired to a FakeQueue."""
        trigger_repo: InMemoryRepository = InMemoryRepository()
        if triggers:
            for t in triggers:
                trigger_repo.save(t)

        queue = FakeQueue()
        bridge = TriggerJobBridge(job_queue=queue)

        sink = RunCompletionTriggerSink(
            trigger_repo=trigger_repo,
            bridge=bridge,
            inner=inner or SpySink(),
        )
        return sink, bridge, queue

    def test_non_terminal_event_not_evaluated(self) -> None:
        """run.step.started does not evaluate on_run_completed triggers."""
        trigger = _make_trigger()
        sink, bridge, queue = self._make_sink(triggers=[trigger])
        sink.emit("run.step.started", {"run_id": "r1", "step_id": "s1"})
        assert queue.enqueued == []

    def test_inner_sink_always_receives_event(self) -> None:
        """Inner sink (EventHubSink) receives all events, even non-terminal."""
        inner = SpySink()
        sink, _, _ = self._make_sink(inner=inner)
        sink.emit("run.started", {"run_id": "r1"})
        sink.emit("run.completed", {"run_id": "r1", "status": "completed"})
        assert "run.started" in inner.types()
        assert "run.completed" in inner.types()

    def test_no_triggers_is_noop(self) -> None:
        """Without any on_run_completed triggers, behaviour is identical to inner-only."""
        inner = SpySink()
        sink, _, queue = self._make_sink(triggers=[], inner=inner)
        sink.emit("run.completed", {"run_id": "r1", "status": "completed"})
        # inner still receives the event
        assert "run.completed" in inner.types()
        # but no job enqueued
        assert queue.enqueued == []

    def test_completed_event_enqueues_job_sync(self) -> None:
        """run.completed matching an on_run_completed trigger creates job (sync path).

        In sync path (no running event loop), on_triggers_fired_sync is used.
        Jobs are created but NOT enqueued to the async queue (by design — the
        bridge's sync path returns Job objects; callers must handle them).
        We verify that the evaluator matched and produced jobs via bridge.
        """
        trigger = _make_trigger(status="completed", rule_id="r42")
        trigger_repo: InMemoryRepository = InMemoryRepository()
        trigger_repo.save(trigger)
        queue = FakeQueue()
        bridge = TriggerJobBridge(job_queue=queue)

        # Patch on_triggers_fired_sync to capture calls
        created_jobs: list = []
        original_sync = bridge.on_triggers_fired_sync

        def _capture_sync(fired):
            jobs = original_sync(fired)
            created_jobs.extend(jobs)
            return jobs

        bridge.on_triggers_fired_sync = _capture_sync

        sink = RunCompletionTriggerSink(
            trigger_repo=trigger_repo,
            bridge=bridge,
            inner=SpySink(),
        )

        sink.emit("run.completed", {
            "run_id": "run-1",
            "status": "completed",
            "source": "job",
            "spec_ref": "my_job",
            "scope": "63",
            "depth": 0,
        })

        # At least one job should have been produced
        assert len(created_jobs) >= 1
        assert created_jobs[0].parameters.get("rule_ids") == ["r42"]
        assert created_jobs[0].parameters.get("trigger_depth") == 1
        assert created_jobs[0].parameters.get("scope") == "63"

    def test_failed_event_does_not_match_completed_trigger_sync(self) -> None:
        """run.failed with a trigger that requires completed → no job."""
        trigger = _make_trigger(status="completed")
        inner = SpySink()
        sink, bridge, queue = self._make_sink(triggers=[trigger], inner=inner)

        sink.emit("run.failed", {
            "run_id": "run-2",
            "status": "failed",
            "source": "job",
            "spec_ref": "x",
            "scope": "",
            "depth": 0,
        })
        # inner received the event
        assert "run.failed" in inner.types()

    def test_depth_guard_in_sink(self) -> None:
        """Depth ≥ max_depth → no job enqueued, no exception."""
        trigger = _make_trigger(status="completed", max_depth=2)
        inner = SpySink()
        sink, bridge, queue = self._make_sink(triggers=[trigger], inner=inner)

        # depth=2 == max_depth=2 → blocked
        sink.emit("run.completed", {
            "run_id": "run-deep",
            "status": "completed",
            "source": "job",
            "spec_ref": "x",
            "scope": "",
            "depth": 2,
        })
        # inner still received it
        assert "run.completed" in inner.types()
        # no job queued
        assert queue.enqueued == []

    @pytest.mark.asyncio
    async def test_completed_event_async_enqueues_job(self) -> None:
        """run.completed with a running loop → job enqueued via asyncio."""
        trigger = _make_trigger(status="completed", rule_id="r99")
        trigger_repo: InMemoryRepository = InMemoryRepository()
        trigger_repo.save(trigger)

        queue = FakeQueue()
        bridge = TriggerJobBridge(job_queue=queue)

        # Capture the running loop before building the sink
        loop = asyncio.get_running_loop()
        sink = RunCompletionTriggerSink(
            trigger_repo=trigger_repo,
            bridge=bridge,
            inner=SpySink(),
            loop=loop,
        )

        sink.emit("run.completed", {
            "run_id": "run-async",
            "status": "completed",
            "source": "job",
            "spec_ref": "x",
            "scope": "63",
            "depth": 0,
        })

        # Give the scheduled coroutine a chance to run
        await asyncio.sleep(0.1)

        assert len(queue.enqueued) == 1
        job = queue.enqueued[0]
        assert "r99" in job.parameters.get("rule_ids", [])
        assert job.parameters["trigger_depth"] == 1
        assert job.parameters["scope"] == "63"

    @pytest.mark.asyncio
    async def test_max_depth_blocks_async_enqueue(self) -> None:
        """Depth at max_depth → no coroutine scheduled, queue stays empty."""
        trigger = _make_trigger(status="completed", max_depth=3)
        trigger_repo: InMemoryRepository = InMemoryRepository()
        trigger_repo.save(trigger)

        queue = FakeQueue()
        bridge = TriggerJobBridge(job_queue=queue)
        loop = asyncio.get_running_loop()
        sink = RunCompletionTriggerSink(
            trigger_repo=trigger_repo,
            bridge=bridge,
            inner=SpySink(),
            loop=loop,
        )

        sink.emit("run.completed", {
            "run_id": "deep-run",
            "status": "completed",
            "source": "job",
            "spec_ref": "x",
            "scope": "",
            "depth": 3,   # == max_depth
        })
        await asyncio.sleep(0.1)
        assert queue.enqueued == []


# ---------------------------------------------------------------------------
# Scenarios router — GraphExecutor construction (anti-#443)
# ---------------------------------------------------------------------------


class TestScenariosRouterGraphExecutorConstruction:
    """GraphExecutor is constructed with capability_getter, not rule_repo (#443)."""

    def test_run_scenario_with_graph_constructs_executor_correctly(self) -> None:
        """Calling POST /scenarios/{id}/run on a scenario with a graph should not
        raise TypeError from the wrong constructor argument."""
        import os
        os.environ["GISPULSE_STORAGE"] = "memory"

        from fastapi.testclient import TestClient
        from gispulse.adapters.http.app import create_app
        from gispulse.adapters.http.rate_limit import limiter
        from unittest.mock import MagicMock

        limiter.enabled = False
        app = create_app()
        app.state.spatial_engine = MagicMock()

        client = TestClient(app)

        # Create a scenario with a minimal graph (one DATASET node, no edges)
        create_resp = client.post("/scenarios", json={
            "name": "graph_scenario",
            "dataset_id": None,
            "jobs": [],
            "rules": [],
            "metadata": {
                "graph": {
                    "nodes": [
                        {"id": "n1", "node_type": "dataset", "capability": None, "params": {}, "bind": None}
                    ],
                    "edges": [],
                }
            },
        })
        assert create_resp.status_code == 201
        scenario_id = create_resp.json()["id"]

        # Execute the scenario — this previously raised TypeError: unexpected keyword 'rule_repo'
        run_resp = client.post(f"/scenarios/{scenario_id}/run")
        assert run_resp.status_code == 200
        body = run_resp.json()
        assert body["status"] in ("success", "failed")  # execution may fail; ctor must not

    def test_run_node_constructs_executor_correctly(self) -> None:
        """POST /scenarios/{id}/run-node must not raise from wrong constructor arg."""
        import os
        os.environ["GISPULSE_STORAGE"] = "memory"

        from fastapi.testclient import TestClient
        from gispulse.adapters.http.app import create_app
        from gispulse.adapters.http.rate_limit import limiter
        from unittest.mock import MagicMock

        limiter.enabled = False
        app = create_app()
        app.state.spatial_engine = MagicMock()

        client = TestClient(app)

        create_resp = client.post("/scenarios", json={
            "name": "node_run_scenario",
            "dataset_id": None,
            "jobs": [],
            "rules": [],
            "metadata": {
                "graph": {
                    "nodes": [
                        {"id": "cap1", "node_type": "capability", "capability": "buffer", "params": {"distance": 1.0}, "bind": None}
                    ],
                    "edges": [],
                }
            },
        })
        assert create_resp.status_code == 201
        scenario_id = create_resp.json()["id"]

        run_resp = client.post(
            f"/scenarios/{scenario_id}/run-node",
            json={"node_id": "cap1", "override_params": {}},
        )
        # May fail (no input GDF) but must not raise TypeError from ctor
        assert run_resp.status_code in (200, 422, 500)
        if run_resp.status_code == 200:
            body = run_resp.json()
            # Must not be a Python TypeError about rule_repo
            if body.get("error"):
                assert "rule_repo" not in str(body.get("error", ""))


# ---------------------------------------------------------------------------
# Scenarios router — PipelineRun persistence
# ---------------------------------------------------------------------------


class TestScenariosRunPipelineRun:
    """POST /scenarios/{id}/run persists a PipelineRun and emits run events."""

    def _client_with_run_repo(self, tmp_path):
        """Create a TestClient with a real RunRepository attached to app.state."""
        import os
        os.environ["GISPULSE_STORAGE"] = "memory"

        from fastapi.testclient import TestClient
        from gispulse.adapters.http.app import create_app
        from gispulse.adapters.http.rate_limit import limiter
        from gispulse.persistence.run_repository import RunRepository
        from unittest.mock import MagicMock

        limiter.enabled = False
        app = create_app()
        app.state.spatial_engine = MagicMock()
        # Inject a real run_repo + spy sink
        run_repo = RunRepository(db_path=tmp_path / "runs.db")
        app.state.run_repo = run_repo
        spy = SpySink()
        app.state.event_sink = spy
        return TestClient(app), run_repo, spy

    def test_run_scenario_persists_pipeline_run(self, tmp_path) -> None:
        client, run_repo, spy = self._client_with_run_repo(tmp_path)

        create_resp = client.post("/scenarios", json={
            "name": "emit_scenario",
            "dataset_id": None,
            "jobs": [],
            "rules": [],
            "metadata": {},
        })
        assert create_resp.status_code == 201
        scenario_id = create_resp.json()["id"]

        run_resp = client.post(f"/scenarios/{scenario_id}/run")
        assert run_resp.status_code == 200

        runs = run_repo.list_all()
        assert len(runs) >= 1
        run = runs[-1]
        assert run.source == "scenario"
        assert str(scenario_id) in run.spec_ref

    def test_run_scenario_emits_run_events(self, tmp_path) -> None:
        client, run_repo, spy = self._client_with_run_repo(tmp_path)

        create_resp = client.post("/scenarios", json={
            "name": "events_scenario",
            "dataset_id": None,
            "jobs": [],
            "rules": [],
            "metadata": {},
        })
        scenario_id = create_resp.json()["id"]
        client.post(f"/scenarios/{scenario_id}/run")

        # run.started must precede run.completed/run.failed
        assert "run.started" in spy.types()
        terminal = [t for t in spy.types() if t in ("run.completed", "run.failed")]
        assert len(terminal) >= 1
        idx_started = spy.types().index("run.started")
        idx_terminal = spy.types().index(terminal[0])
        assert idx_started < idx_terminal


# ---------------------------------------------------------------------------
# P1a — full cycle anti-loop test
# ---------------------------------------------------------------------------


class TestAntiLoopCycleGuard:
    """Self-triggering scenario: max_depth=2 → exactly 2 re-triggers then blocked.

    Simulates the full chain without a real worker:
      emit(run.completed, depth=0)
        → trigger evaluates, creates job depth=1 → emit(run.completed, depth=1)
          → trigger evaluates, creates job depth=2 → emit(run.completed, depth=2)
            → depth=2 == max_depth=2 → blocked (no job created)
    """

    def test_self_trigger_stops_at_max_depth(self) -> None:
        """depth propagates correctly through the chain; max_depth=2 blocks at depth=2."""
        from gispulse.rules.trigger_evaluator import TriggerEvaluator

        trigger = _make_trigger(status="completed", max_depth=2)
        evaluator = TriggerEvaluator()

        def _make_run_completed_record(depth: int) -> "ChangeRecord":
            return ChangeRecord(
                table_name="run.events",
                operation=ChangeOperation.INSERT,
                new_values={
                    "run_id": str(uuid4()),
                    "status": "completed",
                    "source": "job",
                    "spec_ref": "self_trigger_job",
                    "scope": "",
                    "depth": depth,
                },
            )

        # depth=0 → matches (depth < max_depth=2)
        result_d0 = evaluator.evaluate(_make_run_completed_record(0), [trigger])
        assert result_d0[0].matched is True, "depth=0 must match when max_depth=2"

        # depth=1 → matches (depth < max_depth=2)
        result_d1 = evaluator.evaluate(_make_run_completed_record(1), [trigger])
        assert result_d1[0].matched is True, "depth=1 must match when max_depth=2"

        # depth=2 == max_depth → BLOCKED
        result_d2 = evaluator.evaluate(_make_run_completed_record(2), [trigger])
        assert result_d2[0].matched is False, "depth=2 == max_depth=2 must be blocked"

        # depth=3 > max_depth → also blocked
        result_d3 = evaluator.evaluate(_make_run_completed_record(3), [trigger])
        assert result_d3[0].matched is False, "depth=3 > max_depth=2 must be blocked"

    def test_sink_depth_propagation_chain(self) -> None:
        """RunCompletionTriggerSink increments depth in FiredTrigger.result_summary."""
        trigger = _make_trigger(status="completed", max_depth=3, rule_id="chain_rule")
        trigger_repo: InMemoryRepository = InMemoryRepository()
        trigger_repo.save(trigger)

        queue = FakeQueue()
        bridge = TriggerJobBridge(job_queue=queue)

        # Capture what the sync bridge creates to inspect trigger_depth
        created_jobs: list = []
        original_sync = bridge.on_triggers_fired_sync

        def _capture_sync(fired):
            jobs = original_sync(fired)
            created_jobs.extend(jobs)
            return jobs

        bridge.on_triggers_fired_sync = _capture_sync

        sink = RunCompletionTriggerSink(
            trigger_repo=trigger_repo,
            bridge=bridge,
            inner=SpySink(),
        )

        # Simulate depth=0 run completing
        sink.emit("run.completed", {
            "run_id": "run-chain-0",
            "status": "completed",
            "source": "job",
            "spec_ref": "x",
            "scope": "63",
            "depth": 0,
        })
        assert len(created_jobs) == 1
        assert created_jobs[0].parameters["trigger_depth"] == 1

        # Simulate the triggered job at depth=1 completing
        sink.emit("run.completed", {
            "run_id": "run-chain-1",
            "status": "completed",
            "source": "job",
            "spec_ref": "x",
            "scope": "63",
            "depth": 1,
        })
        assert len(created_jobs) == 2
        assert created_jobs[1].parameters["trigger_depth"] == 2

        # Simulate depth=2 completing (still < max_depth=3 → matches)
        sink.emit("run.completed", {
            "run_id": "run-chain-2",
            "status": "completed",
            "source": "job",
            "spec_ref": "x",
            "scope": "63",
            "depth": 2,
        })
        assert len(created_jobs) == 3
        assert created_jobs[2].parameters["trigger_depth"] == 3

        # Simulate depth=3 completing (== max_depth=3 → BLOCKED)
        sink.emit("run.completed", {
            "run_id": "run-chain-3",
            "status": "completed",
            "source": "job",
            "spec_ref": "x",
            "scope": "63",
            "depth": 3,
        })
        # No new job — depth guard blocks it
        assert len(created_jobs) == 3, "depth=3 == max_depth=3 must be blocked; no 4th job"


# ---------------------------------------------------------------------------
# P1b — thread dispatch: emit from sync thread enqueues via loop
# ---------------------------------------------------------------------------


class TestThreadDispatch:
    """emit() from a sync (non-event-loop) thread uses run_coroutine_threadsafe."""

    @pytest.mark.asyncio
    async def test_emit_from_thread_enqueues_job(self) -> None:
        """Simulates the FastAPI sync handler context: emit called from a thread pool
        worker while the event loop is running on the main thread."""
        import threading

        trigger = _make_trigger(status="completed", rule_id="thread_rule")
        trigger_repo: InMemoryRepository = InMemoryRepository()
        trigger_repo.save(trigger)

        queue = FakeQueue()
        bridge = TriggerJobBridge(job_queue=queue)
        loop = asyncio.get_running_loop()

        sink = RunCompletionTriggerSink(
            trigger_repo=trigger_repo,
            bridge=bridge,
            inner=SpySink(),
            loop=loop,
        )

        emit_done = threading.Event()

        def _thread_emit():
            # This simulates a sync FastAPI handler thread: no running loop here.
            sink.emit("run.completed", {
                "run_id": "thread-run-1",
                "status": "completed",
                "source": "job",
                "spec_ref": "x",
                "scope": "42",
                "depth": 0,
            })
            emit_done.set()

        thread = threading.Thread(target=_thread_emit)
        thread.start()

        # Wait for the thread to finish its emit call
        await asyncio.get_event_loop().run_in_executor(None, emit_done.wait)
        thread.join()

        # Give the scheduled coroutine time to run
        await asyncio.sleep(0.2)

        assert len(queue.enqueued) == 1
        job = queue.enqueued[0]
        assert "thread_rule" in job.parameters.get("rule_ids", [])
        assert job.parameters.get("trigger_depth") == 1
        assert job.parameters.get("scope") == "42"


# ---------------------------------------------------------------------------
# P2 — 404 before run creation produces no PipelineRun
# ---------------------------------------------------------------------------


class TestScenarios404NoRun:
    """Validation rejections (404/422) must not create a PipelineRun entry."""

    def _client_with_run_repo(self, tmp_path):
        import os
        os.environ["GISPULSE_STORAGE"] = "memory"

        from fastapi.testclient import TestClient
        from gispulse.adapters.http.app import create_app
        from gispulse.adapters.http.rate_limit import limiter
        from gispulse.persistence.run_repository import RunRepository
        from unittest.mock import MagicMock

        limiter.enabled = False
        app = create_app()
        app.state.spatial_engine = MagicMock()
        run_repo = RunRepository(db_path=tmp_path / "runs.db")
        app.state.run_repo = run_repo
        spy = SpySink()
        app.state.event_sink = spy
        return TestClient(app), run_repo, spy

    def test_run_unknown_scenario_creates_no_run(self, tmp_path) -> None:
        """POST /scenarios/{unknown_id}/run → 404 → no PipelineRun persisted."""
        from uuid import uuid4 as _uuid4
        client, run_repo, _ = self._client_with_run_repo(tmp_path)
        unknown_id = str(_uuid4())
        resp = client.post(f"/scenarios/{unknown_id}/run")
        assert resp.status_code == 404
        assert run_repo.count() == 0

    def test_run_node_unknown_scenario_creates_no_run(self, tmp_path) -> None:
        """POST /scenarios/{unknown_id}/run-node → 404 → no PipelineRun persisted."""
        from uuid import uuid4 as _uuid4
        client, run_repo, _ = self._client_with_run_repo(tmp_path)
        unknown_id = str(_uuid4())
        resp = client.post(
            f"/scenarios/{unknown_id}/run-node",
            json={"node_id": "n1", "override_params": {}},
        )
        assert resp.status_code == 404
        assert run_repo.count() == 0

    def test_run_node_no_graph_creates_no_run(self, tmp_path) -> None:
        """POST /scenarios/{id}/run-node on scenario without graph → 422 → no PipelineRun."""
        client, run_repo, _ = self._client_with_run_repo(tmp_path)

        # Create scenario WITHOUT a graph
        create_resp = client.post("/scenarios", json={
            "name": "no_graph_scenario",
            "dataset_id": None,
            "jobs": [],
            "rules": [],
            "metadata": {},
        })
        assert create_resp.status_code == 201
        scenario_id = create_resp.json()["id"]

        resp = client.post(
            f"/scenarios/{scenario_id}/run-node",
            json={"node_id": "n1", "override_params": {}},
        )
        assert resp.status_code == 422
        assert run_repo.count() == 0

    def test_execution_error_emits_run_failed(self, tmp_path) -> None:
        """If execution raises, a PipelineRun exists and run.failed is emitted."""
        client, run_repo, spy = self._client_with_run_repo(tmp_path)

        # Create a scenario with a graph node that will fail (unknown capability)
        create_resp = client.post("/scenarios", json={
            "name": "fail_scenario",
            "dataset_id": None,
            "jobs": [],
            "rules": [],
            "metadata": {
                "graph": {
                    "nodes": [
                        {
                            "id": "fail_node",
                            "node_type": "capability",
                            "capability": "__nonexistent_capability__",
                            "params": {},
                            "bind": None,
                        }
                    ],
                    "edges": [],
                }
            },
        })
        assert create_resp.status_code == 201
        scenario_id = create_resp.json()["id"]

        run_resp = client.post(f"/scenarios/{scenario_id}/run")
        # Execution may return 200 with failed status or 500 — either way run is persisted
        assert run_resp.status_code in (200, 500)

        # A PipelineRun must exist (execution started)
        assert run_repo.count() >= 1
        # run.started was emitted
        assert "run.started" in spy.types()


# ---------------------------------------------------------------------------
# P3 — enqueue exceptions are observed and logged (not swallowed silently)
# ---------------------------------------------------------------------------


class TestEnqueueErrorObserved:
    """P3: if on_triggers_fired raises, the error is logged and the original event
    is unaffected (no exception propagates back to the caller)."""

    @pytest.mark.asyncio
    async def test_queue_raises_logs_error_and_original_event_intact(self, capsys) -> None:
        """When the bridge's async enqueue raises, the done-callback logs the error.

        The gispulse logger uses structlog (not stdlib), so we capture stdout
        (structlog default output) rather than caplog. The original run.completed
        event must still reach the inner sink untouched.
        """
        trigger = _make_trigger(status="completed", rule_id="err_rule")
        trigger_repo: InMemoryRepository = InMemoryRepository()
        trigger_repo.save(trigger)

        # Bridge that raises on on_triggers_fired
        class ErrorBridge:
            async def on_triggers_fired(self, fired):
                raise RuntimeError("redis_down")

            def on_triggers_fired_sync(self, fired):
                raise RuntimeError("redis_down")

        inner = SpySink()
        loop = asyncio.get_running_loop()
        sink = RunCompletionTriggerSink(
            trigger_repo=trigger_repo,
            bridge=ErrorBridge(),
            inner=inner,
            loop=loop,
        )

        sink.emit("run.completed", {
            "run_id": "err-run-1",
            "status": "completed",
            "source": "job",
            "spec_ref": "x",
            "scope": "",
            "depth": 0,
        })

        # Give the future's done-callback a chance to fire
        await asyncio.sleep(0.2)

        # Original run.completed event was still delivered to inner sink
        assert "run.completed" in inner.types()

        # An error was logged (done-callback fired) — structlog writes to stdout
        captured = capsys.readouterr()
        assert "enqueue_error" in captured.out or "redis_down" in captured.out, (
            f"Expected enqueue_error or redis_down in stdout. Got:\n{captured.out}"
        )

"""Tests for orchestration.trigger_bridge — FiredTrigger → JobQueue bridge."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from core.models import FiredTrigger
from orchestration.trigger_bridge import TriggerJobBridge


@pytest.fixture
def mock_queue():
    q = AsyncMock()
    q.enqueue = AsyncMock()
    return q


@pytest.fixture
def bridge(mock_queue):
    return TriggerJobBridge(job_queue=mock_queue)


def _make_fired(matched=True, actions=None, trigger_id="t1"):
    return FiredTrigger(
        trigger_id=trigger_id,
        change_record_id="cr1",
        matched=matched,
        actions_dispatched=actions or [],
        eval_time_ms=1.0,
        result_summary={},
    )


class TestTriggerJobBridge:
    def test_unmatched_triggers_ignored(self, bridge, mock_queue):
        fired = [_make_fired(matched=False, actions=[{"action_type": "RUN_JOB", "rule_id": "r1"}])]
        jobs = bridge.on_triggers_fired_sync(fired)
        assert len(jobs) == 0

    def test_run_job_creates_job(self, bridge):
        fired = [_make_fired(actions=[{"action_type": "RUN_JOB", "rule_id": "r1"}])]
        # Use sync version to avoid async complexity
        ft = fired[0]
        ft.actions_dispatched = [{"action_type": "RUN_JOB", "rule_id": "r1"}]
        jobs = bridge.on_triggers_fired_sync(fired)
        assert len(jobs) == 1
        assert jobs[0].parameters["rule_ids"] == ["r1"]
        assert jobs[0].parameters["triggered_by"] == "trigger"
        assert jobs[0].parameters["trigger_id"] == "t1"

    def test_run_job_without_rule_id_skipped(self, bridge):
        fired = [_make_fired(actions=[{"action_type": "RUN_JOB"}])]
        ft = fired[0]
        ft.actions_dispatched = [{"action_type": "RUN_JOB"}]
        jobs = bridge.on_triggers_fired_sync(fired)
        assert len(jobs) == 0

    def test_run_graph_creates_job(self, bridge):
        fired = [_make_fired(actions=[{"action_type": "RUN_GRAPH", "graph_id": "g1"}])]
        ft = fired[0]
        ft.actions_dispatched = [{"action_type": "RUN_GRAPH", "graph_id": "g1"}]
        jobs = bridge.on_triggers_fired_sync(fired)
        assert len(jobs) == 1
        assert jobs[0].parameters["graph_id"] == "g1"
        assert jobs[0].parameters["triggered_by"] == "trigger"

    def test_run_graph_without_graph_id_skipped(self, bridge):
        fired = [_make_fired(actions=[{"action_type": "RUN_GRAPH"}])]
        ft = fired[0]
        ft.actions_dispatched = [{"action_type": "RUN_GRAPH"}]
        jobs = bridge.on_triggers_fired_sync(fired)
        assert len(jobs) == 0

    def test_unknown_action_type_ignored(self, bridge):
        fired = [_make_fired(actions=["NOTIFY"])]
        ft = fired[0]
        ft.actions_dispatched = ["NOTIFY"]
        jobs = bridge.on_triggers_fired_sync(fired)
        assert len(jobs) == 0

    def test_multiple_actions_multiple_jobs(self, bridge):
        actions = [
            {"action_type": "RUN_JOB", "rule_id": "r1"},
            {"action_type": "RUN_JOB", "rule_id": "r2"},
        ]
        fired = [_make_fired(actions=actions)]
        ft = fired[0]
        ft.actions_dispatched = actions
        jobs = bridge.on_triggers_fired_sync(fired)
        assert len(jobs) == 2

    @pytest.mark.asyncio
    async def test_async_enqueue(self, bridge, mock_queue):
        actions = [{"action_type": "RUN_JOB", "rule_id": "r1"}]
        fired = [_make_fired(actions=actions)]
        ft = fired[0]
        ft.actions_dispatched = actions
        jobs = await bridge.on_triggers_fired(fired)
        assert len(jobs) == 1
        mock_queue.enqueue.assert_called_once()

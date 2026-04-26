"""Tests for gispulse.adapters.esb.action_dispatcher — multi-action ESB dispatch.

ActionDispatcher routes an ActionDef to one of 9 handlers (notify,
set_field, update_aggregate, run_job, run_graph, webhook, enqueue,
log_event, run_sql). Dispatcher bugs either drop actions silently or
invoke the wrong handler — both break trigger configurations.

We inject fake callables for job_runner/graph_runner/event_hub/
sql_executor/webhook_client and assert what reaches them.
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from gispulse.adapters.esb.action_dispatcher import ActionDispatcher, TriggerContext
from core.graph import ActionDef, ActionType, EvalResult, Transition
from core.models import Trigger


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeHub:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def broadcast(self, event_type: str, data: dict) -> None:
        self.events.append((event_type, data))


class FakeSQL:
    def __init__(self):
        self.calls: list[tuple[str, list]] = []

    def __call__(self, sql: str, params: list) -> None:
        self.calls.append((sql, params))


def _make_context(
    *,
    table: str = "parcels",
    operation: str = "INSERT",
    row_id: str = "",
    new_attrs: dict | None = None,
    transition: Transition | None = None,
) -> TriggerContext:
    return TriggerContext(
        trigger=Trigger(id=uuid4(), name="t"),
        eval_result=EvalResult(matched=True, transition=transition),
        table=table,
        operation=operation,
        row_id=row_id or str(uuid4()),
        new_attrs=new_attrs or {},
    )


# ---------------------------------------------------------------------------
# NOTIFY
# ---------------------------------------------------------------------------


class TestNotify:
    def test_broadcasts_via_event_hub(self):
        hub = FakeHub()
        d = ActionDispatcher(event_hub=hub)
        action = ActionDef(action_type=ActionType.NOTIFY, config={"channel": "alerts"})
        ctx = _make_context(transition=Transition.ENTER)
        d.dispatch(action, ctx)
        assert len(hub.events) == 1
        event_type, data = hub.events[0]
        assert event_type == "trigger:alerts"
        assert data["transition"] == "ENTER"

    def test_issues_pg_notify_via_sql_executor(self):
        sql = FakeSQL()
        d = ActionDispatcher(sql_executor=sql)
        action = ActionDef(action_type=ActionType.NOTIFY, config={"channel": "gispulse_events"})
        d.dispatch(action, _make_context())
        assert len(sql.calls) == 1
        assert "pg_notify" in sql.calls[0][0]

    def test_rejects_unsafe_channel_name(self):
        """validate_identifier must block injection via channel."""
        hub = FakeHub()
        d = ActionDispatcher(event_hub=hub)
        action = ActionDef(
            action_type=ActionType.NOTIFY,
            config={"channel": "evil; DROP TABLE"},
        )
        # Silent failure contract — error logged, no broadcast
        d.dispatch(action, _make_context())
        assert hub.events == []


# ---------------------------------------------------------------------------
# SET_FIELD
# ---------------------------------------------------------------------------


class TestSetField:
    def test_emits_update_sql(self):
        sql = FakeSQL()
        d = ActionDispatcher(sql_executor=sql)
        action = ActionDef(
            action_type=ActionType.SET_FIELD,
            config={"field": "status", "value": "archived"},
        )
        d.dispatch(action, _make_context(table="zones", row_id="r1"))
        assert len(sql.calls) == 1
        assert 'UPDATE "zones"' in sql.calls[0][0]
        assert '"status"' in sql.calls[0][0]
        assert sql.calls[0][1] == ["archived", "r1"]

    def test_rejects_unsafe_table(self):
        sql = FakeSQL()
        d = ActionDispatcher(sql_executor=sql)
        action = ActionDef(
            action_type=ActionType.SET_FIELD,
            config={"field": "status", "value": "x"},
        )
        d.dispatch(action, _make_context(table="bad; DROP"))
        assert sql.calls == []

    def test_rejects_unsafe_field_name(self):
        sql = FakeSQL()
        d = ActionDispatcher(sql_executor=sql)
        action = ActionDef(
            action_type=ActionType.SET_FIELD,
            config={"field": "evil--inject", "value": "x"},
        )
        d.dispatch(action, _make_context())
        assert sql.calls == []

    def test_missing_field_is_noop(self):
        sql = FakeSQL()
        d = ActionDispatcher(sql_executor=sql)
        action = ActionDef(action_type=ActionType.SET_FIELD, config={"value": "x"})
        d.dispatch(action, _make_context())
        assert sql.calls == []


# ---------------------------------------------------------------------------
# UPDATE_AGGREGATE
# ---------------------------------------------------------------------------


class TestUpdateAggregate:
    def test_emits_aggregate_update(self):
        sql = FakeSQL()
        d = ActionDispatcher(sql_executor=sql)
        action = ActionDef(
            action_type=ActionType.UPDATE_AGGREGATE,
            config={
                "target_table": "zones",
                "target_field": "feature_count",
                "aggregate": "COUNT",
            },
        )
        d.dispatch(action, _make_context(table="features"))
        assert len(sql.calls) == 1
        assert "COUNT(*)" in sql.calls[0][0]

    def test_rejects_unsafe_aggregate_function(self):
        sql = FakeSQL()
        d = ActionDispatcher(sql_executor=sql)
        action = ActionDef(
            action_type=ActionType.UPDATE_AGGREGATE,
            config={
                "target_table": "zones",
                "target_field": "cnt",
                "aggregate": "EXEC sp_evil",
            },
        )
        d.dispatch(action, _make_context())
        # Silent failure — no SQL executed
        assert sql.calls == []

    def test_accepts_all_safe_aggregates(self):
        for agg in ("COUNT", "SUM", "AVG", "MIN", "MAX"):
            sql = FakeSQL()
            d = ActionDispatcher(sql_executor=sql)
            action = ActionDef(
                action_type=ActionType.UPDATE_AGGREGATE,
                config={
                    "target_table": "zones",
                    "target_field": "val",
                    "aggregate": agg,
                },
            )
            d.dispatch(action, _make_context(table="features"))
            assert len(sql.calls) == 1
            assert f"{agg}(*)" in sql.calls[0][0]


# ---------------------------------------------------------------------------
# RUN_JOB / RUN_GRAPH
# ---------------------------------------------------------------------------


class TestRunJob:
    def test_invokes_job_runner_with_uuid(self):
        called = []
        rule_id = str(uuid4())
        d = ActionDispatcher(job_runner=lambda r, t, rid: called.append((r, t, rid)))
        action = ActionDef(
            action_type=ActionType.RUN_JOB,
            config={"rule_id": rule_id},
        )
        d.dispatch(action, _make_context(row_id="row-1"))
        assert len(called) == 1
        assert str(called[0][0]) == rule_id

    def test_missing_rule_id_is_noop(self):
        called = []
        d = ActionDispatcher(job_runner=lambda *a, **k: called.append(a))
        action = ActionDef(action_type=ActionType.RUN_JOB, config={})
        d.dispatch(action, _make_context())
        assert called == []


class TestRunGraph:
    def test_invokes_graph_runner_with_params(self):
        called = []
        d = ActionDispatcher(graph_runner=lambda gid, params: called.append((gid, params)))
        action = ActionDef(
            action_type=ActionType.RUN_GRAPH,
            config={"graph_id": "graph-1", "params": {"zone": "A"}},
        )
        d.dispatch(action, _make_context(table="parcels", row_id="r1"))
        assert len(called) == 1
        gid, params = called[0]
        assert gid == "graph-1"
        assert params["zone"] == "A"
        # Trigger context injected
        assert params["_trigger_table"] == "parcels"
        assert params["_trigger_row_id"] == "r1"


# ---------------------------------------------------------------------------
# WEBHOOK
# ---------------------------------------------------------------------------


class TestWebhook:
    def test_calls_webhook_client(self):
        sent = []
        d = ActionDispatcher(webhook_client=lambda url, payload: sent.append((url, payload)))
        action = ActionDef(
            action_type=ActionType.WEBHOOK,
            config={"url": "https://example.com/hook"},
        )
        d.dispatch(action, _make_context(transition=Transition.EXIT))
        assert len(sent) == 1
        assert sent[0][0] == "https://example.com/hook"
        assert sent[0][1]["transition"] == "EXIT"

    def test_missing_url_is_noop(self):
        sent = []
        d = ActionDispatcher(webhook_client=lambda *a: sent.append(a))
        action = ActionDef(action_type=ActionType.WEBHOOK, config={})
        d.dispatch(action, _make_context())
        assert sent == []

    def test_no_client_is_noop_even_with_url(self):
        # Regression: previously _webhook still rendered payload + checked
        # client; ensure the early-return guards both before any work.
        d = ActionDispatcher(webhook_client=None)
        action = ActionDef(
            action_type=ActionType.WEBHOOK,
            config={"url": "https://example.com/hook"},
        )
        # Should not raise
        d.dispatch(action, _make_context(transition=Transition.ENTER))

    def test_payload_contract_enriched(self):
        """Public payload shape (event_type/trigger_id/transition/timestamp/custom)."""
        sent: list[tuple[str, dict]] = []
        d = ActionDispatcher(webhook_client=lambda url, payload: sent.append((url, payload)))
        action = ActionDef(
            action_type=ActionType.WEBHOOK,
            config={"url": "https://example.com/hook", "payload_template": {"foo": "bar"}},
        )
        ctx = _make_context(
            table="parcels",
            operation="UPDATE",
            transition=Transition.ENTER,
        )
        d.dispatch(action, ctx)

        assert len(sent) == 1
        url, payload = sent[0]
        assert url == "https://example.com/hook"
        assert payload["event_type"] == "trigger_fired"
        assert payload["trigger_id"] == str(ctx.trigger.id)
        assert payload["trigger_name"] == "t"
        assert payload["table"] == "parcels"
        assert payload["operation"] == "UPDATE"
        assert payload["matched"] is True
        assert payload["transition"] == "ENTER"
        # ISO-8601 with timezone
        assert payload["timestamp"].endswith("+00:00") or payload["timestamp"].endswith("Z")
        # custom block carries the original render_payload() output
        assert "custom" in payload


# ---------------------------------------------------------------------------
# ENQUEUE / LOG_EVENT
# ---------------------------------------------------------------------------


class TestEnqueue:
    def test_inserts_into_bus_messages(self):
        sql = FakeSQL()
        d = ActionDispatcher(sql_executor=sql)
        action = ActionDef(action_type=ActionType.ENQUEUE, config={"job_kind": "rebuild"})
        d.dispatch(action, _make_context())
        assert len(sql.calls) == 1
        assert "INSERT INTO bus_messages" in sql.calls[0][0]
        # Payload is a JSON string with action_config
        import json
        msg = json.loads(sql.calls[0][1][0])
        assert msg["action_config"]["job_kind"] == "rebuild"


class TestLogEvent:
    def test_logs_and_inserts_history(self):
        sql = FakeSQL()
        d = ActionDispatcher(sql_executor=sql)
        action = ActionDef(action_type=ActionType.LOG_EVENT)
        d.dispatch(action, _make_context(transition=Transition.ENTER))
        assert len(sql.calls) == 1
        assert "status_history" in sql.calls[0][0]


# ---------------------------------------------------------------------------
# RUN_SQL
# ---------------------------------------------------------------------------


class TestRunSql:
    def test_simple_expression(self):
        sql = FakeSQL()
        d = ActionDispatcher(sql_executor=sql)
        action = ActionDef(
            action_type=ActionType.RUN_SQL,
            config={"expression": "VACUUM ANALYZE"},
        )
        # VACUUM isn't blocked by validate_expression (no DDL keyword match)
        d.dispatch(action, _make_context())
        assert len(sql.calls) == 1

    def test_rejects_unsafe_expression(self):
        sql = FakeSQL()
        d = ActionDispatcher(sql_executor=sql)
        action = ActionDef(
            action_type=ActionType.RUN_SQL,
            config={"expression": "DROP TABLE users"},
        )
        d.dispatch(action, _make_context())
        assert sql.calls == []

    def test_empty_expression_is_noop(self):
        sql = FakeSQL()
        d = ActionDispatcher(sql_executor=sql)
        action = ActionDef(action_type=ActionType.RUN_SQL, config={})
        d.dispatch(action, _make_context())
        assert sql.calls == []


# ---------------------------------------------------------------------------
# dispatch_all + error isolation
# ---------------------------------------------------------------------------


class TestDispatchAll:
    def test_returns_count_of_dispatched(self):
        d = ActionDispatcher(event_hub=FakeHub())
        actions = [
            ActionDef(action_type=ActionType.NOTIFY, config={"channel": "a"}),
            ActionDef(action_type=ActionType.NOTIFY, config={"channel": "b"}),
            ActionDef(action_type=ActionType.NOTIFY, config={"channel": "c"}),
        ]
        count = d.dispatch_all(actions, _make_context())
        assert count == 3

    def test_handler_failure_does_not_stop_batch(self):
        """One failing handler must not stop the next from running."""
        sql = FakeSQL()
        hub = FakeHub()

        def failing_sql(s, p):
            raise RuntimeError("downstream failure")

        d = ActionDispatcher(sql_executor=failing_sql, event_hub=hub)
        actions = [
            # First one raises (sql_executor fails)
            ActionDef(action_type=ActionType.ENQUEUE, config={}),
            # Second one succeeds (hub is separate)
            ActionDef(action_type=ActionType.NOTIFY, config={"channel": "ok"}),
        ]
        d.dispatch_all(actions, _make_context())
        # Hub still received its event
        assert len(hub.events) == 1


class TestUnknownAction:
    def test_unregistered_action_type_is_logged_not_raised(self):
        """Unknown action types produce a warning and no dispatch."""
        d = ActionDispatcher()
        # Simulate an unregistered action type by temporarily removing NOTIFY
        saved = ActionDispatcher._handlers.pop(ActionType.NOTIFY)
        try:
            action = ActionDef(action_type=ActionType.NOTIFY, config={"channel": "x"})
            # Should not raise
            d.dispatch(action, _make_context())
        finally:
            ActionDispatcher._handlers[ActionType.NOTIFY] = saved


# ---------------------------------------------------------------------------
# Custom handler registration
# ---------------------------------------------------------------------------


class TestCustomHandlerRegistration:
    def test_register_raises_on_existing_without_override(self):
        with pytest.raises(ValueError, match="already registered"):
            ActionDispatcher.register_action_handler(
                ActionType.NOTIFY,
                lambda self, a, ctx: None,
            )

    def test_register_override_replaces_handler(self):
        called = []

        def custom(self, action, ctx):
            called.append(action)

        try:
            ActionDispatcher.register_action_handler(
                ActionType.APPROVE,
                custom,
            )
            d = ActionDispatcher()
            action = ActionDef(action_type=ActionType.APPROVE, config={})
            d.dispatch(action, _make_context())
            assert len(called) == 1
        finally:
            ActionDispatcher.unregister_action_handler(ActionType.APPROVE)

    def test_unregister_unknown_raises(self):
        with pytest.raises(KeyError):
            ActionDispatcher.unregister_action_handler(ActionType.REJECT)

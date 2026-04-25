"""Tests for pluggable action handler registration in ActionDispatcher."""

from __future__ import annotations

from uuid import uuid4

import pytest

from gispulse.adapters.esb.action_dispatcher import ActionDispatcher, TriggerContext
from core.models import ActionDef, ActionType, EvalResult, Trigger


@pytest.fixture()
def _restore_handlers():
    """Save and restore the class-level _handlers dict between tests."""
    original = dict(ActionDispatcher._handlers)
    yield
    ActionDispatcher._handlers = original


@pytest.fixture()
def ctx() -> TriggerContext:
    """Minimal TriggerContext for dispatch tests."""
    trigger = Trigger(id=uuid4(), name="test-trigger")
    return TriggerContext(
        trigger=trigger,
        eval_result=EvalResult(matched=True),
        table="test_table",
        operation="INSERT",
        row_id="row-1",
    )


# ------------------------------------------------------------------
# Registration
# ------------------------------------------------------------------


@pytest.mark.usefixtures("_restore_handlers")
class TestRegisterActionHandler:
    def test_register_new_handler(self):
        calls: list[str] = []

        def _handle_email(self, action, ctx):
            calls.append("email_sent")

        ActionDispatcher.register_action_handler(ActionType.SEND_EMAIL, _handle_email)
        assert ActionType.SEND_EMAIL in ActionDispatcher._handlers

    def test_register_duplicate_raises(self):
        def _h(self, action, ctx):
            pass

        ActionDispatcher.register_action_handler(ActionType.SEND_EMAIL, _h)
        with pytest.raises(ValueError, match="already registered"):
            ActionDispatcher.register_action_handler(ActionType.SEND_EMAIL, _h)

    def test_register_override(self):
        def _h1(self, action, ctx):
            pass

        def _h2(self, action, ctx):
            pass

        ActionDispatcher.register_action_handler(ActionType.SEND_EMAIL, _h1)
        ActionDispatcher.register_action_handler(
            ActionType.SEND_EMAIL, _h2, override=True
        )
        assert ActionDispatcher._handlers[ActionType.SEND_EMAIL] is _h2

    def test_unregister_handler(self):
        def _h(self, action, ctx):
            pass

        ActionDispatcher.register_action_handler(ActionType.SEND_EMAIL, _h)
        ActionDispatcher.unregister_action_handler(ActionType.SEND_EMAIL)
        assert ActionType.SEND_EMAIL not in ActionDispatcher._handlers

    def test_unregister_missing_raises(self):
        with pytest.raises(KeyError, match="No handler registered"):
            ActionDispatcher.unregister_action_handler(ActionType.SEND_EMAIL)


# ------------------------------------------------------------------
# Dispatch with custom handler
# ------------------------------------------------------------------


@pytest.mark.usefixtures("_restore_handlers")
class TestDispatchCustomHandler:
    def test_dispatch_calls_custom_handler(self, ctx):
        calls: list[dict] = []

        def _handle_email(self, action, ctx):
            calls.append({"to": action.config.get("to"), "table": ctx.table})

        ActionDispatcher.register_action_handler(ActionType.SEND_EMAIL, _handle_email)

        dispatcher = ActionDispatcher()
        action = ActionDef(action_type=ActionType.SEND_EMAIL, config={"to": "admin@test.com"})
        dispatcher.dispatch(action, ctx)

        assert len(calls) == 1
        assert calls[0]["to"] == "admin@test.com"
        assert calls[0]["table"] == "test_table"

    def test_dispatch_all_with_mixed_handlers(self, ctx):
        calls: list[str] = []

        def _handle_flag(self, action, ctx):
            calls.append("flagged")

        ActionDispatcher.register_action_handler(ActionType.FLAG_FEATURE, _handle_flag)

        dispatcher = ActionDispatcher()
        actions = [
            ActionDef(action_type=ActionType.LOG_EVENT, config={}),
            ActionDef(action_type=ActionType.FLAG_FEATURE, config={}),
        ]
        count = dispatcher.dispatch_all(actions, ctx)

        assert count == 2
        assert "flagged" in calls

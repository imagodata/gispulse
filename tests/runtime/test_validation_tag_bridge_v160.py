"""Tests for v1.6.0 — mode=tag bridge from ValidationRunner to ActionDispatcher.

Coverage:
- ``mode: tag`` failure → ``ActionDispatcher.dispatch`` called with a
  ``TAG_FIELD`` action whose config matches the rule (column / value /
  message).
- ``mode: warn`` failure → dispatcher NOT called.
- ``action_dispatcher=None`` → graceful no-op (degrade to warn).
- ``rule.tag_field`` missing on a ``mode: tag`` rule → defence-in-depth
  no-op (the schema enforces this upstream).
- Dispatcher exception → logged + batch keeps moving.
- E2E: real ``ActionDispatcher`` over a sqlite ``_sql_executor`` mutates
  the row's status column on a failing rule.
"""

from __future__ import annotations

import sqlite3
from typing import Any
from unittest.mock import MagicMock

import pytest

from gispulse.runtime.validation_runner import (
    CompiledValidateRule,
    ValidationRunner,
)


def _compiled(
    *,
    id: str = "r",
    table: str = "parcels",
    rule_sql: str = "TRUE",
    mode: str = "tag",
    tag_field: str | None = "validation_status",
    message: str | None = "rule failed",
    pk_col: str = "id",
) -> CompiledValidateRule:
    return CompiledValidateRule(
        id=id,
        table=table,
        pk_col=pk_col,
        rule_sql=rule_sql,
        mode=mode,
        tag_field=tag_field,
        message=message,
    )


# ---------------------------------------------------------------------------
# Bridge unit tests
# ---------------------------------------------------------------------------


class TestTagDispatch:
    def test_tag_failure_calls_dispatcher(self) -> None:
        from gispulse.core.graph import ActionType

        evaluator = lambda sql, params: [(True,)]  # always fail
        dispatcher = MagicMock()
        runner = ValidationRunner(
            [_compiled(id="surface_min", message="too small")],
            evaluator,
            action_dispatcher=dispatcher,
        )
        failures = runner.evaluate("parcels", row_id=1)
        assert len(failures) == 1
        dispatcher.dispatch.assert_called_once()
        action, ctx = dispatcher.dispatch.call_args.args
        assert action.action_type == ActionType.TAG_FIELD
        assert action.config["column"] == "validation_status"
        assert action.config["value"] == "failed:surface_min"
        assert action.config["message"] == "too small"
        assert ctx.table == "parcels"
        assert ctx.row_id == "1"
        assert ctx.operation == "VALIDATE"

    def test_warn_failure_does_not_call_dispatcher(self) -> None:
        evaluator = lambda sql, params: [(True,)]
        dispatcher = MagicMock()
        runner = ValidationRunner(
            [_compiled(mode="warn", tag_field=None)],
            evaluator,
            action_dispatcher=dispatcher,
        )
        failures = runner.evaluate("parcels", row_id=1)
        assert len(failures) == 1
        dispatcher.dispatch.assert_not_called()

    def test_no_dispatcher_degrades_gracefully(self) -> None:
        """Without a dispatcher, mode=tag falls back to warn semantics."""
        evaluator = lambda sql, params: [(True,)]
        runner = ValidationRunner(
            [_compiled()],
            evaluator,
            action_dispatcher=None,
        )
        # No exception, failure is still returned + broadcast (if hub set)
        failures = runner.evaluate("parcels", row_id=1)
        assert len(failures) == 1

    def test_missing_tag_field_skips_dispatch(self) -> None:
        """Defence-in-depth: pydantic validates this, but the runner guards too."""
        evaluator = lambda sql, params: [(True,)]
        dispatcher = MagicMock()
        runner = ValidationRunner(
            [_compiled(tag_field=None)],  # mode=tag but no column
            evaluator,
            action_dispatcher=dispatcher,
        )
        runner.evaluate("parcels", row_id=1)
        dispatcher.dispatch.assert_not_called()

    def test_dispatcher_exception_does_not_abort_batch(self) -> None:
        evaluator = lambda sql, params: [(True,)]
        dispatcher = MagicMock()
        dispatcher.dispatch.side_effect = RuntimeError("dispatch boom")
        runner = ValidationRunner(
            [
                _compiled(id="rule_a"),
                _compiled(id="rule_b"),
            ],
            evaluator,
            action_dispatcher=dispatcher,
        )
        # Both rules fail; both attempt to dispatch; both raise; runner
        # still returns 2 failures and the batch keeps moving.
        failures = runner.evaluate("parcels", row_id=1)
        assert len(failures) == 2
        assert dispatcher.dispatch.call_count == 2

    def test_synthetic_trigger_name_includes_rule_id(self) -> None:
        evaluator = lambda sql, params: [(True,)]
        dispatcher = MagicMock()
        runner = ValidationRunner(
            [_compiled(id="shape_valid")],
            evaluator,
            action_dispatcher=dispatcher,
        )
        runner.evaluate("parcels", row_id=42)
        _, ctx = dispatcher.dispatch.call_args.args
        assert ctx.trigger.name == "validate:shape_valid"


# ---------------------------------------------------------------------------
# E2E with real ActionDispatcher + sqlite
# ---------------------------------------------------------------------------


class _SqlSpy:
    """Wraps a sqlite3 connection and translates %s placeholders to ?."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.calls: list[tuple[str, list[Any]]] = []

    def __call__(self, sql: str, params: list[Any] | None = None) -> Any:
        params = list(params or [])
        self.calls.append((sql, list(params)))
        translated = sql.replace("%s", "?")
        cur = self.conn.execute(translated, params)
        if translated.strip().upper().startswith(("SELECT", "PRAGMA")):
            return cur.fetchall()
        self.conn.commit()
        return None


@pytest.fixture
def sqlite_conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute(
        'CREATE TABLE "parcels" '
        '(id INTEGER PRIMARY KEY, label TEXT, _gispulse_origin TEXT)'
    )
    c.execute('INSERT INTO "parcels" (id, label) VALUES (1, "alpha")')
    c.commit()
    yield c
    c.close()


class TestE2EWithRealDispatcher:
    def test_tag_failure_writes_status_column(self, sqlite_conn) -> None:
        """End-to-end: ValidationRunner failure → real ActionDispatcher → row tagged."""
        from gispulse.adapters.esb.action_dispatcher import ActionDispatcher

        spy = _SqlSpy(sqlite_conn)
        dispatcher = ActionDispatcher(sql_executor=spy)

        evaluator = lambda sql, params: [(True,)]  # rule fails for any row
        runner = ValidationRunner(
            [
                _compiled(
                    id="surface_min",
                    message="surface < 50 m²",
                    tag_field="validation_status",
                )
            ],
            evaluator,
            action_dispatcher=dispatcher,
        )

        failures = runner.evaluate("parcels", row_id=1)
        assert len(failures) == 1

        # The auto-create handler in ActionDispatcher (#123) added the
        # column, then the UPDATE wrote the status.
        cols = {
            row[1]
            for row in sqlite_conn.execute(
                'PRAGMA table_info("parcels")'
            ).fetchall()
        }
        assert "validation_status" in cols
        row = sqlite_conn.execute(
            'SELECT validation_status FROM "parcels" WHERE id = 1'
        ).fetchone()
        assert row[0] == "failed:surface_min"

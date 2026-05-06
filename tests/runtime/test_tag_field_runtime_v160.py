"""Tests for v1.6.0 #123 runtime — tag_field action handler with auto-create.

Coverage:
- ``ActionType.TAG_FIELD`` registered in :class:`ActionDispatcher._handlers`.
- Auto-create: missing target column ⇒ ``ALTER TABLE ADD COLUMN``.
- Idempotent: second invocation reuses the cached column set, no extra
  ``PRAGMA`` or ``ALTER`` calls.
- Multi-column write: ``message_column`` populated when set.
- Origin tagging: ``_gispulse_origin`` is set during the UPDATE so the
  AFTER UPDATE trigger refire is skipped (B-02 contract).
- ``config_loader.to_triggers`` maps a YAML ``tag_field`` action to an
  ``ActionDef`` with the right config dict.
"""

from __future__ import annotations

import sqlite3
from typing import Any
from uuid import UUID, uuid4

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _SqlSpy:
    """Wraps a sqlite3 connection and records every call."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.calls: list[tuple[str, list[Any]]] = []

    def __call__(self, sql: str, params: list[Any] | None = None) -> Any:
        params = list(params or [])
        self.calls.append((sql, list(params)))
        # Translate %s placeholders to sqlite ? for the spy's pass-through
        translated = sql.replace("%s", "?")
        cur = self.conn.execute(translated, params)
        if translated.strip().upper().startswith(("SELECT", "PRAGMA")):
            rows = cur.fetchall()
            return rows
        self.conn.commit()
        return None


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute(
        'CREATE TABLE "parcels" (id INTEGER PRIMARY KEY, label TEXT, _gispulse_origin TEXT)'
    )
    c.execute('INSERT INTO "parcels" (id, label) VALUES (1, "alpha"), (2, "beta")')
    c.commit()
    yield c
    c.close()


@pytest.fixture
def dispatcher_with_spy(conn):
    from gispulse.adapters.esb.action_dispatcher import ActionDispatcher

    spy = _SqlSpy(conn)
    return ActionDispatcher(sql_executor=spy), spy


def _make_ctx(table: str = "parcels", row_id: int = 1):
    from gispulse.core.dispatcher import TriggerContext
    from core.models import Trigger

    trigger = Trigger(name="t-validation")
    return TriggerContext(
        trigger=trigger,
        table=table,
        row_id=row_id,
        operation="UPDATE",
        eval_result=None,
    )


def _action(**cfg):
    from core.graph import ActionDef, ActionType

    return ActionDef(action_type=ActionType.TAG_FIELD, config=cfg)


# ---------------------------------------------------------------------------
# Registry & dispatch
# ---------------------------------------------------------------------------


class TestActionTypeRegistry:
    def test_tag_field_in_handlers(self) -> None:
        from gispulse.adapters.esb.action_dispatcher import ActionDispatcher
        from core.graph import ActionType

        assert ActionType.TAG_FIELD in ActionDispatcher._handlers

    def test_tag_field_action_type_value(self) -> None:
        from core.graph import ActionType

        assert ActionType.TAG_FIELD.value == "tag_field"


# ---------------------------------------------------------------------------
# Auto-create + write
# ---------------------------------------------------------------------------


class TestTagFieldAutoCreate:
    def test_creates_missing_column_and_writes(self, dispatcher_with_spy, conn) -> None:
        dispatcher, spy = dispatcher_with_spy
        action = _action(column="validation_status", value="failed:surface_min")
        ctx = _make_ctx(row_id=1)

        dispatcher._tag_field(action, ctx)

        # Column was added
        cols = {row[1] for row in conn.execute('PRAGMA table_info("parcels")').fetchall()}
        assert "validation_status" in cols
        # Row was tagged
        row = conn.execute(
            'SELECT validation_status FROM "parcels" WHERE id = 1'
        ).fetchone()
        assert row[0] == "failed:surface_min"

    def test_message_column_also_created_and_written(
        self, dispatcher_with_spy, conn
    ) -> None:
        dispatcher, spy = dispatcher_with_spy
        action = _action(
            column="validation_status",
            value="failed:shape_valid",
            message_column="validation_message",
            message="Geometry self-intersects",
        )
        ctx = _make_ctx(row_id=2)

        dispatcher._tag_field(action, ctx)

        cols = {row[1] for row in conn.execute('PRAGMA table_info("parcels")').fetchall()}
        assert {"validation_status", "validation_message"} <= cols
        row = conn.execute(
            'SELECT validation_status, validation_message FROM "parcels" WHERE id = 2'
        ).fetchone()
        assert row[0] == "failed:shape_valid"
        assert row[1] == "Geometry self-intersects"

    def test_idempotent_second_call_skips_pragma(
        self, dispatcher_with_spy, conn
    ) -> None:
        dispatcher, spy = dispatcher_with_spy
        action = _action(column="validation_status", value="ok")

        dispatcher._tag_field(action, _make_ctx(row_id=1))
        n_calls_after_first = len(spy.calls)
        n_pragma_after_first = sum(1 for s, _ in spy.calls if "PRAGMA" in s)

        dispatcher._tag_field(action, _make_ctx(row_id=2))
        n_pragma_after_second = sum(1 for s, _ in spy.calls if "PRAGMA" in s)

        # The second invocation should not run PRAGMA again — it hits the cache
        assert n_pragma_after_second == n_pragma_after_first
        # ... and still UPDATEs row 2
        row = conn.execute(
            'SELECT validation_status FROM "parcels" WHERE id = 2'
        ).fetchone()
        assert row[0] == "ok"

    def test_existing_column_is_not_recreated(
        self, dispatcher_with_spy, conn
    ) -> None:
        dispatcher, spy = dispatcher_with_spy
        # Pre-create the column manually
        conn.execute('ALTER TABLE "parcels" ADD COLUMN validation_status TEXT')
        conn.commit()
        action = _action(column="validation_status", value="ok")

        dispatcher._tag_field(action, _make_ctx(row_id=1))

        # No ALTER call should have been issued
        alter_calls = [s for s, _ in spy.calls if "ALTER TABLE" in s]
        assert alter_calls == []

    def test_origin_tag_written_for_loop_guard(
        self, dispatcher_with_spy, conn
    ) -> None:
        """B-02 origin-tagging: tag_field must mark + clear ``_gispulse_origin``."""
        dispatcher, spy = dispatcher_with_spy
        action = _action(column="validation_status", value="ok")
        dispatcher._tag_field(action, _make_ctx(row_id=1))

        # Two UPDATE calls: one with origin set, one clearing it back to NULL.
        update_calls = [s for s, _ in spy.calls if s.startswith('UPDATE "parcels"')]
        assert len(update_calls) == 2
        assert "_gispulse_origin" in update_calls[0]
        assert "_gispulse_origin" in update_calls[1]


# ---------------------------------------------------------------------------
# config_loader → ActionDef
# ---------------------------------------------------------------------------


class TestToTriggersTagField:
    def test_tag_field_action_mapped(self, tmp_path) -> None:
        from gispulse.runtime.config_loader import (
            ActionConfigModel,
            GISPulseConfig,
            TriggerConfigModel,
            to_triggers,
        )
        from core.graph import ActionType

        gpkg = tmp_path / "x.gpkg"
        gpkg.write_bytes(b"")
        cfg = GISPulseConfig(
            version=1,
            gpkg=str(gpkg),
            triggers=[
                TriggerConfigModel(
                    name="t",
                    table="parcels",
                    when=["INSERT"],
                    actions=[
                        ActionConfigModel(
                            type="tag_field",
                            column="validation_status",
                            value="failed:surface_min",
                            message_column="validation_message",
                            message="Surface < 50 m²",
                        )
                    ],
                )
            ],
        )
        trgs = to_triggers(cfg)
        assert len(trgs[0].actions) == 1
        a = trgs[0].actions[0]
        assert a.action_type == ActionType.TAG_FIELD
        assert a.config["column"] == "validation_status"
        assert a.config["value"] == "failed:surface_min"
        assert a.config["message_column"] == "validation_message"
        assert a.config["message"] == "Surface < 50 m²"

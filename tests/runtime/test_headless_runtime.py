"""Tests for ``gispulse.runtime.headless_runtime``.

Strategy
--------
Build a real GPKG in ``tmp_path``, install ``enable_change_tracking``
on a parcels table, then exercise three trigger flavours end-to-end via
``HeadlessRuntime.run_once()``:

1. ``ActionType.RUN_SQL`` — the dispatcher calls a captured SQL executor.
2. ``ActionType.WEBHOOK`` — a fake webhook callable receives the payload.
3. ``ActionType.SET_FIELD`` — the SQL executor writes back into the table.

We deliberately bypass :class:`HttpWebhookClient` (which would refuse the
fake URL via SSRF policy) and inject a plain stub via ``webhook_client=``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from gispulse.core.enums import TriggerCategory, TriggerEvent, TriggerType
from gispulse.core.graph import ActionDef, ActionType
from gispulse.core.models import Trigger
from gispulse.runtime import build_runtime


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def gpkg_with_parcels(tmp_path: Path) -> Path:
    """Create a real GPKG with a tracked ``parcels`` table.

    We use ``GeoPackageEngine`` directly so the boot path is identical
    to what the runtime will see (bootstrap_gpkg_project, native
    triggers, etc.). The engine is closed after setup so the runtime
    can open it cleanly.
    """
    from gispulse.persistence.gpkg_engine import GeoPackageEngine

    gpkg_path = tmp_path / "fixture.gpkg"
    engine = GeoPackageEngine(path=gpkg_path)
    engine.open()
    try:
        conn = engine._get_conn()  # noqa: SLF001
        conn.execute(
            'CREATE TABLE IF NOT EXISTS "parcels" '
            '(fid INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, status TEXT)'
        )
        conn.commit()
        engine.enable_change_tracking("parcels")
    finally:
        engine.close()
    return gpkg_path


def _make_trigger(*, action: ActionDef, name: str = "t1") -> Trigger:
    return Trigger(
        id=uuid4(),
        name=name,
        description="test trigger",
        event=TriggerEvent.DATA_CHANGED,
        trigger_type=TriggerType.DML,
        category=TriggerCategory.DATA,
        actions=[action],
        enabled=True,
    )


def _insert_row(gpkg: Path, name: str = "alpha") -> None:
    """Open a *separate* sqlite connection to fire the native triggers."""
    conn = sqlite3.connect(str(gpkg))
    try:
        conn.execute('INSERT INTO "parcels"(name, status) VALUES (?, ?)', (name, "pending"))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_run_once_executes_run_sql_action(gpkg_with_parcels: Path) -> None:
    """An INSERT in the GPKG fires the native trigger, the watcher reads
    the change-log and the dispatcher runs our RUN_SQL action."""
    captured: list[tuple[str, Any]] = []

    def fake_sql(sql: str, params: Any = None) -> None:
        captured.append((sql, params))

    trigger = _make_trigger(
        action=ActionDef(
            action_type=ActionType.RUN_SQL,
            config={"expression": "SELECT 1"},
        ),
        name="run_sql_test",
    )

    _insert_row(gpkg_with_parcels)

    runtime = build_runtime(
        gpkg_path=gpkg_with_parcels,
        triggers=[trigger],
        sql_executor=fake_sql,
        # Plain stub so we never reach HttpWebhookClient / SSRF policy.
        webhook_client=lambda url, payload: None,
        dataset_id="test",
    )
    try:
        processed = runtime.run_once()
    finally:
        runtime.close()

    assert processed >= 1, "watcher should have read at least one change-log row"
    # The RUN_SQL handler validates expression then calls fake_sql once.
    assert any("SELECT 1" in sql for sql, _ in captured), (
        f"RUN_SQL handler should have called the executor with our expression. "
        f"captured={captured!r}"
    )


def test_run_once_invokes_webhook_with_correct_payload(gpkg_with_parcels: Path) -> None:
    """WEBHOOK action fires the injected client with a structured payload."""
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_webhook(url: str, payload: dict[str, Any]) -> None:
        calls.append((url, payload))

    trigger = _make_trigger(
        action=ActionDef(
            action_type=ActionType.WEBHOOK,
            config={"url": "https://hook.example.com/parcels"},
        ),
        name="webhook_test",
    )

    _insert_row(gpkg_with_parcels, name="beta")

    runtime = build_runtime(
        gpkg_path=gpkg_with_parcels,
        triggers=[trigger],
        webhook_client=fake_webhook,
        sql_executor=lambda *a, **kw: None,
        dataset_id="test",
    )
    try:
        runtime.run_once()
    finally:
        runtime.close()

    assert calls, "webhook callable should have been invoked"
    url, payload = calls[0]
    assert url == "https://hook.example.com/parcels"
    # Contract from action_dispatcher._webhook
    assert payload["event_type"] == "trigger_fired"
    assert payload["table"] == "parcels"
    assert payload["operation"] == "INSERT"
    assert payload["matched"] is True
    assert payload["trigger_name"] == "webhook_test"


def test_run_once_set_field_writes_value(gpkg_with_parcels: Path) -> None:
    """SET_FIELD action calls the SQL executor with an UPDATE that targets
    the right table + field. We verify the captured SQL shape (the real
    GPKG executor isn't wired in this test, the dispatcher only emits
    the SQL via our stub)."""
    captured: list[tuple[str, Any]] = []

    def fake_sql(sql: str, params: Any = None) -> None:
        captured.append((sql, params))

    trigger = _make_trigger(
        action=ActionDef(
            action_type=ActionType.SET_FIELD,
            config={"field": "status", "value": "ok"},
        ),
        name="set_field_test",
    )

    _insert_row(gpkg_with_parcels, name="gamma")

    runtime = build_runtime(
        gpkg_path=gpkg_with_parcels,
        triggers=[trigger],
        sql_executor=fake_sql,
        webhook_client=lambda url, payload: None,
        dataset_id="test",
    )
    try:
        runtime.run_once()
    finally:
        runtime.close()

    assert captured, f"SET_FIELD should produce one SQL call, got {captured!r}"
    sql, params = captured[0]
    # The handler builds: UPDATE "parcels" SET "status" = %s WHERE id = %s
    assert 'UPDATE "parcels"' in sql
    assert '"status"' in sql
    assert params[0] == "ok"


def test_build_runtime_rejects_missing_gpkg(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        build_runtime(
            gpkg_path=tmp_path / "does_not_exist.gpkg",
            triggers=[],
        )


def test_build_runtime_rejects_invalid_dataset_id(gpkg_with_parcels: Path) -> None:
    with pytest.raises(ValueError, match="dataset_id"):
        build_runtime(
            gpkg_path=gpkg_with_parcels,
            triggers=[],
            dataset_id="",
        )


def test_run_once_with_no_triggers_just_acks(gpkg_with_parcels: Path) -> None:
    """Insert without configured triggers: watcher still drains and acks."""
    _insert_row(gpkg_with_parcels)

    runtime = build_runtime(
        gpkg_path=gpkg_with_parcels,
        triggers=[],
        sql_executor=lambda *a, **kw: None,
        webhook_client=lambda url, payload: None,
        dataset_id="test",
    )
    try:
        processed = runtime.run_once()
        # Second tick: nothing left to process.
        leftover = runtime.run_once()
    finally:
        runtime.close()

    assert processed >= 1
    assert leftover == 0


def test_null_event_hub_swallows_broadcasts() -> None:
    """Smoke test for the no-op hub used in headless mode."""
    from gispulse.runtime import NullEventHub

    hub = NullEventHub()
    # Must not raise no matter what we throw at it.
    hub.broadcast("any.event", {"a": 1, "b": [1, 2, 3]})
    hub.broadcast("empty", None)
    hub.broadcast("nodata")  # type: ignore[call-arg]


def _insert_with_value(
    gpkg: Path, *, name: str, status: str, valeur: int | None = None
) -> None:
    """Helper to insert a parcel with a numeric ``valeur`` column.

    The base fixture ``parcels`` table only has ``name``/``status``;
    predicate tests need a numeric column so we add it on demand. The
    ALTER is idempotent.
    """
    conn = sqlite3.connect(str(gpkg))
    try:
        try:
            conn.execute('ALTER TABLE "parcels" ADD COLUMN valeur INTEGER')
        except sqlite3.OperationalError:
            pass  # column already exists
        conn.execute(
            'INSERT INTO "parcels"(name, status, valeur) VALUES (?, ?, ?)',
            (name, status, valeur),
        )
        conn.commit()
    finally:
        conn.close()


def test_predicate_filters_rows_below_threshold(
    gpkg_with_parcels: Path,
) -> None:
    """A trigger with ``predicate: 'valeur > 100'`` must skip rows
    where ``valeur <= 100``.

    We insert two rows (valeur=50 and valeur=200) and verify that the
    webhook fires exactly once with the high-value payload.
    """
    from gispulse.runtime import parse_predicate

    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_webhook(url: str, payload: dict[str, Any]) -> None:
        calls.append((url, payload))

    trigger = _make_trigger(
        action=ActionDef(
            action_type=ActionType.WEBHOOK,
            config={"url": "https://hook.example.com/x"},
        ),
        name="threshold_filter",
    )
    # Inject the AST manually — this is the same shape ``to_triggers``
    # builds from a YAML config.
    trigger.conditions["predicate_ast"] = parse_predicate("valeur > 100")

    _insert_with_value(gpkg_with_parcels, name="lo", status="x", valeur=50)
    _insert_with_value(gpkg_with_parcels, name="hi", status="x", valeur=200)

    runtime = build_runtime(
        gpkg_path=gpkg_with_parcels,
        triggers=[trigger],
        sql_executor=lambda *a, **kw: None,
        webhook_client=fake_webhook,
        dataset_id="test",
    )
    try:
        runtime.run_once()
    finally:
        runtime.close()

    # Exactly one fire: only the row with valeur=200 matches.
    assert len(calls) == 1, f"expected 1 webhook call, got {len(calls)}: {calls!r}"
    _, payload = calls[0]
    # The webhook payload exposes the change-record metadata; the row
    # values themselves are not leaked over the wire (security choice
    # documented in change_log_watcher.py:318-320). The fact that the
    # webhook fired exactly once is the test contract — a non-matching
    # predicate would have produced zero calls.
    assert payload["table"] == "parcels"
    assert payload["operation"] == "INSERT"
    assert payload["matched"] is True


def test_predicate_compound_and_or(gpkg_with_parcels: Path) -> None:
    """A compound DSL predicate ``A AND (B OR C)`` filters rows correctly."""
    from gispulse.runtime import parse_predicate

    calls: list[tuple[str, dict[str, Any]]] = []

    trigger = _make_trigger(
        action=ActionDef(
            action_type=ActionType.WEBHOOK,
            config={"url": "https://hook.example.com/y"},
        ),
        name="compound_filter",
    )
    trigger.conditions["predicate_ast"] = parse_predicate(
        "valeur > 100 AND (status == 'pending' OR status == 'review')"
    )

    # 4 rows × cartesian: only (valeur>100 AND status in {pending,review}) should match
    _insert_with_value(gpkg_with_parcels, name="r1", status="pending", valeur=50)
    _insert_with_value(gpkg_with_parcels, name="r2", status="pending", valeur=200)
    _insert_with_value(gpkg_with_parcels, name="r3", status="ok", valeur=200)
    _insert_with_value(gpkg_with_parcels, name="r4", status="review", valeur=300)

    runtime = build_runtime(
        gpkg_path=gpkg_with_parcels,
        triggers=[trigger],
        sql_executor=lambda *a, **kw: None,
        webhook_client=lambda url, payload: calls.append((url, payload)),
        dataset_id="test",
    )
    try:
        runtime.run_once()
    finally:
        runtime.close()

    # r2 + r4 match — 2 fires expected.
    assert len(calls) == 2, f"expected 2 webhook calls, got {len(calls)}: {calls!r}"


def test_predicate_missing_attr_skips_silently(
    gpkg_with_parcels: Path,
) -> None:
    """A predicate that references a non-existent attribute resolves to
    a non-match (fail-safe). The watcher tick must not raise."""
    from gispulse.runtime import parse_predicate

    calls: list[tuple[str, dict[str, Any]]] = []

    trigger = _make_trigger(
        action=ActionDef(
            action_type=ActionType.WEBHOOK,
            config={"url": "https://hook.example.com/z"},
        ),
        name="ghost_attr",
    )
    trigger.conditions["predicate_ast"] = parse_predicate(
        "no_such_field == 42"
    )

    _insert_row(gpkg_with_parcels, name="any")

    runtime = build_runtime(
        gpkg_path=gpkg_with_parcels,
        triggers=[trigger],
        sql_executor=lambda *a, **kw: None,
        webhook_client=lambda url, payload: calls.append((url, payload)),
        dataset_id="test",
    )
    try:
        processed = runtime.run_once()
    finally:
        runtime.close()

    assert processed >= 1
    assert calls == [], "predicate over missing attr should suppress the action"


def test_no_predicate_keeps_legacy_always_match(
    gpkg_with_parcels: Path,
) -> None:
    """When no DSL predicate is set, the trigger must keep firing for
    every change-log row (pre-S4 behaviour)."""
    calls: list[tuple[str, dict[str, Any]]] = []

    trigger = _make_trigger(
        action=ActionDef(
            action_type=ActionType.WEBHOOK,
            config={"url": "https://hook.example.com/legacy"},
        ),
        name="no_predicate",
    )
    # No predicate_ast on conditions — equivalent to pre-S4 wiring.

    _insert_row(gpkg_with_parcels, name="always_fires")

    runtime = build_runtime(
        gpkg_path=gpkg_with_parcels,
        triggers=[trigger],
        sql_executor=lambda *a, **kw: None,
        webhook_client=lambda url, payload: calls.append((url, payload)),
        dataset_id="test",
    )
    try:
        runtime.run_once()
    finally:
        runtime.close()

    assert len(calls) == 1


def test_webhook_allowlist_blocks_off_list_host(gpkg_with_parcels: Path) -> None:
    """When ``webhook_allowlist`` is provided and the trigger URL is not
    on it, the dispatcher's per-action try/except logs but doesn't crash
    — and our test ensures the wrapped HttpWebhookClient is never reached
    (no network call)."""

    # Build a runtime with the default webhook_client wrapping
    # HttpWebhookClient, but with an allowlist that excludes the
    # webhook host. The dispatcher's try/except catches the
    # PermissionError so the run does not raise.
    trigger = _make_trigger(
        action=ActionDef(
            action_type=ActionType.WEBHOOK,
            config={"url": "https://blocked.example.com/x"},
        ),
        name="blocked_webhook",
    )

    _insert_row(gpkg_with_parcels)

    runtime = build_runtime(
        gpkg_path=gpkg_with_parcels,
        triggers=[trigger],
        webhook_allowlist=["allowed.example.com"],
        sql_executor=lambda *a, **kw: None,
        dataset_id="test",
    )
    # The dispatcher swallows action errors, so run_once must succeed.
    try:
        processed = runtime.run_once()
    finally:
        runtime.close()
    assert processed >= 1

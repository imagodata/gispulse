"""Tests for adapters.esb.state_store — spatial transition tracking.

StateStore keeps track of per-(object, predicate) ENTER/EXIT/DWELL
transitions so the system doesn't re-fire on every DML. Bugs silently
skip valid transitions or re-fire on steady state.

Covers StateStore.compute_transition (shared logic) and InMemoryStateStore
(dict backend). PostgresStateStore exercised lightly since it requires
a real engine.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from gispulse.adapters.esb.state_store import (
    InMemoryStateStore,
    PostgresStateStore,
    StateStore,
)
from core.models import ObjectState, SpatialState, Transition


# ---------------------------------------------------------------------------
# StateStore.compute_transition — shared pure logic
# ---------------------------------------------------------------------------


class TestComputeTransition:
    def test_unknown_to_inside_enter(self):
        new_state, transition = StateStore.compute_transition(
            SpatialState.UNKNOWN, current_match=True
        )
        assert new_state == SpatialState.INSIDE
        assert transition == Transition.ENTER

    def test_unknown_to_outside_no_transition(self):
        new_state, transition = StateStore.compute_transition(
            SpatialState.UNKNOWN, current_match=False
        )
        assert new_state == SpatialState.OUTSIDE
        assert transition is None

    def test_outside_to_inside_enter(self):
        new_state, transition = StateStore.compute_transition(
            SpatialState.OUTSIDE, current_match=True
        )
        assert new_state == SpatialState.INSIDE
        assert transition == Transition.ENTER

    def test_inside_to_outside_exit(self):
        new_state, transition = StateStore.compute_transition(
            SpatialState.INSIDE, current_match=False
        )
        assert new_state == SpatialState.OUTSIDE
        assert transition == Transition.EXIT

    def test_inside_to_inside_no_transition(self):
        """Steady state must not re-fire."""
        new_state, transition = StateStore.compute_transition(
            SpatialState.INSIDE, current_match=True
        )
        assert new_state == SpatialState.INSIDE
        assert transition is None

    def test_outside_to_outside_no_transition(self):
        new_state, transition = StateStore.compute_transition(
            SpatialState.OUTSIDE, current_match=False
        )
        assert new_state == SpatialState.OUTSIDE
        assert transition is None


# ---------------------------------------------------------------------------
# InMemoryStateStore
# ---------------------------------------------------------------------------


class TestInMemoryGet:
    def test_unknown_object_returns_fresh_object_state(self):
        store = InMemoryStateStore()
        obj_id = uuid4()
        pred_id = uuid4()
        state = store.get_state(obj_id, pred_id)
        assert isinstance(state, ObjectState)
        assert state.object_id == obj_id
        assert state.predicate_id == pred_id
        assert state.state == SpatialState.UNKNOWN

    def test_get_same_key_returns_same_instance(self):
        store = InMemoryStateStore()
        obj_id, pred_id = uuid4(), uuid4()
        a = store.get_state(obj_id, pred_id)
        b = store.get_state(obj_id, pred_id)
        assert a is b  # cached

    def test_size_grows_with_new_pairs(self):
        store = InMemoryStateStore()
        assert store.size == 0
        store.get_state(uuid4(), uuid4())
        assert store.size == 1
        store.get_state(uuid4(), uuid4())
        assert store.size == 2


class TestInMemoryUpdate:
    def test_first_update_to_inside_emits_enter(self):
        store = InMemoryStateStore()
        obj_id, pred_id = uuid4(), uuid4()
        transition = store.update_state(
            obj_id, pred_id, new_spatial=SpatialState.INSIDE
        )
        assert transition == Transition.ENTER
        obj = store.get_state(obj_id, pred_id)
        assert obj.state == SpatialState.INSIDE
        assert obj.entered_at is not None

    def test_first_update_to_outside_emits_no_transition(self):
        store = InMemoryStateStore()
        transition = store.update_state(
            uuid4(), uuid4(), new_spatial=SpatialState.OUTSIDE
        )
        assert transition is None

    def test_transition_inside_to_outside_emits_exit_and_clears_entered_at(self):
        store = InMemoryStateStore()
        obj_id, pred_id = uuid4(), uuid4()
        store.update_state(obj_id, pred_id, new_spatial=SpatialState.INSIDE)

        transition = store.update_state(
            obj_id, pred_id, new_spatial=SpatialState.OUTSIDE
        )
        assert transition == Transition.EXIT
        obj = store.get_state(obj_id, pred_id)
        assert obj.state == SpatialState.OUTSIDE
        assert obj.entered_at is None  # cleared on EXIT

    def test_steady_state_inside_emits_no_transition(self):
        store = InMemoryStateStore()
        obj_id, pred_id = uuid4(), uuid4()
        store.update_state(obj_id, pred_id, new_spatial=SpatialState.INSIDE)
        transition = store.update_state(
            obj_id, pred_id, new_spatial=SpatialState.INSIDE
        )
        assert transition is None

    def test_zone_id_roundtrips(self):
        store = InMemoryStateStore()
        obj_id, pred_id = uuid4(), uuid4()
        zone = uuid4()
        store.update_state(
            obj_id, pred_id, new_spatial=SpatialState.INSIDE, zone_id=zone
        )
        obj = store.get_state(obj_id, pred_id)
        assert obj.zone_id == zone

    def test_last_evaluated_updated(self):
        store = InMemoryStateStore()
        obj_id, pred_id = uuid4(), uuid4()
        before = datetime.now(timezone.utc)
        store.update_state(obj_id, pred_id, new_spatial=SpatialState.INSIDE)
        obj = store.get_state(obj_id, pred_id)
        assert obj.last_evaluated is not None
        assert obj.last_evaluated >= before


class TestInMemoryCleanup:
    def test_cleanup_removes_all_entries_for_predicate(self):
        store = InMemoryStateStore()
        pred_target = uuid4()
        pred_other = uuid4()

        store.update_state(uuid4(), pred_target, SpatialState.INSIDE)
        store.update_state(uuid4(), pred_target, SpatialState.INSIDE)
        store.update_state(uuid4(), pred_target, SpatialState.INSIDE)
        store.update_state(uuid4(), pred_other, SpatialState.INSIDE)

        assert store.size == 4
        removed = store.cleanup_predicate(pred_target)
        assert removed == 3
        assert store.size == 1  # only pred_other left

    def test_cleanup_unknown_predicate_returns_zero(self):
        store = InMemoryStateStore()
        store.update_state(uuid4(), uuid4(), SpatialState.INSIDE)
        assert store.cleanup_predicate(uuid4()) == 0
        assert store.size == 1  # untouched


# ---------------------------------------------------------------------------
# PostgresStateStore — smoke tests with a fake engine
# ---------------------------------------------------------------------------


class FakeEngine:
    """Engine stub that records SQL calls and returns canned rows."""

    def __init__(self, rows_queue: list[list[dict]] | None = None):
        self.calls: list[tuple] = []
        self._rows = rows_queue or []

    def execute_sql(self, sql: str, params=None):
        self.calls.append((sql, params))
        if self._rows:
            return self._rows.pop(0)
        return []


class TestPostgresStateStoreInit:
    def test_init_creates_table(self):
        engine = FakeEngine()
        store = PostgresStateStore(engine)
        # _ensure_table called → at least one CREATE TABLE executed
        assert any("CREATE TABLE" in call[0] for call in engine.calls)

    def test_ddl_includes_primary_key(self):
        engine = FakeEngine()
        PostgresStateStore(engine)
        ddl = engine.calls[0][0]
        assert "PRIMARY KEY" in ddl
        assert "object_id" in ddl
        assert "predicate_id" in ddl

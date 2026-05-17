"""Tests for the SOURCE_CHANGED trigger type (issue #186)."""

from __future__ import annotations

from uuid import uuid4

from gispulse.core.models import ChangeRecord, Trigger, TriggerEvent, TriggerType
from gispulse.rules.trigger_evaluator import TriggerEvaluator


def _record(new_values: dict) -> ChangeRecord:
    return ChangeRecord(
        table_name="_external_source",
        operation="INSERT",
        new_values=new_values,
        feature_id="src",
    )


def _trigger(conditions: dict) -> Trigger:
    return Trigger(
        id=uuid4(),
        name="src-watch",
        event=TriggerEvent.MANUAL,
        trigger_type=TriggerType.SOURCE_CHANGED,
        conditions=conditions,
        enabled=True,
    )


def _fires(new_values: dict, conditions: dict) -> bool:
    fired = TriggerEvaluator().evaluate(_record(new_values), [_trigger(conditions)])
    return fired[0].matched


# --------------------------------------------------------------------------
# The enum value exists and dispatches
# --------------------------------------------------------------------------


def test_trigger_type_value() -> None:
    assert TriggerType.SOURCE_CHANGED.value == "source_changed"


# --------------------------------------------------------------------------
# Revision comparison
# --------------------------------------------------------------------------


def test_fires_when_revision_changed() -> None:
    assert _fires(
        {"source": "cadastre://parcelles", "revision": "2026-02"},
        {"source": "cadastre://parcelles", "last_revision": "2026-01"},
    )


def test_no_fire_when_revision_unchanged() -> None:
    assert not _fires(
        {"source": "cadastre://parcelles", "revision": "2026-01"},
        {"source": "cadastre://parcelles", "last_revision": "2026-01"},
    )


def test_fires_on_first_observation_without_last_revision() -> None:
    # No last_revision recorded yet — the first poll always fires.
    assert _fires(
        {"source": "cadastre://parcelles", "revision": "2026-01"},
        {"source": "cadastre://parcelles"},
    )


def test_no_fire_when_event_has_no_revision() -> None:
    assert not _fires(
        {"source": "cadastre://parcelles"},
        {"source": "cadastre://parcelles", "last_revision": "2026-01"},
    )


# --------------------------------------------------------------------------
# Source matching
# --------------------------------------------------------------------------


def test_no_fire_when_source_mismatch() -> None:
    assert not _fires(
        {"source": "osm://poi", "revision": "r2"},
        {"source": "cadastre://parcelles", "last_revision": "r1"},
    )


def test_fires_when_source_matches_and_revision_new() -> None:
    assert _fires(
        {"source": "bdtopo://batiments", "revision": "v3"},
        {"source": "bdtopo://batiments", "last_revision": "v2"},
    )


def test_unscoped_trigger_fires_for_any_source() -> None:
    # No 'source' in the trigger conditions — watches every source.
    assert _fires(
        {"source": "anything://x", "revision": "new"},
        {"last_revision": "old"},
    )

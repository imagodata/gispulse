"""Integration test — the ETL loop, end to end and bounded (issue #188).

Exercises the full plugin-ETL chain on the real primitives:

    source.fetch()  ──▶  transform  ──▶  sink.write()        (E → T → L)
         ▲                                     │
         └──────── source.changed / cascade ◀──┘             (L → CDC → re-run)

and proves the CDC cascade is bounded by ``MAX_CASCADE_DEPTH`` — a sink
write that re-triggers the pipeline terminates instead of looping.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from gispulse.core.plugin_model import (
    AccessProtocol,
    AccessSpec,
    FetchMode,
    Payload,
    SourceResult,
    WriteReport,
    WriteSpec,
)
from gispulse.core.sources import DeclarativeSink, DeclarativeSource, ProtocolRegistry, SourceEntryRef
from gispulse.core.models import ChangeRecord, Trigger, TriggerEvent, TriggerType
from gispulse.rules.trigger_evaluator import (
    MAX_CASCADE_DEPTH,
    CascadeDepthExceeded,
    TriggerEvaluator,
)


# --------------------------------------------------------------------------
# In-memory protocol adapter — both Fetcher and Writer
# --------------------------------------------------------------------------


class MemoryAdapter:
    """A DB protocol adapter backed by an in-memory store."""

    protocol = AccessProtocol.DB

    def __init__(self) -> None:
        self.tables: dict[str, list[dict]] = {
            "parcels": [{"id": 1, "v": 10}, {"id": 2, "v": 20}],
        }

    def fetch(self, access, *, extent=None, mode=FetchMode.MATERIALIZE):
        rows = list(self.tables[access.params["table"]])
        return SourceResult(payload=Payload.TABLE, mode=mode, data=rows)

    def write(self, result, spec):
        self.tables[spec.destination] = list(result.data)
        return WriteReport(
            destination=spec.destination,
            rows_written=len(result.data),
            created=True,
        )


class ParcelsSource(DeclarativeSource):
    name = "parcels"
    domain = None  # not exercised here
    payload = Payload.TABLE
    jurisdiction = "FR"

    def entries(self):
        return [
            SourceEntryRef(
                id="parcels",
                name="Parcels",
                access=AccessSpec(
                    protocol=AccessProtocol.DB, endpoint="mem://", params={"table": "parcels"}
                ),
            )
        ]


class AnalyseSink(DeclarativeSink):
    name = "analyse"


# --------------------------------------------------------------------------
# E → T → L forward path
# --------------------------------------------------------------------------


def test_etl_forward_path_source_transform_sink() -> None:
    adapter = MemoryAdapter()
    registry = ProtocolRegistry()
    registry.register(adapter)

    source = ParcelsSource(registry=registry)
    sink = AnalyseSink(registry=registry)

    # E — extract
    extracted = source.fetch("parcels")
    assert [r["v"] for r in extracted.data] == [10, 20]

    # T — transform (the capability stage: double every value)
    transformed = SourceResult(
        payload=Payload.TABLE,
        data=[{**row, "v": row["v"] * 2} for row in extracted.data],
    )

    # L — load
    report = sink.write(
        transformed, WriteSpec(protocol=AccessProtocol.DB, destination="parcels_doubled")
    )
    assert report.rows_written == 2
    assert report.created is True
    assert [r["v"] for r in adapter.tables["parcels_doubled"]] == [20, 40]


# --------------------------------------------------------------------------
# L → CDC → re-run, bounded by MAX_CASCADE_DEPTH
# --------------------------------------------------------------------------


def _source_changed_trigger() -> Trigger:
    # Unscoped SOURCE_CHANGED with no last_revision — fires on every new
    # revision, i.e. the worst case: an unbounded re-run loop.
    return Trigger(
        id=uuid4(),
        name="rerun-on-source-change",
        event=TriggerEvent.MANUAL,
        trigger_type=TriggerType.SOURCE_CHANGED,
        conditions={},
        enabled=True,
    )


def _source_changed_record(revision: str) -> ChangeRecord:
    return ChangeRecord(
        table_name="_external_source",
        operation="INSERT",
        new_values={"source": "parcels://parcels", "revision": revision},
        feature_id="parcels",
    )


def test_etl_cdc_cascade_terminates() -> None:
    """A sink write that re-fires the pipeline must stop, not loop forever."""
    evaluator = TriggerEvaluator()
    trigger = _source_changed_trigger()

    runs = 0
    depth = 1
    while True:
        try:
            fired = evaluator.evaluate(
                _source_changed_record(revision=f"r{depth}"), [trigger], depth=depth
            )
        except CascadeDepthExceeded:
            break
        runs += 1
        if not fired[0].matched:
            break
        # Each re-run writes again -> a fresh revision -> a deeper cascade.
        depth += 1

    # Neither cut off after the first run, nor looped unbounded.
    assert runs > 1, "cascade stopped too early"
    assert runs <= MAX_CASCADE_DEPTH, "cascade exceeded the depth guard"


def test_cascade_within_limit_completes() -> None:
    """A chain shorter than the guard runs every step without raising."""
    evaluator = TriggerEvaluator()
    trigger = _source_changed_trigger()
    for depth in range(1, MAX_CASCADE_DEPTH + 1):
        fired = evaluator.evaluate(
            _source_changed_record(revision=f"r{depth}"), [trigger], depth=depth
        )
        assert fired[0].matched is True


def test_cascade_beyond_limit_raises() -> None:
    evaluator = TriggerEvaluator()
    with pytest.raises(CascadeDepthExceeded):
        evaluator.evaluate(
            _source_changed_record(revision="rX"),
            [_source_changed_trigger()],
            depth=MAX_CASCADE_DEPTH + 1,
        )

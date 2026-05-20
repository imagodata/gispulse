"""Unit tests for ``gispulse explain`` (ELT Lot 4E — issue #251).

Exercises :func:`explain_manifest` and :func:`format_explanation_text`
on synthetic v3 manifests covering the three sale-pitch questions the
``gispulse explain`` command exists to answer:

1. What runs — order + dependencies.
2. How — which strategy wins per step on the configured engine.
3. Where the SQL chain breaks — ETL-strict flagging.
"""

from __future__ import annotations

import pytest

from gispulse.core.explain import (
    ManifestExplanation,
    explain_manifest,
    format_explanation_text,
)
from gispulse.core.manifest_v3 import ManifestV3, ModelSpec, SourceSpec


def _manifest_for_engine(*models: tuple[str, str, list[dict]]) -> ManifestV3:
    """Helper — build a minimal multi-model manifest."""
    return ManifestV3(
        sources={"src": SourceSpec(name="src", uri="memory://src")},
        models={
            name: ModelSpec(name=name, select=select, transform=list(transform))
            for name, select, transform in models
        },
    )


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------


def test_explain_orders_models_topologically():
    """Models are emitted in the same order ``run_manifest`` would
    execute them — sources first, then leaves."""
    manifest = _manifest_for_engine(
        ("b", "a", [{"buffer": {"distance": 5}}]),
        ("a", "src", [{"filter": {"expression": "1==1"}}]),
    )
    ex = explain_manifest(manifest, engine="duckdb")
    assert ex.execution_order == ["a", "b"]
    assert [m.name for m in ex.models] == ["a", "b"]
    assert ex.engine == "duckdb"


def test_explain_collects_model_metadata_and_dependencies():
    manifest = ManifestV3(
        sources={"src": SourceSpec(name="src", uri="memory://src")},
        models={
            "a": ModelSpec(
                name="a", select="src",
                transform=[{"filter": {"expression": "1==1"}}],
                materialize="table",
            ),
            "b": ModelSpec(
                name="b", select="a",
                transform=[
                    {"attribute_join": {"with": "a", "left_on": "id", "right_on": "id"}}
                ],
                materialize="view",
                refresh="on_change",
            ),
        },
    )
    ex = explain_manifest(manifest, engine="duckdb")
    a = next(m for m in ex.models if m.name == "a")
    b = next(m for m in ex.models if m.name == "b")
    assert a.materialize == "table"
    assert b.depends_on == ["a"]
    assert b.refresh == "on_change"


def test_explain_uses_staging_engine_by_default():
    """When no override is passed, ``staging.engine`` drives eligibility."""
    manifest = _manifest_for_engine(
        ("m", "src", [{"filter": {"expression": "1==1"}}]),
    )
    manifest.staging.engine = "postgis"
    ex = explain_manifest(manifest)
    assert ex.engine == "postgis"


# ---------------------------------------------------------------------------
# Strategy probing
# ---------------------------------------------------------------------------


def test_explain_picks_postgis_strategy_when_engine_is_postgis():
    manifest = _manifest_for_engine(
        ("m", "src", [{"filter": {"expression": "pop > 100"}}]),
    )
    ex = explain_manifest(manifest, engine="postgis")
    step = ex.models[0].steps[0]
    assert step.capability == "filter"
    assert step.picked is not None
    assert step.picked.mode == "postgis"
    assert step.picked.priority == 100
    # Filter has python + duckdb + postgis strategies.
    modes = {s.mode for s in step.strategies}
    assert {"python", "duckdb", "postgis"}.issubset(modes)


def test_explain_picks_duckdb_strategy_when_engine_is_duckdb():
    manifest = _manifest_for_engine(
        ("m", "src", [{"filter": {"expression": "pop > 100"}}]),
    )
    ex = explain_manifest(manifest, engine="duckdb")
    step = ex.models[0].steps[0]
    assert step.picked is not None
    assert step.picked.mode == "duckdb"
    assert step.picked.priority == 80


def test_explain_marks_etl_strict_when_no_sql_strategy():
    """A capability with no SQL strategy is flagged ETL-strict.

    ``vector_diff`` deliberately stays on Python (Hausdorff-based diff,
    row-by-row) — explain must surface that.
    """
    manifest = _manifest_for_engine(
        ("m", "src", [{"vector_diff": {"id_field": "id"}}]),
    )
    ex = explain_manifest(manifest, engine="duckdb")
    step = ex.models[0].steps[0]
    assert step.etl_strict is True
    # The python-only fallback IS eligible (always), so .picked is not None.
    assert step.picked is not None
    assert step.picked.mode == "python"


def test_explain_unknown_capability_marked_etl_strict():
    manifest = _manifest_for_engine(
        ("m", "src", [{"definitely_not_a_capability": {}}]),
    )
    ex = explain_manifest(manifest, engine="duckdb")
    step = ex.models[0].steps[0]
    assert step.etl_strict is True
    assert step.strategies == []
    assert step.picked is None


# ---------------------------------------------------------------------------
# Validation propagation
# ---------------------------------------------------------------------------


def test_explain_propagates_validation_errors():
    """Cycles / unresolved refs surface from explain too."""
    manifest = ManifestV3(
        sources={"src": SourceSpec(name="src", uri="memory://src")},
        models={
            "a": ModelSpec(
                name="a", select="b", transform=[{"filter": {"expression": "1==1"}}]
            ),
            "b": ModelSpec(
                name="b", select="a", transform=[{"filter": {"expression": "1==1"}}]
            ),
        },
    )
    with pytest.raises(ValueError, match="cycle"):
        explain_manifest(manifest)


# ---------------------------------------------------------------------------
# Text renderer
# ---------------------------------------------------------------------------


def test_format_explanation_text_contains_each_model_and_step():
    manifest = _manifest_for_engine(
        ("m", "src", [{"buffer": {"distance": 50}}]),
    )
    ex = explain_manifest(manifest, engine="duckdb")
    text = format_explanation_text(ex)
    assert "Manifest:" in text
    assert "Engine:    duckdb" in text or "Engine:   duckdb" in text
    assert "model: m" in text
    assert "buffer" in text
    assert "duckdb@" in text or "postgis@" in text


def test_format_explanation_text_flags_etl_strict():
    manifest = _manifest_for_engine(
        ("m", "src", [{"vector_diff": {"id_field": "id"}}]),
    )
    ex = explain_manifest(manifest, engine="duckdb")
    text = format_explanation_text(ex)
    assert "ETL-strict" in text


# ---------------------------------------------------------------------------
# GISPulseApp.explain entry point
# ---------------------------------------------------------------------------


def test_gispulse_app_explain_runs_against_a_manifest_v3_instance():
    from gispulse.app import GISPulseApp

    manifest = _manifest_for_engine(
        ("m", "src", [{"buffer": {"distance": 5}}]),
    )
    result = GISPulseApp().explain(manifest, engine="duckdb")
    assert isinstance(result, ManifestExplanation)
    assert result.execution_order == ["m"]

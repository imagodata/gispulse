"""Unit tests for the v3 manifest runner (ELT Lot 4C — issue #249).

Exercises :func:`run_manifest` end-to-end with in-memory inputs (no real
engine), verifying view / table materialization, execution order, the
incremental NotImplementedError gate, and the materializer cache.
"""

from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import Point

from gispulse.core.manifest_v3 import ManifestV3, ModelSpec, SourceSpec
from gispulse.runtime.manifest_runner import (
    MaterializationMode,
    Materializer,
    RefreshMode,
    run_manifest,
)


# ---------------------------------------------------------------------------
# Fixtures — in-memory sources via a custom source_loader
# ---------------------------------------------------------------------------


def _make_layer(ids, values) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"id": list(ids), "val": list(values)},
        geometry=[Point(i, i) for i in ids],
        crs="EPSG:2154",
    )


def _source_loader_for(layers: dict[str, gpd.GeoDataFrame]):
    def loader(src):
        return layers[src.name]

    return loader


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_run_manifest_view_single_model():
    """A single VIEW model — runs its transforms, caches the result."""
    cad = _make_layer([1, 2, 3], [10, 20, 30])
    manifest = ManifestV3(
        sources={"cad": SourceSpec(name="cad", uri="memory://cad")},
        models={
            "kept": ModelSpec(
                name="kept",
                select="cad",
                transform=[{"filter": {"expression": "val >= 20"}}],
                materialize="view",
            ),
        },
    )
    result = run_manifest(
        manifest, source_loader=_source_loader_for({"cad": cad})
    )
    assert result.execution_order == ["kept"]
    kept = result.materialized["kept"]
    assert kept.mode == MaterializationMode.VIEW
    assert kept.refresh == RefreshMode.MANUAL
    assert sorted(kept.result["val"].tolist()) == [20, 30]
    assert kept.table_ref is None


def test_run_manifest_respects_topo_order():
    """An A→B chain must run A before B (B reads A's materialization)."""
    src = _make_layer([1, 2, 3], [10, 20, 30])
    manifest = ManifestV3(
        sources={"src": SourceSpec(name="src", uri="memory://src")},
        models={
            "b": ModelSpec(
                name="b",
                select="a",
                transform=[{"filter": {"expression": "val >= 30"}}],
            ),
            "a": ModelSpec(
                name="a",
                select="src",
                transform=[{"filter": {"expression": "val >= 20"}}],
            ),
        },
    )
    result = run_manifest(
        manifest, source_loader=_source_loader_for({"src": src})
    )
    assert result.execution_order == ["a", "b"]
    assert sorted(result.materialized["a"].result["val"]) == [20, 30]
    assert result.materialized["b"].result["val"].tolist() == [30]


def test_run_manifest_table_mode_registers_on_engine():
    """TABLE mode hands the gdf to engine.register under the prefixed name."""

    class _FakeEngine:
        backend_name = "duckdb"

        def __init__(self):
            self.registered: dict[str, gpd.GeoDataFrame] = {}

        def load_layer(self, source, *, layer=None, schema="public"):
            raise NotImplementedError  # source_loader is supplied

        def register(self, name, gdf):
            self.registered[name] = gdf

    cad = _make_layer([1, 2], [5, 9])
    engine = _FakeEngine()
    manifest = ManifestV3(
        sources={"cad": SourceSpec(name="cad", uri="memory://cad")},
        models={
            "kept": ModelSpec(
                name="kept",
                select="cad",
                transform=[{"filter": {"expression": "val > 5"}}],
                materialize="table",
            ),
        },
    )
    result = run_manifest(
        manifest,
        engine=engine,
        source_loader=_source_loader_for({"cad": cad}),
    )
    kept = result.materialized["kept"]
    assert kept.mode == MaterializationMode.TABLE
    assert kept.table_ref == "elt_kept"
    assert "elt_kept" in engine.registered
    assert engine.registered["elt_kept"]["val"].tolist() == [9]


def test_run_manifest_incremental_raises_not_implemented():
    src = _make_layer([1], [1])
    manifest = ManifestV3(
        sources={"src": SourceSpec(name="src", uri="memory://src")},
        models={
            "inc": ModelSpec(
                name="inc",
                select="src",
                transform=[{"filter": {"expression": "val >= 0"}}],
                materialize="incremental",
            ),
        },
    )
    with pytest.raises(NotImplementedError, match="incremental"):
        run_manifest(
            manifest, source_loader=_source_loader_for({"src": src})
        )


def test_run_manifest_propagates_validation_errors():
    """Unresolved select must surface from run_manifest (not silently swallowed)."""
    manifest = ManifestV3(
        sources={"src": SourceSpec(name="src", uri="memory://src")},
        models={
            "m": ModelSpec(name="m", select="ghost", transform=[]),
        },
    )
    with pytest.raises(ValueError, match="ghost"):
        run_manifest(
            manifest, source_loader=_source_loader_for({"src": _make_layer([1], [1])})
        )


def test_materializer_reuse_shares_cache_across_runs():
    """Caller-supplied materializer accumulates results across calls."""
    src = _make_layer([1, 2], [10, 20])
    materializer = Materializer()
    manifest_a = ManifestV3(
        sources={"src": SourceSpec(name="src", uri="memory://src")},
        models={"a": ModelSpec(name="a", select="src", transform=[])},
    )
    run_manifest(
        manifest_a,
        source_loader=_source_loader_for({"src": src}),
        materializer=materializer,
    )
    assert "a" in materializer.models
    manifest_b = ManifestV3(
        sources={"src": SourceSpec(name="src", uri="memory://src")},
        models={"b": ModelSpec(name="b", select="src", transform=[])},
    )
    run_manifest(
        manifest_b,
        source_loader=_source_loader_for({"src": src}),
        materializer=materializer,
    )
    assert {"a", "b"} == set(materializer.models)


def test_table_mode_requires_engine():
    src = _make_layer([1], [1])
    manifest = ManifestV3(
        sources={"src": SourceSpec(name="src", uri="memory://src")},
        models={
            "m": ModelSpec(
                name="m", select="src", transform=[], materialize="table"
            ),
        },
    )
    # No engine, no materializer → TABLE has no engine to register on.
    with pytest.raises(RuntimeError, match="TABLE materialization"):
        run_manifest(
            manifest,
            source_loader=_source_loader_for({"src": src}),
        )


def test_run_manifest_with_clause_routes_ref_layer():
    """A model whose transform has ``with: <other_model>`` reads it via
    the ``ref_layer`` plumbing, not as the primary input."""
    left = _make_layer([1, 2, 3], [10, 20, 30])
    manifest = ManifestV3(
        sources={"src": SourceSpec(name="src", uri="memory://src")},
        models={
            "filtered": ModelSpec(
                name="filtered",
                select="src",
                transform=[{"filter": {"expression": "val >= 20"}}],
            ),
            "joined": ModelSpec(
                name="joined",
                select="src",
                transform=[
                    {
                        "attribute_join": {
                            "with": "filtered",
                            "left_on": "id",
                            "right_on": "id",
                            "how": "inner",
                        }
                    }
                ],
            ),
        },
    )
    result = run_manifest(
        manifest, source_loader=_source_loader_for({"src": left})
    )
    assert result.execution_order == ["filtered", "joined"]
    joined = result.materialized["joined"].result
    # Inner-joined with the filtered set (vals >= 20) → 2 rows kept.
    assert len(joined) == 2
    assert sorted(joined["id"].tolist()) == [2, 3]

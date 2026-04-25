"""Tests for core.capability_params — TypedDict mirrors of capability schemas.

These TypedDicts are developer ergonomics (IDE autocompletion for StepSpec
construction). The JSON Schema in each capability's ``get_schema()`` stays
the runtime source of truth — these tests pin the drift contract between
the two surfaces.
"""
from __future__ import annotations

from typing import get_type_hints

import pytest

from core.capability_params import (
    PARAMS_TYPE_MAP,
    AreaLengthParams,
    BufferParams,
    CalculateParams,
    CentroidParams,
    ClipParams,
    DissolveParams,
    FilterParams,
    IntersectsParams,
    ReprojectParams,
    SpatialJoinParams,
)


class TestParamsTypeMap:
    def test_map_keys_match_each_declared_class(self):
        """PARAMS_TYPE_MAP must map each declared class exactly once."""
        expected = {
            "filter": FilterParams,
            "buffer": BufferParams,
            "spatial_join": SpatialJoinParams,
            "dissolve": DissolveParams,
            "centroid": CentroidParams,
            "clip": ClipParams,
            "area_length": AreaLengthParams,
            "calculate": CalculateParams,
            "intersects": IntersectsParams,
            "reproject": ReprojectParams,
        }
        assert PARAMS_TYPE_MAP == expected

    def test_all_values_are_typeddict_subclasses(self):
        from typing import is_typeddict

        for name, cls in PARAMS_TYPE_MAP.items():
            assert is_typeddict(cls), f"{name} → {cls} is not a TypedDict"


class TestTypedDictConstruction:
    """TypedDict at runtime is just dict[str, Any] — but we can verify that
    each declared class supports the documented keys without raising."""

    def test_filter_params_accepts_partial(self):
        p: FilterParams = {"expression": "area > 1000"}
        assert p["expression"] == "area > 1000"

    def test_filter_params_accepts_all_keys(self):
        p: FilterParams = {
            "expression": "x > 0",
            "spatial_predicate": "intersects",
            "ref_wkt": "POINT(0 0)",
            "ref_geojson": {"type": "Point", "coordinates": [0, 0]},
            "ref_layer": "roads",
            "buffer_distance": 10.0,
        }
        assert len(p) == 6

    def test_buffer_params_required_distance(self):
        p: BufferParams = {"distance": 50.0}
        assert p["distance"] == 50.0

    def test_buffer_params_with_optional_crs(self):
        p: BufferParams = {"distance": 50.0, "crs_meters": "EPSG:2154"}
        assert p["crs_meters"] == "EPSG:2154"

    def test_spatial_join_minimum(self):
        p: SpatialJoinParams = {"ref_layer": "zones"}
        assert p["ref_layer"] == "zones"

    def test_spatial_join_full(self):
        p: SpatialJoinParams = {
            "ref_layer": "zones",
            "how": "left",
            "predicate": "within",
            "columns": ["name", "category"],
        }
        assert p["columns"] == ["name", "category"]

    def test_dissolve_without_by(self):
        p: DissolveParams = {}
        assert p == {}

    def test_dissolve_with_by(self):
        p: DissolveParams = {"by": "zone"}
        assert p["by"] == "zone"

    def test_centroid_accepts_empty(self):
        p: CentroidParams = {}
        assert p == {}

    def test_clip_requires_ref_layer(self):
        p: ClipParams = {"ref_layer": "boundary"}
        assert p["ref_layer"] == "boundary"

    def test_area_length_all_optional(self):
        p: AreaLengthParams = {}
        assert p == {}
        p2: AreaLengthParams = {
            "crs_meters": "EPSG:2154",
            "area_col": "surface_m2",
            "length_col": "perim_m",
            "compute_area": True,
            "compute_length": False,
        }
        assert p2["compute_length"] is False

    def test_calculate_expressions_dict(self):
        p: CalculateParams = {
            "expressions": {"area_ha": "area_m2 / 10000", "name_upper": "name.str.upper()"}
        }
        assert len(p["expressions"]) == 2

    def test_intersects_accepts_wkt_or_layer(self):
        a: IntersectsParams = {"wkt": "POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))"}
        b: IntersectsParams = {"ref_layer": "parcels"}
        assert "wkt" in a
        assert "ref_layer" in b

    def test_reproject_requires_target_crs(self):
        p: ReprojectParams = {"target_crs": "EPSG:4326"}
        assert p["target_crs"] == "EPSG:4326"


class TestTypedDictKeyAnnotations:
    """Verify the declared keys on each TypedDict — catches accidental
    key removal / rename in refactors."""

    @pytest.mark.parametrize(
        "cls, keys",
        [
            (FilterParams, {"expression", "spatial_predicate", "ref_wkt",
                            "ref_geojson", "ref_layer", "buffer_distance"}),
            (BufferParams, {"distance", "crs_meters"}),
            (SpatialJoinParams, {"ref_layer", "how", "predicate", "columns"}),
            (DissolveParams, {"by"}),
            (CentroidParams, set()),
            (ClipParams, {"ref_layer"}),
            (AreaLengthParams, {"crs_meters", "area_col", "length_col",
                                "compute_area", "compute_length"}),
            (CalculateParams, {"expressions"}),
            (IntersectsParams, {"wkt", "ref_layer"}),
            (ReprojectParams, {"target_crs"}),
        ],
    )
    def test_declared_keys_are_expected(self, cls, keys):
        hints = get_type_hints(cls)
        assert set(hints.keys()) == keys


class TestRegistryAlignment:
    """PARAMS_TYPE_MAP is a curated subset of the capability registry used by
    the UI / SDK. Not every registered capability needs a TypedDict, but every
    TypedDict key must exist in the registry."""

    def test_every_mapped_name_exists_in_registry(self):
        from capabilities import registry

        registry._ensure_defaults_loaded()
        registered = set(registry.REGISTRY.keys())
        mapped = set(PARAMS_TYPE_MAP.keys())

        missing_in_registry = mapped - registered
        assert not missing_in_registry, (
            f"PARAMS_TYPE_MAP references capabilities that are not registered: "
            f"{missing_in_registry}"
        )


class TestPlumbingNotRequired:
    """Guard against regressions of the `required: ["ref_layer"]` bug.

    `ref_layer` / `ref_gdf` are pipeline plumbing stripped by
    `rules.validation._PLUMBING_KEYS` before schema validation in the v2
    path (PipelineExecutor + GraphExecutor). Listing them in `required`
    makes every v2 call fail with "Required parameter 'ref_layer' is
    missing" before the capability even runs.
    """

    def test_no_capability_requires_plumbing_key(self):
        from capabilities import registry
        from rules.validation import _PLUMBING_KEYS

        registry._ensure_defaults_loaded()

        offenders: list[tuple[str, list[str]]] = []
        for name, cls in registry.REGISTRY.items():
            try:
                schema = cls().get_schema()
            except Exception:
                continue
            required = schema.get("required", []) or []
            bad = [k for k in required if k in _PLUMBING_KEYS]
            if bad:
                offenders.append((name, bad))

        assert not offenders, (
            "Capabilities must not list plumbing keys (ref_layer/ref_gdf) in "
            f"`required` — v2 path strips them before validation. Offenders: {offenders}"
        )

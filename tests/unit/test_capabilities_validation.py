"""Tests for capabilities/validation.py — S11 data quality capabilities.

Covers: TopologyCheckCapability, DuplicateGeometryCapability,
        AttributeValidationCapability, CompletenessCheckCapability.

These capabilities are Community tier (free) — no tier gating.
"""

from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from shapely.wkt import loads as wkt_loads

from capabilities.validation import (
    AttributeValidationCapability,
    CompletenessCheckCapability,
    DuplicateGeometryCapability,
    TopologyCheckCapability,
)


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def _simple_point_gdf(**extra_cols) -> gpd.GeoDataFrame:
    """Three non-overlapping points."""
    data = {
        "id": [1, 2, 3],
        "name": ["A", "B", "C"],
        "geometry": [Point(0, 0), Point(1, 0), Point(2, 0)],
        **extra_cols,
    }
    return gpd.GeoDataFrame(data, crs="EPSG:4326")


def _simple_polygon_gdf() -> gpd.GeoDataFrame:
    """Three non-overlapping unit squares."""
    return gpd.GeoDataFrame(
        {
            "id": [1, 2, 3],
            "geometry": [
                Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
                Polygon([(1, 0), (2, 0), (2, 1), (1, 1)]),
                Polygon([(2, 0), (3, 0), (3, 1), (2, 1)]),
            ],
        },
        crs="EPSG:4326",
    )


def _overlapping_polygon_gdf() -> gpd.GeoDataFrame:
    """Two polygons that overlap by 0.5×1 area."""
    return gpd.GeoDataFrame(
        {
            "id": [1, 2],
            "geometry": [
                Polygon([(0, 0), (1.5, 0), (1.5, 1), (0, 1)]),
                Polygon([(1, 0), (2, 0), (2, 1), (1, 1)]),
            ],
        },
        crs="EPSG:4326",
    )


def _self_intersecting_line_gdf() -> gpd.GeoDataFrame:
    """A figure-8 line that self-intersects."""
    # Bowtie-shaped polygon that is invalid
    bowtie = Polygon([(0, 0), (2, 2), (2, 0), (0, 2), (0, 0)])
    return gpd.GeoDataFrame({"geometry": [bowtie]}, crs="EPSG:4326")


# ---------------------------------------------------------------------------
# TopologyCheckCapability — Community tier (no tier gate)
# ---------------------------------------------------------------------------


class TestTopologyCheck:
    def test_no_issues_on_valid_polygons(self):
        cap = TopologyCheckCapability()
        result = cap.execute(_simple_polygon_gdf())
        assert isinstance(result, gpd.GeoDataFrame)
        assert len(result) == 0

    def test_detects_overlapping_polygons(self):
        cap = TopologyCheckCapability()
        result = cap.execute(_overlapping_polygon_gdf())
        overlap_issues = result[result["issue_type"] == "overlap"]
        assert len(overlap_issues) > 0

    def test_detects_invalid_geometry(self):
        cap = TopologyCheckCapability()
        result = cap.execute(_self_intersecting_line_gdf())
        # The bowtie polygon is invalid
        invalid_issues = result[result["issue_type"].isin(["invalid_geometry", "self_intersection"])]
        assert len(invalid_issues) > 0

    def test_returns_geodataframe(self):
        cap = TopologyCheckCapability()
        result = cap.execute(_simple_polygon_gdf())
        assert isinstance(result, gpd.GeoDataFrame)

    def test_result_columns_present(self):
        cap = TopologyCheckCapability()
        result = cap.execute(_overlapping_polygon_gdf())
        for col in ("feature_id", "issue_type", "description", "geometry"):
            assert col in result.columns

    def test_no_overlap_check_skips_overlap(self):
        cap = TopologyCheckCapability()
        result = cap.execute(_overlapping_polygon_gdf(), check_overlaps=False)
        overlap_issues = result[result["issue_type"] == "overlap"]
        assert len(overlap_issues) == 0

    def test_null_geometry_detected(self):
        gdf = gpd.GeoDataFrame(
            {"id": [1, 2], "geometry": [Point(0, 0), None]}, crs="EPSG:4326"
        )
        cap = TopologyCheckCapability()
        result = cap.execute(gdf)
        null_issues = result[result["issue_type"] == "null_geometry"]
        assert len(null_issues) == 1

    def test_id_col_used_in_feature_id(self):
        cap = TopologyCheckCapability()
        result = cap.execute(_overlapping_polygon_gdf(), id_col="id")
        # feature_id should contain the id values from the column
        assert len(result) > 0
        # feature_id for an overlap contains both ids joined
        assert any("+" in str(fid) for fid in result["feature_id"])

    def test_empty_gdf_returns_empty(self):
        cap = TopologyCheckCapability()
        empty = gpd.GeoDataFrame(columns=["geometry"], geometry="geometry", crs="EPSG:4326")
        result = cap.execute(empty)
        assert isinstance(result, gpd.GeoDataFrame)
        assert len(result) == 0

    def test_schema_has_check_overlaps(self):
        cap = TopologyCheckCapability()
        schema = cap.get_schema()
        assert "check_overlaps" in schema["properties"]

    def test_no_tier_gate(self):
        """TopologyCheckCapability must work without Pro tier."""
        import os
        os.environ["GISPULSE_TIER"] = "community"
        os.environ.pop("GISPULSE_LICENSE_KEY", None)
        cap = TopologyCheckCapability()
        # Should not raise TierError
        result = cap.execute(_simple_polygon_gdf())
        assert isinstance(result, gpd.GeoDataFrame)


# ---------------------------------------------------------------------------
# DuplicateGeometryCapability — Community tier
# ---------------------------------------------------------------------------


class TestDuplicateGeometry:
    def test_no_duplicates_returns_empty(self):
        cap = DuplicateGeometryCapability()
        result = cap.execute(_simple_point_gdf())
        assert len(result) == 0

    def test_exact_duplicates_detected(self):
        gdf = gpd.GeoDataFrame(
            {"id": [1, 2, 3], "geometry": [Point(0, 0), Point(0, 0), Point(1, 1)]},
            crs="EPSG:4326",
        )
        cap = DuplicateGeometryCapability()
        result = cap.execute(gdf)
        assert len(result) == 1

    def test_duplicate_of_column_present(self):
        gdf = gpd.GeoDataFrame(
            {"id": [1, 2], "geometry": [Point(0, 0), Point(0, 0)]},
            crs="EPSG:4326",
        )
        cap = DuplicateGeometryCapability()
        result = cap.execute(gdf)
        assert "duplicate_of" in result.columns
        assert "feature_id" in result.columns

    def test_three_copies_reports_two_duplicates(self):
        gdf = gpd.GeoDataFrame(
            {"geometry": [Point(0, 0), Point(0, 0), Point(0, 0)]},
            crs="EPSG:4326",
        )
        cap = DuplicateGeometryCapability()
        result = cap.execute(gdf)
        assert len(result) == 2

    def test_fuzzy_duplicates_detected(self):
        # Points 0.001 apart should be detected as duplicates with tolerance=0.01
        gdf = gpd.GeoDataFrame(
            {"geometry": [Point(0, 0), Point(0.001, 0), Point(5, 5)]},
            crs="EPSG:4326",
        )
        cap = DuplicateGeometryCapability()
        result = cap.execute(gdf, tolerance=0.01)
        assert len(result) >= 1

    def test_fuzzy_tolerance_zero_no_false_positives(self):
        # Two distinct points 0.001 apart — exact match should NOT flag them
        gdf = gpd.GeoDataFrame(
            {"geometry": [Point(0, 0), Point(0.001, 0)]},
            crs="EPSG:4326",
        )
        cap = DuplicateGeometryCapability()
        result = cap.execute(gdf, tolerance=0.0)
        assert len(result) == 0

    def test_id_col_used(self):
        gdf = gpd.GeoDataFrame(
            {"my_id": ["feat_1", "feat_2"], "geometry": [Point(0, 0), Point(0, 0)]},
            crs="EPSG:4326",
        )
        cap = DuplicateGeometryCapability()
        result = cap.execute(gdf, id_col="my_id")
        assert result["feature_id"].iloc[0] == "feat_2"
        assert result["duplicate_of"].iloc[0] == "feat_1"

    def test_empty_gdf_returns_empty(self):
        cap = DuplicateGeometryCapability()
        empty = gpd.GeoDataFrame(columns=["geometry"], geometry="geometry", crs="EPSG:4326")
        result = cap.execute(empty)
        assert len(result) == 0

    def test_result_has_geometry_column(self):
        gdf = gpd.GeoDataFrame(
            {"geometry": [Point(0, 0), Point(0, 0)]},
            crs="EPSG:4326",
        )
        cap = DuplicateGeometryCapability()
        result = cap.execute(gdf)
        assert "geometry" in result.columns

    def test_no_tier_gate(self):
        """DuplicateGeometryCapability must work without Pro tier."""
        import os
        os.environ["GISPULSE_TIER"] = "community"
        cap = DuplicateGeometryCapability()
        result = cap.execute(_simple_point_gdf())
        assert isinstance(result, gpd.GeoDataFrame)


# ---------------------------------------------------------------------------
# AttributeValidationCapability — Community tier
# ---------------------------------------------------------------------------


class TestAttributeValidation:
    def test_valid_data_returns_empty(self):
        gdf = _simple_point_gdf()
        cap = AttributeValidationCapability()
        result = cap.execute(
            gdf,
            schema={
                "id": {"type": "int", "nullable": False},
                "name": {"type": "str", "nullable": False},
            },
        )
        assert len(result) == 0

    def test_returns_geodataframe(self):
        cap = AttributeValidationCapability()
        result = cap.execute(_simple_point_gdf(), schema={})
        assert isinstance(result, gpd.GeoDataFrame)

    def test_type_violation_detected(self):
        gdf = gpd.GeoDataFrame(
            {"val": [1, "two", 3], "geometry": [Point(0, 0), Point(1, 0), Point(2, 0)]},
            crs="EPSG:4326",
        )
        cap = AttributeValidationCapability()
        result = cap.execute(gdf, schema={"val": {"type": "int"}})
        type_violations = result[result["rule"] == "type"]
        assert len(type_violations) >= 1

    def test_nullable_violation_detected(self):
        import numpy as np

        gdf = gpd.GeoDataFrame(
            {"val": [1, None, 3], "geometry": [Point(0, 0), Point(1, 0), Point(2, 0)]},
            crs="EPSG:4326",
        )
        cap = AttributeValidationCapability()
        result = cap.execute(gdf, schema={"val": {"nullable": False}})
        null_violations = result[result["rule"] == "nullable"]
        assert len(null_violations) == 1

    def test_min_violation_detected(self):
        gdf = gpd.GeoDataFrame(
            {"score": [10, 5, 20], "geometry": [Point(0, 0), Point(1, 0), Point(2, 0)]},
            crs="EPSG:4326",
        )
        cap = AttributeValidationCapability()
        result = cap.execute(gdf, schema={"score": {"min": 8}})
        min_violations = result[result["rule"] == "min"]
        assert len(min_violations) == 1
        assert result[result["rule"] == "min"]["feature_id"].iloc[0] == 1  # index 1

    def test_max_violation_detected(self):
        gdf = gpd.GeoDataFrame(
            {"score": [10, 5, 200], "geometry": [Point(0, 0), Point(1, 0), Point(2, 0)]},
            crs="EPSG:4326",
        )
        cap = AttributeValidationCapability()
        result = cap.execute(gdf, schema={"score": {"max": 100}})
        assert len(result[result["rule"] == "max"]) == 1

    def test_pattern_violation_detected(self):
        gdf = gpd.GeoDataFrame(
            {"code": ["FR-01", "invalid", "FR-03"], "geometry": [Point(0, 0), Point(1, 0), Point(2, 0)]},
            crs="EPSG:4326",
        )
        cap = AttributeValidationCapability()
        result = cap.execute(gdf, schema={"code": {"pattern": r"FR-\d{2}"}})
        pattern_violations = result[result["rule"] == "pattern"]
        assert len(pattern_violations) == 1

    def test_allowed_violation_detected(self):
        gdf = gpd.GeoDataFrame(
            {"status": ["active", "unknown", "active"], "geometry": [Point(0, 0), Point(1, 0), Point(2, 0)]},
            crs="EPSG:4326",
        )
        cap = AttributeValidationCapability()
        result = cap.execute(gdf, schema={"status": {"allowed": ["active", "inactive"]}})
        allowed_violations = result[result["rule"] == "allowed"]
        assert len(allowed_violations) == 1

    def test_missing_column_flags_all_features(self):
        gdf = _simple_point_gdf()
        cap = AttributeValidationCapability()
        result = cap.execute(gdf, schema={"nonexistent_col": {"nullable": False}})
        missing_issues = result[result["rule"] == "missing_column"]
        assert len(missing_issues) == len(gdf)

    def test_empty_schema_returns_empty(self):
        cap = AttributeValidationCapability()
        result = cap.execute(_simple_point_gdf(), schema={})
        assert len(result) == 0

    def test_result_has_expected_columns(self):
        gdf = gpd.GeoDataFrame(
            {"val": [1, "x"], "geometry": [Point(0, 0), Point(1, 0)]},
            crs="EPSG:4326",
        )
        cap = AttributeValidationCapability()
        result = cap.execute(gdf, schema={"val": {"type": "int"}})
        for col in ("feature_id", "column", "rule", "value", "description", "geometry"):
            assert col in result.columns

    def test_string_length_min_violation(self):
        gdf = gpd.GeoDataFrame(
            {"code": ["AB", "A", "CDE"], "geometry": [Point(0, 0), Point(1, 0), Point(2, 0)]},
            crs="EPSG:4326",
        )
        cap = AttributeValidationCapability()
        result = cap.execute(gdf, schema={"code": {"type": "str", "min": 2}})
        min_issues = result[result["rule"] == "min"]
        assert len(min_issues) == 1  # "A" has length 1 < 2

    def test_no_tier_gate(self):
        """AttributeValidationCapability must work without Pro tier."""
        import os
        os.environ["GISPULSE_TIER"] = "community"
        cap = AttributeValidationCapability()
        result = cap.execute(_simple_point_gdf(), schema={"id": {"type": "int"}})
        assert isinstance(result, gpd.GeoDataFrame)


# ---------------------------------------------------------------------------
# CompletenessCheckCapability — Community tier
# ---------------------------------------------------------------------------


class TestCompletenessCheck:
    def test_returns_geodataframe(self):
        cap = CompletenessCheckCapability()
        result = cap.execute(_simple_point_gdf())
        assert isinstance(result, gpd.GeoDataFrame)

    def test_row_per_column(self):
        gdf = _simple_point_gdf()
        cap = CompletenessCheckCapability()
        result = cap.execute(gdf)
        # Should have one row per non-geometry column
        non_geom_cols = [c for c in gdf.columns if c != gdf.geometry.name]
        assert len(result) == len(non_geom_cols)

    def test_result_columns_present(self):
        cap = CompletenessCheckCapability()
        result = cap.execute(_simple_point_gdf())
        for col in ("column", "total", "null_count", "null_ratio", "is_complete"):
            assert col in result.columns

    def test_full_data_null_ratio_zero(self):
        cap = CompletenessCheckCapability()
        result = cap.execute(_simple_point_gdf())
        assert all(result["null_ratio"] == 0.0)

    def test_null_ratio_computed_correctly(self):
        import numpy as np
        gdf = gpd.GeoDataFrame(
            {
                "val": [1.0, None, None, 4.0],
                "geometry": [Point(i, 0) for i in range(4)],
            },
            crs="EPSG:4326",
        )
        cap = CompletenessCheckCapability()
        result = cap.execute(gdf, columns=["val"])
        val_row = result[result["column"] == "val"].iloc[0]
        assert val_row["null_count"] == 2
        assert val_row["null_ratio"] == pytest.approx(0.5)

    def test_is_complete_flag_with_threshold(self):
        import numpy as np
        gdf = gpd.GeoDataFrame(
            {
                "val": [1.0, None, None, 4.0],
                "geometry": [Point(i, 0) for i in range(4)],
            },
            crs="EPSG:4326",
        )
        cap = CompletenessCheckCapability()
        # Threshold 0.6 → 50% null is below threshold → is_complete = True
        result = cap.execute(gdf, columns=["val"], null_threshold=0.6)
        val_row = result[result["column"] == "val"].iloc[0]
        assert bool(val_row["is_complete"]) is True

        # Threshold 0.3 → 50% null exceeds threshold → is_complete = False
        result2 = cap.execute(gdf, columns=["val"], null_threshold=0.3)
        val_row2 = result2[result2["column"] == "val"].iloc[0]
        assert bool(val_row2["is_complete"]) is False

    def test_specific_columns_only(self):
        gdf = _simple_point_gdf()
        cap = CompletenessCheckCapability()
        result = cap.execute(gdf, columns=["id"])
        assert len(result) == 1
        assert result["column"].iloc[0] == "id"

    def test_missing_column_in_spec_flagged(self):
        gdf = _simple_point_gdf()
        cap = CompletenessCheckCapability()
        result = cap.execute(gdf, columns=["nonexistent_col"])
        assert len(result) == 1
        assert result["null_ratio"].iloc[0] == 1.0
        assert bool(result["is_complete"].iloc[0]) is False

    def test_geometry_only_gdf_no_attrs(self):
        """Beta P1 (2026-04-24): a GeoDataFrame with only the geometry column
        and no attribute columns used to crash with
        ``ValueError: Unknown column geometry`` because the empty rows list
        produced a frame with no columns at all. Return an empty result with
        the contract schema instead.
        """
        gdf = gpd.GeoDataFrame(
            geometry=[Point(0, 0), Point(1, 1)], crs="EPSG:4326"
        )
        cap = CompletenessCheckCapability()
        result = cap.execute(gdf)
        assert isinstance(result, gpd.GeoDataFrame)
        assert len(result) == 0
        for col in (
            "column",
            "total",
            "null_count",
            "null_ratio",
            "is_complete",
            "coverage_ratio",
            "geometry",
        ):
            assert col in result.columns

    def test_spatial_coverage_with_reference(self):
        """Coverage should be ~1.0 when reference equals input extent."""
        from shapely.geometry import box
        gdf = gpd.GeoDataFrame(
            {"id": [1], "geometry": [box(0, 0, 1, 1)]}, crs="EPSG:4326"
        )
        ref_gdf = gpd.GeoDataFrame(
            {"geometry": [box(0, 0, 1, 1)]}, crs="EPSG:4326"
        )
        cap = CompletenessCheckCapability()
        result = cap.execute(gdf, reference_gdf=ref_gdf)
        spatial_row = result[result["column"] == "_spatial_coverage"]
        assert len(spatial_row) == 1
        assert spatial_row["coverage_ratio"].iloc[0] == pytest.approx(1.0, abs=0.01)

    def test_partial_coverage_reported(self):
        """Coverage = 0.5 when data covers half the reference."""
        from shapely.geometry import box
        gdf = gpd.GeoDataFrame(
            {"id": [1], "geometry": [box(0, 0, 0.5, 1)]}, crs="EPSG:4326"
        )
        ref_gdf = gpd.GeoDataFrame(
            {"geometry": [box(0, 0, 1, 1)]}, crs="EPSG:4326"
        )
        cap = CompletenessCheckCapability()
        result = cap.execute(gdf, reference_gdf=ref_gdf)
        spatial_row = result[result["column"] == "_spatial_coverage"]
        assert spatial_row["coverage_ratio"].iloc[0] == pytest.approx(0.5, abs=0.01)

    def test_no_tier_gate(self):
        """CompletenessCheckCapability must work without Pro tier."""
        import os
        os.environ["GISPULSE_TIER"] = "community"
        cap = CompletenessCheckCapability()
        result = cap.execute(_simple_point_gdf())
        assert isinstance(result, gpd.GeoDataFrame)

    def test_schema_has_null_threshold(self):
        cap = CompletenessCheckCapability()
        schema = cap.get_schema()
        assert "null_threshold" in schema["properties"]


# ---------------------------------------------------------------------------
# Registry integration — all validation capabilities auto-register
# ---------------------------------------------------------------------------


class TestValidationRegistration:
    def test_topology_check_in_registry(self):
        from capabilities.registry import REGISTRY
        import capabilities.validation  # noqa: F401 — ensure module is loaded
        assert "topology_check" in REGISTRY

    def test_duplicate_geometry_in_registry(self):
        from capabilities.registry import REGISTRY
        assert "duplicate_geometry" in REGISTRY

    def test_attribute_validation_in_registry(self):
        from capabilities.registry import REGISTRY
        assert "attribute_validation" in REGISTRY

    def test_completeness_check_in_registry(self):
        from capabilities.registry import REGISTRY
        assert "completeness_check" in REGISTRY

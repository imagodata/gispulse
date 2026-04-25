"""Tests for SpatialRelationDetector."""

import geopandas as gpd
import pytest
from shapely.geometry import LineString, Point
from shapely.geometry import box

from capabilities.relation_detector import DetectedRelation, SpatialRelationDetector


@pytest.fixture
def detector() -> SpatialRelationDetector:
    return SpatialRelationDetector(sample_size=100)


@pytest.fixture
def polygons_gdf() -> gpd.GeoDataFrame:
    """Grid of 4 polygons."""
    return gpd.GeoDataFrame(
        {"id": [1, 2, 3, 4], "code_commune": ["A", "B", "C", "D"]},
        geometry=[
            box(0, 0, 10, 10),
            box(10, 0, 20, 10),
            box(0, 10, 10, 20),
            box(10, 10, 20, 20),
        ],
        crs="EPSG:4326",
    )


@pytest.fixture
def points_gdf() -> gpd.GeoDataFrame:
    """Points inside the polygon grid."""
    return gpd.GeoDataFrame(
        {"id": [1, 2, 3, 4, 5], "code_commune": ["A", "B", "C", "D", "A"]},
        geometry=[
            Point(5, 5),
            Point(15, 5),
            Point(5, 15),
            Point(15, 15),
            Point(3, 3),
        ],
        crs="EPSG:4326",
    )


@pytest.fixture
def lines_gdf() -> gpd.GeoDataFrame:
    """Lines with no shared attribute fields with polygons (only 'id' which is filtered)."""
    return gpd.GeoDataFrame(
        {"id": [1, 2]},
        geometry=[
            LineString([(0, 0), (5, 5)]),
            LineString([(10, 10), (15, 15)]),
        ],
        crs="EPSG:4326",
    )


class TestAttributeDetection:
    def test_common_fields_detected(
        self,
        detector: SpatialRelationDetector,
        polygons_gdf: gpd.GeoDataFrame,
        points_gdf: gpd.GeoDataFrame,
    ) -> None:
        results = detector.analyze(polygons_gdf, points_gdf, "polygons", "points")
        attr_rels = [r for r in results if r.relation_type == "attribute"]
        assert len(attr_rels) >= 1
        field_names = {r.sample_stats["field"] for r in attr_rels}
        assert "code_commune" in field_names

    def test_no_common_fields(
        self,
        detector: SpatialRelationDetector,
        polygons_gdf: gpd.GeoDataFrame,
        lines_gdf: gpd.GeoDataFrame,
    ) -> None:
        results = detector.analyze(polygons_gdf, lines_gdf, "polygons", "lines")
        attr_rels = [r for r in results if r.relation_type == "attribute"]
        # "id" is the only common column but it is filtered out
        assert len(attr_rels) == 0


class TestContainmentDetection:
    def test_polygons_contain_points(
        self,
        detector: SpatialRelationDetector,
        polygons_gdf: gpd.GeoDataFrame,
        points_gdf: gpd.GeoDataFrame,
    ) -> None:
        results = detector.analyze(polygons_gdf, points_gdf, "polygons", "points")
        contains_rels = [r for r in results if r.relation_type == "contains"]
        assert len(contains_rels) >= 1
        rel = contains_rels[0]
        assert rel.confidence > 0.5
        assert rel.sample_stats["match_pct"] > 0.8


class TestOverlapDetection:
    def test_overlapping_polygons(self, detector: SpatialRelationDetector) -> None:
        a = gpd.GeoDataFrame(
            {"id": [1]},
            geometry=[box(0, 0, 15, 15)],
            crs="EPSG:4326",
        )
        b = gpd.GeoDataFrame(
            {"id": [1]},
            geometry=[box(5, 5, 20, 20)],
            crs="EPSG:4326",
        )
        results = detector.analyze(a, b, "a", "b")
        overlap_rels = [r for r in results if r.relation_type == "overlaps"]
        assert len(overlap_rels) >= 1


class TestProximityDetection:
    def test_proximity_pattern(self, detector: SpatialRelationDetector) -> None:
        """Two rows of points consistently ~10 degrees apart in latitude."""
        a = gpd.GeoDataFrame(
            {"id": range(20)},
            geometry=[Point(i * 5, 0) for i in range(20)],
            crs="EPSG:4326",
        )
        b = gpd.GeoDataFrame(
            {"id": range(20)},
            geometry=[Point(i * 5, 10) for i in range(20)],
            crs="EPSG:4326",
        )
        results = detector.analyze(a, b, "a", "b")
        prox_rels = [r for r in results if r.relation_type == "proximity"]
        assert len(prox_rels) >= 1
        stats = prox_rels[0].sample_stats
        # After UTM reprojection distances are in metres; verify the pattern is
        # consistent (low coefficient of variation) rather than a fixed value.
        assert stats["avg_distance"] > 0
        assert stats["std_distance"] / stats["avg_distance"] < 0.5


class TestAnalyzeAll:
    def test_all_pairs(
        self,
        detector: SpatialRelationDetector,
        polygons_gdf: gpd.GeoDataFrame,
        points_gdf: gpd.GeoDataFrame,
        lines_gdf: gpd.GeoDataFrame,
    ) -> None:
        layers = {
            "polygons": polygons_gdf,
            "points": points_gdf,
            "lines": lines_gdf,
        }
        results = detector.analyze_all(layers)
        # Collect all layer names mentioned in results
        pairs = {(r.layer_a, r.layer_b) for r in results} | {
            (r.layer_b, r.layer_a) for r in results
        }
        # At minimum the polygons-points containment should be detected
        assert any("polygons" in p and "points" in p for p in pairs)


class TestDetectedRelation:
    def test_dataclass_fields(self) -> None:
        r = DetectedRelation(
            layer_a="a",
            layer_b="b",
            relation_type="contains",
            confidence=0.95,
            sample_stats={"match_pct": 0.95},
            suggested_name="b_within_a",
        )
        assert r.layer_a == "a"
        assert r.confidence == 0.95
        assert r.suggested_rule is None  # default value

    def test_suggested_rule_populated(self) -> None:
        rule = {"capability": "spatial_join", "config": {"predicate": "within"}}
        r = DetectedRelation(
            layer_a="zones",
            layer_b="points",
            relation_type="contains",
            confidence=0.9,
            suggested_rule=rule,
        )
        assert r.suggested_rule == rule

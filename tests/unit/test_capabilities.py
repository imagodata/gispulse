"""Unit tests for GISPulse vector capabilities."""

from __future__ import annotations

import pytest
import geopandas as gpd
from shapely.geometry import Point, Polygon

from gispulse.capabilities.registry import get, list_all, REGISTRY
from gispulse.capabilities.vector import (
    AreaLengthCapability,
    BufferCapability,
    CentroidCapability,
    ClipCapability,
    DissolveCapability,
    FilterCapability,
    IntersectsCapability,
    ReprojectCapability,
    SpatialJoinCapability,
    UnionCapability,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def point_gdf() -> gpd.GeoDataFrame:
    """Simple GeoDataFrame with 3 points in EPSG:4326."""
    return gpd.GeoDataFrame(
        {
            "id": [1, 2, 3],
            "name": ["a", "b", "c"],
            "value": [10, 20, 30],
            "geometry": [
                Point(2.3522, 48.8566),  # Paris
                Point(2.3000, 48.8700),
                Point(2.4000, 48.9000),
            ],
        },
        crs="EPSG:4326",
    )


@pytest.fixture
def polygon_gdf() -> gpd.GeoDataFrame:
    """GeoDataFrame with 2 polygons in EPSG:4326."""
    poly1 = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
    poly2 = Polygon([(1, 0), (2, 0), (2, 1), (1, 1)])
    return gpd.GeoDataFrame(
        {
            "id": [1, 2],
            "category": ["A", "B"],
            "area_ha": [1.0, 2.0],
            "geometry": [poly1, poly2],
        },
        crs="EPSG:4326",
    )


@pytest.fixture
def mask_gdf() -> gpd.GeoDataFrame:
    """Small mask polygon."""
    mask = Polygon([(1.5, -0.5), (2.5, -0.5), (2.5, 1.5), (1.5, 1.5)])
    return gpd.GeoDataFrame({"geometry": [mask]}, crs="EPSG:4326")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_capabilities_registered(self):
        assert "buffer" in REGISTRY
        assert "union" in REGISTRY
        assert "reproject" in REGISTRY
        assert "filter" in REGISTRY
        assert "clip" in REGISTRY
        assert "dissolve" in REGISTRY

    def test_get_returns_instance(self):
        cap = get("buffer")
        assert isinstance(cap, BufferCapability)

    def test_get_unknown_raises(self):
        with pytest.raises(KeyError, match="No item named"):
            get("nonexistent_capability")

    def test_list_all_returns_schema(self):
        all_caps = list_all()
        names = [c["name"] for c in all_caps]
        assert "buffer" in names
        assert "filter" in names
        for cap_info in all_caps:
            assert "name" in cap_info
            assert "description" in cap_info
            assert "schema" in cap_info


# ---------------------------------------------------------------------------
# BufferCapability
# ---------------------------------------------------------------------------


class TestBufferCapability:
    def test_buffer_creates_polygons(self, point_gdf):
        cap = BufferCapability()
        result = cap.execute(point_gdf, distance=1000)
        assert len(result) == len(point_gdf)
        # After buffer, all geometries should be Polygon/MultiPolygon
        for geom in result.geometry:
            assert geom.geom_type in ("Polygon", "MultiPolygon")

    def test_buffer_preserves_crs(self, point_gdf):
        cap = BufferCapability()
        result = cap.execute(point_gdf, distance=500)
        assert result.crs == point_gdf.crs

    def test_buffer_zero_distance(self, point_gdf):
        cap = BufferCapability()
        result = cap.execute(point_gdf, distance=0)
        assert len(result) == len(point_gdf)

    def test_schema(self):
        cap = BufferCapability()
        schema = cap.get_schema()
        assert schema["type"] == "object"
        assert "distance" in schema["properties"]
        assert "distance_col" in schema["properties"]


# ---------------------------------------------------------------------------
# UnionCapability
# ---------------------------------------------------------------------------


class TestUnionCapability:
    def test_union_returns_single_row(self, polygon_gdf):
        cap = UnionCapability()
        result = cap.execute(polygon_gdf)
        assert len(result) == 1

    def test_union_preserves_crs(self, polygon_gdf):
        cap = UnionCapability()
        result = cap.execute(polygon_gdf)
        assert result.crs == polygon_gdf.crs

    def test_union_covers_all(self, polygon_gdf):
        cap = UnionCapability()
        result = cap.execute(polygon_gdf)
        original_area = polygon_gdf.to_crs("EPSG:3857").geometry.union_all().area
        result_area = result.to_crs("EPSG:3857").geometry.union_all().area
        assert abs(result_area - original_area) < 1.0  # tolerance 1 m²


# ---------------------------------------------------------------------------
# ReprojectCapability
# ---------------------------------------------------------------------------


class TestReprojectCapability:
    def test_reproject_changes_crs(self, point_gdf):
        cap = ReprojectCapability()
        result = cap.execute(point_gdf, target_crs="EPSG:2154")
        assert result.crs.to_epsg() == 2154

    def test_reproject_same_count(self, point_gdf):
        cap = ReprojectCapability()
        result = cap.execute(point_gdf, target_crs="EPSG:3857")
        assert len(result) == len(point_gdf)

    def test_schema(self):
        cap = ReprojectCapability()
        schema = cap.get_schema()
        assert "target_crs" in schema["properties"]
        assert "target_crs" in schema["required"]


# ---------------------------------------------------------------------------
# FilterCapability
# ---------------------------------------------------------------------------


class TestFilterCapability:
    def test_filter_by_value(self, point_gdf):
        cap = FilterCapability()
        result = cap.execute(point_gdf, expression="value > 15")
        assert len(result) == 2
        assert all(result["value"] > 15)

    def test_filter_empty_expression(self, point_gdf):
        cap = FilterCapability()
        result = cap.execute(point_gdf, expression="")
        assert len(result) == len(point_gdf)

    def test_filter_no_results(self, point_gdf):
        cap = FilterCapability()
        result = cap.execute(point_gdf, expression="value > 999")
        assert len(result) == 0

    def test_schema(self):
        cap = FilterCapability()
        schema = cap.get_schema()
        assert "expression" in schema["properties"]
        assert "spatial_predicate" in schema["properties"]
        assert "ref_filter" in schema["properties"]

    def test_ref_filter_narrows_reference(self):
        """ref_filter restricts ref_gdf before the spatial predicate runs."""
        cap = FilterCapability()
        # Input buildings: near river A (x<5), near river B (x>10)
        buildings = gpd.GeoDataFrame(
            {
                "id": [1, 2],
                "geometry": [Point(1.0, 0.0), Point(11.0, 0.0)],
            },
            crs="EPSG:3857",
        )
        # Reference lines: two rivers, we only want to buffer river A
        from shapely.geometry import LineString
        rivers = gpd.GeoDataFrame(
            {
                "name": ["A", "B"],
                "geometry": [
                    LineString([(0.0, -5.0), (0.0, 5.0)]),
                    LineString([(10.0, -5.0), (10.0, 5.0)]),
                ],
            },
            crs="EPSG:3857",
        )
        result = cap.execute(
            buildings,
            spatial_predicate="intersects",
            ref_gdf=rivers,
            ref_filter="name == 'A'",
            buffer_distance=2.0,
            crs_meters="EPSG:3857",
        )
        assert list(result["id"]) == [1]

    def test_invalid_spatial_predicate_raises_python(self, point_gdf):
        """Python strategy: invalid predicate raises via geopandas method lookup."""
        cap = FilterCapability()
        ref = gpd.GeoDataFrame(
            {"geometry": [Point(10.0, 10.0).buffer(50)]},
            crs=point_gdf.crs,
        )
        with pytest.raises(ValueError, match="Unknown spatial predicate: contians"):
            cap.execute(point_gdf, spatial_predicate="contians", ref_gdf=ref)

    def test_invalid_spatial_predicate_raises_duckdb(self):
        """DuckDB strategy: invalid predicate raises with valid list (no silent INTERSECTS fallback)."""
        from gispulse.capabilities.strategy import ExecutionContext
        from gispulse.capabilities.vector.filter import _FilterDuckDBStrategy

        ctx = ExecutionContext(
            engine=type("E", (), {"backend_name": "duckdb"})(),
            feature_count=1,
            params={"spatial_predicate": "contians", "ref_wkt": "POINT(0 0)"},
        )
        gdf = gpd.GeoDataFrame({"geometry": [Point(0, 0)]}, crs="EPSG:4326")
        with pytest.raises(ValueError, match="Invalid spatial_predicate: 'contians'"):
            _FilterDuckDBStrategy().execute(gdf, ctx)

    def test_invalid_spatial_predicate_raises_postgis(self):
        """PostGIS strategy: invalid predicate raises with valid list (no silent INTERSECTS fallback)."""
        from gispulse.capabilities.strategy import ExecutionContext
        from gispulse.capabilities.vector.filter import _FilterPostGISStrategy

        ctx = ExecutionContext(
            engine=type("E", (), {"backend_name": "postgis"})(),
            feature_count=1,
            params={
                "spatial_predicate": "covered_by_wrong",
                "ref_wkt": "POINT(0 0)",
                "table_name": "t",
            },
        )
        gdf = gpd.GeoDataFrame({"geometry": [Point(0, 0)]}, crs="EPSG:4326")
        with pytest.raises(ValueError, match="Invalid spatial_predicate: 'covered_by_wrong'"):
            _FilterPostGISStrategy().execute(gdf, ctx)


# ---------------------------------------------------------------------------
# ClipCapability
# ---------------------------------------------------------------------------


class TestClipCapability:
    def test_clip_reduces_features(self, polygon_gdf, mask_gdf):
        cap = ClipCapability()
        result = cap.execute(polygon_gdf, mask_gdf=mask_gdf)
        # Only the second polygon overlaps with the mask
        assert len(result) >= 1

    def test_clip_with_ref_gdf(self, polygon_gdf, mask_gdf):
        """ref_gdf (injected by engine) works the same as mask_gdf."""
        cap = ClipCapability()
        result = cap.execute(polygon_gdf, ref_gdf=mask_gdf)
        assert len(result) >= 1

    def test_clip_without_mask_raises(self, polygon_gdf):
        cap = ClipCapability()
        with pytest.raises(ValueError, match="reference layer"):
            cap.execute(polygon_gdf)

    def test_clip_preserves_crs(self, polygon_gdf, mask_gdf):
        cap = ClipCapability()
        result = cap.execute(polygon_gdf, mask_gdf=mask_gdf)
        assert result.crs == polygon_gdf.crs


# ---------------------------------------------------------------------------
# DissolveCapability
# ---------------------------------------------------------------------------


class TestDissolveCapability:
    def test_dissolve_all(self, polygon_gdf):
        cap = DissolveCapability()
        result = cap.execute(polygon_gdf, by=None)
        assert len(result) == 1

    def test_dissolve_by_column(self, polygon_gdf):
        cap = DissolveCapability()
        result = cap.execute(polygon_gdf, by="category")
        # Each category should produce one row
        assert len(result) == 2

    def test_schema(self):
        cap = DissolveCapability()
        schema = cap.get_schema()
        assert "by" in schema["properties"]


# ---------------------------------------------------------------------------
# CentroidCapability
# ---------------------------------------------------------------------------


class TestCentroidCapability:
    def test_centroid_creates_points(self, polygon_gdf):
        cap = CentroidCapability()
        result = cap.execute(polygon_gdf)
        assert len(result) == len(polygon_gdf)
        for geom in result.geometry:
            assert geom.geom_type == "Point"

    def test_centroid_preserves_attributes(self, polygon_gdf):
        cap = CentroidCapability()
        result = cap.execute(polygon_gdf)
        assert list(result["category"]) == list(polygon_gdf["category"])

    def test_centroid_preserves_crs(self, polygon_gdf):
        cap = CentroidCapability()
        result = cap.execute(polygon_gdf)
        assert result.crs == polygon_gdf.crs

    def test_centroid_registered(self):
        assert "centroid" in REGISTRY


# ---------------------------------------------------------------------------
# AreaLengthCapability
# ---------------------------------------------------------------------------


class TestAreaLengthCapability:
    def test_area_length_adds_columns(self, polygon_gdf):
        cap = AreaLengthCapability()
        result = cap.execute(polygon_gdf)
        assert "area_m2" in result.columns
        assert "length_m" in result.columns
        assert all(result["area_m2"] > 0)
        assert all(result["length_m"] > 0)

    def test_area_only(self, polygon_gdf):
        cap = AreaLengthCapability()
        result = cap.execute(polygon_gdf, compute_length=False)
        assert "area_m2" in result.columns
        assert "length_m" not in result.columns

    def test_custom_column_names(self, polygon_gdf):
        cap = AreaLengthCapability()
        result = cap.execute(polygon_gdf, area_col="surface", length_col="perimetre")
        assert "surface" in result.columns
        assert "perimetre" in result.columns

    def test_preserves_crs(self, polygon_gdf):
        cap = AreaLengthCapability()
        result = cap.execute(polygon_gdf)
        assert result.crs == polygon_gdf.crs

    def test_area_length_registered(self):
        assert "area_length" in REGISTRY


# ---------------------------------------------------------------------------
# IntersectsCapability — cross-layer
# ---------------------------------------------------------------------------


class TestIntersectsCapability:
    def test_intersects_with_wkt(self, point_gdf):
        cap = IntersectsCapability()
        # WKT polygon covering only the first point (Paris center)
        wkt = "POLYGON((2.35 48.85, 2.36 48.85, 2.36 48.86, 2.35 48.86, 2.35 48.85))"
        result = cap.execute(point_gdf, wkt=wkt)
        assert len(result) == 1

    def test_intersects_with_ref_gdf(self, polygon_gdf, mask_gdf):
        cap = IntersectsCapability()
        result = cap.execute(polygon_gdf, ref_gdf=mask_gdf)
        assert len(result) >= 1

    def test_intersects_no_ref_raises(self, point_gdf):
        cap = IntersectsCapability()
        with pytest.raises(ValueError, match="ref_layer"):
            cap.execute(point_gdf)

    def test_schema_has_ref_layer(self):
        cap = IntersectsCapability()
        schema = cap.get_schema()
        assert "ref_layer" in schema["properties"]


# ---------------------------------------------------------------------------
# SpatialJoinCapability
# ---------------------------------------------------------------------------


class TestSpatialJoinCapability:
    @pytest.fixture
    def ref_gdf(self) -> gpd.GeoDataFrame:
        """Reference layer with zone data overlapping some polygons."""
        zone = Polygon([(0.5, -0.5), (1.5, -0.5), (1.5, 1.5), (0.5, 1.5)])
        return gpd.GeoDataFrame(
            {"zone_name": ["Zone A"], "risk": ["high"]},
            geometry=[zone],
            crs="EPSG:4326",
        )

    def test_spatial_join_inner(self, polygon_gdf, ref_gdf):
        cap = SpatialJoinCapability()
        result = cap.execute(polygon_gdf, ref_gdf=ref_gdf, how="inner")
        # Both polygons intersect the zone
        assert len(result) == 2
        assert "zone_name" in result.columns
        assert "risk" in result.columns

    def test_spatial_join_left(self, polygon_gdf):
        """Left join preserves all rows, with None for non-matching."""
        cap = SpatialJoinCapability()
        # Use a small ref that only matches one polygon
        small_ref = gpd.GeoDataFrame(
            {"label": ["X"]},
            geometry=[Polygon([(0.2, 0.2), (0.8, 0.2), (0.8, 0.8), (0.2, 0.8)])],
            crs="EPSG:4326",
        )
        result = cap.execute(polygon_gdf, ref_gdf=small_ref, how="left")
        assert len(result) == 2  # both rows preserved
        assert "label" in result.columns

    def test_spatial_join_columns_filter(self, polygon_gdf, ref_gdf):
        """Only requested columns are kept from reference."""
        cap = SpatialJoinCapability()
        result = cap.execute(polygon_gdf, ref_gdf=ref_gdf, columns=["risk"])
        assert "risk" in result.columns
        assert "zone_name" not in result.columns

    def test_spatial_join_no_ref_raises(self, polygon_gdf):
        cap = SpatialJoinCapability()
        with pytest.raises(ValueError, match="reference layer"):
            cap.execute(polygon_gdf)

    def test_spatial_join_registered(self):
        assert "spatial_join" in REGISTRY

    def test_schema(self):
        cap = SpatialJoinCapability()
        schema = cap.get_schema()
        assert "ref_layer" in schema["properties"]
        assert "how" in schema["properties"]
        assert "predicate" in schema["properties"]
        assert "ref_layer" not in schema.get("required", [])


# ---------------------------------------------------------------------------
# Cross-layer engine integration
# ---------------------------------------------------------------------------


class TestCrossLayerEngine:
    def test_engine_resolves_ref_layer(self, polygon_gdf):
        from gispulse.core.models import Rule
        from gispulse.rules.engine import RuleEngine

        ref = gpd.GeoDataFrame(
            {"zone": ["flood"]},
            geometry=[Polygon([(0.5, -0.5), (1.5, -0.5), (1.5, 1.5), (0.5, 1.5)])],
            crs="EPSG:4326",
        )

        def resolver(name: str) -> gpd.GeoDataFrame:
            assert name == "flood_zones"
            return ref

        rule = Rule(
            name="test_intersects",
            capability="intersects",
            config={"ref_layer": "flood_zones"},
        )
        engine = RuleEngine()
        result = engine.apply(rule, polygon_gdf, layer_resolver=resolver)
        assert len(result) >= 1

    def test_engine_no_resolver_raises(self, polygon_gdf):
        from gispulse.core.models import Rule
        from gispulse.rules.engine import RuleEngine

        rule = Rule(
            name="test_intersects",
            capability="intersects",
            config={"ref_layer": "flood_zones"},
        )
        engine = RuleEngine()
        with pytest.raises(ValueError, match="layer_resolver"):
            engine.apply(rule, polygon_gdf)

    def test_apply_all_with_resolver(self, polygon_gdf):
        from gispulse.core.models import Rule
        from gispulse.rules.engine import RuleEngine

        ref = gpd.GeoDataFrame(
            {"risk": ["high"]},
            geometry=[Polygon([(0.5, -0.5), (1.5, -0.5), (1.5, 1.5), (0.5, 1.5)])],
            crs="EPSG:4326",
        )
        resolver = lambda name: ref

        rules = [
            Rule(name="join_risk", capability="spatial_join",
                 config={"ref_layer": "zones", "how": "left", "order": 0}),
            Rule(name="filter_high", capability="filter",
                 config={"expression": "risk == 'high'", "order": 1}),
        ]
        engine = RuleEngine()
        result = engine.apply_all(rules, polygon_gdf, layer_resolver=resolver)
        assert "risk" in result.columns

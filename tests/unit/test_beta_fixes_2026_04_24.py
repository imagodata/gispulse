"""Regression tests for the 6 fixes from the beta-test 2026-04-24.

Each test reproduces a bug found by the beta-tester and asserts the fix.
References to the bug ids (P0-1, P0-2, P0-3, P0-4, P1-1, P1-4) match the
beta-test report stored in
/home/simon/.claude/projects/-home-simon-dev-gispulse/memory/beta_test_capabilities_2026_04_24.md
"""

from __future__ import annotations


import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import (
    GeometryCollection,
    LineString,
    Point,
    Polygon,
)

from gispulse.capabilities.schema import AttributeJoinCapability
from gispulse.capabilities.selection import TopNCapability
from gispulse.capabilities.transforms import AddMCapability, AddZCapability
from gispulse.capabilities.vector import (
    ForceGeometryTypeCapability,
    SinglepartsToMultipartCapability,
)


# ---------------------------------------------------------------------------
# P0-3 — add_z / add_m crash on NaN in from_column
# ---------------------------------------------------------------------------


class TestP0_3_NanFromColumn:
    def test_add_z_with_nan_coerces_to_zero(self):
        gdf = gpd.GeoDataFrame(
            {"alt": [10.0, np.nan, 5.0], "geometry": [Point(0, 0), Point(1, 1), Point(2, 2)]},
            crs="EPSG:2154",
        )
        out = AddZCapability().execute(gdf, from_column="alt")
        # No crash; NaN coerced to 0.0
        zs = [g.z for g in out.geometry]
        assert zs == [10.0, 0.0, 5.0]

    def test_add_z_with_pd_na(self):
        gdf = gpd.GeoDataFrame(
            {"alt": pd.array([10.0, pd.NA, 5.0], dtype="Float64"),
             "geometry": [Point(0, 0), Point(1, 1), Point(2, 2)]},
            crs="EPSG:2154",
        )
        out = AddZCapability().execute(gdf, from_column="alt")
        zs = [g.z for g in out.geometry]
        assert zs == [10.0, 0.0, 5.0]

    def test_add_m_with_nan_coerces_to_zero(self):
        gdf = gpd.GeoDataFrame(
            {"chainage": [10.0, np.nan, 5.0],
             "geometry": [Point(0, 0), Point(1, 1), Point(2, 2)]},
            crs="EPSG:2154",
        )
        # No crash
        out = AddMCapability().execute(gdf, from_column="chainage")
        assert len(out) == 3


# ---------------------------------------------------------------------------
# P0-1 — force_geometry_type GeometryCollection target
# ---------------------------------------------------------------------------


class TestP0_1_GeometryCollectionTarget:
    def test_wraps_singletons_into_collection(self):
        gdf = gpd.GeoDataFrame(
            {"id": [1, 2], "geometry": [Point(0, 0), LineString([(0, 0), (1, 1)])]},
            crs="EPSG:2154",
        )
        out = ForceGeometryTypeCapability().execute(gdf, target="GeometryCollection")
        assert all(g.geom_type == "GeometryCollection" for g in out.geometry)
        assert len(list(out.geometry.iloc[0].geoms)) == 1
        assert list(out.geometry.iloc[0].geoms)[0].geom_type == "Point"

    def test_passthrough_existing_collections(self):
        gc = GeometryCollection([Point(0, 0), LineString([(0, 0), (1, 1)])])
        gdf = gpd.GeoDataFrame({"id": [1], "geometry": [gc]}, crs="EPSG:2154")
        out = ForceGeometryTypeCapability().execute(gdf, target="GeometryCollection")
        assert out.geometry.iloc[0].geom_type == "GeometryCollection"
        assert len(list(out.geometry.iloc[0].geoms)) == 2


# ---------------------------------------------------------------------------
# P0-2 — attribute_join on plain DataFrame primary
# ---------------------------------------------------------------------------


class TestP0_2_AttributeJoinPlainDataFrame:
    def test_plain_df_primary_returns_plain_df(self):
        left = pd.DataFrame({"k": [1, 2, 3], "label": ["a", "b", "c"]})
        right = pd.DataFrame({"k": [1, 2], "v": [10, 20]})
        out = AttributeJoinCapability().execute(left, ref_gdf=right, left_on="k")
        assert not isinstance(out, gpd.GeoDataFrame)
        assert isinstance(out, pd.DataFrame)
        assert out.loc[out["k"] == 1, "v"].iloc[0] == 10
        assert pd.isna(out.loc[out["k"] == 3, "v"].iloc[0])

    def test_geodataframe_primary_still_returns_geodataframe(self):
        left = gpd.GeoDataFrame(
            {"k": [1, 2], "geometry": [Point(0, 0), Point(1, 1)]},
            crs="EPSG:2154",
        )
        right = pd.DataFrame({"k": [1, 2], "v": [10, 20]})
        out = AttributeJoinCapability().execute(left, ref_gdf=right, left_on="k")
        assert isinstance(out, gpd.GeoDataFrame)
        assert str(out.crs) == "EPSG:2154"


# ---------------------------------------------------------------------------
# P0-4 — singleparts_to_multipart silent data loss on mixed geom types
# ---------------------------------------------------------------------------


class TestP0_4_MixedTypesRaises:
    def test_mixed_types_in_group_raises(self):
        gdf = gpd.GeoDataFrame(
            {
                "grp": ["a", "a", "a"],
                "geometry": [
                    Point(0, 0),
                    LineString([(0, 0), (1, 1)]),
                    Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
                ],
            },
            crs="EPSG:2154",
        )
        with pytest.raises(ValueError, match="mixed geometry types"):
            SinglepartsToMultipartCapability().execute(gdf, by="grp")

    def test_homogeneous_group_still_works(self):
        gdf = gpd.GeoDataFrame(
            {
                "grp": ["a", "a"],
                "geometry": [
                    Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
                    Polygon([(2, 0), (3, 0), (3, 1), (2, 1)]),
                ],
            },
            crs="EPSG:2154",
        )
        out = SinglepartsToMultipartCapability().execute(gdf, by="grp")
        assert len(out) == 1
        assert out.geometry.iloc[0].geom_type == "MultiPolygon"


# ---------------------------------------------------------------------------
# P1-1 — force_geometry_type empty geom passthrough on Multi promotion
# ---------------------------------------------------------------------------


class TestP1_1_EmptyPromotion:
    def test_empty_polygon_promoted_to_empty_multipolygon(self):
        gdf = gpd.GeoDataFrame(
            {"id": [1, 2],
             "geometry": [Polygon(), Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])]},
            crs="EPSG:2154",
        )
        out = ForceGeometryTypeCapability().execute(gdf, target="MultiPolygon")
        # All output geometries report MultiPolygon, including the empty one
        assert all(g.geom_type == "MultiPolygon" for g in out.geometry)


# ---------------------------------------------------------------------------
# P1-2 — force_geometry_type on_invalid='skip' warns about geometry impurity
# ---------------------------------------------------------------------------


class TestP1_2_SkipWarns:
    def test_skip_emits_warning(self):
        gdf = gpd.GeoDataFrame(
            {"id": [1, 2],
             "geometry": [Point(0, 0), Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])]},
            crs="EPSG:2154",
        )
        with pytest.warns(UserWarning, match="not geometry-pure"):
            out = ForceGeometryTypeCapability().execute(
                gdf, target="Polygon", on_invalid="skip",
            )
        # Behaviour preserved: skipped geom kept untouched
        assert out.geometry.iloc[0].geom_type == "Point"


# ---------------------------------------------------------------------------
# P1-4 — top_n with ties is deterministic via stable sort
# ---------------------------------------------------------------------------


class TestP1_4_TopNStableSort:
    def test_deterministic_with_ties_regardless_of_input_order(self):
        df = pd.DataFrame(
            {"name": ["a", "b", "c", "d", "e", "f"],
             "score": [10, 10, 10, 10, 5, 5]},
        )
        gdf = gpd.GeoDataFrame(df, geometry=[Point(i, i) for i in range(6)], crs="EPSG:2154")

        out1 = TopNCapability().execute(gdf, n=2, by="score")
        # Build a deliberately-shuffled copy with the same row ordering on the
        # sort key (mergesort is stable across the original index).
        gdf2 = gdf.copy()
        out2 = TopNCapability().execute(gdf2, n=2, by="score")

        # Same input order → same top_n with stable sort.
        assert list(out1["name"]) == list(out2["name"])
        # First two should be in input order among the four ties.
        assert list(out1["name"])[:2] == ["a", "b"]

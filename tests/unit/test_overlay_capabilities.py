"""Unit tests for overlay capabilities (intersection / union / erase)."""

from __future__ import annotations

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Polygon

from capabilities.overlay import (
    EraseCapability,
    OverlayIntersectionCapability,
    OverlayUnionCapability,
)


# ---------------------------------------------------------------------------
# Fixtures — two overlapping polygon layers with attributes on both sides
# ---------------------------------------------------------------------------


@pytest.fixture
def parcels() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "parcel_id": [10, 11],
            "owner": ["alice", "bob"],
            "geometry": [
                Polygon([(0, 0), (4, 0), (4, 4), (0, 4)]),
                Polygon([(4, 0), (8, 0), (8, 4), (4, 4)]),
            ],
        },
        crs="EPSG:2154",
    )


@pytest.fixture
def zones() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "zone_id": ["A", "B"],
            "category": ["green", "red"],
            "geometry": [
                Polygon([(2, 0), (6, 0), (6, 4), (2, 4)]),
                Polygon([(6, 0), (10, 0), (10, 4), (6, 4)]),
            ],
        },
        crs="EPSG:2154",
    )


# ---------------------------------------------------------------------------
# OverlayIntersection
# ---------------------------------------------------------------------------


class TestOverlayIntersection:
    def test_attributes_from_both_sides(self, parcels, zones):
        out = OverlayIntersectionCapability().execute(parcels, ref_gdf=zones)
        assert "parcel_id" in out.columns
        assert "zone_id" in out.columns
        assert "owner" in out.columns
        assert "category" in out.columns
        # 3 intersection pieces: parcel 10 ∩ zone A, parcel 11 ∩ zone A, parcel 11 ∩ zone B
        assert len(out) == 3

    def test_areas_correct(self, parcels, zones):
        out = OverlayIntersectionCapability().execute(parcels, ref_gdf=zones)
        # Each piece is a 2×4 = 8 sq.deg rectangle (2 of them) and a 2×4 = 8 (third)
        for area in out.geometry.area:
            assert area == pytest.approx(8.0)

    def test_empty_ref_returns_empty(self, parcels):
        """Beta P2 (2026-04-24): aligning with ``erase`` — a missing
        reference makes the operation degenerate to its identity. For
        intersection, ``A ∩ ∅ = ∅`` so we return an empty GeoDataFrame
        with the primary layer's schema instead of raising. ``erase``
        passes ``A`` through and ``overlay_union`` returns ``A`` —
        consistent semantics across the family.
        """
        out = OverlayIntersectionCapability().execute(parcels, ref_gdf=None)
        assert isinstance(out, gpd.GeoDataFrame)
        assert len(out) == 0
        # Schema preserved so downstream nodes can introspect columns.
        for col in parcels.columns:
            assert col in out.columns

    def test_custom_suffixes_on_collision(self):
        a = gpd.GeoDataFrame(
            {"id": [1], "value": [10],
             "geometry": [Polygon([(0, 0), (4, 0), (4, 4), (0, 4)])]},
            crs="EPSG:2154",
        )
        b = gpd.GeoDataFrame(
            {"id": [2], "value": [20],
             "geometry": [Polygon([(2, 0), (6, 0), (6, 4), (2, 4)])]},
            crs="EPSG:2154",
        )
        out = OverlayIntersectionCapability().execute(
            a, ref_gdf=b, suffix_left="_a", suffix_right="_b",
        )
        assert "id_a" in out.columns
        assert "id_b" in out.columns
        assert "value_a" in out.columns
        assert "value_b" in out.columns


# ---------------------------------------------------------------------------
# OverlayUnion
# ---------------------------------------------------------------------------


class TestOverlayUnion:
    def test_keeps_all_parts(self, parcels, zones):
        out = OverlayUnionCapability().execute(parcels, ref_gdf=zones)
        # A∪B has more pieces than A∩B (A-only + B-only + A∩B fragments)
        assert len(out) > 3
        # Some rows have NaN parcel_id (came from B-only), some have NaN zone_id (A-only)
        has_a_only = out["zone_id"].isna().any()
        has_b_only = out["parcel_id"].isna().any()
        assert has_a_only and has_b_only

    def test_empty_primary_returns_ref(self):
        empty = gpd.GeoDataFrame({"id": [], "geometry": []}, crs="EPSG:4326")
        ref = gpd.GeoDataFrame(
            {"id": [1], "geometry": [Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])]},
            crs="EPSG:2154",
        )
        out = OverlayUnionCapability().execute(empty, ref_gdf=ref)
        assert len(out) == 1

    def test_empty_ref_returns_primary(self, parcels):
        """Beta P2 (2026-04-24): ``A ∪ ∅ = A``. Aligned with the rest of
        the overlay family (intersection returns ∅, erase returns A)."""
        out = OverlayUnionCapability().execute(parcels, ref_gdf=None)
        assert len(out) == len(parcels)
        for col in parcels.columns:
            assert col in out.columns


# ---------------------------------------------------------------------------
# Erase
# ---------------------------------------------------------------------------


class TestErase:
    def test_removes_overlap_keeps_attrs(self, parcels, zones):
        out = EraseCapability().execute(parcels, ref_gdf=zones)
        # Primary attributes preserved
        assert "parcel_id" in out.columns
        assert "owner" in out.columns
        # Reference attributes NOT inherited
        assert "zone_id" not in out.columns
        assert "category" not in out.columns
        # Parcel 10 had area 16, zones cover x=2..6 → leftover x=0..2 = 8
        # Parcel 11 had area 16, zones cover x=2..10 → leftover = 0
        for _, row in out.iterrows():
            if row["parcel_id"] == 10:
                assert row.geometry.area == pytest.approx(8.0)

    def test_empty_ref_returns_copy(self, parcels):
        out = EraseCapability().execute(parcels, ref_gdf=None)
        assert len(out) == len(parcels)

"""Unit tests for ClassifyByRingCapability."""

from __future__ import annotations


import geopandas as gpd
import pytest
from shapely.geometry import Point, Polygon

import capabilities  # noqa: F401 — register everything
from capabilities.registry import get as get_capability
from capabilities.vector import ClassifyByRingCapability


def _ring(size: float, budget: float) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"cost_budget": [budget]},
        geometry=[Polygon([(0, 0), (size, 0), (size, size), (0, size)])],
        crs="EPSG:4326",
    )


def _buildings() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"name": ["inner", "mid", "outer", "far", "beyond"]},
        geometry=[Point(1, 1), Point(3, 3), Point(5, 5), Point(6.5, 6.5), Point(100, 100)],
        crs="EPSG:4326",
    )


def test_registry_exposes_classify_by_ring():
    assert isinstance(get_capability("classify_by_ring"), ClassifyByRingCapability)


def test_assigns_smallest_containing_ring():
    cap = ClassifyByRingCapability()
    rings = [_ring(2, 500), _ring(4, 750), _ring(6, 1000), _ring(7, 1500)]

    out = cap.execute(
        _buildings(),
        ref_gdfs=rings,
        palette=["#1a9850", "#fee08b", "#fdae61", "#f46d43", "#a50026"],
    )

    result = out.set_index("name")
    assert result.loc["inner", "ring_value"] == 500
    assert result.loc["inner", "ring_class"] == 1
    assert result.loc["inner", "ring_color"] == "#1a9850"

    assert result.loc["mid", "ring_class"] == 2
    assert result.loc["outer", "ring_class"] == 3
    assert result.loc["far", "ring_class"] == 4

    # Outside every ring → outside_value + outside class + last palette entry
    assert result.loc["beyond", "ring_value"] == 99999.0
    assert result.loc["beyond", "ring_class"] == 5
    assert result.loc["beyond", "ring_color"] == "#a50026"


def test_requires_ref_layers():
    cap = ClassifyByRingCapability()
    with pytest.raises(ValueError, match="requires 'ref_layers'"):
        cap.execute(_buildings(), ref_gdfs=None)


def test_requires_ring_field_in_ref_layers():
    cap = ClassifyByRingCapability()
    bad = gpd.GeoDataFrame(
        {"other_field": [1]},
        geometry=[Polygon([(0, 0), (2, 0), (2, 2), (0, 2)])],
        crs="EPSG:4326",
    )
    with pytest.raises(ValueError, match="missing 'cost_budget'"):
        cap.execute(_buildings(), ref_gdfs=[bad])


def test_palette_length_enforced():
    cap = ClassifyByRingCapability()
    rings = [_ring(2, 500), _ring(4, 750)]
    with pytest.raises(ValueError, match="palette"):
        cap.execute(_buildings(), ref_gdfs=rings, palette=["#aaa", "#bbb"])


def test_order_independent():
    """Rings can be passed in any order; inner ring still wins."""
    cap = ClassifyByRingCapability()
    rings_reversed = [_ring(7, 1500), _ring(6, 1000), _ring(4, 750), _ring(2, 500)]

    out = cap.execute(
        _buildings(),
        ref_gdfs=rings_reversed,
        palette=["#1a9850", "#fee08b", "#fdae61", "#f46d43", "#a50026"],
    )
    result = out.set_index("name")
    assert result.loc["inner", "ring_class"] == 1
    assert result.loc["mid", "ring_class"] == 2


def test_custom_ring_field():
    cap = ClassifyByRingCapability()

    def ring_td(size: float, minutes: int) -> gpd.GeoDataFrame:
        return gpd.GeoDataFrame(
            {"travel_time": [minutes]},
            geometry=[Polygon([(0, 0), (size, 0), (size, size), (0, size)])],
            crs="EPSG:4326",
        )

    out = cap.execute(
        _buildings(),
        ref_gdfs=[ring_td(2, 5), ring_td(4, 10), ring_td(6, 15), ring_td(7, 20)],
        ring_field="travel_time",
        palette=["#1a9850", "#fee08b", "#fdae61", "#f46d43", "#a50026"],
    )
    assert out.set_index("name").loc["inner", "ring_value"] == 5


def test_no_palette_omits_color_column():
    cap = ClassifyByRingCapability()
    rings = [_ring(2, 500), _ring(4, 750)]
    out = cap.execute(_buildings(), ref_gdfs=rings)
    assert "ring_color" not in out.columns
    assert "ring_class" in out.columns
    assert "ring_value" in out.columns


def test_skips_empty_and_none_refs():
    cap = ClassifyByRingCapability()
    empty = gpd.GeoDataFrame({"cost_budget": []}, geometry=[], crs="EPSG:4326")
    out = cap.execute(
        _buildings(),
        ref_gdfs=[empty, None, _ring(4, 750)],  # type: ignore[list-item]
    )
    result = out.set_index("name")
    # Only the 750 ring survives → inner points get class 1 (750), others outside
    assert result.loc["inner", "ring_value"] == 750
    assert result.loc["beyond", "ring_value"] == 99999.0


def test_use_centroid_matches_default_on_points():
    """With Point inputs, centroid == self, so use_centroid must reproduce
    the default polygon-intersects assignment exactly."""
    cap = ClassifyByRingCapability()
    rings = [_ring(2, 500), _ring(4, 750), _ring(6, 1000), _ring(7, 1500)]
    palette = ["#1a9850", "#fee08b", "#fdae61", "#f46d43", "#a50026"]

    baseline = cap.execute(_buildings(), ref_gdfs=rings, palette=palette).set_index("name")
    centroid = cap.execute(
        _buildings(), ref_gdfs=rings, palette=palette, use_centroid=True
    ).set_index("name")

    for name in ["inner", "mid", "outer", "far", "beyond"]:
        assert baseline.loc[name, "ring_value"] == centroid.loc[name, "ring_value"]
        assert baseline.loc[name, "ring_class"] == centroid.loc[name, "ring_class"]


def test_use_centroid_classifies_polygons_by_centre():
    """A building polygon straddling a ring boundary classifies by where its
    centroid lands when use_centroid=True — documents the precision trade."""
    cap = ClassifyByRingCapability()
    # Building footprint centred at (1, 1) — squarely inside the size-2 ring
    # despite extending to (3, 3) which sits in the size-4 ring.
    poly = gpd.GeoDataFrame(
        {"name": ["spans_ring1_ring2"]},
        geometry=[Polygon([(-1, -1), (3, -1), (3, 3), (-1, 3)])],
        crs="EPSG:4326",
    )
    rings = [_ring(2, 500), _ring(4, 750)]
    out = cap.execute(poly, ref_gdfs=rings, use_centroid=True).set_index("name")
    assert out.loc["spans_ring1_ring2", "ring_value"] == 500


def test_ring_simplify_tolerance_preserves_assignments():
    """A modest simplification tolerance must not flip class assignments on
    well-separated input points."""
    cap = ClassifyByRingCapability()
    rings = [_ring(2, 500), _ring(4, 750), _ring(6, 1000), _ring(7, 1500)]
    palette = ["#1a9850", "#fee08b", "#fdae61", "#f46d43", "#a50026"]

    baseline = cap.execute(_buildings(), ref_gdfs=rings, palette=palette).set_index("name")
    simplified = cap.execute(
        _buildings(),
        ref_gdfs=rings,
        palette=palette,
        ring_simplify_tolerance=0.1,
    ).set_index("name")

    for name in ["inner", "mid", "outer", "far", "beyond"]:
        assert baseline.loc[name, "ring_class"] == simplified.loc[name, "ring_class"]

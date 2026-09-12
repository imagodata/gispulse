"""Unit tests for the consolidate-networks capabilities (cn_* family).

Port of the QGIS *Consolidate Networks* plugin. Fixtures use EPSG:2154 and the
capabilities are called with ``crs_meters="EPSG:2154"`` so no reprojection
happens and coordinate assertions stay exact.
"""

from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import LineString, Point

from gispulse.capabilities.network_consolidate import (
    CnCalculateDbscanCapability,
    CnConsolidateWithDbscanCapability,
    CnEndpointsSnappingCapability,
    CnEndpointsTrimExtendCapability,
    CnHubSnappingCapability,
    CnMakeIntersectionsVertexesCapability,
    CnSnapEndpointsToLayerCapability,
    CnSnapHubsToLayerCapability,
)

CRS = "EPSG:2154"


def _coords(geom):
    return [tuple(round(v, 6) for v in c[:2]) for c in geom.coords]


def _wkb(gdf):
    return [g.wkb for g in gdf.geometry]


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_all_cn_caps_registered():
    from gispulse.capabilities import registry

    names = {c["name"] for c in registry.list_all()}
    expected = {
        "cn_calculate_dbscan",
        "cn_consolidate_with_dbscan",
        "cn_make_intersections_vertexes",
        "cn_endpoints_trim_extend",
        "cn_endpoints_snapping",
        "cn_hub_snapping",
        "cn_snap_hubs_to_layer",
        "cn_snap_endpoints_to_layer",
    }
    assert expected <= names


def test_ref_layer_not_required_in_schema():
    for cap in (CnSnapHubsToLayerCapability(), CnSnapEndpointsToLayerCapability()):
        schema = cap.get_schema()
        assert "ref_layer" in schema["properties"]
        assert "ref_layer" not in schema.get("required", [])


# ---------------------------------------------------------------------------
# 1. cn_calculate_dbscan
# ---------------------------------------------------------------------------


def test_calculate_dbscan_adds_cluster_columns():
    # A dense bundle of overlapping short lines -> at least one cluster.
    lines = [LineString([(0, float(i) * 0.1), (5, float(i) * 0.1)]) for i in range(6)]
    gdf = gpd.GeoDataFrame({"id": list(range(6)), "geometry": lines}, crs=CRS)
    before = _wkb(gdf)

    out = CnCalculateDbscanCapability().execute(
        gdf, points_dbscan_threshold_distance=0.5, crs_meters=CRS
    )

    assert isinstance(out, gpd.GeoDataFrame)
    assert "CLUSTER_ID" in out.columns
    assert "CLUSTER_SIZE" in out.columns
    assert "__cn_eid__" not in out.columns
    assert str(out.crs) == CRS
    assert _wkb(gdf) == before  # input not mutated


def test_calculate_dbscan_empty():
    gdf = gpd.GeoDataFrame({"id": [], "geometry": []}, crs=CRS)
    out = CnCalculateDbscanCapability().execute(gdf, crs_meters=CRS)
    assert isinstance(out, gpd.GeoDataFrame)
    assert out.empty


# ---------------------------------------------------------------------------
# 2. cn_consolidate_with_dbscan
# ---------------------------------------------------------------------------


def test_consolidate_requires_cluster_id():
    gdf = gpd.GeoDataFrame(
        {"id": [1], "geometry": [LineString([(0, 0), (10, 0)])]}, crs=CRS
    )
    with pytest.raises(ValueError, match="CLUSTER_ID"):
        CnConsolidateWithDbscanCapability().execute(gdf, crs_meters=CRS)


def test_consolidate_snaps_across_clusters():
    gdf = gpd.GeoDataFrame(
        {
            "id": [1, 2],
            "CLUSTER_ID": [1, 2],
            "geometry": [
                LineString([(0, 0), (10, 0)]),
                LineString([(11, 0.5), (20, 0.5)]),
            ],
        },
        crs=CRS,
    )
    before = _wkb(gdf)
    out = CnConsolidateWithDbscanCapability().execute(
        gdf, buffer_dbscan=5.0, crs_meters=CRS
    )
    # line 1's end should have snapped onto line 2's near vertex (11, 0.5)
    assert _coords(out.geometry.iloc[0])[-1] == (11.0, 0.5)
    assert _wkb(gdf) == before


# ---------------------------------------------------------------------------
# 3. cn_make_intersections_vertexes
# ---------------------------------------------------------------------------


def test_make_intersections_inserts_vertex():
    # Horizontal line with no vertex at (5,0); a second line touches it there.
    gdf = gpd.GeoDataFrame(
        {
            "id": [1, 2],
            "geometry": [
                LineString([(0, 0), (10, 0)]),
                LineString([(5, 0), (5, 10)]),
            ],
        },
        crs=CRS,
    )
    before = _wkb(gdf)
    out = CnMakeIntersectionsVertexesCapability().execute(gdf, crs_meters=CRS)

    horiz = out[out["id"] == 1].geometry.iloc[0]
    coords = _coords(horiz)
    assert (5.0, 0.0) in coords
    assert len(coords) == 3
    assert _wkb(gdf) == before


# ---------------------------------------------------------------------------
# 4. cn_endpoints_trim_extend
# ---------------------------------------------------------------------------


def test_trim_extend_returns_lines_unmutated():
    # A dangling line short of a perpendicular barrier within the extend buffer.
    gdf = gpd.GeoDataFrame(
        {
            "id": [1, 2],
            "geometry": [
                LineString([(0, 0), (8, 0)]),        # dangles, gap to barrier
                LineString([(10, -5), (10, 5)]),     # vertical barrier at x=10
            ],
        },
        crs=CRS,
    )
    before = _wkb(gdf)
    out = CnEndpointsTrimExtendCapability().execute(
        gdf,
        buffer_extend=5.0,
        buffer_trim=1.0,
        preferred_behavior_for_starting_extremities=2,  # None for start
        preferred_behavior_for_ending_extremities=1,    # Extend the end
        hausdorff_distance_limit=0.0,
        angular_limit_of_parallel_geometries=10.0,
        crs_meters=CRS,
    )
    assert isinstance(out, gpd.GeoDataFrame)
    assert str(out.crs) == CRS
    assert all(g.geom_type == "LineString" for g in out.geometry)
    assert _wkb(gdf) == before
    # The dangling end should have been pushed out toward the barrier (x grew).
    end_x = _coords(out.geometry.iloc[0])[-1][0]
    assert end_x >= 8.0


# ---------------------------------------------------------------------------
# 5. cn_endpoints_snapping
# ---------------------------------------------------------------------------


def test_endpoints_snapping_closes_gap():
    gdf = gpd.GeoDataFrame(
        {
            "id": [1, 2],
            "geometry": [
                LineString([(0, 0), (10, 0)]),
                LineString([(11, 1), (20, 6)]),
            ],
        },
        crs=CRS,
    )
    before = _wkb(gdf)
    out = CnEndpointsSnappingCapability().execute(
        gdf,
        buffer_endpoints_snapping=5.0,
        hausdorff_distance_limit=0.0,
        crs_meters=CRS,
    )
    # Endpoints that were ~1.41 m apart should now coincide.
    ends = []
    for g in out.geometry:
        c = _coords(g)
        ends.append(c[0])
        ends.append(c[-1])
    min_gap = min(
        Point(a).distance(Point(b))
        for i, a in enumerate(ends)
        for b in ends[i + 1 :]
    )
    assert min_gap == pytest.approx(0.0, abs=1e-6)
    assert _wkb(gdf) == before


# ---------------------------------------------------------------------------
# 6. cn_hub_snapping
# ---------------------------------------------------------------------------


def test_hub_snapping_merges_endpoints():
    gdf = gpd.GeoDataFrame(
        {
            "id": [1, 2, 3],
            "geometry": [
                LineString([(0, 0), (10, 0)]),
                LineString([(10.5, 0.5), (20, 3)]),
                LineString([(10.2, -0.4), (20, -6)]),
            ],
        },
        crs=CRS,
    )
    before = _wkb(gdf)
    out = CnHubSnappingCapability().execute(
        gdf, buffer_hub_snapping=1.5, crs_meters=CRS
    )
    # The three clustered endpoints near (10,0) should collapse onto one hub.
    a_end = _coords(out[out["id"] == 1].geometry.iloc[0])[-1]
    b_start = _coords(out[out["id"] == 2].geometry.iloc[0])[0]
    c_start = _coords(out[out["id"] == 3].geometry.iloc[0])[0]
    assert a_end == b_start == c_start
    assert _wkb(gdf) == before


# ---------------------------------------------------------------------------
# 7. cn_snap_hubs_to_layer
# ---------------------------------------------------------------------------


def test_snap_hubs_to_layer_requires_ref():
    gdf = gpd.GeoDataFrame(
        {"id": [1], "geometry": [LineString([(0, 0), (10, 0)])]}, crs=CRS
    )
    with pytest.raises(ValueError, match="reference layer"):
        CnSnapHubsToLayerCapability().execute(gdf, ref_gdf=None, crs_meters=CRS)


def test_snap_hubs_to_layer_snaps_to_reference():
    gdf = gpd.GeoDataFrame(
        {"id": [1], "geometry": [LineString([(0, 0), (10, 0)])]}, crs=CRS
    )
    ref = gpd.GeoDataFrame(
        {
            "geometry": [
                Point(10.0, 0.0),
                Point(10.4, 0.3),
                Point(9.7, -0.2),
            ]
        },
        crs=CRS,
    )
    before = _wkb(gdf)
    out = CnSnapHubsToLayerCapability().execute(
        gdf, ref_gdf=ref, buffer_hub_snapping=1.5, crs_meters=CRS
    )
    end = _coords(out.geometry.iloc[0])[-1]
    ref_pts = {(10.0, 0.0), (10.4, 0.3), (9.7, -0.2)}
    assert end in ref_pts  # snapped onto an existing reference vertex
    assert _wkb(gdf) == before


# ---------------------------------------------------------------------------
# 8. cn_snap_endpoints_to_layer
# ---------------------------------------------------------------------------


def test_snap_endpoints_to_layer_requires_ref():
    gdf = gpd.GeoDataFrame(
        {"id": [1], "geometry": [LineString([(0, 0), (10, 0)])]}, crs=CRS
    )
    with pytest.raises(ValueError, match="reference layer"):
        CnSnapEndpointsToLayerCapability().execute(gdf, ref_gdf=None, crs_meters=CRS)


def test_snap_endpoints_to_layer_snaps_endpoint():
    gdf = gpd.GeoDataFrame(
        {"id": [1], "geometry": [LineString([(0, 0), (10, 0)])]}, crs=CRS
    )
    ref = gpd.GeoDataFrame(
        {"geometry": [LineString([(11, 1), (20, 8)])]}, crs=CRS
    )
    before = _wkb(gdf)
    out = CnSnapEndpointsToLayerCapability().execute(
        gdf,
        ref_gdf=ref,
        buffer_endpoints_snapping=5.0,
        hausdorff_distance_limit=0.0,
        crs_meters=CRS,
    )
    end = _coords(out.geometry.iloc[0])[-1]
    assert end == (11.0, 1.0)  # snapped onto the reference endpoint
    assert _wkb(gdf) == before

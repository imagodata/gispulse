import geopandas as gpd
import pytest
from shapely.geometry import LineString, Polygon, box

from gispulse.capabilities.vector.polygon_partition import partition_polygons
from gispulse.capabilities.vector.reconcile_knw_structures import reconcile_knw_structures


def wbn(*ids_and_boxes):
    return gpd.GeoDataFrame(
        {"OIDN": [str(i) for i, _ in ids_and_boxes]},
        geometry=[b for _, b in ids_and_boxes],
        crs=31370,
    )


def knw(*type_and_boxes):
    return gpd.GeoDataFrame(
        {
            "OIDN": [str(i) for i in range(len(type_and_boxes))],
            "TYPE": [t for t, _ in type_and_boxes],
        },
        geometry=[b for _, b in type_and_boxes],
        crs=31370,
    )


def faces_of(wbn_frame, wgo_frame=None):
    if wgo_frame is None:
        wgo_frame = gpd.GeoDataFrame({"OIDN": []}, geometry=[], crs=31370)
    faces, _ = partition_polygons(
        wbn_frame,
        wgo_frame,
        polygon_id="OIDN",
        boundary_id="OIDN",
        coverage_bounds=(-100, -100, 100, 100),
        area_tolerance_m2=1e-6,
        length_tolerance_m=1e-6,
    )
    return faces


def reconcile(faces, wbn_frame, knw_frame, **overrides):
    return reconcile_knw_structures(
        faces, wbn_frame, knw_frame, **{"boundary_tolerance_m": 1e-3, **overrides}
    )


def test_corridor_touching_a_bridge_is_flagged_bridge():
    road = wbn((1, box(0, 0, 10, 10)))
    bridge = knw((1, box(10, 0, 12, 10)))  # shares the x=10 edge with the road
    result, report = reconcile(faces_of(road), road, bridge)
    assert set(result.structure_proximity) == {"bridge"}
    assert report["flagged_faces"] == len(result)
    assert report["flagged_corridors"] == 1


def test_corridor_touching_a_tunnel_entrance_is_flagged_tunnel():
    road = wbn((1, box(0, 0, 10, 10)))
    tunnel = knw((12, box(10, 0, 12, 10)))
    result, _ = reconcile(faces_of(road), road, tunnel)
    assert set(result.structure_proximity) == {"tunnel"}


def test_corridor_touching_both_a_bridge_and_a_tunnel_reports_both():
    road = wbn((1, box(0, 0, 10, 10)))
    structures = knw((1, box(10, 0, 12, 10)), (12, box(-2, 0, 0, 10)))
    result, _ = reconcile(faces_of(road), road, structures)
    assert set(result.structure_proximity) == {"bridge,tunnel"}


def test_corridor_touching_nothing_is_none():
    road = wbn((1, box(0, 0, 10, 10)))
    far_bridge = knw((1, box(1000, 1000, 1002, 1010)))
    result, report = reconcile(faces_of(road), road, far_bridge)
    assert set(result.structure_proximity) == {"none"}
    assert report["flagged_faces"] == 0
    assert report["flagged_corridors"] == 0


def test_non_structure_knw_types_touching_wbn_do_not_flag_anything():
    # TYPE=5 (pijler/pillar) is real, common GRB data that touches WBN
    # constantly (fences, street furniture) without being a bridge or tunnel.
    road = wbn((1, box(0, 0, 10, 10)))
    pillar = knw((5, box(10, 0, 10.3, 0.3)))
    result, _ = reconcile(faces_of(road), road, pillar)
    assert set(result.structure_proximity) == {"none"}


def test_only_faces_in_the_touched_corridor_are_flagged_not_the_whole_bundle():
    touched = wbn((1, box(0, 0, 10, 10)))
    untouched = wbn((2, box(100, 100, 110, 110)))
    both = gpd.GeoDataFrame(
        pd_concat(touched, untouched),
        geometry=list(touched.geometry) + list(untouched.geometry),
        crs=31370,
    )
    bridge = knw((1, box(10, 0, 12, 10)))
    result, _ = reconcile(faces_of(both), both, bridge)
    flagged_ids = set(result.loc[result.structure_proximity == "bridge", "polygon_id"])
    assert flagged_ids == {"1"}


def pd_concat(a, b):
    import pandas as pd

    return pd.concat([a.drop(columns="geometry"), b.drop(columns="geometry")], ignore_index=True)


def test_every_face_of_a_multi_face_corridor_shares_the_same_flag():
    wcz = gpd.GeoDataFrame({"OIDN": ["0"]}, geometry=[LineString([(0, 5), (10, 5)])], crs=31370)
    road = wbn((1, box(0, 0, 10, 10)))
    faces = faces_of(road, wcz)
    assert len(faces) == 2  # split into two faces by the internal line
    bridge = knw((1, box(10, 0, 12, 10)))
    result, _ = reconcile(faces, road, bridge)
    assert set(result.structure_proximity) == {"bridge"}
    assert len(result) == 2


def test_no_knw_structures_at_all_flags_everything_none_without_crashing():
    road = wbn((1, box(0, 0, 10, 10)))
    empty_knw = gpd.GeoDataFrame({"OIDN": [], "TYPE": []}, geometry=[], crs=31370)
    result, report = reconcile(faces_of(road), road, empty_knw)
    assert set(result.structure_proximity) == {"none"}
    assert report["knw_structures_considered"] == 0


def test_inputs_are_left_untouched():
    road = wbn((1, box(0, 0, 10, 10)))
    bridge = knw((1, box(10, 0, 12, 10)))
    faces = faces_of(road)
    columns_before = list(faces.columns)
    reconcile(faces, road, bridge)
    assert list(faces.columns) == columns_before
    assert "structure_proximity" not in faces.columns


def test_validation_refuses_mixed_crs_missing_fields_bad_geometry_and_orphan_parent():
    road = wbn((1, box(0, 0, 10, 10)))
    bridge = knw((1, box(10, 0, 12, 10)))
    faces = faces_of(road)
    with pytest.raises(ValueError, match="RECONCILE_CRS_INVALID"):
        reconcile(faces, road.to_crs(4326), bridge)
    with pytest.raises(ValueError, match="RECONCILE_FIELD_MISSING"):
        reconcile(faces, road.drop(columns=["OIDN"]), bridge)
    with pytest.raises(ValueError, match="RECONCILE_FIELD_MISSING"):
        reconcile(faces, road, bridge.drop(columns=["TYPE"]))
    broken = road.copy()
    broken.loc[broken.index[0], "geometry"] = Polygon([(0, 0), (2, 2), (2, 0), (0, 2)])
    with pytest.raises(ValueError, match="RECONCILE_GEOMETRY_INVALID"):
        reconcile(faces, broken, bridge)
    with pytest.raises(ValueError, match="RECONCILE_TOLERANCE_INVALID"):
        reconcile(faces, road, bridge, boundary_tolerance_m=-1)
    duplicated = wbn((1, box(0, 0, 10, 10)), (1, box(20, 20, 30, 30)))
    with pytest.raises(ValueError, match="RECONCILE_ID_INVALID"):
        reconcile(faces, duplicated, bridge)
    other_road = wbn((99, box(50, 50, 60, 60)))
    with pytest.raises(ValueError, match="RECONCILE_PARENT_MISSING"):
        reconcile(faces, other_road, bridge)

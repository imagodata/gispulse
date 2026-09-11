import geopandas as gpd
import pandas as pd
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


def faces_of(wbn_frame, wgo_frame=None, coverage_bounds=(-1000, -1000, 1000, 1000)):
    if wgo_frame is None:
        wgo_frame = gpd.GeoDataFrame({"OIDN": []}, geometry=[], crs=31370)
    faces, _ = partition_polygons(
        wbn_frame,
        wgo_frame,
        polygon_id="OIDN",
        boundary_id="OIDN",
        coverage_bounds=coverage_bounds,
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
    assert set(result.structure_evidence) == {"0:bridge"}
    assert report["flagged_faces"] == len(result)
    assert report["flagged_corridors"] == 1
    assert report["thresholds"] == {"boundary_tolerance_m": 1e-3}
    assert report["inference"] is False


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


def test_pillar_type_5_touching_wbn_does_not_flag_anything():
    # TYPE=5 (pijler/pillar) is real, common GRB data that touches WBN
    # constantly — including genuine bridge piers, per the handbook — without
    # its own polygon being a bridge or tunnel. Deliberately excluded (see
    # module docstring): this is a known, one-directional coverage gap, not
    # a false positive to guard against.
    road = wbn((1, box(0, 0, 10, 10)))
    pillar = knw((5, box(10, 0, 10.3, 0.3)))
    result, _ = reconcile(faces_of(road), road, pillar)
    assert set(result.structure_proximity) == {"none"}


def test_hydraulic_structure_type_2_is_not_treated_as_a_bridge():
    # TYPE=2 (waterbouwkundige constructie) is a real, non-trivial-count GRB
    # code distinct from TYPE=1 (overbrugging): a filter that conflated the
    # two would silently flag corridors next to canal/lock infrastructure
    # that has nothing to do with a road bridge.
    road = wbn((1, box(0, 0, 10, 10)))
    hydraulic = knw((2, box(10, 0, 12, 10)))
    result, _ = reconcile(faces_of(road), road, hydraulic)
    assert set(result.structure_proximity) == {"none"}


def test_dwithin_tolerance_has_a_real_effect_not_just_exact_contact():
    # A genuine 0.5 mm gap: intersects() alone would report False. The
    # default tolerance (1 mm) must still catch it; a tolerance below the
    # gap must not.
    road = wbn((1, box(0, 0, 10, 10)))
    gapped_bridge = knw((1, box(10.0005, 0, 12, 10)))
    within_tolerance, _ = reconcile(faces_of(road), road, gapped_bridge, boundary_tolerance_m=1e-3)
    assert set(within_tolerance.structure_proximity) == {"bridge"}
    below_tolerance, _ = reconcile(faces_of(road), road, gapped_bridge, boundary_tolerance_m=1e-4)
    assert set(below_tolerance.structure_proximity) == {"none"}


def test_flag_propagates_to_every_face_of_the_corridor_even_a_distant_one():
    # The bridge only touches the RIGHT edge (x=100) of a 100x10 corridor
    # split at x=50 into a left and a right face. Only the right face
    # geometrically touches the bridge; this module deliberately propagates
    # to the whole corridor regardless (see module docstring) — a change
    # that narrows this to "only the face that itself touches" must fail
    # this test.
    split = gpd.GeoDataFrame({"OIDN": ["0"]}, geometry=[LineString([(50, 0), (50, 10)])], crs=31370)
    road = wbn((1, box(0, 0, 100, 10)))
    faces = faces_of(road, split, coverage_bounds=(-1, -1, 101, 11))
    assert len(faces) == 2
    bridge = knw((1, box(100, 0, 102, 10)))
    result, _ = reconcile(faces, road, bridge)
    left_face = result[result.geometry.apply(lambda g: g.centroid.x < 50)]
    right_face = result[result.geometry.apply(lambda g: g.centroid.x >= 50)]
    assert len(left_face) == 1 and len(right_face) == 1
    assert (left_face.structure_proximity == "bridge").all()
    assert (right_face.structure_proximity == "bridge").all()


def test_only_faces_in_the_touched_corridor_are_flagged_not_the_whole_bundle():
    touched = wbn((1, box(0, 0, 10, 10)))
    untouched = wbn((2, box(20, 20, 30, 30)))  # inside coverage, genuinely far from the bridge
    both = gpd.GeoDataFrame(
        pd.concat(
            [touched.drop(columns="geometry"), untouched.drop(columns="geometry")],
            ignore_index=True,
        ),
        geometry=list(touched.geometry) + list(untouched.geometry),
        crs=31370,
    )
    bridge = knw((1, box(10, 0, 12, 10)))
    result, _ = reconcile(faces_of(both), both, bridge)
    assert set(result.loc[result.polygon_id == "1", "structure_proximity"]) == {"bridge"}
    assert set(result.loc[result.polygon_id == "2", "structure_proximity"]) == {"none"}


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
    assert report["structures_matching_no_corridor"] == 0


def test_invalid_geometry_on_a_non_structure_knw_row_does_not_block_reconciliation():
    # Only structure-typed (1/12) KNW rows are required to have valid
    # geometry: a defective, unrelated record (a self-intersecting pillar)
    # must not withhold reconciliation for a bundle that never needed it.
    road = wbn((1, box(0, 0, 10, 10)))
    bridge = knw((1, box(10, 0, 12, 10)))
    invalid_pillar = gpd.GeoDataFrame(
        {"OIDN": ["999"], "TYPE": [5]},
        geometry=[Polygon([(50, 50), (52, 52), (52, 50), (50, 52)])],  # self-intersecting bowtie
        crs=31370,
    )
    combined_knw = gpd.GeoDataFrame(
        pd.concat(
            [bridge.drop(columns="geometry"), invalid_pillar.drop(columns="geometry")],
            ignore_index=True,
        ),
        geometry=list(bridge.geometry) + list(invalid_pillar.geometry),
        crs=31370,
    )
    result, _ = reconcile(faces_of(road), road, combined_knw)
    assert set(result.structure_proximity) == {"bridge"}


def test_structures_matching_no_corridor_are_counted_but_do_not_crash():
    # Corridor 2 exists in `wbn` (the full acquisition) but has no face in
    # `faces` — e.g. it was outside_acquisition_coverage upstream. A bridge
    # touching only corridor 2 is real evidence this reconciliation could not
    # act on: it must be counted, not silently dropped or crashed on.
    present = wbn((1, box(0, 0, 10, 10)))
    absent = wbn((2, box(20, 20, 30, 30)))
    both = gpd.GeoDataFrame(
        pd.concat(
            [present.drop(columns="geometry"), absent.drop(columns="geometry")], ignore_index=True
        ),
        geometry=list(present.geometry) + list(absent.geometry),
        crs=31370,
    )
    faces = faces_of(present, coverage_bounds=(-1000, -1000, 1000, 1000))
    bridge_touching_absent = knw((1, box(30, 20, 32, 30)))
    result, report = reconcile(faces, both, bridge_touching_absent)
    assert set(result.structure_proximity) == {"none"}
    assert report["structures_matching_no_corridor"] == 1


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
    with pytest.raises(ValueError, match="RECONCILE_FIELD_MISSING"):
        reconcile(faces, road, bridge.drop(columns=["OIDN"]))
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


def test_blank_or_missing_id_on_a_structure_typed_row_is_refused():
    road = wbn((1, box(0, 0, 10, 10)))
    bridge = knw((1, box(10, 0, 12, 10)))
    bridge.loc[0, "OIDN"] = " "
    with pytest.raises(ValueError, match="RECONCILE_ID_INVALID"):
        reconcile(faces_of(road), road, bridge)

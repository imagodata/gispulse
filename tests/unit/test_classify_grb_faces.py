import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import LineString, Point, Polygon, box

from gispulse.capabilities.vector.classify_grb_faces import classify_grb_faces
from gispulse.capabilities.vector.polygon_partition import partition_polygons

WCZ_SPLIT = (1, LineString([(0, 8), (10, 8)]))
AXIS = LineString([(0, 4), (10, 4)])


def wgo(*typed_lines):
    return gpd.GeoDataFrame(
        {
            "OIDN": [str(i) for i in range(len(typed_lines))],
            "TYPE": [code for code, _ in typed_lines],
        },
        geometry=[line for _, line in typed_lines],
        crs=31370,
    )


def wegsegment(*records):
    # Empty layers keep their typed schema, as the prepare step restores it.
    return gpd.GeoDataFrame(
        pd.DataFrame(
            {
                "WS_OIDN": [str(record[0]) for record in records],
                "VERH": [record[1] for record in records],
                "STATUS": [record[2] for record in records],
            },
            columns=["WS_OIDN", "VERH", "STATUS"],
        ),
        geometry=gpd.GeoSeries([record[3] for record in records], crs=31370),
    )


def faces_of(*typed_lines, coverage_bounds=None, width=10, height=10):
    if coverage_bounds is None:
        coverage_bounds = (-1, -1, width + 1, height + 1)
    faces, _ = partition_polygons(
        gpd.GeoDataFrame({"OIDN": ["road"]}, geometry=[box(0, 0, width, height)], crs=31370),
        wgo(*typed_lines),
        polygon_id="OIDN",
        boundary_id="OIDN",
        coverage_bounds=coverage_bounds,
        area_tolerance_m2=1e-6,
        length_tolerance_m=1e-6,
    )
    return faces


def classify(faces, boundaries, axes, **overrides):
    return classify_grb_faces(
        faces,
        boundaries,
        axes,
        **{
            "axis_coverage_ratio_min": 0.8,
            "axis_min_extent_m": 0.0,
            "boundary_tolerance_m": 1e-3,
            "length_tolerance_m": 1e-6,
            **overrides,
        },
    )


def at(result, x, y):
    hit = result[result.geometry.contains(Point(x, y))]
    assert len(hit) == 1
    return hit.iloc[0]


def test_in_service_paved_axis_becomes_carriageway():
    result, report = classify(faces_of(WCZ_SPLIT), wgo(WCZ_SPLIT), wegsegment((101, 1, 4, AXIS)))
    carriageway = at(result, 5, 4)
    assert carriageway.functional_class == "carriageway_paved"
    assert carriageway.classification_reason == "in_service_paved_axis"
    assert carriageway.classification_evidence == "101:VERH=1"
    assert carriageway.axis_coverage_ratio == pytest.approx(1.0)
    assert report["classes"]["carriageway_paved"] == 1
    assert report["inference"] is False
    # The existing downstream guard must accept the output unchanged.
    faces_only = result[["functional_class"]]
    assert set(faces_only.functional_class).issubset({"carriageway_paved", "sidewalk", "unmapped"})


def test_mixed_surface_code_still_counts_as_paved():
    result, _ = classify(faces_of(WCZ_SPLIT), wgo(WCZ_SPLIT), wegsegment((101, 12, 4, AXIS)))
    assert at(result, 5, 4).functional_class == "carriageway_paved"


def test_unpaved_surface_never_reaches_costing():
    result, _ = classify(faces_of(WCZ_SPLIT), wgo(WCZ_SPLIT), wegsegment((101, 2, 4, AXIS)))
    carriageway = at(result, 5, 4)
    assert carriageway.functional_class == "unmapped"
    assert carriageway.classification_reason == "axis_surface_not_paved"
    assert carriageway.classification_evidence == "101:VERH=2"


@pytest.mark.parametrize("verh", [-8, -9, None])
def test_unknown_surface_is_neither_paved_nor_unpaved_evidence(verh):
    # An unknown/not-applicable VERH is insufficient evidence, not a
    # contradiction: it must not be conflated with genuinely unpaved (VERH=2).
    result, _ = classify(faces_of(WCZ_SPLIT), wgo(WCZ_SPLIT), wegsegment((101, verh, 4, AXIS)))
    carriageway = at(result, 5, 4)
    assert carriageway.functional_class == "unmapped"
    assert carriageway.classification_reason == "axis_surface_unknown"


def test_unknown_surface_does_not_contradict_a_genuinely_paved_axis():
    other = LineString([(0, 5), (10, 5)])
    result, _ = classify(
        faces_of(WCZ_SPLIT), wgo(WCZ_SPLIT), wegsegment((101, 1, 4, AXIS), (102, -8, 4, other))
    )
    carriageway = at(result, 5, 4)
    assert carriageway.functional_class == "carriageway_paved"
    assert carriageway.classification_reason == "in_service_paved_axis"
    # Only the paved axis is retained as evidence; the unknown one is silently insufficient.
    assert carriageway.classification_evidence == "101:VERH=1"


def test_contradictory_axis_surfaces_stay_unmapped_not_silently_paved():
    # Two in-service axes, one paved (VERH=1) and one genuinely unpaved
    # (VERH=2), both fully cover the same face. Letting "paved wins" silently
    # drop the contradicting evidence is itself an undocumented inference; a
    # fail-closed contract must surface the conflict instead.
    contradicting = LineString([(0, 4.5), (10, 4.5)])
    axes = wegsegment((101, 1, 4, AXIS), (102, 2, 4, contradicting))
    result, _ = classify(faces_of(WCZ_SPLIT), wgo(WCZ_SPLIT), axes)
    carriageway = at(result, 5, 4)
    assert carriageway.functional_class == "unmapped"
    assert carriageway.classification_reason == "contradictory_axis_surface"
    assert carriageway.classification_evidence == "101:VERH=1,102:VERH=2"


@pytest.mark.parametrize("status", [1, 2, 3, 5, -8, None])
def test_axis_not_in_service_is_not_evidence(status):
    result, _ = classify(faces_of(WCZ_SPLIT), wgo(WCZ_SPLIT), wegsegment((101, 1, status, AXIS)))
    carriageway = at(result, 5, 4)
    assert carriageway.functional_class == "unmapped"
    assert carriageway.classification_reason == "no_in_service_axis_evidence"
    assert carriageway.axis_coverage_ratio == 0.0


def test_blank_axis_id_on_an_out_of_service_row_does_not_fail_the_whole_call():
    # WS_OIDN is only validated on in-service rows: it never enters the
    # algorithm otherwise, so a blank ID on a filtered-out row must not raise.
    blank_id_out_of_service = wegsegment((101, 1, 4, AXIS))
    blank_id_out_of_service.loc[0, "WS_OIDN"] = "9"
    extra = gpd.GeoDataFrame(
        {"WS_OIDN": [" "], "VERH": [1], "STATUS": [5]},
        geometry=[LineString([(0, 6), (10, 6)])],
        crs=31370,
    )
    axes = pd.concat([blank_id_out_of_service, extra], ignore_index=True)
    result, _ = classify(faces_of(WCZ_SPLIT), wgo(WCZ_SPLIT), axes)
    assert at(result, 5, 4).functional_class == "carriageway_paved"


def test_unresolved_topology_short_circuits_before_reading_axes():
    dangling = (1, LineString([(0, 8), (7, 8)]))
    result, report = classify(faces_of(dangling), wgo(dangling), wegsegment((101, 1, 4, AXIS)))
    assert set(result.functional_class) == {"unmapped"}
    assert set(result.classification_reason) == {"unresolved_topology:unresolved_lines"}
    assert result.axis_coverage_ratio.isna().all()
    assert report["reasons"] == {"unresolved_topology:unresolved_lines": 1}


def test_unsplit_corridor_with_no_internal_boundary_is_classified_not_unresolved():
    # A WBN with no WGO line at all is reported "unsplit" by partition_polygons
    # — area conserved, zero cuts/dangles/invalid residual, nothing to split.
    # That is a resolved topology (the whole corridor unambiguously is one
    # face), not grounds to skip classification.
    faces = faces_of(coverage_bounds=(-1, -1, 11, 11))
    assert set(faces.topology_status) == {"unsplit"}
    result, report = classify(faces, wgo(), wegsegment((101, 1, 4, AXIS)))
    only = result.iloc[0]
    assert only.functional_class == "carriageway_paved"
    assert only.classification_reason == "in_service_paved_axis"
    assert report["reasons"] == {"in_service_paved_axis": 1}


def test_axis_coverage_is_measured_inside_the_parent_corridor_only():
    crossing = LineString([(5, 4), (5, 20)])  # the axis continues beyond the acquired corridor
    result, _ = classify(
        faces_of(WCZ_SPLIT),
        wgo(WCZ_SPLIT),
        wegsegment((101, 1, 4, crossing)),
        axis_coverage_ratio_min=0.6,
    )
    carriageway = at(result, 2, 4)
    assert carriageway.axis_coverage_ratio == pytest.approx(4 / 6)
    assert carriageway.functional_class == "carriageway_paved"


def test_axis_lying_on_a_shared_face_boundary_proves_nothing():
    on_edge = LineString([(0, 8), (10, 8)])
    result, _ = classify(faces_of(WCZ_SPLIT), wgo(WCZ_SPLIT), wegsegment((101, 1, 4, on_edge)))
    carriageway = at(result, 5, 4)
    assert carriageway.axis_coverage_ratio == 0.0
    assert carriageway.functional_class == "unmapped"


def test_zero_coverage_threshold_still_does_not_let_an_edge_axis_prove_anything():
    on_edge = LineString([(0, 8), (10, 8)])
    result, _ = classify(
        faces_of(WCZ_SPLIT),
        wgo(WCZ_SPLIT),
        wegsegment((101, 1, 4, on_edge)),
        axis_coverage_ratio_min=0.0,
    )
    assert set(result.functional_class) == {"unmapped"}


def test_inputs_are_left_untouched_and_empty_faces_keep_the_schema():
    faces = faces_of(WCZ_SPLIT)
    columns = list(faces.columns)
    result, _ = classify(faces, wgo(WCZ_SPLIT), wegsegment((101, 1, 4, AXIS)))
    assert list(faces.columns) == columns
    assert result.functional_class.dtype == faces.topology_status.dtype
    empty, report = classify(faces.iloc[:0], wgo(WCZ_SPLIT), wegsegment())
    assert empty.empty and list(empty.columns) == list(result.columns)
    assert empty.axis_coverage_ratio.dtype == float
    assert report["input_faces"] == 0 and report["class_ratios"]["unmapped"] == 0.0


def test_faces_merged_from_several_bundles_keep_duplicate_index_labels_aligned():
    faces = faces_of(WCZ_SPLIT)
    second = faces.copy()
    second["face_id"] = second.face_id + ":b"
    second["polygon_id"] = "road_b"
    merged = pd.concat([faces, second])  # duplicated index labels, unique face IDs
    result, report = classify(merged, wgo(WCZ_SPLIT), wegsegment((101, 1, 4, AXIS)))
    assert report["classes"]["carriageway_paved"] == 2
    assert list(result.face_id) == list(merged.face_id)


def test_validation_refuses_mixed_crs_duplicate_face_ids_and_broken_geometry():
    faces, boundaries = faces_of(WCZ_SPLIT), wgo(WCZ_SPLIT)
    axes = wegsegment((101, 1, 4, AXIS))
    with pytest.raises(ValueError, match="CLASSIFY_CRS_INVALID"):
        classify(faces, boundaries.to_crs(4326), axes)
    with pytest.raises(ValueError, match="CLASSIFY_ID_INVALID"):
        classify(pd.concat([faces, faces]), boundaries, axes)
    with pytest.raises(ValueError, match="CLASSIFY_FIELD_MISSING"):
        classify(faces, boundaries.drop(columns=["TYPE"]), axes)
    with pytest.raises(ValueError, match="CLASSIFY_FIELD_MISSING"):
        classify(faces, boundaries.drop(columns=["OIDN"]), axes)
    with pytest.raises(ValueError, match="CLASSIFY_FIELD_MISSING"):
        classify(faces, boundaries, axes.drop(columns=["STATUS"]))
    broken = faces.copy()
    broken.loc[broken.index[0], "geometry"] = Polygon([(0, 0), (2, 2), (2, 0), (0, 2)])
    with pytest.raises(ValueError, match="CLASSIFY_GEOMETRY_INVALID"):
        classify(broken, boundaries, axes)
    with pytest.raises(ValueError, match="CLASSIFY_RATIO_INVALID"):
        classify(faces, boundaries, axes, axis_coverage_ratio_min=1.5)
    with pytest.raises(ValueError, match="CLASSIFY_TOLERANCE_INVALID"):
        classify(faces, boundaries, axes, boundary_tolerance_m=-1)
    with pytest.raises(ValueError, match="CLASSIFY_TOLERANCE_INVALID"):
        classify(faces, boundaries, axes, axis_min_extent_m=-1)


def test_duplicate_wegenregister_axis_ids_are_tolerated_not_rejected():
    # WS_OIDN is a Wegenregister reference, not a GRB OIDN: it is not
    # guaranteed unique across a large bbox or pagination seams, and the
    # algorithm never looks an axis up by ID — only evidence text and
    # deterministic ordering use it. Two distinct in-service paved segments
    # sharing the same WS_OIDN must not make the whole call fail closed.
    other = LineString([(0, 5), (10, 5)])
    axes = wegsegment((101, 1, 4, AXIS), (101, 1, 4, other))
    result, _ = classify(faces_of(WCZ_SPLIT), wgo(WCZ_SPLIT), axes)
    assert at(result, 5, 4).functional_class == "carriageway_paved"


def test_evidence_is_deduplicated_when_two_axes_share_id_and_surface():
    other = LineString([(0, 5), (10, 5)])
    axes = wegsegment((101, 1, 4, AXIS), (101, 1, 4, other))
    result, _ = classify(faces_of(WCZ_SPLIT), wgo(WCZ_SPLIT), axes)
    assert at(result, 5, 4).classification_evidence == "101:VERH=1"


def test_axis_min_extent_rejects_a_short_graze_into_the_wrong_face():
    # A ~1.6 m in-service paved access stub sits entirely inside the sidewalk-
    # shaped face. Without an absolute floor on the axis's extent inside the
    # corridor, its coverage ratio is mechanically 1.0 (it never leaves the
    # single face it grazes), which used to promote that face regardless of
    # the 0.8 ratio threshold.
    graze = LineString([(3, 8.2), (3, 9.8)])
    guarded, _ = classify(
        faces_of(WCZ_SPLIT), wgo(WCZ_SPLIT), wegsegment((101, 1, 4, graze)), axis_min_extent_m=5.0
    )
    assert at(guarded, 5, 9).functional_class != "carriageway_paved"
    unguarded, _ = classify(
        faces_of(WCZ_SPLIT), wgo(WCZ_SPLIT), wegsegment((101, 1, 4, graze)), axis_min_extent_m=0.0
    )
    assert at(unguarded, 5, 9).functional_class == "carriageway_paved"


def test_a_wcz_line_can_split_the_same_carriageway_without_being_misread_as_sidewalk():
    # Regression for a rejected design: an earlier version promoted a face to
    # "sidewalk" once it was adjacent to a proven carriageway and dominated by
    # Wcz boundary. But adjacency is symmetric — a carriageway split in two by
    # a transversal Wcz line (e.g. a raised pedestrian crossing) is adjacent
    # to itself across that line. Only one side has axis evidence here; this
    # module must never promote the other to sidewalk on boundary composition
    # alone, however dominant its Wcz share.
    transversal = (1, LineString([(5, 0), (5, 10)]))
    west_axis = LineString([(0, 5), (4.5, 5)])  # stops short of the crossing at x=5
    result, _ = classify(
        faces_of(transversal), wgo(transversal), wegsegment((101, 1, 4, west_axis))
    )
    west = at(result, 2, 5)
    east = at(result, 7, 5)
    assert west.functional_class == "carriageway_paved"
    assert east.functional_class != "sidewalk"
    assert east.functional_class == "unmapped"
    assert east.classification_reason == "no_in_service_axis_evidence"


def test_the_true_carriageway_flanked_by_two_wcz_lines_is_never_promoted_to_sidewalk():
    # The true carriageway, sandwiched between two Wcz lines, carries a HIGHER
    # Wcz perimeter share (0.625) than either flanking face (0.417 each): a
    # bare Wcz-ratio rule inverts the verdict, and an ancestry-based rule that
    # anchors on a neighbour's proven class does not fix it either, because
    # the true carriageway is itself adjacent to the flanking faces. Without
    # any axis evidence anywhere in this corridor, everything must stay
    # unmapped.
    lower_wcz = (1, LineString([(0, 2), (10, 2)]))
    upper_wcz = (1, LineString([(0, 8), (10, 8)]))
    faces = faces_of(lower_wcz, upper_wcz)
    result, _ = classify(faces, wgo(lower_wcz, upper_wcz), wegsegment())
    middle = at(result, 5, 5)
    assert middle.wcz_boundary_ratio == pytest.approx(20 / 32)
    assert middle.wcz_boundary_ratio > at(result, 5, 1).wcz_boundary_ratio
    assert set(result.functional_class) == {"unmapped"}
    assert set(result.classification_reason) == {"no_in_service_axis_evidence"}


def test_an_axis_hugging_an_internal_boundary_then_briefly_diverging_is_not_promoted():
    # Regression for a rejected "fix": excluding boundary-collinear runs
    # symmetrically from both the numerator and the (corridor-wide)
    # denominator lets an axis that hugs an internal Wcz line for a long
    # stretch (50 m), then genuinely diverges for a short one (7.5 m), score
    # ratio 1.0 for the face it diverges into — because the denominator
    # shrinks to match. The asymmetric calculation kept here is deliberately
    # conservative: this short diversion must stay unmapped (a false
    # negative, understated evidence), never promoted (a false positive).
    wide_split = (1, LineString([(0, 8), (100, 8)]))
    hugging_then_diverging = LineString([(0, 8), (50, 8), (50, 0.5)])
    faces = faces_of(wide_split, width=100)
    result, _ = classify(faces, wgo(wide_split), wegsegment((101, 1, 4, hugging_then_diverging)))
    carriageway_face = at(result, 25, 4)
    assert carriageway_face.functional_class == "unmapped"
    assert carriageway_face.axis_coverage_ratio == pytest.approx(7.5 / 57.5)
    assert carriageway_face.axis_coverage_ratio < 0.8

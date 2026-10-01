import math

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import LineString, Point, Polygon, box

from gispulse.capabilities.vector.classify_grb_faces import classify_grb_faces
from gispulse.capabilities.vector.polygon_partition import partition_polygons

WCZ_SPLIT = (1, LineString([(0, 8), (10, 8)]))
AXIS = LineString([(0, 4), (10, 4)])
# MORF 103 = "weg bestaande uit één rijbaan" (single-carriageway motor-traffic road).
CARRIAGEWAY_MORF = 103
# MORF 114 = "wandel- en/of fietsweg niet toegankelijk voor andere voertuigen"
# (walking/cycling path, explicitly closed to other vehicles).
PEDESTRIAN_MORF = 114


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
    """Each record is (WS_OIDN, VERH, STATUS, geometry) or (WS_OIDN, VERH, STATUS, MORF, geometry)."""
    normalized = [r if len(r) == 5 else (*r[:3], CARRIAGEWAY_MORF, r[3]) for r in records]
    # Empty layers keep their typed schema, as the prepare step restores it.
    return gpd.GeoDataFrame(
        pd.DataFrame(
            {
                "WS_OIDN": [str(r[0]) for r in normalized],
                "VERH": [r[1] for r in normalized],
                "STATUS": [r[2] for r in normalized],
                "MORF": [r[3] for r in normalized],
            },
            columns=["WS_OIDN", "VERH", "STATUS", "MORF"],
        ),
        geometry=gpd.GeoSeries([r[4] for r in normalized], crs=31370),
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
            "unsplit_max_width_m": 12.0,
            "boundary_tolerance_m": 1e-3,
            "length_tolerance_m": 1e-6,
            **overrides,
        },
    )


def at(result, x, y):
    hit = result[result.geometry.contains(Point(x, y))]
    assert len(hit) == 1
    return hit.iloc[0]


def test_in_service_paved_motorized_axis_becomes_carriageway():
    result, report = classify(faces_of(WCZ_SPLIT), wgo(WCZ_SPLIT), wegsegment((101, 1, 4, AXIS)))
    carriageway = at(result, 5, 4)
    assert carriageway.functional_class == "carriageway_paved"
    assert carriageway.classification_reason == "in_service_paved_axis"
    assert carriageway.classification_evidence == "101:VERH=1"
    assert carriageway.axis_coverage_ratio == pytest.approx(1.0)
    assert report["classes"]["carriageway_paved"] == 1
    assert report["inference"] is False


def test_mixed_surface_code_still_counts_as_paved():
    result, _ = classify(faces_of(WCZ_SPLIT), wgo(WCZ_SPLIT), wegsegment((101, 12, 4, AXIS)))
    assert at(result, 5, 4).functional_class == "carriageway_paved"


def test_unpaved_motorized_axis_becomes_unpaved_carriageway_never_paved():
    result, report = classify(faces_of(WCZ_SPLIT), wgo(WCZ_SPLIT), wegsegment((101, 2, 4, AXIS)))
    carriageway = at(result, 5, 4)
    assert carriageway.functional_class == "carriageway_unpaved"
    assert carriageway.classification_reason == "in_service_unpaved_axis"
    # Unpaved verdicts cite MORF too: some rest on MORF alone.
    assert carriageway.classification_evidence == "101:VERH=2:MORF=103"
    assert carriageway.axis_coverage_ratio == pytest.approx(1.0)
    assert report["classes"]["carriageway_unpaved"] == 1
    assert report["classes"]["carriageway_paved"] == 0


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
    # Two in-service motorized axes, one paved (VERH=1) and one genuinely
    # unpaved (VERH=2), both fully cover the same face. Letting "paved wins"
    # silently drop the contradicting evidence is itself an undocumented
    # inference; a fail-closed contract must surface the conflict instead.
    contradicting = LineString([(0, 4.5), (10, 4.5)])
    axes = wegsegment((101, 1, 4, AXIS), (102, 2, 4, contradicting))
    result, _ = classify(faces_of(WCZ_SPLIT), wgo(WCZ_SPLIT), axes)
    carriageway = at(result, 5, 4)
    assert carriageway.functional_class == "unmapped"
    assert carriageway.classification_reason == "axis_surface_conflict"
    assert carriageway.classification_evidence == "101:VERH=1:MORF=103,102:VERH=2:MORF=103"


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
    in_service_row = wegsegment((101, 1, 4, AXIS))
    extra = gpd.GeoDataFrame(
        {"WS_OIDN": [" "], "VERH": [1], "STATUS": [5], "MORF": [CARRIAGEWAY_MORF]},
        geometry=[LineString([(0, 6), (10, 6)])],
        crs=31370,
    )
    axes = pd.concat([in_service_row, extra], ignore_index=True)
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
    # face), not grounds to skip classification. The corridor here (10x10, a
    # single axis spanning the full width) is well within unsplit_max_width_m.
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
    with pytest.raises(ValueError, match="CLASSIFY_FIELD_MISSING"):
        classify(faces, boundaries, axes.drop(columns=["MORF"]))
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
    with pytest.raises(ValueError, match="CLASSIFY_TOLERANCE_INVALID"):
        classify(faces, boundaries, axes, unsplit_max_width_m=0)


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


# --- MORF: an in-service, paved axis is not necessarily a carriageway ---


def test_pedestrian_path_axis_never_becomes_carriageway_despite_paved_in_service():
    # Real Gand data: MORF=114 (wandel- en/of fietsweg, niet toegankelijk voor
    # andere voertuigen) axes are commonly STATUS=4 VERH=1 — in service and
    # paved — while being officially not a carriageway. Reading VERH/STATUS
    # alone (the first cut of this module) misclassified these as
    # carriageway_paved on the very bbox cited as validation.
    result, _ = classify(
        faces_of(WCZ_SPLIT), wgo(WCZ_SPLIT), wegsegment((101, 1, 4, PEDESTRIAN_MORF, AXIS))
    )
    face = at(result, 5, 4)
    assert face.functional_class != "carriageway_paved"
    assert face.functional_class == "unmapped"
    assert face.classification_reason == "axis_not_motorized_carriageway"
    assert face.classification_evidence == "101:VERH=1"


# 125 (aardeweg) is absent: coded VERH=1 it contradicts itself, see the
# carriageway_unpaved section below.
@pytest.mark.parametrize("morf", [113, 116, 120, 130])
def test_other_non_motorized_morf_codes_never_become_carriageway(morf):
    result, _ = classify(faces_of(WCZ_SPLIT), wgo(WCZ_SPLIT), wegsegment((101, 1, 4, morf, AXIS)))
    face = at(result, 5, 4)
    assert face.functional_class == "unmapped"
    assert face.classification_reason == "axis_not_motorized_carriageway"


def test_non_motorized_morf_does_not_contradict_a_genuinely_motorized_paved_axis():
    other = LineString([(0, 5), (10, 5)])
    axes = wegsegment((101, 1, 4, CARRIAGEWAY_MORF, AXIS), (102, 1, 4, PEDESTRIAN_MORF, other))
    result, _ = classify(faces_of(WCZ_SPLIT), wgo(WCZ_SPLIT), axes)
    face = at(result, 5, 4)
    assert face.functional_class == "carriageway_paved"
    assert face.classification_evidence == "101:VERH=1"


@pytest.mark.parametrize("morf", [101, 102, 104, 105, 106, 107, 108, 109, 110, 111, 112])
def test_every_documented_motorized_morf_code_can_become_carriageway(morf):
    result, _ = classify(faces_of(WCZ_SPLIT), wgo(WCZ_SPLIT), wegsegment((101, 1, 4, morf, AXIS)))
    assert at(result, 5, 4).functional_class == "carriageway_paved"


# --- unsplit corridors: an axis alone cannot vouch for an overly wide corridor ---


def test_unsplit_corridor_too_wide_for_a_single_carriageway_stays_unmapped():
    # A 10x30 corridor (300 m2) with no internal WGO line at all, crossed end
    # to end by a single paved in-service motorized axis. Its width estimate
    # (area / axis length inside the face) is 10 m; a threshold of 8 m must
    # reject it, since a single axis running through a 10 m-wide, WGO-less
    # corridor cannot vouch for the whole width being carriageway.
    wide_axis = LineString([(0, 5), (30, 5)])
    faces = faces_of(width=30, height=10)
    assert set(faces.topology_status) == {"unsplit"}
    result, _ = classify(faces, wgo(), wegsegment((101, 1, 4, wide_axis)), unsplit_max_width_m=8.0)
    face = at(result, 15, 5)
    assert face.functional_class == "unmapped"
    assert face.classification_reason == "unsplit_corridor_too_wide_for_single_carriageway"


def test_unsplit_corridor_width_guard_does_not_apply_to_partitioned_faces():
    # The same width estimate on a *partitioned* corridor (a real internal WGO
    # cut exists) must not be second-guessed by the width guard: a narrow
    # partitioned face with a short covered axis length can have a large
    # area/length ratio without that meaning anything is wrong, because the
    # subdivision is itself evidence, unlike an unsplit corridor.
    result, _ = classify(
        faces_of(WCZ_SPLIT), wgo(WCZ_SPLIT), wegsegment((101, 1, 4, AXIS)), unsplit_max_width_m=0.5
    )
    assert at(result, 5, 4).functional_class == "carriageway_paved"


# --- carriageway_unpaved: explicit unpaved axis evidence, never geometry ---

# MORF 125 = "aardeweg" (earthen track); MORF 120 = "dienstweg" (service road,
# most of them coded VERH=1 on the live WFS).
EARTHEN_MORF = 125
SERVICE_MORF = 120
SECOND_AXIS = LineString([(0, 5), (10, 5)])


@pytest.mark.parametrize("verh", [2, -8, -9, None])
def test_earthen_track_is_unpaved_evidence_whatever_its_unknown_or_unpaved_verh(verh):
    result, _ = classify(
        faces_of(WCZ_SPLIT), wgo(WCZ_SPLIT), wegsegment((101, verh, 4, EARTHEN_MORF, AXIS))
    )
    face = at(result, 5, 4)
    assert face.functional_class == "carriageway_unpaved"
    assert face.classification_reason == "in_service_unpaved_axis"
    code = "unknown" if verh is None else verh
    assert face.classification_evidence == f"101:VERH={code}:MORF=125"


@pytest.mark.parametrize("verh", [1, 12])
def test_earthen_track_coded_paved_contradicts_itself_and_stays_unmapped(verh):
    result, _ = classify(
        faces_of(WCZ_SPLIT), wgo(WCZ_SPLIT), wegsegment((101, verh, 4, EARTHEN_MORF, AXIS))
    )
    face = at(result, 5, 4)
    assert face.functional_class == "unmapped"
    assert face.classification_reason == "axis_surface_conflict"
    assert face.classification_evidence == f"101:VERH={verh}:MORF=125"


@pytest.mark.parametrize(
    "unpaved_axis",
    [(102, 2, 4, CARRIAGEWAY_MORF, SECOND_AXIS), (102, -9, 4, EARTHEN_MORF, SECOND_AXIS)],
)
@pytest.mark.parametrize("paved_verh", [1, 12])
def test_paved_and_unpaved_evidence_on_one_face_is_a_conflict(paved_verh, unpaved_axis):
    axes = wegsegment((101, paved_verh, 4, AXIS), unpaved_axis)
    result, report = classify(faces_of(WCZ_SPLIT), wgo(WCZ_SPLIT), axes)
    face = at(result, 5, 4)
    assert face.functional_class == "unmapped"
    assert face.classification_reason == "axis_surface_conflict"
    assert face.classification_evidence.startswith(f"101:VERH={paved_verh}:MORF=103,102:")
    assert report["classes"]["carriageway_paved"] == 0
    assert report["classes"]["carriageway_unpaved"] == 0


@pytest.mark.parametrize("unpaved_axis", [(101, 2, 4, AXIS), (101, -9, 4, EARTHEN_MORF, AXIS)])
def test_a_motorized_axis_of_unknown_surface_blocks_an_unpaved_verdict(unpaved_axis):
    # Unlike the paved class, the newer unpaved class does not let an unknown
    # surface pass: that axis may be the face's own road.
    axes = wegsegment(unpaved_axis, (102, -8, 4, SECOND_AXIS))
    result, _ = classify(faces_of(WCZ_SPLIT), wgo(WCZ_SPLIT), axes)
    face = at(result, 5, 4)
    assert face.functional_class == "unmapped"
    assert face.classification_reason == "axis_surface_unknown"
    assert "102:VERH=-8:MORF=103" in face.classification_evidence


def test_unpaved_non_motorized_path_is_not_an_unpaved_carriageway():
    # A VERH=2 walking/cycling path is unpaved, but it is not a carriageway.
    result, _ = classify(
        faces_of(WCZ_SPLIT), wgo(WCZ_SPLIT), wegsegment((101, 2, 4, PEDESTRIAN_MORF, AXIS))
    )
    face = at(result, 5, 4)
    assert face.functional_class == "unmapped"
    assert face.classification_reason == "axis_not_motorized_carriageway"


@pytest.mark.parametrize("verh", [2, -8, None])
def test_service_road_is_not_unpaved_evidence_by_default(verh):
    result, report = classify(
        faces_of(WCZ_SPLIT), wgo(WCZ_SPLIT), wegsegment((101, verh, 4, SERVICE_MORF, AXIS))
    )
    face = at(result, 5, 4)
    assert face.functional_class == "unmapped"
    assert face.classification_reason == "axis_not_motorized_carriageway"
    assert report["options"] == {"dienstweg_unpaved_evidence": False}


def test_opted_in_service_road_coded_unpaved_is_unpaved_evidence():
    result, report = classify(
        faces_of(WCZ_SPLIT),
        wgo(WCZ_SPLIT),
        wegsegment((101, 2, 4, SERVICE_MORF, AXIS)),
        dienstweg_unpaved_evidence=True,
    )
    face = at(result, 5, 4)
    assert face.functional_class == "carriageway_unpaved"
    assert face.classification_reason == "in_service_unpaved_axis"
    assert face.classification_evidence == "101:VERH=2:MORF=120"
    assert report["options"] == {"dienstweg_unpaved_evidence": True}


@pytest.mark.parametrize("verh", [1, 12, -8, -9, None, 0])
def test_opted_in_service_road_is_never_assumed_unpaved(verh):
    # Most dienstwegen are coded VERH=1 on the live WFS: the opt-in admits an
    # explicitly unpaved one, it never makes an unknown or paved one unpaved.
    result, _ = classify(
        faces_of(WCZ_SPLIT),
        wgo(WCZ_SPLIT),
        wegsegment((101, verh, 4, SERVICE_MORF, AXIS)),
        dienstweg_unpaved_evidence=True,
    )
    face = at(result, 5, 4)
    assert face.functional_class == "unmapped"
    assert face.classification_reason == "axis_not_motorized_carriageway"


def test_service_road_only_conflicts_with_a_paved_axis_when_opted_in():
    axes = wegsegment((101, 1, 4, AXIS), (102, 2, 4, SERVICE_MORF, SECOND_AXIS))
    default, _ = classify(faces_of(WCZ_SPLIT), wgo(WCZ_SPLIT), axes)
    assert at(default, 5, 4).functional_class == "carriageway_paved"
    assert at(default, 5, 4).classification_evidence == "101:VERH=1"
    opted, _ = classify(faces_of(WCZ_SPLIT), wgo(WCZ_SPLIT), axes, dienstweg_unpaved_evidence=True)
    assert at(opted, 5, 4).classification_reason == "axis_surface_conflict"


@pytest.mark.parametrize("value", [1, "true", None])
def test_dienstweg_option_must_be_a_real_bool(value):
    # TypeError, not ValueError: callers degrade GRB_CLASSIFY_* ValueErrors as
    # data defects, and a caller mistake must never be swallowed that way.
    with pytest.raises(TypeError, match="GRB_CLASSIFY_OPTION_INVALID"):
        classify(
            faces_of(WCZ_SPLIT),
            wgo(WCZ_SPLIT),
            wegsegment((101, 1, 4, AXIS)),
            dienstweg_unpaved_evidence=value,
        )


@pytest.mark.parametrize("status", [1, 2, 3, 5, -8, None])
@pytest.mark.parametrize("morf", [CARRIAGEWAY_MORF, EARTHEN_MORF])
def test_unpaved_axis_not_in_service_is_not_evidence(status, morf):
    result, _ = classify(
        faces_of(WCZ_SPLIT), wgo(WCZ_SPLIT), wegsegment((101, 2, status, morf, AXIS))
    )
    face = at(result, 5, 4)
    assert face.functional_class == "unmapped"
    assert face.classification_reason == "no_in_service_axis_evidence"


def test_unpaved_axis_below_the_coverage_threshold_proves_nothing():
    # Same face/axis matching rules as paved: 4 m of a 6 m in-corridor run is
    # 0.67 < 0.8, so the unpaved axis is not evidence for this face.
    crossing = LineString([(5, 4), (5, 20)])
    result, _ = classify(
        faces_of(WCZ_SPLIT), wgo(WCZ_SPLIT), wegsegment((101, 2, 4, EARTHEN_MORF, crossing))
    )
    face = at(result, 2, 4)
    assert face.axis_coverage_ratio == pytest.approx(4 / 6)
    assert face.functional_class == "unmapped"
    assert face.classification_reason == "no_in_service_axis_evidence"


def test_unpaved_axis_on_a_shared_face_boundary_proves_nothing():
    on_edge = LineString([(0, 8), (10, 8)])
    result, _ = classify(
        faces_of(WCZ_SPLIT),
        wgo(WCZ_SPLIT),
        wegsegment((101, 2, 4, on_edge)),
        axis_coverage_ratio_min=0.0,
    )
    assert set(result.functional_class) == {"unmapped"}


def test_unsplit_corridor_width_guard_also_applies_to_unpaved_evidence():
    wide_axis = LineString([(0, 5), (30, 5)])
    faces = faces_of(width=30, height=10)
    result, _ = classify(faces, wgo(), wegsegment((101, 2, 4, wide_axis)), unsplit_max_width_m=8.0)
    face = at(result, 15, 5)
    assert face.functional_class == "unmapped"
    assert face.classification_reason == "unsplit_corridor_too_wide_for_single_carriageway"
    assert face.classification_evidence == "101:VERH=2:MORF=103"
    narrow, _ = classify(faces, wgo(), wegsegment((101, 2, 4, wide_axis)))
    assert at(narrow, 15, 5).functional_class == "carriageway_unpaved"


def test_woz_boundaries_never_make_a_face_unpaved():
    # A face bordered only by Woz lines (edge of a soft shoulder) and with no
    # axis evidence stays unmapped: the shoulder is never read from WGO.
    woz = (2, LineString([(0, 8), (10, 8)]))
    result, _ = classify(faces_of(woz), wgo(woz), wegsegment((101, 1, 4, AXIS)))
    shoulder = at(result, 5, 9)
    assert shoulder.woz_boundary_ratio > 0
    assert shoulder.functional_class == "unmapped"
    assert shoulder.classification_reason == "no_in_service_axis_evidence"
    assert at(result, 5, 4).functional_class == "carriageway_paved"


@pytest.mark.parametrize("verh", [99, 0, 3])
def test_earthen_track_with_an_undocumented_surface_code_is_not_evidence(verh):
    result, _ = classify(
        faces_of(WCZ_SPLIT), wgo(WCZ_SPLIT), wegsegment((101, verh, 4, EARTHEN_MORF, AXIS))
    )
    face = at(result, 5, 4)
    assert face.functional_class == "unmapped"
    assert face.classification_reason == "axis_not_motorized_carriageway"


@pytest.mark.parametrize("morf", [PEDESTRIAN_MORF, 113, 116, SERVICE_MORF, None])
@pytest.mark.parametrize("unpaved_axis", [(101, 2, 4, AXIS), (101, -8, 4, EARTHEN_MORF, AXIS)])
def test_any_explicitly_paved_axis_vetoes_an_unpaved_verdict(unpaved_axis, morf):
    # A paved axis need not be motorized to contradict an unpaved verdict: the
    # conflict is about surface, and the newer class gets the stricter rule.
    axes = wegsegment(unpaved_axis, (102, 1, 4, morf, SECOND_AXIS))
    result, _ = classify(faces_of(WCZ_SPLIT), wgo(WCZ_SPLIT), axes)
    face = at(result, 5, 4)
    assert face.functional_class == "unmapped"
    assert face.classification_reason == "axis_surface_conflict"
    assert "102:VERH=1:MORF=" in face.classification_evidence


def test_an_unbound_footpath_does_not_veto_a_paved_carriageway():
    # The reverse is not symmetric on purpose: VERH=2 on a footpath says nothing
    # about the carriageway, and carriageway_paved keeps its validated rule.
    axes = wegsegment((101, 1, 4, AXIS), (102, 2, 4, PEDESTRIAN_MORF, SECOND_AXIS))
    result, _ = classify(faces_of(WCZ_SPLIT), wgo(WCZ_SPLIT), axes)
    face = at(result, 5, 4)
    assert face.functional_class == "carriageway_paved"
    assert face.classification_evidence == "101:VERH=1"


# A 40x8 corridor cut by three transversal Wcz lines: the longitudinal axis
# covers only 0.25 of its in-corridor length in each face (below threshold),
# while an axis crossing the corridor inside one face covers it fully.
TRANSVERSAL_CUTS = tuple((1, LineString([(x, 0), (x, 8)])) for x in (10, 20, 30))
LONGITUDINAL = LineString([(0, 4), (40, 4)])
CROSSING = LineString([(15, -5), (15, 13)])


def transversal_faces():
    return faces_of(*TRANSVERSAL_CUTS, width=40, height=8)


@pytest.mark.parametrize(
    "crossing_axis",
    [(2, 2, 4, CARRIAGEWAY_MORF, CROSSING), (2, -8, 4, EARTHEN_MORF, CROSSING)],
)
def test_a_paved_axis_below_the_threshold_still_vetoes_an_unpaved_crossing(crossing_axis):
    axes = wegsegment((1, 1, 4, CARRIAGEWAY_MORF, LONGITUDINAL), crossing_axis)
    result, _ = classify(transversal_faces(), wgo(*TRANSVERSAL_CUTS), axes)
    face = at(result, 15, 4)
    assert face.functional_class == "unmapped"
    assert face.classification_reason == "axis_surface_conflict"
    assert face.classification_evidence.startswith("1:VERH=1:MORF=103,2:")
    # The verdict-deciding crossing axis covers the face fully.
    assert face.axis_coverage_ratio == pytest.approx(1.0)
    # Faces the crossing axis does not reach are unaffected.
    assert at(result, 5, 4).classification_reason == "no_in_service_axis_evidence"


def test_an_unpaved_axis_below_the_threshold_still_vetoes_a_paved_crossing():
    axes = wegsegment(
        (1, 2, 4, CARRIAGEWAY_MORF, LONGITUDINAL), (2, 1, 4, CARRIAGEWAY_MORF, CROSSING)
    )
    result, _ = classify(transversal_faces(), wgo(*TRANSVERSAL_CUTS), axes)
    face = at(result, 15, 4)
    assert face.functional_class == "unmapped"
    assert face.classification_reason == "axis_surface_conflict"
    assert face.classification_evidence == "1:VERH=2:MORF=103,2:VERH=1:MORF=103"


@pytest.mark.parametrize("verh", [-8, -9, None])
def test_an_unpaved_crossing_cannot_decide_a_road_of_unknown_surface(verh):
    axes = wegsegment(
        (1, verh, 4, CARRIAGEWAY_MORF, LONGITUDINAL), (2, 2, 4, CARRIAGEWAY_MORF, CROSSING)
    )
    result, _ = classify(transversal_faces(), wgo(*TRANSVERSAL_CUTS), axes)
    face = at(result, 15, 4)
    assert face.functional_class == "unmapped"
    assert face.classification_reason == "axis_surface_unknown"


def test_an_unpaved_crossing_over_a_road_with_no_in_service_axis_still_classifies():
    # Known, documented limit: if the crossed road has no in-service axis at
    # all, nothing on the face contradicts the crossing axis.
    axes = wegsegment(
        (1, 1, 5, CARRIAGEWAY_MORF, LONGITUDINAL), (2, 2, 4, CARRIAGEWAY_MORF, CROSSING)
    )
    result, _ = classify(transversal_faces(), wgo(*TRANSVERSAL_CUTS), axes)
    assert at(result, 15, 4).functional_class == "carriageway_unpaved"


# A gravel side road ending on the paved axis: 6 m inside the corridor (past
# a 5 m floor), only 4 m of them inside the carriageway face (0.67 < 0.8).
SIDE_ROAD_STUB = LineString([(5, 10), (5, 4)])


@pytest.mark.parametrize("stub_morf", [CARRIAGEWAY_MORF, EARTHEN_MORF])
def test_a_side_road_ending_on_the_axis_does_not_demote_a_paved_carriageway(stub_morf):
    axes = wegsegment((101, 1, 4, AXIS), (102, 2, 4, stub_morf, SIDE_ROAD_STUB))
    result, _ = classify(faces_of(WCZ_SPLIT), wgo(WCZ_SPLIT), axes, axis_min_extent_m=5.0)
    face = at(result, 5, 4)
    assert face.functional_class == "carriageway_paved"
    assert face.classification_evidence == "101:VERH=1"


def test_a_side_road_shorter_than_half_the_paved_axis_never_vetoes_even_without_floor():
    # 4 m inside the face against a 10 m paved axis: under half its length.
    axes = wegsegment((101, 1, 4, AXIS), (102, 2, 4, SIDE_ROAD_STUB))
    result, _ = classify(faces_of(WCZ_SPLIT), wgo(WCZ_SPLIT), axes)
    assert at(result, 5, 4).functional_class == "carriageway_paved"


# A 40 m carriageway face (y in [0, 8]) with a sidewalk strip above it, and a
# gravel road joining the paved axis at 30 degrees: 12 m inside the corridor,
# 8 m inside the carriageway face — past a 5 m floor, under half of 40 m.
LONG_SPLIT = (1, LineString([(0, 8), (40, 8)]))
LONG_AXIS = LineString([(0, 4), (40, 4)])
OBLIQUE_STUB = LineString([(20 - 6 / math.tan(math.radians(30)), 10), (20, 4)])


def test_an_oblique_side_road_does_not_demote_a_long_paved_carriageway():
    axes = wegsegment((101, 1, 4, LONG_AXIS), (102, 2, 4, OBLIQUE_STUB))
    faces = faces_of(LONG_SPLIT, width=40, height=10)
    result, _ = classify(faces, wgo(LONG_SPLIT), axes, axis_min_extent_m=5.0)
    assert at(result, 20, 2).functional_class == "carriageway_paved"


def test_a_side_road_matched_inside_an_unsplit_corridor_still_demotes_it():
    # Known, fail-closed limit: in an unsplit corridor face and corridor
    # coincide, so any axis past the extent floor is a *match* (ratio 1.0 by
    # construction), and a matched unpaved axis always contradicts a paved one
    # — as a matched VERH=2 carriageway already did before this class existed.
    stub = LineString([(20 - 4 / math.tan(math.radians(30)), 8), (20, 4)])
    faces = faces_of(width=40, height=8)
    assert set(faces.topology_status) == {"unsplit"}
    axes = wegsegment((101, 1, 4, LONG_AXIS), (102, -9, 4, EARTHEN_MORF, stub))
    result, _ = classify(faces, wgo(), axes, axis_min_extent_m=5.0)
    face = at(result, 20, 2)
    assert face.functional_class == "unmapped"
    assert face.classification_reason == "axis_surface_conflict"


@pytest.mark.parametrize(
    "crossed",
    [
        (1, -8, 4, PEDESTRIAN_MORF, LONGITUDINAL),
        (1, -9, 4, None, LONGITUDINAL),
        (1, -8, 4, SERVICE_MORF, LONGITUDINAL),
        (1, 2, 4, PEDESTRIAN_MORF, LONGITUDINAL),
    ],
)
def test_an_unpaved_crossing_cannot_decide_a_face_another_axis_runs_through(crossed):
    axes = wegsegment(crossed, (2, 2, 4, CARRIAGEWAY_MORF, CROSSING))
    result, _ = classify(transversal_faces(), wgo(*TRANSVERSAL_CUTS), axes)
    face = at(result, 15, 4)
    assert face.functional_class == "unmapped"
    assert face.classification_reason == "axis_not_motorized_carriageway"
    assert face.classification_evidence.startswith("1:VERH=")


def test_an_unpaved_axis_grazing_the_face_by_numerical_noise_vetoes_nothing():
    # A neighbouring gravel road overshooting the shared WGO line by 1e-9 m.
    grazing = LineString([(0, 9), (5, 9), (5, 8 - 1e-9), (5.0001, 9), (10, 9)])
    axes = wegsegment((101, 1, 4, AXIS), (102, 2, 4, grazing))
    result, _ = classify(faces_of(WCZ_SPLIT), wgo(WCZ_SPLIT), axes)
    assert at(result, 5, 4).functional_class == "carriageway_paved"


def test_an_unmatched_earthen_track_coded_paved_does_not_veto_a_paved_carriageway():
    # Its VERH agrees with the paved verdict; only matched, it contradicts itself.
    axes = wegsegment((101, 1, 4, AXIS), (102, 1, 4, EARTHEN_MORF, SIDE_ROAD_STUB))
    result, _ = classify(faces_of(WCZ_SPLIT), wgo(WCZ_SPLIT), axes)
    assert at(result, 5, 4).functional_class == "carriageway_paved"
    matched = wegsegment((101, 1, 4, AXIS), (102, 1, 4, EARTHEN_MORF, SECOND_AXIS))
    result, _ = classify(faces_of(WCZ_SPLIT), wgo(WCZ_SPLIT), matched)
    face = at(result, 5, 4)
    assert face.classification_reason == "axis_surface_conflict"
    assert face.classification_evidence == "101:VERH=1:MORF=103,102:VERH=1:MORF=125"

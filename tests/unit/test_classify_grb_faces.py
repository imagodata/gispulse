import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import LineString, Point, Polygon, box

from gispulse.capabilities.vector.classify_faces import validate_functional_faces
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


def faces_of(*typed_lines, coverage_bounds=(-1, -1, 11, 11)):
    faces, _ = partition_polygons(
        gpd.GeoDataFrame({"OIDN": ["road"]}, geometry=[box(0, 0, 10, 10)], crs=31370),
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
            "wcz_ratio_min": 0.4,
            "wrb_ratio_max": 0.5,
            "boundary_tolerance_m": 1e-3,
            "length_tolerance_m": 1e-6,
            **overrides,
        },
    )


def at(result, x, y):
    hit = result[result.geometry.contains(Point(x, y))]
    assert len(hit) == 1
    return hit.iloc[0]


def test_in_service_paved_axis_and_dominant_wcz_split_the_corridor():
    result, report = classify(faces_of(WCZ_SPLIT), wgo(WCZ_SPLIT), wegsegment((101, 1, 4, AXIS)))
    carriageway = at(result, 5, 4)
    assert carriageway.functional_class == "carriageway_paved"
    assert carriageway.classification_reason == "in_service_paved_axis"
    assert carriageway.classification_evidence == "101:VERH=1"
    assert carriageway.axis_coverage_ratio == pytest.approx(1.0)
    sidewalk = at(result, 5, 9)
    assert sidewalk.functional_class == "sidewalk"
    assert sidewalk.classification_reason == "dominant_wcz_boundary"
    assert sidewalk.wcz_boundary_ratio == pytest.approx(10 / 24)
    # The sidewalk verdict is anchored to a proven carriageway, and the WGO
    # line that carried it is citable, unlike the previous always-empty evidence.
    assert sidewalk.classification_evidence == "OIDN=0"
    assert report["classes"] == {"carriageway_paved": 1, "sidewalk": 1, "unmapped": 0}
    assert report["area_ratios"]["carriageway_paved"] == pytest.approx(0.8)
    assert report["inference"] is False
    # The existing downstream guard must accept the output unchanged.
    assert validate_functional_faces(result)[1]["unmapped_faces"] == 0


def test_mixed_surface_code_still_counts_as_paved():
    result, _ = classify(faces_of(WCZ_SPLIT), wgo(WCZ_SPLIT), wegsegment((101, 12, 4, AXIS)))
    assert at(result, 5, 4).functional_class == "carriageway_paved"


@pytest.mark.parametrize("verh", [2, -8, -9])
def test_unpaved_or_unknown_surface_never_reaches_costing(verh):
    result, _ = classify(faces_of(WCZ_SPLIT), wgo(WCZ_SPLIT), wegsegment((101, verh, 4, AXIS)))
    carriageway = at(result, 5, 4)
    assert carriageway.functional_class == "unmapped"
    assert carriageway.classification_reason == "axis_surface_not_paved"
    assert carriageway.classification_evidence == f"101:VERH={verh}"


@pytest.mark.parametrize("status", [1, 2, 3, 5, -8, None])
def test_axis_not_in_service_is_not_evidence(status):
    result, _ = classify(faces_of(WCZ_SPLIT), wgo(WCZ_SPLIT), wegsegment((101, 1, status, AXIS)))
    carriageway = at(result, 5, 4)
    assert carriageway.functional_class == "unmapped"
    # STATUS != 4 removes the axis before it is ever consulted, so this
    # corridor has no carriageway anchor at all: it cannot lend a sidewalk
    # verdict to its neighbour either.
    assert carriageway.classification_reason == "no_carriageway_anchor_in_corridor"
    assert carriageway.axis_coverage_ratio == 0.0


def test_woz_boundary_is_reported_but_never_a_sidewalk():
    woz = (2, LineString([(0, 8), (10, 8)]))
    result, report = classify(
        faces_of(woz),
        wgo(woz),
        wegsegment((101, 1, 4, AXIS)),  # anchors the bottom face
    )
    shoulder = at(result, 5, 9)
    assert shoulder.functional_class == "unmapped"
    assert shoulder.classification_reason == "woz_only_not_sidewalk"
    assert shoulder.woz_boundary_ratio == pytest.approx(10 / 24)
    assert shoulder.wcz_boundary_ratio == 0.0
    assert report["classes"]["sidewalk"] == 0


def test_wrb_boundary_blocks_an_ambiguous_sidewalk_claim():
    wrb = (3, LineString([(0, 9), (10, 9)]))
    result, _ = classify(
        faces_of(WCZ_SPLIT, wrb),
        wgo(WCZ_SPLIT, wrb),
        wegsegment((101, 1, 4, AXIS)),  # anchors the bottom face so the band is even considered
    )
    band = at(result, 5, 8.5)
    assert band.functional_class == "unmapped"
    assert band.classification_reason == "wrb_boundary_dominant"
    assert band.wcz_boundary_ratio == pytest.approx(band.wrb_boundary_ratio)


def test_unresolved_topology_short_circuits_before_reading_axes():
    dangling = (1, LineString([(0, 8), (7, 8)]))
    result, report = classify(faces_of(dangling), wgo(dangling), wegsegment((101, 1, 4, AXIS)))
    assert set(result.functional_class) == {"unmapped"}
    assert set(result.classification_reason) == {"unresolved_topology:unresolved_lines"}
    assert result.axis_coverage_ratio.isna().all()
    assert report["reasons"] == {"unresolved_topology:unresolved_lines": 1}


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
    assert at(result, 2, 9).functional_class == "sidewalk"


def test_axis_lying_on_a_shared_face_boundary_proves_nothing():
    on_edge = LineString([(0, 8), (10, 8)])
    result, _ = classify(faces_of(WCZ_SPLIT), wgo(WCZ_SPLIT), wegsegment((101, 1, 4, on_edge)))
    carriageway = at(result, 5, 4)
    assert carriageway.axis_coverage_ratio == 0.0
    assert carriageway.functional_class == "unmapped"


def test_zero_coverage_threshold_still_does_not_let_an_edge_axis_prove_anything():
    # Regression: an axis fully collinear with a shared boundary has extent 0
    # in the corridor (both numerator and denominator exclude it identically),
    # so it never becomes a candidate at all — axis_coverage_ratio_min=0
    # cannot resurrect it either.
    on_edge = LineString([(0, 8), (10, 8)])
    result, _ = classify(
        faces_of(WCZ_SPLIT),
        wgo(WCZ_SPLIT),
        wegsegment((101, 1, 4, on_edge)),
        axis_coverage_ratio_min=0.0,
    )
    assert set(result.functional_class) == {"unmapped"}


def test_thresholds_are_explicit_and_inclusive_at_the_limit():
    faces, boundaries = faces_of(WCZ_SPLIT), wgo(WCZ_SPLIT)
    exact = 10 / 24
    anchor = wegsegment((101, 1, 4, AXIS))  # anchors the bottom face for both calls
    accepted = at(classify(faces, boundaries, anchor, wcz_ratio_min=exact)[0], 5, 9)
    assert accepted.functional_class == "sidewalk"
    refused = at(classify(faces, boundaries, anchor, wcz_ratio_min=exact + 1e-9)[0], 5, 9)
    assert refused.functional_class == "unmapped"
    assert refused.classification_reason == "no_dominant_wcz_boundary"


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
    assert report["classes"] == {"carriageway_paved": 2, "sidewalk": 2, "unmapped": 0}
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
        classify(faces, boundaries, axes, wcz_ratio_min=1.5)
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


# --- Regressions for bugs found by the adversarial review of the first cut ---


def test_axis_min_extent_rejects_a_short_graze_into_the_wrong_face():
    # A ~1.6 m in-service paved access stub sits entirely inside the sidewalk
    # face. Without an absolute floor on the axis's extent inside the
    # corridor, its coverage ratio is mechanically 1.0 (it never leaves the
    # single face it grazes), which used to promote that face to
    # carriageway_paved regardless of the 0.8 ratio threshold.
    graze = LineString([(3, 8.2), (3, 9.8)])
    guarded, _ = classify(
        faces_of(WCZ_SPLIT),
        wgo(WCZ_SPLIT),
        wegsegment((101, 1, 4, graze)),
        axis_min_extent_m=5.0,
    )
    assert at(guarded, 5, 9).functional_class != "carriageway_paved"
    # Documents the mechanism this closes: with the floor open, the graze still promotes it.
    unguarded, _ = classify(
        faces_of(WCZ_SPLIT),
        wgo(WCZ_SPLIT),
        wegsegment((101, 1, 4, graze)),
        axis_min_extent_m=0.0,
    )
    assert at(unguarded, 5, 9).functional_class == "carriageway_paved"


def test_carriageway_flanked_by_two_wcz_lines_is_not_misread_as_sidewalk():
    # The true carriageway, sandwiched between two Wcz lines, carries a
    # HIGHER Wcz perimeter share (0.625) than either flanking face (0.417
    # each): a bare Wcz-ratio rule inverts the verdict. Without any axis
    # evidence in this corridor, nothing anchors a sidewalk claim either, so
    # every face must stay unmapped rather than guess from geometry alone.
    lower_wcz = (1, LineString([(0, 2), (10, 2)]))
    upper_wcz = (1, LineString([(0, 8), (10, 8)]))
    faces = faces_of(lower_wcz, upper_wcz)
    result, _ = classify(faces, wgo(lower_wcz, upper_wcz), wegsegment())
    middle = at(result, 5, 5)
    assert middle.wcz_boundary_ratio == pytest.approx(20 / 32)
    assert middle.wcz_boundary_ratio > at(result, 5, 1).wcz_boundary_ratio
    assert set(result.functional_class) == {"unmapped"}
    assert set(result.classification_reason) == {"no_carriageway_anchor_in_corridor"}


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


def test_contradictory_axis_surfaces_stay_unmapped_not_silently_paved():
    # Two in-service axes, one paved (VERH=1) and one not (VERH=2), both fully
    # cover the same face. Letting "paved wins" silently drop the
    # contradicting evidence from the class, the reason and the evidence
    # column is itself an undocumented inference; a fail-closed contract
    # should surface the conflict instead of resolving it in secret.
    contradicting = LineString([(0, 4.5), (10, 4.5)])
    axes = wegsegment((101, 1, 4, AXIS), (102, 2, 4, contradicting))
    result, _ = classify(faces_of(WCZ_SPLIT), wgo(WCZ_SPLIT), axes)
    carriageway = at(result, 5, 4)
    assert carriageway.functional_class == "unmapped"
    assert carriageway.classification_reason == "contradictory_axis_surface"
    assert carriageway.classification_evidence == "101:VERH=1,102:VERH=2"


def test_axis_running_along_an_internal_boundary_before_crossing_is_not_penalised():
    # The axis hugs the Wcz boundary for 5 m, then turns and genuinely crosses
    # 4 m into the carriageway face. The hugging stretch is excluded from both
    # the numerator and the denominator (it is collinear with a shared
    # boundary, so it proves membership of neither face); the crossing
    # stretch is excluded from neither, so it alone decides the ratio.
    hugging = LineString([(0, 8), (5, 8), (5, 4)])
    result, _ = classify(faces_of(WCZ_SPLIT), wgo(WCZ_SPLIT), wegsegment((101, 1, 4, hugging)))
    carriageway = at(result, 8, 3)
    assert carriageway.functional_class == "carriageway_paved"
    assert carriageway.axis_coverage_ratio == pytest.approx(1.0)

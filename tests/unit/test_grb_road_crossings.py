import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import LineString, Point, Polygon, box

from gispulse.capabilities.vector.grb_road_crossings import build_grb_road_crossings

BOUNDS = (-100, -100, 100, 100)
KRUISPUNTZONE, WEGSEGMENT = 1, 2


def wbn(*specs):
    """``(oidn, polygon)`` or ``(oidn, polygon, type)``; type defaults to wegsegment."""
    return gpd.GeoDataFrame(
        {
            "OIDN": [s[0] for s in specs],
            "TYPE": [s[2] if len(s) > 2 else WEGSEGMENT for s in specs],
        },
        geometry=[s[1] for s in specs],
        crs=31370,
    )


def axes(*specs):
    """``(ws_id, line)`` or ``(ws_id, line, morf)`` or ``(ws_id, line, morf, status)``."""
    rows = [(*spec, 103, 4)[:4] if len(spec) == 2 else (*spec, 4)[:4] for spec in specs]
    return gpd.GeoDataFrame(
        {
            "WS_OIDN": [r[0] for r in rows],
            "MORF": [r[2] for r in rows],
            "STATUS": [r[3] for r in rows],
        },
        geometry=[r[1] for r in rows],
        crs=31370,
    )


def knw(*specs):
    """``(oidn, type, polygon)``."""
    return gpd.GeoDataFrame(
        {"OIDN": [s[0] for s in specs], "TYPE": [s[1] for s in specs]},
        geometry=[s[2] for s in specs],
        crs=31370,
    )


NO_KNW = gpd.GeoDataFrame(pd.DataFrame({"OIDN": [], "TYPE": []}), geometry=[], crs=31370)


def build(wbn_frame, axis_frame, knw_frame=NO_KNW, **overrides):
    options = {
        "coverage_bounds": BOUNDS,
        "length_tolerance_m": 1e-6,
        "area_tolerance_m2": 1e-6,
        "boundary_tolerance_m": 1e-3,
        "exclusion_buffer_m": 15.0,
        **overrides,
    }
    return build_grb_road_crossings(wbn_frame, axis_frame, knw_frame, **options)


def by_id(crossings):
    return {row.road_id: row for row in crossings.itertuples()}


def lines_of(axis):
    return list(getattr(axis, "geoms", [axis]))


def covers(coverage, x, y):
    return coverage.geometry.iloc[0].contains(Point(x, y))


# --- ground needs a WBN element: never a default ----------------------------


def test_wbn_element_carrying_a_motor_traffic_axis_is_ground():
    crossings, coverage, exclusions, report = build(
        wbn((1, box(0, 0, 10, 10))), axes((9, LineString([(0, 5), (10, 5)])))
    )
    row = by_id(crossings)["GRB:WBN:1"]
    assert row.structure == "ground"
    assert not row.complex_crossing
    assert "maaiveld" in row.structure_evidence
    assert row.axis_ids == "9" and row.morf_codes == "103"
    assert covers(coverage, 5, 5) and exclusions.empty
    assert report["structure_counts"] == {"ground": 1}
    assert report["axis_without_unit_m"] == 0


def test_fragments_of_one_carriageway_share_one_road_id_and_one_axis():
    # A straight road cut into arbitrary WBN elements is one physical carriageway.
    crossings, coverage, _, report = build(
        wbn((0, box(-10, 0, 0, 10)), (1, box(0, 0, 10, 10)), (2, box(10, 0, 20, 10))),
        axes((9, LineString([(-10, 5), (20, 5)]))),
    )
    (row,) = crossings.itertuples()
    assert row.road_id == "GRB:WBN:0" and row.source_ids == "0+1+2"
    assert row.geometry.area == pytest.approx(300)
    # Clipped against the united footprint: one continuous axis whose ends sit
    # on the boundary, never strictly inside, and no break at a fragment cut.
    (axis,) = lines_of(row.axis)
    assert axis.length == pytest.approx(30)
    assert not any(row.geometry.contains(Point(c)) for c in (axis.coords[0], axis.coords[-1]))
    assert report["wbn"]["ground_elements"] == 3 and report["wbn"]["ground_units"] == 1
    assert covers(coverage, 5, 5)


def test_an_element_holding_a_junction_node_is_never_merged():
    # A side street joins without a mapped kruispuntzone: its axis enters the
    # main element up to the node, yet the two are different streets.
    crossings, *_ = build(
        wbn((1, box(0, 0, 30, 10)), (2, box(10, 10, 20, 30))),
        axes(
            (11, LineString([(0, 5), (15, 5)])),
            (12, LineString([(15, 5), (30, 5)])),
            (13, LineString([(15, 5), (15, 30)])),
        ),
    )
    assert set(crossings.road_id) == {"GRB:WBN:1", "GRB:WBN:2"}


def test_an_element_reached_by_a_dead_end_street_is_never_merged():
    # Street 13 ends 3 m inside the main corridor without joining it: sharing
    # that stub must not merge the two streets.
    crossings, *_ = build(
        wbn((1, box(0, 0, 30, 10)), (2, box(10, 10, 20, 30))),
        axes((11, LineString([(0, 3), (30, 3)])), (13, LineString([(15, 7), (15, 30)]))),
    )
    assert set(crossings.road_id) == {"GRB:WBN:1", "GRB:WBN:2"}


def test_an_element_of_undocumented_type_is_never_merged():
    crossings, _, _, report = build(
        wbn((1, box(0, 0, 10, 10), "kruispuntzone"), (2, box(10, 0, 20, 10))),
        axes((9, LineString([(0, 5), (20, 5)]))),
    )
    assert set(crossings.road_id) == {"GRB:WBN:1", "GRB:WBN:2"}
    assert report["wbn"]["unrecognised_type"] == 1


def test_an_axis_on_the_inner_boundary_of_merged_fragments_belongs_to_the_unit():
    crossings, coverage, exclusions, _ = build(
        wbn((1, box(0, 0, 10, 10)), (2, box(10, 0, 20, 10))),
        # 8 leaves 9 at (10, 5) and runs exactly along the inner boundary x = 10.
        axes((9, LineString([(0, 5), (20, 5)])), (8, LineString([(10, 5), (10, 10)]))),
    )
    (row,) = crossings.itertuples()
    assert row.source_ids == "1+2" and row.axis_ids == "8,9"
    assert exclusions.empty and covers(coverage, 10, 8)


def test_fragments_are_not_grouped_across_categories():
    crossings, *_ = build(
        wbn((1, box(0, 0, 10, 10)), (2, box(10, 0, 20, 10))),
        axes((9, LineString([(0, 5), (10, 5)]), 101), (8, LineString([(10, 5), (20, 5)]), 107)),
    )
    assert set(crossings.road_id) == {"GRB:WBN:1", "GRB:WBN:2"}


def test_an_axis_with_no_wbn_is_never_ground_and_leaves_a_coverage_hole():
    crossings, coverage, exclusions, report = build(
        wbn((1, box(0, 0, 10, 10))),
        axes((9, LineString([(-5, 5), (15, 5)])), (8, LineString([(50, -50), (50, 50)]))),
    )
    assert "8" not in ",".join(crossings.axis_ids)
    assert not covers(coverage, 50, 0)
    assert set(exclusions.reason) == {"axis_without_unit"}
    # The 5 m stubs of axis 9 beyond the WBN element are unaccounted for as well
    # (less the boundary tolerance absorbed at each cut).
    assert report["axis_without_unit_m"] == pytest.approx(100 + 10, abs=0.01)


def test_reported_lengths_stop_at_the_bounds():
    _, _, _, report = build(
        wbn((1, box(0, 0, 10, 10))),
        axes((9, LineString([(0, 5), (10, 5)])), (8, LineString([(50, -500), (50, 500)]))),
    )
    assert report["axis_without_unit_m"] == pytest.approx(200)


@pytest.mark.parametrize(
    "morf, status",
    [(-8, 4), (103, 3), (120, 3)],  # unknown, carriageway or dienstweg not in service
)
def test_wbn_element_with_unresolved_axes_has_no_row_and_is_excluded(morf, status):
    crossings, coverage, exclusions, report = build(
        wbn((1, box(0, 0, 10, 10))), axes((9, LineString([(0, 5), (10, 5)]), morf, status))
    )
    assert crossings.empty
    assert set(exclusions.reason) == {"wbn_axes_unresolved"}
    assert not covers(coverage, 5, 5)
    assert report["wbn"]["unresolved"] == 1


def test_an_unresolved_branch_in_a_ground_element_is_kept_out_of_coverage():
    # The carriageway is still ground, but a trench crossing only the dienstweg
    # branch would see no axis: the branch's surroundings leave coverage.
    crossings, coverage, exclusions, report = build(
        wbn((1, box(0, 0, 100, 10))),
        axes((9, LineString([(0, 5), (100, 5)])), (7, LineString([(50, 5), (50, 10)]), -8)),
    )
    row = by_id(crossings)["GRB:WBN:1"]
    assert row.structure == "ground" and row.axis_ids == "9"
    assert set(exclusions.reason) == {"wbn_axes_unresolved"}
    assert not covers(coverage, 50, 8)
    assert covers(coverage, 90, 5)  # the verdict does not depend on the branch
    assert report["wbn"]["with_unresolved_axes"] == 1


def test_an_in_service_dienstweg_is_a_carriageway_and_an_aardeweg_is_not():
    # Decided for costing: a service road is bored under like a road, an
    # earthen track is trenched through.
    crossings, coverage, exclusions, report = build(
        wbn((1, box(0, 0, 10, 10)), (2, box(0, 20, 10, 30))),
        axes((9, LineString([(0, 5), (10, 5)]), 120), (8, LineString([(0, 25), (10, 25)]), 125)),
    )
    assert list(crossings.road_id) == ["GRB:WBN:1"]
    assert by_id(crossings)["GRB:WBN:1"].structure == "ground"
    assert exclusions.empty and covers(coverage, 5, 25)
    assert 120 in report["axis_classes"]["carriageway_morf"]
    assert 125 in report["axis_classes"]["non_carriageway_morf"]


def test_wbn_element_without_any_axis_has_no_row_and_is_excluded():
    crossings, coverage, exclusions, report = build(wbn((1, box(0, 0, 10, 10))), axes())
    assert crossings.empty
    assert set(exclusions.reason) == {"wbn_without_axis"}
    assert not covers(coverage, 5, 5)
    assert report["wbn"]["without_axis"] == 1


def test_documented_non_motor_corridor_stays_covered_without_a_row():
    crossings, coverage, exclusions, report = build(
        wbn((1, box(0, 0, 10, 10))), axes((9, LineString([(0, 5), (10, 5)]), 114))
    )
    assert crossings.empty
    assert exclusions.empty
    assert covers(coverage, 5, 5)
    assert report["wbn"]["non_carriageway"] == 1


def test_a_footprint_leaving_the_bounds_is_excluded():
    # Axes entirely outside the bounds were never acquired: the verdict of a
    # footprint reaching out there cannot be trusted inside either.
    crossings, coverage, exclusions, _ = build(
        wbn((1, box(90, 0, 110, 10))), axes((9, LineString([(90, 5), (110, 5)])))
    )
    assert list(crossings.road_id) == ["GRB:WBN:1"]
    assert "footprint_crosses_bounds" in set(exclusions.reason)
    assert not covers(coverage, 95, 5)


# --- axis geometry: tangent, collinear, cut at the crossroads ----------------


def test_an_axis_tangent_to_a_footprint_is_not_its_axis():
    road = wbn((1, box(0, 0, 10, 10)))
    lines = axes(
        (9, LineString([(0, 5), (10, 5)])),
        (7, LineString([(-5, 15), (0, 10), (5, 15)])),  # touches the (0, 10) corner only
        (6, LineString([(5, 10), (5, 30)])),  # ends on the boundary, from outside
    )
    crossings, *_ = build(road, lines)
    assert by_id(crossings)["GRB:WBN:1"].axis_ids == "9"


def test_an_axis_on_a_shared_boundary_belongs_to_neither_element_and_leaves_a_hole():
    road = wbn((1, box(0, 0, 10, 10)), (2, box(0, 10, 10, 20)))
    lines = axes(
        (9, LineString([(0, 5), (10, 5)])),
        (8, LineString([(0, 15), (10, 15)])),
        (7, LineString([(0, 10), (10, 10)])),  # exactly on the shared edge
    )
    crossings, coverage, exclusions, _ = build(road, lines)
    rows = by_id(crossings)
    assert rows["GRB:WBN:1"].axis_ids == "9"
    assert rows["GRB:WBN:2"].axis_ids == "8"
    assert "axis_without_unit" in set(exclusions.reason)
    assert not covers(coverage, 5, 10)


def test_axes_cut_at_the_crossroads_end_at_the_junction_node_only():
    # Kruispuntzone 2 between arms 1 and 3, with side arm 4; junction node (15, 5).
    road = wbn(
        (1, box(0, 0, 10, 10)),
        (2, box(10, 0, 20, 10), KRUISPUNTZONE),
        (3, box(20, 0, 30, 10)),
        (4, box(10, 10, 20, 20)),
    )
    lines = axes(
        (11, LineString([(0, 5), (15, 5)])),
        (12, LineString([(15, 5), (30, 5)])),
        (13, LineString([(15, 5), (15, 20)])),
    )
    crossings, *_ = build(road, lines)
    rows = by_id(crossings)
    # A kruispuntzone is never merged with the carriageways meeting there.
    assert set(rows) == {"GRB:WBN:1", "GRB:WBN:2", "GRB:WBN:3", "GRB:WBN:4"}
    junction = rows["GRB:WBN:2"]
    assert junction.axis_ids == "11,12,13"
    branches = lines_of(junction.axis)
    assert len(branches) == 3  # degree-3 node: never merged across a junction
    inner_ends = [
        Point(c)
        for line in branches
        for c in (line.coords[0], line.coords[-1])
        if junction.geometry.contains(Point(c))
    ]
    assert inner_ends and all(p.equals(Point(15, 5)) for p in inner_ends)


def test_contiguous_axis_pieces_are_joined_at_a_degree_two_node():
    crossings, *_ = build(
        wbn((1, box(0, 0, 20, 10))),
        axes((11, LineString([(0, 5), (10, 5)])), (12, LineString([(10, 5), (20, 5)]))),
    )
    (axis,) = lines_of(by_id(crossings)["GRB:WBN:1"].axis)
    assert axis.length == pytest.approx(20)


# --- structures: a level relation, grouped by road_id ------------------------


def _overpass():
    """U crosses L at (5, 5) with no node, inside bridge polygon 77."""
    return axes(("U", LineString([(-20, 5), (30, 5)])), ("L", LineString([(5, -20), (5, 30)])))


def _corridors():
    """WBN on both sides of bridge 77 for U and L: the structure is the only gap."""
    return wbn(
        (1, box(-20, 0, 0, 10)),
        (2, box(10, 0, 30, 10)),
        (3, box(0, -20, 10, 0)),
        (4, box(0, 10, 10, 30)),
    )


def test_node_less_crossing_inside_a_bridge_is_grade_separated():
    crossings, coverage, exclusions, report = build(
        _corridors(), _overpass(), knw((77, 1, box(0, 0, 10, 10)))
    )
    row = by_id(crossings)["GRB:KNW:77"]
    assert row.structure == "grade_separated"
    assert "UxL" in row.structure_evidence
    assert covers(coverage, 5, 5) and exclusions.empty
    assert report["structures"]["grade_separated"] == 1
    assert report["axis_without_unit_m"] == 0


def test_bridge_with_a_single_axis_is_unknown_never_ground():
    lines = axes(("U", LineString([(-20, 5), (30, 5)])))
    crossings, coverage, exclusions, report = build(
        wbn((1, box(-20, 0, 0, 10)), (2, box(10, 0, 30, 10))),
        lines,
        knw((77, 1, box(0, 0, 10, 10))),
    )
    row = by_id(crossings)["GRB:KNW:77"]
    assert row.structure == "unknown"
    assert "single_axis" in row.structure_evidence
    assert set(exclusions.reason) == {"structure_single_axis"}
    assert not covers(coverage, 5, 5)
    assert report["structures"]["unknown"] == {"single_axis": 1}


def test_an_axis_ending_inside_a_structure_makes_it_unknown():
    lines = axes(
        ("U", LineString([(-20, 5), (30, 5)])),
        ("L", LineString([(5, -20), (5, 5)])),  # ends on the deck: a junction or dead end
        ("M", LineString([(8, -20), (8, 30)])),
    )
    crossings, *_ = build(wbn((1, box(-20, 0, 0, 10))), lines, knw((77, 12, box(0, 0, 10, 10))))
    assert "axis_ends_inside" in by_id(crossings)["GRB:KNW:77"].structure_evidence


def test_an_axis_crossing_nothing_inside_a_structure_makes_it_unknown():
    lines = axes(
        ("U", LineString([(-20, 5), (30, 5)])),
        ("L", LineString([(5, -20), (5, 30)])),
        ("P", LineString([(-20, 8), (30, 8)])),  # parallel to U, crosses L
        ("Q", LineString([(-20, 2), (30, 2)])),
    )
    # Q and P both cross L, so every axis takes part: grade separated.
    crossings, *_ = build(wbn((1, box(-20, 0, 0, 10))), lines, knw((77, 1, box(0, 0, 10, 10))))
    assert by_id(crossings)["GRB:KNW:77"].structure == "grade_separated"
    lonely = axes(
        ("U", LineString([(-20, 5), (30, 5)])),
        ("L", LineString([(5, -20), (5, 30)])),
        ("P", LineString([(-20, 8), (2, 8), (2, 30)])),  # inside, crosses neither U nor L
    )
    crossings, *_ = build(wbn((1, box(-20, 0, 0, 10))), lonely, knw((77, 1, box(0, 0, 10, 10))))
    assert "axis_not_crossed" in by_id(crossings)["GRB:KNW:77"].structure_evidence


def test_an_unresolved_axis_on_a_structure_keeps_it_out_of_coverage():
    lines = axes(
        ("U", LineString([(-20, 5), (30, 5)])),
        ("L", LineString([(5, -20), (5, 30)])),
        ("D", LineString([(8, -20), (8, 8)]), -8),  # an unknown way ending on the deck
    )
    crossings, coverage, exclusions, report = build(
        _corridors(), lines, knw((77, 1, box(0, 0, 10, 10)))
    )
    assert by_id(crossings)["GRB:KNW:77"].structure == "grade_separated"
    assert "structure_axes_unresolved" in set(exclusions.reason)
    assert not covers(coverage, 8, 8)
    assert report["structures"]["with_unresolved_axes"] == 1


def test_touching_structure_polygons_are_one_road_id_with_one_footprint():
    # The deck is split in two KNW polygons; L only crosses the second one.
    # Evaluated alone, polygon 1 would be a single-axis `unknown`.
    crossings, *_ = build(
        wbn((1, box(-20, 0, 0, 10))),
        axes(("U", LineString([(-20, 5), (30, 5)])), ("L", LineString([(8, -20), (8, 30)]))),
        knw((501, 1, box(0, 0, 5, 10)), (502, 1, box(5, 0, 10, 10))),
    )
    knw_rows = crossings[crossings.source_layer == "KNW"]
    assert list(knw_rows.road_id) == ["GRB:KNW:501+502"]
    assert knw_rows.geometry.iloc[0].area == pytest.approx(100)
    assert knw_rows.structure.iloc[0] == "grade_separated"
    assert crossings.road_id.is_unique


def test_structure_without_any_drivable_axis_is_no_road_and_stays_covered():
    crossings, coverage, _, report = build(
        wbn((1, box(-20, 0, 0, 10))),
        axes(("U", LineString([(-20, 5), (0, 5)])), ("F", LineString([(5, -20), (5, 30)]), 114)),
        knw((77, 1, box(0, 0, 10, 10)), (78, 1, box(40, 40, 50, 50))),
    )
    assert not set(by_id(crossings)) & {"GRB:KNW:77", "GRB:KNW:78"}
    assert covers(coverage, 5, 5) and covers(coverage, 45, 45)
    assert report["structures"]["without_carriageway"] == 2


def test_non_structure_knw_codes_are_ignored():
    crossings, *_ = build(
        wbn((1, box(-20, 0, 0, 10))), _overpass(), knw((77, 5, box(0, 0, 10, 10)))
    )  # 5 = pijler
    assert list(crossings.source_layer) == ["WBN"]


# --- no spatial duplicate ----------------------------------------------------


def test_structures_take_priority_over_overlapping_wbn():
    crossings, _, _, report = build(
        wbn((1, box(-20, 0, 2, 10))), _overpass(), knw((77, 1, box(0, 0, 10, 10)))
    )
    rows = by_id(crossings)
    assert rows["GRB:WBN:1"].geometry.area == pytest.approx(200)
    assert rows["GRB:WBN:1"].geometry.intersection(rows["GRB:KNW:77"].geometry).area == 0
    assert report["wbn"]["overlap_trimmed"] == 1


def test_overlapping_wbn_elements_never_claim_the_same_area_twice():
    # Distinct carriageways: two rows, the later element trimmed.
    crossings, *_ = build(
        wbn((1, box(0, 0, 10, 10)), (2, box(8, 0, 20, 10))),
        axes((9, LineString([(0, 5), (5, 5)])), (8, LineString([(12, 5), (20, 5)]))),
    )
    rows = by_id(crossings)
    assert rows["GRB:WBN:1"].geometry.area == pytest.approx(100)
    assert rows["GRB:WBN:2"].geometry.area == pytest.approx(100)
    assert rows["GRB:WBN:1"].geometry.intersection(rows["GRB:WBN:2"].geometry).area == 0
    # Fragments of one carriageway: one row whose area is counted once.
    crossings, *_ = build(
        wbn((1, box(0, 0, 10, 10)), (2, box(8, 0, 20, 10))),
        axes((9, LineString([(0, 5), (20, 5)]))),
    )
    (row,) = crossings.itertuples()
    assert row.source_ids == "1+2" and row.geometry.area == pytest.approx(200)


# --- category and level consistency -----------------------------------------


def test_complex_crossing_comes_from_the_motorway_morphology_only():
    crossings, *_ = build(
        wbn((1, box(0, 0, 10, 10)), (2, box(0, 20, 10, 30))),
        axes((9, LineString([(0, 5), (10, 5)]), 101), (8, LineString([(0, 25), (10, 25)]), 102)),
    )
    rows = by_id(crossings)
    assert bool(rows["GRB:WBN:1"].complex_crossing) is True
    assert bool(rows["GRB:WBN:2"].complex_crossing) is False
    crossings, _, _, report = build(
        wbn((2, box(0, 20, 10, 30))),
        axes((8, LineString([(0, 25), (10, 25)]), 102)),
        complex_morf_codes=(101, 102),
    )
    assert bool(crossings.complex_crossing.iloc[0]) is True
    assert report["thresholds"]["complex_morf_codes"] == [101, 102]


def test_node_less_crossing_outside_any_structure_is_excluded_from_coverage():
    _, coverage, exclusions, report = build(
        wbn((1, box(-20, 0, 30, 10)), (2, box(0, -20, 10, 0)), (3, box(0, 10, 10, 30))),
        _overpass(),
    )
    assert report["nodeless_crossings_outside_structures"] == 1
    assert "nodeless_crossing_outside_structure" in set(exclusions.reason)
    assert not covers(coverage, 5, 5)


def test_a_self_crossing_axis_outside_any_structure_is_excluded_from_coverage():
    spiral = LineString([(0, 2), (20, 2), (20, 8), (10, 8), (10, 0)])  # crosses itself at (10, 2)
    _, coverage, exclusions, report = build(wbn((1, box(0, 0, 20, 10))), axes((9, spiral, 107)))
    assert report["nodeless_crossings_outside_structures"] == 1
    assert "nodeless_crossing_outside_structure" in set(exclusions.reason)
    assert not covers(coverage, 10, 2)


# --- output contract ---------------------------------------------------------


def test_artifact_round_trips_through_geoparquet_with_both_crs(tmp_path):
    from gispulse.core.io.geoparquet import write_geoparquet
    from gispulse.persistence.io import read_geoparquet

    crossings, coverage, *_ = build(_corridors(), _overpass(), knw((77, 1, box(0, 0, 10, 10))))
    path = tmp_path / "road_crossings.geoparquet"
    write_geoparquet(crossings, str(path))
    frame = read_geoparquet(str(path))
    assert {"road_id", "axis", "structure", "complex_crossing"} <= set(frame.columns)
    assert frame.crs.to_epsg() == 31370
    assert frame["axis"].crs == frame.crs
    assert set(frame.geom_type) <= {"Polygon", "MultiPolygon"}
    assert set(frame["axis"].geom_type) <= {"LineString", "MultiLineString"}
    assert set(frame.structure) <= {"ground", "grade_separated", "unknown"}
    assert frame.complex_crossing.dtype == bool
    assert not frame.road_id.isna().any() and frame.road_id.is_unique
    assert all(line.is_simple for axis in frame["axis"] for line in lines_of(axis))
    assert coverage.crs.to_epsg() == 31370 and len(coverage) == 1


def test_exclusions_are_polygonal_even_when_they_only_touch_the_bounds():
    _, _, exclusions, _ = build(wbn((1, box(100, 0, 110, 10))), axes())
    assert set(exclusions.geom_type) <= {"Polygon", "MultiPolygon"}


def test_empty_inputs_give_an_empty_but_typed_artifact_and_full_coverage():
    crossings, coverage, exclusions, report = build(
        gpd.GeoDataFrame({"OIDN": [], "TYPE": []}, geometry=[], crs=31370),
        axes(),
        gpd.GeoDataFrame({"TYPE": []}, geometry=[], crs=31370),  # no KNW id column at all
    )
    assert crossings.empty and exclusions.empty
    assert crossings["axis"].crs == crossings.crs
    assert coverage.geometry.iloc[0].equals(box(*BOUNDS))
    assert report["rows"] == 0


@pytest.mark.parametrize(
    "mutate, code",
    [
        (lambda w, a, k: (w.to_crs(3812), a, k), "GRB_CROSSINGS_CRS_INVALID"),
        (lambda w, a, k: (w, a.drop(columns="MORF"), k), "GRB_CROSSINGS_FIELD_MISSING"),
        (lambda w, a, k: (w.drop(columns="TYPE"), a, k), "GRB_CROSSINGS_FIELD_MISSING"),
        (lambda w, a, k: (w, a, k.drop(columns="OIDN")), "GRB_CROSSINGS_FIELD_MISSING"),
        (lambda w, a, k: (w, pd.concat([a, a]), k), "GRB_CROSSINGS_ID_INVALID"),
        (lambda w, a, k: (w, a.assign(WS_OIDN=[None, "L"]), k), "GRB_CROSSINGS_ID_INVALID"),
        (lambda w, a, k: (w, a, pd.concat([k, k])), "GRB_CROSSINGS_ID_INVALID"),
        (
            lambda w, a, k: (wbn((1, Polygon([(0, 0), (1, 1), (1, 0), (0, 1)]))), a, k),
            "GRB_CROSSINGS_GEOMETRY_INVALID",
        ),
    ],
)
def test_data_defects_raise_dedicated_codes(mutate, code):
    w, a, k = mutate(wbn((1, box(-20, 0, 0, 10))), _overpass(), knw((77, 1, box(0, 0, 10, 10))))
    with pytest.raises(ValueError, match=code):
        build(w, a, k)


@pytest.mark.parametrize(
    "overrides",
    [{"exclusion_buffer_m": 0}, {"length_tolerance_m": -1}, {"coverage_bounds": (1, 0, 0, 1)}],
)
def test_invalid_parameters_are_rejected(overrides):
    with pytest.raises(ValueError, match="GRB_CROSSINGS_"):
        build(wbn((1, box(0, 0, 10, 10))), axes(), **overrides)

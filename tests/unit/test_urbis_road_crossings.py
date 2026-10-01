import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import LineString, Point, Polygon, box

from gispulse.capabilities.vector.urbis_road_crossings import build_urbis_road_crossings

AREA = box(-100, -100, 100, 100)


def surfaces(*specs):
    """``(inspire_id, polygon, type, lvl)``."""
    return gpd.GeoDataFrame(
        {
            "INSPIRE_ID": [s[0] for s in specs],
            "TYPE": [s[2] for s in specs],
            "LVL": [s[3] for s in specs],
        },
        geometry=[s[1] for s in specs],
        crs=31370,
    )


def axes(*specs):
    """``(inspire_id, line)`` or ``(inspire_id, line, type, lvl, hierarchy)``."""
    rows = [spec if len(spec) == 5 else (*spec, "S", 0, "DR") for spec in specs]
    return gpd.GeoDataFrame(
        {
            "INSPIRE_ID": [r[0] for r in rows],
            "TYPE": [r[2] for r in rows],
            "LVL": [r[3] for r in rows],
            "HIERARCHY": [r[4] for r in rows],
        },
        geometry=[r[1] for r in rows],
        crs=31370,
    )


def structures(*specs):
    """``(inspire_id, polygon, type)``."""
    return gpd.GeoDataFrame(
        {
            "INSPIRE_ID": [s[0] for s in specs],
            "TYPE": [s[2] for s in specs],
            "LVL": [1] * len(specs),
        },
        geometry=[s[1] for s in specs],
        crs=31370,
    )


NONE = structures()


def build(surface_frame, axis_frame, bridges=NONE, tunnels=NONE, **overrides):
    options = {
        "coverage_area": AREA,
        "length_tolerance_m": 1e-6,
        "area_tolerance_m2": 1e-6,
        "boundary_tolerance_m": 1e-3,
        "exclusion_buffer_m": 15.0,
        **overrides,
    }
    return build_urbis_road_crossings(surface_frame, axis_frame, bridges, tunnels, **options)


def by_id(crossings):
    return {row.road_id: row for row in crossings.itertuples()}


def covers(coverage, x, y):
    return coverage.geometry.iloc[0].contains(Point(x, y))


def _overpass():
    """U crosses road bridge B over L; L is drawn at LVL 0 under the deck, U at LVL 1."""
    return (
        surfaces(
            ("1", box(-20, 0, 0, 10), "S", 0),
            ("2", box(10, 0, 30, 10), "S", 0),
            ("3", box(0, -20, 10, 0), "S", 0),
            ("4", box(0, 10, 10, 30), "S", 0),
            ("5", box(0, 0, 10, 10), "S", 1),
            ("6", box(0, 0, 10, 10), "S", 0),
        ),
        axes(
            ("U1", LineString([(-20, 5), (0, 5)])),
            ("U2", LineString([(0, 5), (10, 5)]), "S", 1, "DR"),
            ("U3", LineString([(10, 5), (30, 5)])),
            ("L", LineString([(5, -20), (5, 30)])),
        ),
    )


def test_a_level_zero_carriageway_surface_is_ground_with_its_level_zero_axes():
    crossings, coverage, exclusions, _ = build(
        surfaces(("1", box(0, 0, 10, 10), "S", 0), ("9", box(0, 10, 10, 13), "SW", 0)),
        axes(("a", LineString([(0, 5), (10, 5)]))),
    )
    row = by_id(crossings)["UrbIS:1"]
    assert row.structure == "ground" and row.axis_ids == "a"
    assert covers(coverage, 5, 5) and exclusions.empty
    assert "UrbIS:9" not in by_id(crossings)  # a sidewalk is no carriageway unit


def test_a_road_bridge_is_grade_separated_and_hides_the_road_under_it():
    s, a = _overpass()
    crossings, coverage, exclusions, report = build(
        s, a, bridges=structures(("B", box(0, 0, 10, 10), "ROB"))
    )
    rows = by_id(crossings)
    assert rows["UrbIS:ROB:B"].structure == "grade_separated"
    assert rows["UrbIS:ROB:B"].axis_ids == "L,U2"
    assert "UrbIS:6" not in rows  # the LVL 0 surface under the deck is claimed by it
    assert covers(coverage, 5, 5) and exclusions.empty
    assert report["structures"]["road_bridges"]["rows"] == 1


def test_a_railway_bridge_leaves_the_road_under_it_at_ground():
    crossings, coverage, _, _ = build(
        surfaces(("1", box(0, 0, 10, 10), "S", 0)),
        axes(("a", LineString([(0, 5), (10, 5)]))),
        bridges=structures(("R", box(3, 0, 6, 10), "RAB")),
    )
    assert list(crossings.road_id) == ["UrbIS:1"]
    assert by_id(crossings)["UrbIS:1"].structure == "ground"
    assert covers(coverage, 5, 5)


def test_a_road_tunnel_keeps_its_axis_apart_from_the_road_above():
    crossings, coverage, exclusions, _ = build(
        surfaces(
            ("1", box(-20, 10, 30, 20), "S", 0),  # surface road above
            ("2", box(0, -20, 10, 40), "S", -1),  # tunnel road
        ),
        axes(
            ("S", LineString([(-20, 15), (30, 15)])),
            ("T", LineString([(5, -20), (5, 40)]), "S", -1, "MAR"),
        ),
        tunnels=structures(("TU", box(0, -20, 10, 40), "ROT")),
    )
    rows = by_id(crossings)
    assert rows["UrbIS:ROT:TU"].structure == "grade_separated"
    assert rows["UrbIS:ROT:TU"].axis_ids == "T"
    # The road above stays ground and carries only its own LVL 0 axis.
    assert rows["UrbIS:1"].structure == "ground" and rows["UrbIS:1"].axis_ids == "S"
    assert covers(coverage, 5, 15) and exclusions.empty


def test_a_level_without_its_structure_contradicts_the_layers():
    crossings, coverage, exclusions, report = build(
        surfaces(("1", box(0, 0, 10, 10), "S", 1)),  # above 0, but no road bridge
        axes(("a", LineString([(0, 5), (10, 5)]), "S", 1, "DR")),
    )
    assert crossings.empty
    assert {"surface_level_without_structure", "axis_without_unit"} <= set(exclusions.reason)
    assert not covers(coverage, 5, 5)
    assert report["surfaces"]["level_contradictions"] == 1


def test_a_level_minus_one_axis_outside_any_tunnel_leaves_a_hole():
    _, coverage, exclusions, _ = build(
        surfaces(("1", box(0, 0, 10, 10), "S", 0)),
        axes(
            ("a", LineString([(0, 5), (10, 5)])),
            ("t", LineString([(5, 0), (5, 10)]), "S", -1, "DR"),
        ),
    )
    assert "axis_without_unit" in set(exclusions.reason)
    assert not covers(coverage, 5, 8)


def test_a_carriageway_surface_without_axis_is_excluded():
    crossings, coverage, exclusions, report = build(
        surfaces(("1", box(0, 0, 10, 10), "S", 0)), axes()
    )
    assert crossings.empty
    assert set(exclusions.reason) == {"surface_without_axis"}
    assert not covers(coverage, 5, 5)
    assert report["surfaces"]["without_axis"] == 1


@pytest.mark.parametrize("kind, expected", [("S", "ground"), ("A", None), ("PT", "covered")])
def test_axis_types_decide_carriageways(kind, expected):
    crossings, coverage, exclusions, _ = build(
        surfaces(("1", box(0, 0, 10, 10), "S", 0)),
        axes(("a", LineString([(0, 5), (10, 5)]), kind, 0, "DR")),
    )
    if expected == "ground":
        assert by_id(crossings)["UrbIS:1"].structure == "ground"
    elif expected == "covered":  # pedestrian, metro, rail: no carriageway
        assert crossings.empty and exclusions.empty and covers(coverage, 5, 5)
    else:  # a ramp is unresolved
        assert crossings.empty and not covers(coverage, 5, 5)


def test_complex_crossing_comes_from_the_autoroute_hierarchy():
    crossings, *_ = build(
        surfaces(("1", box(0, 0, 10, 10), "S", 0), ("2", box(0, 20, 10, 30), "S", 0)),
        axes(
            ("a", LineString([(0, 5), (10, 5)]), "S", 0, "H"),
            ("b", LineString([(0, 25), (10, 25)]), "S", 0, "MER"),
        ),
    )
    rows = by_id(crossings)
    assert bool(rows["UrbIS:1"].complex_crossing) is True
    assert bool(rows["UrbIS:2"].complex_crossing) is False


def test_segment_fragments_merge_but_an_intersection_never_does():
    crossings, *_ = build(
        surfaces(
            ("1", box(0, 0, 10, 10), "S", 0),
            ("2", box(10, 0, 20, 10), "S", 0),
            ("3", box(20, 0, 30, 10), "I", 0),
        ),
        axes(("a", LineString([(0, 5), (30, 5)]))),
    )
    rows = by_id(crossings)
    assert set(rows) == {"UrbIS:1", "UrbIS:3"}
    assert rows["UrbIS:1"].source_ids == "1+2"


def test_empty_structure_layers_are_accepted():
    crossings, *_ = build(
        surfaces(("1", box(0, 0, 10, 10), "S", 0)),
        axes(("a", LineString([(0, 5), (10, 5)]))),
        bridges=gpd.GeoDataFrame(geometry=[], crs=31370),
        tunnels=gpd.GeoDataFrame(geometry=[], crs=31370),
    )
    assert list(crossings.road_id) == ["UrbIS:1"]


def test_artifact_contract_round_trips(tmp_path):
    from gispulse.core.io.geoparquet import write_geoparquet
    from gispulse.persistence.io import read_geoparquet

    s, a = _overpass()
    crossings, *_ = build(s, a, bridges=structures(("B", box(0, 0, 10, 10), "ROB")))
    path = tmp_path / "road_crossings.geoparquet"
    write_geoparquet(crossings, str(path))
    frame = read_geoparquet(str(path))
    assert frame.crs.to_epsg() == 31370 and frame["axis"].crs == frame.crs
    assert frame.road_id.is_unique and frame.complex_crossing.dtype == bool
    assert set(frame.structure) <= {"ground", "grade_separated", "unknown"}


@pytest.mark.parametrize(
    "mutate, code",
    [
        (lambda s, a: (s.to_crs(3812), a), "URBIS_CROSSINGS_CRS_INVALID"),
        (lambda s, a: (s.drop(columns="LVL"), a), "URBIS_CROSSINGS_FIELD_MISSING"),
        (lambda s, a: (s, a.drop(columns="HIERARCHY")), "URBIS_CROSSINGS_FIELD_MISSING"),
        (lambda s, a: (pd.concat([s, s]), a), "URBIS_CROSSINGS_ID_INVALID"),
        (
            lambda s, a: (surfaces(("9", Polygon([(0, 0), (1, 1), (1, 0), (0, 1)]), "S", 0)), a),
            "URBIS_CROSSINGS_GEOMETRY_INVALID",
        ),
    ],
)
def test_data_defects_raise_dedicated_codes(mutate, code):
    s, a = mutate(*_overpass())
    with pytest.raises(ValueError, match=code):
        build(s, a)

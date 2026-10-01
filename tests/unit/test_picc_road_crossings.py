import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import LineString, Point, Polygon, box

from gispulse.capabilities.vector.picc_road_crossings import build_picc_road_crossings

AREA = box(-100, -100, 100, 100)
NONE = float("nan")


def surfaces(*specs):
    """``(georef_id, polygon, nature, niveau)``; ``NONE`` is an empty NIVEAU."""
    return gpd.GeoDataFrame(
        {
            "GEOREF_ID": [s[0] for s in specs],
            "NATUR_DESC": [s[2] for s in specs],
            "NIVEAU": [s[3] for s in specs],
        },
        geometry=[s[1] for s in specs],
        crs=31370,
    )


def axes(*specs):
    """``(georef_id, line)`` or ``(georef_id, line, nature)``; nature defaults to Communale."""
    return gpd.GeoDataFrame(
        {
            "GEOREF_ID": [s[0] for s in specs],
            "NATUR_DESC": [s[2] if len(s) > 2 else "Communale" for s in specs],
        },
        geometry=[s[1] for s in specs],
        crs=31370,
    )


def build(surface_frame, axis_frame, **overrides):
    options = {
        "coverage_area": AREA,
        "length_tolerance_m": 1e-6,
        "area_tolerance_m2": 1e-6,
        "boundary_tolerance_m": 1e-3,
        "exclusion_buffer_m": 15.0,
        **overrides,
    }
    return build_picc_road_crossings(surface_frame, axis_frame, **options)


def by_id(crossings):
    return {row.road_id: row for row in crossings.itertuples()}


def covers(coverage, x, y):
    return coverage.geometry.iloc[0].contains(Point(x, y))


def _overpass():
    """U runs over bridge deck 7 (NIVEAU 1); L passes under it on an OVA 0 surface 8."""
    return (
        surfaces(
            ("1", box(-20, 0, 0, 10), "Tronçon", NONE),
            ("2", box(10, 0, 30, 10), "Tronçon", NONE),
            ("3", box(0, -20, 10, 0), "Tronçon", NONE),
            ("4", box(0, 10, 10, 30), "Tronçon", NONE),
            ("7", box(0, 0, 10, 10), "Ouvrage d'art", 1),
            ("8", box(0, 0, 10, 10), "Ouvrage d'art", 0),
        ),
        axes(("U", LineString([(-20, 5), (30, 5)])), ("L", LineString([(5, -20), (5, 30)]))),
    )


def test_a_troncon_with_empty_niveau_is_ground_by_the_continuous_inventory():
    crossings, coverage, exclusions, report = build(
        surfaces(("1", box(0, 0, 10, 10), "Tronçon", NONE)),
        axes(("a", LineString([(0, 5), (10, 5)]))),
    )
    row = by_id(crossings)["PICC:1"]
    assert row.structure == "ground"
    assert row.axis_ids == "a" and row.morf_codes == "Communale"
    assert covers(coverage, 5, 5) and exclusions.empty
    assert report["surfaces"]["ground_surfaces"] == 1


def test_a_bridge_deck_is_grade_separated_and_hides_the_ground_surface_under_it():
    crossings, coverage, exclusions, report = build(*_overpass())
    rows = by_id(crossings)
    deck = rows["PICC:OVA:7"]
    assert deck.structure == "grade_separated"
    assert "NIVEAU ≥ 1" in deck.structure_evidence
    assert deck.axis_ids == "L,U"  # both roads inside the deck footprint
    # The OVA 0 surface under the deck is entirely claimed by the deck: no ground
    # row there, so a trench following U over the deck never bores under L.
    assert "PICC:8" not in rows
    assert covers(coverage, 5, 5) and exclusions.empty
    assert report["structures"]["bridges"] == 1


def test_a_single_axis_bridge_is_grade_separated_by_its_stated_level():
    # Unlike the GRB, the PICC states the deck's level: a road over a river is
    # above ground without any second axis.
    crossings, *_ = build(
        surfaces(
            ("1", box(-20, 0, 0, 10), "Tronçon", NONE),
            ("2", box(10, 0, 30, 10), "Tronçon", NONE),
            ("7", box(0, 0, 10, 10), "Ouvrage d'art", 1),
        ),
        axes(("U", LineString([(-20, 5), (30, 5)]))),
    )
    assert by_id(crossings)["PICC:OVA:7"].structure == "grade_separated"


def test_a_tunnel_is_unknown_with_every_axis_inside_and_never_covered():
    crossings, coverage, exclusions, report = build(
        surfaces(
            ("1", box(0, 0, 10, 30), "Ouvrage d'art", -1),  # tunnel along y
            ("2", box(-20, 10, 30, 20), "Ouvrage d'art", 0),  # surface road above
        ),
        axes(("T", LineString([(5, 0), (5, 30)])), ("S", LineString([(-20, 15), (30, 15)]))),
    )
    rows = by_id(crossings)
    tunnel = rows["PICC:OVA:1"]
    # PICC axes carry no level: inside the tunnel footprint the tunnel's axis
    # cannot be told from the street above, so the consumer must ask for proof.
    assert tunnel.structure == "unknown" and tunnel.axis_ids == "S,T"
    # The ground surface above keeps only its part outside the tunnel footprint,
    # so no ground row ever carries the tunnel's axis.
    above = rows["PICC:2"]
    assert above.structure == "ground" and above.axis_ids == "S"
    assert above.geometry.intersection(tunnel.geometry).area == 0
    assert "tunnel_axis_level_unknown" in set(exclusions.reason)
    assert not covers(coverage, 5, 15) and not covers(coverage, 5, 25)
    assert covers(coverage, -15, 15)
    assert report["structures"]["tunnels_unknown"] == 1


@pytest.mark.parametrize("niveau", [1.5, "1,0", "pont"])
def test_an_unreadable_niveau_is_never_read_as_empty(niveau):
    crossings, coverage, exclusions, _ = build(
        surfaces(("1", box(0, 0, 10, 10), "Tronçon", niveau)),
        axes(("a", LineString([(0, 5), (10, 5)]))),
    )
    assert crossings.empty
    assert "surface_level_unresolved" in set(exclusions.reason)
    assert not covers(coverage, 5, 5)


def test_empty_layers_without_columns_give_an_empty_artifact():
    crossings, coverage, exclusions, report = build(
        gpd.GeoDataFrame(geometry=gpd.GeoSeries([], crs=31370), crs=31370),
        gpd.GeoDataFrame(geometry=gpd.GeoSeries([], crs=31370), crs=31370),
    )
    assert crossings.empty and exclusions.empty and report["rows"] == 0
    assert coverage.geometry.iloc[0].equals(AREA)


def test_an_ouvrage_without_niveau_is_unresolved_never_ground():
    crossings, coverage, exclusions, report = build(
        surfaces(("1", box(0, 0, 10, 10), "Ouvrage d'art", NONE)),
        axes(("a", LineString([(0, 5), (10, 5)]))),
    )
    assert crossings.empty
    assert "surface_level_unresolved" in set(exclusions.reason)
    assert not covers(coverage, 5, 5)
    assert report["surfaces"]["unresolved_surfaces"] == 1


@pytest.mark.parametrize(
    "nature, expected",
    [
        ("Communale", "ground"),
        ("Nationale", "ground"),
        ("Autoroute", "ground"),
        ("Chemin ou sentier", None),
        ("Piste cyclable", None),
    ],
)
def test_axis_natures_decide_carriageways(nature, expected):
    crossings, coverage, exclusions, _ = build(
        surfaces(("1", box(0, 0, 10, 10), "Tronçon", NONE)),
        axes(("a", LineString([(0, 5), (10, 5)]), nature)),
    )
    if expected is None:
        assert crossings.empty and exclusions.empty and covers(coverage, 5, 5)
    else:
        assert by_id(crossings)["PICC:1"].structure == expected


def test_an_undocumented_axis_nature_is_unresolved():
    crossings, coverage, exclusions, _ = build(
        surfaces(("1", box(0, 0, 10, 10), "Tronçon", NONE)),
        axes(("a", LineString([(0, 5), (10, 5)]), "Inconnue")),
    )
    assert crossings.empty
    assert set(exclusions.reason) == {"surface_axes_unresolved"}
    assert not covers(coverage, 5, 5)


def test_complex_crossing_comes_from_the_autoroute_nature():
    crossings, *_ = build(
        surfaces(
            ("1", box(0, 0, 10, 10), "Tronçon", NONE), ("2", box(0, 20, 10, 30), "Tronçon", NONE)
        ),
        axes(
            ("a", LineString([(0, 5), (10, 5)]), "Autoroute"),
            ("b", LineString([(0, 25), (10, 25)]), "Nationale"),
        ),
    )
    rows = by_id(crossings)
    assert bool(rows["PICC:1"].complex_crossing) is True
    assert bool(rows["PICC:2"].complex_crossing) is False


def test_troncon_fragments_merge_but_a_carrefour_never_does():
    crossings, *_ = build(
        surfaces(
            ("1", box(0, 0, 10, 10), "Tronçon", NONE),
            ("2", box(10, 0, 20, 10), "Tronçon", NONE),
            ("3", box(20, 0, 30, 10), "Carrefour", NONE),
        ),
        axes(("a", LineString([(0, 5), (30, 5)]))),
    )
    rows = by_id(crossings)
    assert set(rows) == {"PICC:1", "PICC:3"}
    assert rows["PICC:1"].source_ids == "1+2"


def test_an_axis_with_no_surface_leaves_a_coverage_hole():
    _, coverage, exclusions, report = build(
        surfaces(("1", box(0, 0, 10, 10), "Tronçon", NONE)),
        axes(("a", LineString([(0, 5), (10, 5)])), ("b", LineString([(50, -50), (50, 50)]))),
    )
    assert "axis_without_unit" in set(exclusions.reason)
    assert not covers(coverage, 50, 0)
    assert report["axis_without_unit_m"] == pytest.approx(100, abs=0.01)


def test_a_footprint_leaving_the_area_is_excluded():
    _, coverage, exclusions, _ = build(
        surfaces(("1", box(90, 0, 110, 10), "Tronçon", NONE)),
        axes(("a", LineString([(90, 5), (110, 5)]))),
    )
    assert "footprint_crosses_bounds" in set(exclusions.reason)
    assert not covers(coverage, 95, 5)


def test_artifact_contract_round_trips(tmp_path):
    from gispulse.core.io.geoparquet import write_geoparquet
    from gispulse.persistence.io import read_geoparquet

    crossings, coverage, *_ = build(*_overpass())
    path = tmp_path / "road_crossings.geoparquet"
    write_geoparquet(crossings, str(path))
    frame = read_geoparquet(str(path))
    assert frame.crs.to_epsg() == 31370 and frame["axis"].crs == frame.crs
    assert set(frame.structure) <= {"ground", "grade_separated", "unknown"}
    assert frame.road_id.is_unique and frame.complex_crossing.dtype == bool
    ground = frame[frame.structure == "ground"]
    overlaps = [
        a.intersection(b).area
        for i, a in enumerate(ground.geometry)
        for b in list(ground.geometry)[i + 1 :]
    ]
    assert max(overlaps, default=0) <= 1e-6  # no ground area billed twice
    assert coverage.crs.to_epsg() == 31370


@pytest.mark.parametrize(
    "mutate, code",
    [
        (lambda s, a: (s.to_crs(3812), a), "PICC_CROSSINGS_CRS_INVALID"),
        (lambda s, a: (s.drop(columns="NIVEAU"), a), "PICC_CROSSINGS_FIELD_MISSING"),
        (lambda s, a: (s, a.drop(columns="NATUR_DESC")), "PICC_CROSSINGS_FIELD_MISSING"),
        (lambda s, a: (pd.concat([s, s]), a), "PICC_CROSSINGS_ID_INVALID"),
        (
            lambda s, a: (
                surfaces(("9", Polygon([(0, 0), (1, 1), (1, 0), (0, 1)]), "Tronçon", NONE)),
                a,
            ),
            "PICC_CROSSINGS_GEOMETRY_INVALID",
        ),
    ],
)
def test_data_defects_raise_dedicated_codes(mutate, code):
    s, a = mutate(*_overpass())
    with pytest.raises(ValueError, match=code):
        build(s, a)


def test_invalid_parameters_are_rejected():
    with pytest.raises(ValueError, match="PICC_CROSSINGS_TOLERANCE_INVALID"):
        build(*_overpass(), exclusion_buffer_m=0)
    with pytest.raises(ValueError, match="PICC_CROSSINGS_EXTENT_INVALID"):
        build(*_overpass(), coverage_area=Polygon())

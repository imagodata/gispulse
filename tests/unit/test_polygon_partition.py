import geopandas as gpd
import pytest
from shapely.geometry import LineString, MultiPolygon, box

from gispulse.capabilities.vector.polygon_partition import partition_polygons


def frames(lines, polygon=None):
    return (
        gpd.GeoDataFrame(
            {"id": ["road"]},
            geometry=[polygon if polygon is not None else box(0, 0, 10, 10)],
            crs=31370,
        ),
        gpd.GeoDataFrame({"id": [str(i) for i in range(len(lines))]}, geometry=lines, crs=31370),
    )


def run(p, b, **kwargs):
    return partition_polygons(
        p,
        b,
        polygon_id="id",
        boundary_id="id",
        coverage_bounds=(-1, -1, 11, 11),
        area_tolerance_m2=1e-6,
        length_tolerance_m=1e-6,
        **kwargs,
    )


def test_complete_partition_conserves_area_and_ids_under_reordering():
    p, b = frames([LineString([(3, 0), (3, 10)]), LineString([(7, 0), (7, 10)])])
    faces, report = run(p, b)
    assert len(faces) == 3 and faces.area.sum() == pytest.approx(100)
    assert report.status.tolist() == ["partitioned"]
    assert "functional_class" not in faces and "structure" not in faces
    other, _ = run(p, b.iloc[::-1])
    assert faces.face_id.tolist() == other.face_id.tolist()


def test_area_conservation_does_not_hide_dangling_line():
    p, b = frames([LineString([(3, 0), (3, 10)]), LineString([(7, 0), (7, 5)])])
    faces, report = run(p, b)
    assert faces.area.sum() == 100
    assert report.status.tolist() == ["unresolved_lines"]
    assert report.dangles_m.iloc[0] == 5
    assert report.unresolved_geometry.iloc[0].length == 5
    assert set(faces.topology_status) == {"unresolved_lines"}


def test_unclosed_boundary_is_never_snapped():
    p, b = frames([LineString([(3, 0.01), (3, 9.99)])])
    faces, report = run(p, b)
    assert len(faces) == 1 and report.status.iloc[0] == "unresolved_lines"


def test_corridor_extending_beyond_acquired_bbox_has_no_faces():
    p, b = frames([], box(0, 0, 20, 20))
    faces, report = run(p, b)
    assert faces.empty and report.status.iloc[0] == "outside_acquisition_coverage"


def test_disconnected_original_parts_are_not_called_subdivision():
    p, b = frames([], MultiPolygon([box(0, 0, 2, 2), box(5, 5, 7, 7)]))
    faces, report = run(p, b)
    assert len(faces) == 2 and report.status.iloc[0] == "unsplit"


def test_wrong_crs_and_duplicate_boundaries_refused():
    p, b = frames([LineString([(0, 0), (10, 10)])])
    with pytest.raises(ValueError, match="CRS_INVALID"):
        run(p, b.to_crs(4326))
    import pandas as pd

    with pytest.raises(ValueError, match="ID_INVALID"):
        run(p, pd.concat([b, b]))

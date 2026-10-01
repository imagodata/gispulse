import geopandas as gpd
import pytest
from shapely.geometry import box

from gispulse.capabilities.vector.classify_faces import validate_functional_faces


def frame(values):
    return gpd.GeoDataFrame(
        {"functional_class": values}, geometry=[box(0, 0, 1, 1)] * len(values), crs=31370
    )


def test_explicit_classes_are_retained_and_reported():
    result, report = validate_functional_faces(
        frame(["carriageway_paved", "carriageway_unpaved", "sidewalk", None])
    )
    assert result.functional_class.tolist() == [
        "carriageway_paved",
        "carriageway_unpaved",
        "sidewalk",
        "unmapped",
    ]
    assert result.ready_for_costing.tolist() == [True, True, True, False]
    assert report["classified_faces"] == 3 and report["unmapped_faces"] == 1
    assert report["inference"] is False


def test_missing_or_unknown_class_fails_closed():
    with pytest.raises(ValueError, match="FUNCTION_CLASS_MISSING"):
        validate_functional_faces(frame([]).drop(columns=["functional_class"]))
    with pytest.raises(ValueError, match="FUNCTION_CLASS_UNKNOWN"):
        validate_functional_faces(frame(["heavy_pavement"]))

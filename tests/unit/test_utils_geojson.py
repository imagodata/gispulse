"""Tests for gispulse.utils.geojson — normalize_geometry, bbox_for_geometry,
iter_positions, json_safe, first_str."""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from gispulse.utils.geojson import (
    bbox_for_geometry,
    first_str,
    iter_positions,
    json_safe,
    normalize_geometry,
)


# ---------------------------------------------------------------------------
# first_str
# ---------------------------------------------------------------------------


class TestFirstStr:
    def test_returns_first_non_empty(self) -> None:
        assert first_str({"a": "hello", "b": "world"}, "a", "b") == "hello"

    def test_skips_none(self) -> None:
        assert first_str({"a": None, "b": "found"}, "a", "b") == "found"

    def test_skips_empty_string(self) -> None:
        assert first_str({"a": "", "b": "ok"}, "a", "b") == "ok"

    def test_returns_none_when_all_missing(self) -> None:
        assert first_str({"a": None}, "a", "missing") is None

    def test_coerces_to_str(self) -> None:
        assert first_str({"a": 42}, "a") == "42"

    def test_no_keys_returns_none(self) -> None:
        assert first_str({}) is None


# ---------------------------------------------------------------------------
# iter_positions
# ---------------------------------------------------------------------------


class TestIterPositions:
    def test_point_coordinates(self) -> None:
        positions = list(iter_positions([2.3, 48.9]))
        assert positions == [(2.3, 48.9)]

    def test_linestring_coordinates(self) -> None:
        coords = [[0.0, 1.0], [2.0, 3.0]]
        positions = list(iter_positions(coords))
        assert positions == [(0.0, 1.0), (2.0, 3.0)]

    def test_polygon_coordinates(self) -> None:
        ring = [[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]
        positions = list(iter_positions([ring]))
        assert len(positions) == 5

    def test_tuple_backed_position(self) -> None:
        positions = list(iter_positions((5.0, 6.0)))
        assert positions == [(5.0, 6.0)]

    def test_empty_list_yields_nothing(self) -> None:
        assert list(iter_positions([])) == []

    def test_non_numeric_ignored(self) -> None:
        assert list(iter_positions("not a coord")) == []

    def test_returns_floats(self) -> None:
        for lon, lat in iter_positions([1, 2]):
            assert isinstance(lon, float)
            assert isinstance(lat, float)


# ---------------------------------------------------------------------------
# bbox_for_geometry
# ---------------------------------------------------------------------------


class TestBboxForGeometry:
    def test_point(self) -> None:
        geom = {"type": "Point", "coordinates": [2.3, 48.9]}
        bbox = bbox_for_geometry(geom)
        assert bbox == [2.3, 48.9, 2.3, 48.9]

    def test_polygon(self) -> None:
        ring = [[0.0, 0.0], [10.0, 0.0], [10.0, 5.0], [0.0, 5.0], [0.0, 0.0]]
        geom = {"type": "Polygon", "coordinates": [ring]}
        bbox = bbox_for_geometry(geom)
        assert bbox == [0.0, 0.0, 10.0, 5.0]

    def test_none_geometry_returns_none(self) -> None:
        assert bbox_for_geometry(None) is None

    def test_empty_coordinates_returns_none(self) -> None:
        assert bbox_for_geometry({"type": "LineString", "coordinates": []}) is None


# ---------------------------------------------------------------------------
# normalize_geometry
# ---------------------------------------------------------------------------


class TestNormalizeGeometry:
    def test_point_passthrough(self) -> None:
        geom = {"type": "Point", "coordinates": [2.3, 48.9]}
        result = normalize_geometry(geom)
        assert result is not None
        assert result["type"] == "Point"
        assert result["coordinates"] == [2.3, 48.9]

    def test_polygon_passthrough(self) -> None:
        ring = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0]]
        geom = {"type": "Polygon", "coordinates": [ring]}
        result = normalize_geometry(geom)
        assert result is not None
        assert result["type"] == "Polygon"

    def test_tuple_coordinates_converted_to_lists(self) -> None:
        geom = {"type": "Point", "coordinates": (2.3, 48.9)}
        result = normalize_geometry(geom)
        assert result is not None
        assert isinstance(result["coordinates"], list)

    def test_none_returns_none(self) -> None:
        assert normalize_geometry(None) is None

    def test_string_returns_none(self) -> None:
        assert normalize_geometry("not a geometry") is None

    def test_unknown_type_returns_none(self) -> None:
        geom = {"type": "Circle", "coordinates": [0, 0]}
        assert normalize_geometry(geom) is None

    def test_non_list_coordinates_returns_none(self) -> None:
        geom = {"type": "Point", "coordinates": "bad"}
        assert normalize_geometry(geom) is None

    def test_multipolygon(self) -> None:
        ring = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 0.0]]
        geom = {"type": "MultiPolygon", "coordinates": [[[ring]]]}
        result = normalize_geometry(geom)
        assert result is not None
        assert result["type"] == "MultiPolygon"

    def test_linestring(self) -> None:
        geom = {"type": "LineString", "coordinates": [[0.0, 0.0], [1.0, 1.0]]}
        result = normalize_geometry(geom)
        assert result is not None

    def test_empty_coordinates_returns_none(self) -> None:
        geom = {"type": "LineString", "coordinates": []}
        assert normalize_geometry(geom) is None


# ---------------------------------------------------------------------------
# json_safe
# ---------------------------------------------------------------------------


class TestJsonSafe:
    def test_none_returns_none(self) -> None:
        assert json_safe(None) is None

    def test_nan_returns_none(self) -> None:
        assert json_safe(float("nan")) is None

    def test_string_passthrough(self) -> None:
        assert json_safe("hello") == "hello"

    def test_int_passthrough(self) -> None:
        assert json_safe(42) == 42

    def test_float_passthrough(self) -> None:
        assert json_safe(3.14) == 3.14

    def test_dict_passthrough(self) -> None:
        d = {"a": 1}
        assert json_safe(d) == d

    def test_list_passthrough(self) -> None:
        assert json_safe([1, 2, 3]) == [1, 2, 3]

    def test_datetime_to_iso(self) -> None:
        dt = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        result = json_safe(dt)
        assert result == "2024-01-15T12:00:00+00:00"

    def test_date_to_iso(self) -> None:
        d = date(2024, 6, 1)
        result = json_safe(d)
        assert result == "2024-06-01"

    def test_path_to_str(self) -> None:
        result = json_safe(Path("/tmp/file.gpkg"))
        assert isinstance(result, str)

    def test_nested_dict_with_none(self) -> None:
        # json_safe is shallow — nested nesting is caller's responsibility
        result = json_safe({"key": None})
        assert result == {"key": None}

    def test_numpy_scalar_if_available(self) -> None:
        try:
            import numpy as np
            arr = np.float32(1.5)
            result = json_safe(arr)
            assert isinstance(result, float)
        except ImportError:
            pytest.skip("numpy not available")

    # P3b — circular / ValueError-raising structures
    def test_circular_dict_returns_string_not_exception(self) -> None:
        """json_safe must not raise on circular references — returns str() fallback."""
        d: dict = {}
        d["self"] = d  # circular reference
        result = json_safe(d)
        # Must be a string representation, not an exception
        assert isinstance(result, str)

    def test_circular_list_returns_string_not_exception(self) -> None:
        lst: list = []
        lst.append(lst)  # circular reference
        result = json_safe(lst)
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# P3c — GeometryCollection support
# ---------------------------------------------------------------------------


class TestGeometryCollection:
    _point = {"type": "Point", "coordinates": [2.0, 48.0]}
    _line = {"type": "LineString", "coordinates": [[0.0, 0.0], [1.0, 1.0]]}

    def test_normalize_geometry_collection(self) -> None:
        gc = {"type": "GeometryCollection", "geometries": [self._point, self._line]}
        result = normalize_geometry(gc)
        assert result is not None
        assert result["type"] == "GeometryCollection"
        assert len(result["geometries"]) == 2

    def test_normalize_geometry_collection_empty(self) -> None:
        gc = {"type": "GeometryCollection", "geometries": []}
        result = normalize_geometry(gc)
        assert result is not None
        assert result["geometries"] == []

    def test_normalize_geometry_collection_drops_invalid_members(self) -> None:
        gc = {
            "type": "GeometryCollection",
            "geometries": [
                self._point,
                {"type": "Circle", "coordinates": [0, 0]},  # invalid — dropped
                self._line,
            ],
        }
        result = normalize_geometry(gc)
        assert result is not None
        assert len(result["geometries"]) == 2

    def test_normalize_geometry_collection_no_geometries_key(self) -> None:
        gc = {"type": "GeometryCollection"}
        result = normalize_geometry(gc)
        assert result is None

    def test_normalize_geometry_collection_non_list_geometries(self) -> None:
        gc = {"type": "GeometryCollection", "geometries": "bad"}
        result = normalize_geometry(gc)
        assert result is None

    def test_bbox_for_geometry_collection(self) -> None:
        gc = {
            "type": "GeometryCollection",
            "geometries": [
                {"type": "Point", "coordinates": [1.0, 2.0]},
                {"type": "Point", "coordinates": [5.0, 6.0]},
            ],
        }
        bbox = bbox_for_geometry(gc)
        assert bbox == [1.0, 2.0, 5.0, 6.0]

    def test_bbox_for_empty_geometry_collection(self) -> None:
        gc = {"type": "GeometryCollection", "geometries": []}
        assert bbox_for_geometry(gc) is None

    def test_bbox_for_nested_geometry_collection(self) -> None:
        """Nested GeometryCollection — bbox recurses correctly."""
        inner = {
            "type": "GeometryCollection",
            "geometries": [{"type": "Point", "coordinates": [10.0, 20.0]}],
        }
        outer = {"type": "GeometryCollection", "geometries": [inner]}
        bbox = bbox_for_geometry(outer)
        assert bbox == [10.0, 20.0, 10.0, 20.0]

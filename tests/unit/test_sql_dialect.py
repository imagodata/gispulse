"""Tests for persistence.sql_dialect — cross-backend spatial SQL abstraction."""
from __future__ import annotations

import pytest

from gispulse.persistence.sql_dialect import (
    DuckDBDialect,
    PostGISDialect,
    SpatiaLiteDialect,
    get_dialect,
)


class TestGetDialect:
    def test_postgis(self):
        d = get_dialect("postgis")
        assert d.name == "postgis"
        assert isinstance(d, PostGISDialect)

    def test_duckdb(self):
        d = get_dialect("duckdb")
        assert d.name == "duckdb"
        assert isinstance(d, DuckDBDialect)

    def test_spatialite(self):
        d = get_dialect("spatialite")
        assert d.name == "spatialite"
        assert isinstance(d, SpatiaLiteDialect)

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown SQL dialect"):
            get_dialect("mysql")


class TestPostGISDialect:
    @pytest.fixture
    def d(self):
        return PostGISDialect()

    def test_st_area_uses_geography(self, d):
        assert "::geography" in d.st_area("geom")

    def test_st_distance_uses_geography(self, d):
        assert "::geography" in d.st_distance("a", "b")

    def test_st_geom_from_text_with_srid(self, d):
        result = d.st_geom_from_text("'POINT(0 0)'", 4326)
        assert "4326" in result

    def test_st_geom_from_text_without_srid(self, d):
        result = d.st_geom_from_text("'POINT(0 0)'")
        assert "4326" not in result

    def test_string_agg(self, d):
        assert "STRING_AGG" in d.string_agg("name")

    def test_st_buffer(self, d):
        result = d.st_buffer("geom", "100")
        assert "::geography" in result


class TestDuckDBDialect:
    @pytest.fixture
    def d(self):
        return DuckDBDialect()

    def test_st_area_no_geography(self, d):
        assert "::geography" not in d.st_area("geom")

    def test_st_distance_no_geography(self, d):
        assert "::geography" not in d.st_distance("a", "b")

    def test_st_geom_from_text_ignores_srid(self, d):
        result = d.st_geom_from_text("'POINT(0 0)'", 4326)
        assert "4326" not in result

    def test_string_agg(self, d):
        assert "STRING_AGG" in d.string_agg("name")

    def test_st_buffer(self, d):
        result = d.st_buffer("geom", "100")
        assert "geography" not in result


class TestSpatiaLiteDialect:
    @pytest.fixture
    def d(self):
        return SpatiaLiteDialect()

    def test_st_area_uses_area(self, d):
        assert d.st_area("geom") == "Area(geom)"

    def test_st_length_uses_glength(self, d):
        assert d.st_length("geom") == "GLength(geom)"

    def test_st_distance(self, d):
        assert d.st_distance("a", "b") == "Distance(a, b)"

    def test_st_geom_from_text_with_srid(self, d):
        result = d.st_geom_from_text("'POINT(0 0)'", 4326)
        assert result == "GeomFromText('POINT(0 0)', 4326)"

    def test_string_agg_uses_group_concat(self, d):
        assert "GROUP_CONCAT" in d.string_agg("name")

    def test_st_intersects(self, d):
        assert d.st_intersects("a", "b") == "Intersects(a, b)"

    def test_st_within(self, d):
        assert d.st_within("a", "b") == "Within(a, b)"

    def test_st_contains(self, d):
        assert d.st_contains("a", "b") == "Contains(a, b)"


class TestCrossBackendConsistency:
    """Verify all dialects implement the same interface."""

    @pytest.fixture(params=["postgis", "duckdb", "spatialite"])
    def d(self, request):
        return get_dialect(request.param)

    def test_all_methods_return_strings(self, d):
        assert isinstance(d.st_area("g"), str)
        assert isinstance(d.st_length("g"), str)
        assert isinstance(d.st_distance("a", "b"), str)
        assert isinstance(d.st_buffer("g", "10"), str)
        assert isinstance(d.st_intersects("a", "b"), str)
        assert isinstance(d.st_within("a", "b"), str)
        assert isinstance(d.st_contains("a", "b"), str)
        assert isinstance(d.st_geom_from_text("'POINT(0 0)'", 4326), str)
        assert isinstance(d.st_is_valid("g"), str)
        assert isinstance(d.st_centroid("g"), str)
        assert isinstance(d.string_agg("col"), str)
        assert isinstance(d.st_overlaps("a", "b"), str)
        assert isinstance(d.st_crosses("a", "b"), str)

    def test_name_is_string(self, d):
        assert isinstance(d.name, str)
        assert d.name in ("postgis", "duckdb", "spatialite")

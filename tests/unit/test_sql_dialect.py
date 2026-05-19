"""Tests for persistence.sql_dialect — cross-backend spatial SQL abstraction."""
from __future__ import annotations

import pytest

from gispulse.persistence.sql_dialect import (
    BufferStyle,
    DuckDBDialect,
    PostGISDialect,
    SpatiaLiteDialect,
    UnsupportedInDialect,
    get_dialect,
)
from gispulse.persistence.spatial_queries import (
    buffer_select,
    clip_select,
    intersects_select,
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


# ===========================================================================
# ELT Lot 1 (#244) — the five audited DuckDB/PostGIS divergences
# ===========================================================================


class TestDivergence1WkbVsNative:
    """Divergence #1 — registered geometry: WKB blob vs native column."""

    def test_duckdb_lifts_wkb_blob(self):
        d = DuckDBDialect()
        assert d.geom_column == "__wkb"
        assert d.geom_ref() == "ST_GeomFromWKB(__wkb)"
        assert d.geom_ref(table="i") == "ST_GeomFromWKB(i.__wkb)"

    def test_postgis_casts_native_column(self):
        d = PostGISDialect()
        assert d.geom_column == "geometry"
        assert d.geom_ref() == "geometry::geometry"
        assert d.geom_ref(table="m") == "m.geometry::geometry"

    def test_geom_ref_rejects_hostile_identifier(self):
        with pytest.raises(ValueError):
            DuckDBDialect().geom_ref("__wkb); DROP TABLE x --")
        with pytest.raises(ValueError):
            PostGISDialect().geom_ref(table='i" UNION SELECT')


class TestDivergence2StyledBuffer:
    """Divergence #2 — styled ST_Buffer: PostGIS only."""

    def test_default_style_both_dialects(self):
        assert DuckDBDialect().st_buffer_styled("g", 100.0) == "ST_Buffer(g, 100.0)"
        assert PostGISDialect().st_buffer_styled("g", 100.0) == "ST_Buffer(g, 100.0)"

    def test_negative_distance_is_erosion(self):
        assert DuckDBDialect().st_buffer_styled("g", -25.0) == "ST_Buffer(g, -25.0)"

    def test_styled_postgis_emits_style_string(self):
        style = BufferStyle(quad_segs=16, cap_style="flat", join_style="mitre")
        assert PostGISDialect().st_buffer_styled("g", 5.0, style) == (
            "ST_Buffer(g, 5.0, 'quad_segs=16 endcap=flat join=mitre "
            "mitre_limit=5.000 side=both')"
        )

    def test_styled_duckdb_raises(self):
        with pytest.raises(UnsupportedInDialect):
            DuckDBDialect().st_buffer_styled("g", 5.0, BufferStyle(quad_segs=16))

    def test_unsupported_is_a_notimplementederror(self):
        # New callers catch the precise type; legacy callers catching the
        # broad NotImplementedError keep working.
        assert issubclass(UnsupportedInDialect, NotImplementedError)

    def test_default_style_object_accepted_by_duckdb(self):
        assert DuckDBDialect().st_buffer_styled("g", 5.0, BufferStyle()) == (
            "ST_Buffer(g, 5.0)"
        )

    def test_capability_flag(self):
        assert PostGISDialect().supports_styled_buffer is True
        assert DuckDBDialect().supports_styled_buffer is False


class TestBufferStyle:
    def test_single_sided_renders_left(self):
        assert "side=left" in BufferStyle(single_sided=True).to_postgis_style()
        assert "side=both" in BufferStyle().to_postgis_style()

    def test_validates_enums(self):
        with pytest.raises(ValueError):
            BufferStyle(cap_style="pointy")
        with pytest.raises(ValueError):
            BufferStyle(join_style="weird")
        with pytest.raises(ValueError):
            BufferStyle(quad_segs=0)

    def test_is_default_flag(self):
        assert BufferStyle().is_default is True
        assert BufferStyle(quad_segs=16).is_default is False
        assert BufferStyle(single_sided=True).is_default is False


class TestDivergence3Knn:
    """Divergence #3 — KNN <-> operator: PostGIS only."""

    def test_postgis_emits_operator(self):
        assert PostGISDialect().supports_knn is True
        assert PostGISDialect().st_knn_distance("a.geom", "b.geom") == (
            "(a.geom <-> b.geom)"
        )

    def test_duckdb_raises(self):
        assert DuckDBDialect().supports_knn is False
        with pytest.raises(UnsupportedInDialect):
            DuckDBDialect().st_knn_distance("a", "b")


class TestDivergence4Transform:
    """Divergence #4 — ST_Transform arity / axis order."""

    def test_duckdb_uses_four_arg_always_xy(self):
        assert DuckDBDialect().st_transform(
            "g", src_srid=4326, dst_srid=2154
        ) == "ST_Transform(g, 'EPSG:4326', 'EPSG:2154', always_xy := true)"

    def test_postgis_uses_two_arg_target_srid(self):
        assert PostGISDialect().st_transform(
            "g", src_srid=4326, dst_srid=2154
        ) == "ST_Transform(g, 2154)"


class TestDivergence5Coverage:
    """Divergence #5 — topological coverage capability flag."""

    def test_coverage_flags(self):
        assert PostGISDialect().supports_coverage is True
        assert DuckDBDialect().supports_coverage is False


@pytest.mark.parametrize("dialect", [DuckDBDialect(), PostGISDialect()])
def test_intersection_portable_across_sql_engines(dialect):
    assert dialect.st_intersection("a", "b") == "ST_Intersection(a, b)"


# ===========================================================================
# ELT Lot 1 — statement builders (spatial_queries) — golden SQL
# ===========================================================================


class TestBufferSelect:
    def test_duckdb_golden(self):
        # DuckDB derives the geometry via `* REPLACE` so the result keeps
        # the canonical __wkb name the result decoder keys on.
        q = buffer_select(DuckDBDialect(), source_table="_input", distance=100.0)
        assert q.sql == (
            "SELECT * REPLACE (ST_Buffer(ST_GeomFromWKB(__wkb), 100.0) AS __wkb) "
            "FROM _input"
        )
        assert q.geom_column == "__wkb"

    def test_postgis_golden_with_style(self):
        q = buffer_select(
            PostGISDialect(),
            source_table="_input",
            distance=100.0,
            style=BufferStyle(),
        )
        assert q.sql == (
            "SELECT *, ST_Buffer(geometry::geometry, 100.0, "
            "'quad_segs=8 endcap=round join=round mitre_limit=5.000 side=both') "
            "AS geometry_buf FROM _input"
        )
        assert q.geom_column == "geometry_buf"


class TestClipSelect:
    def test_duckdb_uses_replace_projection(self):
        q = clip_select(
            DuckDBDialect(), source_table="_clip_input", mask_table="_clip_mask"
        )
        assert q.sql == (
            "SELECT i.* REPLACE (ST_Intersection(ST_GeomFromWKB(i.__wkb), "
            "ST_GeomFromWKB(m.__wkb)) AS __wkb) "
            "FROM _clip_input i, _clip_mask m "
            "WHERE ST_Intersects(ST_GeomFromWKB(i.__wkb), ST_GeomFromWKB(m.__wkb))"
        )
        assert q.geom_column == "__wkb"

    def test_postgis_appends_geometry_clip(self):
        q = clip_select(
            PostGISDialect(), source_table="_clip_input", mask_table="_clip_mask"
        )
        assert q.sql == (
            "SELECT i.*, ST_Intersection(i.geometry::geometry, "
            "m.geometry::geometry) AS geometry_clip "
            "FROM _clip_input i, _clip_mask m "
            "WHERE ST_Intersects(i.geometry::geometry, m.geometry::geometry)"
        )
        assert q.geom_column == "geometry_clip"


class TestIntersectsSelect:
    def test_duckdb_golden(self):
        q = intersects_select(
            DuckDBDialect(), source_table="_is_input", ref_table="_is_ref"
        )
        assert q.sql == (
            "SELECT i.* FROM _is_input i, _is_ref r "
            "WHERE ST_Intersects(ST_GeomFromWKB(i.__wkb), ST_GeomFromWKB(r.__wkb))"
        )
        assert q.geom_column == "__wkb"

    def test_postgis_golden(self):
        q = intersects_select(
            PostGISDialect(), source_table="_is_input", ref_table="_is_ref"
        )
        assert q.sql == (
            "SELECT i.* FROM _is_input i, _is_ref r "
            "WHERE ST_Intersects(i.geometry::geometry, r.geometry::geometry)"
        )
        assert q.geom_column == "geometry"


@pytest.mark.parametrize("backend", ["duckdb", "postgis"])
def test_builders_reject_hostile_table_names(backend):
    d = get_dialect(backend)
    with pytest.raises(ValueError):
        buffer_select(d, source_table="x; DROP TABLE t", distance=1.0)
    with pytest.raises(ValueError):
        clip_select(d, source_table="ok", mask_table='m"--')
    with pytest.raises(ValueError):
        intersects_select(d, source_table="ok", ref_table="r;SELECT")


# ===========================================================================
# ELT Lot 3 (#246) — single-layer geometry transform spellings
# ===========================================================================


@pytest.mark.parametrize("dialect", [DuckDBDialect(), PostGISDialect()])
def test_lot3_geometry_functions_spelled_identically(dialect):
    # DuckDB-spatial and PostGIS share the ST_* spelling for these.
    assert dialect.st_boundary("g") == "ST_Boundary(g)"
    assert dialect.st_envelope("g") == "ST_Envelope(g)"
    assert dialect.st_convex_hull("g") == "ST_ConvexHull(g)"
    assert dialect.st_make_valid("g") == "ST_MakeValid(g)"
    assert dialect.st_simplify("g", 0.5) == "ST_Simplify(g, 0.5)"
    assert dialect.st_is_empty("g") == "ST_IsEmpty(g)"


@pytest.mark.parametrize("dialect", [DuckDBDialect(), PostGISDialect()])
def test_concave_hull_uses_three_arg_form(dialect):
    # The 2-arg form is rejected by DuckDB-spatial — always emit 3 args.
    assert dialect.st_concave_hull("g", 0.5) == "ST_ConcaveHull(g, 0.5, false)"
    assert (
        dialect.st_concave_hull("g", 0.3, allow_holes=True)
        == "ST_ConcaveHull(g, 0.3, true)"
    )


def test_geometry_functions_raise_on_gpkg():
    from gispulse.persistence.sql_dialect import get_dialect

    gpkg = get_dialect("gpkg")
    for call in (
        lambda: gpkg.st_boundary("g"),
        lambda: gpkg.st_convex_hull("g"),
        lambda: gpkg.st_make_valid("g"),
        lambda: gpkg.st_simplify("g", 1.0),
    ):
        with pytest.raises(NotImplementedError):
            call()

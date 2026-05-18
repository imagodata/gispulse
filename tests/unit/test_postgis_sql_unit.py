"""Coverage tests for capabilities.postgis_sql.

Hits the validation branches and the _safe_render helper without requiring
a live PostGIS connection. The full execute() path that hits PostGIS is
covered by integration tests when a DSN is available.

Closes one of the gaps from #443 (capabilities/postgis_sql.py was at 30%).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from gispulse.capabilities.postgis_sql import PostGISSQLCapability, _safe_render


def _make_gdf():
    """Build a small GDF lazily — defers shapely/geopandas import to call site
    so the cov plugin's numpy reload (which breaks _NoValue) cannot interfere
    with tests that don't actually need a GDF (validation branches)."""
    import geopandas as gpd
    from shapely.geometry import Point

    return gpd.GeoDataFrame(
        {"id": [1, 2], "geometry": [Point(0, 0), Point(1, 1)]},
        crs="EPSG:4326",
    )


# ---------------------------------------------------------------------------
# _safe_render — pure helper, no I/O
# ---------------------------------------------------------------------------


class TestSafeRender:
    def test_string_value_validated_as_identifier(self):
        out = _safe_render("SELECT * FROM {tbl}", {"tbl": "parcels"})
        assert out == "SELECT * FROM parcels"

    def test_qualified_identifier_dot_allowed(self):
        out = _safe_render("SELECT * FROM {tbl}", {"tbl": "public.parcels"})
        assert out == "SELECT * FROM public.parcels"

    def test_int_passthrough(self):
        out = _safe_render("SELECT * FROM t WHERE id = {n}", {"n": 42})
        assert out == "SELECT * FROM t WHERE id = 42"

    def test_float_passthrough(self):
        out = _safe_render("SELECT * FROM t WHERE x > {f}", {"f": 3.14})
        assert "3.14" in out

    def test_missing_param_raises(self):
        with pytest.raises(ValueError, match="missing parameters"):
            _safe_render("SELECT * FROM {missing}", {})

    def test_extra_params_ignored(self):
        out = _safe_render("SELECT * FROM {tbl}", {"tbl": "t", "unused": "x"})
        assert out == "SELECT * FROM t"

    def test_non_scalar_value_dropped_then_missing(self):
        # list values are not whitelisted → not added to safe → reported missing
        with pytest.raises(ValueError, match="missing parameters"):
            _safe_render("SELECT {dyn}", {"dyn": ["a", "b"]})

    def test_string_with_injection_attempt_rejected(self):
        # validate_identifier blocks anything outside [A-Za-z0-9_.]
        with pytest.raises(Exception):
            _safe_render("SELECT * FROM {tbl}", {"tbl": "parcels; DROP TABLE users"})


# ---------------------------------------------------------------------------
# PostGISSQLCapability — validation branches + schema
# ---------------------------------------------------------------------------


class TestExecuteValidation:
    def test_missing_dsn_raises(self):
        # Validation branch raises before touching gdf — pass a lightweight stub
        with pytest.raises(ValueError, match="dsn"):
            PostGISSQLCapability().execute(MagicMock(), dsn="", sql="SELECT 1")

    def test_missing_sql_raises(self):
        with pytest.raises(ValueError, match="sql"):
            PostGISSQLCapability().execute(MagicMock(), dsn="postgresql://x/y", sql="")


class TestExecuteWithMockedEngine:
    def test_dsn_normalised_with_psycopg2_driver(self):
        # Use MagicMock for gdf to avoid the cov-plugin / numpy-reload
        # interaction that breaks shapely Point creation in this env.
        # gdf.to_postgis(...) becomes a no-op MagicMock call.
        gdf = MagicMock()
        captured: dict = {}

        def fake_create_engine(dsn, future=False):
            captured["dsn"] = dsn
            return MagicMock()

        with patch("sqlalchemy.create_engine", side_effect=fake_create_engine):
            with patch("geopandas.read_postgis", return_value=MagicMock(reset_index=lambda drop: MagicMock())):
                PostGISSQLCapability().execute(
                    gdf,
                    dsn="postgresql://u:p@h/db",
                    sql="SELECT * FROM {input_table}",
                )

        assert "+psycopg2" in captured["dsn"]

    def test_dsn_already_with_driver_unchanged(self):
        gdf = MagicMock()
        captured: dict = {}

        def fake_create_engine(dsn, future=False):
            captured["dsn"] = dsn
            return MagicMock()

        with patch("sqlalchemy.create_engine", side_effect=fake_create_engine):
            with patch("geopandas.read_postgis", return_value=MagicMock(reset_index=lambda drop: MagicMock())):
                PostGISSQLCapability().execute(
                    gdf,
                    dsn="postgresql+psycopg2://u:p@h/db",
                    sql="SELECT * FROM {input_table}",
                )

        # DSN should not be doubly-rewritten
        assert captured["dsn"].count("+psycopg2") == 1

    def test_input_table_passed_through_uses_qualified_name(self):
        gdf = MagicMock()
        captured: dict = {}

        def capture_read_postgis(sql, con, geom_col):
            captured["sql"] = sql
            return MagicMock(reset_index=lambda drop: MagicMock())

        with patch("sqlalchemy.create_engine", return_value=MagicMock()):
            with patch("geopandas.read_postgis", side_effect=capture_read_postgis):
                PostGISSQLCapability().execute(
                    gdf,
                    dsn="postgresql://x/y",
                    sql="SELECT * FROM {input_table}",
                    input_table="parcels",
                    input_schema="myschema",
                )

        assert "myschema.parcels" in captured["sql"]


class TestSchema:
    def test_schema_shape(self):
        schema = PostGISSQLCapability().get_schema()
        assert schema["type"] == "object"
        assert "dsn" in schema["properties"]
        assert "sql" in schema["properties"]
        assert "geom_col" in schema["properties"]
        assert schema["required"] == ["dsn", "sql"]

    def test_capability_identity(self):
        cap = PostGISSQLCapability()
        assert cap.name == "postgis_sql"
        assert "PostGIS" in cap.description

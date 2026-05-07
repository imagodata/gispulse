"""Tests for ``gispulse.runtime.engine_inference``.

Covers the URI → engine mapping table, the explicit-override conflict
detection, and the integration with :class:`GISPulseConfig.resolved_engine`.
"""

from __future__ import annotations

import pytest

from gispulse.runtime.engine_inference import (
    ALL_ENGINES,
    EngineInferenceError,
    infer_engine,
    resolve_engine,
)


class TestInferEngineFromUri:
    @pytest.mark.parametrize(
        "uri,expected",
        [
            ("/data/parcels.gpkg", "gpkg"),
            ("./parcels.GPKG", "gpkg"),  # case-insensitive
            ("file:///data/parcels.gpkg", "gpkg"),
            ("/data/parcels.sqlite", "spatialite"),
            ("/data/parcels.db", "spatialite"),
            ("postgresql://user:pwd@localhost:5432/gis", "postgis"),
            ("postgres://user:pwd@localhost:5432/gis", "postgis"),
            ("postgis://user:pwd@localhost:5432/gis", "postgis"),
            ("/data/parcels.shp", "duckdb_diff"),
            ("/data/parcels.geojson", "duckdb_diff"),
            ("/data/parcels.json", "duckdb_diff"),
            ("/data/parcels.fgb", "duckdb_diff"),
            ("/data/parcels.kml", "duckdb_diff"),
            ("/data/parcels.kmz", "duckdb_diff"),
            ("/data/parcels.tab", "duckdb_diff"),
            ("/data/parcels.csv", "duckdb_diff"),
        ],
    )
    def test_known_uris(self, uri: str, expected: str) -> None:
        assert infer_engine(uri) == expected

    @pytest.mark.parametrize(
        "uri",
        [
            "",
            "   ",
            "/data/parcels.unknown",
            "s3://bucket/parcels.gpkg",  # explicit non-local scheme bails out
            "https://example.com/parcels.gpkg",
        ],
    )
    def test_unknown_uris_return_none(self, uri: str) -> None:
        assert infer_engine(uri) is None


class TestResolveEngineOverrides:
    def test_inference_only(self) -> None:
        assert resolve_engine("/data/parcels.gpkg") == "gpkg"

    def test_explicit_match(self) -> None:
        assert resolve_engine("/data/parcels.gpkg", "gpkg") == "gpkg"

    def test_unknown_uri_no_override_raises(self) -> None:
        with pytest.raises(EngineInferenceError) as exc:
            resolve_engine("/data/parcels.weirdformat")
        assert "cannot infer engine" in str(exc.value)

    def test_unknown_uri_with_override_trusts_user(self) -> None:
        assert resolve_engine("/data/x.weirdformat", "duckdb_diff") == "duckdb_diff"

    def test_unknown_override_value_raises(self) -> None:
        with pytest.raises(EngineInferenceError) as exc:
            resolve_engine("/data/parcels.gpkg", "spark")
        assert "unknown engine" in str(exc.value)

    @pytest.mark.parametrize(
        "uri,override",
        [
            ("/data/parcels.gpkg", "duckdb_diff"),  # opt-in CDC mode
            ("/data/parcels.sqlite", "duckdb_diff"),
            ("/data/parcels.gpkg", "spatialite"),  # SQLite siblings swap
            ("/data/parcels.sqlite", "gpkg"),
        ],
    )
    def test_compatible_override_allowed(self, uri: str, override: str) -> None:
        assert resolve_engine(uri, override) == override

    @pytest.mark.parametrize(
        "uri,override",
        [
            ("/data/parcels.gpkg", "postgis"),  # file → server impossible
            ("postgresql://localhost/gis", "gpkg"),  # server → file impossible
            ("/data/parcels.geojson", "gpkg"),  # file format mismatch
        ],
    )
    def test_incompatible_override_raises(self, uri: str, override: str) -> None:
        with pytest.raises(EngineInferenceError) as exc:
            resolve_engine(uri, override)
        assert "incompatible" in str(exc.value)

    def test_all_engines_constant_matches_literal(self) -> None:
        assert set(ALL_ENGINES) == {"gpkg", "spatialite", "postgis", "duckdb_diff"}


class TestGISPulseConfigResolvedEngine:
    def test_resolved_engine_inferred(self) -> None:
        from gispulse.runtime.config_loader import GISPulseConfig

        cfg = GISPulseConfig(version=1, gpkg="/tmp/foo.gpkg")
        assert cfg.resolved_engine() == "gpkg"

    def test_resolved_engine_with_override(self) -> None:
        from gispulse.runtime.config_loader import GISPulseConfig

        cfg = GISPulseConfig(version=1, gpkg="/tmp/foo.gpkg", engine="duckdb_diff")
        assert cfg.resolved_engine() == "duckdb_diff"

    def test_resolved_engine_conflict(self) -> None:
        from gispulse.runtime.config_loader import GISPulseConfig

        cfg = GISPulseConfig(version=1, gpkg="/tmp/foo.gpkg", engine="postgis")
        with pytest.raises(EngineInferenceError):
            cfg.resolved_engine()

    def test_postgresql_uri_inferred(self) -> None:
        from gispulse.runtime.config_loader import GISPulseConfig

        cfg = GISPulseConfig(version=1, gpkg="postgresql://localhost/gis")
        assert cfg.resolved_engine() == "postgis"

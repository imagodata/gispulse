"""Tests for core.crs — metric-vs-angular CRS helpers."""

from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import LineString, Point

from gispulse.core.crs import (
    LAMBERT_93,
    WEB_MERCATOR,
    is_angular,
    suggest_metric_crs,
    to_metric,
)


class TestIsAngular:
    def test_epsg_4326_is_angular(self):
        gdf = gpd.GeoDataFrame(geometry=[Point(0, 0)], crs="EPSG:4326")
        assert is_angular(gdf) is True

    def test_epsg_2154_is_projected(self):
        gdf = gpd.GeoDataFrame(geometry=[Point(652000, 6860000)], crs="EPSG:2154")
        assert is_angular(gdf) is False

    def test_epsg_3857_is_projected(self):
        gdf = gpd.GeoDataFrame(geometry=[Point(0, 0)], crs="EPSG:3857")
        assert is_angular(gdf) is False

    def test_missing_crs_is_not_angular(self):
        gdf = gpd.GeoDataFrame(geometry=[Point(0, 0)], crs=None)
        assert is_angular(gdf) is False

    def test_none_gdf_is_not_angular(self):
        assert is_angular(None) is False


class TestSuggestMetricCrs:
    def test_france_bounds_returns_lambert93(self):
        # Clermont-Ferrand area
        gdf = gpd.GeoDataFrame(
            geometry=[Point(3.085, 45.778), Point(3.100, 45.788)],
            crs="EPSG:4326",
        )
        assert suggest_metric_crs(gdf) == LAMBERT_93

    def test_paris_returns_lambert93(self):
        gdf = gpd.GeoDataFrame(
            geometry=[Point(2.35, 48.85)], crs="EPSG:4326"
        )
        assert suggest_metric_crs(gdf) == LAMBERT_93

    def test_new_york_returns_utm_zone_18n(self):
        # NYC is in UTM zone 18 North
        gdf = gpd.GeoDataFrame(
            geometry=[Point(-74.006, 40.7128)], crs="EPSG:4326"
        )
        # UTM 18N = EPSG:32618
        assert suggest_metric_crs(gdf) == "EPSG:32618"

    def test_sydney_returns_utm_zone_56s(self):
        gdf = gpd.GeoDataFrame(
            geometry=[Point(151.21, -33.87)], crs="EPSG:4326"
        )
        # UTM 56S = EPSG:32756
        assert suggest_metric_crs(gdf) == "EPSG:32756"

    def test_empty_returns_web_mercator_fallback(self):
        gdf = gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
        assert suggest_metric_crs(gdf) == WEB_MERCATOR

    def test_already_projected_input_still_works(self):
        # Lambert-93 coords for Clermont-Ferrand — projected input
        gdf = gpd.GeoDataFrame(
            geometry=[Point(712000, 6520000)], crs="EPSG:2154"
        )
        # Bounds reprojected to 4326 fall inside France → Lambert-93
        assert suggest_metric_crs(gdf) == LAMBERT_93


class TestToMetric:
    def test_reprojects_angular_input(self):
        gdf = gpd.GeoDataFrame(
            geometry=[Point(3.085, 45.778)], crs="EPSG:4326"
        )
        result, original = to_metric(gdf, crs_meters=LAMBERT_93)
        assert str(result.crs).endswith("2154")
        assert str(original).endswith("4326")
        # Clermont-Ferrand should land roughly at Lambert-93 (700k, 6.5M)
        assert 680_000 < result.geometry.iloc[0].x < 720_000

    def test_passes_through_projected_input(self):
        gdf = gpd.GeoDataFrame(
            geometry=[Point(712000, 6520000)], crs="EPSG:2154"
        )
        result, original = to_metric(gdf, crs_meters=WEB_MERCATOR)
        assert result is gdf  # no reprojection
        assert str(original).endswith("2154")

    def test_empty_gdf_returned_as_is(self):
        gdf = gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
        result, original = to_metric(gdf)
        assert result is gdf


class TestIsochroneBatchModeMetric:
    """End-to-end check: batch isochrone on 4326 data uses EPSG:2154 and
    returns a meter-scale result, not a degrees-scale artefact."""

    @pytest.fixture(autouse=True)
    def _tier_pro(self, monkeypatch):
        monkeypatch.setenv("GISPULSE_TIER", "pro")
        monkeypatch.setenv("GISPULSE_LICENCE_SKIP_VERIFY", "1")
        monkeypatch.setenv(
            "GISPULSE_LICENSE_KEY",
            "eyJvcmciOiAidGVzdCIsICJ0aWVyIjogInBybyIsICJleHAiOiAiMjAzMC0wMS0wMVQwMDowMDowMFoifQ.AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        )

    def _clermont_network_4326(self) -> gpd.GeoDataFrame:
        # 4 tiny segments around Clermont-Ferrand, in 4326
        lines = [
            LineString([(3.085, 45.778), (3.090, 45.778)]),
            LineString([(3.090, 45.778), (3.095, 45.778)]),
            LineString([(3.085, 45.778), (3.085, 45.782)]),
            LineString([(3.085, 45.782), (3.090, 45.782)]),
        ]
        return gpd.GeoDataFrame(geometry=lines, crs="EPSG:4326")

    def _sources_4326(self) -> gpd.GeoDataFrame:
        return gpd.GeoDataFrame(
            geometry=[Point(3.085, 45.778)], crs="EPSG:4326"
        )

    def test_batch_mode_metric_budget(self):
        from gispulse.capabilities.network import IsochroneCapability

        network = self._clermont_network_4326()
        sources = self._sources_4326()

        cap = IsochroneCapability()
        # 500 m budget — in EPSG:2154, only the segments touching the start
        # node should be reachable (each segment ~400-500 m). A naive
        # non-reprojected call would treat 500 as degrees (~55 km) and
        # reach everything.
        result = cap.execute(
            sources,
            ref_gdf=network,
            cost_budget=500,
            crs_meters=LAMBERT_93,
            edge_buffer_m=20,
            dissolve=True,
        )
        assert len(result) == 1
        assert "cost_budget" in result.columns
        # Result must be back in the source CRS
        assert str(result.crs).endswith("4326")
        # Polygon area must be non-zero and bounded (not full-scene)
        area_m2 = result.to_crs(LAMBERT_93).geometry.area.iloc[0]
        assert 0 < area_m2 < 5_000_000  # <5 km²


class TestAutoInjectCrsMeters:
    """Unit tests for the executor's auto-injection helper."""

    def test_injects_when_angular_and_schema_supports_it(self):
        from gispulse.orchestration.pipeline_executor import _auto_inject_crs_meters

        class FakeCap:
            name = "buffer"

            def get_schema(self):
                return {"properties": {"crs_meters": {"type": "string"}}}

        params: dict = {}
        gdf = gpd.GeoDataFrame(
            geometry=[Point(3.085, 45.778)], crs="EPSG:4326"
        )
        _auto_inject_crs_meters(FakeCap(), "s1", params, gdf)
        assert params["crs_meters"] == LAMBERT_93

    def test_respects_user_value(self):
        from gispulse.orchestration.pipeline_executor import _auto_inject_crs_meters

        class FakeCap:
            name = "buffer"

            def get_schema(self):
                return {"properties": {"crs_meters": {"type": "string"}}}

        params = {"crs_meters": "EPSG:3857"}
        gdf = gpd.GeoDataFrame(
            geometry=[Point(3.085, 45.778)], crs="EPSG:4326"
        )
        _auto_inject_crs_meters(FakeCap(), "s1", params, gdf)
        assert params["crs_meters"] == "EPSG:3857"

    def test_skips_when_projected(self):
        from gispulse.orchestration.pipeline_executor import _auto_inject_crs_meters

        class FakeCap:
            name = "buffer"

            def get_schema(self):
                return {"properties": {"crs_meters": {"type": "string"}}}

        params: dict = {}
        gdf = gpd.GeoDataFrame(
            geometry=[Point(712000, 6520000)], crs="EPSG:2154"
        )
        _auto_inject_crs_meters(FakeCap(), "s1", params, gdf)
        assert "crs_meters" not in params

    def test_skips_when_schema_does_not_declare_param(self):
        from gispulse.orchestration.pipeline_executor import _auto_inject_crs_meters

        class FakeCap:
            name = "dissolve"

            def get_schema(self):
                return {"properties": {"by": {"type": "string"}}}

        params: dict = {}
        gdf = gpd.GeoDataFrame(
            geometry=[Point(3.085, 45.778)], crs="EPSG:4326"
        )
        _auto_inject_crs_meters(FakeCap(), "s1", params, gdf)
        assert "crs_meters" not in params

"""Tests for core/routing_providers.py + capabilities/routing.py (route_pairs)."""

from __future__ import annotations

import json

import geopandas as gpd
import pytest
from shapely.geometry import LineString, Point

from gispulse.capabilities.routing import RoutePairsCapability
from gispulse.core.routing_providers import (
    CachedRoutingProvider,
    OSRMProvider,
    RoutingError,
    TortuosityBand,
    TortuosityProvider,
    route_key,
    tortuosity_factor,
)


@pytest.fixture(autouse=True)
def pro_tier(monkeypatch):
    """route_pairs requires Pro tier — activate it for every test."""
    monkeypatch.setenv("GISPULSE_TIER", "pro")
    monkeypatch.setenv("GISPULSE_LICENCE_SKIP_VERIFY", "true")
    monkeypatch.setenv("GISPULSE_LICENSE_KEY", "eyJvcmciOiAidGVzdCIsICJ0aWVyIjogInBybyIsICJleHAiOiAiMjAzMC0wMS0wMVQwMDowMDowMFoifQ.AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")


_CRS = "EPSG:3857"  # projected: no reprojection, coordinates stay exact


def _points(coords) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(geometry=[Point(*c) for c in coords], crs=_CRS)


# --------------------------------------------------------------------------- #
# Tortuosity math                                                              #
# --------------------------------------------------------------------------- #
class TestTortuosity:
    def test_bands_evaluated_in_order(self):
        bands = (
            TortuosityBand(max_straight_m=100.0, factor=1.5),
            TortuosityBand(max_straight_m=None, factor=1.2),
        )
        assert tortuosity_factor(50.0, bands) == 1.5
        assert tortuosity_factor(100.0, bands) == 1.5  # upper bound inclusive
        assert tortuosity_factor(101.0, bands) == 1.2

    def test_negative_distance_raises(self):
        with pytest.raises(ValueError, match="negative"):
            tortuosity_factor(-1.0, (TortuosityBand(None, 1.0),))

    def test_uncovered_distance_raises(self):
        with pytest.raises(ValueError, match="no tortuosity band"):
            tortuosity_factor(500.0, (TortuosityBand(max_straight_m=100.0, factor=1.5),))

    def test_provider_route_is_straight_segment(self):
        provider = TortuosityProvider(bands=(TortuosityBand(None, 1.4),))
        route = provider.route((0.0, 0.0), (3.0, 4.0))
        assert route.distance_m == pytest.approx(7.0)  # 5 × 1.4
        assert route.geometry == ((0.0, 0.0), (3.0, 4.0))


# --------------------------------------------------------------------------- #
# route_pairs capability (tortuosity + contract)                               #
# --------------------------------------------------------------------------- #
class TestRoutePairs:
    def test_tortuosity_default_is_straight_line(self):
        origins = _points([(0, 0), (10, 0)])
        dests = _points([(3, 4), (10, 5)])
        result = RoutePairsCapability().execute(origins, ref_gdf=dests)
        assert list(result["pair_id"]) == [0, 1]
        assert list(result["distance_m"]) == pytest.approx([5.0, 5.0])
        assert list(result["straight_m"]) == pytest.approx([5.0, 5.0])
        assert set(result["routing_source"]) == {"tortuosity"}

    def test_tortuosity_bands_applied(self):
        origins = _points([(0, 0), (0, 0)])
        dests = _points([(3, 4), (300, 400)])  # straight 5 m and 500 m
        result = RoutePairsCapability().execute(
            origins,
            ref_gdf=dests,
            tortuosity_bands=[
                {"max_straight_m": 100, "factor": 1.5},
                {"max_straight_m": None, "factor": 1.2},
            ],
        )
        assert list(result["distance_m"]) == pytest.approx([7.5, 600.0])

    def test_length_mismatch_raises(self):
        with pytest.raises(ValueError, match="same length"):
            RoutePairsCapability().execute(
                _points([(0, 0)]), ref_gdf=_points([(1, 1), (2, 2)])
            )

    def test_missing_ref_layer_raises(self):
        with pytest.raises(ValueError, match="destination layer"):
            RoutePairsCapability().execute(_points([(0, 0)]))

    def test_unknown_provider_raises(self):
        with pytest.raises(ValueError, match="unknown provider"):
            RoutePairsCapability().execute(
                _points([(0, 0)]), ref_gdf=_points([(1, 1)]), provider="teleport"
            )

    def test_invalid_bands_raise(self):
        with pytest.raises(ValueError, match="factor"):
            RoutePairsCapability().execute(
                _points([(0, 0)]),
                ref_gdf=_points([(1, 1)]),
                tortuosity_bands=[{"max_straight_m": None}],
            )

    def test_osrm_requires_endpoint(self):
        with pytest.raises(ValueError, match="osrm_endpoint"):
            RoutePairsCapability().execute(
                _points([(0, 0)]), ref_gdf=_points([(1, 1)]), provider="osrm"
            )

    def test_empty_input(self):
        empty = gpd.GeoDataFrame(geometry=[], crs=_CRS)
        result = RoutePairsCapability().execute(empty, ref_gdf=empty)
        assert result.empty
        assert list(result.columns) == [
            "pair_id",
            "distance_m",
            "straight_m",
            "routing_source",
            "geometry",
        ]


# --------------------------------------------------------------------------- #
# OSRM provider over a mocked httpx transport                                  #
# --------------------------------------------------------------------------- #
def _mock_osrm(provider: OSRMProvider, handler) -> None:
    """Install an httpx.MockTransport client on the provider's thread slot."""
    import httpx

    provider._thread_local.client = httpx.Client(
        transport=httpx.MockTransport(handler), timeout=provider.timeout_s
    )


def _osrm_route_payload(distance: float, coords) -> dict:
    return {
        "code": "Ok",
        "routes": [{"distance": distance, "geometry": {"coordinates": coords}}],
    }


class TestOSRMProvider:
    def _provider(self, **kwargs) -> OSRMProvider:
        return OSRMProvider(endpoint="http://osrm.test", crs=_CRS, **kwargs)

    def test_route_parses_distance_and_geometry(self):
        import httpx

        a, b = (0.0, 0.0), (1000.0, 0.0)

        def handler(request: httpx.Request) -> httpx.Response:
            assert "/route/v1/driving/" in request.url.path
            # trace: straight WGS84 segment between the two query points
            coords = [[0.0, 0.0], [0.0089831529, 0.0]]
            return httpx.Response(200, text=json.dumps(_osrm_route_payload(1234.5, coords)))

        provider = self._provider()
        _mock_osrm(provider, handler)
        route = provider.route(a, b)
        assert route.distance_m == pytest.approx(1234.5)
        assert len(route.geometry) == 2
        # geometry reprojected back to the working CRS ≈ original points
        assert route.geometry[0][0] == pytest.approx(0.0, abs=1.0)
        assert route.geometry[1][0] == pytest.approx(1000.0, abs=1.0)

    def test_no_route_raises_machine_readable(self):
        import httpx

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=json.dumps({"code": "NoRoute", "routes": []}))

        provider = self._provider()
        _mock_osrm(provider, handler)
        with pytest.raises(RoutingError) as exc:
            provider.route((0.0, 0.0), (1000.0, 0.0))
        assert exc.value.code == "OSRM_NO_ROUTE"

    def test_degenerate_drop_floored_to_tortuosity(self):
        import httpx

        a, b = (0.0, 0.0), (30.0, 40.0)  # straight 50 m, distinct points

        def handler(request: httpx.Request) -> httpx.Response:
            # OSRM snapped both endpoints on the same node: distance 0, no trace
            return httpx.Response(200, text=json.dumps(_osrm_route_payload(0.0, [])))

        provider = self._provider(floor_bands=(TortuosityBand(None, 1.3),))
        _mock_osrm(provider, handler)
        route = provider.route(a, b)
        assert route.distance_m == pytest.approx(65.0)  # 50 × 1.3
        assert route.geometry == (a, b)  # straight-segment trace fallback
        snap = provider.telemetry.snapshot(suspect_floor_rate=0.5)
        assert snap.routed_count == 1
        assert snap.floored_drop_count == 1
        assert snap.floor_rate_suspect is True

    def test_degenerate_drop_without_bands_raises(self):
        import httpx

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=json.dumps(_osrm_route_payload(0.0, [])))

        provider = self._provider()  # no floor_bands
        _mock_osrm(provider, handler)
        with pytest.raises(RoutingError) as exc:
            provider.route((0.0, 0.0), (30.0, 40.0))
        assert exc.value.code == "OSRM_DEGENERATE_DROP"

    def test_unreachable_raises(self):
        import httpx

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        provider = self._provider()
        _mock_osrm(provider, handler)
        with pytest.raises(RoutingError) as exc:
            provider.route((0.0, 0.0), (1000.0, 0.0))
        assert exc.value.code == "OSRM_UNREACHABLE"

    def test_table_matrix_none_passthrough_and_flooring(self):
        import httpx

        source = (0.0, 0.0)
        dests = [(30.0, 40.0), (1000.0, 0.0), (2000.0, 0.0)]

        def handler(request: httpx.Request) -> httpx.Response:
            assert "/table/v1/driving/" in request.url.path
            # cell 1: degenerate 0 (floored) ; cell 2: unroutable ; cell 3: real
            return httpx.Response(
                200, text=json.dumps({"code": "Ok", "distances": [[0.0, None, 2500.0]]})
            )

        provider = self._provider(floor_bands=(TortuosityBand(None, 1.3),))
        _mock_osrm(provider, handler)
        matrix = provider.distance_matrix([source], dests)
        assert matrix[0][0] == pytest.approx(65.0)  # floored
        assert matrix[0][1] is None  # caller decides the fallback
        assert matrix[0][2] == pytest.approx(2500.0)


# --------------------------------------------------------------------------- #
# Pre-routed cache provider                                                    #
# --------------------------------------------------------------------------- #
def _write_cache(tmp_path, rows, crs=_CRS):
    pytest.importorskip("pyarrow", reason="pyarrow not installed")
    data: dict = {
        "route_key": [],
        "a_x_m": [],
        "a_y_m": [],
        "b_x_m": [],
        "b_y_m": [],
        "distance_m": [],
        "geometry": [],
    }
    has_source = any("routing_source" in r for r in rows)
    if has_source:
        data["routing_source"] = []
    for r in rows:
        a, b = r["a"], r["b"]
        data["route_key"].append(route_key(a, b, crs=crs))
        data["a_x_m"].append(a[0])
        data["a_y_m"].append(a[1])
        data["b_x_m"].append(b[0])
        data["b_y_m"].append(b[1])
        data["distance_m"].append(r["distance_m"])
        data["geometry"].append(r.get("geometry") or LineString([a, b]))
        if has_source:
            data["routing_source"].append(r.get("routing_source", "osrm"))
    gdf = gpd.GeoDataFrame(data, geometry="geometry", crs=crs)
    path = tmp_path / "preroute.parquet"
    gdf.to_parquet(path)
    return path


class TestCachedRoutingProvider:
    def test_hit_and_reverse_direction(self, tmp_path):
        a, b = (0.0, 0.0), (100.0, 0.0)
        trace = LineString([a, (50.0, 10.0), b])
        path = _write_cache(tmp_path, [{"a": a, "b": b, "distance_m": 120.0, "geometry": trace}])
        provider = CachedRoutingProvider(path, crs=_CRS)
        route_ab = provider.route(a, b)
        assert route_ab.distance_m == pytest.approx(120.0)
        assert route_ab.geometry[0] == a
        # Swapped direction: same distance, reversed trace (canonical key).
        route_ba = provider.route(b, a)
        assert route_ba.distance_m == pytest.approx(120.0)
        assert route_ba.geometry[0] == b

    def test_miss_raises_cache_miss(self, tmp_path):
        a, b = (0.0, 0.0), (100.0, 0.0)
        path = _write_cache(tmp_path, [{"a": a, "b": b, "distance_m": 120.0}])
        provider = CachedRoutingProvider(path, crs=_CRS)
        with pytest.raises(RoutingError) as exc:
            provider.distance((0.0, 0.0), (999.0, 999.0))
        assert exc.value.code == "ROUTE_CACHE_MISS"

    def test_same_endpoint_is_zero(self, tmp_path):
        a, b = (0.0, 0.0), (100.0, 0.0)
        path = _write_cache(tmp_path, [{"a": a, "b": b, "distance_m": 120.0}])
        provider = CachedRoutingProvider(path, crs=_CRS)
        assert provider.distance(a, a) == 0.0

    def test_tortuosity_fallback_provenance(self, tmp_path):
        a, b = (0.0, 0.0), (100.0, 0.0)
        c, d = (0.0, 0.0), (0.0, 200.0)
        path = _write_cache(
            tmp_path,
            [
                {"a": a, "b": b, "distance_m": 120.0, "routing_source": "osrm"},
                {"a": c, "b": d, "distance_m": 260.0, "routing_source": "tortuosity_fallback"},
            ],
        )
        provider = CachedRoutingProvider(path, crs=_CRS)
        assert provider.is_tortuosity_fallback(a, b) is False
        assert provider.is_tortuosity_fallback(c, d) is True
        assert provider.tortuosity_fallback_count == 1

    def test_degenerate_geometry_repaired(self, tmp_path):
        a, b = (0.0, 0.0), (100.0, 0.0)
        degenerate = LineString([a, a])  # zero-length stored trace
        path = _write_cache(
            tmp_path, [{"a": a, "b": b, "distance_m": 120.0, "geometry": degenerate}]
        )
        provider = CachedRoutingProvider(path, crs=_CRS)
        assert provider.degenerate_geometry_repaired_count == 1
        route = provider.route(a, b)
        assert route.geometry == (a, b)  # straight repair between endpoints
        assert route.distance_m == pytest.approx(120.0)  # routed distance kept

    def test_crs_mismatch_raises(self, tmp_path):
        a, b = (0.0, 0.0), (100.0, 0.0)
        path = _write_cache(tmp_path, [{"a": a, "b": b, "distance_m": 120.0}])
        with pytest.raises(ValueError, match="CRS mismatch"):
            CachedRoutingProvider(path, crs="EPSG:2154")

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(RoutingError) as exc:
            CachedRoutingProvider(tmp_path / "absent.parquet", crs=_CRS)
        assert exc.value.code == "PREROUTE_CACHE_MISSING"

    def test_route_pairs_cached_with_skip(self, tmp_path):
        """Capability + cache: served pair routed, missing pair skipped."""
        a, b = (0.0, 0.0), (100.0, 0.0)
        path = _write_cache(
            tmp_path,
            [{"a": a, "b": b, "distance_m": 120.0, "routing_source": "tortuosity_fallback"}],
        )
        origins = _points([a, (500.0, 500.0)])
        dests = _points([b, (900.0, 900.0)])
        result = RoutePairsCapability().execute(
            origins,
            ref_gdf=dests,
            provider="cached",
            cache_path=str(path),
            on_no_route="skip",
        )
        assert list(result["pair_id"]) == [0]
        assert result["routing_source"].iloc[0] == "tortuosity_fallback"
        # Same input with on_no_route="raise" fails loudly on the miss.
        with pytest.raises(RoutingError):
            RoutePairsCapability().execute(
                origins,
                ref_gdf=dests,
                provider="cached",
                cache_path=str(path),
                on_no_route="raise",
            )

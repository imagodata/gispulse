"""Tests for gispulse.utils.wfs — fetch_wfs_features."""

from __future__ import annotations

import json

import pytest

from gispulse.utils.http import DataClientError
from gispulse.utils.wfs import GEOPLATEFORME_WFS_URL, fetch_wfs_features


# ---------------------------------------------------------------------------
# Duck-typed client stub (identical pattern to test_utils_http)
# ---------------------------------------------------------------------------


class _Response:
    def __init__(
        self,
        status_code: int = 200,
        body: bytes = b"{}",
        content_type: str = "application/json",
    ) -> None:
        self.status_code = status_code
        self._body = body
        self.headers = {"content-type": content_type}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")

    def json(self) -> object:
        return json.loads(self._body)


class _Client:
    def __init__(self, response: _Response) -> None:
        self._response = response
        self.calls: list[dict] = []

    def get(self, url: str, *, params: object = None, timeout: float = 0.0) -> _Response:
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        return self._response


def _fc(features: list) -> bytes:
    return json.dumps({"type": "FeatureCollection", "features": features}).encode()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_returns_feature_collection_structure() -> None:
    features = [{"type": "Feature", "properties": {}, "geometry": None}]
    client = _Client(_Response(body=_fc(features)))
    result = fetch_wfs_features("ns:layer", (2.0, 48.0, 2.5, 48.5), client=client)
    assert result["type"] == "FeatureCollection"
    assert len(result["features"]) == 1


def test_query_params_forwarded() -> None:
    client = _Client(_Response(body=_fc([])))
    fetch_wfs_features(
        "ns:layer",
        (1.0, 2.0, 3.0, 4.0),
        client=client,
        max_features=50,
        sort_by="id",
        timeout=10.0,
    )
    assert len(client.calls) == 1
    params = client.calls[0]["params"]
    assert params["typeNames"] == "ns:layer"
    assert params["count"] == 50
    assert params["sortBy"] == "id"
    assert "1.0,2.0,3.0,4.0" in params["bbox"]
    assert client.calls[0]["timeout"] == 10.0


def test_sort_by_none_omits_param() -> None:
    client = _Client(_Response(body=_fc([])))
    fetch_wfs_features("ns:layer", (0, 0, 1, 1), client=client, sort_by=None)
    params = client.calls[0]["params"]
    assert "sortBy" not in params


def test_uses_geoplateforme_url_by_default() -> None:
    """Without an explicit wfs_url, the call targets GEOPLATEFORME_WFS_URL."""
    client = _Client(_Response(body=_fc([])))
    fetch_wfs_features("ns:layer", (0, 0, 1, 1), client=client)
    assert client.calls[0]["url"] == GEOPLATEFORME_WFS_URL


def test_custom_wfs_url() -> None:
    client = _Client(_Response(body=_fc([])))
    fetch_wfs_features(
        "ns:layer",
        (0, 0, 1, 1),
        client=client,
        wfs_url="https://my.wfs/ows",
    )
    assert client.calls[0]["url"] == "https://my.wfs/ows"


def test_bbox_order_lon_lat() -> None:
    """BBOX is forwarded as minlon,minlat,maxlon,maxlat (Géoplateforme convention)."""
    client = _Client(_Response(body=_fc([])))
    fetch_wfs_features("l", (2.1, 48.5, 2.6, 49.0), client=client)
    bbox_param = client.calls[0]["params"]["bbox"]
    assert bbox_param.startswith("2.1,48.5,2.6,49.0")


def test_wfs_version_is_2_0_0() -> None:
    client = _Client(_Response(body=_fc([])))
    fetch_wfs_features("l", (0, 0, 1, 1), client=client)
    assert client.calls[0]["params"]["version"] == "2.0.0"


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


def test_missing_features_key_raises_data_client_error() -> None:
    bad_body = json.dumps({"type": "FeatureCollection"}).encode()
    client = _Client(_Response(body=bad_body))
    with pytest.raises(DataClientError, match="features"):
        fetch_wfs_features("ns:layer", (0, 0, 1, 1), client=client)


def test_non_list_features_raises_data_client_error() -> None:
    bad_body = json.dumps({"type": "FeatureCollection", "features": "oops"}).encode()
    client = _Client(_Response(body=bad_body))
    with pytest.raises(DataClientError):
        fetch_wfs_features("ns:layer", (0, 0, 1, 1), client=client)


def test_constant_url_is_geopf() -> None:
    assert "geopf.fr" in GEOPLATEFORME_WFS_URL


# ---------------------------------------------------------------------------
# P3a — owned client lifecycle (closed when created internally)
# ---------------------------------------------------------------------------


def test_provided_client_not_closed() -> None:
    """When the caller supplies a client, fetch_wfs_features must not close it."""

    class _TrackingClient(_Client):
        def __init__(self, response: _Response) -> None:
            super().__init__(response)
            self.closed = False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.closed = True

        def close(self) -> None:
            self.closed = True

    client = _TrackingClient(_Response(body=_fc([])))
    fetch_wfs_features("l", (0, 0, 1, 1), client=client)
    assert not client.closed, "Caller-supplied client must not be closed by fetch_wfs_features"


def test_owned_client_closed_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """When no client is provided, the internally created httpx.Client must be
    used as a context manager (i.e. __enter__/__exit__ called)."""
    import httpx

    closed_flag: list[bool] = [False]

    class _SpyClient:
        """Minimal duck-typed client that tracks context-manager exit."""

        def __init__(self) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            closed_flag[0] = True

        def get(self, url: str, *, params=None, timeout: float = 0.0):
            return _Response(body=_fc([]))

    monkeypatch.setattr(httpx, "Client", _SpyClient)

    fetch_wfs_features("l", (0, 0, 1, 1))

    assert closed_flag[0], "Internally created client must be closed (context-manager __exit__) after fetch"

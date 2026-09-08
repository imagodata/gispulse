from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from gispulse.adapters.ogc.counted_wfs import _get_wfs_hits
from gispulse.adapters.ogc.wfs_fetcher import WfsFetcher
from gispulse.core.plugin_model import AccessSpec, AccessProtocol


def access():
    return AccessSpec(
        protocol=AccessProtocol.WFS,
        endpoint="https://example.test/wfs",
        params={
            "typename": "X:roads",
            "crs": "EPSG:31370",
            "native_crs": "EPSG:31370",
            "require_extent": True,
            "bbox_filter": "intersects",
            "geometry_field": "SHAPE",
            "count_format": "wfs_hits_xml",
            "sortBy": "OIDN A",
            "pagination": {
                "mode": "offset",
                "offset_param": "startIndex",
                "limit_param": "count",
                "page_size": 2,
                "max_pages": 10,
                "max_features": 100,
                "id_field": "OIDN",
                "count_key": "count",
                "count_query": {"resultType": "hits"},
            },
        },
    )


def test_hits_and_geometry_pages_use_identical_native_filter(monkeypatch):
    calls = []

    def get(url, **kwargs):
        q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        calls.append(q)
        assert "bbox" not in q and "geometry_field" not in q and "count_format" not in q
        assert (
            q["CQL_FILTER"]
            == "INTERSECTS(SHAPE,POLYGON((0.0 0.0,10.0 0.0,10.0 10.0,0.0 10.0,0.0 0.0)))"
        )
        req = httpx.Request("GET", url)
        if q.get("resultType") == "hits":
            return httpx.Response(
                200,
                request=req,
                content=b'<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0" numberMatched="3"/>',
            )
        ids = [1, 2] if q["startIndex"] == "0" else [3]
        return httpx.Response(
            200,
            request=req,
            json={
                "type": "FeatureCollection",
                "crs": {"properties": {"name": "EPSG:31370"}},
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"OIDN": i},
                        "geometry": {"type": "Point", "coordinates": [i, i]},
                    }
                    for i in ids
                ],
            },
        )

    monkeypatch.setattr(httpx, "get", get)
    result = WfsFetcher().fetch(access(), extent=(0, 0, 10, 10))
    assert result.data.OIDN.tolist() == [1, 2, 3]
    assert result.metadata["complete"] and len(calls) == 4


@pytest.mark.parametrize(
    "body",
    [
        b"<error/>",
        b"bad xml",
        b'<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0" numberMatched="unknown"/>',
    ],
)
def test_hits_errors_never_become_zero(monkeypatch, body):
    monkeypatch.setattr(
        httpx,
        "get",
        lambda url, **kw: httpx.Response(200, request=httpx.Request("GET", url), content=body),
    )
    with pytest.raises(ValueError, match="WFS_COUNT_INVALID"):
        _get_wfs_hits("https://example.test/wfs", 10)


def test_native_crs_mismatch_fails_before_http(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: pytest.fail("network before validation"))
    spec = access()
    spec.params["crs"] = "EPSG:4326"
    with pytest.raises(ValueError, match="FILTER_CRS_INVALID"):
        WfsFetcher().fetch(spec, extent=(0, 0, 1, 1))

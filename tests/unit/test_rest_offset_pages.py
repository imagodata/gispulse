"""Counted pagination rejects silent truncation and unstable page identities."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest

from gispulse.adapters.rest.offset_pages import OffsetPagination, collect_offset_pages
from gispulse.adapters.rest.rest_fetcher import RestGeoJsonFetcher
from gispulse.core.plugin_model import AccessProtocol, AccessSpec


def recipe(**overrides):
    return {
        "mode": "offset",
        "offset_param": "offset",
        "limit_param": "limit",
        "page_size": 2,
        "max_pages": 4,
        "max_features": 10,
        "id_field": "id",
        "count_query": {"countOnly": "true"},
        **overrides,
    }


def page(ids, more):
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"id": i},
                "geometry": {"type": "Point", "coordinates": [i, i]},
            }
            for i in ids
        ],
        "exceededTransferLimit": more,
    }


def test_offset_transport_counts_and_preserves_all_pages(monkeypatch):
    calls = []

    def get(url, timeout):
        query = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        calls.append(query)
        if query.get("countOnly") == "true":
            return {"count": 3}
        assert "pagination" not in query and "require_extent" not in query
        assert query["geometry"] == "0.0,0.0,10.0,10.0"
        return page([0, 1], True) if query["offset"] == "0" else page([2], False)

    monkeypatch.setattr("gispulse.adapters.rest.rest_fetcher._get_geojson", get)
    result = RestGeoJsonFetcher().fetch(
        AccessSpec(
            protocol=AccessProtocol.REST_API,
            endpoint="https://example.test/query",
            params={"bbox_param": "geometry", "require_extent": True, "pagination": recipe()},
        ),
        extent=(0, 0, 10, 10),
    )
    assert list(result.data.id) == [0, 1, 2]
    assert result.metadata["complete"] is True
    assert result.metadata["page_count"] == 2
    assert result.metadata["expected_count"] == 3
    assert len(calls) == 4


@pytest.mark.parametrize(
    "responses,code",
    [
        ([{"count": 3}, page([0, 1], True), page([0], False)], "DUPLICATE_ID"),
        ([{"count": 3}, page([], False)], "INCOMPLETE"),
        ([{"count": 3}, page([0, 1], False)], "INCOMPLETE"),
        ([{"count": 1}, page([0], False), {"count": 2}], "COUNT_CHANGED"),
        ([{"count": 1}, {"error": {"code": 500}}], "PAGE_INVALID"),
        ([{"count": 100}], "FEATURE_LIMIT"),
        ([{"count": -1}], "COUNT_INVALID"),
        ([{"count": 1}, page([None], False)], "ID_MISSING"),
    ],
)
def test_failures_never_return_a_partial_collection(responses, code):
    iterator = iter(responses)
    with pytest.raises(ValueError, match=code):
        collect_offset_pages(OffsetPagination.from_params(recipe()), {}, lambda _: next(iterator))


def test_page_budget_and_reserved_offsets_are_explicit():
    responses = iter([{"count": 3}, page([0, 1], True)])
    with pytest.raises(ValueError, match="PAGE_LIMIT"):
        collect_offset_pages(
            OffsetPagination.from_params(recipe(max_pages=1)), {}, lambda _: next(responses)
        )
    with pytest.raises(ValueError, match="PAGINATION_INVALID"):
        collect_offset_pages(OffsetPagination.from_params(recipe()), {"offset": 1}, lambda _: {})


@pytest.mark.parametrize("extent", [None, (10, 0, 0, 1), (0, 0, float("nan"), 1), (180, 0, 181, 1)])
def test_bbox_fast_fail_before_network(monkeypatch, extent):
    def unexpected(*args):
        pytest.fail("network called before extent validation")

    monkeypatch.setattr("gispulse.adapters.rest.rest_fetcher._get_geojson", unexpected)
    with pytest.raises(ValueError, match="REST_EXTENT"):
        RestGeoJsonFetcher().fetch(
            AccessSpec(
                protocol=AccessProtocol.REST_API,
                endpoint="https://example.test",
                params={"require_extent": True, "bbox_param": "geometry", "pagination": recipe()},
            ),
            extent=extent,
        )


def test_empty_collection_is_counted_twice():
    calls = []

    def request(query):
        calls.append(query)
        return {"count": 0}

    features, report = collect_offset_pages(OffsetPagination.from_params(recipe()), {}, request)
    assert features == [] and report["page_count"] == 0
    assert len(calls) == 2


def test_arcgis_spatial_windows_can_be_short_or_empty():
    calls = []
    responses = iter(
        [
            {"count": 2},
            page([1], True),
            page([], True),
            page([2], True),
            page([], False),
            {"count": 2},
        ]
    )

    def request(query):
        calls.append(query)
        return next(responses)

    features, report = collect_offset_pages(
        OffsetPagination.from_params(recipe(cursor_policy="arcgis_page_window")), {}, request
    )
    assert [f["properties"]["id"] for f in features] == [1, 2]
    assert [q["offset"] for q in calls if "offset" in q] == [0, 2, 4, 6]
    assert report["page_count"] == 4


def test_arcgis_missing_completion_flag_requires_complete_count():
    last = page([1], False)
    del last["exceededTransferLimit"]
    responses = iter([{"count": 2}, last])
    with pytest.raises(ValueError, match="INCOMPLETE"):
        collect_offset_pages(
            OffsetPagination.from_params(recipe(cursor_policy="arcgis_page_window")),
            {},
            lambda _: next(responses),
        )


@pytest.mark.parametrize(
    "payload,code",
    [(None, "PAGE_INVALID"), ({**page([1], False), "properties": []}, "TRANSFER_FLAG_INVALID")],
)
def test_malformed_page_has_structured_error(payload, code):
    responses = iter([{"count": 1}, payload])
    with pytest.raises(ValueError, match=code):
        collect_offset_pages(OffsetPagination.from_params(recipe()), {}, lambda _: next(responses))

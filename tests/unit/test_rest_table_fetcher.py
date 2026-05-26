"""Tests for the paginated tabular REST fetcher (``AccessProtocol.REST_TABLE``).

Issue #196 — generic transport adapter for tabular JSON REST APIs that
answer ``{"data": [...], "next": ...}`` (Géorisques, BAN, RNB, …), as
opposed to :class:`RestGeoJsonFetcher` (#192) which expects a GeoJSON
``FeatureCollection``. MATERIALIZE-only: a paginated API has no zero-copy
DuckDB scan, so ``FetchMode.REFERENCE`` is not supported.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gispulse.core.plugin_model import (
    AccessProtocol,
    AccessSpec,
    FetchMode,
    Payload,
)

# Every fetch crosses the LazyFetcher/SSRF guard; offline_ssrf resolves
# test hosts to a public IP (no network) while keeping IP literals blockable.
pytestmark = pytest.mark.usefixtures("offline_ssrf")


def test_rest_table_protocol_member_exists() -> None:
    assert AccessProtocol.REST_TABLE.value == "rest-table"


def test_single_page_materializes_rows_to_jsonl(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from gispulse.adapters.rest import rest_table_fetcher
    from gispulse.adapters.rest.rest_table_fetcher import RestTableFetcher

    def fake_get(url: str, timeout: float) -> dict:
        return {"data": [{"code_insee": "63113"}, {"code_insee": "63001"}]}

    monkeypatch.setattr(rest_table_fetcher, "_get_json", fake_get)

    out = tmp_path / "out.jsonl"
    access = AccessSpec(
        protocol=AccessProtocol.REST_TABLE,
        endpoint="https://www.georisques.gouv.fr/api/v1/rga",
        params={"local_path": str(out)},
    )
    result = RestTableFetcher().fetch(access)

    assert result.payload is Payload.TABLE
    assert result.mode is FetchMode.MATERIALIZE
    assert result.metadata["row_count"] == 2
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert rows == [{"code_insee": "63113"}, {"code_insee": "63001"}]


def test_s3_uri_materializes_rows_to_storage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hashlib

    from gispulse.adapters.rest import rest_table_fetcher
    from gispulse.adapters.rest.rest_table_fetcher import RestTableFetcher

    def fake_get(url: str, timeout: float) -> dict:
        return {"data": [{"code_insee": "63113"}, {"code_insee": "63001"}]}

    captured: dict[str, object] = {}

    def fake_upload(s3_uri: str, body) -> None:
        captured["s3_uri"] = s3_uri
        captured["body"] = body.read()

    monkeypatch.setattr(rest_table_fetcher, "_get_json", fake_get)
    monkeypatch.setattr(
        rest_table_fetcher, "_upload_jsonl_to_s3", fake_upload, raising=False
    )

    uri = "s3://gispulse/raw/georisques/radon-63113.jsonl"
    access = AccessSpec(
        protocol=AccessProtocol.REST_TABLE,
        endpoint="https://www.georisques.gouv.fr/api/v1/radon",
        params={"s3_uri": uri},
    )
    result = RestTableFetcher().fetch(access)

    body = b'{"code_insee":"63113"}\n{"code_insee":"63001"}\n'
    assert result.payload is Payload.TABLE
    assert result.mode is FetchMode.MATERIALIZE
    assert result.data == uri
    assert result.reference == uri
    assert result.metadata["s3_uri"] == uri
    assert result.metadata["row_count"] == 2
    assert result.metadata["sha256"] == hashlib.sha256(body).hexdigest()
    assert captured == {"s3_uri": uri, "body": body}


def test_follows_next_url_and_accumulates_pages(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from gispulse.adapters.rest import rest_table_fetcher
    from gispulse.adapters.rest.rest_table_fetcher import RestTableFetcher

    pages = {
        "https://geo.example.org/api?page=1": {
            "data": [{"i": 1}],
            "next": "https://geo.example.org/api?page=2",
        },
        "https://geo.example.org/api?page=2": {"data": [{"i": 2}], "next": None},
    }
    calls: list[str] = []

    def fake_get(url: str, timeout: float) -> dict:
        calls.append(url)
        return pages[url]

    monkeypatch.setattr(rest_table_fetcher, "_get_json", fake_get)

    out = tmp_path / "out.jsonl"
    access = AccessSpec(
        protocol=AccessProtocol.REST_TABLE,
        endpoint="https://geo.example.org/api?page=1",
        params={"local_path": str(out), "pagination": {"next_key": "next"}},
    )
    result = RestTableFetcher().fetch(access)

    assert calls == list(pages.keys())
    assert result.metadata["row_count"] == 2
    assert result.metadata["page_count"] == 2
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert rows == [{"i": 1}, {"i": 2}]


def _chained_pages(n: int) -> dict[str, dict]:
    """``n`` pages, each linking to the next; the last one has ``next=None``."""
    base = "https://geo.example.org/api?page="
    return {
        f"{base}{i}": {
            "data": [{"i": i}],
            "next": f"{base}{i + 1}" if i < n else None,
        }
        for i in range(1, n + 1)
    }


def test_max_pages_raises_when_pagination_would_truncate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from gispulse.adapters.rest import rest_table_fetcher
    from gispulse.adapters.rest.rest_table_fetcher import RestTableFetcher

    pages = _chained_pages(5)
    calls: list[str] = []

    def fake_get(url: str, timeout: float) -> dict:
        calls.append(url)
        return pages[url]

    monkeypatch.setattr(rest_table_fetcher, "_get_json", fake_get)

    access = AccessSpec(
        protocol=AccessProtocol.REST_TABLE,
        endpoint="https://geo.example.org/api?page=1",
        params={
            "local_path": str(tmp_path / "out.jsonl"),
            "pagination": {"next_key": "next", "max_pages": 2},
        },
    )
    with pytest.raises(RuntimeError, match="REST_TABLE reached max_pages=2"):
        RestTableFetcher().fetch(access)
    assert len(calls) == 2


def test_max_rows_caps_and_truncates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from gispulse.adapters.rest import rest_table_fetcher
    from gispulse.adapters.rest.rest_table_fetcher import RestTableFetcher

    base = "https://geo.example.org/api?page="
    pages = {
        f"{base}1": {"data": [{"i": 1}, {"i": 2}], "next": f"{base}2"},
        f"{base}2": {"data": [{"i": 3}, {"i": 4}], "next": f"{base}3"},
        f"{base}3": {"data": [{"i": 5}], "next": None},
    }

    def fake_get(url: str, timeout: float) -> dict:
        return pages[url]

    monkeypatch.setattr(rest_table_fetcher, "_get_json", fake_get)

    out = tmp_path / "out.jsonl"
    access = AccessSpec(
        protocol=AccessProtocol.REST_TABLE,
        endpoint=f"{base}1",
        params={
            "local_path": str(out),
            "pagination": {"next_key": "next", "max_rows": 3},
        },
    )
    result = RestTableFetcher().fetch(access)

    assert result.metadata["row_count"] == 3
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert rows == [{"i": 1}, {"i": 2}, {"i": 3}]


def test_stops_on_already_seen_next_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from gispulse.adapters.rest import rest_table_fetcher
    from gispulse.adapters.rest.rest_table_fetcher import RestTableFetcher

    p1 = "https://geo.example.org/api?page=1"
    p2 = "https://geo.example.org/api?page=2"
    pages = {  # p2 cycles back to p1
        p1: {"data": [{"i": 1}], "next": p2},
        p2: {"data": [{"i": 2}], "next": p1},
    }
    calls: list[str] = []

    def fake_get(url: str, timeout: float) -> dict:
        calls.append(url)
        return pages[url]

    monkeypatch.setattr(rest_table_fetcher, "_get_json", fake_get)

    access = AccessSpec(
        protocol=AccessProtocol.REST_TABLE,
        endpoint=p1,
        params={
            "local_path": str(tmp_path / "out.jsonl"),
            "pagination": {"next_key": "next"},
        },
    )
    result = RestTableFetcher().fetch(access)

    assert calls == [p1, p2]
    assert result.metadata["page_count"] == 2


def test_rejects_cross_origin_next_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from gispulse.adapters.rest import rest_table_fetcher
    from gispulse.adapters.rest.rest_table_fetcher import RestTableFetcher

    p1 = "https://geo.example.org/api?page=1"
    evil = "https://evil.example.com/api?page=2"
    pages = {
        p1: {"data": [{"i": 1}], "next": evil},
        evil: {"data": [{"i": 99}], "next": None},
    }
    calls: list[str] = []

    def fake_get(url: str, timeout: float) -> dict:
        calls.append(url)
        return pages[url]

    monkeypatch.setattr(rest_table_fetcher, "_get_json", fake_get)

    out = tmp_path / "out.jsonl"
    access = AccessSpec(
        protocol=AccessProtocol.REST_TABLE,
        endpoint=p1,
        params={"local_path": str(out), "pagination": {"next_key": "next"}},
    )
    result = RestTableFetcher().fetch(access)

    assert calls == [p1]  # the cross-origin next URL is not followed
    assert result.metadata["page_count"] == 1
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert rows == [{"i": 1}]


def test_rejects_ssrf_internal_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gispulse.adapters.rest import rest_table_fetcher
    from gispulse.adapters.rest.rest_table_fetcher import RestTableFetcher
    from gispulse.core.ssrf import SSRFError

    def must_not_fetch(url: str, timeout: float) -> dict:
        raise AssertionError("SSRF guard should block before any GET")

    monkeypatch.setattr(rest_table_fetcher, "_get_json", must_not_fetch)

    access = AccessSpec(
        protocol=AccessProtocol.REST_TABLE,
        endpoint="http://127.0.0.1/api/v1/rga",
        params={},
    )
    with pytest.raises(SSRFError):
        RestTableFetcher().fetch(access)


def test_reference_mode_is_not_supported() -> None:
    from gispulse.adapters.rest.rest_table_fetcher import RestTableFetcher

    access = AccessSpec(
        protocol=AccessProtocol.REST_TABLE,
        endpoint="https://geo.example.org/api/v1/rga",
        params={},
    )
    with pytest.raises(NotImplementedError):
        RestTableFetcher().fetch(access, mode=FetchMode.REFERENCE)


def test_register_files_under_rest_table_without_touching_rest_api() -> None:
    from gispulse.adapters.rest.rest_fetcher import (
        RestGeoJsonFetcher,
        register_rest_geojson_fetcher,
    )
    from gispulse.adapters.rest.rest_table_fetcher import (
        RestTableFetcher,
        register_rest_table_fetcher,
    )
    from gispulse.core.sources import ProtocolRegistry

    reg = ProtocolRegistry()
    register_rest_geojson_fetcher(reg)
    register_rest_table_fetcher(reg)

    assert isinstance(reg.get_fetcher(AccessProtocol.REST_TABLE), RestTableFetcher)
    assert isinstance(reg.get_fetcher(AccessProtocol.REST_API), RestGeoJsonFetcher)


def test_query_params_are_forwarded_to_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from urllib.parse import parse_qs, urlsplit

    from gispulse.adapters.rest import rest_table_fetcher
    from gispulse.adapters.rest.rest_table_fetcher import RestTableFetcher

    seen: dict[str, str] = {}

    def fake_get(url: str, timeout: float) -> dict:
        seen["url"] = url
        return {"data": []}

    monkeypatch.setattr(rest_table_fetcher, "_get_json", fake_get)

    access = AccessSpec(
        protocol=AccessProtocol.REST_TABLE,
        endpoint="https://geo.example.org/api/v1/rga",
        params={
            "local_path": str(tmp_path / "out.jsonl"),
            "query": {"code_insee": "63113", "page_size": 100},
        },
    )
    RestTableFetcher().fetch(access)

    q = parse_qs(urlsplit(seen["url"]).query)
    assert q["code_insee"] == ["63113"]
    assert q["page_size"] == ["100"]


def test_metadata_carries_sha256_of_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import hashlib

    from gispulse.adapters.rest import rest_table_fetcher
    from gispulse.adapters.rest.rest_table_fetcher import RestTableFetcher

    def fake_get(url: str, timeout: float) -> dict:
        return {"data": [{"a": 1}, {"a": 2}]}

    monkeypatch.setattr(rest_table_fetcher, "_get_json", fake_get)

    out = tmp_path / "out.jsonl"
    access = AccessSpec(
        protocol=AccessProtocol.REST_TABLE,
        endpoint="https://geo.example.org/api/v1/rga",
        params={"local_path": str(out)},
    )
    result = RestTableFetcher().fetch(access)

    expected = hashlib.sha256(out.read_bytes()).hexdigest()
    assert result.metadata["sha256"] == expected


def test_follows_relative_next_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from gispulse.adapters.rest import rest_table_fetcher
    from gispulse.adapters.rest.rest_table_fetcher import RestTableFetcher

    base = "https://geo.example.org/api/v1/rga"
    pages = {
        f"{base}?page=1": {"data": [{"i": 1}], "next": "?page=2"},  # relative
        f"{base}?page=2": {"data": [{"i": 2}], "next": None},
    }
    calls: list[str] = []

    def fake_get(url: str, timeout: float) -> dict:
        calls.append(url)
        return pages[url]

    monkeypatch.setattr(rest_table_fetcher, "_get_json", fake_get)

    access = AccessSpec(
        protocol=AccessProtocol.REST_TABLE,
        endpoint=f"{base}?page=1",
        params={
            "local_path": str(tmp_path / "out.jsonl"),
            "pagination": {"next_key": "next"},
        },
    )
    result = RestTableFetcher().fetch(access)

    assert calls == [f"{base}?page=1", f"{base}?page=2"]
    assert result.metadata["page_count"] == 2


def test_non_list_data_key_yields_no_rows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from gispulse.adapters.rest import rest_table_fetcher
    from gispulse.adapters.rest.rest_table_fetcher import RestTableFetcher

    def fake_get(url: str, timeout: float) -> dict:
        return {"data": {"not": "a list"}}  # malformed: dict, not a list

    monkeypatch.setattr(rest_table_fetcher, "_get_json", fake_get)

    access = AccessSpec(
        protocol=AccessProtocol.REST_TABLE,
        endpoint="https://geo.example.org/api/v1/rga",
        params={"local_path": str(tmp_path / "out.jsonl")},
    )
    result = RestTableFetcher().fetch(access)

    assert result.metadata["row_count"] == 0


def test_empty_body_decode_error_is_fail_loud_even_when_configured_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from gispulse.adapters.rest import rest_table_fetcher
    from gispulse.adapters.rest.rest_table_fetcher import RestTableFetcher

    def fake_get(url: str, timeout: float) -> dict:
        raise json.JSONDecodeError("Expecting value", "", 0)

    monkeypatch.setattr(rest_table_fetcher, "_get_json", fake_get)

    access = AccessSpec(
        protocol=AccessProtocol.REST_TABLE,
        endpoint="https://geo.example.org/api/v1/rga",
        params={
            "local_path": str(tmp_path / "out.jsonl"),
            "pagination": {"empty_body_is_empty": True},
        },
    )
    with pytest.raises(ValueError, match="REST_TABLE JSON decode failed"):
        RestTableFetcher().fetch(access)


def test_empty_body_decode_error_still_raises_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from gispulse.adapters.rest import rest_table_fetcher
    from gispulse.adapters.rest.rest_table_fetcher import RestTableFetcher

    def fake_get(url: str, timeout: float) -> dict:
        raise json.JSONDecodeError("Expecting value", "", 0)

    monkeypatch.setattr(rest_table_fetcher, "_get_json", fake_get)

    access = AccessSpec(
        protocol=AccessProtocol.REST_TABLE,
        endpoint="https://geo.example.org/api/v1/rga",
        params={"local_path": str(tmp_path / "out.jsonl")},
    )
    with pytest.raises(ValueError, match="REST_TABLE JSON decode failed"):
        RestTableFetcher().fetch(access)


def test_get_json_disables_redirects(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    from gispulse.adapters.rest.rest_table_fetcher import _get_json

    captured: dict[str, object] = {}

    class _FakeResp:
        def raise_for_status(self) -> None: ...

        def json(self) -> dict:
            return {"data": []}

    def fake_httpx_get(url: str, **kwargs: object) -> _FakeResp:
        captured.update(kwargs)
        return _FakeResp()

    monkeypatch.setattr(httpx, "get", fake_httpx_get)
    _get_json("https://geo.example.org/api/v1/rga", 5.0)

    assert captured["follow_redirects"] is False


def test_max_total_seconds_raises_when_pagination_would_truncate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from gispulse.adapters.rest import rest_table_fetcher
    from gispulse.adapters.rest.rest_table_fetcher import RestTableFetcher

    pages = _chained_pages(5)

    def fake_get(url: str, timeout: float) -> dict:
        return pages[url]

    monkeypatch.setattr(rest_table_fetcher, "_get_json", fake_get)

    access = AccessSpec(
        protocol=AccessProtocol.REST_TABLE,
        endpoint="https://geo.example.org/api?page=1",
        params={
            "local_path": str(tmp_path / "out.jsonl"),
            # budget 0 → the deadline is reached right after the first page
            "pagination": {"next_key": "next", "max_total_seconds": 0},
        },
    )
    with pytest.raises(RuntimeError, match="REST_TABLE reached max_total_seconds"):
        RestTableFetcher().fetch(access)


def test_package_import_registers_rest_table_in_global_protocols() -> None:
    import gispulse.adapters.rest  # noqa: F401  (side-effect import)
    from gispulse.adapters.rest.rest_table_fetcher import RestTableFetcher
    from gispulse.core.sources import PROTOCOLS

    assert isinstance(
        PROTOCOLS.get_fetcher(AccessProtocol.REST_TABLE), RestTableFetcher
    )


@pytest.mark.parametrize(
    "evil_next",
    [
        "//evil.com/api",                  # scheme-relative → resolves cross-host
        "http://geo.example.org/api",      # scheme downgrade https → http
        "https://evil.example.com/api",    # outright different host
        "https://geo.example.org.evil.com/api",  # suffix-spoof host
    ],
)
def test_rejects_malicious_next_urls(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, evil_next: str
) -> None:
    from gispulse.adapters.rest import rest_table_fetcher
    from gispulse.adapters.rest.rest_table_fetcher import RestTableFetcher

    origin = "https://geo.example.org/api?page=1"
    calls: list[str] = []

    def fake_get(url: str, timeout: float) -> dict:
        calls.append(url)
        if url == origin:
            return {"data": [{"i": 1}], "next": evil_next}
        return {"data": [{"i": 99}]}  # would only be hit if the guard failed

    monkeypatch.setattr(rest_table_fetcher, "_get_json", fake_get)

    access = AccessSpec(
        protocol=AccessProtocol.REST_TABLE,
        endpoint=origin,
        params={"local_path": str(tmp_path / "out.jsonl"), "pagination": {"next_key": "next"}},
    )
    result = RestTableFetcher().fetch(access)

    assert calls == [origin]  # the malicious next is never followed
    assert result.metadata["page_count"] == 1


def test_empty_status_returns_empty_table(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import httpx

    from gispulse.adapters.rest import rest_table_fetcher
    from gispulse.adapters.rest.rest_table_fetcher import RestTableFetcher

    def fake_get(url: str, timeout: float) -> dict:
        request = httpx.Request("GET", url)
        response = httpx.Response(404, request=request)
        raise httpx.HTTPStatusError("404", request=request, response=response)

    monkeypatch.setattr(rest_table_fetcher, "_get_json", fake_get)

    access = AccessSpec(
        protocol=AccessProtocol.REST_TABLE,
        endpoint="https://geo.example.org/api/v1/tri_zonage",
        params={
            "local_path": str(tmp_path / "out.jsonl"),
            "pagination": {"empty_statuses": [404]},
        },
    )
    result = RestTableFetcher().fetch(access)

    assert result.metadata["row_count"] == 0
    assert result.metadata["page_count"] == 0


def test_non_empty_status_still_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import httpx

    from gispulse.adapters.rest import rest_table_fetcher
    from gispulse.adapters.rest.rest_table_fetcher import RestTableFetcher

    def fake_get(url: str, timeout: float) -> dict:
        request = httpx.Request("GET", url)
        response = httpx.Response(500, request=request)
        raise httpx.HTTPStatusError("500", request=request, response=response)

    monkeypatch.setattr(rest_table_fetcher, "_get_json", fake_get)

    access = AccessSpec(
        protocol=AccessProtocol.REST_TABLE,
        endpoint="https://geo.example.org/api/v1/tri_zonage",
        params={
            "local_path": str(tmp_path / "out.jsonl"),
            "pagination": {"empty_statuses": [404]},  # 500 not listed → must raise
        },
    )
    with pytest.raises(httpx.HTTPStatusError):
        RestTableFetcher().fetch(access)


def test_body_row_source_wraps_whole_body(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from gispulse.adapters.rest import rest_table_fetcher
    from gispulse.adapters.rest.rest_table_fetcher import RestTableFetcher

    def fake_get(url: str, timeout: float) -> dict:
        # Géorisques RGA 2024+ shape: a top-level object, no "data" list.
        return {"codeExposition": "2", "exposition": "moyen"}

    monkeypatch.setattr(rest_table_fetcher, "_get_json", fake_get)

    out = tmp_path / "out.jsonl"
    access = AccessSpec(
        protocol=AccessProtocol.REST_TABLE,
        endpoint="https://geo.example.org/api/v1/rga",
        params={
            "local_path": str(out),
            "pagination": {"row_source": "body"},
        },
    )
    result = RestTableFetcher().fetch(access)

    assert result.metadata["row_count"] == 1
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert rows == [{"codeExposition": "2", "exposition": "moyen"}]

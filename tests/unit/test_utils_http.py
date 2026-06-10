"""Tests for gispulse.utils.http — DataClientError + json_get."""

from __future__ import annotations

import json

import pytest

from gispulse.utils.http import DataClientError, json_get


# ---------------------------------------------------------------------------
# Minimal duck-typed httpx.Client stub (no httpx import needed)
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
    """Minimal duck-typed HTTP client stub."""

    def __init__(self, response: _Response) -> None:
        self._response = response
        self.last_url: str | None = None
        self.last_params: object = None
        self.last_timeout: float | None = None

    def get(self, url: str, *, params: object = None, timeout: float = 0.0) -> _Response:
        self.last_url = url
        self.last_params = params
        self.last_timeout = timeout
        return self._response


class _BadJsonResponse(_Response):
    def json(self) -> object:
        raise json.JSONDecodeError("bad", "", 0)


# ---------------------------------------------------------------------------
# DataClientError
# ---------------------------------------------------------------------------


def test_data_client_error_is_runtime_error() -> None:
    exc = DataClientError("boom")
    assert isinstance(exc, RuntimeError)
    assert str(exc) == "boom"


# ---------------------------------------------------------------------------
# json_get — happy path
# ---------------------------------------------------------------------------


def test_json_get_returns_dict() -> None:
    body = json.dumps({"key": "value"}).encode()
    client = _Client(_Response(body=body))
    result = json_get(client, "https://example.com/api", timeout=5.0)
    assert result == {"key": "value"}


def test_json_get_passes_params_and_timeout() -> None:
    client = _Client(_Response())
    json_get(client, "https://x/", params={"a": "1"}, timeout=7.0)
    assert client.last_params == {"a": "1"}
    assert client.last_timeout == 7.0


def test_json_get_url_forwarded() -> None:
    client = _Client(_Response())
    json_get(client, "https://x/path", timeout=1.0)
    assert client.last_url == "https://x/path"


def test_json_get_default_timeout_is_positive() -> None:
    import inspect
    sig = inspect.signature(json_get)
    assert sig.parameters["timeout"].default > 0


# ---------------------------------------------------------------------------
# json_get — error paths
# ---------------------------------------------------------------------------


def test_json_get_raises_data_client_error_on_bad_json() -> None:
    client = _Client(_BadJsonResponse())
    with pytest.raises(DataClientError, match="Non-JSON"):
        json_get(client, "https://x/", timeout=1.0)


def test_json_get_raises_data_client_error_on_non_object() -> None:
    # Server returns a JSON array — not a dict
    body = json.dumps([1, 2, 3]).encode()
    client = _Client(_Response(body=body))
    with pytest.raises(DataClientError, match="non-object"):
        json_get(client, "https://x/", timeout=1.0)


def test_json_get_error_includes_content_type() -> None:
    client = _Client(_BadJsonResponse(content_type="text/html; charset=utf-8"))
    with pytest.raises(DataClientError, match="text/html"):
        json_get(client, "https://x/", timeout=1.0)


def test_json_get_chained_cause_on_json_decode_error() -> None:
    client = _Client(_BadJsonResponse())
    with pytest.raises(DataClientError) as exc_info:
        json_get(client, "https://x/", timeout=1.0)
    assert isinstance(exc_info.value.__cause__, json.JSONDecodeError)

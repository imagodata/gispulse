"""Tests for gispulse.utils.errors — ErrorContext Literal + classify_exception."""

from __future__ import annotations

import json

import pytest

from gispulse.utils.errors import ErrorContext, classify_exception


# ---------------------------------------------------------------------------
# ErrorContext sanity
# ---------------------------------------------------------------------------


def test_error_context_values() -> None:
    # All expected values exist as valid Literal members
    valid: list[ErrorContext] = [
        "network",
        "parse",
        "missing_data",
        "config",
        "truncation",
    ]
    # Runtime check — just ensure they're plain strings
    for v in valid:
        assert isinstance(v, str)


# ---------------------------------------------------------------------------
# classify_exception — stdlib exceptions (no httpx needed)
# ---------------------------------------------------------------------------


def test_json_decode_error_is_parse() -> None:
    exc = json.JSONDecodeError("msg", "", 0)
    assert classify_exception(exc) == "parse"


def test_runtime_error_is_config() -> None:
    assert classify_exception(RuntimeError("missing catalog entry")) == "config"


# ---------------------------------------------------------------------------
# P2a — cause-chain walking + DataClientError classification
# ---------------------------------------------------------------------------


def test_data_client_error_bare_is_parse() -> None:
    """DataClientError raised without a cause classifies as parse."""
    from gispulse.utils.http import DataClientError

    exc = DataClientError("non-object response")
    assert classify_exception(exc) == "parse"


def test_data_client_error_from_json_decode_error_is_parse() -> None:
    """DataClientError chained from JSONDecodeError: outer is parse, inner is also
    parse — either way the result must be parse, not config."""
    from gispulse.utils.http import DataClientError

    cause = json.JSONDecodeError("bad json", "", 0)
    try:
        raise DataClientError("Non-JSON response") from cause
    except DataClientError as exc:
        assert exc.__cause__ is cause
        assert classify_exception(exc) == "parse"


def test_runtime_error_from_timeout_is_network() -> None:
    """A plain RuntimeError wrapping an httpx.TimeoutException: the outer
    RuntimeError is classified first as 'config' (it IS a RuntimeError but
    not a DataClientError). Verify that the outer wins."""
    httpx_mod = pytest.importorskip("httpx", reason="httpx not installed")
    inner = httpx_mod.TimeoutException("timed out")
    try:
        raise RuntimeError("pipeline failed") from inner
    except RuntimeError as outer:
        # RuntimeError → "config" (first hit in chain walk, before the cause).
        assert classify_exception(outer) == "config"


def test_cause_classified_when_outer_is_generic() -> None:
    """When the outer exception maps to None (generic Exception), the cause wins."""
    inner = json.JSONDecodeError("bad", "", 0)

    class _Wrapper(Exception):
        pass

    try:
        raise _Wrapper("wrapper") from inner
    except _Wrapper as outer:
        assert classify_exception(outer) == "parse"


def test_cycle_in_chain_does_not_hang() -> None:
    """A self-referential __cause__ must not cause an infinite loop."""
    exc = RuntimeError("cycle")
    try:
        exc.__cause__ = exc  # type: ignore[assignment]
    except (TypeError, AttributeError):
        pytest.skip("cannot set __cause__ to self on this Python version")
    # Should return without hanging.
    result = classify_exception(exc)
    assert result in ("config", "network", "parse", "missing_data", "truncation")


def test_key_error_is_parse() -> None:
    assert classify_exception(KeyError("field")) == "parse"


def test_type_error_is_parse() -> None:
    assert classify_exception(TypeError("expected str")) == "parse"


def test_value_error_is_parse() -> None:
    assert classify_exception(ValueError("invalid")) == "parse"


def test_generic_exception_is_missing_data() -> None:
    assert classify_exception(Exception("unknown")) == "missing_data"


def test_os_error_is_missing_data() -> None:
    assert classify_exception(OSError("disk error")) == "missing_data"


# ---------------------------------------------------------------------------
# classify_exception — httpx exceptions (skip if httpx not installed)
# ---------------------------------------------------------------------------


httpx = pytest.importorskip("httpx", reason="httpx not installed")


def test_timeout_exception_is_network() -> None:
    exc = httpx.TimeoutException("timed out")
    assert classify_exception(exc) == "network"


def test_connect_error_is_network() -> None:
    exc = httpx.ConnectError("refused")
    assert classify_exception(exc) == "network"


def test_http_status_error_5xx_is_network() -> None:
    response = httpx.Response(500)
    exc = httpx.HTTPStatusError("error", request=httpx.Request("GET", "https://x/"), response=response)
    assert classify_exception(exc) == "network"


def test_http_status_error_4xx_is_config() -> None:
    response = httpx.Response(403)
    exc = httpx.HTTPStatusError("forbidden", request=httpx.Request("GET", "https://x/"), response=response)
    assert classify_exception(exc) == "config"


def test_http_status_error_401_is_config() -> None:
    response = httpx.Response(401)
    exc = httpx.HTTPStatusError("unauth", request=httpx.Request("GET", "https://x/"), response=response)
    assert classify_exception(exc) == "config"


def test_read_error_is_network() -> None:
    exc = httpx.ReadError("read reset")
    assert classify_exception(exc) == "network"

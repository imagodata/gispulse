"""Unit tests for tabular file downloads (``AccessProtocol.TABLE_FILE``)."""

from __future__ import annotations

from pathlib import Path

import pytest

from gispulse.core.fetchers import DUCKDB_SCAN_KEY
from gispulse.core.fetchers.table_file import TableFileFetcher
from gispulse.core.plugin_model import (
    AccessProtocol,
    AccessSpec,
    FetchMode,
    Payload,
)
from gispulse.core.ssrf import SSRFError

pytestmark = pytest.mark.usefixtures("offline_ssrf")


def _access(endpoint: str, **params: object) -> AccessSpec:
    return AccessSpec(protocol=AccessProtocol.TABLE_FILE, endpoint=endpoint, params=params)


def test_protocol_and_payload() -> None:
    assert TableFileFetcher.protocol is AccessProtocol.TABLE_FILE
    assert TableFileFetcher.payload is Payload.TABLE


def test_reference_scan_plain_csv_uses_duckdb_read_csv() -> None:
    result = TableFileFetcher().virtual_table(_access("https://host.example.org/iris.csv"))

    assert result.payload is Payload.TABLE
    assert result.mode is FetchMode.REFERENCE
    assert result.metadata[DUCKDB_SCAN_KEY] == (
        "read_csv_auto('/vsicurl/https://host.example.org/iris.csv')"
    )


def test_reference_scan_zip_csv_requires_explicit_archive_member() -> None:
    with pytest.raises(
        ValueError,
        match="TABLE_FILE zip archives require access.params.archive_member",
    ):
        TableFileFetcher().virtual_table(
            _access(
                "https://host.example.org/iris_csv.zip",
                archive_format="zip",
                table_format="csv",
            )
        )


def test_reference_scan_zip_csv_can_target_archive_member() -> None:
    result = TableFileFetcher().virtual_table(
        _access(
            "https://host.example.org/iris_csv.zip",
            archive_format="zip",
            table_format="csv",
            archive_member="tables/iris.csv",
        )
    )

    assert result.metadata[DUCKDB_SCAN_KEY] == (
        "read_csv_auto("
        "'/vsizip//vsicurl/https://host.example.org/iris_csv.zip/tables/iris.csv')"
    )


def test_materialize_streams_remote_table_file_to_disk(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}

    class _FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def iter_bytes(self):
            yield b"IRIS;LIBIRIS\n"
            yield b"631130101;La Gauthiere\n"

        def __enter__(self) -> "_FakeResponse":
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    def _fake_stream(method: str, url: str, **kw: object) -> _FakeResponse:
        assert method == "GET"
        assert url == "https://host.example.org/iris_csv.zip"
        captured.update(kw)
        return _FakeResponse()

    import httpx

    monkeypatch.setattr(httpx, "stream", _fake_stream)

    dest = tmp_path / "iris_csv.zip"
    result = TableFileFetcher().fetch(
        _access(
            "https://host.example.org/iris_csv.zip",
            local_path=str(dest),
            archive_format="zip",
            table_format="csv",
        ),
        mode=FetchMode.MATERIALIZE,
    )

    assert result.payload is Payload.TABLE
    assert result.mode is FetchMode.MATERIALIZE
    assert result.data == str(dest)
    assert result.metadata == {"archive_format": "zip", "table_format": "csv"}
    assert captured["timeout"] == 120.0
    assert captured["follow_redirects"] is True
    assert dest.read_bytes() == b"IRIS;LIBIRIS\n631130101;La Gauthiere\n"


def test_materialize_retries_transient_table_file_download(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import httpx

    calls = 0

    class _FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def iter_bytes(self):
            yield b"ok"

        def __enter__(self) -> "_FakeResponse":
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    def _fake_stream(method: str, url: str, **kw: object) -> _FakeResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            request = httpx.Request(method, url)
            raise httpx.ConnectError("temporary network failure", request=request)
        return _FakeResponse()

    monkeypatch.setattr(httpx, "stream", _fake_stream)

    dest = tmp_path / "iris_csv.zip"
    result = TableFileFetcher().fetch(
        _access(
            "https://host.example.org/iris_csv.zip",
            local_path=str(dest),
            retry_backoff=0,
        ),
        mode=FetchMode.MATERIALIZE,
    )

    assert result.data == str(dest)
    assert calls == 2
    assert dest.read_bytes() == b"ok"


def test_materialize_local_table_file_returns_path(tmp_path: Path) -> None:
    csv_path = tmp_path / "iris.csv"
    csv_path.write_text("IRIS;LIBIRIS\n631130101;La Gauthiere\n", encoding="utf-8")

    result = TableFileFetcher().fetch(_access(str(csv_path)))

    assert result.payload is Payload.TABLE
    assert result.data == str(csv_path)


def test_fetch_rejects_private_address() -> None:
    with pytest.raises(SSRFError):
        TableFileFetcher().fetch(_access("http://127.0.0.1/iris.csv"))

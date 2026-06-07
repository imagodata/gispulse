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


def test_reference_scan_zip_csv_rejects_wildcard_archive_member() -> None:
    with pytest.raises(
        ValueError,
        match="TABLE_FILE zip archive_member must name one concrete member",
    ):
        TableFileFetcher().virtual_table(
            _access(
                "https://host.example.org/iris_csv.zip",
                archive_format="zip",
                table_format="csv",
                archive_member="*.csv",
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
        "read_csv_auto('/vsizip//vsicurl/https://host.example.org/iris_csv.zip/tables/iris.csv')"
    )


def test_reference_scan_zip_csv_escapes_archive_member_sql_literal() -> None:
    result = TableFileFetcher().virtual_table(
        _access(
            "https://host.example.org/iris_csv.zip",
            archive_format="zip",
            table_format="csv",
            archive_member="tables/l'iris.csv",
        )
    )

    assert result.metadata[DUCKDB_SCAN_KEY] == (
        "read_csv_auto("
        "'/vsizip//vsicurl/https://host.example.org/iris_csv.zip/tables/l''iris.csv')"
    )


def test_reference_scan_zip_csv_escapes_archive_member_sql_literal() -> None:
    result = TableFileFetcher().virtual_table(
        _access(
            "https://host.example.org/iris_csv.zip",
            archive_format="zip",
            table_format="csv",
            archive_member="tables/l'iris.csv",
        )
    )

    assert result.metadata[DUCKDB_SCAN_KEY] == (
        "read_csv_auto("
        "'/vsizip//vsicurl/https://host.example.org/iris_csv.zip/tables/l''iris.csv')"
    )


def test_materialize_streams_remote_table_file_to_disk(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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
    assert dest.read_bytes() == b"IRIS;LIBIRIS\n631130101;La Gauthiere\n"


def test_materialize_remote_table_file_passes_explicit_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import httpx

    timeouts: list[httpx.Timeout] = []

    class _FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def iter_bytes(self):
            yield b"IRIS;LIBIRIS\n"

        def __enter__(self) -> "_FakeResponse":
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    def _fake_stream(method: str, url: str, **kw: object) -> _FakeResponse:
        assert method == "GET"
        assert url == "https://host.example.org/iris.csv"
        timeout = kw["timeout"]
        assert isinstance(timeout, httpx.Timeout)
        timeouts.append(timeout)
        return _FakeResponse()

    monkeypatch.setenv("GISPULSE_HTTP_READ_TIMEOUT", "42.5")
    monkeypatch.setattr(httpx, "stream", _fake_stream)

    dest = tmp_path / "iris.csv"
    TableFileFetcher().fetch(
        _access("https://host.example.org/iris.csv", local_path=str(dest)),
        mode=FetchMode.MATERIALIZE,
    )

    assert len(timeouts) == 1
    assert timeouts[0].connect == 10.0
    assert timeouts[0].read == 42.5
    assert timeouts[0].write == 30.0
    assert timeouts[0].pool == 10.0


def test_materialize_remote_table_file_retries_timeout_then_succeeds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import httpx

    attempts = 0
    sleeps: list[float] = []

    class _FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def iter_bytes(self):
            yield b"IRIS;LIBIRIS\n"

        def __enter__(self) -> "_FakeResponse":
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    def _fake_stream(method: str, url: str, **kw: object) -> _FakeResponse:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            request = httpx.Request(method, url)
            raise httpx.ReadTimeout("read operation timed out", request=request)
        return _FakeResponse()

    monkeypatch.setattr("time.sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(httpx, "stream", _fake_stream)

    dest = tmp_path / "iris.csv"
    result = TableFileFetcher().fetch(
        _access("https://host.example.org/iris.csv", local_path=str(dest)),
        mode=FetchMode.MATERIALIZE,
    )

    assert attempts == 2
    assert sleeps == [2.0]
    assert result.data == str(dest)
    assert dest.read_bytes() == b"IRIS;LIBIRIS\n"


def test_materialize_remote_table_file_retries_500_then_succeeds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import httpx

    attempts = 0
    sleeps: list[float] = []

    class _ServerErrorResponse:
        def raise_for_status(self) -> None:
            request = httpx.Request("GET", "https://host.example.org/iris.csv")
            response = httpx.Response(500, request=request)
            raise httpx.HTTPStatusError("server error", request=request, response=response)

        def iter_bytes(self):
            raise AssertionError("body must not be read before retrying")

        def __enter__(self) -> "_ServerErrorResponse":
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    class _OkResponse:
        def raise_for_status(self) -> None:
            return None

        def iter_bytes(self):
            yield b"IRIS;LIBIRIS\n"

        def __enter__(self) -> "_OkResponse":
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    def _fake_stream(method: str, url: str, **kw: object):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return _ServerErrorResponse()
        return _OkResponse()

    monkeypatch.setattr("time.sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(httpx, "stream", _fake_stream)

    dest = tmp_path / "iris.csv"
    TableFileFetcher().fetch(
        _access("https://host.example.org/iris.csv", local_path=str(dest)),
        mode=FetchMode.MATERIALIZE,
    )

    assert attempts == 2
    assert sleeps == [2.0]
    assert dest.read_bytes() == b"IRIS;LIBIRIS\n"


def test_materialize_remote_table_file_does_not_retry_404(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import httpx

    attempts = 0

    class _NotFoundResponse:
        def raise_for_status(self) -> None:
            request = httpx.Request("GET", "https://host.example.org/missing.csv")
            response = httpx.Response(404, request=request)
            raise httpx.HTTPStatusError("not found", request=request, response=response)

        def iter_bytes(self):
            raise AssertionError("body must not be read for a 404")

        def __enter__(self) -> "_NotFoundResponse":
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    def _fake_stream(method: str, url: str, **kw: object) -> _NotFoundResponse:
        nonlocal attempts
        attempts += 1
        return _NotFoundResponse()

    monkeypatch.setattr(httpx, "stream", _fake_stream)

    with pytest.raises(httpx.HTTPStatusError):
        TableFileFetcher().fetch(
            _access(
                "https://host.example.org/missing.csv",
                local_path=str(tmp_path / "missing.csv"),
            ),
            mode=FetchMode.MATERIALIZE,
        )

    assert attempts == 1


def test_materialize_local_table_file_returns_path(tmp_path: Path) -> None:
    csv_path = tmp_path / "iris.csv"
    csv_path.write_text("IRIS;LIBIRIS\n631130101;La Gauthiere\n", encoding="utf-8")

    result = TableFileFetcher().fetch(_access(str(csv_path)))

    assert result.payload is Payload.TABLE
    assert result.data == str(csv_path)


def test_materialize_can_copy_csv_directly_to_s3_uri(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executed: list[str] = []

    class _FakeConn:
        def execute(self, sql: str) -> None:
            executed.append(sql)

    class _FakeSession:
        conn = _FakeConn()

        def __enter__(self) -> "_FakeSession":
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    def _unexpected_stream(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("S3 materialization must not stream the raw file")

    import gispulse.persistence.duckdb_engine as duckdb_engine
    import httpx

    monkeypatch.setattr(duckdb_engine, "DuckDBSession", _FakeSession)
    monkeypatch.setattr(httpx, "stream", _unexpected_stream)

    uri = "s3://gispulse/stage/georisques/sis-bulk/millesime=2025/national/sis.parquet"
    result = TableFileFetcher().fetch(
        _access(
            "https://host.example.org/sis.csv",
            table_format="csv",
            s3_uri=uri,
        ),
        mode=FetchMode.MATERIALIZE,
    )

    assert result.payload is Payload.TABLE
    assert result.mode is FetchMode.MATERIALIZE
    assert result.data == uri
    assert result.reference == uri
    assert result.metadata["s3_uri"] == uri
    assert result.metadata["table_format"] == "csv"
    assert f"TO '{uri}' (FORMAT PARQUET)" in executed[0]
    assert "read_csv_auto('/vsicurl/https://host.example.org/sis.csv')" in executed[0]


def test_materialize_can_copy_zip_csv_to_s3_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executed: list[str] = []

    class _FakeConn:
        def execute(self, sql: str) -> None:
            executed.append(sql)

    class _FakeSession:
        conn = _FakeConn()

        def __enter__(self) -> "_FakeSession":
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    def _unexpected_stream(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("S3 materialization must not stream the raw file")

    import gispulse.persistence.duckdb_engine as duckdb_engine
    import httpx

    monkeypatch.setenv("GISPULSE_S3_BUCKET", "gispulse-beta")
    monkeypatch.setattr(duckdb_engine, "DuckDBSession", _FakeSession)
    monkeypatch.setattr(httpx, "stream", _unexpected_stream)

    result = TableFileFetcher().fetch(
        _access(
            "https://host.example.org/gaspar.zip",
            archive_format="zip",
            table_format="csv",
            archive_member="tables/gaspar.csv",
            s3_key="stage/georisques/gaspar-bulk/millesime=2025/national/gaspar.parquet",
        ),
        mode=FetchMode.MATERIALIZE,
    )

    uri = "s3://gispulse-beta/stage/georisques/gaspar-bulk/millesime=2025/national/gaspar.parquet"
    assert result.data == uri
    assert result.reference == uri
    assert result.metadata["s3_uri"] == uri
    assert f"TO '{uri}' (FORMAT PARQUET)" in executed[0]
    assert (
        "read_csv_auto('/vsizip//vsicurl/https://host.example.org/gaspar.zip/tables/gaspar.csv')"
    ) in executed[0]


def test_fetch_rejects_private_address() -> None:
    with pytest.raises(SSRFError):
        TableFileFetcher().fetch(_access("http://127.0.0.1/iris.csv"))

"""Unit tests for A3 (#229) — ``GeoParquetS3Fetcher``.

Zero network: the lazy path is pure SQL string-building, and the
materialise path's only network actor (``DuckDBSession``) is monkey-
patched with a recording fake.
"""

from __future__ import annotations

import pytest

from gispulse.core.fetchers import DUCKDB_SCAN_KEY
from gispulse.core.fetchers.geoparquet_s3 import GeoParquetS3Fetcher
from gispulse.core.plugin_model import (
    AccessProtocol,
    AccessSpec,
    FetchMode,
    Payload,
)
from gispulse.core.ssrf import SSRFError

# Every test exercises the LazyFetcher SSRF guard; the offline_ssrf
# fixture (tests/unit/conftest.py) keeps DNS resolution off the network.
pytestmark = pytest.mark.usefixtures("offline_ssrf")


def _access(endpoint: str, **params: object) -> AccessSpec:
    return AccessSpec(
        protocol=AccessProtocol.REMOTE_TABLE, endpoint=endpoint, params=params
    )


# -- contract ---------------------------------------------------------------


def test_protocol_and_payload() -> None:
    assert GeoParquetS3Fetcher.protocol is AccessProtocol.REMOTE_TABLE
    assert GeoParquetS3Fetcher.payload is Payload.VECTOR


# -- lazy scan --------------------------------------------------------------


def test_reference_scan_emits_read_parquet_with_hive() -> None:
    result = GeoParquetS3Fetcher().virtual_table(
        _access("s3://overture/release")
    )
    scan = result.metadata[DUCKDB_SCAN_KEY]
    assert scan == (
        "read_parquet('s3://overture/release/**', hive_partitioning=true)"
    )
    assert result.mode is FetchMode.REFERENCE
    assert result.data is None


def test_reference_scan_pushdown_bbox_against_overture_struct() -> None:
    result = GeoParquetS3Fetcher().virtual_table(
        _access("s3://overture/release"), extent=(1.0, 2.0, 3.0, 4.0)
    )
    scan = result.metadata[DUCKDB_SCAN_KEY]
    # Wrapped in a sub-query carrying the bbox-struct WHERE predicate.
    assert scan.startswith("(SELECT * FROM read_parquet(")
    assert "bbox.xmin <= 3.0" in scan
    assert "bbox.xmax >= 1.0" in scan
    assert "bbox.ymin <= 4.0" in scan
    assert "bbox.ymax >= 2.0" in scan


def test_reference_scan_custom_bbox_column() -> None:
    result = GeoParquetS3Fetcher().virtual_table(
        _access("s3://lake/data", bbox_column="geo_bbox"),
        extent=(0, 0, 1, 1),
    )
    assert "geo_bbox.xmin" in result.metadata[DUCKDB_SCAN_KEY]


def test_reference_scan_single_file_no_glob() -> None:
    result = GeoParquetS3Fetcher().virtual_table(
        _access("https://host/places.parquet", glob="")
    )
    assert result.metadata[DUCKDB_SCAN_KEY] == (
        "read_parquet('https://host/places.parquet', hive_partitioning=true)"
    )


def test_reference_scan_hive_can_be_disabled() -> None:
    result = GeoParquetS3Fetcher().virtual_table(
        _access("s3://lake/data", hive_partitioning=False)
    )
    assert "hive_partitioning=false" in result.metadata[DUCKDB_SCAN_KEY]


# -- mode dispatch ----------------------------------------------------------


def test_fetch_materialize_runs_copy_to_parquet(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
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

    import gispulse.persistence.duckdb_engine as duckdb_engine

    monkeypatch.setattr(duckdb_engine, "DuckDBSession", _FakeSession)

    dest = str(tmp_path / "out.parquet")  # type: ignore[operator]
    result = GeoParquetS3Fetcher().fetch(
        _access("s3://overture/release", local_path=dest),
        mode=FetchMode.MATERIALIZE,
        extent=(0, 0, 10, 10),
    )
    assert result.mode is FetchMode.MATERIALIZE
    assert result.data == dest
    assert len(executed) == 1
    assert executed[0].startswith("COPY (SELECT * FROM ")
    assert f"TO '{dest}' (FORMAT PARQUET)" in executed[0]
    # bbox pushdown survives into the COPY.
    assert "bbox.xmin" in executed[0]


# -- SSRF guard -------------------------------------------------------------


@pytest.mark.parametrize("mode", [FetchMode.REFERENCE, FetchMode.MATERIALIZE])
def test_fetch_rejects_private_address(mode: FetchMode) -> None:
    with pytest.raises(SSRFError):
        GeoParquetS3Fetcher().fetch(
            _access("http://127.0.0.1/lake"), mode=mode
        )

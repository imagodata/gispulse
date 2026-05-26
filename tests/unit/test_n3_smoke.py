"""Unit tests for the isolated N3 smoke script."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from gispulse.core.plugin_model import AccessProtocol, AccessSpec, Payload
from gispulse.core.sources import SourceEntryRef


class _FakeSource:
    name = "georisques"

    def catalog(self) -> list[SourceEntryRef]:
        return [
            SourceEntryRef(
                id="sis-bulk",
                name="SIS bulk",
                access=AccessSpec(
                    protocol=AccessProtocol.TABLE_FILE,
                    endpoint="https://example.invalid/sis.csv",
                    params={"table_format": "csv"},
                ),
                payload=Payload.TABLE,
                metadata={"base_key": "sis", "data_format": "csv"},
            )
        ]


@dataclass
class _FakeBulkResult:
    manifest: dict[str, object]


class _FakeRunner:
    instances: list["_FakeRunner"] = []

    def __init__(
        self,
        *,
        bucket: str,
        key_prefix: str,
        write_table_raw: bool,
    ) -> None:
        self.bucket = bucket
        self.key_prefix = key_prefix
        self.write_table_raw = write_table_raw
        self.calls: list[tuple[object, str, object, object]] = []
        self.instances.append(self)

    def run_entry(
        self,
        source: object,
        entry: str,
        *,
        departement: object | None = None,
        revision: object | None = None,
    ) -> _FakeBulkResult:
        self.calls.append((source, entry, departement, revision))
        return _FakeBulkResult(
            manifest={
                "raw_s3_uri": (
                    "s3://gispulse/smoke-n3/raw/georisques/sis-bulk/"
                    "millesime=smoke/departement=63/sis.csv"
                ),
                "stage_s3_uri": (
                    "s3://gispulse/smoke-n3/stage/georisques/sis-bulk/"
                    "millesime=smoke/departement=63/sis.parquet"
                ),
            }
        )


class _FakeS3Client:
    def __init__(self) -> None:
        self.heads: list[tuple[str, str]] = []

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, object]:
        self.heads.append((Bucket, Key))
        return {"ContentLength": 100 if Key.endswith(".csv") else 512}


class _FakeConn:
    def __init__(self) -> None:
        self.sql: list[str] = []

    def execute(self, sql: str) -> "_FakeConn":
        self.sql.append(sql)
        return self

    def fetchone(self) -> tuple[int]:
        return (42,)


class _FakeDuckDBSession:
    instances: list["_FakeDuckDBSession"] = []

    def __init__(self) -> None:
        self.conn = _FakeConn()
        self.instances.append(self)

    def __enter__(self) -> "_FakeDuckDBSession":
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def test_n3_smoke_uses_isolated_prefix_and_reads_stage_parquet_from_s3(
    caplog,
) -> None:
    from scripts.n3_smoke import N3SmokeConfig, run_smoke

    caplog.set_level(logging.INFO)
    _FakeRunner.instances.clear()
    _FakeDuckDBSession.instances.clear()
    s3 = _FakeS3Client()
    source = _FakeSource()
    cfg = N3SmokeConfig(
        bucket="gispulse",
        prefix="smoke-n3/",
        source="georisques",
        entry="sis-bulk",
        department="63",
        revision="smoke",
    )

    report = run_smoke(
        cfg,
        source_loader=lambda name: source,
        runner_factory=_FakeRunner,
        s3_client_factory=lambda: s3,
        duckdb_session_factory=_FakeDuckDBSession,
    )

    runner = _FakeRunner.instances[0]
    assert runner.bucket == "gispulse"
    assert runner.key_prefix == "smoke-n3/"
    assert runner.write_table_raw is True
    assert runner.calls == [(source, "sis-bulk", "63", "smoke")]
    assert s3.heads == [
        (
            "gispulse",
            "smoke-n3/raw/georisques/sis-bulk/millesime=smoke/"
            "departement=63/sis.csv",
        ),
        (
            "gispulse",
            "smoke-n3/stage/georisques/sis-bulk/millesime=smoke/"
            "departement=63/sis.parquet",
        ),
    ]
    assert _FakeDuckDBSession.instances[0].conn.sql == [
        (
            "SELECT count(*) FROM read_parquet('s3://gispulse/smoke-n3/stage/"
            "georisques/sis-bulk/millesime=smoke/departement=63/sis.parquet')"
        )
    ]
    assert report.raw_size_bytes == 100
    assert report.stage_size_bytes == 512
    assert report.stage_row_count == 42
    assert "stage rows: 42" in caplog.text

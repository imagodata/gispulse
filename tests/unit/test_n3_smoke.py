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


class _FakeCadastreSource:
    name = "cadastre"

    def catalog(self) -> list[SourceEntryRef]:
        return [
            SourceEntryRef(
                id="parcelles_bulk",
                name="Parcelles bulk",
                access=AccessSpec(
                    protocol=AccessProtocol.DOWNLOAD,
                    endpoint=(
                        "https://cadastre.data.gouv.fr/data/etalab-cadastre/latest/"
                        "geojson/departements/{departement}/"
                        "cadastre-{departement}-parcelles.json.gz"
                    ),
                    params={"departement": "75", "layer": "parcelles"},
                    format="application/geo+json+gzip",
                ),
                payload=Payload.VECTOR,
                metadata={"base_key": "parcelles", "archive_format": "json.gz"},
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
        if entry == "parcelles_bulk":
            return _FakeBulkResult(
                manifest={
                    "raw_s3_uri": (
                        "s3://gispulse/smoke-n3/raw/cadastre/parcelles_bulk/"
                        "millesime=cadastre-smoke/departement=63/"
                        "cadastre-63-parcelles.json.gz"
                    ),
                    "stage_s3_uri": (
                        "s3://gispulse/smoke-n3/stage/cadastre/parcelles_bulk/"
                        "millesime=cadastre-smoke/departement=63/parcelles.parquet"
                    ),
                }
            )
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
        raw_suffixes = (".csv", ".json.gz")
        return {"ContentLength": 100 if Key.endswith(raw_suffixes) else 512}


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


def test_n3_cadastre_smoke_targets_parcelles_bulk_with_isolated_prefix(
    caplog,
) -> None:
    from scripts.n3_smoke_cadastre import N3CadastreSmokeConfig, run_smoke

    caplog.set_level(logging.INFO)
    _FakeRunner.instances.clear()
    _FakeDuckDBSession.instances.clear()
    s3 = _FakeS3Client()
    source = _FakeCadastreSource()
    cfg = N3CadastreSmokeConfig(
        bucket="gispulse",
        prefix="smoke-n3/",
        entry="parcelles_bulk",
        department="63",
        revision="cadastre-smoke",
    )

    report = run_smoke(
        cfg,
        source_loader=lambda: source,
        runner_factory=_FakeRunner,
        s3_client_factory=lambda: s3,
        duckdb_session_factory=_FakeDuckDBSession,
    )

    runner = _FakeRunner.instances[0]
    assert runner.bucket == "gispulse"
    assert runner.key_prefix == "smoke-n3/"
    assert runner.write_table_raw is True
    assert runner.calls == [(source, "parcelles_bulk", "63", "cadastre-smoke")]
    assert s3.heads == [
        (
            "gispulse",
            "smoke-n3/raw/cadastre/parcelles_bulk/millesime=cadastre-smoke/"
            "departement=63/cadastre-63-parcelles.json.gz",
        ),
        (
            "gispulse",
            "smoke-n3/stage/cadastre/parcelles_bulk/millesime=cadastre-smoke/"
            "departement=63/parcelles.parquet",
        ),
    ]
    assert _FakeDuckDBSession.instances[0].conn.sql == [
        (
            "SELECT count(*) FROM read_parquet('s3://gispulse/smoke-n3/stage/"
            "cadastre/parcelles_bulk/millesime=cadastre-smoke/departement=63/"
            "parcelles.parquet')"
        )
    ]
    assert report.raw_size_bytes == 100
    assert report.stage_size_bytes == 512
    assert report.stage_row_count == 42
    assert "cadastre N3 smoke" in caplog.text

"""Unit tests for the N3 declarative bulk ingest runner."""

from __future__ import annotations

import gzip
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest

from gispulse.core.plugin_model import (
    AccessProtocol,
    AccessSpec,
    FetchMode,
    Payload,
    SourceDomain,
    SourceResult,
)
from gispulse.core.sources import DeclarativeSource, ProtocolRegistry, SourceEntryRef

pytestmark = pytest.mark.usefixtures("offline_ssrf")


class _FakeTableFetcher:
    protocol = AccessProtocol.TABLE_FILE

    def __init__(self) -> None:
        self.calls: list[tuple[AccessSpec, FetchMode]] = []

    def fetch(
        self,
        access: AccessSpec,
        *,
        extent: object | None = None,
        mode: FetchMode = FetchMode.MATERIALIZE,
    ) -> SourceResult:
        self.calls.append((access, mode))
        local_path = Path(access.params["local_path"])
        local_path.write_bytes(b"code\n63000\n")
        return SourceResult(
            payload=Payload.TABLE,
            mode=mode,
            data=str(local_path),
            metadata={"local_path": str(local_path)},
        )


class _StaticSource(DeclarativeSource):
    name = "georisques"
    domain = SourceDomain.ENVIRONNEMENT
    payload = Payload.TABLE
    jurisdiction = "FR"

    def __init__(
        self,
        entry: SourceEntryRef,
        *,
        registry: ProtocolRegistry | None = None,
        revision: str | None = None,
        name: str = "georisques",
    ) -> None:
        super().__init__(registry=registry)
        self._entry_ref = entry
        self.name = name
        self._revision = revision

    def entries(self) -> list[SourceEntryRef]:
        return [self._entry_ref]

    def revision(self, entry_id: str) -> str | None:
        self._entry(entry_id)
        return self._revision


class _FakeStorage:
    def __init__(self) -> None:
        self.uploads: list[tuple[str, bytes, str]] = []

    async def upload(
        self,
        key: str,
        data: bytes | object,
        content_type: str = "",
    ) -> str:
        if isinstance(data, bytes):
            payload = data
        else:
            payload = data.read()
        self.uploads.append((key, payload, content_type))
        return key


def _zip_bytes(files: dict[str, bytes]) -> bytes:
    buf = BytesIO()
    with ZipFile(buf, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buf.getvalue()


def _patch_http_stream(monkeypatch: pytest.MonkeyPatch, payload: bytes) -> list[str]:
    requested: list[str] = []

    class _FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def iter_bytes(self):
            yield payload

        def __enter__(self) -> "_FakeResponse":
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    def _fake_stream(method: str, url: str, **_kw: object) -> _FakeResponse:
        requested.append(f"{method} {url}")
        return _FakeResponse()

    import httpx

    monkeypatch.setattr(httpx, "stream", _fake_stream)
    return requested


def _patch_duckdb(monkeypatch: pytest.MonkeyPatch) -> list[str]:
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
    return executed


def test_runner_materializes_table_file_entry_to_stage_s3_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gispulse.core.bulk_runner import BulkIngestRunner

    executed = _patch_duckdb(monkeypatch)
    fetcher = _FakeTableFetcher()
    registry = ProtocolRegistry()
    registry.register(fetcher)
    source = _StaticSource(
        SourceEntryRef(
            id="sis-bulk",
            name="SIS bulk",
            access=AccessSpec(
                protocol=AccessProtocol.TABLE_FILE,
                endpoint="https://host.example.org/{dataset}.csv",
                params={"dataset": "sis", "table_format": "csv"},
            ),
            payload=Payload.TABLE,
            metadata={"base_key": "sis", "data_format": "csv"},
        ),
        registry=registry,
    )

    result = BulkIngestRunner(registry=registry).run_entry(
        source,
        "sis-bulk",
        revision="sis-2026",
    )

    access, mode = fetcher.calls[0]
    assert mode is FetchMode.MATERIALIZE
    assert access.endpoint == "https://host.example.org/sis.csv"
    assert "s3_key" not in access.params
    assert "s3_bucket" not in access.params
    assert access.params["local_path"].endswith("/sis.csv")
    assert result.access.params["s3_key"] == (
        "stage/georisques/sis-bulk/millesime=sis-2026/national/sis.parquet"
    )
    assert len(executed) == 1
    assert "read_csv_auto('" in executed[0]
    assert "/vsicurl/" not in executed[0]
    assert result.manifest["raw_s3_uri"] == (
        "s3://gispulse/raw/georisques/sis-bulk/millesime=sis-2026/national/sis.csv"
    )
    assert result.manifest["stage_s3_uri"] == (
        "s3://gispulse/stage/georisques/sis-bulk/millesime=sis-2026/national/"
        "sis.parquet"
    )
    assert result.fetch_result.reference == result.manifest["stage_s3_uri"]


def test_runner_can_prefix_and_upload_raw_table_file_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gispulse.core.bulk_runner import BulkIngestRunner

    executed = _patch_duckdb(monkeypatch)
    fetcher = _FakeTableFetcher()
    registry = ProtocolRegistry()
    registry.register(fetcher)
    storage = _FakeStorage()
    source = _StaticSource(
        SourceEntryRef(
            id="sis-bulk",
            name="SIS bulk",
            access=AccessSpec(
                protocol=AccessProtocol.TABLE_FILE,
                endpoint="https://host.example.org/{dataset}.csv",
                params={"dataset": "sis", "table_format": "csv"},
                format="text/csv",
            ),
            payload=Payload.TABLE,
            metadata={"base_key": "sis", "data_format": "csv"},
        ),
        registry=registry,
    )

    result = BulkIngestRunner(
        registry=registry,
        storage=storage,
        bucket="gispulse",
        key_prefix="smoke-n3/",
        write_table_raw=True,
    ).run_entry(
        source,
        "sis-bulk",
        departement="63",
        revision="smoke",
    )

    access, _mode = fetcher.calls[0]
    assert access.endpoint == "https://host.example.org/sis.csv"
    assert "s3_key" not in access.params
    assert "s3_bucket" not in access.params
    assert result.access.params["s3_key"] == (
        "smoke-n3/stage/georisques/sis-bulk/millesime=smoke/"
        "departement=63/sis.parquet"
    )
    assert len(executed) == 1
    assert "read_csv_auto('" in executed[0]
    assert "/vsicurl/" not in executed[0]
    assert storage.uploads == [
        (
            "smoke-n3/raw/georisques/sis-bulk/millesime=smoke/"
            "departement=63/sis.csv",
            b"code\n63000\n",
            "text/csv",
        )
    ]
    assert result.manifest["raw_s3_uri"] == (
        "s3://gispulse/smoke-n3/raw/georisques/sis-bulk/millesime=smoke/"
        "departement=63/sis.csv"
    )
    assert result.manifest["stage_s3_uri"] == (
        "s3://gispulse/smoke-n3/stage/georisques/sis-bulk/millesime=smoke/"
        "departement=63/sis.parquet"
    )


def test_runner_copies_table_file_from_fetched_local_file_not_endpoint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from gispulse.core.bulk_runner import BulkIngestRunner

    executed = _patch_duckdb(monkeypatch)
    fetched_paths: list[Path] = []

    class _DownloadedCsvFetcher:
        protocol = AccessProtocol.TABLE_FILE

        def __init__(self) -> None:
            self.calls: list[tuple[AccessSpec, FetchMode]] = []

        def fetch(
            self,
            access: AccessSpec,
            *,
            extent: object | None = None,
            mode: FetchMode = FetchMode.MATERIALIZE,
        ) -> SourceResult:
            self.calls.append((access, mode))
            assert "s3_key" not in access.params
            assert "s3_bucket" not in access.params
            local_path = Path(access.params["local_path"])
            local_path.write_text("code\n63000\n", encoding="utf-8")
            fetched_paths.append(local_path)
            return SourceResult(
                payload=Payload.TABLE,
                mode=mode,
                data=str(local_path),
                metadata={"table_format": "csv"},
            )

    fetcher = _DownloadedCsvFetcher()
    registry = ProtocolRegistry()
    registry.register(fetcher)
    source = _StaticSource(
        SourceEntryRef(
            id="sis-bulk",
            name="SIS bulk",
            access=AccessSpec(
                protocol=AccessProtocol.TABLE_FILE,
                endpoint=(
                    "https://mapsref.brgm.fr/wxs/georisques/georisques_dl?"
                    "service=WFS&request=GetFeature&typeName=georisques:sis"
                    "&outputformat=CSVTEXT"
                ),
                params={"table_format": "csv"},
                format="text/csv",
            ),
            payload=Payload.TABLE,
            metadata={"base_key": "sis", "data_format": "csv"},
        ),
        registry=registry,
    )

    result = BulkIngestRunner(
        registry=registry,
        bucket="gispulse",
        temp_dir=tmp_path,
    ).run_entry(source, "sis-bulk", revision="smoke")

    assert len(fetcher.calls) == 1
    access, mode = fetcher.calls[0]
    assert mode is FetchMode.MATERIALIZE
    assert access.endpoint.startswith("https://mapsref.brgm.fr/wxs/georisques/")
    assert fetched_paths
    assert not fetched_paths[0].exists()
    assert len(executed) == 1
    assert f"read_csv_auto('{fetched_paths[0]}')" in executed[0]
    assert "/vsicurl/" not in executed[0]
    assert "mapsref.brgm.fr" not in executed[0]
    assert (
        "TO 's3://gispulse/stage/georisques/sis-bulk/millesime=smoke/"
        "national/sis.parquet' (FORMAT PARQUET)"
    ) in executed[0]
    assert result.fetch_result.reference == result.manifest["stage_s3_uri"]


def test_runner_injects_department_partition_and_resolves_endpoint_before_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gispulse.core.bulk_runner import BulkIngestRunner

    _patch_duckdb(monkeypatch)
    fetcher = _FakeTableFetcher()
    registry = ProtocolRegistry()
    registry.register(fetcher)
    source = _StaticSource(
        SourceEntryRef(
            id="partitioned-table",
            name="Partitioned table",
            access=AccessSpec(
                protocol=AccessProtocol.TABLE_FILE,
                endpoint="https://host.example.org/{departement}/{partition}.csv",
                params={"table_format": "csv"},
            ),
            payload=Payload.TABLE,
            metadata={"base_key": "partitioned"},
        ),
        registry=registry,
    )

    result = BulkIngestRunner(registry=registry).run_entry(
        source,
        "partitioned-table",
        departement="69",
        partition="DU_200046977",
        revision="rev-1",
    )

    access, _mode = fetcher.calls[0]
    assert access.endpoint == "https://host.example.org/69/DU_200046977.csv"
    assert access.params["departement"] == "69"
    assert access.params["partition"] == "DU_200046977"
    assert "s3_key" not in access.params
    assert "s3_bucket" not in access.params
    assert result.access.params["s3_key"] == (
        "stage/georisques/partitioned-table/millesime=rev-1/"
        "departement=69/partition=DU_200046977/partitioned.parquet"
    )


def test_download_archive_uploads_raw_zip_and_copies_shapefile_to_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gispulse.core.bulk_runner import BulkIngestRunner

    payload = _zip_bytes({"tri/tri_2020.shp": b"fake-shp"})
    _patch_http_stream(monkeypatch, payload)
    executed = _patch_duckdb(monkeypatch)
    storage = _FakeStorage()
    source = _StaticSource(
        SourceEntryRef(
            id="tri-bulk",
            name="TRI bulk",
            access=AccessSpec(
                protocol=AccessProtocol.DOWNLOAD,
                endpoint="https://host.example.org/tri_2020_sig_di_{departement}.zip",
                params={"departement": "69"},
                format="application/zip",
            ),
            payload=Payload.VECTOR,
            metadata={
                "base_key": "tri_2020",
                "archive_format": "zip",
                "data_format": "shapefile",
            },
        ),
        name="georisques",
    )

    result = BulkIngestRunner(storage=storage).run_entry(
        source,
        "tri-bulk",
        departement="69",
        revision="2020",
    )

    assert storage.uploads == [
        (
            "raw/georisques/tri-bulk/millesime=2020/departement=69/archive.zip",
            payload,
            "application/zip",
        )
    ]
    assert "ST_Read('" in executed[0]
    assert "tri_2020.shp')" in executed[0]
    assert (
        "TO 's3://gispulse/stage/georisques/tri-bulk/millesime=2020/"
        "departement=69/tri_2020.parquet' (FORMAT PARQUET)"
    ) in executed[0]
    assert result.manifest["raw_s3_uri"] == (
        "s3://gispulse/raw/georisques/tri-bulk/millesime=2020/departement=69/"
        "archive.zip"
    )
    assert result.manifest["stage_s3_uri"] == (
        "s3://gispulse/stage/georisques/tri-bulk/millesime=2020/departement=69/"
        "tri_2020.parquet"
    )
    assert result.manifest["status"] == "success"


def test_download_archive_writes_one_stage_parquet_per_vector_member(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gispulse.core.bulk_runner import BulkIngestRunner

    payload = _zip_bytes(
        {
            "CNIG/zone_urba.shp": b"fake-zone",
            "CNIG/doc_urba.shp": b"fake-doc",
        }
    )
    _patch_http_stream(monkeypatch, payload)
    executed = _patch_duckdb(monkeypatch)
    storage = _FakeStorage()
    source = _StaticSource(
        SourceEntryRef(
            id="gpu_documents_bulk_index",
            name="GPU bulk",
            access=AccessSpec(
                protocol=AccessProtocol.DOWNLOAD,
                endpoint="https://host.example.org/download-by-partition/{partition}",
                params={"partition": "DU_200046977", "departement": "69"},
                format="application/zip",
            ),
            payload=Payload.VECTOR,
            metadata={"archive_format": "zip", "data_format": "shapefile"},
        ),
        name="gpu",
    )

    result = BulkIngestRunner(storage=storage).run_entry(
        source,
        "gpu_documents_bulk_index",
        departement="69",
        partition="DU_200046977",
        revision="gpu-rev",
    )

    assert len(executed) == 2
    assert "zone_urba.shp')" in executed[0]
    assert "doc_urba.shp')" in executed[1]
    assert result.manifest["stage_s3_uris"] == [
        (
            "s3://gispulse/stage/gpu/gpu_documents_bulk_index/millesime=gpu-rev/"
            "departement=69/partition=DU_200046977/zone_urba.parquet"
        ),
        (
            "s3://gispulse/stage/gpu/gpu_documents_bulk_index/millesime=gpu-rev/"
            "departement=69/partition=DU_200046977/doc_urba.parquet"
        ),
    ]


def test_download_archive_derives_iris_zone_and_reads_gpkg_layer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from gispulse.core import bulk_runner
    from gispulse.core.bulk_runner import BulkIngestRunner

    requested = _patch_http_stream(monkeypatch, b"fake-7z")
    executed = _patch_duckdb(monkeypatch)
    storage = _FakeStorage()

    def _fake_extract_7z(archive_path: Path, dest_dir: Path) -> None:
        assert archive_path.read_bytes() == b"fake-7z"
        (dest_dir / "IRIS_GE.gpkg").write_bytes(b"fake-gpkg")

    monkeypatch.setattr(bulk_runner, "_extract_7z_archive", _fake_extract_7z)
    source = _StaticSource(
        SourceEntryRef(
            id="iris_bulk",
            name="IRIS bulk",
            access=AccessSpec(
                protocol=AccessProtocol.DOWNLOAD,
                endpoint=(
                    "https://host.example.org/IRIS-GE_3-0__GPKG_LAMB93_{zone}_"
                    "2026-01-01.7z"
                ),
                params={"zone": "D075", "layer": "iris_ge"},
                format="application/x-7z-compressed",
            ),
            payload=Payload.VECTOR,
            metadata={
                "base_key": "iris",
                "archive_format": "7z",
                "format": "GPKG",
                "millesime": "2026-01-01",
                "zone_format": "D{code_departement:0>3}",
            },
        ),
        name="insee",
    )

    result = BulkIngestRunner(storage=storage, temp_dir=tmp_path).run_entry(
        source,
        "iris_bulk",
        departement="75",
        revision="2026-01-01",
    )

    assert requested == [
        "GET https://host.example.org/IRIS-GE_3-0__GPKG_LAMB93_D075_2026-01-01.7z"
    ]
    assert storage.uploads[0][0] == (
        "raw/insee/iris_bulk/millesime=2026-01-01/departement=75/archive.7z"
    )
    assert "ST_Read('" in executed[0]
    assert "IRIS_GE.gpkg', layer='iris_ge')" in executed[0]
    assert result.manifest["stage_s3_uri"] == (
        "s3://gispulse/stage/insee/iris_bulk/millesime=2026-01-01/"
        "departement=75/iris.parquet"
    )


def test_download_spatial_json_gz_uploads_raw_and_copies_vsigzip_to_stage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from gispulse.core.bulk_runner import BulkIngestRunner

    geojson = (
        b'{"type":"FeatureCollection","features":[{"type":"Feature",'
        b'"properties":{"id":"630000000A0001"},"geometry":null}]}'
    )
    payload = gzip.compress(geojson)
    requested = _patch_http_stream(monkeypatch, payload)
    executed = _patch_duckdb(monkeypatch)
    storage = _FakeStorage()
    source = _StaticSource(
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
        ),
        name="cadastre",
    )

    result = BulkIngestRunner(
        storage=storage,
        temp_dir=tmp_path,
        key_prefix="smoke-n3/",
    ).run_entry(
        source,
        "parcelles_bulk",
        departement="63",
        revision="cadastre-smoke",
    )

    assert requested == [
        (
            "GET https://cadastre.data.gouv.fr/data/etalab-cadastre/latest/"
            "geojson/departements/63/cadastre-63-parcelles.json.gz"
        )
    ]
    assert storage.uploads == [
        (
            "smoke-n3/raw/cadastre/parcelles_bulk/millesime=cadastre-smoke/"
            "departement=63/cadastre-63-parcelles.json.gz",
            payload,
            "application/geo+json+gzip",
        )
    ]
    assert len(executed) == 1
    assert "ST_Read('/vsigzip/" in executed[0]
    assert "cadastre-63-parcelles.json.gz'" in executed[0]
    assert "layer='parcelles')" in executed[0]
    assert (
        "TO 's3://gispulse/smoke-n3/stage/cadastre/parcelles_bulk/"
        "millesime=cadastre-smoke/departement=63/parcelles.parquet' "
        "(FORMAT PARQUET)"
    ) in executed[0]
    assert result.manifest["raw_s3_uri"] == (
        "s3://gispulse/smoke-n3/raw/cadastre/parcelles_bulk/"
        "millesime=cadastre-smoke/departement=63/"
        "cadastre-63-parcelles.json.gz"
    )
    assert result.manifest["stage_s3_uri"] == (
        "s3://gispulse/smoke-n3/stage/cadastre/parcelles_bulk/"
        "millesime=cadastre-smoke/departement=63/parcelles.parquet"
    )
    assert result.fetch_result.metadata["stage_s3_uris"] == [
        result.manifest["stage_s3_uri"]
    ]

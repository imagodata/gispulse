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
    def __init__(
        self,
        *,
        existing_keys: set[str] | None = None,
        exists_raises: Exception | None = None,
    ) -> None:
        self.uploads: list[tuple[str, bytes, str]] = []
        self.exists_calls: list[str] = []
        self._existing_keys: set[str] = existing_keys or set()
        self._exists_raises = exists_raises

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

    async def exists(self, key: str) -> bool:
        self.exists_calls.append(key)
        if self._exists_raises is not None:
            raise self._exists_raises
        return key in self._existing_keys


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


def test_runner_rejects_zip_table_file_without_archive_member(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gispulse.core.bulk_runner import BulkIngestRunner

    _patch_duckdb(monkeypatch)
    fetcher = _FakeTableFetcher()
    registry = ProtocolRegistry()
    registry.register(fetcher)
    source = _StaticSource(
        SourceEntryRef(
            id="gaspar-bulk",
            name="GASPAR bulk",
            access=AccessSpec(
                protocol=AccessProtocol.TABLE_FILE,
                endpoint="https://host.example.org/gaspar.zip",
                params={"archive_format": "zip", "table_format": "csv"},
            ),
            payload=Payload.TABLE,
            metadata={"base_key": "gaspar", "data_format": "csv"},
        ),
        registry=registry,
    )

    with pytest.raises(
        ValueError,
        match="TABLE_FILE zip archives require access.params.archive_member",
    ):
        BulkIngestRunner(registry=registry).run_entry(
            source,
            "gaspar-bulk",
            revision="gaspar-2026",
        )


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


def test_download_spatial_json_gz_uploads_raw_and_streams_geojson_to_stage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from gispulse.core.bulk_runner import BulkIngestRunner

    geojson = (
        b'{"type":"FeatureCollection","features":['
        b'{"type":"Feature","properties":{"id":"630000000A0001",'
        b'"commune":"63000","contenance":12,"arpente":false},'
        b'"geometry":{"type":"Polygon","coordinates":[[[3.0,45.0],'
        b"[3.1,45.0],[3.1,45.1],[3.0,45.1],[3.0,45.0]]]}},"
        b'{"type":"Feature","properties":{"id":"630000000A0002",'
        b'"commune":"63000","contenance":24,"arpente":true},'
        b'"geometry":{"type":"Polygon","coordinates":[[[3.2,45.2],'
        b"[3.3,45.2],[3.3,45.3],[3.2,45.3],[3.2,45.2]]]}}]}"
    )
    payload = gzip.compress(geojson)
    requested = _patch_http_stream(monkeypatch, payload)

    class _NoDuckDBSession:
        def __enter__(self) -> "_NoDuckDBSession":
            raise AssertionError("json.gz cadastre path must not call DuckDB ST_Read")

        def __exit__(self, *exc: object) -> None:
            return None

    import gispulse.persistence.duckdb_engine as duckdb_engine

    monkeypatch.setattr(duckdb_engine, "DuckDBSession", _NoDuckDBSession)
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
    source.schema = lambda entry_id: {  # type: ignore[method-assign]
        "id": "str",
        "geometry": "geometry",
        "commune": "str",
        "prefixe": "str",
        "section": "str",
        "numero": "str",
        "contenance": "int",
        "arpente": "bool",
        "created": "date",
        "updated": "date",
    }

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
    assert storage.uploads[0] == (
        "smoke-n3/raw/cadastre/parcelles_bulk/millesime=cadastre-smoke/"
        "departement=63/cadastre-63-parcelles.json.gz",
        payload,
        "application/geo+json+gzip",
    )
    assert storage.uploads[1][0] == (
        "smoke-n3/stage/cadastre/parcelles_bulk/millesime=cadastre-smoke/"
        "departement=63/parcelles.parquet"
    )
    assert storage.uploads[1][2] == "application/vnd.apache.parquet"
    stage_path = tmp_path / "stage.parquet"
    stage_path.write_bytes(storage.uploads[1][1])
    import duckdb

    rows = (
        duckdb.connect(":memory:")
        .execute(
            "SELECT id, commune, contenance, arpente, geometry FROM read_parquet(?) ORDER BY id",
            [str(stage_path)],
        )
        .fetchall()
    )
    assert rows == [
        ("630000000A0001", "63000", 12, False, rows[0][4]),
        ("630000000A0002", "63000", 24, True, rows[1][4]),
    ]
    assert all(isinstance(row[4], bytes) for row in rows)
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
    assert result.fetch_result.metadata["stage_rows"] == 2


# ---------------------------------------------------------------------------
# skip-if-staged feature
# ---------------------------------------------------------------------------


def _make_table_source(
    registry: ProtocolRegistry,
    *,
    entry_id: str = "sis-bulk",
    name: str = "georisques",
) -> "_StaticSource":
    return _StaticSource(
        SourceEntryRef(
            id=entry_id,
            name="SIS bulk",
            access=AccessSpec(
                protocol=AccessProtocol.TABLE_FILE,
                endpoint="https://host.example.org/sis.csv",
                params={"table_format": "csv"},
            ),
            payload=Payload.TABLE,
            metadata={"base_key": "sis", "data_format": "csv"},
        ),
        registry=registry,
        name=name,
    )


def _make_json_gz_source(name: str = "cadastre") -> "_StaticSource":
    return _StaticSource(
        SourceEntryRef(
            id="parcelles_bulk",
            name="Parcelles bulk",
            access=AccessSpec(
                protocol=AccessProtocol.DOWNLOAD,
                endpoint=(
                    "https://cadastre.data.gouv.fr/data/etalab-cadastre/latest/"
                    "geojson/departements/63/cadastre-63-parcelles.json.gz"
                ),
                params={"departement": "63"},
                format="application/geo+json+gzip",
            ),
            payload=Payload.VECTOR,
            metadata={"base_key": "parcelles", "archive_format": "json.gz"},
        ),
        name=name,
    )


# (a) Default (skip_if_staged=False) — no exists call, behaviour unchanged.
def test_skip_if_staged_default_does_not_call_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gispulse.core.bulk_runner import BulkIngestRunner

    _patch_duckdb(monkeypatch)
    fetcher = _FakeTableFetcher()
    registry = ProtocolRegistry()
    registry.register(fetcher)
    storage = _FakeStorage()
    source = _make_table_source(registry)

    result = BulkIngestRunner(
        registry=registry,
        storage=storage,
        bucket="gispulse",
        # skip_if_staged NOT set — default False
    ).run_entry(source, "sis-bulk", revision="rev1")

    assert not result.skipped
    assert storage.exists_calls == []  # exists must not be consulted
    assert len(fetcher.calls) == 1  # nominal path executed


# (b) skip_if_staged=True + object present → skipped=True, no fetch/upload.
def test_skip_if_staged_present_skips_fetch_and_upload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gispulse.core.bulk_runner import BulkIngestRunner

    executed = _patch_duckdb(monkeypatch)
    fetcher = _FakeTableFetcher()
    registry = ProtocolRegistry()
    registry.register(fetcher)
    stage_key = "stage/georisques/sis-bulk/millesime=rev1/national/sis.parquet"
    storage = _FakeStorage(existing_keys={stage_key})
    source = _make_table_source(registry)

    result = BulkIngestRunner(
        registry=registry,
        storage=storage,
        bucket="gispulse",
        skip_if_staged=True,
    ).run_entry(source, "sis-bulk", revision="rev1")

    assert result.skipped is True
    assert result.manifest["skipped"] is True
    assert result.manifest["reason"] == "stage_exists"
    assert result.manifest["status"] == "skipped"
    assert result.fetch_result.reference == f"s3://gispulse/{stage_key}"
    assert fetcher.calls == []  # no fetch
    assert storage.uploads == []  # no upload
    assert executed == []  # no DuckDB
    assert storage.exists_calls == [stage_key]


# (c) skip_if_staged=True + object absent → nominal path executed.
def test_skip_if_staged_absent_runs_nominal_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gispulse.core.bulk_runner import BulkIngestRunner

    executed = _patch_duckdb(monkeypatch)
    fetcher = _FakeTableFetcher()
    registry = ProtocolRegistry()
    registry.register(fetcher)
    storage = _FakeStorage(existing_keys=set())  # nothing staged
    source = _make_table_source(registry)

    result = BulkIngestRunner(
        registry=registry,
        storage=storage,
        bucket="gispulse",
        skip_if_staged=True,
    ).run_entry(source, "sis-bulk", revision="rev1")

    assert result.skipped is False
    assert len(fetcher.calls) == 1
    assert len(executed) == 1  # DuckDB COPY ran


# (d) force=True bypasses skip even when stage exists.
def test_skip_if_staged_force_overrides_skip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gispulse.core.bulk_runner import BulkIngestRunner

    executed = _patch_duckdb(monkeypatch)
    fetcher = _FakeTableFetcher()
    registry = ProtocolRegistry()
    registry.register(fetcher)
    stage_key = "stage/georisques/sis-bulk/millesime=rev1/national/sis.parquet"
    storage = _FakeStorage(existing_keys={stage_key})
    source = _make_table_source(registry)

    result = BulkIngestRunner(
        registry=registry,
        storage=storage,
        bucket="gispulse",
        skip_if_staged=True,
    ).run_entry(source, "sis-bulk", revision="rev1", force=True)

    assert result.skipped is False
    assert len(fetcher.calls) == 1
    assert len(executed) == 1
    assert storage.exists_calls == []  # exists not consulted when force=True


# (e) exists raises → warning logged, nominal path executed.
def test_skip_if_staged_exists_raises_falls_back_to_nominal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gispulse.core.bulk_runner import BulkIngestRunner

    executed = _patch_duckdb(monkeypatch)
    fetcher = _FakeTableFetcher()
    registry = ProtocolRegistry()
    registry.register(fetcher)
    storage = _FakeStorage(exists_raises=OSError("network error"))
    source = _make_table_source(registry)

    # Should not raise — warning is swallowed, nominal path runs.
    result = BulkIngestRunner(
        registry=registry,
        storage=storage,
        bucket="gispulse",
        skip_if_staged=True,
    ).run_entry(source, "sis-bulk", revision="rev1")

    assert result.skipped is False
    assert len(fetcher.calls) == 1  # nominal ran
    assert len(executed) == 1


# (f) Spatial gzip archive: skip works when stage exists.
def test_skip_if_staged_json_gz_present_skips_download(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pytest.FixtureDef,
) -> None:
    from gispulse.core.bulk_runner import BulkIngestRunner

    requested = _patch_http_stream(monkeypatch, b"would-not-be-read")
    stage_key = (
        "stage/cadastre/parcelles_bulk/millesime=smoke/departement=63/parcelles.parquet"
    )
    storage = _FakeStorage(existing_keys={stage_key})
    source = _make_json_gz_source()

    result = BulkIngestRunner(
        storage=storage,
        bucket="gispulse",
        skip_if_staged=True,
    ).run_entry(source, "parcelles_bulk", departement="63", revision="smoke")

    assert result.skipped is True
    assert result.manifest["skipped"] is True
    assert result.manifest["reason"] == "stage_exists"
    assert result.fetch_result.reference == f"s3://gispulse/{stage_key}"
    assert requested == []  # no HTTP download
    assert storage.uploads == []  # no upload
    assert storage.exists_calls == [stage_key]


# (f-2) ZIP archive: no skip even when stage key would match (content-determined keys).
def test_skip_if_staged_zip_archive_never_skips(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gispulse.core.bulk_runner import BulkIngestRunner

    payload = _zip_bytes({"tri/tri_2020.shp": b"fake-shp"})
    _patch_http_stream(monkeypatch, payload)
    _patch_duckdb(monkeypatch)
    # Pre-populate *any* key to prove skip is not attempted for ZIP archives.
    storage = _FakeStorage(existing_keys={"stage/georisques/tri-bulk/millesime=2020/departement=69/tri_2020.parquet"})
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

    result = BulkIngestRunner(
        storage=storage,
        bucket="gispulse",
        skip_if_staged=True,
    ).run_entry(source, "tri-bulk", departement="69", revision="2020")

    # ZIP archive must always be processed — exists never called.
    assert result.skipped is False
    assert storage.exists_calls == []
    assert len(storage.uploads) >= 1  # raw upload happened


# (P2a) _create_s3_storage raises when self._storage is None → warning + nominal.
def test_skip_if_staged_create_storage_raises_falls_back_to_nominal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gispulse.core import bulk_runner
    from gispulse.core.bulk_runner import BulkIngestRunner

    executed = _patch_duckdb(monkeypatch)
    fetcher = _FakeTableFetcher()
    registry = ProtocolRegistry()
    registry.register(fetcher)

    # _create_s3_storage raises — simulates missing S3 config.
    monkeypatch.setattr(
        bulk_runner,
        "_create_s3_storage",
        lambda bucket: (_ for _ in ()).throw(RuntimeError("no S3 endpoint")),
    )

    source = _make_table_source(registry)

    # No storage= passed → runner would call _create_s3_storage for the check.
    # It must catch the exception, warn, and proceed with the nominal path.
    # But the nominal path also needs storage for write_table_raw — since
    # write_table_raw=False (default), the nominal path only uses DuckDB COPY
    # and does NOT call _create_s3_storage again.  So we expect success.
    result = BulkIngestRunner(
        registry=registry,
        # storage=None intentionally — forces _create_s3_storage in skip check
        bucket="gispulse",
        skip_if_staged=True,
    ).run_entry(source, "sis-bulk", revision="rev1")

    assert result.skipped is False
    assert len(fetcher.calls) == 1  # nominal ran
    assert len(executed) == 1


# (P3a) skip with partition= — stage key checked includes partition segment.
def test_skip_if_staged_partition_key_includes_partition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gispulse.core.bulk_runner import BulkIngestRunner

    _patch_duckdb(monkeypatch)
    fetcher = _FakeTableFetcher()
    registry = ProtocolRegistry()
    registry.register(fetcher)
    # Key with partition segment as produced by bulk_s3_key.
    stage_key = (
        "stage/georisques/sis-bulk/millesime=rev1/"
        "departement=63/partition=DU_200046977/sis.parquet"
    )
    storage = _FakeStorage(existing_keys={stage_key})
    source = _StaticSource(
        SourceEntryRef(
            id="sis-bulk",
            name="SIS bulk",
            access=AccessSpec(
                protocol=AccessProtocol.TABLE_FILE,
                endpoint="https://host.example.org/sis.csv",
                params={"table_format": "csv"},
            ),
            payload=Payload.TABLE,
            metadata={"base_key": "sis", "data_format": "csv"},
        ),
        registry=registry,
        name="georisques",
    )

    result = BulkIngestRunner(
        registry=registry,
        storage=storage,
        bucket="gispulse",
        skip_if_staged=True,
    ).run_entry(
        source,
        "sis-bulk",
        departement="63",
        partition="DU_200046977",
        revision="rev1",
    )

    assert result.skipped is True
    # The key consulted must carry the partition segment.
    assert storage.exists_calls == [stage_key]
    assert fetcher.calls == []


# (P3b) skipped result carries the same manifest fields as nominal for consumers
# (e.g. scripts reading manifest["stage_s3_uri"]).
def test_skip_if_staged_result_manifest_compatible_with_nominal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gispulse.core.bulk_runner import BulkIngestRunner

    executed = _patch_duckdb(monkeypatch)
    fetcher = _FakeTableFetcher()
    registry = ProtocolRegistry()
    registry.register(fetcher)

    stage_key = "stage/georisques/sis-bulk/millesime=rev1/national/sis.parquet"
    raw_key = "raw/georisques/sis-bulk/millesime=rev1/national/sis.csv"
    storage_skip = _FakeStorage(existing_keys={stage_key})
    storage_nominal = _FakeStorage(existing_keys=set())
    source_skip = _make_table_source(registry)
    source_nominal = _make_table_source(registry)

    skipped_result = BulkIngestRunner(
        registry=registry,
        storage=storage_skip,
        bucket="gispulse",
        skip_if_staged=True,
    ).run_entry(source_skip, "sis-bulk", revision="rev1")

    # Re-register fetcher for the nominal run (calls list reused).
    nominal_result = BulkIngestRunner(
        registry=registry,
        storage=storage_nominal,
        bucket="gispulse",
        skip_if_staged=True,
    ).run_entry(source_nominal, "sis-bulk", revision="rev1")

    # Both manifests must expose the same essential keys for downstream callers.
    for key in ("stage_s3_uri", "raw_s3_uri", "source", "entry", "revision", "status"):
        assert key in skipped_result.manifest, f"skipped manifest missing {key!r}"
        assert key in nominal_result.manifest, f"nominal manifest missing {key!r}"

    # Values must be consistent (same URIs).
    assert skipped_result.manifest["stage_s3_uri"] == f"s3://gispulse/{stage_key}"
    assert skipped_result.manifest["raw_s3_uri"] == f"s3://gispulse/{raw_key}"
    assert skipped_result.manifest["status"] == "skipped"
    assert nominal_result.manifest["status"] == "success"

    # fetch_result.reference points to stage URI in both cases.
    assert skipped_result.fetch_result.reference == skipped_result.manifest["stage_s3_uri"]
    assert nominal_result.fetch_result.reference == nominal_result.manifest["stage_s3_uri"]

    # Skipped result has no DuckDB/fetch side effects.
    assert fetcher.calls  # nominal fetched
    assert executed  # nominal ran DuckDB

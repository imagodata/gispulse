"""Unit tests for the `gispulse source` CLI group."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from gispulse.cli import app
from gispulse.core.plugin_model import (
    AccessProtocol,
    AccessSpec,
    FetchMode,
    Payload,
    PluginKind,
    PluginRecord,
    PluginState,
    SourceResult,
    SourceDomain,
)
from gispulse.core.sources import DeclarativeSource, ProtocolRegistry, SOURCES, SourceEntryRef

runner = CliRunner()
pytestmark = pytest.mark.usefixtures("offline_ssrf")


class _DynamicSource(DeclarativeSource):
    name = "ax-source"
    domain = SourceDomain.FONCIER
    payload = Payload.VECTOR
    jurisdiction = "FR"

    def __init__(self, *, registry: ProtocolRegistry | None = None) -> None:
        super().__init__(registry=registry)

    def entries(self) -> list[SourceEntryRef]:
        return [
            SourceEntryRef(
                id="parcelles",
                name="Parcelles",
                access=AccessSpec(
                    protocol=AccessProtocol.REST_API,
                    endpoint="https://data.example.org/{departement}.geojson",
                    params={"departement": "75"},
                    format="application/geo+json",
                ),
                revision_token="2026-01",
                payload=Payload.VECTOR,
                domain=SourceDomain.FONCIER,
                jurisdiction="FR",
                metadata={"base_key": "parcelles"},
            ),
            SourceEntryRef(
                id="metadata",
                name="Metadata table",
                access=AccessSpec(
                    protocol=AccessProtocol.TABLE_FILE,
                    endpoint="https://data.example.org/metadata.csv",
                    params={"table_format": "csv"},
                    format="text/csv",
                ),
                payload=Payload.TABLE,
                domain=SourceDomain.STATISTIQUE,
                jurisdiction="FR",
            ),
        ]


class _RivalSource(DeclarativeSource):
    name = "ax-source"
    domain = SourceDomain.FONCIER
    payload = Payload.VECTOR
    jurisdiction = "FR"

    def __init__(self, *, registry: ProtocolRegistry | None = None) -> None:
        super().__init__(registry=registry)

    def entries(self) -> list[SourceEntryRef]:
        return [
            SourceEntryRef(
                id="rivals",
                name="Rival Parcelles",
                access=AccessSpec(
                    protocol=AccessProtocol.REST_API,
                    endpoint="https://rival.example.org/{departement}.geojson",
                    params={"departement": "75"},
                    format="application/geo+json",
                ),
                revision_token="2026-02",
                payload=Payload.VECTOR,
                domain=SourceDomain.FONCIER,
                jurisdiction="FR",
                metadata={"base_key": "rivals"},
            )
        ]


@dataclass
class _FakeHub:
    records: list[PluginRecord]

    def records_by_kind(self, kind: PluginKind) -> list[PluginRecord]:
        assert kind is PluginKind.SOURCE
        return self.records


class _JsonFetcher:
    protocol = AccessProtocol.REST_API

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
        return SourceResult(
            payload=Payload.VECTOR,
            mode=mode,
            data=b'{"type":"FeatureCollection","features":[]}\n',
            metadata={"row_count": 0},
        )


@pytest.fixture(autouse=True)
def _reset_sources() -> None:
    SOURCES.clear()
    yield
    SOURCES.clear()


def _patch_source_hub(
    monkeypatch: pytest.MonkeyPatch,
    *,
    registry: ProtocolRegistry | None = None,
) -> list[str]:
    calls: list[str] = []
    rec = _source_record(
        name="gispulse-src-ax",
        factory=lambda: _DynamicSource(registry=registry),
        calls=calls,
        call_label="register",
    )
    hub = _FakeHub([rec])

    from gispulse.core.plugin_hub import ExtensionHub

    monkeypatch.setattr(ExtensionHub, "get", classmethod(lambda cls: hub))
    return calls


def _patch_colliding_source_hub(
    monkeypatch: pytest.MonkeyPatch,
    *,
    registry: ProtocolRegistry | None = None,
) -> list[str]:
    calls: list[str] = []
    hub = _FakeHub(
        [
            _source_record(
                name="gispulse-src-ax",
                factory=lambda: _DynamicSource(registry=registry),
                calls=calls,
            ),
            _source_record(
                name="gispulse-src-rival",
                factory=lambda: _RivalSource(registry=registry),
                calls=calls,
            ),
        ]
    )

    from gispulse.core.plugin_hub import ExtensionHub

    monkeypatch.setattr(ExtensionHub, "get", classmethod(lambda cls: hub))
    return calls


def _source_record(
    *,
    name: str,
    factory: Callable[[], DeclarativeSource],
    calls: list[str],
    call_label: str | None = None,
) -> PluginRecord:
    def register() -> None:
        calls.append(call_label or name)
        SOURCES.register(factory())

    return PluginRecord(
        name=name,
        kind=PluginKind.SOURCE,
        state=PluginState.ACTIVE,
        obj=register,
    )


def test_source_catalog_bootstraps_dynamic_source_plugins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _patch_source_hub(monkeypatch)

    result = runner.invoke(app, ["source", "catalog", "--json"])

    assert result.exit_code == 0, result.output
    assert calls == ["register"]
    payload = json.loads(result.output.strip().splitlines()[-1])
    assert payload["warnings"] == []
    assert payload["plugins"] == [
        {
            "name": "gispulse-src-ax",
            "module": "gispulse-src-ax",
            "status": "ok",
            "sources": [
                {
                    "name": "ax-source",
                    "plugin": "gispulse-src-ax",
                    "entries": [
                        {
                            "id": "metadata",
                            "name": "Metadata table",
                            "protocol": "table-file",
                            "revision": None,
                            "payload": "table",
                            "domain": "statistique",
                            "jurisdiction": "FR",
                            "format": "text/csv",
                            "metadata": {},
                        },
                        {
                            "id": "parcelles",
                            "name": "Parcelles",
                            "protocol": "rest-api",
                            "revision": "2026-01",
                            "payload": "vector",
                            "domain": "foncier",
                            "jurisdiction": "FR",
                            "format": "application/geo+json",
                            "metadata": {"base_key": "parcelles"},
                        },
                    ],
                }
            ],
        }
    ]


def test_source_catalog_filters_by_entry_and_protocol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_source_hub(monkeypatch)

    result = runner.invoke(
        app,
        [
            "source",
            "catalog",
            "--source",
            "ax-source",
            "--entry",
            "parcelles",
            "--protocol",
            "rest-api",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip().splitlines()[-1])
    sources = payload["plugins"][0]["sources"]
    assert [source["name"] for source in sources] == ["ax-source"]
    assert [entry["id"] for entry in sources[0]["entries"]] == ["parcelles"]


def test_source_catalog_warns_and_shows_colliding_source_plugins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_colliding_source_hub(monkeypatch)

    result = runner.invoke(app, ["source", "catalog", "--json"])

    assert result.exit_code == 0, result.output
    assert "Warning: source 'ax-source' is provided by multiple plugins" in result.output
    payload = json.loads(result.output.strip().splitlines()[-1])
    assert payload["collisions"] == [
        {"source": "ax-source", "plugins": ["gispulse-src-ax", "gispulse-src-rival"]}
    ]
    plugin_sources = {
        plugin["name"]: plugin["sources"][0]["entries"][0]["id"]
        for plugin in payload["plugins"]
    }
    assert plugin_sources == {
        "gispulse-src-ax": "metadata",
        "gispulse-src-rival": "rivals",
    }


def test_source_ingest_writes_local_artifacts_and_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fetcher = _JsonFetcher()
    registry = ProtocolRegistry()
    registry.register(fetcher)
    _patch_source_hub(monkeypatch, registry=registry)
    monkeypatch.setenv("GISPULSE_DATA_DIR", str(tmp_path))

    result = runner.invoke(
        app,
        [
            "source",
            "ingest",
            "ax-source",
            "parcelles",
            "--scope",
            "departement=63",
            "--revision",
            "rev-1",
            "--dest",
            "local",
            "--prefix",
            "source-test",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    access, mode = fetcher.calls[0]
    assert mode is FetchMode.MATERIALIZE
    assert access.endpoint == "https://data.example.org/63.geojson"
    assert access.params["departement"] == "63"
    payload = json.loads(result.output.strip().splitlines()[-1])
    assert payload["status"] == "success"
    assert payload["plugin"] == "gispulse-src-ax"
    assert payload["source"] == "ax-source"
    assert payload["entry"] == "parcelles"
    assert payload["scope"] == "departement=63"
    assert payload["scope_params"] == {"departement": "63"}
    assert payload["revision"] == "rev-1"
    assert payload["row_count"] == 0
    assert payload["raw_key"] == (
        "source-test/raw/gispulse-src-ax/ax-source/parcelles/millesime=rev-1/"
        "departement=63/parcelles.geojson"
    )
    assert payload["stage_key"] == (
        "source-test/stage/gispulse-src-ax/ax-source/parcelles/millesime=rev-1/"
        "departement=63/parcelles.geojson"
    )
    assert payload["manifest_key"] == (
        "source-test/manifest/source-artifacts/gispulse-src-ax/ax-source/parcelles/"
        "millesime=rev-1/departement=63/manifest.json"
    )
    assert (tmp_path / payload["raw_key"]).read_bytes() == (
        b'{"type":"FeatureCollection","features":[]}\n'
    )
    assert (tmp_path / payload["stage_key"]).read_bytes() == (
        b'{"type":"FeatureCollection","features":[]}\n'
    )
    persisted = json.loads((tmp_path / payload["manifest_key"]).read_text())
    assert persisted == payload


def test_source_ingest_plugin_qualified_source_resolves_collision(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fetcher = _JsonFetcher()
    registry = ProtocolRegistry()
    registry.register(fetcher)
    _patch_colliding_source_hub(monkeypatch, registry=registry)
    monkeypatch.setenv("GISPULSE_DATA_DIR", str(tmp_path))

    result = runner.invoke(
        app,
        [
            "source",
            "ingest",
            "gispulse-src-ax:ax-source",
            "parcelles",
            "--revision",
            "rev-1",
            "--dest",
            "local",
            "--prefix",
            "source-test",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip().splitlines()[-1])
    assert payload["plugin"] == "gispulse-src-ax"
    assert payload["source"] == "ax-source"
    assert payload["entry"] == "parcelles"
    assert payload["raw_key"].startswith("source-test/raw/gispulse-src-ax/ax-source/")


def test_source_ingest_ambiguous_source_without_plugin_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    registry = ProtocolRegistry()
    registry.register(_JsonFetcher())
    _patch_colliding_source_hub(monkeypatch, registry=registry)
    monkeypatch.setenv("GISPULSE_DATA_DIR", str(tmp_path))

    result = runner.invoke(
        app,
        [
            "source",
            "ingest",
            "ax-source",
            "parcelles",
            "--dest",
            "local",
            "--json",
        ],
    )

    assert result.exit_code == 1
    assert "ambiguous source 'ax-source'" in result.output
    assert "gispulse-src-ax:ax-source" in result.output
    assert "gispulse-src-rival:ax-source" in result.output


def test_source_ingest_s3_without_endpoint_returns_structured_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetcher = _JsonFetcher()
    registry = ProtocolRegistry()
    registry.register(fetcher)
    _patch_source_hub(monkeypatch, registry=registry)
    monkeypatch.delenv("GISPULSE_S3_ENDPOINT", raising=False)
    monkeypatch.delenv("GISPULSE_S3_ACCESS_KEY", raising=False)
    monkeypatch.delenv("GISPULSE_S3_SECRET_KEY", raising=False)

    result = runner.invoke(
        app,
        [
            "source",
            "ingest",
            "ax-source",
            "parcelles",
            "--dest",
            "s3",
            "--json",
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["status"] == "error"
    assert payload["code"] == "STORAGE_S3_NOT_CONFIGURED"
    assert "GISPULSE_S3_ENDPOINT" in payload["recovery"]


def test_source_ingest_uses_bulk_runner_for_s3_table_entries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    registry = ProtocolRegistry()
    registry.register(_JsonFetcher())
    _patch_source_hub(monkeypatch, registry=registry)

    from gispulse.persistence.storage import LocalStorage

    import gispulse.cli_source as cli_source

    monkeypatch.setattr(
        cli_source,
        "_storage_for_dest",
        lambda dest: LocalStorage(base_path=tmp_path),
    )
    calls: list[dict[str, object]] = []

    class _FakeBulkRunner:
        def __init__(self, **kwargs: object) -> None:
            calls.append({"init": kwargs})

        def run_entry(self, source: object, entry_id: str, **kwargs: object) -> object:
            calls.append({"entry_id": entry_id, "kwargs": kwargs})
            return SimpleNamespace(
                manifest={
                    "source": "ax-source",
                    "entry": entry_id,
                    "scope": "national",
                    "revision": "rev-bulk",
                    "raw_s3_uri": (
                        "s3://gispulse/source-test/raw/ax-source/metadata/"
                        "millesime=rev-bulk/national/metadata.csv"
                    ),
                    "stage_s3_uri": (
                        "s3://gispulse/source-test/stage/ax-source/metadata/"
                        "millesime=rev-bulk/national/metadata.parquet"
                    ),
                    "row_count": 4,
                    "status": "success",
                },
            )

    import gispulse.core.bulk_runner as bulk_runner

    monkeypatch.setattr(bulk_runner, "BulkIngestRunner", _FakeBulkRunner)

    result = runner.invoke(
        app,
        [
            "source",
            "ingest",
            "ax-source",
            "metadata",
            "--revision",
            "rev-bulk",
            "--dest",
            "s3",
            "--prefix",
            "source-test",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls[0]["init"]["key_prefix"] == "source-test"
    assert calls[1]["entry_id"] == "metadata"
    assert calls[1]["kwargs"]["revision"] == "rev-bulk"
    payload = json.loads(result.output.strip().splitlines()[-1])
    assert payload["plugin"] == "gispulse-src-ax"
    assert payload["raw_key"] == (
        "source-test/raw/ax-source/metadata/millesime=rev-bulk/national/metadata.csv"
    )
    assert payload["stage_key"] == (
        "source-test/stage/ax-source/metadata/millesime=rev-bulk/national/metadata.parquet"
    )
    assert payload["row_count"] == 4
    assert payload["manifest_key"] == (
        "source-test/manifest/source-artifacts/gispulse-src-ax/ax-source/metadata/"
        "millesime=rev-bulk/national/manifest.json"
    )
    assert (tmp_path / payload["manifest_key"]).exists()


def _write_manifest_fixture(tmp_path: Path) -> dict[str, object]:
    record: dict[str, object] = {
        "plugin": "gispulse-src-ax",
        "source": "ax-source",
        "entry": "parcelles",
        "scope": "departement=63",
        "scope_params": {"departement": "63"},
        "revision": "rev-1",
        "dest": "local",
        "raw_uri": (
            "local://source-test/raw/gispulse-src-ax/ax-source/parcelles/millesime=rev-1/"
            "departement=63/parcelles.geojson"
        ),
        "stage_uri": (
            "local://source-test/stage/gispulse-src-ax/ax-source/parcelles/millesime=rev-1/"
            "departement=63/parcelles.geojson"
        ),
        "raw_key": (
            "source-test/raw/gispulse-src-ax/ax-source/parcelles/millesime=rev-1/"
            "departement=63/parcelles.geojson"
        ),
        "stage_key": (
            "source-test/stage/gispulse-src-ax/ax-source/parcelles/millesime=rev-1/"
            "departement=63/parcelles.geojson"
        ),
        "row_count": 0,
        "status": "success",
        "manifest_key": (
            "source-test/manifest/source-artifacts/gispulse-src-ax/ax-source/parcelles/"
            "millesime=rev-1/departement=63/manifest.json"
        ),
    }
    for key in (record["raw_key"], record["stage_key"]):
        path = tmp_path / str(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"artifact")
    manifest_path = tmp_path / str(record["manifest_key"])
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(record, sort_keys=True), encoding="utf-8")
    return record


def test_source_list_reads_manifest_and_checks_artifact_existence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    record = _write_manifest_fixture(tmp_path)
    monkeypatch.setenv("GISPULSE_DATA_DIR", str(tmp_path))

    result = runner.invoke(
        app,
        [
            "source",
            "list",
            "--prefix",
            "source-test",
            "--source",
            "ax-source",
            "--entry",
            "parcelles",
            "--scope",
            "departement=63",
            "--kind",
            "stage",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip().splitlines()[-1])
    assert payload["count"] == 1
    item = payload["artifacts"][0]
    assert item["plugin"] == record["plugin"]
    assert item["source"] == record["source"]
    assert item["entry"] == record["entry"]
    assert item["kind"] == "stage"
    assert item["exists"] is True
    assert item["selected_key"] == record["stage_key"]


def test_source_delete_is_dry_run_by_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    record = _write_manifest_fixture(tmp_path)
    monkeypatch.setenv("GISPULSE_DATA_DIR", str(tmp_path))

    result = runner.invoke(
        app,
        [
            "source",
            "delete",
            "--prefix",
            "source-test",
            "--source",
            "ax-source",
            "--entry",
            "parcelles",
            "--scope",
            "departement=63",
            "--kind",
            "stage",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip().splitlines()[-1])
    assert payload["dry_run"] is True
    assert payload["deleted"] == []
    assert payload["matched"][0]["selected_key"] == record["stage_key"]
    assert (tmp_path / str(record["stage_key"])).exists()
    assert (tmp_path / str(record["manifest_key"])).exists()


def test_source_delete_yes_removes_artifact_and_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    record = _write_manifest_fixture(tmp_path)
    monkeypatch.setenv("GISPULSE_DATA_DIR", str(tmp_path))

    result = runner.invoke(
        app,
        [
            "source",
            "delete",
            "--prefix",
            "source-test",
            "--source",
            "ax-source",
            "--entry",
            "parcelles",
            "--scope",
            "departement=63",
            "--kind",
            "stage",
            "--yes",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip().splitlines()[-1])
    assert payload["dry_run"] is False
    assert payload["deleted"] == [record["stage_key"]]
    assert payload["deleted_manifests"] == [record["manifest_key"]]
    assert not (tmp_path / str(record["stage_key"])).exists()
    assert not (tmp_path / str(record["manifest_key"])).exists()

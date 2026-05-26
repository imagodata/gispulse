"""Unit tests for the gispulse-src-georisques plugin (#196).

Zero-network: the plugin only *declares* API AccessSpecs against REST_TABLE,
bulk AccessSpecs against DOWNLOAD / TABLE_FILE, and builds per-query API
AccessSpecs via :meth:`GeorisquesSource.access_for`. The actual fetch is
handled by core fetchers (tested elsewhere).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PKG = Path(__file__).resolve().parents[2] / "plugins" / "gispulse-src-georisques"
_PKG_PATH = str(_PKG)
if _PKG_PATH in sys.path:
    sys.path.remove(_PKG_PATH)
sys.path.insert(0, _PKG_PATH)
for _module in ("gispulse_src_georisques.source", "gispulse_src_georisques"):
    sys.modules.pop(_module, None)

from gispulse_src_georisques.source import GeorisquesSource  # noqa: E402

from gispulse.core.plugin_model import (  # noqa: E402
    AccessProtocol,
    FetchMode,
    Payload,
    SourceDomain,
    SourceResult,
)
from gispulse.core.sources import DataSource, ProtocolRegistry  # noqa: E402
from gispulse.core.fetchers import DUCKDB_SCAN_KEY, register_core_fetchers  # noqa: E402

pytestmark = pytest.mark.usefixtures("offline_ssrf")

_CODE_INSEE_ENTRIES = {"gaspar-risques", "radon", "sismicite"}
_LATLON_ENTRIES = {"rga", "tri-zonage", "ssp"}
_BULK_ENTRIES = {"rga-bulk", "tri-bulk", "sis-bulk", "gaspar-bulk"}


class FakeDownload:
    """Records resolved Géorisques bulk download AccessSpecs."""

    protocol = AccessProtocol.DOWNLOAD

    def __init__(self) -> None:
        self.calls: list = []

    def fetch(self, access, *, extent=None, mode=FetchMode.MATERIALIZE):
        self.calls.append(access)
        return SourceResult(payload=Payload.VECTOR, mode=mode, data=access.endpoint)


class _FakeResponse:
    """Minimal ``httpx.Response`` stand-in for revision() tests."""

    def __init__(self, headers: dict[str, str]) -> None:
        self.headers = headers


@pytest.fixture
def source() -> GeorisquesSource:
    reg = ProtocolRegistry()
    reg.register(FakeDownload())
    return GeorisquesSource(registry=reg)


def test_is_a_datasource(source: GeorisquesSource) -> None:
    assert isinstance(source, DataSource)
    assert source.name == "georisques"
    assert source.domain is SourceDomain.ENVIRONNEMENT
    assert source.payload is Payload.TABLE
    assert source.jurisdiction == "FR"


def test_declares_api_and_bulk_entries(source: GeorisquesSource) -> None:
    ids = {e.id for e in source.entries()}
    assert ids == _CODE_INSEE_ENTRIES | _LATLON_ENTRIES | _BULK_ENTRIES


def test_api_entries_use_rest_table(source: GeorisquesSource) -> None:
    for entry in source.entries():
        if entry.id in _BULK_ENTRIES:
            continue
        assert entry.access.protocol is AccessProtocol.REST_TABLE
        assert entry.access.endpoint.startswith("https://www.georisques.gouv.fr/")


def test_bulk_entries_use_download_and_keep_georisques_metadata(
    source: GeorisquesSource,
) -> None:
    by_id = {e.id: e for e in source.entries()}

    assert by_id["rga-bulk"].access.protocol is AccessProtocol.DOWNLOAD
    assert by_id["rga-bulk"].name == (
        "Retrait-gonflement des argiles 2025 (bulk, département)"
    )
    assert by_id["rga-bulk"].access.endpoint == (
        "https://files.georisques.fr/argiles/2025/AleaRG_2025_{departement}_L93.zip"
    )
    assert by_id["rga-bulk"].access.params == {"departement": "69"}
    assert by_id["rga-bulk"].metadata["millesime"] == "2025"
    assert by_id["rga-bulk"].metadata["base_key"] == "alearg_25"
    assert by_id["rga-bulk"].metadata["department_param"] == "codeDepartement"
    assert by_id["rga-bulk"].metadata["format"] == "zip"

    assert by_id["tri-bulk"].access.endpoint == (
        "https://files.georisques.fr/di_2020/tri_2020_sig_di_{departement}.zip"
    )
    assert by_id["tri-bulk"].metadata["base_key"] == "tri_2020"

    for entry_id in ("rga-bulk", "tri-bulk"):
        assert by_id[entry_id].metadata["join_strategy"] == "spatial"
        assert by_id[entry_id].metadata["geometry_key"] == "geometry"


def test_table_bulk_entries_use_table_file_and_keep_georisques_metadata(
    source: GeorisquesSource,
) -> None:
    by_id = {e.id: e for e in source.entries()}

    assert by_id["sis-bulk"].access.protocol is AccessProtocol.TABLE_FILE
    assert by_id["sis-bulk"].access.endpoint.startswith(
        "https://mapsref.brgm.fr/wxs/georisques/georisques_dl"
    )
    assert by_id["sis-bulk"].access.params == {"table_format": "csv"}
    assert by_id["sis-bulk"].payload is Payload.TABLE
    assert by_id["sis-bulk"].metadata["base_key"] == "sis"
    assert by_id["sis-bulk"].metadata["department_param"] is None
    assert by_id["sis-bulk"].metadata["join_keys"] == ("code_insee",)

    assert by_id["gaspar-bulk"].access.protocol is AccessProtocol.TABLE_FILE
    assert by_id["gaspar-bulk"].access.endpoint == (
        "https://files.georisques.fr/GASPAR/gaspar.zip"
    )
    assert by_id["gaspar-bulk"].access.params == {
        "archive_format": "zip",
        "table_format": "csv",
        "archive_member": "risq_gaspar.csv",
    }
    assert by_id["gaspar-bulk"].payload is Payload.TABLE
    assert by_id["gaspar-bulk"].metadata["base_key"] == "gaspar"
    assert by_id["gaspar-bulk"].metadata["join_keys"] == ("code_insee",)


def test_table_bulk_entries_fetch_as_tables_with_real_core_registry() -> None:
    reg = ProtocolRegistry()
    register_core_fetchers(reg)
    src = GeorisquesSource(registry=reg)

    for entry_id in ("sis-bulk", "gaspar-bulk"):
        result = src.fetch(entry_id, mode=FetchMode.REFERENCE)
        scan = result.metadata[DUCKDB_SCAN_KEY]

        assert result.payload is Payload.TABLE
        assert "read_csv_auto(" in scan
        assert "ST_Read(" not in scan


def test_query_key_metadata(source: GeorisquesSource) -> None:
    by_id = {e.id: e for e in source.entries()}
    for entry_id in _CODE_INSEE_ENTRIES:
        assert by_id[entry_id].metadata["query_key"] == "code_insee"
    for entry_id in _LATLON_ENTRIES:
        assert by_id[entry_id].metadata["query_key"] == "latlon"


def test_rga_and_ssp_use_body_row_source(source: GeorisquesSource) -> None:
    by_id = {e.id: e for e in source.entries()}
    for entry_id in ("rga", "ssp"):
        assert by_id[entry_id].access.params["pagination"]["row_source"] == "body"


def test_rga_does_not_treat_decode_errors_as_empty(source: GeorisquesSource) -> None:
    by_id = {e.id: e for e in source.entries()}
    assert "empty_body_is_empty" not in by_id["rga"].access.params["pagination"]


def test_tri_treats_404_as_empty(source: GeorisquesSource) -> None:
    by_id = {e.id: e for e in source.entries()}
    assert 404 in by_id["tri-zonage"].access.params["pagination"]["empty_statuses"]


def test_access_for_merges_code_insee(source: GeorisquesSource) -> None:
    access = source.access_for("radon", code_insee="63113", local_path="/tmp/r.jsonl")
    assert access.protocol is AccessProtocol.REST_TABLE
    assert access.params["query"]["code_insee"] == "63113"
    assert access.params["local_path"] == "/tmp/r.jsonl"


def test_access_for_can_target_s3_key(source: GeorisquesSource) -> None:
    access = source.access_for(
        "radon",
        code_insee="63113",
        s3_key="raw/georisques/radon/63113.jsonl",
    )
    assert access.params["query"]["code_insee"] == "63113"
    assert access.params["s3_key"] == "raw/georisques/radon/63113.jsonl"


def test_access_for_merges_latlon_and_preserves_static_query(
    source: GeorisquesSource,
) -> None:
    access = source.access_for("ssp", latlon="3.08,45.77", local_path="/tmp/ssp.jsonl")
    assert access.params["query"]["latlon"] == "3.08,45.77"
    assert access.params["query"]["rayon"] == 500
    assert access.params["query"]["page_size"] == 5
    assert access.params["pagination"]["row_source"] == "body"


def test_access_for_unknown_entry_raises(source: GeorisquesSource) -> None:
    with pytest.raises(KeyError, match="unknown_entry"):
        source.access_for("unknown_entry", code_insee="x")


def test_access_for_rejects_wrong_query_arg(source: GeorisquesSource) -> None:
    # radon is a code_insee endpoint — passing latlon is a usage error
    with pytest.raises(ValueError):
        source.access_for("radon", latlon="3.08,45.77")


def test_access_for_copies_nested_pagination(source: GeorisquesSource) -> None:
    first = source.access_for("ssp", latlon="3.08,45.77")
    first.params["pagination"]["row_source"] = "mutated"

    second = source.access_for("ssp", latlon="3.09,45.78")
    entry = source._entry("ssp")

    assert first.params["query"]["latlon"] == "3.08,45.77"
    assert second.params["query"]["latlon"] == "3.09,45.78"
    assert second.params["pagination"]["row_source"] == "body"
    assert entry.access.params["pagination"]["row_source"] == "body"


def test_fetch_rga_bulk_resolves_department_template() -> None:
    download = FakeDownload()
    reg = ProtocolRegistry()
    reg.register(download)
    src = GeorisquesSource(registry=reg)

    result = src.fetch("rga-bulk")

    assert result.payload is Payload.VECTOR
    assert result.data == "https://files.georisques.fr/argiles/2025/AleaRG_2025_69_L93.zip"
    assert len(download.calls) == 1
    assert download.calls[0].protocol is AccessProtocol.DOWNLOAD


def test_schema_radon_exposes_raw_classe_potentiel(source: GeorisquesSource) -> None:
    schema = source.schema("radon")
    assert "classe_potentiel" in schema


def test_schema_gaspar_exposes_raw_risques_detail(source: GeorisquesSource) -> None:
    schema = source.schema("gaspar-risques")
    assert schema["code_insee"] == "str"
    assert schema["risques_detail"] == "json"
    assert "num_risque" not in schema
    assert "libelle_risque_long" not in schema


def test_schema_sismicite_exposes_raw_zone_fields(source: GeorisquesSource) -> None:
    schema = source.schema("sismicite")
    assert schema["code_zone"] == "str"
    assert schema["libelle_commune"] == "str"


def test_schema_ssp_exposes_raw_casias_payload(source: GeorisquesSource) -> None:
    schema = source.schema("ssp")
    assert schema["casias"] == "json"
    assert schema["instructions"] == "json"
    assert schema["conclusions_sis"] == "json"
    assert schema["conclusions_sup"] == "json"
    assert "results" not in schema


def test_schema_bulk_entries_expose_raw_join_fields(source: GeorisquesSource) -> None:
    assert source.schema("rga-bulk")["geometry"] == "geometry"
    assert source.schema("tri-bulk")["code_national_tri"] == "str"
    assert source.schema("sis-bulk")["classification"] == "str"
    assert source.schema("gaspar-bulk")["code_insee"] == "str"


def test_revision_is_none_for_api_entries(source: GeorisquesSource) -> None:
    assert source.revision("radon") is None


def test_revision_probes_bulk_headers_and_uses_etag_or_last_modified(
    source: GeorisquesSource, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[str] = []

    def fake_head(url, **_kw):
        captured.append(url)
        if "gaspar.zip" in url:
            return _FakeResponse({"etag": '"gaspar-2026-05"'})
        return _FakeResponse({"last-modified": "Tue, 26 May 2026 08:00:00 GMT"})

    monkeypatch.setattr("httpx.head", fake_head)

    assert source.revision("gaspar-bulk") == "gaspar-2026-05"
    assert source.revision("sis-bulk") == "Tue, 26 May 2026 08:00:00 GMT"
    assert any("gaspar.zip" in url for url in captured)
    assert any("georisques_dl" in url for url in captured)


def test_revision_resolves_department_template_before_bulk_head(
    source: GeorisquesSource, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[str] = []

    def fake_head(url, **_kw):
        captured.append(url)
        return _FakeResponse({"etag": '"rga-69-2026-05"'})

    monkeypatch.setattr("httpx.head", fake_head)

    assert source.revision("rga-bulk") == "rga-69-2026-05"
    assert captured == [
        "https://files.georisques.fr/argiles/2025/AleaRG_2025_69_L93.zip"
    ]


def test_revision_returns_none_for_bulk_without_headers(
    source: GeorisquesSource, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_head(url, **_kw):
        return _FakeResponse({})

    monkeypatch.setattr("httpx.head", fake_head)
    assert source.revision("tri-bulk") is None


def test_register_adds_source_to_registry() -> None:
    from gispulse.core.sources import SOURCES
    from gispulse_src_georisques import register

    register()
    assert SOURCES.get("georisques") is not None

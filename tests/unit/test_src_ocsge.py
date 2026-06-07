"""Unit tests for the gispulse-src-ocsge plugin."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

_PKG = Path(__file__).resolve().parents[2] / "plugins" / "gispulse-src-ocsge"
_PKG_PATH = str(_PKG)
if _PKG_PATH in sys.path:
    sys.path.remove(_PKG_PATH)
sys.path.insert(0, _PKG_PATH)
for _module in ("gispulse_src_ocsge.source", "gispulse_src_ocsge"):
    sys.modules.pop(_module, None)

from gispulse.core.plugin_model import (  # noqa: E402
    AccessProtocol,
    FetchMode,
    Payload,
    SourceDomain,
    SourceResult,
)
from gispulse.core.sources import DataSource, ProtocolRegistry  # noqa: E402
from gispulse_src_ocsge.source import OcsgeSource  # noqa: E402
import gispulse_src_ocsge.source as ocsge_source  # noqa: E402

_ENTRY_ID = "occupation-sol"
_DEFAULT_DOWNLOAD = (
    "https://data.geopf.fr/telechargement/download/OCSGE/"
    "OCS-GE_2-0__GPKG_LAMB93_D075_2021-01-01/"
    "OCS-GE_2-0__GPKG_LAMB93_D075_2021-01-01.7z"
)


def _listing_xml(*titles: str) -> bytes:
    entries = "\n".join(
        f"""
  <entry>
    <title>{title}</title>
    <link href="https://data.geopf.fr/telechargement/resource/OCSGE/{title}" />
    <content>{title}</content>
  </entry>"""
        for title in titles
    )
    return f"""<?xml version='1.0' encoding='UTF-8'?>
<feed xmlns="http://www.w3.org/2005/Atom">
{entries}
</feed>
""".encode()


@pytest.fixture(autouse=True)
def clear_ocsge_listing_cache() -> None:
    ocsge_source._published_releases_for_zone.cache_clear()


def test_resource_listing_fetch_uses_user_agent_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    class _FakeResponse:
        def __enter__(self) -> "_FakeResponse":
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def read(self) -> bytes:
            return b"<feed />"

    def _fake_urlopen(request: object, *, timeout: int) -> _FakeResponse:
        seen["url"] = request.full_url
        seen["user_agent"] = request.get_header("User-agent")
        seen["timeout"] = timeout
        return _FakeResponse()

    monkeypatch.setattr(ocsge_source.urllib.request, "urlopen", _fake_urlopen)

    assert ocsge_source._fetch_resource_listing("D063") == b"<feed />"
    assert seen == {
        "url": "https://data.geopf.fr/telechargement/resource/OCSGE?zone=D063",
        "user_agent": "gispulse-src-ocsge/0.1",
        "timeout": 20,
    }


class FakeDownload:
    """Records resolved AccessSpecs for OCS GE archive downloads."""

    protocol = AccessProtocol.DOWNLOAD

    def __init__(self) -> None:
        self.calls: list = []

    def fetch(self, access, *, extent=None, mode=FetchMode.MATERIALIZE):
        self.calls.append(access)
        return SourceResult(payload=Payload.VECTOR, mode=mode, data=access.endpoint)


@pytest.fixture
def source() -> OcsgeSource:
    reg = ProtocolRegistry()
    reg.register(FakeDownload())
    return OcsgeSource(registry=reg)


def test_ocsge_is_a_datasource(source: OcsgeSource) -> None:
    assert isinstance(source, DataSource)
    assert source.name == "ocsge"
    assert source.domain is SourceDomain.FONCIER
    assert source.payload is Payload.VECTOR
    assert source.jurisdiction == "FR"


def test_catalog_declares_department_geopackage_download(source: OcsgeSource) -> None:
    entries = source.catalog()
    assert [entry.id for entry in entries] == [_ENTRY_ID]

    entry = entries[0]
    assert entry.name == "OCS GE - occupation du sol (GPKG departemental)"
    assert entry.domain is SourceDomain.FONCIER
    assert entry.payload is Payload.VECTOR
    assert entry.jurisdiction == "FR"
    assert entry.access.protocol is AccessProtocol.DOWNLOAD
    assert entry.access.endpoint == (
        "https://data.geopf.fr/telechargement/download/OCSGE/"
        "OCS-GE_{version}__GPKG_{crs}_{zone}_{millesime}/"
        "OCS-GE_{version}__GPKG_{crs}_{zone}_{millesime}.7z"
    )
    assert entry.access.params == {
        "resource": "OCSGE",
        "version": "2-0",
        "format": "GPKG",
        "crs": "LAMB93",
        "departement": "75",
        "zone": "D075",
        "millesime": "2021-01-01",
        "layer": "OCCUPATION_SOL",
        "archive_format": "7z",
        "data_format": "gpkg",
    }
    assert entry.access.format == "application/x-7z-compressed"


def test_catalog_metadata_documents_provider_and_artificialisation_rules(
    source: OcsgeSource,
) -> None:
    metadata = source.catalog()[0].metadata

    assert metadata["provider"] == "IGN"
    assert metadata["platform"] == "Géoplateforme / cartes.gouv.fr"
    assert metadata["dataset"] == "OCS GE Nouvelle génération"
    assert metadata["license"] == "Licence Ouverte 2.0"
    assert metadata["default_millesime"] == "2021-01-01"
    assert metadata["department_param"] == "departement"
    assert metadata["zone_format"] == "D{code_departement:0>3}"
    assert metadata["geometry_key"] == "the_geom"
    assert metadata["join_strategy"] == "spatial_intersects"
    assert metadata["included_layers"] == ("OCCUPATION_SOL", "ZONE_CONSTRUITE")
    assert metadata["resource_listing_endpoint"] == (
        "https://data.geopf.fr/telechargement/resource/OCSGE"
    )
    assert metadata["derived_artificialisation_resource"] == (
        "https://data.geopf.fr/telechargement/resource/OCSGE-ARTIFICIALISATION"
    )

    hints = metadata["artificialisation_hints"]
    assert hints["built_cover"] == ("CS1.1.1.1",)
    assert hints["impervious_or_composite_cover"] == (
        "CS1.1.1.2",
        "CS1.1.2.2",
    )
    assert hints["mineral_cover_except_extraction"] == {
        "code_cs": "CS1.1.2.1",
        "excluded_code_us": "US1.3",
    }
    assert hints["herbaceous_cover_artificial_usages"] == {
        "code_cs_prefix": "CS2.2",
        "code_us_values": (
            "US2",
            "US3",
            "US5",
            "US235",
            "US4.*",
            "US6.1",
            "US6.2",
        ),
    }
    assert "indicative" in hints["status"]


def test_access_for_resolves_department_and_millesime_without_mutating_catalog(
    monkeypatch: pytest.MonkeyPatch,
    source: OcsgeSource,
) -> None:
    monkeypatch.setattr(
        ocsge_source,
        "_fetch_resource_listing",
        lambda zone: _listing_xml(
            "OCS-GE_2-0__GPKG_LAMB93_D063_2019-01-01",
            "OCS-GE_2-0__GPKG_LAMB93_D063_2022-01-01",
        ),
    )
    access = source.access_for(
        _ENTRY_ID,
        departement="63",
        millesime="2022-01-01",
        local_path="/tmp/ocsge63.7z",
    )
    original = source._entry(_ENTRY_ID).access

    assert access.params["departement"] == "63"
    assert access.params["zone"] == "D063"
    assert access.params["crs"] == "LAMB93"
    assert access.params["millesime"] == "2022-01-01"
    assert access.params["local_path"] == "/tmp/ocsge63.7z"
    assert original.params["departement"] == "75"
    assert original.params["zone"] == "D075"
    assert original.params["millesime"] == "2021-01-01"


def test_access_for_accepts_department_aliases(
    monkeypatch: pytest.MonkeyPatch,
    source: OcsgeSource,
) -> None:
    listings = {
        "D007": _listing_xml("OCS-GE_2-0__GPKG_LAMB93_D007_2022-01-01"),
        "D02A": _listing_xml("OCS-GE_2-0__GPKG_LAMB93_D02A_2022-01-01"),
        "D971": _listing_xml("OCS-GE_2-0__GPKG_RGAF09UTM20_D971_2022-01-01"),
    }
    monkeypatch.setattr(
        ocsge_source,
        "_fetch_resource_listing",
        lambda zone: listings[zone],
    )

    single_digit = source.access_for(_ENTRY_ID, code_departement="7")
    corsica = source.access_for(_ENTRY_ID, department="2a")
    drom = source.access_for(_ENTRY_ID, departement="971")

    assert single_digit.params["departement"] == "07"
    assert single_digit.params["zone"] == "D007"
    assert corsica.params["departement"] == "2A"
    assert corsica.params["zone"] == "D02A"
    assert drom.params["departement"] == "971"
    assert drom.params["zone"] == "D971"
    assert drom.params["crs"] == "RGAF09UTM20"


def test_access_for_chooses_latest_available_millesime_and_dom_crs(
    monkeypatch: pytest.MonkeyPatch,
    source: OcsgeSource,
) -> None:
    monkeypatch.setattr(
        ocsge_source,
        "_fetch_resource_listing",
        lambda zone: _listing_xml(
            "OCS-GE_2-0__GPKG_RGAF09UTM20_D971_2017-01-01",
            "OCS-GE_2-0__GPKG_RGAF09UTM20_D971_2022-01-01",
            "OCS-GE_2-0_DIFF-2017-2022_GPKG_RGAF09UTM20_D971_2025-03-07",
        ),
    )

    access = source.access_for(_ENTRY_ID, departement="971")

    assert access.params["zone"] == "D971"
    assert access.params["crs"] == "RGAF09UTM20"
    assert access.params["millesime"] == "2022-01-01"
    assert "RGAF09UTM20_D971_2022-01-01" in access.endpoint


def test_access_for_rejects_unavailable_department_millesime(
    monkeypatch: pytest.MonkeyPatch,
    source: OcsgeSource,
) -> None:
    monkeypatch.setattr(
        ocsge_source,
        "_fetch_resource_listing",
        lambda zone: _listing_xml(
            "OCS-GE_2-0__GPKG_LAMB93_D083_2017-01-01",
            "OCS-GE_2-0__GPKG_LAMB93_D083_2020-01-01",
            "OCS-GE_2-0__GPKG_LAMB93_D083_2023-01-01",
        ),
    )

    with pytest.raises(ValueError, match="unavailable OCS GE archive.*D083.*2021"):
        source.access_for(_ENTRY_ID, departement="83", millesime="2021-01-01")


def test_access_for_rejects_invalid_department_or_millesime(
    source: OcsgeSource,
) -> None:
    with pytest.raises(ValueError, match="invalid OCS GE department code"):
        source.access_for(_ENTRY_ID, departement="999")

    with pytest.raises(ValueError, match="invalid OCS GE millesime"):
        source.access_for(_ENTRY_ID, millesime="2021")


def test_fetch_resolves_default_download_template() -> None:
    download = FakeDownload()
    reg = ProtocolRegistry()
    reg.register(download)
    src = OcsgeSource(registry=reg)

    result = src.fetch(_ENTRY_ID)

    assert result.payload is Payload.VECTOR
    assert result.data == _DEFAULT_DOWNLOAD
    assert len(download.calls) == 1
    assert download.calls[0].protocol is AccessProtocol.DOWNLOAD


def test_bulk_runner_resolves_department_zone_and_latest_available_url(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    source: OcsgeSource,
) -> None:
    from gispulse.core import bulk_runner
    from gispulse.core.bulk_runner import BulkIngestRunner

    requested: list[str] = []
    monkeypatch.setattr(
        ocsge_source,
        "_fetch_resource_listing",
        lambda zone: _listing_xml(
            "OCS-GE_2-0__GPKG_LAMB93_D063_2019-01-01",
            "OCS-GE_2-0__GPKG_LAMB93_D063_2022-01-01",
        ),
    )

    def _fake_download(url: str, archive_path: Path) -> None:
        requested.append(url)
        archive_path.write_bytes(b"fake-7z")

    def _fake_extract_7z(archive_path: Path, dest_dir: Path) -> None:
        assert archive_path.read_bytes() == b"fake-7z"
        (dest_dir / "OCCUPATION_SOL.gpkg").write_bytes(b"fake-gpkg")

    class _FakeConn:
        def execute(self, _sql: str) -> None:
            return None

    class _FakeSession:
        conn = _FakeConn()

        def __enter__(self) -> "_FakeSession":
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    class _FakeStorage:
        async def upload(
            self,
            key: str,
            data: bytes | object,
            content_type: str = "",
        ) -> str:
            return key

    import gispulse.persistence.duckdb_engine as duckdb_engine

    monkeypatch.setattr(bulk_runner, "_download_to_path", _fake_download)
    monkeypatch.setattr(bulk_runner, "_extract_7z_archive", _fake_extract_7z)
    monkeypatch.setattr(duckdb_engine, "DuckDBSession", _FakeSession)

    BulkIngestRunner(storage=_FakeStorage(), temp_dir=tmp_path).run_entry(
        source,
        _ENTRY_ID,
        departement="63",
        revision="ocsge-test",
    )

    assert requested == [
        (
            "https://data.geopf.fr/telechargement/download/OCSGE/"
            "OCS-GE_2-0__GPKG_LAMB93_D063_2022-01-01/"
            "OCS-GE_2-0__GPKG_LAMB93_D063_2022-01-01.7z"
        )
    ]


def test_bulk_runner_rejects_unavailable_ocsge_millesime(
    monkeypatch: pytest.MonkeyPatch,
    source: OcsgeSource,
) -> None:
    from gispulse.core.bulk_runner import BulkIngestRunner

    monkeypatch.setattr(
        ocsge_source,
        "_fetch_resource_listing",
        lambda zone: _listing_xml(
            "OCS-GE_2-0__GPKG_LAMB93_D083_2017-01-01",
            "OCS-GE_2-0__GPKG_LAMB93_D083_2020-01-01",
            "OCS-GE_2-0__GPKG_LAMB93_D083_2023-01-01",
        ),
    )

    with pytest.raises(ValueError, match="unavailable OCS GE archive.*D083.*2021"):
        BulkIngestRunner().run_entry(
            source,
            _ENTRY_ID,
            departement="83",
            revision="ocsge-test",
            params={"millesime": "2021-01-01"},
        )


def test_schema_exposes_verified_occupation_sol_fields(source: OcsgeSource) -> None:
    schema = source.schema(_ENTRY_ID)

    assert schema == {
        "fid": "int",
        "the_geom": "geometry",
        "id": "str",
        "code_cs": "str",
        "code_us": "str",
        "millesime": "str",
        "source": "str",
        "ossature": "int",
        "id_origine": "str",
        "code_or": "str",
    }


def test_revision_is_unknown_when_no_live_token_is_modelled(
    source: OcsgeSource,
) -> None:
    assert source.revision(_ENTRY_ID) is None


def test_unknown_entry_raises(source: OcsgeSource) -> None:
    with pytest.raises(KeyError, match="unknown entry 'ghost'"):
        source.schema("ghost")
    with pytest.raises(KeyError, match="unknown entry 'ghost'"):
        source.revision("ghost")


def test_register_hook_registers_ocsge_source(monkeypatch: pytest.MonkeyPatch) -> None:
    module = importlib.import_module("gispulse_src_ocsge")
    sources = getattr(sys.modules[DataSource.__module__], "SOURCES")

    monkeypatch.setattr(sources, "_sources", {})
    module.register()

    registered = sources.get("ocsge")
    assert isinstance(registered, DataSource)
    assert registered.domain is SourceDomain.FONCIER

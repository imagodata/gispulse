"""Unit tests for the gispulse-src-ban plugin.

Zero-network: the plugin declares BAN geocoding API and bulk CSV AccessSpecs.
Core fetchers own HTTP, file materialization and lazy scans.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

_PKG = Path(__file__).resolve().parents[2] / "plugins" / "gispulse-src-ban"
_PKG_PATH = str(_PKG)
if _PKG_PATH in sys.path:
    sys.path.remove(_PKG_PATH)
sys.path.insert(0, _PKG_PATH)
for _module in ("gispulse_src_ban.source", "gispulse_src_ban"):
    sys.modules.pop(_module, None)

from gispulse.core.plugin_model import (  # noqa: E402
    AccessProtocol,
    FetchMode,
    Payload,
    SourceDomain,
    SourceResult,
)
from gispulse.core.sources import DataSource, ProtocolRegistry  # noqa: E402

pytestmark = pytest.mark.usefixtures("offline_ssrf")


class FakeRestTable:
    """Records REST_TABLE AccessSpecs and returns the endpoint."""

    protocol = AccessProtocol.REST_TABLE

    def __init__(self) -> None:
        self.calls: list = []

    def fetch(self, access, *, extent=None, mode=FetchMode.MATERIALIZE):
        self.calls.append(access)
        return SourceResult(payload=Payload.TABLE, mode=mode, data=access.endpoint)


class FakeTableFile:
    """Records TABLE_FILE AccessSpecs and returns the endpoint."""

    protocol = AccessProtocol.TABLE_FILE

    def __init__(self) -> None:
        self.calls: list = []

    def fetch(self, access, *, extent=None, mode=FetchMode.MATERIALIZE):
        self.calls.append(access)
        return SourceResult(payload=Payload.TABLE, mode=mode, data=access.endpoint)


def _source_class():
    return importlib.import_module("gispulse_src_ban.source").BanSource


@pytest.fixture
def source():
    reg = ProtocolRegistry()
    reg.register(FakeRestTable())
    reg.register(FakeTableFile())
    return _source_class()(registry=reg)


def test_pyproject_declares_ban_entrypoint_and_base_manifest() -> None:
    tomllib = pytest.importorskip("tomllib")
    pyproject = tomllib.loads((_PKG / "pyproject.toml").read_text())

    assert pyproject["project"]["entry-points"]["gispulse.data_sources"] == {
        "ban": "gispulse_src_ban:register"
    }

    manifest = pyproject["tool"]["gispulse"]["plugin"]
    assert manifest["kind"] == "source"
    assert manifest["domain"] == "base"
    assert manifest["jurisdiction"] == "FR"


def test_register_adds_ban_source_to_global_registry() -> None:
    from gispulse.core.sources import SOURCES

    BanSource = _source_class()
    register = importlib.import_module("gispulse_src_ban").register

    SOURCES.clear()
    try:
        register()
        registered = SOURCES.get("ban")
        assert isinstance(registered, BanSource)
        assert {entry.id for entry in registered.catalog()} == {
            "addresses-search",
            "addresses-reverse",
            "addresses-departement",
        }
    finally:
        SOURCES.clear()


def test_ban_is_a_base_table_datasource(source) -> None:
    assert isinstance(source, DataSource)
    assert source.name == "ban"
    assert source.domain is SourceDomain.BASE
    assert source.payload is Payload.TABLE
    assert source.jurisdiction == "FR"


def test_search_entry_uses_geoplateforme_rest_table_with_dept63_default(source) -> None:
    entry = source._entry("addresses-search")

    assert entry.access.protocol is AccessProtocol.REST_TABLE
    assert entry.access.endpoint == "https://data.geopf.fr/geocodage/search/"
    assert entry.access.format == "application/json"
    assert entry.access.params["query"] == {
        "q": "1 rue abbe Girard Clermont-Ferrand",
        "citycode": "63113",
        "limit": 5,
    }
    assert entry.access.params["pagination"] == {
        "data_key": "features",
        "max_pages": 1,
        "max_rows": 5,
    }
    assert entry.metadata["default_departement"] == "63"
    assert entry.metadata["default_citycode"] == "63113"
    assert entry.metadata["api_host"] == "data.geopf.fr/geocodage"
    assert entry.metadata["legacy_api_host"] == "api-adresse.data.gouv.fr"
    assert entry.metadata["query_kind"] == "search"
    assert entry.metadata["join_keys"] == (
        "properties.id",
        "properties.banId",
        "properties.citycode",
    )


def test_reverse_entry_uses_geoplateforme_rest_table_with_clermont_default(source) -> None:
    entry = source._entry("addresses-reverse")

    assert entry.access.protocol is AccessProtocol.REST_TABLE
    assert entry.access.endpoint == "https://data.geopf.fr/geocodage/reverse/"
    assert entry.access.params["query"] == {
        "lon": 3.087025,
        "lat": 45.777222,
        "limit": 1,
    }
    assert entry.access.params["pagination"] == {
        "data_key": "features",
        "max_pages": 1,
        "max_rows": 1,
    }
    assert entry.metadata["query_kind"] == "reverse"
    assert entry.metadata["default_lon"] == 3.087025
    assert entry.metadata["default_lat"] == 45.777222


def test_department_bulk_entry_uses_ban_csv_gzip_with_dept63_default(source) -> None:
    entry = source._entry("addresses-departement")

    assert entry.access.protocol is AccessProtocol.TABLE_FILE
    assert entry.access.endpoint == (
        "https://adresse.data.gouv.fr/data/ban/adresses/latest/csv/"
        "adresses-{departement}.csv.gz"
    )
    assert entry.access.params == {
        "departement": "63",
        "table_format": "csv",
        "delimiter": ";",
    }
    assert entry.access.format == "text/csv"
    assert entry.payload is Payload.TABLE
    assert entry.metadata["default_departement"] == "63"
    assert entry.metadata["compression"] == "gzip"
    assert entry.metadata["join_keys"] == (
        "id",
        "code_insee",
        "cad_parcelles",
    )


def test_access_for_search_overrides_query_without_mutating_catalog(source) -> None:
    access = source.access_for(
        "addresses-search",
        q="8 boulevard du port",
        citycode="95127",
        limit=1,
        s3_key="raw/ban/search/cergy.jsonl",
    )
    original = source._entry("addresses-search").access

    assert access.params["query"] == {
        "q": "8 boulevard du port",
        "citycode": "95127",
        "limit": 1,
    }
    assert access.params["pagination"]["max_rows"] == 1
    assert access.params["s3_key"] == "raw/ban/search/cergy.jsonl"
    assert original.params["query"]["citycode"] == "63113"


def test_access_for_reverse_replaces_coordinates_and_type_filter(source) -> None:
    access = source.access_for(
        "addresses-reverse",
        lon=2.062821,
        lat=49.031624,
        address_type="housenumber",
        limit=3,
        local_path="/tmp/ban-reverse.jsonl",
    )

    assert access.params["query"] == {
        "lon": 2.062821,
        "lat": 49.031624,
        "limit": 3,
        "type": "housenumber",
    }
    assert access.params["pagination"]["max_rows"] == 3
    assert access.params["local_path"] == "/tmp/ban-reverse.jsonl"


def test_access_for_bulk_normalises_department_and_materialization(source) -> None:
    access = source.access_for(
        "addresses-departement",
        departement="1",
        s3_key="raw/ban/adresses-01.parquet",
    )

    assert access.params == {
        "departement": "01",
        "table_format": "csv",
        "delimiter": ";",
        "s3_key": "raw/ban/adresses-01.parquet",
    }


def test_access_for_rejects_invalid_limit_and_department(source) -> None:
    with pytest.raises(ValueError, match="limit"):
        source.access_for("addresses-search", limit=21)

    with pytest.raises(ValueError, match="department"):
        source.access_for("addresses-departement", departement="999")


def test_fetch_delegates_search_to_rest_table_adapter() -> None:
    rest = FakeRestTable()
    reg = ProtocolRegistry()
    reg.register(rest)
    reg.register(FakeTableFile())
    src = _source_class()(registry=reg)

    result = src.fetch("addresses-search")

    assert result.payload is Payload.TABLE
    assert result.data == "https://data.geopf.fr/geocodage/search/"
    assert len(rest.calls) == 1
    assert rest.calls[0].protocol is AccessProtocol.REST_TABLE


def test_fetch_delegates_department_template_to_table_file_adapter() -> None:
    table_file = FakeTableFile()
    reg = ProtocolRegistry()
    reg.register(FakeRestTable())
    reg.register(table_file)
    src = _source_class()(registry=reg)

    result = src.fetch("addresses-departement")

    assert result.payload is Payload.TABLE
    assert result.data.endswith("/adresses-63.csv.gz")
    assert len(table_file.calls) == 1
    assert table_file.calls[0].endpoint.endswith("/adresses-63.csv.gz")


def test_schema_exposes_search_feature_and_bulk_csv_fields(source) -> None:
    search_schema = source.schema("addresses-search")
    bulk_schema = source.schema("addresses-departement")

    assert search_schema["properties.id"] == "str"
    assert search_schema["properties.banId"] == "str"
    assert search_schema["properties.citycode"] == "str"
    assert search_schema["geometry.coordinates"] == "list[float]"

    assert bulk_schema["id"] == "str"
    assert bulk_schema["code_insee"] == "str"
    assert bulk_schema["lon"] == "float"
    assert bulk_schema["lat"] == "float"
    assert bulk_schema["cad_parcelles"] == "str"


def test_revision_is_unknown_for_api_entries(source) -> None:
    assert source.revision("addresses-search") is None
    assert source.revision("addresses-reverse") is None

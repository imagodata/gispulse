"""Unit tests for the gispulse-src-bdnb plugin."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

_PKG = Path(__file__).resolve().parents[2] / "plugins" / "gispulse-src-bdnb"
if str(_PKG) not in sys.path:
    sys.path.insert(0, str(_PKG))

from gispulse.core.plugin_model import (  # noqa: E402
    AccessProtocol,
    FetchMode,
    Payload,
    SourceDomain,
    SourceResult,
)
from gispulse.core.sources import DataSource, ProtocolRegistry  # noqa: E402


class FakeDownload:
    """Records resolved BDNB GPKG archive AccessSpecs."""

    protocol = AccessProtocol.DOWNLOAD

    def __init__(self) -> None:
        self.calls: list = []

    def fetch(self, access, *, extent=None, mode=FetchMode.MATERIALIZE):
        self.calls.append(access)
        return SourceResult(payload=Payload.VECTOR, mode=mode, data=access.endpoint)


class FakeTableFile:
    """Records resolved BDNB CSV archive AccessSpecs."""

    protocol = AccessProtocol.TABLE_FILE

    def __init__(self) -> None:
        self.calls: list = []

    def fetch(self, access, *, extent=None, mode=FetchMode.MATERIALIZE):
        self.calls.append(access)
        return SourceResult(payload=Payload.TABLE, mode=mode, data=access.endpoint)


def _source_class():
    return importlib.import_module("gispulse_src_bdnb.source").BdnbSource


@pytest.fixture
def source():
    reg = ProtocolRegistry()
    reg.register(FakeDownload())
    reg.register(FakeTableFile())
    return _source_class()(registry=reg)


def test_pyproject_declares_bdnb_entrypoint_and_foncier_manifest() -> None:
    tomllib = pytest.importorskip("tomllib")
    pyproject = tomllib.loads((_PKG / "pyproject.toml").read_text())

    assert pyproject["project"]["entry-points"]["gispulse.data_sources"] == {
        "bdnb": "gispulse_src_bdnb:register"
    }

    manifest = pyproject["tool"]["gispulse"]["plugin"]
    assert manifest["kind"] == "source"
    assert manifest["domain"] == "foncier"
    assert manifest["jurisdiction"] == "FR"


def test_register_adds_bdnb_source_to_global_registry() -> None:
    from gispulse.core.sources import SOURCES

    BdnbSource = _source_class()
    register = importlib.import_module("gispulse_src_bdnb").register

    SOURCES.clear()
    try:
        register()
        registered = SOURCES.get("bdnb")
        assert isinstance(registered, BdnbSource)
        assert {entry.id for entry in registered.catalog()} == {
            "batiments",
            "batiments_tables",
        }
    finally:
        SOURCES.clear()


def test_bdnb_is_a_foncier_vector_datasource(source) -> None:
    assert isinstance(source, DataSource)
    assert source.name == "bdnb"
    assert source.domain is SourceDomain.FONCIER
    assert source.payload is Payload.VECTOR
    assert source.jurisdiction == "FR"


def test_catalog_declares_departmental_gpkg_and_csv_building_archives(source) -> None:
    entries = {entry.id: entry for entry in source.catalog()}

    batiments = entries["batiments"]
    assert batiments.access.protocol is AccessProtocol.DOWNLOAD
    assert batiments.access.endpoint == (
        "https://open-data.s3.fr-par.scw.cloud/bdnb_millesime_2026-02-a/"
        "millesime_2026-02-a_dep{departement}/"
        "open_data_millesime_2026-02-a_dep{departement}_gpkg.zip"
    )
    assert batiments.access.params == {
        "departement": "63",
        "archive_format": "zip",
        "data_format": "gpkg",
    }
    assert batiments.access.format == "application/zip"
    assert batiments.payload is Payload.VECTOR
    assert batiments.metadata["millesime"] == "2026-02.a"
    assert batiments.metadata["department_param"] == "departement"
    assert batiments.metadata["join_key"] == "batiment_groupe_id"
    assert batiments.metadata["geometry_key"] == "geometry"

    tables = entries["batiments_tables"]
    assert tables.access.protocol is AccessProtocol.TABLE_FILE
    assert tables.access.endpoint.endswith("_dep{departement}_csv.zip")
    assert tables.access.params == {
        "departement": "63",
        "archive_format": "zip",
        "table_format": "csv",
        "archive_member": "csv/batiment_groupe.csv",
    }
    assert tables.payload is Payload.TABLE
    assert tables.metadata["table_name"] == "batiment_groupe"
    assert tables.metadata["archive_member"] == "csv/batiment_groupe.csv"
    assert "971" not in tables.metadata["published_departements"]
    assert "2a" in tables.metadata["published_departements"]


def test_access_for_resolves_department_without_mutating_catalog(source) -> None:
    access = source.access_for("batiments", departement="75", local_path="/tmp/bdnb75.zip")
    original = source._entry("batiments").access

    assert access.params["departement"] == "75"
    assert access.params["local_path"] == "/tmp/bdnb75.zip"
    assert original.params == {
        "departement": "63",
        "archive_format": "zip",
        "data_format": "gpkg",
    }


def test_access_for_accepts_code_departement_alias_and_normalises_single_digit(
    source,
) -> None:
    access = source.access_for("batiments_tables", code_departement="7")

    assert access.params["departement"] == "07"
    assert access.endpoint.endswith("_dep{departement}_csv.zip")


def test_access_for_maps_corsica_to_published_lowercase_key(source) -> None:
    access = source.access_for("batiments_tables", departement="2A")

    assert access.params["departement"] == "2a"


def test_access_for_rejects_unpublished_department_keys(source) -> None:
    with pytest.raises(ValueError, match="invalid BDNB department code"):
        source.access_for("batiments", departement="999")

    with pytest.raises(ValueError, match="invalid BDNB department code"):
        source.access_for("batiments", departement="971")


def test_fetch_batiments_resolves_default_department_template() -> None:
    download = FakeDownload()
    reg = ProtocolRegistry()
    reg.register(download)
    reg.register(FakeTableFile())
    src = _source_class()(registry=reg)

    result = src.fetch("batiments")

    assert result.payload is Payload.VECTOR
    assert result.data == (
        "https://open-data.s3.fr-par.scw.cloud/bdnb_millesime_2026-02-a/"
        "millesime_2026-02-a_dep63/open_data_millesime_2026-02-a_dep63_gpkg.zip"
    )
    assert len(download.calls) == 1
    assert download.calls[0].protocol is AccessProtocol.DOWNLOAD


def test_fetch_tables_resolves_default_department_template() -> None:
    table_file = FakeTableFile()
    reg = ProtocolRegistry()
    reg.register(FakeDownload())
    reg.register(table_file)
    src = _source_class()(registry=reg)

    result = src.fetch("batiments_tables")

    assert result.payload is Payload.TABLE
    assert result.data.endswith(
        "/millesime_2026-02-a_dep63/open_data_millesime_2026-02-a_dep63_csv.zip"
    )
    assert len(table_file.calls) == 1
    assert table_file.calls[0].protocol is AccessProtocol.TABLE_FILE
    assert table_file.calls[0].params["archive_member"] == "csv/batiment_groupe.csv"


def test_schema_exposes_bdnb_building_headline_fields(source) -> None:
    schema = source.schema("batiments")

    assert schema["batiment_groupe_id"] == "str"
    assert schema["code_departement_insee"] == "str"
    assert schema["s_geom_groupe"] == "float"
    assert schema["hauteur_mean"] == "float"
    assert schema["dpe_arrete_2021_annee_construction_dpe"] == "int"
    assert schema["dpe_arrete_2021_periode_construction_dpe"] == "str"
    assert schema["dpe_arrete_2021_classe_conso_energie"] == "str"
    assert schema["geometry"] == "geometry"


def test_revision_is_the_declared_bdnb_millesime(source) -> None:
    assert source.revision("batiments") == "bdnb-2026-02-a"
    assert source.revision("batiments_tables") == "bdnb-2026-02-a"

"""Unit tests for the gispulse-src-sitadel plugin.

Zero-network: the plugin only declares data.gouv.fr/DiDo AccessSpecs.
Core TABLE_FILE fetchers own HTTP, materialisation and lazy scans.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

_PKG = Path(__file__).resolve().parents[2] / "plugins" / "gispulse-src-sitadel"
_PKG_PATH = str(_PKG)
if _PKG_PATH in sys.path:
    sys.path.remove(_PKG_PATH)
sys.path.insert(0, _PKG_PATH)
for _module in ("gispulse_src_sitadel.source", "gispulse_src_sitadel"):
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


class FakeTableFile:
    """Records TABLE_FILE AccessSpecs and returns the endpoint."""

    protocol = AccessProtocol.TABLE_FILE

    def __init__(self) -> None:
        self.calls: list = []

    def fetch(self, access, *, extent=None, mode=FetchMode.MATERIALIZE):
        self.calls.append(access)
        return SourceResult(payload=Payload.TABLE, mode=mode, data=access.endpoint)


def _source_class():
    return importlib.import_module("gispulse_src_sitadel.source").SitadelSource


@pytest.fixture
def source():
    reg = ProtocolRegistry()
    reg.register(FakeTableFile())
    return _source_class()(registry=reg)


# ---------------------------------------------------------------------------
# Manifest / entry-point
# ---------------------------------------------------------------------------


def test_pyproject_declares_sitadel_entrypoint_and_foncier_manifest() -> None:
    tomllib = pytest.importorskip("tomllib")
    pyproject = tomllib.loads((_PKG / "pyproject.toml").read_text())

    assert pyproject["project"]["entry-points"]["gispulse.data_sources"] == {
        "sitadel": "gispulse_src_sitadel:register"
    }

    manifest = pyproject["tool"]["gispulse"]["plugin"]
    assert manifest["kind"] == "source"
    assert manifest["domain"] == "foncier"
    assert manifest["jurisdiction"] == "FR"


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_register_adds_sitadel_source_to_global_registry() -> None:
    from gispulse.core.sources import SOURCES

    SitadelSource = _source_class()
    register = importlib.import_module("gispulse_src_sitadel").register

    SOURCES.clear()
    try:
        register()
        registered = SOURCES.get("sitadel")
        assert isinstance(registered, SitadelSource)
        assert {entry.id for entry in registered.catalog()} == {
            "autorisations_logements",
            "autorisations_locaux",
        }
    finally:
        SOURCES.clear()


# ---------------------------------------------------------------------------
# Source contract
# ---------------------------------------------------------------------------


def test_sitadel_is_a_foncier_table_datasource(source) -> None:
    assert isinstance(source, DataSource)
    assert source.name == "sitadel"
    assert source.domain is SourceDomain.FONCIER
    assert source.payload is Payload.TABLE
    assert source.jurisdiction == "FR"


# ---------------------------------------------------------------------------
# Catalog entries
# ---------------------------------------------------------------------------


def test_catalog_declares_logements_and_locaux_entries(source) -> None:
    entries = {e.id: e for e in source.catalog()}

    assert set(entries) == {"autorisations_logements", "autorisations_locaux"}


def test_logements_entry_targets_datagouv_redirect(source) -> None:
    entry = source._entry("autorisations_logements")

    assert entry.access.protocol is AccessProtocol.TABLE_FILE
    assert entry.access.endpoint == (
        "https://www.data.gouv.fr/api/1/datasets/r/65a9e264-7a20-46a9-9d98-66becb817bc3"
    )
    assert entry.access.params == {"table_format": "csv", "delimiter": ";"}
    assert entry.access.format == "text/csv"
    assert entry.payload is Payload.TABLE
    assert entry.domain is SourceDomain.FONCIER
    assert entry.metadata["resource_id"] == "65a9e264-7a20-46a9-9d98-66becb817bc3"
    assert entry.metadata["join_key"] == "COMM"
    assert entry.metadata["license"] == "Licence Ouverte 2.0"
    assert entry.metadata["update_cadence"] == "mensuel"


def test_locaux_entry_targets_datagouv_redirect(source) -> None:
    entry = source._entry("autorisations_locaux")

    assert entry.access.protocol is AccessProtocol.TABLE_FILE
    assert entry.access.endpoint == (
        "https://www.data.gouv.fr/api/1/datasets/r/8f23d65f-7142-4ac5-94c1-077b028255bf"
    )
    assert entry.metadata["resource_id"] == "8f23d65f-7142-4ac5-94c1-077b028255bf"
    assert entry.metadata["join_key"] == "COMM"


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_schema_logements_exposes_key_sitadel_fields(source) -> None:
    schema = source.schema("autorisations_logements")

    # geography + dossier
    assert schema["REG"] == "str"
    assert schema["DEP"] == "str"
    assert schema["COMM"] == "str"
    assert schema["Type_DAU"] == "str"
    assert schema["Etat_DAU"] == "str"
    # dates
    assert schema["DATE_REELLE_AUTORISATION"] == "str"
    # key metrics
    assert schema["NB_LGT_TOT_CREES"] == "int"
    assert schema["NB_LGT_IND_CREES"] == "int"
    assert schema["NB_LGT_COL_CREES"] == "int"
    assert schema["SURF_HAB_CREEE"] == "float"
    assert schema["SUPERFICIE_TERRAIN"] == "float"
    assert schema["NATURE_PROJET"] == "str"


def test_schema_locaux_exposes_key_fields(source) -> None:
    schema = source.schema("autorisations_locaux")

    assert schema["COMM"] == "str"
    assert schema["DATE_REELLE_AUTORISATION"] == "str"
    assert schema["SURF_LOC_CREEE"] == "float"
    assert schema["NATURE_PROJET"] == "str"
    # logement fields must NOT be in locaux schema
    assert "NB_LGT_TOT_CREES" not in schema


# ---------------------------------------------------------------------------
# Revision
# ---------------------------------------------------------------------------


def test_revision_embeds_millesime_and_resource_id(source) -> None:
    rev_log = source.revision("autorisations_logements")
    rev_loc = source.revision("autorisations_locaux")

    assert rev_log is not None
    assert "sitadel-" in rev_log
    assert "65a9e264-7a20-46a9-9d98-66becb817bc3" in rev_log

    assert rev_loc is not None
    assert "8f23d65f-7142-4ac5-94c1-077b028255bf" in rev_loc

    # two different entries → two different revision tokens
    assert rev_log != rev_loc


# ---------------------------------------------------------------------------
# Fetch delegation
# ---------------------------------------------------------------------------


def test_fetch_delegates_logements_to_table_file_adapter() -> None:
    table_file = FakeTableFile()
    reg = ProtocolRegistry()
    reg.register(table_file)
    src = _source_class()(registry=reg)

    result = src.fetch("autorisations_logements")

    assert result.payload is Payload.TABLE
    assert result.data == (
        "https://www.data.gouv.fr/api/1/datasets/r/65a9e264-7a20-46a9-9d98-66becb817bc3"
    )
    assert len(table_file.calls) == 1
    assert table_file.calls[0].protocol is AccessProtocol.TABLE_FILE
    assert table_file.calls[0].params["delimiter"] == ";"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_unknown_entry_raises(source) -> None:
    with pytest.raises(KeyError, match="unknown entry 'ghost'"):
        source.schema("ghost")

"""Unit tests for the gispulse-src-statbel-be plugin.

Zero-network: the plugin only declares Statbel open-data AccessSpecs.
Core fetchers own HTTP and materialisation.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

_PKG = (
    Path(__file__).resolve().parents[2]
    / "plugins"
    / "gispulse-src-statbel-be"
)
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


# ---------------------------------------------------------------------------
# Fake fetcher — DOWNLOAD protocol
# ---------------------------------------------------------------------------


class FakeDownload:
    """Records the AccessSpec it is handed and returns a marker result."""

    protocol = AccessProtocol.DOWNLOAD

    def __init__(self) -> None:
        self.calls: list = []

    def fetch(self, access, *, extent=None, mode=FetchMode.MATERIALIZE):
        self.calls.append(access)
        return SourceResult(
            payload=Payload.VECTOR,
            mode=mode,
            data=access.endpoint,
        )


def _source_class():
    return importlib.import_module(
        "gispulse_src_statbel_be.source"
    ).StatbelBeSource


@pytest.fixture
def source():
    reg = ProtocolRegistry()
    reg.register(FakeDownload())
    return _source_class()(registry=reg)


# ---------------------------------------------------------------------------
# Packaging + registration
# ---------------------------------------------------------------------------


def test_pyproject_declares_statbel_be_entrypoint_and_manifest() -> None:
    tomllib = pytest.importorskip("tomllib")
    pyproject = tomllib.loads((_PKG / "pyproject.toml").read_text())

    assert pyproject["project"]["entry-points"]["gispulse.data_sources"] == {
        "statbel_be": "gispulse_src_statbel_be:register"
    }

    manifest = pyproject["tool"]["gispulse"]["plugin"]
    assert manifest["kind"] == "source"
    assert manifest["domain"] == "statistique"
    assert manifest["jurisdiction"] == "BE"


def test_register_adds_statbel_be_source_to_global_registry() -> None:
    from gispulse.core.sources import SOURCES

    StatbelBeSource = _source_class()
    register = importlib.import_module("gispulse_src_statbel_be").register

    SOURCES.clear()
    try:
        register()
        registered = SOURCES.get("statbel_be")
        assert isinstance(registered, StatbelBeSource)
        expected_ids = {
            "sectors-contours",
            "indicators-population",
            "indicators-households",
            "indicators-income",
            "indicators-cars",
            "indicators-building-permits",
        }
        assert {e.id for e in registered.catalog()} == expected_ids
    finally:
        SOURCES.clear()


# ---------------------------------------------------------------------------
# Contract conformance + declared axes
# ---------------------------------------------------------------------------


def test_statbel_be_is_a_statistical_datasource(source) -> None:
    assert isinstance(source, DataSource)
    assert source.name == "statbel_be"
    assert source.domain is SourceDomain.STATISTIQUE
    assert source.jurisdiction == "BE"


def test_catalog_lists_all_six_entries(source) -> None:
    ids = {entry.id for entry in source.catalog()}
    assert ids == {
        "sectors-contours",
        "indicators-population",
        "indicators-households",
        "indicators-income",
        "indicators-cars",
        "indicators-building-permits",
    }


# ---------------------------------------------------------------------------
# sectors-contours — geometry entry, VECTOR payload, DOWNLOAD protocol
# ---------------------------------------------------------------------------


def test_sectors_contours_is_vector_download(source) -> None:
    entry = next(e for e in source.catalog() if e.id == "sectors-contours")

    assert entry.access.protocol is AccessProtocol.DOWNLOAD
    assert entry.access.endpoint.startswith("https://statbel.fgov.be/")
    assert entry.access.format == "application/geo+json"
    assert entry.payload is Payload.VECTOR
    assert entry.jurisdiction == "BE"


def test_sectors_contours_metadata_has_crs_and_join_key(source) -> None:
    entry = next(e for e in source.catalog() if e.id == "sectors-contours")
    meta = entry.metadata

    assert meta["crs"] == "EPSG:31370"
    assert meta["join_key"] == "cd_sector"
    assert meta["provider"].startswith("Statbel")
    assert meta["license"] == "Creative Commons Zero (CC0 1.0)"


# ---------------------------------------------------------------------------
# Indicator entries — TABLE payload, DOWNLOAD protocol
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "entry_id",
    [
        "indicators-population",
        "indicators-households",
        "indicators-income",
        "indicators-cars",
    ],
)
def test_indicator_entry_is_table_download_keyed_by_cd_sector(
    source, entry_id: str
) -> None:
    entry = next(e for e in source.catalog() if e.id == entry_id)

    assert entry.access.protocol is AccessProtocol.DOWNLOAD
    assert entry.access.endpoint.startswith("https://statbel.fgov.be/")
    assert entry.payload is Payload.TABLE
    assert entry.metadata["join_key"] == "cd_sector"
    assert entry.metadata["license"] == "Creative Commons Zero (CC0 1.0)"


def test_building_permits_is_table_keyed_by_nis5(source) -> None:
    entry = next(
        e for e in source.catalog() if e.id == "indicators-building-permits"
    )

    assert entry.access.protocol is AccessProtocol.DOWNLOAD
    assert entry.payload is Payload.TABLE
    # Permits are at municipality level, join key differs
    assert entry.metadata["join_key"] == "cd_nis5"
    assert entry.metadata["granularity"] == "municipality"


# ---------------------------------------------------------------------------
# Declarative fetch — delegates to the DOWNLOAD adapter, zero network code
# ---------------------------------------------------------------------------


def test_fetch_sectors_contours_delegates_to_download_adapter() -> None:
    dl = FakeDownload()
    reg = ProtocolRegistry()
    reg.register(dl)
    src = _source_class()(registry=reg)

    result = src.fetch("sectors-contours")

    assert result.payload is Payload.VECTOR
    assert result.data.startswith("https://statbel.fgov.be/")
    assert len(dl.calls) == 1
    assert dl.calls[0].protocol is AccessProtocol.DOWNLOAD


def test_fetch_unknown_entry_raises(source) -> None:
    with pytest.raises(KeyError, match="unknown entry 'ghost'"):
        source.fetch("ghost")


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_schema_sectors_contours_has_geometry_and_crs(source) -> None:
    schema = source.schema("sectors-contours")

    assert schema["cd_sector"] == "str"
    assert schema["geometry"] == "geometry"
    assert schema["crs"] == "EPSG:31370"
    assert schema["cd_nis5"] == "str"
    assert schema["tx_sector_descr_fr"] == "str"


def test_schema_population_has_identity_and_count_fields(source) -> None:
    schema = source.schema("indicators-population")

    assert schema["cd_sector"] == "str"
    assert schema["ms_pop"] == "int"
    assert schema["cd_sex"] == "str"


def test_schema_income_has_mean_and_median(source) -> None:
    schema = source.schema("indicators-income")

    assert schema["ms_mean_total_net_taxable_income"] == "float"
    assert schema["ms_median_total_net_taxable_income"] == "float"
    assert schema["cd_sector"] == "str"


def test_schema_building_permits_has_nis5_and_permits(source) -> None:
    schema = source.schema("indicators-building-permits")

    assert schema["cd_nis5"] == "str"
    assert schema["ms_nb_permits"] == "int"


def test_schema_validates_entry_id(source) -> None:
    with pytest.raises(KeyError, match="unknown entry 'ghost'"):
        source.schema("ghost")


# ---------------------------------------------------------------------------
# revision()
# ---------------------------------------------------------------------------


def test_revision_is_none_for_all_entries(source) -> None:
    for entry in source.catalog():
        assert source.revision(entry.id) is None


def test_revision_validates_entry_id(source) -> None:
    with pytest.raises(KeyError, match="unknown entry 'ghost'"):
        source.revision("ghost")

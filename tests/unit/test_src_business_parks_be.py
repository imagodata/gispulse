"""Unit tests for the gispulse-src-business-parks-be plugin.

Zero-network: the plugin declares Belgian business-park upstreams only.
Core fetchers own HTTP, WFS dispatch and file materialization.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PKG = Path(__file__).resolve().parents[2] / "plugins" / "gispulse-src-business-parks-be"
_PKG_PATH = str(_PKG)
if _PKG_PATH in sys.path:
    sys.path.remove(_PKG_PATH)
sys.path.insert(0, _PKG_PATH)
for _module in (
    "gispulse_src_business_parks_be.source",
    "gispulse_src_business_parks_be",
):
    sys.modules.pop(_module, None)

from gispulse_src_business_parks_be.source import BusinessParksBeSource  # noqa: E402

from gispulse.core.plugin_model import AccessProtocol, Payload, SourceDomain  # noqa: E402
from gispulse.core.sources import DataSource  # noqa: E402

pytestmark = pytest.mark.usefixtures("offline_ssrf")


@pytest.fixture
def source() -> BusinessParksBeSource:
    return BusinessParksBeSource()


def test_pyproject_declares_business_parks_entrypoint_and_manifest() -> None:
    tomllib = pytest.importorskip("tomllib")
    pyproject = tomllib.loads((_PKG / "pyproject.toml").read_text())

    assert pyproject["project"]["entry-points"]["gispulse.data_sources"] == {
        "business_parks_be": "gispulse_src_business_parks_be:register"
    }

    manifest = pyproject["tool"]["gispulse"]["plugin"]
    assert manifest["kind"] == "source"
    assert manifest["domain"] == "foncier"
    assert manifest["jurisdiction"] == "BE"


def test_is_a_belgian_foncier_vector_datasource(source: BusinessParksBeSource) -> None:
    assert isinstance(source, DataSource)
    assert source.name == "business_parks_be"
    assert source.domain is SourceDomain.FONCIER
    assert source.payload is Payload.VECTOR
    assert source.jurisdiction == "BE"


def test_declares_three_real_business_park_upstreams(
    source: BusinessParksBeSource,
) -> None:
    ids = {entry.id for entry in source.entries()}

    assert ids == {
        "vlaio-bedrijventerreinen",
        "spw-economic-perimeters",
        "walspace",
    }


def test_vlaio_entry_uses_registered_wfs_protocol(source: BusinessParksBeSource) -> None:
    entry = {entry.id: entry for entry in source.entries()}["vlaio-bedrijventerreinen"]

    assert entry.access.protocol is AccessProtocol.WFS
    assert entry.access.endpoint == "https://geo.api.vlaanderen.be/Bedrijventerreinen/wfs"
    assert entry.access.format == "application/json"
    assert entry.access.params == {
        "typename": "Bedrijventerreinen:Bedrter",
        "version": "2.0.0",
        "crs": "EPSG:31370",
    }
    assert entry.metadata["provider"] == "VLAIO"
    assert entry.metadata["region"] == "VL"
    assert entry.metadata["canonical_field_map"]["park_id"] == "IDBEDR"


def test_spw_entry_uses_registered_download_protocol(source: BusinessParksBeSource) -> None:
    entry = {entry.id: entry for entry in source.entries()}["spw-economic-perimeters"]

    assert entry.access.protocol is AccessProtocol.DOWNLOAD
    assert entry.access.endpoint == (
        "https://www.odwb.be/api/explore/v2.1/catalog/datasets/"
        "perimetres-de-reconnaissance-economique-wallonie/exports/geojson"
        "?lang=fr&timezone=Europe%2FBrussels"
    )
    assert entry.access.format == "application/geo+json"
    assert entry.access.params == {
        "source_crs": "EPSG:4326",
        "target_crs": "EPSG:31370",
    }
    assert entry.metadata["provider"] == "SPW"
    assert entry.metadata["region"] == "WAL"
    assert entry.metadata["canonical_field_map"]["park_id"] == "codecarto"


def test_walspace_entry_uses_registered_download_protocol(
    source: BusinessParksBeSource,
) -> None:
    entry = {entry.id: entry for entry in source.entries()}["walspace"]

    assert entry.access.protocol is AccessProtocol.DOWNLOAD
    assert entry.access.endpoint == (
        "https://www.odwb.be/api/explore/v2.1/catalog/datasets/"
        "walspace/exports/geojson?lang=fr&timezone=Europe%2FBrussels"
    )
    assert entry.access.format == "application/geo+json"
    assert entry.access.params == {
        "source_crs": "EPSG:4326",
        "target_crs": "EPSG:31370",
    }
    assert entry.metadata["provider"] == "WalSpace"
    assert entry.metadata["region"] == "WAL"
    assert entry.metadata["canonical_field_map"]["park_id"] == "pae_id"


def test_access_for_returns_declared_access_without_mutating_catalog(
    source: BusinessParksBeSource,
) -> None:
    access = source.access_for("spw-economic-perimeters")
    access.params["target_crs"] = "EPSG:4326"

    original = source._entry("spw-economic-perimeters").access
    assert access.protocol is AccessProtocol.DOWNLOAD
    assert original.params["target_crs"] == "EPSG:31370"


def test_schema_exposes_canonical_business_parks_contract(
    source: BusinessParksBeSource,
) -> None:
    schema = source.schema("vlaio-bedrijventerreinen")

    assert schema == {
        "source": "str",
        "region": "str",
        "park_id": "str",
        "name": "str",
        "municipality": "str",
        "use": "str",
        "area_ha": "float",
        "geometry": "geometry[EPSG:31370]",
    }


def test_revision_is_unknown_for_business_park_upstreams(
    source: BusinessParksBeSource,
) -> None:
    assert source.revision("vlaio-bedrijventerreinen") is None
    assert source.revision("spw-economic-perimeters") is None
    assert source.revision("walspace") is None


def test_register_adds_source_to_registry() -> None:
    from gispulse.core.sources import SOURCES
    from gispulse_src_business_parks_be import register

    SOURCES.clear()
    try:
        register()
        assert SOURCES.get("business_parks_be") is not None
    finally:
        SOURCES.clear()

"""Unit tests for the gispulse-src-rnb plugin.

Zero-network: the plugin only declares RNB API AccessSpecs and builds filtered
query specs. Core fetchers own HTTP and materialization.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PKG = Path(__file__).resolve().parents[2] / "plugins" / "gispulse-src-rnb"
_PKG_PATH = str(_PKG)
if _PKG_PATH in sys.path:
    sys.path.remove(_PKG_PATH)
sys.path.insert(0, _PKG_PATH)
for _module in ("gispulse_src_rnb.source", "gispulse_src_rnb"):
    sys.modules.pop(_module, None)

from gispulse_src_rnb.source import RnbSource  # noqa: E402

from gispulse.core.plugin_model import (  # noqa: E402
    AccessProtocol,
    Payload,
    SourceDomain,
)
from gispulse.core.sources import DataSource  # noqa: E402

pytestmark = pytest.mark.usefixtures("offline_ssrf")


@pytest.fixture
def source() -> RnbSource:
    return RnbSource()


def test_pyproject_declares_rnb_entrypoint_and_foncier_manifest() -> None:
    tomllib = pytest.importorskip("tomllib")
    pyproject = tomllib.loads((_PKG / "pyproject.toml").read_text())

    assert pyproject["project"]["entry-points"]["gispulse.data_sources"] == {
        "rnb": "gispulse_src_rnb:register"
    }

    manifest = pyproject["tool"]["gispulse"]["plugin"]
    assert manifest["kind"] == "source"
    assert manifest["domain"] == "foncier"
    assert manifest["jurisdiction"] == "FR"


def test_is_a_foncier_table_datasource(source: RnbSource) -> None:
    assert isinstance(source, DataSource)
    assert source.name == "rnb"
    assert source.domain is SourceDomain.FONCIER
    assert source.payload is Payload.TABLE
    assert source.jurisdiction == "FR"


def test_declares_building_lookup_entries(source: RnbSource) -> None:
    ids = {entry.id for entry in source.entries()}

    assert ids == {
        "buildings-bbox",
        "buildings-parcelle",
        "buildings-address",
    }


def test_buildings_bbox_entry_uses_rnb_rest_table_with_dept63_default(
    source: RnbSource,
) -> None:
    entry = {entry.id: entry for entry in source.entries()}["buildings-bbox"]

    assert entry.access.protocol is AccessProtocol.REST_TABLE
    assert entry.access.endpoint == "https://rnb-api.beta.gouv.fr/api/alpha/buildings/"
    assert entry.access.format == "application/json"
    assert entry.access.params["query"] == {
        "bbox": "3.0885,45.7943,3.0895,45.7950",
        "limit": 100,
        "withPlots": 1,
    }
    assert entry.access.params["pagination"] == {
        "data_key": "results",
        "next_key": "next",
        "max_pages": 10,
        "max_rows": 1000,
    }
    assert entry.metadata["default_departement"] == "63"
    assert entry.metadata["default_insee_code"] == "63113"
    assert entry.metadata["query_kind"] == "bbox"
    assert entry.metadata["join_keys"] == ("rnb_id", "addresses.id", "plots.id")


def test_parcelle_entry_uses_plot_path_template_with_real_dept63_plot(
    source: RnbSource,
) -> None:
    entry = {entry.id: entry for entry in source.entries()}["buildings-parcelle"]

    assert entry.access.protocol is AccessProtocol.REST_TABLE
    assert entry.access.endpoint == (
        "https://rnb-api.beta.gouv.fr/api/alpha/buildings/plot/{plot_id}/"
    )
    assert entry.access.params["plot_id"] == "63113000MT0158"
    assert entry.access.params["query"] == {"limit": 100}
    assert entry.metadata["query_kind"] == "parcelle"
    assert entry.metadata["default_plot_id"] == "63113000MT0158"


def test_address_entry_uses_address_endpoint_and_ban_key_default(
    source: RnbSource,
) -> None:
    entry = {entry.id: entry for entry in source.entries()}["buildings-address"]

    assert entry.access.protocol is AccessProtocol.REST_TABLE
    assert entry.access.endpoint == (
        "https://rnb-api.beta.gouv.fr/api/alpha/buildings/address/"
    )
    assert entry.access.params["query"] == {
        "cle_interop_ban": "63113_2615_00089",
        "limit": 100,
    }
    assert entry.metadata["query_kind"] == "address"
    assert entry.metadata["default_address"] == "89 rue Lecuelle, 63100 Clermont-Ferrand"


def test_access_for_bbox_overrides_query_without_mutating_catalog(
    source: RnbSource,
) -> None:
    access = source.access_for(
        "buildings-bbox",
        bbox="2.424525,48.839201,2.434158,48.845782",
        status=("constructed", "demolished"),
        with_plots=False,
        limit=25,
        s3_key="raw/rnb/paris.jsonl",
    )
    original = source._entry("buildings-bbox").access

    assert access.params["query"] == {
        "bbox": "2.424525,48.839201,2.434158,48.845782",
        "limit": 25,
        "status": "constructed,demolished",
        "withPlots": 0,
    }
    assert access.params["pagination"]["max_rows"] == 25
    assert access.params["s3_key"] == "raw/rnb/paris.jsonl"
    assert original.params["query"]["bbox"] == "3.0885,45.7943,3.0895,45.7950"


def test_access_for_plot_replaces_path_parameter_and_materialization(
    source: RnbSource,
) -> None:
    access = source.access_for(
        "buildings-parcelle",
        plot_id="63113000MT0434",
        limit=10,
        local_path="/tmp/rnb-plot.jsonl",
    )

    assert access.endpoint.endswith("/plot/{plot_id}/")
    assert access.params["plot_id"] == "63113000MT0434"
    assert access.params["query"] == {"limit": 10}
    assert access.params["local_path"] == "/tmp/rnb-plot.jsonl"


def test_access_for_address_prefers_ban_key_over_text_query(
    source: RnbSource,
) -> None:
    access = source.access_for(
        "buildings-address",
        q="ignored when cle_interop_ban is supplied",
        cle_interop_ban="63113_2615_00089",
        min_score=0.9,
        limit=1,
    )

    assert access.params["query"] == {
        "cle_interop_ban": "63113_2615_00089",
        "limit": 1,
        "min_score": 0.9,
    }
    assert access.params["pagination"]["max_rows"] == 1


def test_access_for_rejects_unbounded_limit(source: RnbSource) -> None:
    with pytest.raises(ValueError, match="limit"):
        source.access_for("buildings-bbox", limit=101)


def test_schema_exposes_identity_geometry_and_join_fields(source: RnbSource) -> None:
    schema = source.schema("buildings-bbox")

    assert schema["rnb_id"] == "str"
    assert schema["status"] == "str"
    assert schema["point"] == "geojson-point"
    assert schema["shape"] == "geojson-geometry"
    assert schema["addresses"] == "list[json]"
    assert schema["addresses.id"] == "str"
    assert schema["plots"] == "list[json]"
    assert schema["plots.id"] == "str"
    assert schema["bdg_cover_ratio"] == "float"


def test_revision_is_unknown_for_query_scoped_api_entries(source: RnbSource) -> None:
    assert source.revision("buildings-bbox") is None
    assert source.revision("buildings-parcelle") is None


def test_register_adds_source_to_registry() -> None:
    from gispulse.core.sources import SOURCES
    from gispulse_src_rnb import register

    SOURCES.clear()
    try:
        register()
        assert SOURCES.get("rnb") is not None
    finally:
        SOURCES.clear()

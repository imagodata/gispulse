"""Unit tests for the gispulse-src-georisques plugin (#196).

Zero-network: the plugin only *declares* AccessSpecs against REST_TABLE and
builds per-query AccessSpecs via :meth:`GeorisquesSource.access_for`. The
actual fetch is the core ``RestTableFetcher``'s job (tested elsewhere).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PKG = Path(__file__).resolve().parents[2] / "plugins" / "gispulse-src-georisques"
if str(_PKG) not in sys.path:
    sys.path.insert(0, str(_PKG))

from gispulse_src_georisques.source import GeorisquesSource  # noqa: E402

from gispulse.core.plugin_model import (  # noqa: E402
    AccessProtocol,
    Payload,
    SourceDomain,
)
from gispulse.core.sources import DataSource  # noqa: E402

_CODE_INSEE_ENTRIES = {"gaspar-risques", "radon", "sismicite"}
_LATLON_ENTRIES = {"rga", "tri-zonage", "ssp"}


@pytest.fixture
def source() -> GeorisquesSource:
    return GeorisquesSource()


def test_is_a_datasource(source: GeorisquesSource) -> None:
    assert isinstance(source, DataSource)
    assert source.name == "georisques"
    assert source.domain is SourceDomain.ENVIRONNEMENT
    assert source.payload is Payload.TABLE
    assert source.jurisdiction == "FR"


def test_declares_six_entries(source: GeorisquesSource) -> None:
    ids = {e.id for e in source.entries()}
    assert ids == _CODE_INSEE_ENTRIES | _LATLON_ENTRIES


def test_all_entries_use_rest_table(source: GeorisquesSource) -> None:
    for entry in source.entries():
        assert entry.access.protocol is AccessProtocol.REST_TABLE
        assert entry.access.endpoint.startswith("https://www.georisques.gouv.fr/")


def test_query_key_metadata(source: GeorisquesSource) -> None:
    by_id = {e.id: e for e in source.entries()}
    for entry_id in _CODE_INSEE_ENTRIES:
        assert by_id[entry_id].metadata["query_key"] == "code_insee"
    for entry_id in _LATLON_ENTRIES:
        assert by_id[entry_id].metadata["query_key"] == "latlon"


def test_rga_and_ssp_use_object_row_shape(source: GeorisquesSource) -> None:
    by_id = {e.id: e for e in source.entries()}
    for entry_id in ("rga", "ssp"):
        assert by_id[entry_id].access.params["pagination"]["row_shape"] == "object"


def test_rga_treats_empty_body_as_empty(source: GeorisquesSource) -> None:
    by_id = {e.id: e for e in source.entries()}
    assert by_id["rga"].access.params["pagination"]["empty_body_is_empty"] is True


def test_tri_treats_404_as_empty(source: GeorisquesSource) -> None:
    by_id = {e.id: e for e in source.entries()}
    assert 404 in by_id["tri-zonage"].access.params["pagination"]["empty_statuses"]


def test_access_for_merges_code_insee(source: GeorisquesSource) -> None:
    access = source.access_for("radon", code_insee="63113", local_path="/tmp/r.jsonl")
    assert access.protocol is AccessProtocol.REST_TABLE
    assert access.params["query"]["code_insee"] == "63113"
    assert access.params["local_path"] == "/tmp/r.jsonl"


def test_access_for_merges_latlon_and_preserves_static_query(
    source: GeorisquesSource,
) -> None:
    access = source.access_for("ssp", latlon="3.08,45.77", local_path="/tmp/ssp.jsonl")
    assert access.params["query"]["latlon"] == "3.08,45.77"
    assert access.params["query"]["rayon"] == 500
    assert access.params["query"]["page_size"] == 5
    assert access.params["pagination"]["row_shape"] == "object"


def test_access_for_unknown_entry_raises(source: GeorisquesSource) -> None:
    with pytest.raises(KeyError, match="unknown_entry"):
        source.access_for("unknown_entry", code_insee="x")


def test_access_for_rejects_wrong_query_arg(source: GeorisquesSource) -> None:
    # radon is a code_insee endpoint — passing latlon is a usage error
    with pytest.raises(ValueError):
        source.access_for("radon", latlon="3.08,45.77")


def test_access_for_copies_nested_pagination(source: GeorisquesSource) -> None:
    first = source.access_for("ssp", latlon="3.08,45.77")
    first.params["pagination"]["row_shape"] = "mutated"

    second = source.access_for("ssp", latlon="3.09,45.78")
    entry = source._entry("ssp")

    assert first.params["query"]["latlon"] == "3.08,45.77"
    assert second.params["query"]["latlon"] == "3.09,45.78"
    assert second.params["pagination"]["row_shape"] == "object"
    assert entry.access.params["pagination"]["row_shape"] == "object"


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


def test_revision_is_none(source: GeorisquesSource) -> None:
    assert source.revision("radon") is None


def test_register_adds_source_to_registry() -> None:
    from gispulse.core.sources import SOURCES
    from gispulse_src_georisques import register

    register()
    assert SOURCES.get("georisques") is not None

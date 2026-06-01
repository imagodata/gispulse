"""Unit tests for the gispulse-src-dpe plugin.

Zero-network: the plugin only *declares* API AccessSpecs against REST_TABLE
and builds per-query AccessSpecs via :meth:`DpeSource.access_for`. The
actual fetch is handled by core fetchers (tested elsewhere).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PKG = Path(__file__).resolve().parents[2] / "plugins" / "gispulse-src-dpe"
_PKG_PATH = str(_PKG)
if _PKG_PATH in sys.path:
    sys.path.remove(_PKG_PATH)
sys.path.insert(0, _PKG_PATH)
for _module in ("gispulse_src_dpe.source", "gispulse_src_dpe"):
    sys.modules.pop(_module, None)

from gispulse_src_dpe.source import (  # noqa: E402
    _CODE_DEPARTEMENT_RE,
    _CODE_INSEE_RE,
    DpeSource,
)

from gispulse.core.plugin_model import (  # noqa: E402
    AccessProtocol,
    FetchMode,
    Payload,
    SourceDomain,
    SourceResult,
)
from gispulse.core.sources import DataSource, ProtocolRegistry  # noqa: E402

pytestmark = pytest.mark.usefixtures("offline_ssrf")

_ENTRY_IDS = {"logements-existants", "logements-neufs"}

_ADEME_BASE = "https://data.ademe.fr/data-fair/api/v1/datasets"
_DATASET_EXISTANTS = "meg-83tjwtg8dyz4vv7h1dqe"
_DATASET_NEUFS = "g3cgx7jb3cmys5voxz1mrm22"


class FakeRestTable:
    """Records resolved DPE REST_TABLE AccessSpecs."""

    protocol = AccessProtocol.REST_TABLE

    def __init__(self) -> None:
        self.calls: list = []

    def fetch(self, access, *, extent=None, mode=FetchMode.MATERIALIZE):
        self.calls.append(access)
        return SourceResult(payload=Payload.TABLE, mode=mode, data=access.endpoint)


class _FakeResponse:
    """Minimal ``httpx.Response`` stand-in for revision() tests."""

    def __init__(
        self,
        json_body: dict | None = None,
        *,
        status_code: int = 200,
    ) -> None:
        self._json = json_body or {}
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self._json


@pytest.fixture
def source() -> DpeSource:
    reg = ProtocolRegistry()
    reg.register(FakeRestTable())
    return DpeSource(registry=reg)


# ---------------------------------------------------------------------------
# Contract conformance
# ---------------------------------------------------------------------------


def test_is_a_datasource(source: DpeSource) -> None:
    assert isinstance(source, DataSource)
    assert source.name == "dpe"
    assert source.domain is SourceDomain.STATISTIQUE
    assert source.payload is Payload.TABLE
    assert source.jurisdiction == "FR"


def test_declares_two_entries(source: DpeSource) -> None:
    ids = {e.id for e in source.entries()}
    assert ids == _ENTRY_IDS


# ---------------------------------------------------------------------------
# AccessSpec — REST_TABLE against ADEME data-fair /lines
# ---------------------------------------------------------------------------


def test_entries_use_rest_table(source: DpeSource) -> None:
    for entry in source.entries():
        assert entry.access.protocol is AccessProtocol.REST_TABLE


def test_existants_endpoint_targets_ademe_lines(source: DpeSource) -> None:
    by_id = {e.id: e for e in source.entries()}
    ep = by_id["logements-existants"].access.endpoint
    assert ep == f"{_ADEME_BASE}/{_DATASET_EXISTANTS}/lines"


def test_neufs_endpoint_targets_ademe_lines(source: DpeSource) -> None:
    by_id = {e.id: e for e in source.entries()}
    ep = by_id["logements-neufs"].access.endpoint
    assert ep == f"{_ADEME_BASE}/{_DATASET_NEUFS}/lines"


def test_pagination_uses_results_and_next_keys(source: DpeSource) -> None:
    for entry in source.entries():
        pagination = entry.access.params["pagination"]
        assert pagination["data_key"] == "results"
        assert pagination["next_key"] == "next"


def test_static_query_sets_page_size(source: DpeSource) -> None:
    for entry in source.entries():
        assert entry.access.params["query"]["size"] == 10000


def test_entries_set_rest_pagination_guards(source: DpeSource) -> None:
    for entry in source.entries():
        params = entry.access.params
        assert params["timeout"] == 20.0
        assert params["pagination"]["max_pages"] == 1000
        assert params["pagination"]["max_total_seconds"] == 600.0
        assert params["retry"] == {
            "max_attempts": 4,
            "backoff_seconds": 1.0,
            "backoff_factor": 2.0,
            "statuses": [429, 500, 502, 503, 504],
        }


# ---------------------------------------------------------------------------
# access_for — builds the Lucene qs filter
# ---------------------------------------------------------------------------


def test_access_for_code_insee_builds_qs_filter(source: DpeSource) -> None:
    access = source.access_for("logements-existants", code_insee="63113")
    assert access.protocol is AccessProtocol.REST_TABLE
    assert access.params["query"]["qs"] == "code_insee_ban:63113"


def test_access_for_code_departement_builds_qs_filter(source: DpeSource) -> None:
    access = source.access_for("logements-existants", code_departement="63")
    assert access.params["query"]["qs"] == "code_departement_ban:63"


@pytest.mark.parametrize(
    ("code_insee", "expected_qs"),
    [
        ("63113", "code_insee_ban:63113"),
        ("2a004", "code_insee_ban:2A004"),
        ("97411", "code_insee_ban:97411"),
    ],
)
def test_access_for_accepts_canonical_code_insee(
    source: DpeSource,
    code_insee: str,
    expected_qs: str,
) -> None:
    access = source.access_for("logements-existants", code_insee=code_insee)
    assert access.params["query"]["qs"] == expected_qs


@pytest.mark.parametrize("code_departement", ["2A", "2B", "974"])
def test_access_for_accepts_canonical_department_codes(
    source: DpeSource,
    code_departement: str,
) -> None:
    access = source.access_for(
        "logements-existants", code_departement=code_departement
    )
    assert access.params["query"]["qs"] == f"code_departement_ban:{code_departement}"


def test_access_for_normalizes_corsica_department_code(source: DpeSource) -> None:
    access = source.access_for("logements-neufs", code_departement="2a")
    assert access.params["query"]["qs"] == "code_departement_ban:2A"


def test_access_for_code_insee_takes_precedence_over_departement(
    source: DpeSource,
) -> None:
    access = source.access_for(
        "logements-existants", code_insee="63113", code_departement="63"
    )
    assert access.params["query"]["qs"] == "code_insee_ban:63113"


def test_access_for_preserves_static_page_size(source: DpeSource) -> None:
    access = source.access_for("logements-existants", code_insee="63113")
    assert access.params["query"]["size"] == 10000


def test_access_for_preserves_pagination_spec(source: DpeSource) -> None:
    access = source.access_for("logements-existants", code_insee="63113")
    assert access.params["pagination"]["data_key"] == "results"
    assert access.params["pagination"]["next_key"] == "next"


def test_access_for_accepts_local_path(source: DpeSource) -> None:
    access = source.access_for(
        "logements-existants", code_insee="63113", local_path="/tmp/dpe.jsonl"
    )
    assert access.params["local_path"] == "/tmp/dpe.jsonl"


def test_access_for_accepts_s3_key(source: DpeSource) -> None:
    access = source.access_for(
        "logements-neufs",
        code_departement="75",
        s3_key="raw/dpe/neufs/75.jsonl",
    )
    assert access.params["query"]["qs"] == "code_departement_ban:75"
    assert access.params["s3_key"] == "raw/dpe/neufs/75.jsonl"


def test_access_for_without_spatial_key_raises(source: DpeSource) -> None:
    with pytest.raises(ValueError, match="code_insee"):
        source.access_for("logements-existants")


def test_access_for_rejects_unsafe_code_insee_filter(source: DpeSource) -> None:
    with pytest.raises(ValueError, match="Invalid code_insee"):
        source.access_for("logements-existants", code_insee="63113 OR *:*")


def test_access_for_rejects_unsafe_department_filter(source: DpeSource) -> None:
    with pytest.raises(ValueError, match="Invalid code_departement"):
        source.access_for("logements-existants", code_departement="63 OR *:*")


@pytest.mark.parametrize("code_insee", ["20123", "6311"])
def test_access_for_rejects_non_canonical_code_insee(
    source: DpeSource,
    code_insee: str,
) -> None:
    with pytest.raises(ValueError, match="Invalid code_insee"):
        source.access_for("logements-existants", code_insee=code_insee)


def test_access_for_rejects_non_canonical_department_code(source: DpeSource) -> None:
    with pytest.raises(ValueError, match="Invalid code_departement"):
        source.access_for("logements-existants", code_departement="20")


@pytest.mark.parametrize("code_insee", ["63113", "2A004", "97411"])
def test_code_insee_regex_accepts_canonical_commune_codes(code_insee: str) -> None:
    assert _CODE_INSEE_RE.fullmatch(code_insee)


@pytest.mark.parametrize("code_insee", ["20123", "6311"])
def test_code_insee_regex_rejects_non_canonical_commune_codes(
    code_insee: str,
) -> None:
    assert _CODE_INSEE_RE.fullmatch(code_insee) is None


@pytest.mark.parametrize("code_departement", ["63", "2A", "2B", "974"])
def test_department_regex_accepts_canonical_department_codes(
    code_departement: str,
) -> None:
    assert _CODE_DEPARTEMENT_RE.fullmatch(code_departement)


@pytest.mark.parametrize("code_departement", ["20", "6311"])
def test_department_regex_rejects_non_canonical_department_codes(
    code_departement: str,
) -> None:
    assert _CODE_DEPARTEMENT_RE.fullmatch(code_departement) is None


def test_access_for_unknown_entry_raises(source: DpeSource) -> None:
    with pytest.raises(KeyError, match="unknown_entry"):
        source.access_for("unknown_entry", code_insee="63113")


def test_access_for_does_not_mutate_shared_entry(source: DpeSource) -> None:
    """Two consecutive access_for calls must not share mutable query dicts."""
    first = source.access_for("logements-existants", code_insee="63113")
    first.params["query"]["qs"] = "MUTATED"

    second = source.access_for("logements-existants", code_insee="69123")
    entry = source._entry("logements-existants")

    # second must be unaffected by mutating first
    assert second.params["query"]["qs"] == "code_insee_ban:69123"
    # the canonical entry must not carry qs — it was not in the original spec
    assert "qs" not in entry.access.params["query"]


def test_access_for_deep_copies_nested_params(source: DpeSource) -> None:
    """Future nested REST_TABLE params must not leak through shallow copies."""
    entry = source._entry("logements-existants")
    entry.access.params["pagination"]["cursor"] = {"field": "_i"}

    first = source.access_for("logements-existants", code_insee="63113")
    first.params["pagination"]["cursor"]["field"] = "MUTATED"

    second = source.access_for("logements-existants", code_insee="69123")

    assert second.params["pagination"]["cursor"]["field"] == "_i"
    assert entry.access.params["pagination"]["cursor"]["field"] == "_i"


def test_access_for_neufs_entry(source: DpeSource) -> None:
    access = source.access_for("logements-neufs", code_insee="75056")
    assert access.endpoint == f"{_ADEME_BASE}/{_DATASET_NEUFS}/lines"
    assert access.params["query"]["qs"] == "code_insee_ban:75056"


def test_fetch_delegates_to_rest_table_adapter() -> None:
    """fetch() must hand the AccessSpec to the REST_TABLE adapter unchanged."""
    adapter = FakeRestTable()
    reg = ProtocolRegistry()
    reg.register(adapter)
    src = DpeSource(registry=reg)

    result = src.fetch("logements-existants")

    assert result.payload is Payload.TABLE
    assert _DATASET_EXISTANTS in result.data
    assert len(adapter.calls) == 1
    assert adapter.calls[0].protocol is AccessProtocol.REST_TABLE
    assert adapter.calls[0].endpoint.startswith(_ADEME_BASE)


# ---------------------------------------------------------------------------
# schema — key columns declared
# ---------------------------------------------------------------------------


def test_schema_exposes_etiquette_dpe(source: DpeSource) -> None:
    for entry_id in _ENTRY_IDS:
        assert source.schema(entry_id)["etiquette_dpe"] == "str"


def test_schema_exposes_etiquette_ges(source: DpeSource) -> None:
    for entry_id in _ENTRY_IDS:
        assert source.schema(entry_id)["etiquette_ges"] == "str"


def test_schema_exposes_conso_ep(source: DpeSource) -> None:
    for entry_id in _ENTRY_IDS:
        schema = source.schema(entry_id)
        assert schema["conso_5_usages_ep"] == "float"
        assert schema["conso_5_usages_par_m2_ep"] == "float"


def test_schema_exposes_emission_ges(source: DpeSource) -> None:
    for entry_id in _ENTRY_IDS:
        schema = source.schema(entry_id)
        assert schema["emission_ges_5_usages"] == "float"
        assert schema["emission_ges_5_usages_par_m2"] == "float"


def test_schema_exposes_surface_habitable(source: DpeSource) -> None:
    for entry_id in _ENTRY_IDS:
        assert source.schema(entry_id)["surface_habitable_logement"] == "float"


def test_schema_exposes_geo_join_fields(source: DpeSource) -> None:
    for entry_id in _ENTRY_IDS:
        schema = source.schema(entry_id)
        assert schema["code_insee_ban"] == "str"
        assert schema["code_departement_ban"] == "str"
        assert schema["identifiant_ban"] == "str"
        assert schema["coordonnee_cartographique_x_ban"] == "float"
        assert schema["coordonnee_cartographique_y_ban"] == "float"
        assert schema["statut_geocodage"] == "str"


def test_schema_exposes_incremental_and_address_fields(source: DpeSource) -> None:
    for entry_id in _ENTRY_IDS:
        schema = source.schema(entry_id)
        assert schema["date_derniere_modification_dpe"] == "str"
        assert schema["score_ban"] == "float"
        assert schema["nom_commune_ban"] == "str"
        assert schema["code_postal_ban"] == "str"
        assert schema["code_region_ban"] == "str"
        assert schema["adresse_complete_brut"] == "str"
        assert schema["id_rnb"] == "str"
        assert schema["provenance_id_rnb"] == "str"
        assert schema["_geopoint"] == "str"


def test_schema_unknown_entry_raises(source: DpeSource) -> None:
    with pytest.raises(KeyError):
        source.schema("ghost")


# ---------------------------------------------------------------------------
# metadata — dataset_id, filter_field
# ---------------------------------------------------------------------------


def test_metadata_carries_dataset_ids(source: DpeSource) -> None:
    by_id = {e.id: e for e in source.entries()}
    assert by_id["logements-existants"].metadata["dataset_id"] == _DATASET_EXISTANTS
    assert by_id["logements-neufs"].metadata["dataset_id"] == _DATASET_NEUFS


def test_metadata_carries_filter_fields(source: DpeSource) -> None:
    for entry in source.entries():
        assert entry.metadata["filter_field"] == "code_insee_ban"
        assert entry.metadata["dept_field"] == "code_departement_ban"


def test_metadata_carries_geo_join_note(source: DpeSource) -> None:
    for entry in source.entries():
        assert "code_insee_ban" in entry.metadata["geo_join_note"]


def test_metadata_carries_geometry_mapping(source: DpeSource) -> None:
    for entry in source.entries():
        geometry = entry.metadata["geometry"]
        assert geometry["type"] == "point"
        assert geometry["x_field"] == "coordonnee_cartographique_x_ban"
        assert geometry["y_field"] == "coordonnee_cartographique_y_ban"
        assert geometry["crs"] == "EPSG:2154"
        assert geometry["quality_field"] == "statut_geocodage"
        assert geometry["recommended_quality_value"] == "adresse géocodée ban à l'adresse"
        assert geometry["missing_coordinates"] == "preserve_row_without_geometry"


def test_entries_do_not_share_nested_metadata(source: DpeSource) -> None:
    entries = {e.id: e for e in source.entries()}
    entries["logements-existants"].metadata["geometry"]["x_field"] = "MUTATED"

    fresh_entries = {e.id: e for e in source.entries()}
    assert (
        fresh_entries["logements-neufs"].metadata["geometry"]["x_field"]
        == "coordonnee_cartographique_x_ban"
    )


def test_metadata_carries_ademe_decommission_notice(source: DpeSource) -> None:
    for entry in source.entries():
        notice = entry.metadata["upstream_churn_risk"]
        assert notice["status"] == "decommissioning-announced"
        assert notice["notice_url"] == "https://data.ademe.fr/pages/dpe"
        assert "dataset URLs" in notice["risk"]


# ---------------------------------------------------------------------------
# revision() — GET on the ADEME data-fair dataset metadata API
# ---------------------------------------------------------------------------


def test_revision_returns_data_updated_at(
    source: DpeSource, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[str] = []

    def fake_get(url, **_kw):
        captured.append(url)
        return _FakeResponse({"dataUpdatedAt": "2026-05-28T16:14:29.077Z"})

    monkeypatch.setattr("httpx.get", fake_get)
    token = source.revision("logements-existants")

    assert token == "2026-05-28T16:14:29.077Z"
    assert any(_DATASET_EXISTANTS in url for url in captured)


def test_revision_uses_correct_dataset_id_for_neufs(
    source: DpeSource, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[str] = []

    def fake_get(url, **_kw):
        captured.append(url)
        return _FakeResponse({"dataUpdatedAt": "2026-05-27T21:16:34.334Z"})

    monkeypatch.setattr("httpx.get", fake_get)
    token = source.revision("logements-neufs")

    assert token == "2026-05-27T21:16:34.334Z"
    assert any(_DATASET_NEUFS in url for url in captured)


def test_revision_returns_none_on_network_error(
    source: DpeSource, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_get(url, **_kw):
        raise RuntimeError("simulated DNS failure")

    monkeypatch.setattr("httpx.get", fake_get)
    assert source.revision("logements-existants") is None


def test_revision_returns_none_when_field_missing(
    source: DpeSource, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_get(url, **_kw):
        return _FakeResponse({"updatedAt": "2026-03-24T13:36:53.484Z"})

    monkeypatch.setattr("httpx.get", fake_get)
    assert source.revision("logements-existants") is None


def test_revision_returns_none_on_non_2xx(
    source: DpeSource, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_get(url, **_kw):
        return _FakeResponse({}, status_code=503)

    monkeypatch.setattr("httpx.get", fake_get)
    assert source.revision("logements-existants") is None


# ---------------------------------------------------------------------------
# register() — adds source to the process-wide registry
# ---------------------------------------------------------------------------


def test_register_adds_source_to_registry() -> None:
    from gispulse.core.sources import SOURCES
    from gispulse_src_dpe import register

    register()
    assert SOURCES.get("dpe") is not None

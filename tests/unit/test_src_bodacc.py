"""Unit tests for the gispulse-src-bodacc plugin.

Zero-network: the plugin only declares BODACC OpenDataSoft AccessSpecs and
builds filtered query specs. Core fetchers own HTTP and materialization.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PKG = Path(__file__).resolve().parents[2] / "plugins" / "gispulse-src-bodacc"
_PKG_PATH = str(_PKG)
if _PKG_PATH in sys.path:
    sys.path.remove(_PKG_PATH)
sys.path.insert(0, _PKG_PATH)
for _module in ("gispulse_src_bodacc.source", "gispulse_src_bodacc"):
    sys.modules.pop(_module, None)

from gispulse_src_bodacc.source import BodaccSource  # noqa: E402

from gispulse.core.plugin_model import (  # noqa: E402
    AccessProtocol,
    Payload,
    SourceDomain,
)
from gispulse.core.sources import DataSource  # noqa: E402

pytestmark = pytest.mark.usefixtures("offline_ssrf")


class _FakeResponse:
    """Minimal ``httpx.Response`` stand-in for revision() tests."""

    def __init__(
        self,
        payload: dict | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._payload = payload or {}
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


@pytest.fixture
def source() -> BodaccSource:
    return BodaccSource()


def test_is_a_datasource(source: BodaccSource) -> None:
    assert isinstance(source, DataSource)
    assert source.name == "bodacc"
    assert source.domain is SourceDomain.STATISTIQUE
    assert source.payload is Payload.TABLE
    assert source.jurisdiction == "FR"


def test_declares_commercial_notice_entries(source: BodaccSource) -> None:
    ids = {e.id for e in source.entries()}

    assert ids == {
        "annonces-commerciales",
        "ventes-cessions",
        "procedures-collectives",
        "immatriculations",
        "creations-etablissements",
        "modifications",
        "radiations",
        "conciliations",
        "retablissements-professionnels",
    }


def test_entries_use_bodacc_opendatasoft_rest_table(source: BodaccSource) -> None:
    by_id = {e.id: e for e in source.entries()}
    entry = by_id["ventes-cessions"]

    assert entry.access.protocol is AccessProtocol.REST_TABLE
    assert entry.access.endpoint == (
        "https://bodacc-datadila.opendatasoft.com/api/explore/v2.1/"
        "catalog/datasets/annonces-commerciales/records"
    )
    assert entry.access.params["pagination"] == {
        "data_key": "results",
        "max_pages": 1,
        "max_rows": 100,
    }
    assert entry.access.params["query"]["limit"] == 100
    assert entry.access.params["query"]["offset"] == 0
    assert entry.access.params["query"]["where"] == 'familleavis="vente"'
    assert entry.metadata["dataset_id"] == "annonces-commerciales"
    assert entry.metadata["ods_pagination"] == "limit-offset"
    assert entry.metadata["core_fetcher_note"] == (
        "REST_TABLE currently materializes one ODS page; pass offset for more."
    )


def test_entry_metadata_records_real_family_codes(source: BodaccSource) -> None:
    by_id = {e.id: e for e in source.entries()}

    assert by_id["ventes-cessions"].metadata["familleavis"] == "vente"
    assert by_id["procedures-collectives"].metadata["familleavis"] == "collective"
    assert by_id["conciliations"].metadata["familleavis"] == "conciliation"
    assert (
        by_id["retablissements-professionnels"].metadata["familleavis"]
        == "retablissement_professionnel"
    )
    assert by_id["annonces-commerciales"].metadata["familleavis"] is None


def test_access_for_merges_siren_commune_and_dates(source: BodaccSource) -> None:
    access = source.access_for(
        "ventes-cessions",
        siren="514 395 532",
        commune="Rieulay",
        code_postal="59870",
        date_from="2009-01-01",
        date_to="2009-12-31",
        local_path="/tmp/bodacc.jsonl",
    )

    assert access.protocol is AccessProtocol.REST_TABLE
    assert access.params["query"]["limit"] == 100
    assert access.params["query"]["offset"] == 0
    assert access.params["query"]["where"] == (
        'familleavis="vente" AND registre="514395532" AND ville="Rieulay" '
        "AND cp=\"59870\" AND dateparution>=date'2009-01-01' "
        "AND dateparution<=date'2009-12-31'"
    )
    assert access.params["local_path"] == "/tmp/bodacc.jsonl"


def test_access_for_accepts_siret_by_searching_siret_and_siren_prefix(
    source: BodaccSource,
) -> None:
    access = source.access_for("procedures-collectives", siret="51439553200016")

    assert access.params["query"]["where"] == (
        'familleavis="collective" AND '
        '(search("51439553200016") OR registre="514395532")'
    )


def test_access_for_supports_department_offset_limit_and_s3_key(
    source: BodaccSource,
) -> None:
    access = source.access_for(
        "annonces-commerciales",
        departement="59",
        offset=200,
        limit=25,
        s3_key="raw/bodacc/59/page-200.jsonl",
    )

    assert access.params["query"] == {
        "limit": 25,
        "offset": 200,
        "where": 'numerodepartement="59"',
    }
    assert access.params["s3_key"] == "raw/bodacc/59/page-200.jsonl"
    assert access.params["pagination"]["max_rows"] == 25


def test_access_for_copies_nested_params(source: BodaccSource) -> None:
    first = source.access_for("ventes-cessions", siren="514395532")
    first.params["pagination"]["data_key"] = "mutated"

    second = source.access_for("ventes-cessions", siren="752461681")
    entry = source._entry("ventes-cessions")

    assert second.params["pagination"]["data_key"] == "results"
    assert entry.access.params["pagination"]["data_key"] == "results"


def test_access_for_rejects_unbounded_limit(source: BodaccSource) -> None:
    with pytest.raises(ValueError, match="limit"):
        source.access_for("ventes-cessions", limit=101)


def test_access_for_unknown_entry_raises(source: BodaccSource) -> None:
    with pytest.raises(KeyError, match="unknown_entry"):
        source.access_for("unknown_entry", siren="514395532")


def test_schema_exposes_raw_bodacc_columns(source: BodaccSource) -> None:
    schema = source.schema("procedures-collectives")

    assert schema["registre"] == "list[str]"
    assert schema["ville"] == "str"
    assert schema["jugement"] == "json-string"
    assert schema["acte"] == "json-string"
    assert "normalized_signal" not in schema


def test_revision_reads_dataset_processed_timestamp(
    source: BodaccSource,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[str] = []

    def fake_get(url, **_kw):
        captured.append(url)
        return _FakeResponse(
            {
                "metas": {
                    "default": {
                        "data_processed": "2026-05-31T06:16:26.303000+00:00",
                        "metadata_processed": "2026-06-01T01:13:20.035000+00:00",
                    }
                }
            }
        )

    monkeypatch.setattr("httpx.get", fake_get)

    assert source.revision("ventes-cessions") == "2026-05-31T06:16:26.303000+00:00"
    assert captured == [
        "https://bodacc-datadila.opendatasoft.com/api/explore/v2.1/"
        "catalog/datasets/annonces-commerciales"
    ]


def test_revision_falls_back_to_last_modified_header(
    source: BodaccSource,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_get(url, **_kw):
        return _FakeResponse(headers={"last-modified": "Mon, 01 Jun 2026 01:13:20 GMT"})

    monkeypatch.setattr("httpx.get", fake_get)
    assert source.revision("procedures-collectives") == (
        "Mon, 01 Jun 2026 01:13:20 GMT"
    )


def test_register_adds_source_to_registry() -> None:
    from gispulse.core.sources import SOURCES
    from gispulse_src_bodacc import register

    register()
    assert SOURCES.get("bodacc") is not None

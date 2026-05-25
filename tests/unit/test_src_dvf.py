"""Unit tests for the gispulse-src-dvf pilot plugin (issue #184, wave 2)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PKG = Path(__file__).resolve().parents[2] / "plugins" / "gispulse-src-dvf"
if str(_PKG) not in sys.path:
    sys.path.insert(0, str(_PKG))

from gispulse_src_dvf.source import (  # noqa: E402
    DvfSource,
    dvf_registry,
    resolve_dvf_scan,
)

from gispulse.core.plugin_model import (  # noqa: E402
    AccessProtocol,
    FetchMode,
    Payload,
    SourceDomain,
    SourceResult,
)
from gispulse.core.sources import DataSource, ProtocolRegistry  # noqa: E402


class FakeRemoteTable:
    """Records the AccessSpec it is handed and returns a marker result."""

    protocol = AccessProtocol.REMOTE_TABLE

    def __init__(self) -> None:
        self.calls: list = []

    def fetch(self, access, *, extent=None, mode=FetchMode.MATERIALIZE):
        self.calls.append(access)
        return SourceResult(payload=Payload.TABLE, mode=mode, data=access.endpoint)


@pytest.fixture
def source() -> DvfSource:
    reg = ProtocolRegistry()
    reg.register(FakeRemoteTable())
    return DvfSource(registry=reg)


# --------------------------------------------------------------------------
# Contract conformance + declared axes
# --------------------------------------------------------------------------


def test_dvf_is_a_datasource(source: DvfSource) -> None:
    assert isinstance(source, DataSource)
    assert source.name == "dvf"
    assert source.domain is SourceDomain.STATISTIQUE
    assert source.payload is Payload.TABLE
    assert source.jurisdiction == "FR"


def test_catalog_lists_mutations_entry(source: DvfSource) -> None:
    ids = {e.id for e in source.catalog()}
    assert ids == {"mutations"}


def test_catalog_search(source: DvfSource) -> None:
    found = source.catalog(search="mutation")
    assert [e.id for e in found] == ["mutations"]


# --------------------------------------------------------------------------
# AccessSpec — REMOTE_TABLE against the Etalab geo-dvf mirror
# --------------------------------------------------------------------------


def test_access_spec_targets_live_etalab_geo_dvf_csv(source: DvfSource) -> None:
    """The declared endpoint must point at the living geo-dvf CSV mirror."""
    entry = next(iter(source.catalog()))
    access = entry.access
    assert access.protocol is AccessProtocol.REMOTE_TABLE
    assert access.endpoint == "https://files.data.gouv.fr/geo-dvf/latest/csv"
    assert access.params["years"] == ("2021", "2022", "2023", "2024", "2025")
    assert (
        access.params["department_endpoint_template"]
        == "{base}/{year}/departements/{departement}.csv.gz"
    )
    assert access.params["full_endpoint_template"] == "{base}/{year}/full.csv.gz"
    assert access.format == "text/csv"


def test_reference_scan_uses_department_csv_shards_when_requested() -> None:
    """A department hint selects the lighter geo-dvf shard before bbox filter."""
    src = DvfSource()

    result = src.fetch(
        "mutations",
        extent={"bbox": (3.0, 45.0, 4.0, 46.0), "departement": "63"},
        mode=FetchMode.REFERENCE,
    )

    scan = result.metadata["duckdb_scan"]
    assert result.payload is Payload.TABLE
    assert "read_csv_auto(" in scan
    assert "/2021/departements/63.csv.gz" in scan
    assert "/2025/departements/63.csv.gz" in scan
    assert "/full.csv.gz" not in scan
    assert '"longitude" BETWEEN 3.0 AND 4.0' in scan
    assert '"latitude" BETWEEN 45.0 AND 46.0' in scan


def test_reference_scan_falls_back_to_full_csv_without_department() -> None:
    """Without a department hint, the scan uses full yearly CSVs plus bbox."""
    src = DvfSource()

    result = src.fetch(
        "mutations",
        extent=(3.0, 45.0, 4.0, 46.0),
        mode=FetchMode.REFERENCE,
    )

    scan = result.metadata["duckdb_scan"]
    assert "/2021/full.csv.gz" in scan
    assert "/2025/full.csv.gz" in scan
    assert "/departements/" not in scan
    assert '"longitude" BETWEEN 3.0 AND 4.0' in scan


def test_reference_scan_restores_legacy_cadastral_columns() -> None:
    """The CSV lacks raw pivot fields, so the scan recreates them."""
    result = DvfSource().fetch("mutations", mode=FetchMode.REFERENCE)
    scan = result.metadata["duckdb_scan"]

    assert 'substr("id_parcelle", 6, 3) AS "prefixe_section"' in scan
    assert 'substr("id_parcelle", 9, 2) AS "section"' in scan
    assert 'substr("id_parcelle", 11, 4) AS "numero_plan"' in scan


def test_public_dvf_registry_dispatches_to_csv_fetcher() -> None:
    """Consumers can explicitly bypass the global REMOTE_TABLE slot."""
    entry = next(iter(DvfSource().catalog()))

    result = dvf_registry().dispatch_fetch(
        entry.access,
        extent={"bbox": (3.0, 45.0, 4.0, 46.0), "departement": "63"},
        mode=FetchMode.REFERENCE,
    )
    scan = result.metadata["duckdb_scan"]

    assert "read_csv_auto(" in scan
    assert "read_parquet" not in scan
    assert "/departements/63.csv.gz" in scan


def test_resolve_dvf_scan_returns_csv_scan() -> None:
    entry = next(iter(DvfSource().catalog()))

    scan = resolve_dvf_scan(
        entry,
        extent={"bbox": (3.0, 45.0, 4.0, 46.0), "departement": "63"},
    )

    assert "read_csv_auto(" in scan
    assert "read_parquet" not in scan
    assert '"longitude" BETWEEN 3.0 AND 4.0' in scan


# --------------------------------------------------------------------------
# Declarative fetch — delegates to the REMOTE_TABLE adapter, zero network
# --------------------------------------------------------------------------


def test_fetch_delegates_to_remote_table_adapter() -> None:
    adapter = FakeRemoteTable()
    reg = ProtocolRegistry()
    reg.register(adapter)
    src = DvfSource(registry=reg)

    result = src.fetch("mutations")

    assert result.payload is Payload.TABLE
    assert "files.data.gouv.fr/geo-dvf" in result.data
    assert len(adapter.calls) == 1
    assert adapter.calls[0].protocol is AccessProtocol.REMOTE_TABLE


def test_fetch_unknown_entry_raises(source: DvfSource) -> None:
    with pytest.raises(KeyError, match="unknown entry 'ghost'"):
        source.fetch("ghost")


# --------------------------------------------------------------------------
# Schema — cadastral pivot + canonical id_parcelle
# --------------------------------------------------------------------------


def test_schema_exposes_raw_cadastral_pivot_fields(source: DvfSource) -> None:
    """The four raw DVF cadastral pivot fields must be exposed so the
    REMOTE_TABLE adapter can map CSV columns one-to-one."""
    schema = source.schema("mutations")
    for field in ("code_commune", "prefixe_section", "section", "numero_plan"):
        assert field in schema, f"missing pivot field: {field}"


def test_schema_exposes_canonical_id_parcelle(source: DvfSource) -> None:
    """A synthetic ``id_parcelle`` field is exposed as the canonical
    join key downstream plugins (e.g. gispulse-permis) consume."""
    schema = source.schema("mutations")
    assert schema["id_parcelle"] == "str"


def test_schema_uses_latitude_longitude_names(source: DvfSource) -> None:
    """Geo-dvf mirror uses ``latitude`` / ``longitude`` (not ``lat`` /
    ``lon`` — that's the cquest community facade); schema must match
    the declared endpoint."""
    schema = source.schema("mutations")
    assert schema["latitude"] == "float"
    assert schema["longitude"] == "float"


def test_schema_exposes_headline_economic_field(source: DvfSource) -> None:
    """``valeur_fonciere`` is the headline column of every DVF use case."""
    assert source.schema("mutations")["valeur_fonciere"] == "float"


# --------------------------------------------------------------------------
# revision() — GET on the data.gouv.fr metadata API, never a fetch()
# --------------------------------------------------------------------------


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


def test_revision_returns_last_modified_from_datagouv_api(
    source: DvfSource, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``revision()`` extracts ``last_modified`` from the dataset API."""
    captured: list[str] = []

    def fake_get(url, **_kw):
        captured.append(url)
        return _FakeResponse({"last_modified": "2026-04-07T15:08:32.041000+00:00"})

    monkeypatch.setattr("httpx.get", fake_get)
    token = source.revision("mutations")

    assert token == "2026-04-07T15:08:32.041000+00:00"
    assert captured and "data.gouv.fr/api/1/datasets" in captured[0]
    assert "demandes-de-valeurs-foncieres" in captured[0]


def test_revision_returns_none_on_network_error(
    source: DvfSource, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Network errors degrade to ``None`` so the watcher skips silently."""

    def fake_get(url, **_kw):
        raise RuntimeError("simulated DNS failure")

    monkeypatch.setattr("httpx.get", fake_get)
    assert source.revision("mutations") is None


def test_revision_returns_none_on_non_2xx(
    source: DvfSource, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 5xx / 4xx response degrades to ``None`` rather than crashing."""

    def fake_get(url, **_kw):
        return _FakeResponse({}, status_code=502)

    monkeypatch.setattr("httpx.get", fake_get)
    assert source.revision("mutations") is None


def test_revision_returns_none_when_field_missing(
    source: DvfSource, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Malformed payload — ``last_modified`` missing — degrades to ``None``."""

    def fake_get(url, **_kw):
        return _FakeResponse({"title": "DVF", "id": "abc"})

    monkeypatch.setattr("httpx.get", fake_get)
    assert source.revision("mutations") is None

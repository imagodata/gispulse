"""Unit tests for the gispulse-src-insee pilot plugin."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

_PKG = Path(__file__).resolve().parents[2] / "plugins" / "gispulse-src-insee"
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


_SOCIODEMO_ENTRY_IDS = {
    "iris_population_2022",
    "iris_logement_2022",
    "iris_menages_2022",
    "iris_activite_2022",
    "iris_diplomes_2022",
    "iris_filosofi_revenus_declares_2021",
    "iris_filosofi_revenus_disponibles_2021",
}


class FakeWFS:
    """Records the AccessSpec it is handed and returns a marker result."""

    protocol = AccessProtocol.WFS

    def __init__(self) -> None:
        self.calls: list = []

    def fetch(self, access, *, extent=None, mode=FetchMode.MATERIALIZE):
        self.calls.append(access)
        return SourceResult(payload=Payload.VECTOR, mode=mode, data=access.params["typename"])


class FakeTableFile:
    """Records tabular file AccessSpecs and returns a marker result."""

    protocol = AccessProtocol.TABLE_FILE

    def __init__(self) -> None:
        self.calls: list = []

    def fetch(self, access, *, extent=None, mode=FetchMode.MATERIALIZE):
        self.calls.append(access)
        return SourceResult(payload=Payload.TABLE, mode=mode, data=access.endpoint)


def _source_class():
    return importlib.import_module("gispulse_src_insee.source").InseeSource


@pytest.fixture
def source():
    reg = ProtocolRegistry()
    reg.register(FakeWFS())
    reg.register(FakeTableFile())
    return _source_class()(registry=reg)


# --------------------------------------------------------------------------
# Packaging + registration
# --------------------------------------------------------------------------


def test_pyproject_declares_insee_entrypoint_and_statistical_manifest() -> None:
    # tomllib is stdlib only on Python 3.11+; the CI matrix includes 3.10.
    tomllib = pytest.importorskip("tomllib")
    pyproject = tomllib.loads((_PKG / "pyproject.toml").read_text())

    assert pyproject["project"]["entry-points"]["gispulse.data_sources"] == {
        "insee": "gispulse_src_insee:register"
    }

    manifest = pyproject["tool"]["gispulse"]["plugin"]
    assert manifest["kind"] == "source"
    assert manifest["domain"] == "statistique"
    assert manifest["jurisdiction"] == "FR"


def test_register_adds_insee_source_to_global_registry() -> None:
    from gispulse.core.sources import SOURCES

    InseeSource = _source_class()
    register = importlib.import_module("gispulse_src_insee").register

    SOURCES.clear()
    try:
        register()
        registered = SOURCES.get("insee")
        assert isinstance(registered, InseeSource)
        assert {entry.id for entry in registered.catalog()} == {
            "iris",
            *_SOCIODEMO_ENTRY_IDS,
        }
    finally:
        SOURCES.clear()


# --------------------------------------------------------------------------
# Contract conformance + declared axes
# --------------------------------------------------------------------------


def test_insee_is_a_statistical_vector_datasource(source) -> None:
    assert isinstance(source, DataSource)
    assert source.name == "insee"
    assert source.domain is SourceDomain.STATISTIQUE
    assert source.payload is Payload.VECTOR
    assert source.jurisdiction == "FR"


def test_catalog_lists_iris_and_sociodemo_entries(source) -> None:
    ids = {entry.id for entry in source.catalog()}
    assert ids == {"iris", *_SOCIODEMO_ENTRY_IDS}


def test_catalog_search_iris(source) -> None:
    found = source.catalog(search="iris")
    assert [entry.id for entry in found] == [
        "iris",
        "iris_population_2022",
        "iris_logement_2022",
        "iris_menages_2022",
        "iris_activite_2022",
        "iris_diplomes_2022",
        "iris_filosofi_revenus_declares_2021",
        "iris_filosofi_revenus_disponibles_2021",
    ]


def test_catalog_search_sociodemo_theme(source) -> None:
    found = source.catalog(search="logement")
    assert [entry.id for entry in found] == ["iris_logement_2022"]


# --------------------------------------------------------------------------
# AccessSpec — WFS against the Géoplateforme statistical units namespace
# --------------------------------------------------------------------------


def test_iris_targets_geoplateforme_wfs_typename(source) -> None:
    entry = source._entry("iris")
    access = entry.access

    assert access.protocol is AccessProtocol.WFS
    assert access.endpoint == "https://data.geopf.fr/wfs/ows"
    assert access.params == {"typename": "STATISTICALUNITS.IRIS:contour_iris"}
    assert access.format == "application/json"


def test_iris_entry_carries_classification_axes_and_metadata(source) -> None:
    entry = source._entry("iris")

    assert entry.domain is SourceDomain.STATISTIQUE
    assert entry.payload is Payload.VECTOR
    assert entry.jurisdiction == "FR"
    assert entry.metadata == {
        "provider": "IGN / INSEE",
        "platform": "WFS Géoplateforme",
        "license": "Licence Ouverte 2.0",
        "update_cadence": "annuel",
        "typename": "STATISTICALUNITS.IRIS:contour_iris",
    }


def test_iris_sociodemo_entries_are_table_files(source) -> None:
    entries = {entry.id: entry for entry in source.catalog()}

    for entry_id in _SOCIODEMO_ENTRY_IDS:
        entry = entries[entry_id]
        assert entry.domain is SourceDomain.STATISTIQUE
        assert entry.payload is Payload.TABLE
        assert entry.jurisdiction == "FR"
        assert entry.access.protocol is AccessProtocol.TABLE_FILE
        assert entry.access.endpoint.startswith("https://www.insee.fr/fr/statistiques/fichier/")
        assert entry.access.endpoint.endswith("_CSV.zip") or entry.access.endpoint.endswith(
            "_csv.zip"
        )
        assert entry.access.params["archive_format"] == "zip"
        assert entry.access.params["table_format"] == "csv"


def test_iris_sociodemo_entries_keep_official_download_urls(source) -> None:
    endpoints = {entry.id: entry.access.endpoint for entry in source.catalog()}

    assert endpoints["iris_population_2022"].endswith(
        "/8647014/base-ic-evol-struct-pop-2022_csv.zip"
    )
    assert endpoints["iris_logement_2022"].endswith("/8647012/base-ic-logement-2022_csv.zip")
    assert endpoints["iris_menages_2022"].endswith(
        "/8647008/base-ic-couples-familles-menages-2022_csv.zip"
    )
    assert endpoints["iris_activite_2022"].endswith(
        "/8647006/base-ic-activite-residents-2022_csv.zip"
    )
    assert endpoints["iris_diplomes_2022"].endswith(
        "/8647010/base-ic-diplomes-formation-2022_csv.zip"
    )
    assert endpoints["iris_filosofi_revenus_declares_2021"].endswith(
        "/8229323/BASE_TD_FILO_IRIS_2021_DEC_CSV.zip"
    )
    assert endpoints["iris_filosofi_revenus_disponibles_2021"].endswith(
        "/8229323/BASE_TD_FILO_IRIS_2021_DISP_CSV.zip"
    )


# --------------------------------------------------------------------------
# Declarative fetch — delegates to the WFS adapter, zero network code
# --------------------------------------------------------------------------


def test_fetch_delegates_to_wfs_adapter() -> None:
    wfs = FakeWFS()
    reg = ProtocolRegistry()
    reg.register(wfs)
    src = _source_class()(registry=reg)

    result = src.fetch("iris")

    assert result.payload is Payload.VECTOR
    assert result.data == "STATISTICALUNITS.IRIS:contour_iris"
    assert len(wfs.calls) == 1
    assert wfs.calls[0].protocol is AccessProtocol.WFS


def test_fetch_delegates_sociodemo_entry_to_table_file_adapter() -> None:
    adapter = FakeTableFile()
    reg = ProtocolRegistry()
    reg.register(FakeWFS())
    reg.register(adapter)
    src = _source_class()(registry=reg)

    result = src.fetch("iris_population_2022")

    assert result.payload is Payload.TABLE
    assert result.data.endswith("/8647014/base-ic-evol-struct-pop-2022_csv.zip")
    assert len(adapter.calls) == 1
    assert adapter.calls[0].protocol is AccessProtocol.TABLE_FILE


def test_fetch_unknown_entry_raises(source) -> None:
    with pytest.raises(KeyError, match="unknown entry 'ghost'"):
        source.fetch("ghost")


# --------------------------------------------------------------------------
# Schema — raw upstream IRIS attributes
# --------------------------------------------------------------------------


def test_schema_exposes_raw_iris_fields(source) -> None:
    schema = source.schema("iris")

    assert schema["code_iris"] == "str"
    assert schema["nom_iris"] == "str"
    assert schema["insee_com"] == "str"
    assert schema["nom_com"] == "str"
    assert schema["type_iris"] == "str"
    assert schema["geometry"] == "geometry"


def test_schema_exposes_common_raw_sociodemo_fields(source) -> None:
    schema = source.schema("iris_population_2022")

    for field in ("IRIS", "COM", "TYP_IRIS", "LAB_IRIS"):
        assert schema[field] == "str"


def test_schema_exposes_theme_headline_fields(source) -> None:
    assert source.schema("iris_population_2022")["P22_POP"] == "float"
    assert source.schema("iris_logement_2022")["P22_LOG"] == "float"
    assert source.schema("iris_menages_2022")["C22_MEN"] == "float"
    assert source.schema("iris_activite_2022")["P22_ACT1564"] == "float"
    assert source.schema("iris_diplomes_2022")["P22_NSCOL15P"] == "float"
    assert source.schema("iris_filosofi_revenus_declares_2021")["DEC_MED21"] == "float"
    assert source.schema("iris_filosofi_revenus_disponibles_2021")["DISP_MED21"] == "float"


def test_schema_validates_entry_id(source) -> None:
    with pytest.raises(KeyError, match="unknown entry 'ghost'"):
        source.schema("ghost")


# --------------------------------------------------------------------------
# revision() — HEAD on WFS GetCapabilities, never a fetch()
# --------------------------------------------------------------------------


class _FakeResponse:
    """Minimal stand-in for an ``httpx.Response`` — only ``.headers``."""

    def __init__(self, headers: dict[str, str]) -> None:
        self.headers = headers


def test_revision_probes_wfs_capabilities_and_uses_etag(
    source, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[str] = []

    def fake_head(url, **_kw):
        captured.append(url)
        return _FakeResponse({"etag": '"iris-millesime-2026"'})

    monkeypatch.setattr("httpx.head", fake_head)
    token = source.revision("iris")

    assert token == "iris-millesime-2026"
    assert captured and "GetCapabilities" in captured[0]


def test_revision_falls_back_to_last_modified(source, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_head(url, **_kw):
        return _FakeResponse({"last-modified": "Mon, 01 Jun 2026 00:00:00 GMT"})

    monkeypatch.setattr("httpx.head", fake_head)
    assert source.revision("iris") == "Mon, 01 Jun 2026 00:00:00 GMT"


def test_revision_returns_none_on_network_error(source, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_head(url, **_kw):
        raise RuntimeError("simulated DNS failure")

    monkeypatch.setattr("httpx.head", fake_head)
    assert source.revision("iris") is None


def test_revision_returns_none_without_freshness_header(
    source, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("httpx.head", lambda *_a, **_kw: _FakeResponse({}))
    assert source.revision("iris") is None


def test_revision_returns_static_millesime_for_sociodemo_entry(source) -> None:
    assert source.revision("iris_population_2022") == "insee-rp-iris-population-2022-geo-2024-01-01"
    assert (
        source.revision("iris_filosofi_revenus_disponibles_2021")
        == "insee-filosofi-iris-revenus-disponibles-2021-geo-2022-01-01"
    )


def test_revision_validates_entry_id(source, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("httpx.head", lambda *_a, **_kw: _FakeResponse({}))
    with pytest.raises(KeyError, match="unknown entry 'ghost'"):
        source.revision("ghost")

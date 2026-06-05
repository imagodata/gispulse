"""Unit tests for the gispulse-src-gpu pilot plugin (issue #184, wave 2)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PKG = Path(__file__).resolve().parents[2] / "plugins" / "gispulse-src-gpu"
_PKG_PATH = str(_PKG)
if _PKG_PATH in sys.path:
    sys.path.remove(_PKG_PATH)
sys.path.insert(0, _PKG_PATH)
for _module in ("gispulse_src_gpu.source", "gispulse_src_gpu"):
    sys.modules.pop(_module, None)

from gispulse_src_gpu.source import (  # noqa: E402
    GpuSource,
    gpu_du_partition,
    gpu_du_partitions_for_department,
)

from gispulse.core.plugin_model import (  # noqa: E402
    AccessProtocol,
    FetchMode,
    Payload,
    SourceDomain,
    SourceResult,
)
from gispulse.core.sources import DataSource, ProtocolRegistry  # noqa: E402


class FakeWFS:
    """Records the AccessSpec it is handed and returns a marker result."""

    protocol = AccessProtocol.WFS

    def __init__(self) -> None:
        self.calls: list = []

    def fetch(self, access, *, extent=None, mode=FetchMode.MATERIALIZE):
        self.calls.append(access)
        return SourceResult(
            payload=Payload.VECTOR, mode=mode, data=access.params["typename"]
        )


class FakeDownload:
    """Records resolved AccessSpecs for GPU bulk archives."""

    protocol = AccessProtocol.DOWNLOAD

    def __init__(self) -> None:
        self.calls: list = []

    def fetch(self, access, *, extent=None, mode=FetchMode.MATERIALIZE):
        self.calls.append(access)
        return SourceResult(payload=Payload.VECTOR, mode=mode, data=access.endpoint)


@pytest.fixture
def source() -> GpuSource:
    reg = ProtocolRegistry()
    reg.register(FakeWFS())
    reg.register(FakeDownload())
    return GpuSource(registry=reg)


# --------------------------------------------------------------------------
# Contract conformance + declared axes
# --------------------------------------------------------------------------


def test_gpu_is_a_datasource(source: GpuSource) -> None:
    assert isinstance(source, DataSource)
    assert source.name == "gpu"
    assert source.domain is SourceDomain.REGLEMENTAIRE
    assert source.payload is Payload.VECTOR
    assert source.jurisdiction == "FR"


def test_catalog_lists_wfs_and_bulk_entries(source: GpuSource) -> None:
    ids = {e.id for e in source.catalog()}
    assert ids == {
        "zone-urba",
        "doc-urba",
        "secteur-cc",
        "prescription-surf",
        "prescription-lin",
        "prescription-pct",
        "info-surf",
        "info-lin",
        "info-pct",
        "gpu_documents_bulk_index",
    }


def test_catalog_search_zone(source: GpuSource) -> None:
    found = source.catalog(search="zone")
    assert [e.id for e in found] == ["zone-urba"]


def test_catalog_search_prescription_matches_three(source: GpuSource) -> None:
    found = source.catalog(search="prescription")
    assert {e.id for e in found} == {
        "prescription-surf",
        "prescription-lin",
        "prescription-pct",
    }


# --------------------------------------------------------------------------
# AccessSpec — WFS against the Géoplateforme
# --------------------------------------------------------------------------


def test_zone_urba_targets_wfs_du_namespace(source: GpuSource) -> None:
    """The ``zone-urba`` entry must point at the ``wfs_du:zone_urba``
    typename so the shipped WfsFetcher (#209) can dispatch it."""
    entry = next(e for e in source.catalog() if e.id == "zone-urba")
    access = entry.access
    assert access.protocol is AccessProtocol.WFS
    assert "data.geopf.fr/wfs" in access.endpoint
    assert access.params["typename"] == "wfs_du:zone_urba"
    assert access.format == "application/json"


def test_every_entry_targets_wfs_du_typename(source: GpuSource) -> None:
    """Every published entry must declare a ``wfs_du:*`` typename so the
    upstream WFS naming is preserved and a typo cannot slip in."""
    for entry in source.catalog():
        if entry.id == "gpu_documents_bulk_index":
            continue
        access = entry.access
        assert access.protocol is AccessProtocol.WFS
        assert access.params["typename"].startswith("wfs_du:"), (
            f"{entry.id}: typename should be under wfs_du namespace, "
            f"got {access.params['typename']!r}"
        )


def test_every_entry_carries_classification_axes(source: GpuSource) -> None:
    """Each entry must carry the per-entry domain / payload / jurisdiction
    axes (#227, EPIC #226) so the worldwide catalogue can index it
    directly — leaving them ``None`` would drop the entry from the
    domain / payload / jurisdiction filters."""
    for entry in source.catalog():
        assert entry.domain is SourceDomain.REGLEMENTAIRE, entry.id
        assert entry.payload is Payload.VECTOR, entry.id
        assert entry.jurisdiction == "FR", entry.id


# --------------------------------------------------------------------------
# Declarative fetch — delegates to the WFS adapter, zero network code
# --------------------------------------------------------------------------


def test_fetch_delegates_to_wfs_adapter() -> None:
    wfs = FakeWFS()
    reg = ProtocolRegistry()
    reg.register(wfs)
    reg.register(FakeDownload())
    src = GpuSource(registry=reg)

    result = src.fetch("zone-urba")

    assert result.payload is Payload.VECTOR
    assert "wfs_du:zone_urba" in result.data
    assert len(wfs.calls) == 1
    assert wfs.calls[0].protocol is AccessProtocol.WFS


def test_gpu_documents_bulk_entry_declares_cnig_partition_download(
    source: GpuSource,
) -> None:
    entry = next(e for e in source.catalog() if e.id == "gpu_documents_bulk_index")
    access = entry.access

    assert access.protocol is AccessProtocol.DOWNLOAD
    assert access.endpoint == (
        "https://www.geoportail-urbanisme.gouv.fr/api/document/"
        "download-by-partition/{partition}"
    )
    assert access.params == {"partition": "DU_200046977", "departement": "69"}
    assert access.format == "application/zip"
    assert entry.metadata["base_key"] == "gpu_documents"
    assert entry.metadata["archive_family"] == ("pack_plu1", "pack_plu2", "pack_plui", "pack_cc")
    assert entry.metadata["partition_prefix"] == "DU_"
    assert entry.metadata["partition_code_fields"] == ("insee", "siren")
    assert entry.metadata["join_keys"] == ("idurba", "insee")


def test_gpu_du_partition_helpers_keep_department_attachment() -> None:
    assert gpu_du_partition("69123") == "DU_69123"
    assert gpu_du_partition("200046977") == "DU_200046977"
    assert gpu_du_partitions_for_department(
        "69",
        codes_insee=["69123", "75056"],
        sirens=[("69", "200046977"), ("75", "200054781")],
    ) == ["DU_69123", "DU_200046977"]


def test_fetch_gpu_documents_bulk_resolves_partition_template() -> None:
    download = FakeDownload()
    reg = ProtocolRegistry()
    reg.register(FakeWFS())
    reg.register(download)
    src = GpuSource(registry=reg)

    result = src.fetch("gpu_documents_bulk_index")

    assert result.payload is Payload.VECTOR
    assert result.data.endswith("/download-by-partition/DU_200046977")
    assert "{partition}" not in result.data
    assert len(download.calls) == 1
    assert download.calls[0].protocol is AccessProtocol.DOWNLOAD


def test_fetch_unknown_entry_raises(source: GpuSource) -> None:
    with pytest.raises(KeyError, match="unknown entry 'ghost'"):
        source.fetch("ghost")


# --------------------------------------------------------------------------
# Schema — per-layer attributes, common ``idurba`` join key
# --------------------------------------------------------------------------


def test_zone_urba_schema_exposes_pluzone_attributes(source: GpuSource) -> None:
    """zone-urba must expose ``libelle`` (UA/UB/A/N…) and ``typezone``
    (U/AU/A/N) — the two attributes a PLU consumer needs to interpret
    a parcel's zoning."""
    schema = source.schema("zone-urba")
    assert schema["libelle"] == "str"
    assert schema["typezone"] == "str"
    assert schema["idurba"] == "str"


def test_doc_urba_schema_exposes_approval_date(source: GpuSource) -> None:
    """doc-urba must carry ``datappro`` — the legal approval date that
    determines which regulation applies on the ground."""
    schema = source.schema("doc-urba")
    assert schema["datappro"] == "date"
    assert schema["typedoc"] == "str"


def test_all_schemas_share_idurba_join_key(source: GpuSource) -> None:
    """Every layer must expose ``idurba`` so consumers can join a
    feature back to its parent urban-planning document."""
    for entry in source.catalog():
        if entry.id == "gpu_documents_bulk_index":
            continue
        schema = source.schema(entry.id)
        assert "idurba" in schema, (
            f"{entry.id}: missing idurba join key in schema"
        )


def test_gpu_documents_bulk_schema_exposes_cnig_join_keys(source: GpuSource) -> None:
    schema = source.schema("gpu_documents_bulk_index")

    assert schema["idurba"] == "str"
    assert schema["insee"] == "str"
    assert schema["partition"] == "str"


def test_prescription_schema_exposes_typepsc_and_txt(source: GpuSource) -> None:
    """The three prescription layers share the same attribute pattern."""
    for entry_id in ("prescription-surf", "prescription-lin", "prescription-pct"):
        schema = source.schema(entry_id)
        assert schema["typepsc"] == "str"
        assert schema["txt"] == "str"


def test_info_schema_exposes_typeinf(source: GpuSource) -> None:
    """The three info layers share the ``typeinf`` discriminator."""
    for entry_id in ("info-surf", "info-lin", "info-pct"):
        schema = source.schema(entry_id)
        assert schema["typeinf"] == "str"


# --------------------------------------------------------------------------
# revision() — HEAD on WFS GetCapabilities, never a fetch()
# --------------------------------------------------------------------------


class _FakeResponse:
    """Minimal stand-in for an ``httpx.Response`` — only ``.headers``."""

    def __init__(self, headers: dict[str, str]) -> None:
        self.headers = headers


def test_revision_probes_wfs_capabilities_and_uses_etag(
    source: GpuSource, monkeypatch: pytest.MonkeyPatch
) -> None:
    """revision() derives its token from the WFS ETag header."""
    captured: list[str] = []

    def fake_head(url, **_kw):
        captured.append(url)
        return _FakeResponse({"etag": '"gpu-millesime-2026-05"'})

    monkeypatch.setattr("httpx.head", fake_head)
    token = source.revision("zone-urba")

    assert token == "gpu-millesime-2026-05"
    assert captured and "GetCapabilities" in captured[0]


def test_revision_falls_back_to_last_modified(
    source: GpuSource, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_head(url, **_kw):
        return _FakeResponse({"last-modified": "Mon, 12 May 2026 06:00:00 GMT"})

    monkeypatch.setattr("httpx.head", fake_head)
    assert source.revision("prescription-surf") == (
        "Mon, 12 May 2026 06:00:00 GMT"
    )


def test_revision_returns_none_on_network_error(
    source: GpuSource, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_head(url, **_kw):
        raise RuntimeError("simulated DNS failure")

    monkeypatch.setattr("httpx.head", fake_head)
    assert source.revision("info-surf") is None


def test_revision_probes_gpu_documents_bulk_redirect_location(
    source: GpuSource, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[str] = []

    def fake_head(url, **_kw):
        captured.append(url)
        return _FakeResponse(
            {
                "location": (
                    "https://www.geoportail-urbanisme.gouv.fr/document/"
                    "du/2026-05-26/DU_200046977-9f13.zip"
                )
            }
        )

    monkeypatch.setattr("httpx.head", fake_head)

    assert source.revision("gpu_documents_bulk_index") == (
        "https://www.geoportail-urbanisme.gouv.fr/document/"
        "du/2026-05-26/DU_200046977-9f13.zip"
    )
    assert captured == [
        "https://www.geoportail-urbanisme.gouv.fr/api/document/"
        "download-by-partition/DU_200046977"
    ]


def test_revision_returns_none_for_gpu_documents_bulk_without_redirect_or_headers(
    source: GpuSource, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_head(url, **_kw):
        return _FakeResponse({})

    monkeypatch.setattr("httpx.head", fake_head)
    assert source.revision("gpu_documents_bulk_index") is None

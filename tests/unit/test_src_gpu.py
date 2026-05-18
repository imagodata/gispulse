"""Unit tests for the gispulse-src-gpu pilot plugin (issue #184, wave 2)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PKG = Path(__file__).resolve().parents[2] / "plugins" / "gispulse-src-gpu"
if str(_PKG) not in sys.path:
    sys.path.insert(0, str(_PKG))

from gispulse_src_gpu.source import GpuSource  # noqa: E402

from core.plugin_model import (  # noqa: E402
    AccessProtocol,
    FetchMode,
    Payload,
    SourceDomain,
    SourceResult,
)
from core.sources import DataSource, ProtocolRegistry  # noqa: E402


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


@pytest.fixture
def source() -> GpuSource:
    reg = ProtocolRegistry()
    reg.register(FakeWFS())
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


def test_catalog_lists_nine_entries(source: GpuSource) -> None:
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
        access = entry.access
        assert access.protocol is AccessProtocol.WFS
        assert access.params["typename"].startswith("wfs_du:"), (
            f"{entry.id}: typename should be under wfs_du namespace, "
            f"got {access.params['typename']!r}"
        )


# --------------------------------------------------------------------------
# Declarative fetch — delegates to the WFS adapter, zero network code
# --------------------------------------------------------------------------


def test_fetch_delegates_to_wfs_adapter() -> None:
    wfs = FakeWFS()
    reg = ProtocolRegistry()
    reg.register(wfs)
    src = GpuSource(registry=reg)

    result = src.fetch("zone-urba")

    assert result.payload is Payload.VECTOR
    assert "wfs_du:zone_urba" in result.data
    assert len(wfs.calls) == 1
    assert wfs.calls[0].protocol is AccessProtocol.WFS


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
        schema = source.schema(entry.id)
        assert "idurba" in schema, (
            f"{entry.id}: missing idurba join key in schema"
        )


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

"""Unit tests for A4 (#230) — ``OGCFeaturesFetcher``.

Zero network: the lazy path builds an ``ST_Read`` URL string, and the
materialise path's only network actors (the WFS client functions) are
monkey-patched with recording fakes.
"""

from __future__ import annotations

import pytest

from gispulse.core.fetchers import DUCKDB_SCAN_KEY
from gispulse.core.fetchers.ogc_features import OGCFeaturesFetcher
from gispulse.core.plugin_model import (
    AccessProtocol,
    AccessSpec,
    FetchMode,
    Payload,
)
from gispulse.core.ssrf import SSRFError

# offline_ssrf (tests/unit/conftest.py) keeps the SSRF guard's DNS
# resolution off the network — CI does zero network.
pytestmark = pytest.mark.usefixtures("offline_ssrf")


def _access(endpoint: str, **params: object) -> AccessSpec:
    return AccessSpec(
        protocol=AccessProtocol.OGC_FEATURES, endpoint=endpoint, params=params
    )


# -- contract ---------------------------------------------------------------


def test_protocol_and_payload() -> None:
    assert OGCFeaturesFetcher.protocol is AccessProtocol.OGC_FEATURES
    assert OGCFeaturesFetcher.payload is Payload.VECTOR


# -- lazy scan --------------------------------------------------------------


def test_reference_scan_emits_st_read_items_url() -> None:
    result = OGCFeaturesFetcher().virtual_table(
        _access("https://api.example.org/ogc", collection="buildings")
    )
    scan = result.metadata[DUCKDB_SCAN_KEY]
    assert scan == (
        "ST_Read('https://api.example.org/ogc/collections/buildings"
        "/items?f=json')"
    )
    assert result.mode is FetchMode.REFERENCE


def test_reference_scan_pushdown_bbox_query_param() -> None:
    result = OGCFeaturesFetcher().virtual_table(
        _access("https://api.example.org/ogc", collection="parcels"),
        extent=(1.0, 2.0, 3.0, 4.0),
    )
    scan = result.metadata[DUCKDB_SCAN_KEY]
    # OGC API Features pushdown: a server-side bbox= query parameter.
    assert "bbox=1.0,2.0,3.0,4.0" in scan


def test_reference_scan_layer_name_alias() -> None:
    result = OGCFeaturesFetcher().virtual_table(
        _access("https://api.example.org/ogc", layer_name="roads")
    )
    assert "/collections/roads/items" in result.metadata[DUCKDB_SCAN_KEY]


def test_missing_collection_raises() -> None:
    with pytest.raises(ValueError, match="collection"):
        OGCFeaturesFetcher().virtual_table(_access("https://api.example.org/ogc"))


# -- mode dispatch ----------------------------------------------------------


def test_fetch_materialize_delegates_to_ogc_api_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []

    def _fake_ogc(cfg: object, bbox: object = None) -> str:
        calls.append({"cfg": cfg, "bbox": bbox})
        return "GDF-ogc"

    def _fake_wfs(cfg: object, bbox: object = None) -> str:  # pragma: no cover
        raise AssertionError("WFS path must not run for source_type=ogc")

    import gispulse.adapters.ogc.wfs_client as wfs_client

    monkeypatch.setattr(wfs_client, "fetch_ogc_api_features", _fake_ogc)
    monkeypatch.setattr(wfs_client, "fetch_wfs", _fake_wfs)

    result = OGCFeaturesFetcher().fetch(
        _access("https://api.example.org/ogc", collection="buildings"),
        mode=FetchMode.MATERIALIZE,
        extent=(0, 0, 10, 10),
    )
    assert result.mode is FetchMode.MATERIALIZE
    assert result.data == "GDF-ogc"
    assert len(calls) == 1
    assert calls[0]["bbox"] == (0.0, 0.0, 10.0, 10.0)
    assert calls[0]["cfg"].layer_name == "buildings"
    assert calls[0]["cfg"].source_type == "ogc_api_features"


def test_fetch_materialize_routes_to_wfs_when_source_type_wfs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []

    def _fake_wfs(cfg: object, bbox: object = None) -> str:
        calls.append({"cfg": cfg, "bbox": bbox})
        return "GDF-wfs"

    import gispulse.adapters.ogc.wfs_client as wfs_client

    monkeypatch.setattr(wfs_client, "fetch_wfs", _fake_wfs)

    result = OGCFeaturesFetcher().fetch(
        _access(
            "https://geo.example.org/wfs",
            collection="topp:states",
            source_type="wfs",
        ),
        mode=FetchMode.MATERIALIZE,
    )
    assert result.data == "GDF-wfs"
    assert calls[0]["cfg"].source_type == "wfs"


# -- SSRF guard -------------------------------------------------------------


@pytest.mark.parametrize("mode", [FetchMode.REFERENCE, FetchMode.MATERIALIZE])
def test_fetch_rejects_private_address(mode: FetchMode) -> None:
    with pytest.raises(SSRFError):
        OGCFeaturesFetcher().fetch(
            _access("http://127.0.0.1/ogc", collection="x"), mode=mode
        )

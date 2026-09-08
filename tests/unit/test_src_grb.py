"""Unit tests for the gispulse-src-grb plugin.

Zero-network: the plugin only declares GRB Flanders WFS layers. Core fetchers own
the WFS dispatch.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PKG = Path(__file__).resolve().parents[2] / "plugins" / "gispulse-src-grb"
_PKG_PATH = str(_PKG)
if _PKG_PATH in sys.path:
    sys.path.remove(_PKG_PATH)
sys.path.insert(0, _PKG_PATH)
for _module in ("gispulse_src_grb.source", "gispulse_src_grb"):
    sys.modules.pop(_module, None)

from gispulse_src_grb.source import GrbSource  # noqa: E402

from gispulse.core.plugin_model import AccessProtocol, Payload, SourceDomain  # noqa: E402
from gispulse.core.sources import DataSource  # noqa: E402

pytestmark = pytest.mark.usefixtures("offline_ssrf")


@pytest.fixture
def source() -> GrbSource:
    return GrbSource()


def test_pyproject_declares_grb_entrypoint_and_manifest() -> None:
    tomllib = pytest.importorskip("tomllib")
    pyproject = tomllib.loads((_PKG / "pyproject.toml").read_text())

    assert pyproject["project"]["entry-points"]["gispulse.data_sources"] == {
        "grb": "gispulse_src_grb:register"
    }
    manifest = pyproject["tool"]["gispulse"]["plugin"]
    assert manifest["kind"] == "source"
    assert manifest["domain"] == "base"
    assert manifest["jurisdiction"] == "BE"


def test_is_a_base_vector_datasource(source: GrbSource) -> None:
    assert isinstance(source, DataSource)
    assert source.name == "grb"
    assert source.domain is SourceDomain.BASE
    assert source.payload is Payload.VECTOR


def test_wegbaan_entry_is_wfs_in_lambert72(source: GrbSource) -> None:
    access = source.access_for("grb-wegbaan-vl")
    assert access.protocol is AccessProtocol.WFS
    assert access.endpoint == "https://geo.api.vlaanderen.be/GRB/wfs"
    assert access.params["typename"] == "GRB:WBN"
    # GRB est nativement en Lambert 72 → pas de reprojection.
    assert access.params["crs"] == "EPSG:31370"


def test_exposes_both_carriageway_and_appurtenance(source: GrbSource) -> None:
    ids = {entry.id for entry in source.entries()}
    assert {"grb-wegbaan-vl", "grb-aanhorigheid-vl"} <= ids


def test_aanhorigheid_targets_wga_layer(source: GrbSource) -> None:
    access = source.access_for("grb-aanhorigheid-vl")
    assert access.params["typename"] == "GRB:WGA"


def test_extended_grb_entries_preserve_specific_attributes(source):
    assert source.access_for("grb-wegopdeling-vl").params["typename"] == "GRB:WGO"
    assert source.access_for("grb-kunstwerk-vl").params["typename"] == "GRB:KNW"
    assert {"VERH", "METHODE", "STATUS", "WS_OIDN"} <= source.schema("grb-wegsegment-vl").keys()
    access = source.access_for("grb-wegbaan-vl")
    assert access.params["bbox_filter"] == "intersects"
    assert access.params["count_format"] == "wfs_hits_xml"
    access.params["pagination"]["page_size"] = 1
    assert source.access_for("grb-wegbaan-vl").params["pagination"]["page_size"] == 2000


def test_grb_dry_run_has_no_side_effects(tmp_path, monkeypatch):
    from gispulse_src_grb.prepare import prepare_grb
    from gispulse.adapters.ogc.wfs_fetcher import WfsFetcher

    monkeypatch.setattr(WfsFetcher, "fetch", lambda *a, **k: pytest.fail("network in dry run"))
    output = tmp_path / "new" / "grb"
    report = prepare_grb(bbox=(100000, 190000, 101000, 191000), output=output)
    assert report["status"] == "planned" and report["ready_for_costing"] is False
    assert not output.parent.exists()


def test_grb_failure_on_second_layer_never_publishes_prefix(tmp_path, monkeypatch):
    from gispulse_src_grb.prepare import prepare_grb
    from gispulse.adapters.ogc.wfs_fetcher import WfsFetcher
    from types import SimpleNamespace
    import geopandas as gpd
    from shapely.geometry import box

    calls = []

    def fetch(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise ValueError("WFS_COUNT_INVALID")
        return SimpleNamespace(
            data=gpd.GeoDataFrame({"OIDN": [1]}, geometry=[box(0, 0, 1, 1)], crs=31370), metadata={}
        )

    monkeypatch.setattr(WfsFetcher, "fetch", fetch)
    with pytest.raises(ValueError, match="COUNT_INVALID"):
        prepare_grb(bbox=(0, 0, 2, 2), output=tmp_path / "bundle", write=True)
    assert list(tmp_path.iterdir()) == []

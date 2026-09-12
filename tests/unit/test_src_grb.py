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

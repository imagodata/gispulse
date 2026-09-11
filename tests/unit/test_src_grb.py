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


def _grb_wfs_stub(monkeypatch, *, wegsegment):
    """Patch WfsFetcher.fetch to serve a small synthetic GRB bundle.

    ``wegsegment`` is injected as given so a test can hand over a broken frame
    (missing column, duplicate/blank ID, invalid geometry) without touching
    WBN/WGO, which stay valid throughout.
    """
    from types import SimpleNamespace

    import geopandas as gpd
    from shapely.geometry import LineString, box

    from gispulse.adapters.ogc.wfs_fetcher import WfsFetcher

    layers = {
        "GRB:WBN": gpd.GeoDataFrame({"OIDN": [1]}, geometry=[box(0, 0, 10, 10)], crs=31370),
        "GRB:WGO": gpd.GeoDataFrame(
            {"OIDN": [1], "TYPE": [1]}, geometry=[LineString([(0, 8), (10, 8)])], crs=31370
        ),
        "GRB:Wegsegment": wegsegment,
    }
    other = gpd.GeoDataFrame({"OIDN": [1]}, geometry=[box(0, 0, 1, 1)], crs=31370)

    def fetch(self, access, extent):
        data = layers.get(access.params["typename"], other)
        return SimpleNamespace(data=data.copy(), metadata={})

    monkeypatch.setattr(WfsFetcher, "fetch", fetch)


def test_grb_bundle_publishes_a_separate_evidence_based_classification(tmp_path, monkeypatch):
    import json

    import geopandas as gpd
    from shapely.geometry import LineString

    from gispulse_src_grb.prepare import prepare_grb

    # MORF=103: "weg bestaande uit één rijbaan" — a documented motor-traffic code.
    wegsegment = gpd.GeoDataFrame(
        {"OIDN": [1], "WS_OIDN": ["9"], "VERH": [1], "STATUS": [4], "MORF": [103]},
        geometry=[LineString([(0, 4), (10, 4)])],
        crs=31370,
    )
    _grb_wfs_stub(monkeypatch, wegsegment=wegsegment)
    output = tmp_path / "bundle"
    report = prepare_grb(bbox=(-1, -1, 11, 11), output=output, write=True)

    assert report["classification"]["classes"] == {
        "carriageway_paved": 1,
        "sidewalk": 0,
        "unmapped": 1,
    }
    assert report["classification"]["reasons"] == {
        "in_service_paved_axis": 1,
        "no_in_service_axis_evidence": 1,
    }
    assert report["classification"]["inference"] is False
    # The chantier's mandate keeps this false until axis/structure
    # reconciliation is complete; a per-face flag must not contradict it.
    assert report["ready_for_costing"] is False
    assert (
        json.loads((output / "report.json").read_text())["classification"]["thresholds"][
            "axis_coverage_ratio_min"
        ]
        == 0.8
    )
    classified = gpd.read_parquet(output / "classified_faces.geoparquet")
    assert set(classified.functional_class) == {"carriageway_paved", "unmapped"}
    assert "in_service_paved_axis" in set(classified.classification_reason)
    # No per-face ready_for_costing leaks into the published file: only
    # validate_functional_faces' own return value carries that column, and it
    # is used here purely as an internal self-check, never written out.
    assert "ready_for_costing" not in classified.columns
    # The candidate contract stays deliberately unclassified.
    assert "functional_class" not in gpd.read_parquet(output / "candidate_faces.geoparquet")


def test_grb_bundle_never_labels_a_pedestrian_path_as_carriageway(tmp_path, monkeypatch):
    """End-to-end regression for the real Gand false positive: MORF=114 (walking/
    cycling path, closed to other vehicles) STATUS=4 VERH=1 must not become
    carriageway_paved just because it is a paved, in-service axis."""
    import geopandas as gpd
    from shapely.geometry import LineString

    from gispulse_src_grb.prepare import prepare_grb

    pedestrian_path = gpd.GeoDataFrame(
        {"OIDN": [1], "WS_OIDN": ["9"], "VERH": [1], "STATUS": [4], "MORF": [114]},
        geometry=[LineString([(0, 4), (10, 4)])],
        crs=31370,
    )
    _grb_wfs_stub(monkeypatch, wegsegment=pedestrian_path)
    output = tmp_path / "bundle"
    report = prepare_grb(bbox=(-1, -1, 11, 11), output=output, write=True)

    assert report["classification"]["classes"]["carriageway_paved"] == 0
    assert report["classification"]["reasons"].get("axis_not_motorized_carriageway", 0) >= 1
    classified = gpd.read_parquet(output / "classified_faces.geoparquet")
    assert "carriageway_paved" not in set(classified.functional_class)


def test_grb_bundle_survives_a_broken_wegsegment_layer_and_degrades_classification(
    tmp_path, monkeypatch
):
    """A Wegsegment defect must not erase acquisition the raw layers already paid for."""
    import geopandas as gpd
    from shapely.geometry import LineString

    from gispulse_src_grb.prepare import prepare_grb

    # Missing VERH entirely: classify_grb_faces raises GRB_CLASSIFY_FIELD_MISSING.
    broken = gpd.GeoDataFrame(
        {"OIDN": [1], "WS_OIDN": ["9"], "STATUS": [4]},
        geometry=[LineString([(0, 4), (10, 4)])],
        crs=31370,
    )
    _grb_wfs_stub(monkeypatch, wegsegment=broken)
    output = tmp_path / "bundle"
    report = prepare_grb(bbox=(-1, -1, 11, 11), output=output, write=True)

    assert report["status"] == "prepared_candidates"
    assert report["classification"]["status"] == "failed"
    assert "GRB_CLASSIFY_FIELD_MISSING" in report["classification"]["error"]
    assert (output / "candidate_faces.geoparquet").exists()
    assert (output / "partition_diagnostics.geoparquet").exists()
    assert (output / "grb-wegsegment-vl.geoparquet").exists()
    assert not (output / "classified_faces.geoparquet").exists()


def test_a_genuine_classifier_bug_is_never_relabelled_as_a_data_defect(tmp_path, monkeypatch):
    """validate_functional_faces raising on a classifier's own output must propagate, not degrade.

    Only classify_grb_faces' own GRB_CLASSIFY_* data-validation errors may
    degrade the bundle to classification: {"status": "failed"}. A bug in the
    classifier itself — here simulated by monkeypatching it to emit a class
    validate_functional_faces does not accept — is a different failure mode
    entirely and must surface as a crash, not a data-defect report.
    """
    import geopandas as gpd
    from shapely.geometry import LineString

    import gispulse_src_grb.prepare as prepare_module

    wegsegment = gpd.GeoDataFrame(
        {"OIDN": [1], "WS_OIDN": ["9"], "VERH": [1], "STATUS": [4]},
        geometry=[LineString([(0, 4), (10, 4)])],
        crs=31370,
    )
    _grb_wfs_stub(monkeypatch, wegsegment=wegsegment)

    def buggy_classify(faces, *_args, **_kwargs):
        broken = faces.copy()
        broken["functional_class"] = "kerb_stone"  # not in classify_faces._ALLOWED
        broken["classification_reason"] = "buggy"
        broken["classification_evidence"] = ""
        broken["axis_coverage_ratio"] = 0.0
        broken["wcz_boundary_ratio"] = 0.0
        broken["wrb_boundary_ratio"] = 0.0
        broken["woz_boundary_ratio"] = 0.0
        return broken, {"classes": {}, "reasons": {}, "inference": False}

    monkeypatch.setattr(prepare_module, "classify_grb_faces", buggy_classify)
    output = tmp_path / "bundle"
    with pytest.raises(ValueError, match="GRB_FUNCTION_CLASS_UNKNOWN"):
        prepare_module.prepare_grb(bbox=(-1, -1, 11, 11), output=output, write=True)

"""Unit tests for persistence.style_sidecar + integration via persistence.io.write_vector."""

from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import Polygon

from gispulse.capabilities.classification import (
    ChoroplethCapability,
    ClassifyCategoricalCapability,
    HeadTailBreaksCapability,
)
from gispulse.persistence.style_sidecar import write_style_sidecars


@pytest.fixture
def polygons() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "price": [1000, 2000, 3000, 4000, 5000, 6000, 7000, 8000, 9000, 10000],
            "geometry": [
                Polygon([(i, i), (i + 1, i), (i + 1, i + 1), (i, i + 1)])
                for i in range(10)
            ],
        },
        crs="EPSG:4326",
    )


class TestSidecarDirect:
    def test_choropleth_produces_three_sidecars(self, polygons, tmp_path):
        styled = ChoroplethCapability().execute(
            polygons, field="price", method="jenks", bins=5, palette="YlOrRd",
        )
        gpkg = tmp_path / "dvf.gpkg"
        gpkg.write_bytes(b"")  # sidecar writer doesn't read the primary file
        written = write_style_sidecars(styled, gpkg)
        assert set(written) == {"legend", "sld", "qml"}
        assert Path(written["legend"]).exists()
        assert Path(written["sld"]).exists()
        assert Path(written["qml"]).exists()

    def test_legend_json_content(self, polygons, tmp_path):
        styled = ChoroplethCapability().execute(
            polygons, field="price", method="quantile", bins=5, palette="YlOrRd",
        )
        gpkg = tmp_path / "dvf.gpkg"
        gpkg.write_bytes(b"")
        written = write_style_sidecars(styled, gpkg)
        legend = json.loads(Path(written["legend"]).read_text())
        assert legend["type"] == "legend"
        assert legend["field"] == "price"
        assert len(legend["classes"]) == 5

    def test_sld_contains_graduated_rules(self, polygons, tmp_path):
        styled = ChoroplethCapability().execute(
            polygons, field="price", bins=5, palette="YlOrRd",
        )
        gpkg = tmp_path / "dvf.gpkg"
        gpkg.write_bytes(b"")
        written = write_style_sidecars(styled, gpkg)
        sld = Path(written["sld"]).read_text()
        assert "PropertyIsBetween" in sld
        assert sld.count("<se:Rule>") == 5

    def test_qml_is_graduated(self, polygons, tmp_path):
        styled = ChoroplethCapability().execute(
            polygons, field="price", bins=5, palette="YlOrRd",
        )
        gpkg = tmp_path / "dvf.gpkg"
        gpkg.write_bytes(b"")
        written = write_style_sidecars(styled, gpkg)
        qml = Path(written["qml"]).read_text()
        assert "graduatedSymbol" in qml

    def test_categorical_produces_sidecars(self, tmp_path):
        from shapely.geometry import Point
        gdf = gpd.GeoDataFrame(
            {
                "status": ["deployed", "planned", "deployed", "in_progress"],
                "geometry": [Point(i, i) for i in range(4)],
            },
            crs="EPSG:4326",
        )
        styled = ClassifyCategoricalCapability().execute(gdf, field="status", palette="Set2")
        gpkg = tmp_path / "ftth.gpkg"
        gpkg.write_bytes(b"")
        written = write_style_sidecars(styled, gpkg)
        assert "sld" in written and "qml" in written
        sld = Path(written["sld"]).read_text()
        assert "PropertyIsEqualTo" in sld

    def test_no_sidecars_without_style(self, polygons, tmp_path):
        gpkg = tmp_path / "plain.gpkg"
        gpkg.write_bytes(b"")
        written = write_style_sidecars(polygons, gpkg)  # no gispulse_style attr
        assert written == {}

    def test_headtail_renderer_skips_sld_and_qml(self, polygons, tmp_path):
        """head_tail is not in the QML/SLD renderer whitelist — only legend written."""
        styled = HeadTailBreaksCapability().execute(polygons, field="price", palette="YlOrRd")
        # head_tail emits method metadata but no "renderer" key → skipped
        gpkg = tmp_path / "ht.gpkg"
        gpkg.write_bytes(b"")
        written = write_style_sidecars(styled, gpkg)
        # No legend on head_tail (only classify-style meta), no sld/qml
        assert written == {}


class TestSidecarViaWriteVector:
    def test_write_vector_creates_sidecars(self, polygons, tmp_path):
        """End-to-end: write_vector() must produce both the GPKG and sidecars."""
        from gispulse.persistence.io import write_vector

        styled = ChoroplethCapability().execute(
            polygons, field="price", bins=5, palette="YlOrRd",
        )
        gpkg = tmp_path / "dvf.gpkg"
        write_vector(styled, str(gpkg))
        assert gpkg.exists()
        assert gpkg.with_suffix(".style.qml").exists()
        assert gpkg.with_suffix(".style.sld").exists()
        assert gpkg.with_suffix(".legend.json").exists()

    def test_write_vector_opt_out(self, polygons, tmp_path):
        """write_style_sidecars=False suppresses sidecar emission."""
        from gispulse.persistence.io import write_vector

        styled = ChoroplethCapability().execute(
            polygons, field="price", bins=5, palette="YlOrRd",
        )
        gpkg = tmp_path / "dvf.gpkg"
        write_vector(styled, str(gpkg), write_style_sidecars=False)
        assert gpkg.exists()
        assert not gpkg.with_suffix(".style.qml").exists()
        assert not gpkg.with_suffix(".style.sld").exists()
        assert not gpkg.with_suffix(".legend.json").exists()

    def test_write_vector_plain_gdf_no_sidecars(self, polygons, tmp_path):
        """Output without classify metadata writes no sidecars."""
        from gispulse.persistence.io import write_vector

        gpkg = tmp_path / "plain.gpkg"
        write_vector(polygons, str(gpkg))
        assert gpkg.exists()
        assert not gpkg.with_suffix(".style.qml").exists()

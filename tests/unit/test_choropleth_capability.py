"""Unit tests for ChoroplethCapability and legend/style builders."""

from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import LineString, Point, Polygon

from capabilities.classification import (
    ChoroplethCapability,
    ClassifyCapability,
    build_graduated_style_def,
    build_legend,
)


@pytest.fixture
def polygons_gdf() -> gpd.GeoDataFrame:
    """10 polygons with monotonic values — trivially classifiable."""
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


@pytest.fixture
def points_gdf() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"v": list(range(1, 11)), "geometry": [Point(i, i) for i in range(10)]},
        crs="EPSG:4326",
    )


@pytest.fixture
def lines_gdf() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"v": list(range(1, 11)), "geometry": [LineString([(i, i), (i + 1, i + 1)]) for i in range(10)]},
        crs="EPSG:4326",
    )


class TestChoroplethCapability:
    def test_basic_execution(self, polygons_gdf):
        out = ChoroplethCapability().execute(
            polygons_gdf, field="price", method="quantile", bins=5, palette="YlOrRd",
        )
        assert "class" in out.columns
        assert "color" in out.columns
        assert "gispulse_style" in out.attrs
        assert "gispulse_legend" in out.attrs

    def test_field_required(self, polygons_gdf):
        with pytest.raises(ValueError, match="'field' parameter is required"):
            ChoroplethCapability().execute(polygons_gdf)

    def test_palette_required(self, polygons_gdf):
        with pytest.raises(ValueError, match="'palette' is required"):
            ChoroplethCapability().execute(polygons_gdf, field="price", palette=None)

    def test_style_attr_is_graduated_renderer(self, polygons_gdf):
        out = ChoroplethCapability().execute(
            polygons_gdf, field="price", method="jenks", bins=5, palette="YlOrRd",
        )
        style = out.attrs["gispulse_style"]
        assert style["renderer"] == "graduated"
        assert style["graduatedField"] == "price"
        assert style["classifyMethod"] == "jenks"
        assert style["classifyMethodLabel"] == "NaturalBreaks"
        assert len(style["classes"]) == 5

    def test_style_classes_have_symbols(self, polygons_gdf):
        out = ChoroplethCapability().execute(
            polygons_gdf, field="price", bins=5, palette="YlOrRd",
        )
        for cls in out.attrs["gispulse_style"]["classes"]:
            assert "lower" in cls
            assert "upper" in cls
            assert "label" in cls
            assert cls["symbol"]["kind"] == "fill"  # polygons
            assert cls["symbol"]["color"].startswith("#")

    def test_geom_type_inference_point(self, points_gdf):
        out = ChoroplethCapability().execute(
            points_gdf, field="v", bins=5, palette="YlOrRd",
        )
        for cls in out.attrs["gispulse_style"]["classes"]:
            assert cls["symbol"]["kind"] == "point"
            assert cls["symbol"]["shape"] == "circle"

    def test_geom_type_inference_line(self, lines_gdf):
        out = ChoroplethCapability().execute(
            lines_gdf, field="v", bins=5, palette="YlOrRd",
        )
        for cls in out.attrs["gispulse_style"]["classes"]:
            assert cls["symbol"]["kind"] == "line"

    def test_explicit_geom_type_override(self, polygons_gdf):
        """geom_type hint forces the chosen kind even when geometry is polygon."""
        out = ChoroplethCapability().execute(
            polygons_gdf, field="price", bins=5, palette="YlOrRd", geom_type="point",
        )
        for cls in out.attrs["gispulse_style"]["classes"]:
            assert cls["symbol"]["kind"] == "point"

    def test_schema_requires_field_and_palette(self):
        schema = ChoroplethCapability().get_schema()
        assert set(schema["required"]) == {"field", "palette"}
        assert "geom_type" in schema["properties"]
        assert "label_fmt" in schema["properties"]

    def test_choropleth_registered(self):
        from capabilities.registry import get
        inst = get("choropleth")
        assert isinstance(inst, ChoroplethCapability)


class TestLegendBuilder:
    def test_legend_counts(self, polygons_gdf):
        classified = ClassifyCapability().execute(
            polygons_gdf, field="price", method="quantile", bins=5, palette="YlOrRd",
        )
        legend = build_legend(classified)
        assert legend["type"] == "legend"
        assert legend["field"] == "price"
        assert legend["method"] == "quantile"
        assert legend["palette"] == "YlOrRd"
        assert legend["total_features"] == 10
        assert legend["nan_count"] == 0
        assert len(legend["classes"]) == 5
        # Each class has a count, min, max, label, color
        for cls in legend["classes"]:
            assert "index" in cls and cls["index"] >= 1
            assert "count" in cls
            assert "color" in cls
            assert "label" in cls

    def test_legend_count_distribution(self, polygons_gdf):
        """10 features in 5 quantile bins → 2 per bin."""
        classified = ClassifyCapability().execute(
            polygons_gdf, field="price", method="quantile", bins=5, palette="YlOrRd",
        )
        legend = build_legend(classified)
        counts = [cls["count"] for cls in legend["classes"]]
        assert counts == [2, 2, 2, 2, 2]

    def test_legend_nan_preserved(self):
        import geopandas as gpd  # noqa: F811
        gdf = gpd.GeoDataFrame(
            {"v": [1.0, 2.0, None, 4.0, 5.0], "geometry": [Point(i, i) for i in range(5)]},
            crs="EPSG:4326",
        )
        classified = ClassifyCapability().execute(
            gdf, field="v", method="quantile", bins=2, palette="YlOrRd",
        )
        legend = build_legend(classified)
        assert legend["nan_count"] == 1
        assert legend["total_features"] == 5

    def test_legend_custom_label_fmt(self, polygons_gdf):
        classified = ClassifyCapability().execute(
            polygons_gdf, field="price", method="quantile", bins=5, palette="YlOrRd",
        )
        legend = build_legend(classified, label_fmt="{lo:.0f} € to {hi:.0f} €")
        assert all("€" in cls["label"] for cls in legend["classes"])


class TestGraduatedStyleDefBuilder:
    def test_builds_graduated_renderer(self, polygons_gdf):
        classified = ClassifyCapability().execute(
            polygons_gdf, field="price", method="jenks", bins=5, palette="YlOrRd",
        )
        style = build_graduated_style_def(classified)
        assert style["renderer"] == "graduated"
        assert style["graduatedField"] == "price"
        assert len(style["classes"]) == 5

    def test_style_is_qml_compatible(self, polygons_gdf):
        """Output must consume cleanly through the QML converter."""
        from persistence.style_converter import style_def_to_qml
        classified = ClassifyCapability().execute(
            polygons_gdf, field="price", method="jenks", bins=5, palette="YlOrRd",
        )
        style = build_graduated_style_def(classified)
        qml = style_def_to_qml(style, geom_type="polygon")
        assert "<qgis" in qml or "<renderer-v2" in qml
        assert "graduatedSymbol" in qml

    def test_style_is_sld_compatible(self, polygons_gdf):
        """Output must consume cleanly through the SLD converter."""
        from persistence.sld_converter import style_def_to_sld
        classified = ClassifyCapability().execute(
            polygons_gdf, field="price", method="jenks", bins=5, palette="YlOrRd",
        )
        style = build_graduated_style_def(classified)
        sld = style_def_to_sld(style, geom_type="polygon", layer_name="test")
        assert "PropertyIsBetween" in sld
        assert "PolygonSymbolizer" in sld
        # 5 classes → 5 rules
        assert sld.count("<se:Rule>") == 5

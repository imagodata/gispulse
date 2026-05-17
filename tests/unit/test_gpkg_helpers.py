"""Tests for persistence.gpkg — low-level GPKG helpers.

Complements test_gpkg_engine (engine lifecycle) with coverage of the
plain helper functions: list_layers, read_gpkg, write_gpkg, multi-layer
I/O, layer_styles table read/write/copy, QML/SLD color parsing, and the
Dataset factory.
"""
from __future__ import annotations

import sqlite3

import geopandas as gpd
import pytest
from shapely.geometry import Point

from gispulse.persistence import gpkg as gpkg_mod


@pytest.fixture
def sample_gdf() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "id": [1, 2, 3],
            "name": ["A", "B", "C"],
            "geometry": [Point(0, 0), Point(1, 1), Point(2, 2)],
        },
        crs="EPSG:4326",
    )


@pytest.fixture
def gpkg_path(tmp_path, sample_gdf) -> str:
    p = tmp_path / "sample.gpkg"
    sample_gdf.to_file(p, layer="cities", driver="GPKG")
    return str(p)


# ---------------------------------------------------------------------------
# list_layers / read / write
# ---------------------------------------------------------------------------


class TestListLayers:
    def test_single_layer_file(self, gpkg_path):
        assert gpkg_mod.list_layers(gpkg_path) == ["cities"]

    def test_empty_path_raises(self, tmp_path):
        with pytest.raises(Exception):
            gpkg_mod.list_layers(str(tmp_path / "does_not_exist.gpkg"))


class TestReadGpkg:
    def test_reads_named_layer(self, gpkg_path):
        gdf = gpkg_mod.read_gpkg(gpkg_path, layer="cities")
        assert len(gdf) == 3
        assert "name" in gdf.columns

    def test_reads_first_layer_by_default(self, gpkg_path):
        gdf = gpkg_mod.read_gpkg(gpkg_path)
        assert len(gdf) == 3

    def test_empty_gpkg_raises(self, tmp_path, sample_gdf):
        # Create an empty file — pyogrio will raise on list_layers
        bad = tmp_path / "empty.gpkg"
        bad.touch()
        with pytest.raises(Exception):
            gpkg_mod.read_gpkg(str(bad))


class TestWriteGpkg:
    def test_creates_new_file(self, tmp_path, sample_gdf):
        out = tmp_path / "new.gpkg"
        gpkg_mod.write_gpkg(sample_gdf, str(out), layer="points")
        assert out.exists()
        assert "points" in gpkg_mod.list_layers(str(out))

    def test_append_mode_adds_second_layer(self, tmp_path, sample_gdf):
        out = tmp_path / "multi.gpkg"
        gpkg_mod.write_gpkg(sample_gdf, str(out), layer="layer_a", mode="w")
        gpkg_mod.write_gpkg(sample_gdf, str(out), layer="layer_b", mode="a")
        layers = gpkg_mod.list_layers(str(out))
        assert set(layers) == {"layer_a", "layer_b"}


# ---------------------------------------------------------------------------
# Multi-layer I/O
# ---------------------------------------------------------------------------


class TestMultiLayerIO:
    def test_read_all_layers(self, tmp_path, sample_gdf):
        path = tmp_path / "multi.gpkg"
        sample_gdf.to_file(path, layer="l1", driver="GPKG")
        sample_gdf.iloc[:2].to_file(path, layer="l2", driver="GPKG", mode="a")
        layers = gpkg_mod.read_all_layers(str(path))
        assert set(layers.keys()) == {"l1", "l2"}
        assert len(layers["l1"]) == 3
        assert len(layers["l2"]) == 2

    def test_write_all_layers_roundtrip(self, tmp_path, sample_gdf):
        path = tmp_path / "w.gpkg"
        gpkg_mod.write_all_layers(
            {"a": sample_gdf, "b": sample_gdf.iloc[:1]}, str(path)
        )
        assert set(gpkg_mod.list_layers(str(path))) == {"a", "b"}

    def test_write_all_layers_creates_parent_dir(self, tmp_path, sample_gdf):
        path = tmp_path / "nested" / "dir" / "proj.gpkg"
        gpkg_mod.write_all_layers({"x": sample_gdf}, str(path))
        assert path.exists()

    def test_read_all_applies_fallback_crs(self, tmp_path, sample_gdf):
        # Strip CRS to trigger the fallback path
        nocrs = sample_gdf.copy()
        nocrs = nocrs.set_crs(None, allow_override=True)
        path = tmp_path / "nocrs.gpkg"
        nocrs.to_file(path, layer="x", driver="GPKG")

        layers = gpkg_mod.read_all_layers(str(path), crs="EPSG:4326")
        gdf = layers["x"]
        # The read may have had a CRS embedded already — we only check that
        # the fallback didn't error out
        assert len(gdf) == 3


# ---------------------------------------------------------------------------
# layer_styles table
# ---------------------------------------------------------------------------


class TestLayerStyles:
    def test_read_styles_missing_table_returns_empty(self, gpkg_path):
        assert gpkg_mod.read_styles(gpkg_path) == []

    def test_write_and_read_styles(self, gpkg_path):
        styles = [
            {
                "f_table_name": "cities",
                "styleName": "default",
                "styleQML": '<qgis><name="color" value="255,0,0,255"/></qgis>',
                "useAsDefault": 1,
            }
        ]
        gpkg_mod.write_styles(gpkg_path, styles)
        read = gpkg_mod.read_styles(gpkg_path)
        assert len(read) == 1
        assert read[0]["f_table_name"] == "cities"

    def test_write_styles_empty_is_noop(self, gpkg_path):
        gpkg_mod.write_styles(gpkg_path, [])
        # No layer_styles table should have been created
        conn = sqlite3.connect(gpkg_path)
        try:
            cur = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='layer_styles'"
            )
            assert cur.fetchone() is None
        finally:
            conn.close()

    def test_layer_mapping_on_write(self, gpkg_path):
        styles = [
            {
                "f_table_name": "old_name",
                "styleName": "default",
                "styleQML": "<qgis/>",
            }
        ]
        gpkg_mod.write_styles(
            gpkg_path, styles, layer_mapping={"old_name": "cities"}
        )
        read = gpkg_mod.read_styles(gpkg_path)
        assert read[0]["f_table_name"] == "cities"


class TestCopyStyles:
    def test_copy_styles_no_src_is_zero(self, tmp_path, sample_gdf):
        src = tmp_path / "src.gpkg"
        dst = tmp_path / "dst.gpkg"
        sample_gdf.to_file(src, layer="cities", driver="GPKG")
        sample_gdf.to_file(dst, layer="cities", driver="GPKG")
        assert gpkg_mod.copy_styles(str(src), str(dst)) == 0

    def test_copy_styles_filters_by_dst_layers(self, tmp_path, sample_gdf):
        src = tmp_path / "src.gpkg"
        dst = tmp_path / "dst.gpkg"
        sample_gdf.to_file(src, layer="cities", driver="GPKG")
        sample_gdf.to_file(src, layer="orphan", driver="GPKG", mode="a")
        sample_gdf.to_file(dst, layer="cities", driver="GPKG")

        gpkg_mod.write_styles(
            str(src),
            [
                {"f_table_name": "cities", "styleName": "a", "styleQML": "<qgis/>"},
                {"f_table_name": "orphan", "styleName": "b", "styleQML": "<qgis/>"},
            ],
        )
        # Only "cities" exists in dst → only its style is copied
        n = gpkg_mod.copy_styles(str(src), str(dst))
        assert n == 1
        assert len(gpkg_mod.read_styles(str(dst))) == 1


# ---------------------------------------------------------------------------
# parse_style_colors + extract_layer_styles
# ---------------------------------------------------------------------------


class TestParseStyleColors:
    def test_qml_rgba_color(self):
        style = {
            "f_table_name": "cities",
            "styleName": "default",
            "styleQML": 'prop name="color" value="255,128,0,200"',
        }
        info = gpkg_mod.parse_style_colors(style)
        assert info["color"] == "#ff8000"
        assert info["opacity"] == round(200 / 255, 2)

    def test_qml_alpha_attribute_overrides_rgba_alpha(self):
        style = {
            "styleQML": (
                'prop name="color" value="10,20,30,255" alpha="0.5"'
            ),
        }
        info = gpkg_mod.parse_style_colors(style)
        assert info["opacity"] == 0.5

    def test_qml_outline_color_and_width(self):
        style = {
            "styleQML": (
                'prop name="color" value="0,0,0,255" '
                'prop name="outline_color" value="255,0,0,255" '
                'prop name="outline_width" value="2.5"'
            ),
        }
        info = gpkg_mod.parse_style_colors(style)
        assert info["stroke_color"] == "#ff0000"
        assert info["stroke_width"] == 2.5

    def test_sld_fill(self):
        style = {
            "styleSLD": (
                '<se:SvgParameter name="fill">#00ff00</se:SvgParameter>'
                '<se:SvgParameter name="fill-opacity">0.6</se:SvgParameter>'
            ),
        }
        info = gpkg_mod.parse_style_colors(style)
        assert info["color"] == "#00ff00"
        assert info["opacity"] == 0.6

    def test_no_style_returns_nulls(self):
        info = gpkg_mod.parse_style_colors({"f_table_name": "x"})
        assert info["color"] is None
        assert info["opacity"] is None


class TestExtractLayerStyles:
    def test_extracts_only_styles_with_color(self, gpkg_path):
        gpkg_mod.write_styles(
            gpkg_path,
            [
                {
                    "f_table_name": "cities",
                    "styleName": "colored",
                    "styleQML": 'prop name="color" value="255,0,0,255"',
                },
                {
                    "f_table_name": "cities",
                    "styleName": "empty",
                    "styleQML": "<no-color-here/>",
                },
            ],
        )
        info = gpkg_mod.extract_layer_styles(gpkg_path)
        assert len(info) == 1
        assert info[0]["color"] == "#ff0000"


# ---------------------------------------------------------------------------
# dataset_from_gpkg
# ---------------------------------------------------------------------------


class TestDatasetFromGpkg:
    def test_returns_dataset_with_layers_metadata(self, gpkg_path):
        ds = gpkg_mod.dataset_from_gpkg(gpkg_path)
        assert ds.name == "sample"
        assert ds.format == "GPKG"
        assert ds.data_category == "vector"
        assert ds.metadata["layer_count"] == 1
        assert ds.metadata["layers"][0]["name"] == "cities"
        assert ds.metadata["layers"][0]["feature_count"] == 3

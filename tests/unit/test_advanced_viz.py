"""Unit tests for R3 advanced visualization capabilities."""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import Point

from capabilities.classification import (
    BivariateChoroplethCapability,
    ContinuousRampCapability,
    GraduatedSizeCapability,
    HeadTailBreaksCapability,
)


@pytest.fixture
def linear_gdf() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"v": list(range(1, 11)), "geometry": [Point(i, i) for i in range(10)]},
        crs="EPSG:4326",
    )


@pytest.fixture
def heavy_tail_gdf() -> gpd.GeoDataFrame:
    rng = np.random.default_rng(0)
    bulk = rng.exponential(1.0, 200)
    mid = rng.exponential(5.0, 30)
    outliers = np.array([50, 80, 100])
    arr = np.concatenate([bulk, mid, outliers])
    return gpd.GeoDataFrame(
        {"v": arr, "geometry": [Point(i, 0) for i in range(len(arr))]},
        crs="EPSG:4326",
    )


# ── GraduatedSizeCapability ──────────────────────────────────────────────


class TestGraduatedSize:
    def test_basic_linear(self, linear_gdf):
        out = GraduatedSizeCapability().execute(
            linear_gdf, field="v", method="quantile", bins=5, size_range=[4, 20],
        )
        sizes = out["marker_size"].dropna().unique().tolist()
        assert min(sizes) == 4.0
        assert max(sizes) == 20.0
        assert len(sizes) == 5

    def test_sqrt_scaling(self, linear_gdf):
        """sqrt scaling compresses small classes, spreads large classes."""
        out_lin = GraduatedSizeCapability().execute(
            linear_gdf, field="v", bins=5, size_range=[4, 20], scaling="linear",
        )
        out_sqrt = GraduatedSizeCapability().execute(
            linear_gdf, field="v", bins=5, size_range=[4, 20], scaling="sqrt",
        )
        # Class 2 in sqrt should be > class 2 in linear
        c2_lin = out_lin[out_lin["class"] == 2]["marker_size"].iloc[0]
        c2_sqrt = out_sqrt[out_sqrt["class"] == 2]["marker_size"].iloc[0]
        assert c2_sqrt > c2_lin

    def test_log_scaling(self, linear_gdf):
        out = GraduatedSizeCapability().execute(
            linear_gdf, field="v", bins=5, size_range=[4, 20], scaling="log",
        )
        assert out["marker_size"].dropna().min() == 4.0
        assert out["marker_size"].dropna().max() == 20.0

    def test_style_metadata(self, linear_gdf):
        out = GraduatedSizeCapability().execute(
            linear_gdf, field="v", bins=5, size_range=[2, 16],
        )
        style = out.attrs["gispulse_style"]
        assert style["renderer"] == "graduated_size"
        assert style["sizeField"] == "v"
        assert style["sizeRange"] == [2.0, 16.0]

    def test_invalid_size_range(self, linear_gdf):
        with pytest.raises(ValueError, match="size_range"):
            GraduatedSizeCapability().execute(
                linear_gdf, field="v", size_range=[10, 5],
            )

    def test_invalid_scaling(self, linear_gdf):
        with pytest.raises(ValueError, match="scaling"):
            GraduatedSizeCapability().execute(
                linear_gdf, field="v", scaling="cubic",
            )

    def test_custom_size_col(self, linear_gdf):
        out = GraduatedSizeCapability().execute(
            linear_gdf, field="v", size_col="r", size_range=[2, 10],
        )
        assert "r" in out.columns
        assert "marker_size" not in out.columns

    def test_registered(self):
        from capabilities.registry import get
        assert isinstance(get("graduated_size"), GraduatedSizeCapability)


# ── HeadTailBreaksCapability ─────────────────────────────────────────────


class TestHeadTailBreaks:
    def test_heavy_tail_produces_classes(self, heavy_tail_gdf):
        out = HeadTailBreaksCapability().execute(heavy_tail_gdf, field="v")
        n_classes = int(out["class"].dropna().nunique())
        assert 3 <= n_classes <= 8, f"expected 3..8 classes on heavy-tail, got {n_classes}"

    def test_uniform_distribution_one_or_two_classes(self):
        """Near-uniform data should yield very few classes (algorithm stops early)."""
        rng = np.random.default_rng(0)
        arr = rng.uniform(0, 1, 300)
        gdf = gpd.GeoDataFrame(
            {"v": arr, "geometry": [Point(i, 0) for i in range(len(arr))]},
            crs="EPSG:4326",
        )
        out = HeadTailBreaksCapability().execute(gdf, field="v")
        # Uniform → head ratio ~0.5 > cutoff 0.4 → stops quickly (≤3 classes)
        n = int(out["class"].dropna().nunique())
        assert n <= 3

    def test_with_palette(self, heavy_tail_gdf):
        out = HeadTailBreaksCapability().execute(
            heavy_tail_gdf, field="v", palette="YlOrRd",
        )
        assert "color" in out.columns
        assert out["color"].dropna().nunique() >= 2

    def test_style_metadata(self, heavy_tail_gdf):
        out = HeadTailBreaksCapability().execute(heavy_tail_gdf, field="v")
        style = out.attrs["gispulse_style"]
        assert style["method"] == "head_tail_breaks"
        assert "breaks" in style

    def test_monotonic_breaks(self, heavy_tail_gdf):
        """Breaks must be ascending (cumulative means of heads)."""
        out = HeadTailBreaksCapability().execute(heavy_tail_gdf, field="v")
        breaks = out.attrs["gispulse_style"]["breaks"]
        assert breaks == sorted(breaks)

    def test_registered(self):
        from capabilities.registry import get
        assert isinstance(get("head_tail_breaks"), HeadTailBreaksCapability)


# ── ContinuousRampCapability ─────────────────────────────────────────────


class TestContinuousRamp:
    def test_linear_ramp(self):
        gdf = gpd.GeoDataFrame(
            {"d": [0.0, 0.5, 1.0], "geometry": [Point(i, 0) for i in range(3)]},
            crs="EPSG:4326",
        )
        out = ContinuousRampCapability().execute(gdf, field="d", palette="Viridis", scaling="linear")
        colors = out["color"].tolist()
        # Start is darkest (Viridis starts at purple), end is brightest yellow
        assert colors[0].startswith("#4")  # #440154-ish
        assert colors[-1].startswith("#f") or colors[-1].startswith("#e")

    def test_nan_preserved(self):
        gdf = gpd.GeoDataFrame(
            {"v": [1.0, 2.0, None, 4.0], "geometry": [Point(i, 0) for i in range(4)]},
            crs="EPSG:4326",
        )
        out = ContinuousRampCapability().execute(gdf, field="v", palette="Viridis")
        assert out["color"].iloc[2] is None

    def test_log_scaling(self):
        gdf = gpd.GeoDataFrame(
            {"v": [1, 10, 100, 1000], "geometry": [Point(i, 0) for i in range(4)]},
            crs="EPSG:4326",
        )
        out = ContinuousRampCapability().execute(gdf, field="v", palette="Viridis", scaling="log")
        colors = out["color"].dropna().tolist()
        assert len(colors) == 4
        assert all(c.startswith("#") for c in colors)

    def test_log_rejects_nonpositive(self):
        gdf = gpd.GeoDataFrame(
            {"v": [0, 10, 100], "geometry": [Point(i, 0) for i in range(3)]},
            crs="EPSG:4326",
        )
        out = ContinuousRampCapability().execute(gdf, field="v", palette="Viridis", scaling="log")
        # log(0) → NaN, not an exception
        assert out["color"].iloc[0] is None

    def test_explicit_domain(self):
        """Values outside the domain clip to palette extremes."""
        gdf = gpd.GeoDataFrame(
            {"v": [-5, 0, 50, 100, 200], "geometry": [Point(i, 0) for i in range(5)]},
            crs="EPSG:4326",
        )
        out = ContinuousRampCapability().execute(
            gdf, field="v", palette="Viridis", domain=[0, 100],
        )
        # -5 clips to lowest, 200 clips to highest
        assert out["color"].iloc[0] == out["color"].iloc[1]
        assert out["color"].iloc[-1] == out["color"].iloc[-2]

    def test_invalid_scaling(self):
        gdf = gpd.GeoDataFrame(
            {"v": [1, 2, 3], "geometry": [Point(i, 0) for i in range(3)]},
            crs="EPSG:4326",
        )
        with pytest.raises(ValueError, match="scaling"):
            ContinuousRampCapability().execute(gdf, field="v", scaling="cubic")

    def test_style_metadata(self):
        gdf = gpd.GeoDataFrame(
            {"v": [1, 2, 3], "geometry": [Point(i, 0) for i in range(3)]},
            crs="EPSG:4326",
        )
        out = ContinuousRampCapability().execute(gdf, field="v", palette="Plasma")
        style = out.attrs["gispulse_style"]
        assert style["renderer"] == "continuous"
        assert style["palette"] == "Plasma"
        assert style["domain"] == [1.0, 3.0]

    def test_registered(self):
        from capabilities.registry import get
        assert isinstance(get("continuous_ramp"), ContinuousRampCapability)


# ── BivariateChoroplethCapability ────────────────────────────────────────


class TestBivariate:
    def _gdf(self) -> gpd.GeoDataFrame:
        """9 features where price increases, volume decreases — diagonal pattern."""
        return gpd.GeoDataFrame(
            {
                "price": list(range(1, 10)),
                "volume": list(range(9, 0, -1)),
                "geometry": [Point(i, 0) for i in range(9)],
            },
            crs="EPSG:4326",
        )

    def test_basic_3x3(self):
        out = BivariateChoroplethCapability().execute(
            self._gdf(), field_x="price", field_y="volume", grid=3, palette="BlueOrange",
        )
        assert "bi_class" in out.columns
        assert "bi_color" in out.columns
        classes = out["bi_class"].dropna().unique().tolist()
        # Each cell is a "y_x" string
        assert all(c.count("_") == 1 for c in classes)

    def test_class_format(self):
        out = BivariateChoroplethCapability().execute(
            self._gdf(), field_x="price", field_y="volume", grid=3,
        )
        # The low-price/high-volume rows should be in class "3_1"
        # (y=3 = high volume, x=1 = low price)
        low_price_high_vol = out[(out["price"] <= 3) & (out["volume"] >= 7)]
        assert (low_price_high_vol["bi_class"] == "3_1").all()

    def test_legend_grid(self):
        out = BivariateChoroplethCapability().execute(
            self._gdf(), field_x="price", field_y="volume", grid=3,
        )
        legend = out.attrs["gispulse_legend"]
        assert legend["type"] == "bivariate_legend"
        assert legend["grid"] == 3
        assert len(legend["cells"]) == 9
        # Each cell has x_class, y_class, color
        for cell in legend["cells"]:
            assert "x_class" in cell and "y_class" in cell and "color" in cell

    def test_style_matrix(self):
        out = BivariateChoroplethCapability().execute(
            self._gdf(), field_x="price", field_y="volume", grid=3, palette="PurpleGreen",
        )
        style = out.attrs["gispulse_style"]
        assert style["renderer"] == "bivariate"
        assert len(style["matrix"]) == 3
        assert len(style["matrix"][0]) == 3

    def test_grid_4(self):
        out = BivariateChoroplethCapability().execute(
            self._gdf(), field_x="price", field_y="volume", grid=4, palette="BlueOrange",
        )
        assert len(out.attrs["gispulse_legend"]["cells"]) == 16

    def test_missing_fields(self):
        with pytest.raises(ValueError, match="field_x.*field_y"):
            BivariateChoroplethCapability().execute(
                self._gdf(), field_x="price",
            )

    def test_invalid_grid(self):
        with pytest.raises(ValueError, match="grid must be"):
            BivariateChoroplethCapability().execute(
                self._gdf(), field_x="price", field_y="volume", grid=10,
            )

    def test_unknown_palette(self):
        with pytest.raises(ValueError, match="Unknown bivariate palette"):
            BivariateChoroplethCapability().execute(
                self._gdf(), field_x="price", field_y="volume", palette="NotAPalette",
            )

    def test_nan_inputs_preserved(self):
        """NaN on either axis → no class assigned."""
        gdf = gpd.GeoDataFrame(
            {
                "x": [1, 2, None, 4, 5, 6, 7, 8, 9],
                "y": [9, None, 7, 6, 5, 4, 3, 2, 1],
                "geometry": [Point(i, 0) for i in range(9)],
            },
            crs="EPSG:4326",
        )
        out = BivariateChoroplethCapability().execute(gdf, field_x="x", field_y="y", grid=3)
        assert pd.isna(out["bi_class"].iloc[2])  # NaN in x
        assert pd.isna(out["bi_class"].iloc[1])  # NaN in y

    def test_registered(self):
        from capabilities.registry import get
        assert isinstance(get("bivariate_choropleth"), BivariateChoroplethCapability)

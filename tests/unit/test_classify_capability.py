"""Unit tests for ClassifyCapability."""

from __future__ import annotations

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point

from capabilities.classification import ClassifyCapability


@pytest.fixture
def price_gdf() -> gpd.GeoDataFrame:
    """10 features with monotonically increasing prices — easy to quintile."""
    return gpd.GeoDataFrame(
        {
            "id": list(range(1, 11)),
            "price_per_m2": [1000.0, 2000.0, 3000.0, 4000.0, 5000.0,
                             6000.0, 7000.0, 8000.0, 9000.0, 10000.0],
            "geometry": [Point(i, i) for i in range(10)],
        },
        crs="EPSG:4326",
    )


_YLORRD5 = ["#ffffb2", "#fecc5c", "#fd8d3c", "#f03b20", "#bd0026"]


class TestClassifyCapability:
    def test_quantile_five_bins(self, price_gdf):
        """Quintiles on 10 sorted values -> each bin has 2 features."""
        out = ClassifyCapability().execute(
            price_gdf, field="price_per_m2", method="quantile", bins=5
        )
        assert "class" in out.columns
        # 10 points / 5 bins -> counts should be exactly 2 per bin
        counts = out["class"].value_counts().sort_index()
        assert list(counts.values) == [2, 2, 2, 2, 2]
        # lowest value gets class 1, highest gets class 5
        assert out.loc[out["price_per_m2"] == 1000.0, "class"].iloc[0] == 1
        assert out.loc[out["price_per_m2"] == 10000.0, "class"].iloc[0] == 5

    def test_palette_adds_color_column(self, price_gdf):
        out = ClassifyCapability().execute(
            price_gdf, field="price_per_m2", method="quantile", bins=5,
            palette=_YLORRD5,
        )
        assert "color" in out.columns
        assert out.loc[out["price_per_m2"] == 1000.0, "color"].iloc[0] == "#ffffb2"
        assert out.loc[out["price_per_m2"] == 10000.0, "color"].iloc[0] == "#bd0026"

    def test_custom_class_and_color_cols(self, price_gdf):
        out = ClassifyCapability().execute(
            price_gdf, field="price_per_m2", bins=5,
            class_col="price_class", color_col="price_color",
            palette=_YLORRD5,
        )
        assert "price_class" in out.columns
        assert "price_color" in out.columns
        assert "class" not in out.columns
        assert "color" not in out.columns

    def test_palette_length_mismatch(self, price_gdf):
        with pytest.raises(ValueError, match="palette length"):
            ClassifyCapability().execute(
                price_gdf, field="price_per_m2", bins=5,
                palette=["#000", "#fff"],
            )

    def test_unknown_field(self, price_gdf):
        with pytest.raises(ValueError, match="not in layer columns"):
            ClassifyCapability().execute(price_gdf, field="does_not_exist")

    def test_bad_method(self, price_gdf):
        with pytest.raises(ValueError, match="method must be"):
            ClassifyCapability().execute(
                price_gdf, field="price_per_m2", method="kmeans"
            )

    def test_manual_breaks(self, price_gdf):
        out = ClassifyCapability().execute(
            price_gdf, field="price_per_m2", method="manual", bins=4,
            breaks=[0, 3000, 5000, 7000, 11000],
        )
        counts = out["class"].value_counts().sort_index()
        # [0,3000]=3 features (1,2,3k), (3k,5k]=2 (4,5k), (5k,7k]=2 (6,7k), (7k,11k]=3 (8,9,10k)
        assert list(counts.values) == [3, 2, 2, 3]

    def test_manual_bad_breaks_length(self, price_gdf):
        with pytest.raises(ValueError, match="requires 'breaks' with"):
            ClassifyCapability().execute(
                price_gdf, field="price_per_m2", method="manual", bins=4,
                breaks=[0, 5000, 10000],  # should be 5 values
            )

    def test_equal_interval(self, price_gdf):
        out = ClassifyCapability().execute(
            price_gdf, field="price_per_m2", method="equal_interval", bins=5
        )
        # range 1000..10000, width 1800 per bin
        # bin 1 = [1000, 2800]: 1000, 2000 -> 2
        # bin 2 = (2800, 4600]: 3000, 4000 -> 2
        # bin 3 = (4600, 6400]: 5000, 6000 -> 2
        # bin 4 = (6400, 8200]: 7000, 8000 -> 2
        # bin 5 = (8200, 10000]: 9000, 10000 -> 2
        counts = out["class"].value_counts().sort_index()
        assert list(counts.values) == [2, 2, 2, 2, 2]

    def test_equal_interval_constant_column(self):
        """All values equal -> everyone lands in class 1."""
        gdf = gpd.GeoDataFrame(
            {"v": [5.0, 5.0, 5.0], "geometry": [Point(i, i) for i in range(3)]},
            crs="EPSG:4326",
        )
        out = ClassifyCapability().execute(
            gdf, field="v", method="equal_interval", bins=3
        )
        assert (out["class"] == 1).all()

    def test_nan_preserved(self):
        """Features with NaN in classified field get NaN class + None color."""
        gdf = gpd.GeoDataFrame(
            {
                "v": [1.0, 2.0, None, 4.0, 5.0],
                "geometry": [Point(i, i) for i in range(5)],
            },
            crs="EPSG:4326",
        )
        out = ClassifyCapability().execute(
            gdf, field="v", method="quantile", bins=2,
            palette=["#aaa", "#bbb"],
        )
        # Feature with NaN input must have NaN class and null color
        # (pd.isna handles both None and np.nan — pandas 3.0 coerces None→NaN
        # in string-dtype columns)
        nan_row = out[out["v"].isna()]
        assert pd.isna(nan_row["class"].iloc[0])
        assert pd.isna(nan_row["color"].iloc[0])

    def test_qcut_with_ties_does_not_crash(self):
        """Heavily tied data (many zeros) used to raise 'Bin edges must be unique'.
        With duplicates='drop' it now degrades gracefully to fewer effective bins."""
        gdf = gpd.GeoDataFrame(
            {
                "v": [0, 0, 0, 0, 0, 1, 2, 3, 4, 5],
                "geometry": [Point(i, i) for i in range(10)],
            },
            crs="EPSG:4326",
        )
        # Should not raise
        out = ClassifyCapability().execute(gdf, field="v", method="quantile", bins=5)
        assert "class" in out.columns

    def test_schema(self):
        schema = ClassifyCapability().get_schema()
        assert schema["required"] == ["field"]
        assert "quantile" in schema["properties"]["method"]["enum"]
        assert "jenks" in schema["properties"]["method"]["enum"]
        assert "pretty" in schema["properties"]["method"]["enum"]
        assert "std_dev" in schema["properties"]["method"]["enum"]

    # ── Named palettes ───────────────────────────────────────────────────

    def test_named_palette(self, price_gdf):
        """palette='YlOrRd' resolves to the ColorBrewer 5-class palette."""
        out = ClassifyCapability().execute(
            price_gdf, field="price_per_m2", bins=5, palette="YlOrRd",
        )
        assert "color" in out.columns
        # ColorBrewer YlOrRd 5 first/last
        assert out.loc[out["price_per_m2"] == 1000.0, "color"].iloc[0] == "#ffffb2"
        assert out.loc[out["price_per_m2"] == 10000.0, "color"].iloc[0] == "#bd0026"

    def test_named_palette_resampled(self, price_gdf):
        """Request bins=6 for YlOrRd (stored only at 3/5/7/9) — should interpolate."""
        out = ClassifyCapability().execute(
            price_gdf, field="price_per_m2", bins=6, palette="YlOrRd",
        )
        assert "color" in out.columns
        non_null = out["color"].dropna().unique()
        assert 2 <= len(non_null) <= 6

    def test_unknown_palette_name(self, price_gdf):
        with pytest.raises(ValueError, match="Unknown palette"):
            ClassifyCapability().execute(
                price_gdf, field="price_per_m2", bins=5, palette="NotAPalette",
            )

    # ── Jenks ────────────────────────────────────────────────────────────

    def test_jenks_basic(self, price_gdf):
        """Jenks on monotonic data produces bins+1 breaks and class range 1..bins."""
        out = ClassifyCapability().execute(
            price_gdf, field="price_per_m2", method="jenks", bins=5,
        )
        classes = out["class"].dropna().unique()
        assert set(classes).issubset({1, 2, 3, 4, 5})
        # Breaks stored in attrs
        assert "breaks" in out.attrs["gispulse_style"]
        assert len(out.attrs["gispulse_style"]["breaks"]) == 6

    def test_jenks_two_clusters(self):
        """Jenks separates two well-defined clusters into distinct top/bottom classes."""
        import numpy as np
        rng = np.random.default_rng(42)
        # 50 points near 1.0, 50 points near 10.0 — trivially separable
        cluster_a = rng.normal(1.0, 0.3, 50)
        cluster_b = rng.normal(10.0, 0.3, 50)
        values = np.concatenate([cluster_a, cluster_b])
        gdf = gpd.GeoDataFrame(
            {"v": values, "geometry": [Point(i, i) for i in range(len(values))]},
            crs="EPSG:4326",
        )
        out = ClassifyCapability().execute(gdf, field="v", method="jenks", bins=2)
        # All low-cluster features in class 1, all high-cluster in class 2
        low_classes = out[out["v"] < 5.0]["class"].unique()
        high_classes = out[out["v"] > 5.0]["class"].unique()
        assert len(low_classes) == 1
        assert len(high_classes) == 1
        assert low_classes[0] != high_classes[0]

    def test_jenks_with_palette(self, price_gdf):
        out = ClassifyCapability().execute(
            price_gdf, field="price_per_m2", method="jenks", bins=5, palette="Viridis",
        )
        assert "color" in out.columns
        # Viridis starts at deep purple ~#440154
        lowest = out.loc[out["price_per_m2"] == 1000.0, "color"].iloc[0]
        assert lowest.lower().startswith("#4")

    # ── Pretty breaks ────────────────────────────────────────────────────

    def test_pretty_breaks(self, price_gdf):
        """Pretty breaks produce round-number edges."""
        out = ClassifyCapability().execute(
            price_gdf, field="price_per_m2", method="pretty", bins=5,
        )
        breaks = out.attrs["gispulse_style"]["breaks"]
        # All breaks should be multiples of the step (2000 for 1000..10000 / 5)
        for b in breaks:
            assert b == round(b / 1000) * 1000, f"break {b} is not a round thousand"

    def test_pretty_constant_column(self):
        gdf = gpd.GeoDataFrame(
            {"v": [42.0, 42.0, 42.0], "geometry": [Point(i, i) for i in range(3)]},
            crs="EPSG:4326",
        )
        out = ClassifyCapability().execute(gdf, field="v", method="pretty", bins=3)
        assert "class" in out.columns

    # ── Standard deviation ───────────────────────────────────────────────

    def test_std_dev_basic(self):
        """std_dev on N(0,1) with bins=5 produces 4 interior breaks symmetric around μ."""
        import numpy as np
        rng = np.random.default_rng(0)
        values = rng.normal(0.0, 1.0, 500)
        gdf = gpd.GeoDataFrame(
            {"v": values, "geometry": [Point(i, i) for i in range(len(values))]},
            crs="EPSG:4326",
        )
        out = ClassifyCapability().execute(gdf, field="v", method="std_dev", bins=5)
        breaks = out.attrs["gispulse_style"]["breaks"]
        interior = breaks[1:-1]
        assert len(interior) == 4
        # Symmetric around mean: breaks at μ ± 1.5σ, μ ± 0.5σ
        # With μ≈0, σ≈1: expect roughly [-1.5, -0.5, 0.5, 1.5]
        assert abs(interior[0] + 1.5) < 0.3
        assert abs(interior[1] + 0.5) < 0.2
        assert abs(interior[2] - 0.5) < 0.2
        assert abs(interior[3] - 1.5) < 0.3

    def test_std_dev_requires_odd_bins(self, price_gdf):
        with pytest.raises(ValueError, match="odd"):
            ClassifyCapability().execute(
                price_gdf, field="price_per_m2", method="std_dev", bins=4,
            )

    def test_std_dev_with_multiplier(self):
        """std_multiplier=0.5 tightens the bands."""
        import numpy as np
        rng = np.random.default_rng(1)
        values = rng.normal(10.0, 2.0, 300)
        gdf = gpd.GeoDataFrame(
            {"v": values, "geometry": [Point(i, i) for i in range(len(values))]},
            crs="EPSG:4326",
        )
        out_wide = ClassifyCapability().execute(
            gdf, field="v", method="std_dev", bins=5, std_multiplier=1.0,
        )
        out_tight = ClassifyCapability().execute(
            gdf, field="v", method="std_dev", bins=5, std_multiplier=0.5,
        )
        # Tighter bands: distance between interior breaks should be smaller
        wide_b = out_wide.attrs["gispulse_style"]["breaks"]
        tight_b = out_tight.attrs["gispulse_style"]["breaks"]
        assert (wide_b[-2] - wide_b[1]) > (tight_b[-2] - tight_b[1])

    # ── Metadata / style attrs ───────────────────────────────────────────

    def test_style_metadata_attached(self, price_gdf):
        """gispulse_style attrs are populated for downstream consumers."""
        out = ClassifyCapability().execute(
            price_gdf, field="price_per_m2", method="quantile", bins=5,
            palette="YlOrRd",
        )
        meta = out.attrs["gispulse_style"]
        assert meta["field"] == "price_per_m2"
        assert meta["method"] == "quantile"
        assert meta["palette"] == "YlOrRd"
        assert meta["bins"] == 5

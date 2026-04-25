"""Unit tests for ClassifyCategoricalCapability and NormalizeCapability."""

from __future__ import annotations

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point

from capabilities.classification import (
    ClassifyCategoricalCapability,
    NormalizeCapability,
)


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def ftth_gdf() -> gpd.GeoDataFrame:
    """FTTH-ish data: 3 common statuses + 1 rare + 1 NaN."""
    return gpd.GeoDataFrame(
        {
            "status": [
                "deployed", "planned", "deployed", "in_progress",
                "deployed", "planned", "cancelled", None,
                "deployed", "planned",
            ],
            "geometry": [Point(i, i) for i in range(10)],
        },
        crs="EPSG:4326",
    )


@pytest.fixture
def pop_gdf() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "pop": [100, 500, 1000, 5000, 10000, 20000, 0, None],
            "area": [1, 2, 5, 10, 20, 50, 0, 10],
            "geometry": [Point(i, i) for i in range(8)],
        },
        crs="EPSG:4326",
    )


# ── ClassifyCategoricalCapability ────────────────────────────────────────


class TestClassifyCategorical:
    def test_basic_named_palette(self, ftth_gdf):
        out = ClassifyCategoricalCapability().execute(ftth_gdf, field="status", palette="Set2")
        assert "class" in out.columns
        assert "color" in out.columns
        # Most frequent value ("deployed", n=4) gets class 1
        deployed_classes = out[out["status"] == "deployed"]["class"].unique()
        assert list(deployed_classes) == [1]
        # NaN preserved
        assert pd.isna(out[out["status"].isna()]["class"].iloc[0])
        assert out[out["status"].isna()]["color"].iloc[0] is None

    def test_frequency_sort(self, ftth_gdf):
        """Class 1 must be the most frequent, class 4 the rarest."""
        out = ClassifyCategoricalCapability().execute(ftth_gdf, field="status", palette="Set2")
        # counts: deployed=4, planned=3, in_progress=1, cancelled=1
        counts = out.dropna(subset=["class"]).groupby("class")["status"].count().to_dict()
        assert counts[1] == 4  # deployed

    def test_explicit_dict_palette(self, ftth_gdf):
        """Unmapped values collapse into the 'other' bucket."""
        out = ClassifyCategoricalCapability().execute(
            ftth_gdf,
            field="status",
            palette={"deployed": "#2ca02c", "planned": "#ff7f0e"},
        )
        # deployed & planned keep their colors
        assert out.loc[out["status"] == "deployed", "color"].unique().tolist() == ["#2ca02c"]
        assert out.loc[out["status"] == "planned", "color"].unique().tolist() == ["#ff7f0e"]
        # "in_progress" and "cancelled" land in the "other" class with default grey
        other_rows = out[out["status"].isin(["in_progress", "cancelled"])]
        assert other_rows["class"].nunique() == 1
        assert other_rows["color"].unique().tolist() == ["#bdbdbd"]

    def test_max_categories_creates_other_bucket(self, ftth_gdf):
        """Limit to top-2 → 'in_progress' and 'cancelled' collapse into 'other'."""
        out = ClassifyCategoricalCapability().execute(
            ftth_gdf, field="status", palette="Set2", max_categories=2, other_label="Autre",
        )
        # Named classes: deployed (1), planned (2). Rest: class 3 (other).
        tail = out[out["status"].isin(["in_progress", "cancelled"])]
        assert tail["class"].nunique() == 1
        assert tail["class"].unique().tolist() == [3]
        # Legend labels the tail as "Autre"
        legend = out.attrs["gispulse_legend"]
        other_cls = [c for c in legend["classes"] if c["value"] is None]
        assert len(other_cls) == 1
        assert other_cls[0]["label"] == "Autre"

    def test_layerstyle_categorized_renderer(self, ftth_gdf):
        out = ClassifyCategoricalCapability().execute(ftth_gdf, field="status", palette="Set2")
        style = out.attrs["gispulse_style"]
        assert style["renderer"] == "categorized"
        assert style["classField"] == "status"
        # All 4 categories (no other bucket) — points → point symbols
        assert len(style["categories"]) == 4
        for cat in style["categories"]:
            assert cat["symbol"]["kind"] == "point"

    def test_qml_sld_compatible_output(self, ftth_gdf):
        """Style attrs consumable by both QML and SLD converters."""
        from persistence.sld_converter import style_def_to_sld
        from persistence.style_converter import style_def_to_qml
        out = ClassifyCategoricalCapability().execute(ftth_gdf, field="status", palette="Set2")
        style = out.attrs["gispulse_style"]
        qml = style_def_to_qml(style, geom_type="point")
        sld = style_def_to_sld(style, geom_type="point", layer_name="ftth")
        assert "categorizedSymbol" in qml
        assert "PropertyIsEqualTo" in sld
        assert sld.count("<se:Rule>") == 4  # 4 categories

    def test_field_required(self, ftth_gdf):
        with pytest.raises(ValueError, match="'field' parameter is required"):
            ClassifyCategoricalCapability().execute(ftth_gdf, palette="Set2")

    def test_unknown_field(self, ftth_gdf):
        with pytest.raises(ValueError, match="not in layer columns"):
            ClassifyCategoricalCapability().execute(ftth_gdf, field="nope", palette="Set2")

    def test_invalid_palette_type(self, ftth_gdf):
        with pytest.raises(TypeError, match="palette must be"):
            ClassifyCategoricalCapability().execute(ftth_gdf, field="status", palette=42)  # type: ignore[arg-type]

    def test_schema(self):
        schema = ClassifyCategoricalCapability().get_schema()
        assert schema["required"] == ["field"]
        assert "max_categories" in schema["properties"]

    def test_registered(self):
        from capabilities.registry import get
        inst = get("classify_categorical")
        assert isinstance(inst, ClassifyCategoricalCapability)


# ── NormalizeCapability ──────────────────────────────────────────────────


class TestNormalize:
    def test_minmax(self, pop_gdf):
        out = NormalizeCapability().execute(pop_gdf, field="pop", method="minmax")
        vals = out["pop_minmax"].dropna().tolist()
        assert min(vals) == 0.0
        assert max(vals) == 1.0

    def test_minmax_constant(self):
        gdf = gpd.GeoDataFrame(
            {"v": [5.0, 5.0, 5.0], "geometry": [Point(i, i) for i in range(3)]},
            crs="EPSG:4326",
        )
        out = NormalizeCapability().execute(gdf, field="v", method="minmax")
        # Degenerate: all values equal → all map to 0.5
        assert (out["v_minmax"] == 0.5).all()

    def test_zscore(self):
        import numpy as np
        rng = np.random.default_rng(0)
        gdf = gpd.GeoDataFrame(
            {"v": rng.normal(10.0, 2.0, 200), "geometry": [Point(i, 0) for i in range(200)]},
            crs="EPSG:4326",
        )
        out = NormalizeCapability().execute(gdf, field="v", method="zscore")
        arr = out["v_zscore"].to_numpy()
        # Mean ≈ 0, std ≈ 1
        assert abs(arr.mean()) < 0.05
        assert abs(arr.std() - 1.0) < 0.05

    def test_log_rejects_nonpositive(self, pop_gdf):
        with pytest.raises(ValueError, match="non-positive"):
            NormalizeCapability().execute(pop_gdf, field="pop", method="log")

    def test_log1p_accepts_zero(self, pop_gdf):
        out = NormalizeCapability().execute(pop_gdf, field="pop", method="log1p")
        # log1p(0) = 0, log1p(100)≈4.61
        assert out.loc[pop_gdf["pop"] == 0, "pop_log1p"].iloc[0] == 0.0

    def test_rank_normalized(self, pop_gdf):
        out = NormalizeCapability().execute(pop_gdf, field="pop", method="rank")
        vals = out["pop_rank"].dropna().tolist()
        assert min(vals) == 0.0
        assert max(vals) == 1.0

    def test_percent_sums_to_100(self, pop_gdf):
        out = NormalizeCapability().execute(pop_gdf, field="pop", method="percent")
        assert abs(out["pop_percent"].dropna().sum() - 100.0) < 1e-6

    def test_denom_field(self, pop_gdf):
        """pop / area, then minmax — produces a density normalized choropleth input."""
        out = NormalizeCapability().execute(
            pop_gdf, field="pop", method="minmax", denom_field="area", out_field="density_norm",
        )
        # Row where area=0 → NaN (no infinite density)
        assert pd.isna(out[out["area"] == 0]["density_norm"].iloc[0])

    def test_denom_field_missing(self, pop_gdf):
        with pytest.raises(ValueError, match="denom_field"):
            NormalizeCapability().execute(pop_gdf, field="pop", denom_field="nope")

    def test_custom_out_field(self, pop_gdf):
        out = NormalizeCapability().execute(
            pop_gdf, field="pop", method="minmax", out_field="norm_0_1",
        )
        assert "norm_0_1" in out.columns
        assert "pop_minmax" not in out.columns

    def test_nan_preserved(self, pop_gdf):
        out = NormalizeCapability().execute(pop_gdf, field="pop", method="minmax")
        assert pd.isna(out.loc[pop_gdf["pop"].isna(), "pop_minmax"].iloc[0])

    def test_field_required(self, pop_gdf):
        with pytest.raises(ValueError, match="'field' parameter is required"):
            NormalizeCapability().execute(pop_gdf)

    def test_unknown_method(self, pop_gdf):
        with pytest.raises(ValueError, match="method must be"):
            NormalizeCapability().execute(pop_gdf, field="pop", method="unknown")

    def test_schema(self):
        schema = NormalizeCapability().get_schema()
        assert schema["required"] == ["field"]
        assert "minmax" in schema["properties"]["method"]["enum"]
        assert "denom_field" in schema["properties"]

    def test_registered(self):
        from capabilities.registry import get
        inst = get("normalize")
        assert isinstance(inst, NormalizeCapability)

    def test_chain_into_classify(self, pop_gdf):
        """Common pipeline: normalize → choropleth."""
        from capabilities.classification import ChoroplethCapability
        normalized = NormalizeCapability().execute(
            pop_gdf, field="pop", method="log1p", out_field="pop_log",
        )
        out = ChoroplethCapability().execute(
            normalized, field="pop_log", method="quantile", bins=3, palette="YlOrRd",
        )
        assert "class" in out.columns
        assert "gispulse_style" in out.attrs

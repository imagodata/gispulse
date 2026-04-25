"""Coverage for PivotCapability + UnpivotCapability — schema reshape ops.

Audit deep 2026-04-24 v3 §6 flagged both capabilities at 0 tests despite
PivotCapability shipping a new ``geom_strategy`` parameter (commit d56d10b).
This module locks down the contract: nominal happy-path, the three
geom_strategy branches, missing-input validation, melt round-trip, and
geometry preservation across reshape.
"""
from __future__ import annotations

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point, Polygon

from capabilities.registry import get as cap_get
import capabilities  # noqa: F401


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def long_pop() -> gpd.GeoDataFrame:
    """Long-format population per parcel × year (year stored as ``y2020`` etc.
    so pivot output column names remain valid identifiers — pure ints get
    cast to ``"2020"`` which fails `_validate_ident`)."""
    return gpd.GeoDataFrame(
        {
            "parcel_id": ["A", "A", "A", "B", "B", "B"],
            "year": ["y2020", "y2021", "y2022", "y2020", "y2021", "y2022"],
            "pop": [10, 11, 12, 20, 22, 24],
            "geometry": [
                Point(0, 0), Point(0, 0), Point(0, 0),
                Point(1, 1), Point(1, 1), Point(1, 1),
            ],
        },
        crs="EPSG:3857",
    )


@pytest.fixture
def long_pop_divergent_geom() -> gpd.GeoDataFrame:
    """Long-format with divergent geometries within the same index group."""
    return gpd.GeoDataFrame(
        {
            "parcel_id": ["A", "A", "A"],
            "year": ["y2020", "y2021", "y2022"],
            "pop": [10, 11, 12],
            "geometry": [Point(0, 0), Point(0, 1), Point(0, 2)],
        },
        crs="EPSG:3857",
    )


@pytest.fixture
def wide_pop() -> gpd.GeoDataFrame:
    """Wide-format counterpart of long_pop — used for unpivot round-trip."""
    return gpd.GeoDataFrame(
        {
            "parcel_id": ["A", "B"],
            "pop_2020": [10, 20],
            "pop_2021": [11, 22],
            "pop_2022": [12, 24],
            "geometry": [Point(0, 0), Point(1, 1)],
        },
        crs="EPSG:3857",
    )


# ---------------------------------------------------------------------------
# PivotCapability
# ---------------------------------------------------------------------------


class TestPivot:
    def test_nominal_long_to_wide(self, long_pop):
        cap = cap_get("pivot")
        out = cap.execute(
            long_pop,
            index="parcel_id",
            columns="year",
            values="pop",
        )
        assert isinstance(out, gpd.GeoDataFrame)
        assert set(out["parcel_id"]) == {"A", "B"}
        # Three years become three columns prefixed with the value column name
        # (pandas pivot_table convention to avoid identifier collisions).
        for col in ("pop_y2020", "pop_y2021", "pop_y2022"):
            assert col in out.columns
        row_a = out[out["parcel_id"] == "A"].iloc[0]
        assert row_a["pop_y2020"] == 10 and row_a["pop_y2022"] == 12
        # Geometry preserved.
        assert out.geometry.iloc[0].geom_type == "Point"

    def test_geom_strategy_first_picks_first_geom(self, long_pop_divergent_geom):
        cap = cap_get("pivot")
        out = cap.execute(
            long_pop_divergent_geom,
            index="parcel_id", columns="year", values="pop",
            geom_strategy="first",
        )
        assert len(out) == 1
        assert out.geometry.iloc[0].equals(Point(0, 0))

    def test_geom_strategy_raise_if_differs_raises_on_divergent(
        self, long_pop_divergent_geom
    ):
        cap = cap_get("pivot")
        with pytest.raises(ValueError, match=r"divergent geometries"):
            cap.execute(
                long_pop_divergent_geom,
                index="parcel_id", columns="year", values="pop",
                geom_strategy="raise_if_differs",
            )

    def test_geom_strategy_union_dissolves_divergent(
        self, long_pop_divergent_geom
    ):
        cap = cap_get("pivot")
        out = cap.execute(
            long_pop_divergent_geom,
            index="parcel_id", columns="year", values="pop",
            geom_strategy="union",
        )
        assert len(out) == 1
        # Three Points → MultiPoint via dissolve.
        assert out.geometry.iloc[0].geom_type == "MultiPoint"
        assert len(out.geometry.iloc[0].geoms) == 3

    def test_missing_index_raises(self, long_pop):
        cap = cap_get("pivot")
        with pytest.raises(ValueError, match="requires 'index'"):
            cap.execute(long_pop, columns="year", values="pop")

    def test_unknown_column_raises(self, long_pop):
        cap = cap_get("pivot")
        with pytest.raises(KeyError, match="not in layer"):
            cap.execute(long_pop, index="parcel_id", columns="missing", values="pop")

    def test_invalid_geom_strategy_rejected(self, long_pop):
        cap = cap_get("pivot")
        with pytest.raises(ValueError, match="geom_strategy must be"):
            cap.execute(
                long_pop,
                index="parcel_id", columns="year", values="pop",
                geom_strategy="bogus",
            )

    def test_invalid_aggfunc_rejected(self, long_pop):
        cap = cap_get("pivot")
        with pytest.raises(ValueError, match="aggfunc must be"):
            cap.execute(
                long_pop,
                index="parcel_id", columns="year", values="pop",
                aggfunc="totally_not_a_func",
            )


# ---------------------------------------------------------------------------
# UnpivotCapability
# ---------------------------------------------------------------------------


class TestUnpivot:
    def test_nominal_wide_to_long(self, wide_pop):
        cap = cap_get("unpivot")
        out = cap.execute(
            wide_pop,
            id_vars=["parcel_id"],
            value_vars=["pop_2020", "pop_2021", "pop_2022"],
            var_name="year",
            value_name="population",
        )
        assert len(out) == 6  # 2 parcels × 3 years
        assert {"parcel_id", "year", "population"} <= set(out.columns)
        # Geometry replicated per melted row.
        a_rows = out[out["parcel_id"] == "A"]
        assert len(a_rows) == 3
        assert all(g.equals(Point(0, 0)) for g in a_rows.geometry)

    def test_value_vars_default_to_all_non_id(self, wide_pop):
        """When value_vars is None, all non-id columns are melted."""
        cap = cap_get("unpivot")
        out = cap.execute(
            wide_pop,
            id_vars=["parcel_id"],
            value_vars=None,
        )
        # 3 pop_* columns × 2 parcels = 6 rows
        assert len(out) == 6

    def test_round_trip_pivot_then_unpivot(self, long_pop):
        """Long → pivot → unpivot must return the same row count + values."""
        pivot = cap_get("pivot")
        unpivot = cap_get("unpivot")

        wide = pivot.execute(
            long_pop, index="parcel_id", columns="year", values="pop",
        )
        long = unpivot.execute(
            wide,
            id_vars=["parcel_id"],
            value_vars=["pop_y2020", "pop_y2021", "pop_y2022"],
            var_name="year",
            value_name="pop",
        )
        # Row count restored.
        assert len(long) == len(long_pop)
        # Value distribution restored (sum check sufficient for this fixture).
        assert long["pop"].sum() == long_pop["pop"].sum()

    def test_missing_id_vars_raises(self, wide_pop):
        cap = cap_get("unpivot")
        with pytest.raises(ValueError, match="requires 'id_vars'"):
            cap.execute(wide_pop, value_vars=["pop_2020"])

    def test_unknown_value_var_raises(self, wide_pop):
        cap = cap_get("unpivot")
        with pytest.raises(KeyError, match="not in layer"):
            cap.execute(
                wide_pop,
                id_vars=["parcel_id"],
                value_vars=["pop_9999"],
            )

    def test_geometry_and_crs_preserved(self, wide_pop):
        cap = cap_get("unpivot")
        out = cap.execute(
            wide_pop,
            id_vars=["parcel_id"],
            value_vars=["pop_2020", "pop_2021"],
        )
        assert out.crs.to_epsg() == 3857
        assert out.geometry.notna().all()

"""Unit tests for schema/attribute manipulation capabilities."""

from __future__ import annotations

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point, Polygon

from gispulse.capabilities.schema import (
    AddFieldCapability,
    AttributeJoinCapability,
    CastFieldCapability,
    DescribeCapability,
    DropFieldCapability,
    RenameFieldCapability,
    SelectColumnsCapability,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def parcels() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "id": [1, 2, 3],
            "name": ["a", "b", "c"],
            "pop": [10, 20, 30],
            "geometry": [
                Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
                Polygon([(1, 0), (2, 0), (2, 1), (1, 1)]),
                Polygon([(2, 0), (3, 0), (3, 1), (2, 1)]),
            ],
        },
        crs="EPSG:4326",
    )


@pytest.fixture
def insee_ref() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "INSEE_COM": [1, 2, 99],
            "nom_long": ["Alpha", "Beta", "Outsider"],
            "population": [100, 200, 999],
        },
    )


# ---------------------------------------------------------------------------
# AddFieldCapability
# ---------------------------------------------------------------------------


class TestAddField:
    def test_add_constant_string(self, parcels):
        out = AddFieldCapability().execute(
            parcels, fields=[{"name": "status", "dtype": "string", "default": "pending"}],
        )
        assert "status" in out.columns
        assert (out["status"] == "pending").all()

    def test_add_null_float(self, parcels):
        out = AddFieldCapability().execute(
            parcels, fields=[{"name": "score", "dtype": "float64"}],
        )
        assert "score" in out.columns
        assert out["score"].isna().all()

    def test_skip_existing(self, parcels):
        out = AddFieldCapability().execute(
            parcels, fields=[{"name": "pop", "default": 999}],
        )
        # Default behaviour: do not overwrite existing columns.
        assert (out["pop"] == parcels["pop"]).all()

    def test_overwrite(self, parcels):
        out = AddFieldCapability().execute(
            parcels,
            fields=[{"name": "pop", "dtype": "Int64", "default": 0}],
            overwrite=True,
        )
        assert (out["pop"] == 0).all()

    def test_protect_geometry(self, parcels):
        with pytest.raises(ValueError, match="geometry"):
            AddFieldCapability().execute(
                parcels, fields=[{"name": "geometry", "default": None}],
            )

    def test_invalid_name(self, parcels):
        with pytest.raises(ValueError, match="Invalid"):
            AddFieldCapability().execute(parcels, fields=[{"name": "1bad"}])


# ---------------------------------------------------------------------------
# DropFieldCapability
# ---------------------------------------------------------------------------


class TestDropField:
    def test_drop_one(self, parcels):
        out = DropFieldCapability().execute(parcels, fields=["pop"])
        assert "pop" not in out.columns
        assert "name" in out.columns

    def test_ignore_missing(self, parcels):
        out = DropFieldCapability().execute(parcels, fields=["nope"])
        assert list(out.columns) == list(parcels.columns)

    def test_raise_missing(self, parcels):
        with pytest.raises(KeyError):
            DropFieldCapability().execute(parcels, fields=["nope"], ignore_missing=False)

    def test_protect_geometry(self, parcels):
        with pytest.raises(ValueError, match="geometry"):
            DropFieldCapability().execute(parcels, fields=["geometry"])


# ---------------------------------------------------------------------------
# SelectColumnsCapability
# ---------------------------------------------------------------------------


class TestSelectColumns:
    def test_keeps_geometry(self, parcels):
        out = SelectColumnsCapability().execute(parcels, fields=["id"])
        assert set(out.columns) == {"id", "geometry"}
        assert isinstance(out, gpd.GeoDataFrame)

    def test_unknown_columns_dropped_silently(self, parcels):
        out = SelectColumnsCapability().execute(parcels, fields=["id", "ghost"])
        assert set(out.columns) == {"id", "geometry"}


# ---------------------------------------------------------------------------
# RenameFieldCapability
# ---------------------------------------------------------------------------


class TestRenameField:
    def test_simple(self, parcels):
        out = RenameFieldCapability().execute(parcels, mapping={"pop": "population"})
        assert "population" in out.columns
        assert "pop" not in out.columns

    def test_collision(self, parcels):
        with pytest.raises(ValueError, match="collides"):
            RenameFieldCapability().execute(parcels, mapping={"pop": "name"})

    def test_protect_geometry(self, parcels):
        with pytest.raises(ValueError, match="geometry"):
            RenameFieldCapability().execute(parcels, mapping={"geometry": "geom"})


# ---------------------------------------------------------------------------
# CastFieldCapability
# ---------------------------------------------------------------------------


class TestCastField:
    def test_to_string(self, parcels):
        out = CastFieldCapability().execute(parcels, casts={"pop": "string"})
        assert out["pop"].dtype == "string"

    def test_to_int_from_float(self, parcels):
        df = parcels.copy()
        df["pop"] = df["pop"].astype(float)
        out = CastFieldCapability().execute(df, casts={"pop": "int"})
        assert out["pop"].dtype == "Int64"

    def test_coerce_invalid(self, parcels):
        df = parcels.copy()
        df["pop"] = ["10", "bad", "30"]
        out = CastFieldCapability().execute(df, casts={"pop": "int"}, errors="coerce")
        assert pd.isna(out["pop"].iloc[1])
        assert out["pop"].iloc[0] == 10
        assert out["pop"].iloc[2] == 30

    def test_raise_invalid(self, parcels):
        df = parcels.copy()
        df["pop"] = ["10", "bad", "30"]
        with pytest.raises(ValueError):
            CastFieldCapability().execute(df, casts={"pop": "int"})


# ---------------------------------------------------------------------------
# AttributeJoinCapability
# ---------------------------------------------------------------------------


class TestAttributeJoin:
    def test_left_join(self, parcels, insee_ref):
        out = AttributeJoinCapability().execute(
            parcels,
            ref_gdf=insee_ref,
            left_on="id",
            right_on="INSEE_COM",
            columns=["nom_long", "population"],
        )
        assert isinstance(out, gpd.GeoDataFrame)
        assert "nom_long" in out.columns
        assert "population" in out.columns
        assert out.loc[out["id"] == 1, "nom_long"].iloc[0] == "Alpha"
        # parcel id=3 has no match → NaN
        assert pd.isna(out.loc[out["id"] == 3, "nom_long"].iloc[0])
        # right_on column should be dropped (different from left_on)
        assert "INSEE_COM" not in out.columns

    def test_inner_drops_unmatched(self, parcels, insee_ref):
        out = AttributeJoinCapability().execute(
            parcels, ref_gdf=insee_ref, left_on="id", right_on="INSEE_COM",
            how="inner",
        )
        assert len(out) == 2
        assert set(out["id"]) == {1, 2}

    def test_prefix_renaming(self, parcels, insee_ref):
        out = AttributeJoinCapability().execute(
            parcels, ref_gdf=insee_ref, left_on="id", right_on="INSEE_COM",
            columns=["nom_long"], prefix="ref_",
        )
        assert "ref_nom_long" in out.columns
        assert "nom_long" not in out.columns

    def test_missing_ref_raises(self, parcels):
        with pytest.raises(ValueError, match="reference layer"):
            AttributeJoinCapability().execute(parcels, left_on="id")

    def test_missing_key_raises(self, parcels, insee_ref):
        with pytest.raises(KeyError):
            AttributeJoinCapability().execute(
                parcels, ref_gdf=insee_ref, left_on="ghost", right_on="INSEE_COM",
            )

    def test_geodataframe_ref_strips_geom(self, parcels):
        ref_gdf = gpd.GeoDataFrame(
            {"id": [1, 2], "label": ["x", "y"], "geometry": [Point(0, 0), Point(1, 1)]},
            crs="EPSG:4326",
        )
        out = AttributeJoinCapability().execute(
            parcels, ref_gdf=ref_gdf, left_on="id", right_on="id",
        )
        assert "label" in out.columns
        # primary geometry preserved (parcels' polygons), not ref points
        assert out.geometry.iloc[0].geom_type == "Polygon"


class TestDescribeCapability:
    def test_passthrough_with_attrs_report(self, parcels: gpd.GeoDataFrame) -> None:
        out = DescribeCapability().execute(parcels)
        # Layer is unchanged (same shape, same data, same crs)
        assert out.shape == parcels.shape
        assert list(out.columns) == list(parcels.columns)
        assert out.crs == parcels.crs

        report = out.attrs["__schema_describe__"]
        assert report["n_rows"] == 3
        assert report["geometry_column"] == "geometry"

        cols = {c["name"]: c for c in report["columns"]}
        assert cols["id"]["dtype"].startswith("int")
        assert cols["id"]["n_nulls"] == 0
        assert cols["id"]["n_unique"] == 3
        assert "geometry" not in cols  # geometry handled separately

        geom = report["geometry"]
        assert geom["type_counts"] == {"Polygon": 3}
        assert geom["n_empty"] == 0
        assert geom["crs"] == "EPSG:4326"
        assert len(geom["bounds"]) == 4

    def test_sample_size_includes_jsonable_values(
        self, parcels: gpd.GeoDataFrame,
    ) -> None:
        out = DescribeCapability().execute(parcels, sample_size=2)
        cols = {c["name"]: c for c in out.attrs["__schema_describe__"]["columns"]}
        assert cols["name"]["sample"] == ["a", "b"]
        # numpy ints coerced to python int
        assert all(isinstance(v, int) for v in cols["pop"]["sample"])

    def test_kwargs_validation_rejects_negative_sample(
        self, parcels: gpd.GeoDataFrame,
    ) -> None:
        with pytest.raises(ValueError, match="sample_size"):
            DescribeCapability().execute(parcels, sample_size=-1)
        with pytest.raises(ValueError, match="sample_size"):
            DescribeCapability().execute(parcels, sample_size="abc")

    def test_handles_nulls_and_empty_geometry(self) -> None:
        from shapely.wkt import loads

        empty_poly = loads("POLYGON EMPTY")
        gdf = gpd.GeoDataFrame(
            {
                "id": [1, 2, None],
                "geometry": [
                    Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
                    empty_poly,
                    None,
                ],
            },
            crs="EPSG:4326",
        )
        report = DescribeCapability().execute(gdf).attrs["__schema_describe__"]
        id_entry = next(c for c in report["columns"] if c["name"] == "id")
        assert id_entry["n_nulls"] == 1
        assert report["geometry"]["n_empty"] == 2  # empty + None
        assert report["geometry"]["type_counts"] == {"Polygon": 1}

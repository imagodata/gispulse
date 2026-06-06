"""Unit tests for the datamart provider (DP2)."""

from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import Point

from gispulse.persistence.datamart import (
    DATAMARTS,
    Datamart,
    DatamartRegistry,
    parse_datamart_uri,
)
from gispulse.persistence.loader import load, resolve_source


@pytest.fixture
def points_gdf() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"name": ["a", "b"]},
        geometry=[Point(0, 0), Point(1, 1)],
        crs="EPSG:4326",
    )


@pytest.fixture(autouse=True)
def _clean_default_registry():
    DATAMARTS.clear()
    yield
    DATAMARTS.clear()


# ---------------------------------------------------------------------------
# Datamart.uri_for
# ---------------------------------------------------------------------------


def test_uri_for_default_pattern():
    m = Datamart(name="m", location="s3://bucket/marts/m")
    assert m.uri_for("parcels") == "s3://bucket/marts/m/parcels.parquet"


def test_uri_for_trailing_slash_normalised():
    m = Datamart(name="m", location="s3://bucket/marts/m/")
    assert m.uri_for("parcels") == "s3://bucket/marts/m/parcels.parquet"


def test_uri_for_custom_pattern():
    m = Datamart(name="m", location="/data/m", table_pattern="{table}/data.fgb")
    assert m.uri_for("roads") == "/data/m/roads/data.fgb"


def test_uri_for_empty_table_raises():
    m = Datamart(name="m", location="/data/m")
    with pytest.raises(ValueError):
        m.uri_for("")


# ---------------------------------------------------------------------------
# parse_datamart_uri
# ---------------------------------------------------------------------------


def test_parse_uri_ok():
    assert parse_datamart_uri("datamart://ref/parcels") == ("ref", "parcels")


def test_parse_uri_nested_table():
    assert parse_datamart_uri("datamart://ref/cadastre/parcels") == (
        "ref",
        "cadastre/parcels",
    )


@pytest.mark.parametrize(
    "uri",
    ["datamart://ref", "datamart://ref/", "datamart:///x", "wfs://h/x"],
)
def test_parse_uri_invalid(uri):
    with pytest.raises(ValueError):
        parse_datamart_uri(uri)


# ---------------------------------------------------------------------------
# DatamartRegistry
# ---------------------------------------------------------------------------


def test_register_get_names():
    reg = DatamartRegistry()
    reg.register(Datamart(name="a", location="/x"))
    reg.register(Datamart(name="b", location="/y"))
    assert reg.names() == ["a", "b"]
    assert reg.get("a").location == "/x"


def test_register_duplicate_raises():
    reg = DatamartRegistry()
    reg.register(Datamart(name="a", location="/x"))
    with pytest.raises(ValueError):
        reg.register(Datamart(name="a", location="/y"))
    reg.register(Datamart(name="a", location="/y"), override=True)
    assert reg.get("a").location == "/y"


def test_get_unknown_raises():
    reg = DatamartRegistry()
    with pytest.raises(KeyError):
        reg.get("nope")


def test_resolve_returns_uri():
    reg = DatamartRegistry()
    reg.register(Datamart(name="m", location="s3://b/m"))
    assert reg.resolve("m", "t") == "s3://b/m/t.parquet"


# ---------------------------------------------------------------------------
# Environment loading
# ---------------------------------------------------------------------------


def test_env_declarations_loaded(monkeypatch):
    monkeypatch.setenv(
        "GISPULSE_DATAMARTS",
        '{"ref": {"location": "s3://b/ref", "table_pattern": "{table}.fgb"}}',
    )
    reg = DatamartRegistry()
    assert reg.get("ref").uri_for("x") == "s3://b/ref/x.fgb"


def test_env_invalid_json_ignored(monkeypatch):
    monkeypatch.setenv("GISPULSE_DATAMARTS", "{not json")
    reg = DatamartRegistry()
    assert reg.names() == []


def test_explicit_registration_wins_over_env(monkeypatch):
    monkeypatch.setenv(
        "GISPULSE_DATAMARTS", '{"m": {"location": "s3://env/m"}}'
    )
    reg = DatamartRegistry()
    reg.register(Datamart(name="m", location="s3://explicit/m"))
    assert reg.get("m").location == "s3://explicit/m"


# ---------------------------------------------------------------------------
# Integration with resolve_source / load
# ---------------------------------------------------------------------------


def test_resolve_source_datamart_indirection(tmp_path):
    from pathlib import Path

    DATAMARTS.register(Datamart(name="m", location=str(tmp_path)))
    resolved = resolve_source("datamart://m/pts")
    assert resolved == Path(str(tmp_path / "pts.parquet"))


def test_load_via_datamart(tmp_path, points_gdf):
    (points_gdf).to_parquet(tmp_path / "pts.parquet")
    DATAMARTS.register(Datamart(name="m", location=str(tmp_path)))

    out = load("datamart://m/pts")
    assert isinstance(out, gpd.GeoDataFrame)
    assert len(out) == 2
    assert list(out["name"]) == ["a", "b"]

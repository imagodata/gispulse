from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import Point

from gispulse.core.fetchers.http_file import HttpFileFetcher
from gispulse.persistence import DuckDBSession
from gispulse.persistence.duckdb_relations import (
    materialize_remote_gpkg,
    parquet_scan,
    st_dwithin_join_sql,
)


def test_duckdb_relation_helpers_are_exported_from_persistence() -> None:
    from gispulse import persistence

    assert persistence.parquet_scan is parquet_scan
    assert persistence.materialize_remote_gpkg is materialize_remote_gpkg
    assert persistence.st_dwithin_join_sql is st_dwithin_join_sql


def test_parquet_scan_escapes_uri_and_controls_union_by_name() -> None:
    assert parquet_scan("s3://milou-data/path/companies.parquet") == (
        "read_parquet('s3://milou-data/path/companies.parquet', union_by_name=true)"
    )
    assert parquet_scan("/tmp/o'hara.parquet", union_by_name=False) == (
        "read_parquet('/tmp/o''hara.parquet', union_by_name=false)"
    )


def test_parquet_scan_expression_reads_synthetic_local_parquet(tmp_path: Path) -> None:
    path = tmp_path / "companies.parquet"
    gpd.GeoDataFrame(
        {"company": ["a", "b"], "geometry": [Point(0, 0), Point(1, 1)]},
        crs="EPSG:4326",
    ).to_parquet(path)

    with DuckDBSession() as session:
        rows = session.execute_sql(f"SELECT count(*) AS n FROM {parquet_scan(path)}")

    assert rows == [{"n": 2}]


def test_st_dwithin_join_sql_quotes_identifiers_and_accepts_parquet_relation() -> None:
    companies = parquet_scan("s3://milou-data/data/processed/companies_be.parquet")

    sql = st_dwithin_join_sql(
        "project.sites",
        companies,
        distance_m=200,
        left_geom="site geom",
        right_geom="geom",
        select="l.id, r.company_name",
    )

    assert sql == (
        "SELECT l.\"id\", r.\"company_name\" "
        "FROM \"project\".\"sites\" AS l "
        "JOIN read_parquet('s3://milou-data/data/processed/companies_be.parquet', "
        "union_by_name=true) AS r "
        "ON ST_DWithin(l.\"site geom\", r.\"geom\", 200.0)"
    )


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"left": "sites; DROP TABLE sites"}, "Unsafe relation"),
        ({"right": "companies WHERE 1=1"}, "Unsafe relation"),
        ({"left_geom": "geom) OR true --"}, "Unsafe identifier"),
        ({"select": "*, now()"}, "Unsafe select"),
        ({"distance_m": -1}, "distance_m"),
    ],
)
def test_st_dwithin_join_sql_rejects_unsafe_sql(kwargs: dict[str, object], match: str) -> None:
    params: dict[str, object] = {
        "left": "sites",
        "right": "companies",
        "distance_m": 10,
    }
    params.update(kwargs)

    with pytest.raises(ValueError, match=match):
        st_dwithin_join_sql(**params)  # type: ignore[arg-type]


def test_materialize_remote_gpkg_returns_existing_local_file(tmp_path: Path) -> None:
    path = tmp_path / "sites.gpkg"
    gpd.GeoDataFrame(
        {"name": ["site"], "geometry": [Point(0, 0)]},
        crs="EPSG:4326",
    ).to_file(path, layer="sites", driver="GPKG")

    result = materialize_remote_gpkg(path, layer="sites")

    assert result == path


def test_materialize_remote_gpkg_uses_cache_for_http_download(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.gpkg"
    gpd.GeoDataFrame(
        {"name": ["site"], "geometry": [Point(0, 0)]},
        crs="EPSG:4326",
    ).to_file(source, layer="sites", driver="GPKG")
    calls: list[tuple[str, str]] = []

    def fake_fetch(self: HttpFileFetcher, access, *, mode, extent=None):  # noqa: ANN001
        calls.append((access.endpoint, access.params["local_path"]))
        Path(access.params["local_path"]).write_bytes(source.read_bytes())
        return type("Result", (), {"data": access.params["local_path"]})()

    monkeypatch.setattr(HttpFileFetcher, "fetch", fake_fetch)

    uri = "https://data.example.test/sites.gpkg?sig=secret"
    first = materialize_remote_gpkg(uri, layer="sites", cache_dir=tmp_path / "cache")
    second = materialize_remote_gpkg(uri, layer="sites", cache_dir=tmp_path / "cache")

    assert first == second
    assert first.exists()
    assert calls == [(uri, str(first))]

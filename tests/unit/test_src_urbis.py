"""UrbIS source declarations and counted WFS transport invariants."""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "plugins" / "gispulse-src-urbis"))
from gispulse_src_urbis.source import UrbisSource

from gispulse.adapters.ogc.wfs_fetcher import WfsFetcher


def test_urbis_layers_and_raw_contract():
    source = UrbisSource()
    assert len(source.entries()) == 4
    access = source.access_for("urbis-street-surfaces-bxl")
    assert access.params["typename"] == "urbisvector:StreetSurfaces"
    assert access.params["crs"] == "EPSG:31370"
    assert access.params["sortBy"] == "INSPIRE_ID A"
    assert source.schema("urbis-street-surfaces-bxl")["LVL"] == "int"
    access.params["pagination"]["page_size"] = 1
    assert source.access_for("urbis-street-surfaces-bxl").params["pagination"]["page_size"] == 2000


def test_urbis_refuses_legacy_core(monkeypatch):
    monkeypatch.setitem(sys.modules, "gispulse.adapters.ogc.counted_wfs", None)
    with pytest.raises(RuntimeError, match="URBIS_PAGINATION_REQUIRED"):
        UrbisSource().entries()


@pytest.mark.parametrize("extent", [None, (1, 2, 0, 3), (float("nan"), 0, 1, 2)])
def test_counted_wfs_requires_valid_extent_before_http(monkeypatch, extent):
    def unexpected(*args):
        pytest.fail("network before extent validation")

    monkeypatch.setattr("gispulse.adapters.ogc.counted_wfs._get_geojson_with_retry", unexpected)
    with pytest.raises(ValueError, match="WFS_EXTENT"):
        WfsFetcher().fetch(UrbisSource().access_for("urbis-street-surfaces-bxl"), extent=extent)


def test_counted_wfs_pages_and_crs(monkeypatch):
    calls = []

    def request(url, timeout, retry):
        q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        calls.append(q)
        assert q["bbox"] == "148000.0,170000.0,149000.0,171000.0,EPSG:31370"
        assert "pagination" not in q and "require_extent" not in q
        if q["count"] == "1":
            return {"numberMatched": 3}
        ids = [1, 2] if q["startIndex"] == "0" else [3]
        return {
            "type": "FeatureCollection",
            "numberMatched": 3,
            "crs": {"properties": {"name": "EPSG:31370"}},
            "features": [
                {
                    "type": "Feature",
                    "properties": {"INSPIRE_ID": str(i), "TYPE": "SW", "LVL": 0},
                    "geometry": {"type": "Point", "coordinates": [148000 + i, 170000]},
                }
                for i in ids
            ],
        }

    monkeypatch.setattr("gispulse.adapters.ogc.counted_wfs._get_geojson_with_retry", request)
    access = UrbisSource().access_for("urbis-street-surfaces-bxl")
    access.params["pagination"]["page_size"] = 2
    result = WfsFetcher().fetch(access, extent=(148000, 170000, 149000, 171000))
    assert list(result.data.INSPIRE_ID) == ["1", "2", "3"]
    assert result.data.crs.to_epsg() == 31370
    assert result.metadata["complete"] is True
    assert len(calls) == 4


def test_counted_wfs_rejects_wrong_advertised_crs(monkeypatch):
    monkeypatch.setattr(
        "gispulse.adapters.ogc.counted_wfs._get_geojson_with_retry",
        lambda *a: {"numberMatched": 0, "crs": {"properties": {"name": "EPSG:4326"}}},
    )
    with pytest.raises(ValueError, match="WFS_CRS_MISMATCH"):
        WfsFetcher().fetch(
            UrbisSource().access_for("urbis-street-surfaces-bxl"),
            extent=(148000, 170000, 149000, 171000),
        )


def test_counted_wfs_does_not_label_unreferenced_geojson_as_projected(monkeypatch):
    payload = {
        "type": "FeatureCollection",
        "numberMatched": 1,
        "features": [
            {
                "type": "Feature",
                "properties": {"INSPIRE_ID": "1"},
                "geometry": {"type": "Point", "coordinates": [4.35, 50.84]},
            }
        ],
    }
    monkeypatch.setattr(
        "gispulse.adapters.ogc.counted_wfs._get_geojson_with_retry", lambda *a: payload
    )
    with pytest.raises(ValueError, match="WFS_CRS_MISSING"):
        WfsFetcher().fetch(
            UrbisSource().access_for("urbis-street-surfaces-bxl"),
            extent=(148000, 170000, 149000, 171000),
        )


def _urbis_stub(monkeypatch, *, surface_ids=("S1",)):
    from types import SimpleNamespace

    import geopandas as gpd
    from shapely.geometry import LineString, box

    layers = {
        "urbisvector:StreetSurfaces": gpd.GeoDataFrame(
            {
                "INSPIRE_ID": list(surface_ids),
                "TYPE": ["S"] * len(surface_ids),
                "LVL": [0] * len(surface_ids),
            },
            geometry=[box(0, 0, 10, 10)] * len(surface_ids),
            crs=31370,
        ),
        "urbisvector:StreetAxes": gpd.GeoDataFrame(
            {"INSPIRE_ID": ["A1"], "TYPE": ["S"], "LVL": [0], "HIERARCHY": ["DR"]},
            geometry=[LineString([(0, 5), (10, 5)])],
            crs=31370,
        ),
    }
    empty = gpd.GeoDataFrame(geometry=gpd.GeoSeries([], crs=31370), crs=31370)

    def fetch(self, access, extent):
        data = layers.get(access.params["typename"], empty)
        return SimpleNamespace(data=data.copy(), metadata={"complete": True})

    monkeypatch.setattr(WfsFetcher, "fetch", fetch)


def test_prepare_dry_run_has_no_network_or_files(monkeypatch, tmp_path):
    from gispulse_src_urbis.prepare import prepare_urbis

    monkeypatch.setattr(WfsFetcher, "fetch", lambda *a, **kw: pytest.fail("network in dry-run"))
    report = prepare_urbis(bbox=(0, 0, 1, 1), output=tmp_path / "u")
    assert report["status"] == "planned"
    assert list(tmp_path.iterdir()) == []


def test_prepare_publishes_raw_layers_and_road_crossings(monkeypatch, tmp_path):
    import geopandas as gpd
    from gispulse_src_urbis.prepare import prepare_urbis

    _urbis_stub(monkeypatch)
    output = tmp_path / "u"
    report = prepare_urbis(bbox=(-5, -5, 15, 15), output=output, write=True)
    assert report["status"] == "complete"
    assert report["road_crossings"]["status"] == "prepared"
    assert report["road_crossings"]["structure_counts"] == {"ground": 1}
    crossings = gpd.read_parquet(output / "road_crossings.geoparquet")
    assert list(crossings.road_id) == ["UrbIS:S1"]
    assert crossings["axis"].crs == crossings.crs
    assert "urbis-bridges-bxl.geoparquet" in report["sha256"]


def test_prepare_degrades_only_the_crossings_on_a_data_defect(monkeypatch, tmp_path):
    from gispulse_src_urbis.prepare import prepare_urbis

    _urbis_stub(monkeypatch, surface_ids=("S1", "S1"))
    output = tmp_path / "u"
    report = prepare_urbis(bbox=(-5, -5, 15, 15), output=output, write=True)
    assert report["road_crossings"]["status"] == "failed"
    assert "URBIS_CROSSINGS_ID_INVALID" in report["road_crossings"]["error"]
    assert (output / "urbis-street-surfaces-bxl.geoparquet").exists()
    assert not (output / "road_crossings.geoparquet").exists()


@pytest.mark.parametrize(
    "kwargs, code",
    [({"bbox": (1, 0, 0, 1)}, "EXTENT"), ({"crossing_exclusion_buffer_m": 0}, "TOLERANCE")],
)
def test_prepare_validates_before_network(tmp_path, kwargs, code):
    from gispulse_src_urbis.prepare import prepare_urbis

    with pytest.raises(ValueError, match=code):
        prepare_urbis(**{"bbox": (0, 0, 1, 1), "output": tmp_path / "u", **kwargs})

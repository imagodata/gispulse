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

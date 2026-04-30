"""Tests for generic API Carto GeoJSON loading."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from gispulse.adapters.apicarto import ApiCartoGeoJsonClient


def test_apicarto_client_fetches_catalog_entry_with_geom_query() -> None:
    requested: list[str] = []

    def fake_get_json(url: str, timeout: float) -> dict:
        requested.append(url)
        assert timeout == 12.0
        return {
            "type": "FeatureCollection",
            "features": [],
            "totalFeatures": 0,
            "numberMatched": 0,
            "numberReturned": 0,
        }

    client = ApiCartoGeoJsonClient(get_json=fake_get_json, timeout=12.0)

    payload = client.fetch_geojson_for_geometry(
        "opendata:ign:apicarto-nature-znieff1",
        {"type": "Point", "coordinates": [2.424573, 48.845726]},
        limit=25,
    )

    assert payload["type"] == "FeatureCollection"
    parsed = urlparse(requested[0])
    params = parse_qs(parsed.query)
    assert parsed.scheme == "https"
    assert parsed.netloc == "apicarto.ign.fr"
    assert parsed.path == "/api/nature/znieff1"
    assert params["_limit"] == ["25"]
    assert params["geom"] == ['{"type":"Point","coordinates":[2.424573,48.845726]}']

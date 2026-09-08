"""PICC source contract: real service IDs, bounded requests, raw source semantics."""

from __future__ import annotations
import sys
from pathlib import Path
import pytest

_PKG = Path(__file__).resolve().parents[2] / "plugins" / "gispulse-src-picc"
sys.path.insert(0, str(_PKG))
from gispulse_src_picc.source import PiccSource
from gispulse.core.plugin_model import AccessProtocol
from gispulse.adapters.rest.rest_fetcher import RestGeoJsonFetcher


def test_plugin_manifest_and_entries():
    import tomllib

    manifest = tomllib.loads((_PKG / "pyproject.toml").read_text())
    assert manifest["project"]["entry-points"]["gispulse.data_sources"] == {
        "picc": "gispulse_src_picc:register"
    }
    assert {e.id for e in PiccSource().entries()} == {"picc-road-surfaces-wa", "picc-road-axes-wa"}


@pytest.mark.parametrize("entry,layer", [("picc-road-surfaces-wa", 24), ("picc-road-axes-wa", 21)])
def test_source_requests_geojson_wgs84_with_counted_pagination(entry, layer):
    source = PiccSource()
    access = source.access_for(entry)
    assert access.protocol is AccessProtocol.REST_API
    assert access.endpoint.endswith(f"/MapServer/{layer}/query")
    assert access.params["outSR"] == "4326"
    assert access.params["orderByFields"] == "OBJECTID ASC"
    assert access.params["pagination"]["count_query"]["returnCountOnly"] == "true"
    assert "NIVEAU" in source.schema(entry)
    assert "gc_surface" not in source.schema(entry)
    with pytest.raises(ValueError, match="REST_EXTENT_REQUIRED"):
        RestGeoJsonFetcher().fetch(access)


def test_nested_overrides_do_not_mutate_source_defaults():
    source = PiccSource()
    source.access_for("picc-road-surfaces-wa").params["pagination"]["page_size"] = 1
    assert source.access_for("picc-road-surfaces-wa").params["pagination"]["page_size"] == 2000


def test_legacy_core_is_refused_before_network(monkeypatch):
    monkeypatch.setitem(sys.modules, "gispulse.adapters.rest.offset_pages", None)
    with pytest.raises(RuntimeError, match="PICC_PAGINATION_REQUIRED"):
        PiccSource().access_for("picc-road-surfaces-wa")


def test_export_dry_run_has_no_network_or_files(monkeypatch, tmp_path):
    from gispulse_src_picc.export import export_picc

    monkeypatch.setattr(
        RestGeoJsonFetcher, "fetch", lambda *a, **kw: pytest.fail("network in dry-run")
    )
    output = tmp_path / "new" / "picc"
    report = export_picc(bbox=(4, 50, 5, 51), output=output)
    assert report["status"] == "planned"
    assert not output.parent.exists()


def test_export_failure_on_second_layer_publishes_nothing(monkeypatch, tmp_path):
    from types import SimpleNamespace
    import geopandas as gpd
    from shapely.geometry import box
    from gispulse_src_picc.export import export_picc

    calls = []

    def fetch(self, access, **kwargs):
        calls.append(access)
        if len(calls) == 2:
            raise ValueError("REST_INCOMPLETE")
        return SimpleNamespace(
            data=gpd.GeoDataFrame(
                {"OBJECTID": [1], "NATUR_DESC": ["Tronçon"], "NIVEAU": [0]},
                geometry=[box(4, 50, 4.1, 50.1)],
                crs=4326,
            ),
            metadata={"complete": True},
        )

    monkeypatch.setattr(RestGeoJsonFetcher, "fetch", fetch)
    output = tmp_path / "picc"
    with pytest.raises(ValueError, match="REST_INCOMPLETE"):
        export_picc(bbox=(4, 50, 5, 51), output=output, write=True)
    assert not output.exists()
    assert list(tmp_path.iterdir()) == []


def test_export_validates_bounds_before_network(tmp_path):
    from gispulse_src_picc.export import export_picc

    for kwargs, code in [
        ({"page_size": 2001}, "PAGE_SIZE"),
        ({"max_features": 0}, "PAGINATION"),
        ({"bbox": (5, 50, 4, 51)}, "EXTENT"),
    ]:
        with pytest.raises(ValueError, match=code):
            export_picc(**{"bbox": (4, 50, 5, 51), "output": tmp_path / "picc", **kwargs})

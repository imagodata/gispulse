"""Tests for the GIS catalog system."""

from catalog import registry
from catalog.models import CatalogDomain, ProjectionEntry, BasemapEntry, FluxEntry


class TestCatalogRegistry:
    def test_providers_registered(self):
        providers = registry.list_providers()
        names = {p["name"] for p in providers}
        assert "epsg" in names
        assert "basemaps" in names
        assert "ign" in names
        assert "osm" in names

    def test_search_projections(self):
        results = registry.search(domain=CatalogDomain.PROJECTION)
        assert len(results) > 20
        assert all(isinstance(e, ProjectionEntry) for e in results)

    def test_search_projection_by_code(self):
        results = registry.search(domain=CatalogDomain.PROJECTION, search="2154")
        assert any(e.epsg_code == 2154 for e in results)

    def test_search_projection_by_area(self):
        results = registry.search(domain=CatalogDomain.PROJECTION, search="france")
        assert len(results) >= 5

    def test_search_basemaps(self):
        results = registry.search(domain=CatalogDomain.BASEMAP)
        assert len(results) >= 10
        assert all(isinstance(e, BasemapEntry) for e in results)

    def test_basemap_has_url(self):
        results = registry.search(domain=CatalogDomain.BASEMAP, search="osm")
        osm = next((e for e in results if "osm" in e.id and "fr" not in e.id), None)
        assert osm is not None
        assert osm.url_template
        assert "{z}" in osm.url_template

    def test_search_flux(self):
        results = registry.search(domain=CatalogDomain.FLUX)
        assert len(results) >= 10
        assert all(isinstance(e, FluxEntry) for e in results)

    def test_search_flux_by_provider(self):
        ign = registry.search(domain=CatalogDomain.FLUX, provider="ign")
        osm = registry.search(domain=CatalogDomain.FLUX, provider="osm")
        assert len(ign) > 0
        assert len(osm) > 0
        assert all(e.provider == "ign" for e in ign)
        assert all(e.provider == "osm" for e in osm)

    def test_search_opendata_ign(self):
        results = registry.search(domain=CatalogDomain.OPENDATA, provider="ign")
        assert len(results) >= 5
        assert any("BD TOPO" in e.name for e in results)

    def test_get_entry(self):
        entry = registry.get_entry("projection:epsg:4326")
        assert entry is not None
        assert isinstance(entry, ProjectionEntry)
        assert entry.epsg_code == 4326

    def test_get_entry_not_found(self):
        entry = registry.get_entry("projection:epsg:999999")
        assert entry is None

    def test_cross_domain_search(self):
        results = registry.search(search="france")
        domains = {e.domain for e in results}
        assert len(domains) >= 2  # Should span multiple domains

    def test_pagination(self):
        all_proj = registry.search(domain=CatalogDomain.PROJECTION, limit=100)
        page1 = registry.search(domain=CatalogDomain.PROJECTION, limit=5, offset=0)
        page2 = registry.search(domain=CatalogDomain.PROJECTION, limit=5, offset=5)
        assert len(page1) == 5
        assert len(page2) == 5
        assert page1[0].id != page2[0].id

    def test_flux_protocols(self):
        results = registry.search(domain=CatalogDomain.FLUX)
        protocols = {e.protocol for e in results}
        assert "wms" in protocols or "wmts" in protocols
        assert "wfs" in protocols or "ogc-features" in protocols

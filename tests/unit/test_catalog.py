"""Tests for the GIS catalog system."""

from catalog import registry
from catalog.models import CatalogDomain, ProjectionEntry, BasemapEntry, FluxEntry


GPU_FLUX_ENTRIES = {
    "flux:ign:ign-gpu-zone-urba-wfs": {
        "layer_name": "wfs_du:zone_urba",
        "tags": {"gpu", "plu", "urbanisme", "zonage", "france", "vector"},
    },
    "flux:ign:ign-gpu-prescription-surf-wfs": {
        "layer_name": "wfs_du:prescription_surf",
        "tags": {"gpu", "plu", "urbanisme", "prescription", "france", "vector"},
    },
    "flux:ign:ign-gpu-doc-urba-wfs": {
        "layer_name": "wfs_du:doc_urba",
        "tags": {"gpu", "plu", "urbanisme", "document", "france", "vector"},
    },
    "flux:ign:ign-gpu-prescription-lin-wfs": {
        "layer_name": "wfs_du:prescription_lin",
        "tags": {"gpu", "plu", "urbanisme", "prescription", "lineaire", "france", "vector"},
    },
    "flux:ign:ign-gpu-prescription-pct-wfs": {
        "layer_name": "wfs_du:prescription_pct",
        "tags": {"gpu", "plu", "urbanisme", "prescription", "ponctuel", "france", "vector"},
    },
    "flux:ign:ign-gpu-info-surf-wfs": {
        "layer_name": "wfs_du:info_surf",
        "tags": {"gpu", "plu", "urbanisme", "information", "surfacique", "france", "vector"},
    },
    "flux:ign:ign-gpu-info-lin-wfs": {
        "layer_name": "wfs_du:info_lin",
        "tags": {"gpu", "plu", "urbanisme", "information", "lineaire", "france", "vector"},
    },
    "flux:ign:ign-gpu-info-pct-wfs": {
        "layer_name": "wfs_du:info_pct",
        "tags": {"gpu", "plu", "urbanisme", "information", "ponctuel", "france", "vector"},
    },
    "flux:ign:ign-gpu-secteur-cc-wfs": {
        "layer_name": "wfs_du:secteur_cc",
        "tags": {"gpu", "carte-communale", "urbanisme", "secteur", "france", "vector"},
    },
}
GPU_FLUX_IDS = set(GPU_FLUX_ENTRIES)
SUP_FLUX_ENTRIES = {
    "flux:ign:ign-sup-servitude-wfs": "wfs_sup:servitude",
    "flux:ign:ign-sup-assiette-s-wfs": "wfs_sup:assiette_sup_s",
    "flux:ign:ign-sup-assiette-l-wfs": "wfs_sup:assiette_sup_l",
    "flux:ign:ign-sup-assiette-p-wfs": "wfs_sup:assiette_sup_p",
    "flux:ign:ign-sup-generateur-s-wfs": "wfs_sup:generateur_sup_s",
    "flux:ign:ign-sup-generateur-l-wfs": "wfs_sup:generateur_sup_l",
    "flux:ign:ign-sup-generateur-p-wfs": "wfs_sup:generateur_sup_p",
    "flux:ign:ign-sup-acte-wfs": "wfs_sup:acte_sup",
}
GPU_WFS_SERVICE_URL = "https://data.geopf.fr/wfs/ows?SERVICE=WFS&VERSION=2.0.0"
APICARTO_NATURE_ENTRIES = {
    "opendata:ign:apicarto-nature-natura-habitat": "/api/nature/natura-habitat",
    "opendata:ign:apicarto-nature-natura-oiseaux": "/api/nature/natura-oiseaux",
    "opendata:ign:apicarto-nature-znieff1": "/api/nature/znieff1",
    "opendata:ign:apicarto-nature-znieff2": "/api/nature/znieff2",
}


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

    def test_gpu_flux_entries_resolve(self):
        for entry_id, expected in GPU_FLUX_ENTRIES.items():
            entry = registry.get_entry(entry_id)
            assert entry is not None, entry_id
            assert isinstance(entry, FluxEntry)
            assert entry.protocol == "wfs"
            assert entry.service_url == GPU_WFS_SERVICE_URL
            assert entry.layer_name == expected["layer_name"]
            assert set(entry.tags) == expected["tags"]

    def test_sup_flux_entries_resolve(self):
        for entry_id, layer_name in SUP_FLUX_ENTRIES.items():
            entry = registry.get_entry(entry_id)
            assert entry is not None, entry_id
            assert isinstance(entry, FluxEntry)
            assert entry.protocol == "wfs"
            assert entry.service_url == GPU_WFS_SERVICE_URL
            assert entry.layer_name == layer_name
            assert "sup" in entry.tags
            assert "servitude" in entry.tags
            # SUP are part of the urbanism regulatory layer family;
            # callers filtering by `urbanisme` must pick them up.
            assert "urbanisme" in entry.tags

    def test_apicarto_nature_entries_resolve_from_catalog(self):
        for entry_id, endpoint_path in APICARTO_NATURE_ENTRIES.items():
            entry = registry.get_entry(entry_id)

            assert entry is not None, entry_id
            assert entry.provider == "ign"
            assert entry.source_url == f"https://apicarto.ign.fr{endpoint_path}"
            assert entry.format == "geojson"
            assert entry.metadata["endpoint_path"] == endpoint_path
            assert entry.metadata["query_param"] == "geom"

    def test_search_flux_gpu_returns_gpu_entries(self):
        results = registry.search(domain=CatalogDomain.FLUX, search="gpu")
        ids = {entry.id for entry in results}
        assert GPU_FLUX_IDS <= ids

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

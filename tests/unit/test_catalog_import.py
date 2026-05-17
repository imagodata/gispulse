"""Tests for catalog import endpoint and enriched providers."""

import pytest

from gispulse.catalog import registry
from gispulse.catalog.models import CatalogDomain, FluxEntry, FluxProtocol, OpenDataEntry


class TestEnrichedProviders:
    """Test that IGN opendata entries have WFS metadata for bbox import."""

    def test_ign_bdtopo_has_wfs_flux_id(self):
        entry = registry.get_entry("opendata:ign:bdtopo")
        assert entry is not None
        assert isinstance(entry, OpenDataEntry)
        assert entry.metadata.get("wfs_flux_id") == "flux:ign:ign-bdtopo-wfs"

    def test_ign_admin_has_wfs_flux_id(self):
        entry = registry.get_entry("opendata:ign:admin-express")
        assert entry is not None
        assert entry.metadata.get("wfs_flux_id") == "flux:ign:ign-admin-wfs"

    def test_ign_parcellaire_has_wfs_layer(self):
        entry = registry.get_entry("opendata:ign:bd-parcellaire")
        assert entry is not None
        assert entry.metadata.get("wfs_layer") is not None
        assert "CADASTRALPARCELS" in entry.metadata["wfs_layer"]
        assert entry.metadata.get("wfs_url") is not None

    def test_ign_iris_has_wfs_layer(self):
        entry = registry.get_entry("opendata:ign:contours-iris")
        assert entry is not None
        assert entry.metadata.get("wfs_layer") is not None

    def test_wfs_flux_entry_exists_for_linked(self):
        """Entries with wfs_flux_id must reference a real flux entry."""
        opendata = registry.search(domain=CatalogDomain.OPENDATA, provider="ign")
        for entry in opendata:
            flux_id = entry.metadata.get("wfs_flux_id")
            if flux_id:
                flux = registry.get_entry(flux_id)
                assert flux is not None, f"Missing flux entry {flux_id} for {entry.id}"
                assert isinstance(flux, FluxEntry)


class TestHubEauProvider:
    """Test the new Hub'Eau provider registration."""

    def test_hubeau_registered(self):
        providers = registry.list_providers()
        names = {p["name"] for p in providers}
        assert "hubeau" in names

    def test_hubeau_has_entries(self):
        results = registry.search(domain=CatalogDomain.FLUX, provider="hubeau")
        assert len(results) >= 3

    def test_hubeau_stations_hydro(self):
        results = registry.search(domain=CatalogDomain.FLUX, search="hydro")
        hubeau = [e for e in results if e.provider == "hubeau"]
        assert len(hubeau) >= 1
        assert any("hydro" in e.name.lower() for e in hubeau)


class TestCatalogImportRequest:
    """Test the Pydantic schema for catalog import."""

    def test_valid_request(self):
        from gispulse.adapters.http.schemas import CatalogImportRequest

        req = CatalogImportRequest(
            entry_id="flux:ign:ign-bdtopo-wfs",
            bbox=[2.2, 48.8, 2.5, 48.9],
            crs="EPSG:2154",
            max_features=1000,
            name="Test import",
        )
        assert req.entry_id == "flux:ign:ign-bdtopo-wfs"
        assert req.bbox == [2.2, 48.8, 2.5, 48.9]
        assert req.crs == "EPSG:2154"
        assert req.max_features == 1000
        assert req.name == "Test import"

    def test_defaults(self):
        from gispulse.adapters.http.schemas import CatalogImportRequest

        req = CatalogImportRequest(entry_id="flux:ign:ign-bdtopo-wfs")
        assert req.bbox is None
        assert req.crs == "EPSG:4326"
        assert req.max_features is None
        assert req.name is None

    def test_bbox_must_have_4_elements(self):
        from gispulse.adapters.http.schemas import CatalogImportRequest

        with pytest.raises(Exception):
            CatalogImportRequest(entry_id="test", bbox=[1.0, 2.0])

    def test_max_features_bounds(self):
        from gispulse.adapters.http.schemas import CatalogImportRequest

        with pytest.raises(Exception):
            CatalogImportRequest(entry_id="test", max_features=0)
        with pytest.raises(Exception):
            CatalogImportRequest(entry_id="test", max_features=200000)


class TestCatalogImportEndpoint:
    """Test the import endpoint logic (unit, no HTTP)."""

    def test_entry_not_found_raises(self):
        entry = registry.get_entry("flux:fake:does-not-exist")
        assert entry is None

    def test_wfs_flux_entry_importable(self):
        entry = registry.get_entry("flux:ign:ign-bdtopo-wfs")
        assert entry is not None
        assert isinstance(entry, FluxEntry)
        assert entry.protocol in (FluxProtocol.WFS, FluxProtocol.OGC_FEATURES)

    def test_raster_flux_not_downloadable(self):
        entry = registry.get_entry("flux:ign:ign-ortho-wmts")
        assert entry is not None
        assert isinstance(entry, FluxEntry)
        assert entry.protocol not in (FluxProtocol.WFS, FluxProtocol.OGC_FEATURES)

    def test_opendata_ign_resolves_to_wfs(self):
        """BD TOPO opendata entry should resolve to its WFS flux entry."""
        entry = registry.get_entry("opendata:ign:bdtopo")
        assert isinstance(entry, OpenDataEntry)
        assert entry.download_url is None
        flux_id = entry.metadata.get("wfs_flux_id")
        assert flux_id is not None
        flux = registry.get_entry(flux_id)
        assert isinstance(flux, FluxEntry)
        assert flux.protocol == FluxProtocol.WFS

"""IGN Géoplateforme open data provider — static catalog of key datasets.

Datasets reference both a source page and WFS flux entry IDs so the
catalog import can fetch features via WFS with bbox clipping when no
direct download URL is available.
"""

from __future__ import annotations

from catalog.models import CatalogDomain, OpenDataEntry
from catalog.providers.base import CatalogProvider
from catalog.registry import register_provider

# Géoplateforme WFS base for direct bbox queries
_WFS_BASE = "https://data.geopf.fr/wfs"
_APICARTO_BASE = "https://apicarto.ign.fr"

_IGN_DATASETS: list[dict] = [
    {
        "id": "bdtopo",
        "name": "BD TOPO",
        "description": "Base de données topographique 3D — bâtiments, routes, hydrographie, végétation, réseaux",
        "source_url": "https://geoservices.ign.fr/bdtopo",
        "download_url": None,
        "format": "gpkg",
        "license": "Licence Ouverte 2.0",
        "tags": ["bdtopo", "bâtiments", "routes", "hydro", "3d"],
        "update_frequency": "trimestriel",
        "wfs_flux_id": "flux:ign:ign-bdtopo-wfs",
    },
    {
        "id": "admin-express",
        "name": "ADMIN EXPRESS",
        "description": "Limites administratives — communes, EPCI, départements, régions",
        "source_url": "https://geoservices.ign.fr/adminexpress",
        "download_url": None,
        "format": "shp",
        "license": "Licence Ouverte 2.0",
        "tags": ["admin", "communes", "départements", "régions", "epci"],
        "update_frequency": "annuel",
        "wfs_flux_id": "flux:ign:ign-admin-wfs",
    },
    {
        "id": "bd-parcellaire",
        "name": "Parcellaire Express (PCI)",
        "description": "Parcelles cadastrales vectorielles — France entière",
        "source_url": "https://geoservices.ign.fr/parcellaire-express",
        "download_url": None,
        "format": "gpkg",
        "license": "Licence Ouverte 2.0",
        "tags": ["cadastre", "parcelles", "foncier"],
        "update_frequency": "semestriel",
        "wfs_layer": "CADASTRALPARCELS.PARCELLAIRE_EXPRESS:parcelle",
    },
    {
        "id": "contours-iris",
        "name": "Contours IRIS",
        "description": "Découpage infra-communal INSEE — IRIS (Ilots Regroupés pour l'Information Statistique)",
        "source_url": "https://geoservices.ign.fr/contoursiris",
        "download_url": None,
        "format": "gpkg",
        "license": "Licence Ouverte 2.0",
        "tags": ["iris", "insee", "statistiques", "infracommunal"],
        "update_frequency": "annuel",
        "wfs_layer": "STATISTICALUNITS.IRIS:contour_iris",
    },
    {
        "id": "rge-alti",
        "name": "RGE ALTI (MNT)",
        "description": "Modèle Numérique de Terrain — résolution 1m à 5m, France entière",
        "source_url": "https://geoservices.ign.fr/rgealti",
        "download_url": None,
        "format": "asc",
        "license": "Licence Ouverte 2.0",
        "tags": ["mnt", "altitude", "elevation", "raster", "lidar"],
        "update_frequency": "annuel",
    },
    {
        "id": "bd-foret",
        "name": "BD Forêt v2",
        "description": "Couverture forestière — essences, peuplements, types de formation",
        "source_url": "https://geoservices.ign.fr/bdforet",
        "download_url": None,
        "format": "gpkg",
        "license": "Licence Ouverte 2.0",
        "tags": ["forêt", "végétation", "environnement"],
        "update_frequency": "pluriannuel",
        "wfs_layer": "LANDCOVER.FORESTINVENTORY.V2:formation_vegetale",
    },
    {
        "id": "route500",
        "name": "ROUTE 500",
        "description": "Réseau routier national et européen simplifié",
        "source_url": "https://geoservices.ign.fr/route500",
        "download_url": None,
        "format": "gpkg",
        "license": "Licence Ouverte 2.0",
        "tags": ["routes", "transport", "réseau"],
        "update_frequency": "annuel",
    },
    {
        "id": "rpg",
        "name": "RPG (Registre Parcellaire Graphique)",
        "description": "Îlots et parcelles agricoles déclarés à la PAC",
        "source_url": "https://geoservices.ign.fr/rpg",
        "download_url": None,
        "format": "gpkg",
        "license": "Licence Ouverte 2.0",
        "tags": ["agriculture", "rpg", "pac", "parcelles"],
        "update_frequency": "annuel",
        "wfs_flux_id": "flux:ign:ign-rpg-wfs",
    },
    {
        "id": "ocsge",
        "name": "OCS GE (Occupation du sol)",
        "description": "Occupation du sol à grande échelle — artificialisation, couvert, usage",
        "source_url": "https://geoservices.ign.fr/ocsge",
        "download_url": None,
        "format": "gpkg",
        "license": "Licence Ouverte 2.0",
        "tags": ["occupation-du-sol", "artificialisation", "environnement"],
        "update_frequency": "pluriannuel",
    },
    {
        "id": "apicarto-nature-natura-habitat",
        "name": "API Carto Nature — Natura 2000 habitats",
        "description": "Zonages Natura 2000 directive habitats retournés en GeoJSON par géométrie.",
        "source_url": f"{_APICARTO_BASE}/api/nature/natura-habitat",
        "download_url": None,
        "format": "geojson",
        "license": "Licence Ouverte 2.0",
        "tags": ["apicarto", "nature", "natura2000", "habitats", "environnement"],
        "update_frequency": "service live",
        "metadata": {
            "endpoint_path": "/api/nature/natura-habitat",
            "query_param": "geom",
            "rest_base_url": _APICARTO_BASE,
        },
    },
    {
        "id": "apicarto-nature-natura-oiseaux",
        "name": "API Carto Nature — Natura 2000 oiseaux",
        "description": "Zonages Natura 2000 directive oiseaux retournés en GeoJSON par géométrie.",
        "source_url": f"{_APICARTO_BASE}/api/nature/natura-oiseaux",
        "download_url": None,
        "format": "geojson",
        "license": "Licence Ouverte 2.0",
        "tags": ["apicarto", "nature", "natura2000", "oiseaux", "environnement"],
        "update_frequency": "service live",
        "metadata": {
            "endpoint_path": "/api/nature/natura-oiseaux",
            "query_param": "geom",
            "rest_base_url": _APICARTO_BASE,
        },
    },
    {
        "id": "apicarto-nature-znieff1",
        "name": "API Carto Nature — ZNIEFF type 1",
        "description": "Zonages ZNIEFF type 1 retournés en GeoJSON par géométrie.",
        "source_url": f"{_APICARTO_BASE}/api/nature/znieff1",
        "download_url": None,
        "format": "geojson",
        "license": "Licence Ouverte 2.0",
        "tags": ["apicarto", "nature", "znieff", "znieff1", "environnement"],
        "update_frequency": "service live",
        "metadata": {
            "endpoint_path": "/api/nature/znieff1",
            "query_param": "geom",
            "rest_base_url": _APICARTO_BASE,
        },
    },
    {
        "id": "apicarto-nature-znieff2",
        "name": "API Carto Nature — ZNIEFF type 2",
        "description": "Zonages ZNIEFF type 2 retournés en GeoJSON par géométrie.",
        "source_url": f"{_APICARTO_BASE}/api/nature/znieff2",
        "download_url": None,
        "format": "geojson",
        "license": "Licence Ouverte 2.0",
        "tags": ["apicarto", "nature", "znieff", "znieff2", "environnement"],
        "update_frequency": "service live",
        "metadata": {
            "endpoint_path": "/api/nature/znieff2",
            "query_param": "geom",
            "rest_base_url": _APICARTO_BASE,
        },
    },
]


class IGNOpenDataProvider(CatalogProvider):
    name = "ign"
    domain = CatalogDomain.OPENDATA
    description = "IGN Géoplateforme — jeux de données géographiques de référence"

    def __init__(self) -> None:
        self._entries: dict[str, OpenDataEntry] = {}
        for item in _IGN_DATASETS:
            entry_id = f"opendata:ign:{item['id']}"
            metadata: dict = dict(item.get("metadata", {}))
            if item.get("wfs_flux_id"):
                metadata["wfs_flux_id"] = item["wfs_flux_id"]
            if item.get("wfs_layer"):
                metadata["wfs_layer"] = item["wfs_layer"]
                metadata["wfs_url"] = _WFS_BASE
            self._entries[entry_id] = OpenDataEntry(
                id=entry_id,
                domain=CatalogDomain.OPENDATA,
                provider="ign",
                name=item["name"],
                description=item.get("description", ""),
                tags=item.get("tags", []),
                metadata=metadata,
                source_url=item.get("source_url", ""),
                format=item.get("format", ""),
                license=item.get("license", ""),
                download_url=item.get("download_url"),
                update_frequency=item.get("update_frequency", ""),
                spatial_coverage="France",
            )

    def list_entries(self, search=None, tags=None, limit=50, offset=0):
        entries = list(self._entries.values())
        if search:
            q = search.lower()
            entries = [
                e
                for e in entries
                if q in e.name.lower()
                or q in e.description.lower()
                or any(q in t for t in e.tags)
            ]
        if tags:
            entries = [e for e in entries if any(t in e.tags for t in tags)]
        return entries[offset : offset + limit]

    def get_entry(self, entry_id):
        return self._entries.get(entry_id)


register_provider(IGNOpenDataProvider())
